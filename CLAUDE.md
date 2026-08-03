# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone FastAPI microservice serving an AI customer-support chat for casino/
sportsbook brands (the original single tenant was **NikaBet**). It is API-isolated:
other modules talk to it over HTTP/JSON by `session_id` (UUID), and the contract is
consumer-agnostic so multiple front-ends can plug in. The admin dashboard,
hot-reloaded tuning, KB editing, and the signed front-end handshake are all built
(see "Admin / management" below). Escalation is a contact-button hand-off (no
in-app form, no live agent).

## Project layout

All backend Python lives in the **`app/` package**; the repo root carries the
config files, docs, and the sibling surfaces (admin SPA, widget, MCP facade,
scripts, tests). This file refers to modules by bare name (`db.py`,
`prompts.py`, …) — resolve them against this map:

```
app/
├── main.py        # FastAPI app: lifespan, middleware, routers, static mounts
├── api/           # HTTP layer: chat.py (public Chat API), admin.py,
│                  #   admin_auth.py (authz choke points), retention.py,
│                  #   quality.py (AI-judge surface), client_ip.py, health.py
├── core/          # platform: config.py (env), settings.py (hot-reloaded knobs),
│                  #   db.py (data layer), tenancy.py (product scope), auth.py
│                  #   (JWT + handshake), secretbox.py, metrics.py, logcapture.py
├── ai/            # model-facing: prompts.py (THE prompt template),
│                  #   openai_client.py (two-key failover), kb.py, starter_kb.py,
│                  #   reviewer.py (LLM-as-judge quality pass)
├── i18n/          # language.py (resolution), translations.py (copy registry)
├── chat/          # support flow: chat_service.py, escalation.py, antispam.py
├── retention/     # Telegram bot: retention.py, retention_v2.py,
│                  #   retention_idle.py, telegram_transport.py,
│                  #   telegram_format.py, delivery.py, send_worker.py
│                  #   (the send stage), media_normalizer.py,
│                  #   player_sync.py, outcomes.py (attribution ledger)
└── worker.py      # background-only entrypoint (`python -m app.worker`)
admin/             # React Admin SPA (its own Vite build)
frontend/          # no-build widget + test page + integration docs (static)
mcp_server/        # admin-API MCP facade (`python -m mcp_server`, .mcp.json)
scripts/           # preflight.sh, check_invariants.py, docs_check.py,
                   #   check_queue_sql.py (needs a real Postgres)
tests/             # pytest suite (conftest.py stubs openai/asyncpg)
```

Imports use the package paths (`from app.core import db`). There are **two** entry
points over the same image: `uvicorn app.main:app` (Dockerfile CMD) serves HTTP and
`python -m app.worker` runs the background pipeline; which one a process is comes
from `SERVICE_ROLE` (see "Event pipeline → Process roles"). Static assets (`frontend/`,
`admin/dist`) and the local `media/` default stay at the REPO root —
`app/main.py` and `app/core/config.py` resolve them via a repo-root anchor
(two/three levels up from `__file__`), so moving a module means re-checking
those anchors.

## MULTI-TENANCY (partners → products) — the commercial-product backbone

The service is **multi-tenant**: **partners** own casino **products**, and nearly
everything resolves per product. This is the central organizing principle — keep it
in mind for every change:

- **Data model** (`db.py`): `partners` → `products` (+ `admin_memberships`,
  `product_settings`). `kb_topics`, `kb_variables`, `chat_sessions`,
  `ai_interaction_logs`, `admin_events` all carry `product_id`. Boot
  (`db._migrate_tenancy`, idempotent, every start) seeds a `default` partner +
  `default` product, adopts pre-tenancy rows into it, and gives legacy
  `admin_users` accounts a global membership — an old deployment upgrades in place.
- **Request scope** (`tenancy.py`): the acting product rides in a **ContextVar**,
  set once at the API boundary (widget key on public chat routes, the session row
  on per-session routes, the admin's selected `product_id` on `/admin/*`). The sync
  `settings.*()` getters read it transparently, so per-product resolution needed no
  signature churn. `None` scope = global-only resolution (pre-tenancy behaviour;
  tests unaffected). A request handler may just SET it (its context dies with the
  request), but a **background worker MUST use `tenancy.scoped_product(pid)`** —
  a worker loop is ONE long-lived task, so a bare set leaves the last product of
  the pass bound for everything the task does afterwards (including
  `db.log_admin_event`, which falls back to this ContextVar for `product_id`).
  The pipeline now fans products out CONCURRENTLY, which makes this stricter, not
  looser: each product's drain/maintenance runs in its own task under its own
  `scoped_product`, so a bare set would race the sibling products' scopes.
- **Settings resolution** is now four layers, merged field-by-field:
  `product_settings` → `app_settings` → env → built-in default. Prompt variables,
  translations and the test profile are stored per product too (`product_settings`
  keys; admin writes with a `product_id` land there); translations merge **per
  language** (a product override of one key keeps the global override of a sibling
  key). Layer 1 of the prompt renders per product (each casino gets its own brand/
  persona) and stays byte-stable *within* a product scope — the cache-invariant
  holds per tenant.
- **Widget identity**: each product has a public, rotatable `widget_key`
  (`wk_…`). The embed snippet passes it (`data-widget-key`); `POST /api/chat/session`,
  `GET /topics`, `GET /i18n` resolve the product from it (absent key → the default
  product, so single-product deployments keep working). Unknown/inactive key → 403.
  The session row stores `product_id`; every later turn re-enters that scope.
- **Per-product secrets**: OpenAI keys (1–2, same two-key failover), the
  handshake secret and the **Turnstile secret** live on the product row,
  **encrypted at rest** via
  `secretbox.py` (stdlib HMAC-CTR keystream + encrypt-then-MAC; master key =
  `SECRETS_MASTER_KEY` env, falling back to `SESSION_JWT_SECRET` with a startup
  warning). They are write-only through the API (`PUT /admin/products/{id}/secrets`
  → only `has_*` flags come back); `db.get_product_openai_keys` /
  `get_product_handshake_secret` / `get_product_turnstile_secret` are the only
  decrypting readers. A product without
  its own keys falls back to the deploy env keys
  (`openai_client.client_for_product`, cached per product + key fingerprint).
  The **handshake secret does NOT fall back that way**: `auth.effective_handshake_secret`
  applies the deploy-level `WIDGET_HANDSHAKE_SECRET` only in the **default product
  scope** (`tenancy.is_default_scope()`, the same gate `escalation.build_payload`
  puts on `CONTACT_FORM_URL`) — otherwise whoever holds it could sign a trusted
  player profile for ANOTHER partner's casino. A non-default product with no
  secret of its own simply has no signed mode; note that "is a secret in effect"
  (production mode: never trust unsigned browser context) stays the BROADER
  `product_secret or env`, so such a product still refuses browser-supplied
  context rather than falling back to dev behaviour.
- **Per-product Cloudflare Turnstile**: each product (domain) runs its own
  Turnstile widget (created as **Invisible** in the Cloudflare dashboard — no
  challenge UI ever shows) — the PUBLIC `turnstile_site_key` on the product row
  (edited in Structure; `PUT /admin/products/{id}` body field) is served to the
  widget via `GET /api/chat/i18n` and adopted automatically (`widget.js
  fetchI18n` — no embed change; a host page may still pin its own via
  `mount()`), and the secret is a normal encrypted product secret.
  `create_session` resolves the product FIRST and verifies against the product
  secret (`antispam.verify_turnstile(secret=...)`); the deploy env
  `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET` pair is only the fallback. The check
  is **ADVISORY (fail-open)**: a missing client token (the Turnstile script is
  blocked in some regions) and a verifier outage SKIP it (logged, sampled) —
  only an explicit "invalid token" verdict from Cloudflare 403s. The other
  anti-spam layers still gate every request, so a player never loses the chat
  over a blocked Cloudflare.
- **Machine admin credentials** (`admin_api_keys`): service API keys for an
  external master admin panel — Bearer `sak_…` tokens on the same `/admin/*`
  surface. Only the SHA-256 hash + a 4-char hint are stored; the plaintext is
  returned exactly once by `POST /admin/api-keys`. Each key carries ONE role at
  ONE scope (global/partner/product) and `require_admin` translates it into a
  synthetic membership, so every scope helper works unchanged; deactivation
  applies on the next request. Key management (`/admin/api-keys*`) is
  restricted to HUMAN admin accounts within their scope (a leaked key cannot
  mint keys). `/openapi.json`, `/docs`, `/redoc` are NOT served unless
  `EXPOSE_API_DOCS=1`.
- **Authorization** (`app/api/admin_auth.py`): accounts (`admin_users`) get roles via
  `admin_memberships` — one role per scope: `global`, `partner` (all its products)
  or `product`; role `admin` writes within the scope, `manager` reads. All checks
  go through `require_admin` (loads memberships per request) + the scope helpers
  (`role_for_product`, `accessible_product_ids`, `resolve_scope_filter`,
  `require_product_write`, `require_global_write`). Dashboard queries take a
  `product_ids` filter; `None` = all, empty list = match nothing. User management
  reach: an admin touches only accounts whose ENTIRE membership set lies inside
  its own admin scopes.
- **Admin surface**: a **Partner → Product switcher block in the header** of the
  SPA re-scopes every tab; the **Structure** tab manages partners/products, widget
  keys (+ copyable embed snippet), and product secrets; the **Users** tab manages
  accounts + memberships. `GET /admin/structure` feeds the switcher.
- **Integration docs**: a FAMILY of public, self-contained HTML guides (Russian)
  for partner/CMS dev teams, split by task. `GET /integration` is the HUB
  (overview, architecture/multi-tenancy, deploy env vars, docs index); its
  per-topic siblings are `GET /integration-widget` (embedding the ready-made
  widget: snippet, widget key, Turnstile, CORS), `GET /integration-data` (player
  data transfer & sync — the ONE home for the whitelist fields, signed-handshake
  format + signing samples, the lazy-pull Player API contract, the push webhook
  and the activity timestamps; other pages link here instead of duplicating the
  contracts), `GET /integration-chat-api` (the public Chat API reference + the
  mandatory client logic for a custom UI), `GET /integration-telegram` (the
  Telegram retention bot: deeplink contract, subscription gate, proactive agent,
  admin setup), and `GET /integration-admin` (wiring an external master admin
  panel: roles model, JWT login, `sak_…` service keys, the `/admin/*` endpoint
  reference) — same house style, all cross-link via header + footer. The example
  page (`frontend/test.html`) carries exactly one link to each. Update the
  matching page when a public contract changes; keep the family in the same
  house style.
  The one **GENERATED** member is `GET /integration-reference` — the API &
  interop reference handed to OTHER teams' programmers to match this service
  against their systems. It is ONE flat, filterable table: a row is an
  integration UNIT (endpoint, wire field, header, canonical event) with its
  format, obligation, example, who calls whom, authorization, limits and error
  codes. Its scope is deliberately narrow — **only what crosses a system
  boundary**. Product-internal surface (env vars, the hot settings groups,
  prompt/KB variables, the translations registry, the DB schema) is OUT on
  purpose: nobody outside edits it, and it dilutes the contract surface. It is
  NOT hand-edited: `scripts/build_api_reference.py` renders BOTH
  `frontend/integration-reference.html` and the downloadable
  `frontend/api-reference.xlsx` (linked from the page, served by the `/static`
  mount) from ONE `ROWS` list, so page and workbook can never drift apart. When
  a public contract changes, edit `ROWS` and re-run
  `python scripts/build_api_reference.py`; never hand-edit the two generated files.
- **The prompt template stays the one shared, deploy-level artifact** — brands
  differ only via prompt variables + KB + translations + settings, never per-tenant
  prompt forks.

**The prompt WORDING lives in one place: the file `prompts.py` (the single source of
truth) — as a DRY TEMPLATE.** The Layer-1 core (`SYSTEM_CORE` — Nika's tone-of-voice + the
absolute/escalation/responsible-gaming/links rules), every behavioural directive (greeting,
formatting, KB-grounding, escalation restraint, suggestions, finish-chat, lead-forward —
STATIC, in Layer 1; language, personalization, topic-routing — DYNAMIC, in Layer 3), and the
forbidden-topics list are constants in that file. The wording is **not** editable from the
admin panel — to change it you edit `prompts.py` and redeploy. What IS admin-editable are
the **prompt variables** (see "Prompt variables" below): the `{placeholder}` values —
persona name, brand, products, tone of voice, support scope — that uniquify the
template per brand (the seam for future white-label deployments). The admin **Prompt** tab
has two sub-tabs: **Preview** (a **read-only** view of the whole assembled prompt, all
layers as sent, variables substituted) and **Prompt variables** (those values, plus the
escalation keyword lists and the test player profile as sibling blocks). (The per-topic
knowledge base — Layer 2 — stays editable in the Knowledge-base tab, since it's answer
content, not instructions.) There is no system-prompt versioning, no A/B split, and no
admin prompt editor — those were removed in favour of this single source of truth.

The two source briefs (`CLAUDE_CODE_PROMPT_support_chat*.md`) are the authoritative spec.
When extending the service, treat them as the contract and keep the invariants below.

## Commands

```bash
# Run the full test suite (stubs OpenAI + asyncpg; no real DB/API key needed)
SUPPORT_CHAT_TEST_MODE=1 python -m pytest -q

# Run one test file / one test
SUPPORT_CHAT_TEST_MODE=1 python -m pytest tests/test_failover.py -q
SUPPORT_CHAT_TEST_MODE=1 python -m pytest tests/test_antispam.py::test_rate_limit_blocks_past_threshold -q
```

**Test gotcha:** `conftest.py` stubs only `openai` and `asyncpg`. `httpx` and `pydantic`
are imported for real (via `antispam.py` / FastAPI), so a bare `pip install pytest
pytest-asyncio` is not enough — `httpx` must be installed too. `pytest.ini` sets
`asyncio_mode = auto`, so async tests need no `@pytest.mark.asyncio`.

`SUPPORT_CHAT_TEST_MODE=1` makes `config.py` fill the required env vars with placeholders
so modules import without real secrets. `conftest.py` sets it too, so it is only needed
when invoking modules outside pytest.

### Dev tooling (`.claude/`, `scripts/`, CI)

- **`scripts/preflight.sh`** is the one verify command: install deps → `ruff` →
  invariant checks → `pytest`. Run `bash scripts/preflight.sh` before committing
  (or `--checks` to skip install). CI (`.github/workflows/ci.yml`) runs the exact
  same script on every PR, so green preflight = green PR. It installs the runtime
  deps that must be real (fastapi/uvicorn/httpx/python-multipart) + dev tools, and
  deliberately **omits** `openai`/`asyncpg` (conftest stubs them; the failover
  tests build openai errors with the stub's lenient constructors, which the real
  SDK rejects).
- **SessionStart hook** (`.claude/hooks/session-start.sh`, registered in
  `.claude/settings.json`) runs `preflight.sh --install` on Claude Code on the web
  so tests/ruff work immediately in a fresh session. Synchronous; merge it to the
  default branch for future sessions to use it.
- **`scripts/check_invariants.py`** statically enforces the "breaks silently"
  rules by importing the real modules (reusing conftest's stubs): every
  translations key has shipped English copy, the Layer-1 prompt core is
  byte-stable, and every writable settings group surfaces in the admin schema.
- **`scripts/check_queue_sql.py`** runs the event/send-queue statements against a
  REAL Postgres. `conftest.py` stubs asyncpg, so the suite can assert which SQL a
  helper issues but never that Postgres accepts it — and the queue shipped
  statements that fail at PREPARE time (a bare `$n` inside an expression has
  nothing to infer a type from). Deliberately outside `preflight.sh` (which has
  no database): run it by hand, against a scratch DB, when you touch queue SQL.
- **`ruff`** config in `pyproject.toml` is conservative on purpose (real-bug rules
  F/E9 only; line length and semicolons off) — don't broaden it into a restyle.
- **`scripts/docs_check.py`** (skill `/docs-check`) is the manual replacement for
  the removed docs-sync: it diffs the working tree vs `origin/main` and flags the
  docs a change of that shape usually needs — architecture `.py`/`app/api/` →
  `CLAUDE.md`; `app/core/config.py` → the README env table; a public API file → its
  `frontend/integration-*.html` page; any integration/widget change →
  `frontend/test.html`. Advisory (exit 0), since whether an edit warrants a doc
  change is a judgment call.
- **Skills** in `.claude/skills/` scaffold the recurring cross-file changes so no
  touch-point is missed — reach for them when doing that kind of change (the
  session's skill listing enumerates them).

## Architecture — the big picture

### 3-layer prefix-cache-optimised prompt (the central design)
`prompts.py` assembles every request in three layers so the OpenAI prefix cache stays warm.
The split is by **mutability**, not by topic: anything byte-stable belongs in the cached
system prefix; only per-request data may sit after the (growing) history.
- **Layer 1 — the byte-stable system block (`prompts.get_system_core()`)** — the persona
  core `SYSTEM_CORE` (Nika's tone-of-voice + the absolute / escalation / responsible-gaming /
  links rules) **plus every STATIC behavioural directive** (greeting, formatting, KB-grounding,
  escalation restraint, suggested questions, finish-chat, lead-forward). None of these carry
  per-request data, so they ride in the cached prefix; the whole block is byte-identical across
  requests (a test enforces this). It is **never** edited to add per-request behaviour.
- **Layer 2** — the KB block for the selected topic, appended to the system message after a
  fixed separator. Changes only when the topic changes (an accepted cache break that never
  invalidates the larger byte-stable Layer-1 prefix).
- **Layer 3** — *only* per-request data lives in the **user message**: sanitized
  `user_context`, the personalization line, the resolved language directive, the topic-routing
  catalogue, the conversation history, the new user turn, the recency guardrails /
  forbidden-topics block (kept **last**, after the player's message, on purpose — an
  anti-injection / anti-off-topic reminder bites hardest closest to the input), and — only
  on the last turns before the message cap — the conversation-budget wrap-up notice.

A STATIC rule goes into Layer 1 (so it is cached); a rule that needs per-request data goes
into Layer 3 — **never** does per-request data enter the byte-stable Layer-1 block. **The whole
model-facing prompt is written in English** — English is the most token-efficient language for
the model, and the prompt text never needs to match the player: the language directive makes the
model **answer in the player's language** regardless, and the KB (Layer 2) can be in any language.
Only the model-facing prompt is English; user-facing copy (escalation/contact text, the
low-content nudge, widget chrome — all in the `translations.py` registry, admin-editable per
language) and the user-input detectors (injection / escalation keyword
scans) stay multilingual. The Layer-3 directive tells
the model to **answer in the language of the player's current message** (falling back to the
session's base language when it's too short/ambiguous) — so the answers follow the player if
they switch language mid-chat, while the widget chrome stays fixed to the browser language
(see "Language resolution" below).

**Tone of voice — the persona "Nika" (`SYSTEM_CORE`).** The assistant is **Ника / Nika**, a
warm, playful, lightly flirtatious **international** guide-persona (not a Russia-specific
character): talks on «ты», simply and informally but respectfully, makes every player feel VIP,
and nudges them toward play without pressure — while **dialling the playfulness down** in
money/dispute/complaint/escalation situations (there she is calm, attentive, caring). She
highlights the chance to win rewards (bonuses/prizes/tickets) but takes every concrete
amount/condition/date/name **strictly from the KB** (never invents), never promises a win,
**uses no emoji**, uses the player's name sparingly, and keeps her character **on every
language**. The tone rides in the byte-stable core, so it is cached and consistent. The persona
name, the brand name and the tone-of-voice paragraph are **prompt variables**
(`{persona_name}`, `{brand_name}`, `{tone_of_voice}`, …) editable from the admin Prompt →
Prompt variables sub-tab; to change the surrounding wording itself you edit `SYSTEM_CORE`
(the template) and redeploy.

**Responsible gaming (brief, `SYSTEM_CORE`).** Nika never raises addiction herself and never
moralizes; but if the **player** says they have trouble controlling play or asks to limit/pause
play or self-exclude, she drops the flirt, responds with care, and **escalates immediately**
(`[[ESCALATE]]`) to a human. **Links policy (`SYSTEM_CORE`):** only links from the KB or
official NikaBet links — never invent URLs.

**Personalization** also lives in Layer 3 (never `SYSTEM_CORE`): when the sanitized
`user_context` carries a `full_name`, `prompts._personalization_directive` adds a line giving
the model the player's **first name** and telling it to use the name **only once — in the first
greeting — and then not again** in every reply (models otherwise parrot the name on every line,
which reads robotic; the directive allows a rare reuse only for reassurance in a complaint/
sensitive case). No name ⇒ the line is omitted and the prompt is unchanged. The whitelisted context
fields the model ever sees are `prompts._CONTEXT_FIELDS` (`id, full_name, email,
activation_status, country, balance, vip_level, registration_date`) — anything else in
`user_context` is dropped, so adding a model-visible field is a deliberate edit to that list.

**Greeting hygiene** is a STATIC directive in the Layer-1 core (`prompts._GREETING_DIRECTIVE`):
**the model never introduces itself, and the one greeting it gives is the by-name opener in the
first reply.** The widget always paints its canned greeting bubble («Привет, я Ника, чем могу
тебе помочь?» in the chrome language — client-side only, never persisted) the moment the player
picks a topic, BEFORE their first message, so Nika has already said hello and introduced herself.
The earlier "greet exactly once, in the first reply" rule therefore produced a DOUBLE
self-introduction (the canned bubble immediately followed by the model's own "Привет, я Ника…"
opener — and another re-greet after a mid-chat language switch, which the model treated as a
fresh start). The rule now: when the player's name is known (the Layer-3 PERSONALIZATION block),
the VERY FIRST reply opens with a short by-name greeting («Привет, Андрей!») and then answers;
with no name there is no greeting at all; no reply ever contains a self-introduction; and no
reply after the first one greets — a language switch is NOT a new conversation (a greeting-only
player message gets a warm "what do you need?" — still without re-greeting).
`_personalization_directive` (Layer 3) supplies the name, the transliteration rule, and — the
part that makes the greeting actually happen — an explicit per-turn imperative:
`build_messages` computes `first_turn` (empty prompt history AND not ongoing/closing), and on
that genuinely first turn the block orders "you MUST open THIS reply with a short by-name
greeting; the brevity/no-filler rules do NOT drop it", while every later turn gets the
suppression wording ("the greeting already happened — do not greet or reuse the name", rare
reassurance in a complaint/sensitive case excepted). Leaving the model to *infer* "is this my
first reply?" from the empty history did not work: the reasoning model weighed the static
no-filler / never-introduce-yourself rules over the conditional greeting rule and skipped the
greeting entirely. **After a topic switch** the prompt history is cut at `context_reset_id`, so the model
sees an empty history — `chat_service` passes `ongoing=True` and Layer 3 gets
`_ONGOING_CONVERSATION_DIRECTIVE` ("CONVERSATION STATE: already in progress, do not greet"),
so the by-name greeting is never repeated across the boundary.

**Formatting hygiene** is another STATIC Layer-1 directive (`prompts._FORMATTING_DIRECTIVE`), and `SYSTEM_CORE` must not contradict it by asking for plain text only:
the model reaches for Markdown on its own (`**bold**`, lists, links), and the widget now renders a
small fixed subset of it (`widget.js` `renderMarkdown` — see "Conventions"). Left unguided the model
also emits markup the widget can't render (tables, fenced code blocks, raw HTML), which leaks to the
player as literal characters. This directive pins the model to exactly the rendered subset — bold,
italic, inline `code`, links, and bulleted/numbered lists — and tells it to avoid the rest, so the
two stay in lockstep: whatever the model emits, the widget renders. Rides in the byte-stable Layer-1
block (it carries no per-request data).

**KB grounding** is a STATIC Layer-1 directive (`prompts._KB_GROUNDING_DIRECTIVE`), phrased to be a
no-op for the catch-all `other` (which loads no KB and whose routing directive already steers the
model to a specialized branch). The KB block (Layer 2) is the single source of truth, but the model
tends to miss a matching entry when the player phrases the question differently from how the KB is
written, then falls back to vague generic prose or invented specifics instead of the exact answer that
IS in the KB (e.g. a player asks about a specific bonus under «Бонусы» worded unlike the KB's example
questions, and gets generic/made-up info though the precise entry exists). The directive tells the
model to match the question to the KB by **meaning/intent**, not literal wording; answer strictly and
precisely from the matched entry; never substitute generic or invented conditions/numbers/dates/names
when concrete ones exist; answer generically only when the question really is generic and the KB has
nothing; and ask one short **clarifying question** to steer the player toward a specific KB answer when
the question is too vague or spans several entries. Rides in the byte-stable Layer-1 block.

**Escalation restraint** is a STATIC Layer-1 directive (`prompts._ESCALATION_RESTRAINT_DIRECTIVE`)
that paces the model's hand-off. Layer 1's escalation rule tells the model to emit `[[ESCALATE]]` when
it "cannot resolve the question or the KB has nothing" — but in practice the model reaches for the tag
too early: it bails the moment the player's first phrasing doesn't hit an exact KB entry, or the
question is vague, instead of working with the player to surface the answer that IS in the KB (often the
player hasn't even articulated what they need yet). This directive makes escalation a **last resort**:
don't escalate just because the answer wasn't found on the first try or the question is fuzzy — first
try to help and clarify (one short question at a time) and steer the player to the concrete KB answer.
It deliberately **preserves the immediate-escalation cases** (explicit request for a human,
complaint/grievance, suspected fraud, legal threat) so genuine hand-offs are never delayed; everything
else escalates only after an honest attempt to help and clarify still leaves nothing answerable in the
KB. Applies in **every** topic, including the catch-all `other`. Pairs with `_KB_GROUNDING_DIRECTIVE`
(try hard to find the answer → don't give up too early). Rides in the byte-stable Layer-1 block; the
backend escalation triggers are unchanged — this only makes the model emit the sentinel more
deliberately. (The keyword triggers run pre-model in `chat_service` and never reach the model at
all — see "Escalation" below.)

### Data layer — no ORM, no migrations (`db.py`)
The schema *is* the code in `db.init_db()` (run on startup via `main.py` lifespan). To change
schema, edit the `_SCHEMA` string. **A new column on an existing table will NOT be applied by
`CREATE TABLE IF NOT EXISTS`** — add an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
to `_ensure_columns()`. Every table read/write goes through a `db.<name>(...)` async helper;
nothing else touches tables directly.

**A guarded ALTER may run at boot; a BACKFILL may not.** `init_db` is ONE transaction on a
POOL connection, so `command_timeout` (30s) applies to the whole of it: an `UPDATE` over
millions of legacy rows there times out, rolls the entire boot back, and does it again on
every restart — an unbootable deployment, not a slow one. Adding the column is cheap and
belongs in `_ensure_columns()`; moving the existing rows onto the new contract is a separate,
**batched, post-boot** job (`db.backfill_event_lifecycle` is the pattern: each batch its own
short transaction so partial progress sticks, `max_batches` bounding one pass, driven from
`retention_v2.maintenance_loop`). Write the READER so it is correct before the backfill
finishes — the event claim tests `processed_at IS NULL` alongside `status`, so it never
re-reacts to unbackfilled history.

**Every query goes through the bounded acquire.** Use the module helpers
`_fetch/_fetchrow/_fetchval/_execute` (or `async with _acquire()` for a multi-statement
transaction) — never asyncpg's `Pool.fetch/execute/...` convenience methods. Those wait for
a free pool slot with **no ceiling** (`command_timeout` bounds how long a query may RUN, not
how long it may WAIT), so a single call reaching for them opts that query out of
`DB_ACQUIRE_TIMEOUT_SEC` and hangs forever under pool exhaustion. A lock or job that holds a
connection for **minutes** (the quality judge's sweep lock, the media normalizer) must instead
take a `db.dedicated_connection()`: it would otherwise eat a pool slot, and the pool's
`command_timeout` kills a *blocking* `pg_advisory_lock` wait outright. The pool's bounds are
role-aware (`DB_POOL_MIN`/`DB_POOL_MAX`, 25 on the worker vs 10 on web) — the worker runs many
concurrent player shards, the web process serves requests. The retention event drain holds no
advisory lock at all any more; its mutual exclusion is the claim (see "Event pipeline").

### No seeds — empty DB starts empty
There is **no seed step** for topics, KB content, or settings. On a fresh/empty database there
are no topics, KB, or stored settings: the owner creates topics + their KB from the admin panel,
and runtime settings resolve through `settings.*()` with precedence `app_settings` (DB) → env →
built-in default, so an empty `app_settings` simply falls back to env/defaults until the owner
overrides a knob in the admin. The DB is the source of truth for KB + settings once edited;
nothing on boot mutates existing rows. (The **prompt** is not stored at all — it lives in
`prompts.py`.) **The one seeded table is `kb_variables`** (`db.seed_kb_variables`, run in
`init_db`): it inserts the default `{placeholder}` registry with `ON CONFLICT (key) DO NOTHING`,
so it never overwrites an admin-edited value — boot only fills keys that don't exist yet. The
registry's default VALUES are **brand-neutral** (no brand names/URLs — `{{PLACEHOLDER}}` marks
per-brand-only values), because the same registry seeds every product.

**Exception — NEW products get a starter baseline (`starter_kb.py`).** `db.create_product`
(the admin "add product" path, NOT boot) seeds the new casino so its chat works out of the box
before the owner translates/uniquifies: (1) the kb_variables registry; (2) the starter
topics + KB texts from `starter_kb.STARTER_TOPICS` — the ANONYMIZED production KB developed
on the original tenant: brand-neutral, English, structured JSON Q&A documents,
**seven topics** mirroring the live picker (deposits, withdrawals, account &
verification, bonuses, betting & games, technical + `other` last) that assert **no**
brand-specific facts (no brand names, URLs, campaign names, amounts or schedules — every
brand-specific value is a `{placeholder}` from the default kb_variables registry, which
seeds alongside the texts as a matched pair, and market/campaign specifics were rewritten
generically); (3) the FULL `prompt_variables` set into `product_settings`
(template defaults, `brand_name` = the product's name, via
`starter_kb.starter_prompt_variables`) — **and, symmetrically, the FULL
`retention_prompt_variables` set** (`starter_kb.starter_retention_prompt_variables`,
retention defaults + `retention_brand_name` = the product's name; the Telegram persona is a
separate prompt with its own registry, so without its own seed the bot would introduce
itself under the registry-default brand) — so a new product never inherits another brand's
**global** prompt-variable overrides (the API endpoint calls `settings.reload()` after the
seed so it applies immediately); (4) the **starter retention-KB document**
(`starter_kb.STARTER_RETENTION_KB` via `db.seed_starter_retention_kb` — same brand-neutral
English contract, seeded only when the product has no retention KB at all) so the Telegram
bot also works out of the box; (5) the **starter idle-ping ladder**
(`retention_idle.seed_starter_idle_rules` — the production-tuned 3/5/7/10/14/21/30/45/60-day
re-engagement ladder, seeded only
when the product has no rules) so quiet players are re-engaged out of the box. Translations and
the `retention`/other settings groups need
**no** per-product seed: their shipped defaults resolve for every product until overridden.
`db.seed_starter_kb` is idempotent-safe: it inserts only
topics the product doesn't have and writes a KB entry only for a topic it just created — it
can never overwrite existing content. The boot-seeded default product's KB/prompt-variables are
untouched (it goes through `_migrate_tenancy`, not `create_product`). Tests in
`tests/test_starter_kb.py` pin the no-brand-leak contract (support + retention starters).

### KB variables — `{placeholder}` registry (`db.py` + `kb.render_variables`)
KB texts may contain `{key}` placeholders (e.g. `{min_deposit}`). The `kb_variables` table holds
one admin-managed `value` (+ description) per key. `kb.kb_block_for_topic` runs
`kb.render_variables` over the topic's KB before it enters Layer 2, substituting each `{key}` with
its registry value (unknown placeholders are left **as-is** so missing entries are visible in the
prompt preview). The admin **Knowledge base → Variables** sub-tab (`GET/PUT /admin/kb/variables`)
lists + edits them (it lives under the KB view because these values belong to the KB texts — the
old top-level Variables tab was folded in; the legacy `#variables` hash redirects).
NB: `list_kb_variables`/`set_kb_variable` must return `updated_at` as an **isoformat string**
(via `db._row_to_kb_variable`) — a raw `datetime` cannot be serialized by `JSONResponse` and 500s
the tab (the bug that shipped with the feature).

### English-only guard for model-facing content (`settings.ensure_english`)
The model-facing prompt is English by design (invariant §7), so every admin write
that FEEDS the prompt is validated to Latin script and 400s on Cyrillic/CJK/Arabic/
etc.: prompt variables (support + retention), KB texts (`PUT /admin/kb/content`),
the canonical English topic title, KB variable values, the retention KB document
and site-map titles/purposes. Player-facing copy (translations, per-language topic
titles) and the multilingual escalation keyword stems are deliberately NOT guarded.
The error names the first offending character and points the operator at
Translations.

### Prompt variables — the brand-uniquification registry (`prompts.py` + `settings.prompt_variables`)
The prompt in `prompts.py` is a **dry template**: `SYSTEM_CORE`, `_GUARDRAILS`, the
forbidden-topics list/refusal and the closing-goodbye directive carry `{placeholder}` tokens
(`{persona_name}`, `{brand_name}`, `{products}`, `{persona_role}`,
`{tone_of_voice}`, `{support_scope}`). The RETENTION (Telegram) persona has its **own
registry** (`prompts.RETENTION_PROMPT_VARIABLES`: `retention_persona_name/_persona_role/
_brand_name/_products/retention_tone_of_voice`) with its **own store**
(`retention_prompt_variables`, `settings.retention_prompt_variables()`) and its **own admin
editor** — the **Retention → Prompt variables** tab. It is a **SEPARATE prompt, fully
decoupled from the support chat**: every retention key ships its **OWN retention default**
(name/role/brand/products/tone) and an empty override falls back to that default — **never**
to a support value, so a support edit can never leak into the bot (the old `inherits_from`
value-inheritance was removed — the Telegram bot must not read as "the support chat in
Telegram", e.g. its role no longer inherits "...works as a customer-support assistant"). The
retention tone ships its own bolder default, so the retention KB's sexier persona never has to
fight the support tone. The retention templates keep the BASE placeholder names
(`{persona_name}`, …); each registry entry's 4th field is a **`renders_as`** target — which
base placeholder it fills (a RENDER link, **not** a value-inheritance link) —, and
`prompts.render_retention_prompt_variables` fills each base placeholder from ONLY the retention
store (used by `get_retention_system_core()` and the retention Layer-3 guardrails), so the
retention Layer 1 stays byte-stable per product × mode. The B2B platform the brand runs on is deliberately
**absent** — the prompt names only the brand and its products; anything platform-related is
KB content (Layer 2), managed from the Knowledge-base tab, never prompt material.
`prompts.PROMPT_VARIABLES` is the registry — (key,
description, default) — and `prompts.render_prompt_variables` substitutes registered keys with
values from `settings.prompt_variables()` (app_settings `prompt_variables` override > the file
defaults; hot-reloaded like every setting). This is how a future white-label deployment re-brands
the assistant from the admin without touching the prompt file. Only *registered* keys are
substituted (a stray `{brace}` stays as-is), rendering is applied **per template string, never
over player text** (`build_dynamic_prompt` renders `_GUARDRAILS`/forbidden/closing individually,
so a `{brand_name}` typed by the player reaches the model literally), and `get_system_core()`
renders from the in-process cache, so Layer 1 stays **byte-stable between requests** — it changes
only on an admin save, the same accepted cache break as a KB edit. Values are English (the
model-facing prompt stays English; no per-language uniquification). Edited from the admin
**Prompt → Prompt variables** sub-tab (`GET/PUT /admin/prompt-variables`,
`settings.validate_prompt_variables`; empty values fall back to the defaults). The **escalation
keyword lists** (a friendlier one-per-line editor over the existing `escalation` settings group —
the multilingual trigger stems stay multilingual, they scan the player's raw message, not the
prompt) and the **test player profile** used to be sibling blocks on that sub-tab; they are now
their own pages in the sidebar's **Common** section (`/escalation-keywords`, `/test-profile`; the
legacy `#test` hash redirects to the latter).

### Site map — official pages the model may link to (`prompts.render_site_map_block` + `settings.site_map`)
A single per-product setting: the list of the product's official website pages (`{title, url,
purpose}`) the assistant is allowed to link to. Stored under its own `product_settings` key
`site_map` (like `prompt_variables`/`translations`, OUTSIDE `SETTING_KEYS`, its own admin
endpoint `GET/PUT /admin/site-map`), on the PRODUCT (brand-specific URLs). The product layer
REPLACES the global list as a whole (a list has no keys to field-merge); no product override ⇒
the global list, else empty. `settings.validate_site_map` requires an http(s) `url` per row
(drops blank rows, caps at 60 pages, length-caps fields). `prompts.render_site_map_block(pages,
brand)` renders a deterministic `=== SITE MAP ===` block (brand already substituted, appended
AFTER the prompt-variable render so admin URLs never pass through `{placeholder}` substitution),
which `get_system_core()` AND `get_retention_system_core()` append to their byte-stable Layer-1
core — so **both** bots (support + retention) get the same catalogue, and each core's links
policy names "the official {brand_name} site pages provided to you" as an allowed link source.
Empty list ⇒ no block, so the cores render exactly as before (the byte-stability invariant holds
when no pages are configured; it reads the in-process settings cache, so the block is byte-stable
WITHIN a product scope and changes only on an admin save — the same accepted cache break as a
prompt-variable edit). The read-only effective-prompt previews pick it up automatically (they
reuse `get_system_core`/`get_retention_system_core`). Admin: the **Common → Site map** page
(`admin/src/pages/SiteMap.jsx`, `RequireProduct`-gated, admins edit / managers read-only). No
per-product seed (like translations — empty until the owner adds pages). Tests:
`tests/test_site_map.py`.

### Translations — the user-facing copy registry (`translations.py`)
Every string the player sees now resolves through one registry: the widget chrome (header title,
topic heading, canned greeting, placeholder, buttons, error notes, switch notices, finish copy)
AND the server-generated turns (the escalation card message/button, the closing "Issue solved."
bubble, the low-content nudge, the model-error nudge). `translations.KEYS` is the catalogue
((key, scope `widget`/`server`, description)); `translations.DEFAULTS` holds the shipped copy for
en/ru/es/tr/pt (the per-module dicts that used to live in `escalation.py` / `chat_service.py` /
`antispam.py` moved here — those modules now call `translations.text(key, lang)`). Resolution
chain: admin override[lang] → default[lang] → override/default of the default language → English,
so a language **added from the admin Language tab starts on English copy and becomes fully
translatable** via overrides. Overrides live in app_settings `translations` ({lang: {key: text}},
`settings.validate_translations` — ISO-validated codes, registered keys only, empties dropped),
edited from the admin **Translations** tab (`GET/PUT /admin/translations`), which also edits the
per-language **topic titles** (stored on `kb_topics.title` via the existing topic upsert). The
widget keeps a baked-in copy of the `widget`-scope strings (`widget.js` `I18N`) for an instant
first paint, then fetches the session-free, cacheable `GET /api/chat/i18n` and merges the
server-resolved strings over it (`fetchI18n`), so admin copy edits reach the chrome without a
widget redeploy. The admin SPA's own chrome is bilingual (EN/RU via `admin/src/i18n.js` —
English source strings are the keys, components wrap them in `t()`; a new admin-visible
string needs an RU entry there). Admin CONTENT that feeds the model (prompts, KB,
variable values) stays English-only — `settings.ensure_english` enforces it.

### Atomic turn write (invariant)
`db.persist_turn` writes the user message, the assistant message, the `ai_interaction_logs`
row, and the `chat_sessions.message_count` bump in **one transaction**. Do not split it.
When adding per-turn columns, join them into this same transaction. **`ai_meta` is optional**:
model-free turns (the message-cap hand-off, low-content nudge) pass `ai_meta=None` so the visible
chat turn + counter still persist atomically but **no `ai_interaction_logs` row** is written
(there was no OpenAI call — consistent with invariant §4, which scopes the AI log to actual calls).

### Two-key OpenAI failover (`openai_client.py`)
Primary key first; if it stays silent for `OPENAI_KEY_SWITCH_TIMEOUT_SEC`, the fallback is
launched **in parallel** and whichever responds first wins (loser cancelled). A hard error
(auth/quota/not-found) fails over immediately; transient errors (429/timeout) retry with
exponential backoff up to `OPENAI_MAX_ATTEMPTS`. Every fallback engagement fires an
`on_failover` callback → `admin_events('key_failover')`. Cost is computed from token usage
via `_PRICING` (marked "verify before trusting" — prices may be stale; unknown models cost 0).
`_pricing_for_model` first tries the exact id, then strips a trailing `-YYYY-MM-DD` snapshot date
and prices it as the stable alias — so a new dated snapshot doesn't silently flatten dashboard
cost to $0. Every call path (incl. `_call_with_backoff` retries and the race) emits structured
`log.info/warning` lines for Railway tracing.

The default model is **GPT-5.6 Luna** (`gpt-5.6-luna`) — the cheapest tier of the GPT-5.6
reasoning family (Sol > Terra > Luna), aimed at high-volume latency-sensitive chat. Reasoning models
change the request shape: the call sends `max_completion_tokens` (**not** `max_tokens`), does
**not** send `temperature` (rejected by these models), and instead passes `reasoning_effort`
and `verbosity` (each `low`/`medium`/`high`). Both are sent only when set — an empty string in
the `model` group **omits** the parameter so the model's own default applies (and so the owner
can drop a knob a future model rejects without a redeploy). The `max_output_tokens` budget
counts reasoning tokens (billed as output), so it ships higher (2000) than a non-reasoning
model would need — too low and the visible answer can return empty.

**Truncation self-heal (`openai_client._is_truncated_empty` + `_KeyClient.call`).** A reasoning
model can spend the **entire** `max_output_tokens` budget on hidden reasoning and return an empty
visible answer (`finish_reason='length'`, no content). That blanks the chat turn AND emits **no
control sentinels** — so cross-topic routing (`[[TOPIC:slug]]`), suggestions, and finish-chat all
silently die and the widget looks frozen. When `call` detects this (empty content + `length`
finish), it **retries the same request once** with a larger budget (`max(budget*3, 2000)`, capped
at 8000) so the answer + tags fit; same messages, so the prefix cache stays warm. It logs
`openai_empty_truncated_retry` (raise the `model` group's `max_output_tokens` to avoid the extra
call). **The discarded first attempt's token usage is NOT lost:** `call` stashes it
(`_pending_extra_usage`, keyed by the retry response) and `_result` folds it into the returned
counts, so `compute_cost`/`ai_interaction_logs` cover BOTH billed calls (a cancelled failover-race
loser remains the one unaccountable case — its usage rides in a response that is never received;
flagged in the `openai_complete_race_won` log line). `chat_service` keeps a backstop: if the reply is still empty and it's neither an escalation
nor a topic switch, it returns the localized low-content nudge (`chat_empty_reply_fallback`) so the
widget never renders a blank bubble. This was the bug behind "a new chat in the wrong topic just
hangs" — at `max_output_tokens=700` the wrong-topic routing reasoning ate the whole budget, so the
switch suggestion was never produced.

**Timeouts are PER CALL PURPOSE (`openai_client.CALL_PURPOSES` + `settings.model_profile`).**
Every caller declares which block of the stack it is — `chat` (support widget turns +
Telegram bot replies), `agent` (the proactive retention agent: event decisions + ping
writing), `review` (the quality-review judge) or `media` (photo/video cataloguing) — and the
request timeout + key-switch timeout resolve from that purpose's fields in the `model`
settings group (`agent_*`/`review_*`/`media_*`; the unprefixed pair stays the interactive
one, so a stored row keeps its meaning, and a blank background field falls back to it). The
race above exists to protect a player watching the typing indicator; a background pass has
nobody waiting, so the background purposes ship with **`key_switch_timeout_sec = 0` = never
race** — the fallback key is engaged only on a real error (invariant §5 holds; only the
speculative second call is dropped). This was a live cost leak: a quality review reads a
whole transcript and a vision call carries a multi-MB image, so both routinely ran past the
interactive 15s and raced a second FULL call whose tokens OpenAI bills but which can never be
accounted (the loser's usage rides in a response that is never received). Background purposes
also get longer request timeouts (agent 90s, review/media 120s vs the chat's 30s). Every log
line carries `purpose=…`: a background call has no `session_id`, so without it a
`switch_timeout` line named no culprit.

The tuning knobs (model name, reasoning effort, verbosity, max output tokens, the per-purpose
timeouts, max attempts, per-key concurrency) are NOT read from env directly — they come from
the hot-reloaded `model` settings group (`settings.model()`, precedence `app_settings` → env →
default). Everything except `max_concurrent_per_key` is read **live per call** (the per-call
`timeout` kwarg overrides the SDK client's construction-time default, so a timeout edit needs
no rebuild); `max_concurrent_per_key` is bound when the client is built, so a `model` write
also calls `openai_client.reset()` to rebuild the singleton (no effect on the OpenAI-side
prefix cache). API keys themselves stay secrets in env.

### Anti-spam gate order (`antispam.py`, enforced in `app/api/chat.py`)
`POST /api/chat/message` checks in this exact order: verify session token (401) →
**open-session check (409 `session_closed` if resolved/escalated)** → IP rate-limit (429 + log)
→ cooldown (429) → input length (400) → **low-content guard** → injection scan (always audits;
**hard-blocks with 400 by default**, settings-gated via `injection_hard_block`) → message-cap
fast path (forces an escalation response with no model call) → **pre-model keyword-escalation
gate in `chat_service`** (soft hand-off, no model call — see "Escalation") → build/call/persist.
The cooldown gate only CHECKS; the stamp is armed (`antispam.arm_cooldown`) after every
reject-gate passes, so a rejected message (too long / low-content / injection-blocked) never
throttles the player's immediate corrected resend.
**Every OTHER session-bound route is throttled too** (`chat.py` `_check_session_op_rate`): `POST
/topic`, `POST /escalate`, `POST /resolve` and `GET /session/{id}` share ONE per-IP budget
(`chat-op:{ip}`, `_SESSION_OP_RATE_LIMIT`), separate from `/message`'s. Only `/message` used to be
gated, so a single valid session token bought UNLIMITED calls to the rest — each doing real DB work
(a topic select writes `context_reset_id`, a resume reads 50 messages) — and the 20-sessions-per-IP
create budget turned into unbounded DB load. The cap is deliberately generous (like
`_CATALOGUE_RATE_LIMIT`): a real conversation taps a topic a handful of times (the widget's
auto-switch adds at most `MAX_AUTO_SWITCHES` per turn), resumes on reload, and finishes once, so it
only catches scripted abuse. It runs BEFORE `_auth_session`, so a blocked call costs no session
lookup — and it is additive, never a replacement for the token check.
Rate-limit and cooldown use **in-memory dicts** — fine for Phase 1 but they do not span multiple
instances. Turnstile is verified at session create and skips gracefully (logged) when no secret
is set, when the client sent NO token (the Turnstile script is blocked in some regions —
fail-open by design), or on a verifier outage; only an explicit "invalid token" verdict from
Cloudflare 403s. **High-volume block events are SAMPLED**
(`db.log_admin_event_sampled`: `rate_limited`, `injection_blocked`, `low_content_blocked`,
`turnstile_skipped`, `model_error` — max 20 per type per 5 min, in-memory): each rejected request
used to insert an `admin_events` row, so a hammering attacker grew the table without bound even
while being 429'd. Security-critical singular events (escalation, failover, login failures) stay
unsampled. The **request-body cap** middleware (main.py) also rejects chunked bodies
(no `Content-Length` + `Transfer-Encoding` ⇒ 411) — a chunked request would otherwise bypass the
declared-length check entirely and still be buffered whole by the JSON parser.

The **IP key** comes from `app/api/client_ip.py` `client_ip()`, which trusts `X-Forwarded-For` **only**
when the immediate socket peer (the TCP source, which a public client cannot forge) is in
`config.TRUSTED_PROXY_IPS`; then it reads `TRUSTED_PROXY_COUNT` hops from the RIGHT. The default
trust list is the **private/reserved ranges** (RFC1918 + CGNAT + loopback/ULA), so on Railway/most
PaaS the real client IP resolves correctly out of the box without trusting spoofable XFF — an
attacker on the public internet has a public peer IP and is never trusted. This is a
**network-perimeter deploy var → Railway env, not the admin panel** (like `CORS_ALLOW_ORIGINS` /
`TRUSTED_PROXY_COUNT`): a compromised admin must not be able to disable spoofing protection.

**Sessions are created lazily by the widget — and the topic tap paints the chat INSTANTLY.**
`POST /session` (Turnstile + token + DB row) fires only when the player actually picks a topic
(`onTopic`), NOT on panel open — the old open-time warm-up minted a DB session (and burned the
per-IP `session:` budget) for every visitor who opened and closed the widget without engaging.
The topic picker still paints instantly from the session-free cached `GET /topics`. The tap
itself is **optimistic**: `onTopic` shows the conversation view + the canned greeting bubble
immediately (both are client-side) and runs the slow setup — Turnstile token + `POST /session` +
`POST /topic` — in the background (`state.setupPromise`); the player's first `sendMessage` awaits
that promise, so the send transparently waits for the token instead of failing (it used to await
the whole session create BEFORE showing the chat, freezing the picker for seconds after the tap).
A failed setup returns the player to the picker with the localized start error. The Turnstile
script itself is pre-loaded at widget mount (`loadTurnstile` in `buildUI` / `fetchI18n`) — it's a
third-party fetch and was the slowest piece of the tap-time setup. Every Turnstile step (script
load, invisible render, token callback) races a timeout and degrades to a **null token**; the
backend then skips the check (advisory), so a blocked `challenges.cloudflare.com` can never wedge
or kill session creation.

The **low-content guard** (`antispam.check_low_content`) stops messages with nothing to
answer — a lone character, symbol/emoji-only spam, or one character mashed over and over
(`"a"`, `"???"`, `"aaaaaa"`) — **before** the model call, so a bot or idle user typing one
char at a time in a loop can't keep burning OpenAI tokens. A message must carry at least
`min_meaningful_chars` (default 2) distinct letters/digits. Unlike a hard reject, it returns
a localized model-free nudge as a normal `200` turn (`low_content_reply`), logs
`admin_events('low_content_blocked')`, and does **not** persist the turn or count it toward
the message cap. Both knobs (`low_content_block` master switch, `min_meaningful_chars`) live
in the hot-reloaded `antispam` settings group.

### Language resolution (`language.py`)
**The chrome STARTS at the browser language; both the answers and the chrome FOLLOW the
player.** The widget opens in the browser language (resolved client-side, no flicker), but the
*conversation* switches to whatever supported language the player actually writes in: open in
Russian, start typing English, and the answers move to English — and the widget chrome
(buttons, labels, the canned greeting, topic titles) re-localizes to match, so the whole
widget moves together.

The browser locale is still the **starting** answer language. Deterministic resolution for the
session's base/UI code: `locale` (e.g. `es-MX`→`es`; this is where the browser's
`navigator.language` lands) → `AUTO` (→ `DEFAULT_LANGUAGE`). `create_session` resolves it and
stores it on `chat_sessions.lang` (the browser/UI language — never overwritten by the drift
below).

**Answer-language drift (`chat_service` + the Layer-3 directive).** Each turn the base/fallback
language is the session's sticky `conv_lang` (the language the player last switched to) if set,
else `chat_sessions.lang`. The Layer-3 `_language_directive` tells the model to answer in the
language of the player's **current** message when it is one of the `supported` codes, and to
fall back to the base only when the message is too short / numeric / emoji-only or written in an
unsupported language. The model reports the language it answered in via a `[[LANG:xx]]` sentinel
on its first line; `chat_service` strips it (`prompts.strip_language_tag`, mirroring the
`[[ESCALATE]]` / `[[TOPIC:slug]]` strips), validates the code against `supported`, uses it as the
turn's `answer_lang` (escalation/contact copy + metadata), and — when it differs from the stored
value — persists it to `chat_sessions.conv_lang` so later turns stick to it (including the
model-free message-cap and low-content paths, which read `conv_lang` → `lang`) until the player
switches again. Stickiness also rides the prompt history: the model sees the prior turns, so an
ambiguous follow-up stays in the language the conversation drifted to. No separate detection
call — detection is the model's, at no extra cost.

The **widget chrome** language is resolved **synchronously, before the panel is ever painted**
(`widget.js` `resolveLang`): browser `locale` → English. Resolving the *starting* language on the
client (the locale is available immediately) kills the old "opens in English, then jumps to
Russian a few seconds later" flicker, where the chrome only learned the real language from the
slow `/session` round-trip. After that, the chrome **follows** the conversation: each `/message`
response carries the answer `lang`, and `widget.js` `maybeSwitchLang` re-localizes the shell
(static labels, the greeting bubble, and a background topic-title refresh) whenever that `lang`
drifts to another supported language. Async responses (`/topics`, `/session`) still **follow**
`state.lang` and never redefine it. The set of supported languages still comes from the
hot-reloaded `language` settings group (`default` + `supported`).

### Escalation (`escalation.py`) — two strengths: HARD closes, SOFT keeps chatting
Escalation returns a contact-button payload only (no form, no live agent, no ticket/Telegram
notifier). The payload carries a **`final`** flag mirroring the split:
- **HARD (`final=true`)** — the model's `[[ESCALATE]]` sentinel, the **hard message ceiling**
  (soft cap × `escalation.HARD_CAP_MULTIPLIER`, the cost backstop), or the explicit
  `/escalate` tap (incl. the cap notice's "hand over to a human" button — see below).
  `db.mark_escalated` sets `status='escalated'` (+ `escalated=TRUE`), the widget
  ends the conversation, and further turns 409. `decide()` covers exactly these post-model
  triggers (ceiling first, then `model_signalled`); the old `already_escalated` auto-trigger branch
  is **gone** — a soft-escalated session keeps chatting normally.
- **SOFT (`final=false`)** — the keyword triggers (high-risk fraud/legal stems, explicit ask for a
  human), checked by `escalation.keyword_trigger()` in `chat_service` **BEFORE the model call**
  (they don't depend on the model, so the hand-off turn burns **no tokens**; the turn is persisted
  with `ai_meta=None` and the reply text rides only in the escalation card, `reply=""`).
  `db.mark_escalated_soft` sets only `escalated=TRUE` — the session **stays `open`** and the widget
  keeps the composer, so a fuzzy stem false positive can never kill a live conversation. Metrics
  and the Unresolved queue still see it (they key on the `escalated` flag / open status). A later
  hard trigger upgrades it: the hard paths call `mark_escalated` **unconditionally** (idempotent)
  and guard the duplicate `admin_events('escalation')` row on `status != 'escalated'`, not on the
  `escalated` flag.

The keyword scans run on a normalized copy of the message (NFKC + zero-width strip, via
`antispam._normalize_for_scan`) so obfuscation can't hide a trigger. Matching is **word-boundary
aware** (`_matches_keywords`), not raw substring: a phrase (with a space) matches as a substring; a
stem matches only at the **start of a word** (`поддержк` → «поддержку», never mid-word); a **short
stem (≤3 chars) must equal a whole word** — so «судя по всему»/«судьба»/«рассудите» no longer trip
the `суд` stem (the substring matcher used to escalate-and-close on those). Both lists live in the
`escalation` settings group — `high_risk_keywords` and `human_request_keywords` — and their ONE
admin editor is the **Common → Escalation keywords** page;
the group is deliberately skipped in the generic Settings tab so the same knob is never editable
from two places. The constants in `escalation.py` are only the built-in defaults.

**The message cap is SOFT — it explains itself and offers a choice (`cap_notice`).**
`max_messages_per_session` is a technical limit in the **`general`** settings group (the legacy
`escalation.max_messages_per_session` DB override is still honoured as a fallback). The turn whose
prospective count (current + 1) reaches it — and every turn after — is **still answered by the
model**, but the `/message` response swaps the suggestion bubbles for **`cap_notice`**
(`escalation.cap_notice_payload`): `{message, escalate_label, finish_label}` — a stable, localized
explanation that the chat has stalled ("we've been at this a while without a solution") plus the
two ways forward. The copy lives in the **translations registry** (`cap_notice_message`,
`cap_escalate_button`, `cap_finish_button` — admin Translations tab, never model-generated). The
widget (`widget.js` `renderCapNotice`) renders it in the suggestion strip: the amber
**escalate button on top** (→ `POST /escalate`, the normal explicit HARD hand-off — so it lands in
the retention-bot deeplink / contact card exactly like every other escalation path) and the green
**finish button below** (→ `POST /resolve` via `finishChat`). If the player keeps chatting, the
notice re-renders on every further turn (suggestions/closing/resolved are suppressed alongside it);
it is skipped on a hand-off and on a topic switch. The notice also covers the **wrap-up window**
(the last `_TURN_BUDGET_NOTICE_TURNS` turns before the cap): there the turn-budget directive pushes
the model to wrap up, it marks `[[RESOLVED]]`, and the widget used to swap the bubbles for a single
out-of-context green "End chat" button (the observed jarring case — no explanation, no way to
escalate). A `resolved` turn inside the window therefore returns `cap_notice` (with
`resolved=false`) instead of the lone button; a resolved turn in a short, normally-finished chat is
untouched. The old behaviour — force-escalate and close
exactly at the cap with no explanation — is gone. The **HARD ceiling** remains as the cost
backstop: at `max_messages_per_session × escalation.HARD_CAP_MULTIPLIER` (2) `decide()` fires
`message_cap` and the model-free fast path in `app/api/chat.py` short-circuits a session already
at/over the ceiling — complementary, not a duplicate. The model is kept in the loop:
`chat_service` passes `turns_left` (soft cap minus this turn's prospective count) into
`build_messages`, and `prompts._turn_budget_directive` adds a Layer-3 wrap-up notice on the last
`_TURN_BUDGET_NOTICE_TURNS` (2) turns — and, at/past the cap, tells the model the escalate/finish
notice is on screen: answer fully but briefly, open no new threads, hand off deliberately
([[ESCALATE]]) if the issue cannot be finished here. It is per-request, so it never
touches the byte-stable Layer 1, and it is skipped on a `closing` turn (the goodbye directive
already ends the chat). Tests: `tests/test_cap_notice.py`. The button URL is **per-language**: the
`contact_url` key in the translations registry (admin Translations tab — each language can point
at its own contact form) — **the ONE home for the URL**. A legacy hidden value stored by early
builds in `app_settings.general.contact_form_url` (the old Settings tab wrote it; the field then
left the UI, leaving a link the owner could not see or edit anywhere) is **auto-migrated on
boot** (`db._migrate_legacy_contact_url`, one-time) into the default product's Translations as
`en.contact_url` and the legacy key is deleted. The `CONTACT_FORM_URL` env var remains only as a
deploy-level default that applies **only to the boot-seeded default product**
(`tenancy.is_default_scope()`, gated in `escalation.build_payload`) — a deploy/DB fallback must
never leak one brand's contact link into another partner's product, so every non-default product
gets its URL exclusively from its own admin Translations tab (empty until set; the widget then
renders the card without a button link).

**Escalation routes INTO the retention bot when the product runs one
(`escalation.build_payload_for_session`).** The `contact_url` above is the DEFAULT
target (a form / support group / chat). But when the session's product has
`retention_enabled` **and** a `telegram_bot_username`, the escalation button is
**replaced**: instead of the static link it carries a freshly-minted, one-time
*escalation-entry* retention deeplink (`https://t.me/<bot>?start=<nonce>`,
`entry_type='escalation'`), so tapping it drops the player into Nika's Telegram bot —
which runs the **channel-subscription gate** on `/start` (they subscribe on the way in)
and offers **"go to a manager"** in its menu (the escalation entry). The player's session
profile snapshot rides in the nonce, so Nika greets them by name. This is the **primary
channel** behaviour: the WIDGET's own escalation card does the hand-off (the site "Написать
Нике" button is a secondary, optional integration — see the retention section). The payload
gains a `retention: true` marker so API consumers / the widget can tell the hand-off leads to
the bot. It is per-product (the product is resolved from the session's `product_id`, the
deeplink uses that product's bot + product-scoped nonce settings and stores that product's id)
and **fully graceful**: retention off, no bot, or any mint failure ⇒ it falls straight back to
the static `contact_url` (escalation never breaks). Every hand-off path routes through this one
helper — the pre-model SOFT keyword card, the post-model HARD `[[ESCALATE]]`/decide, the
message-cap fast path, and the explicit `/escalate` tap — so the bot hand-off is consistent
everywhere. The nonce is TTL-bounded (`retention.nonce_ttl_sec`, default 120s) and minted at
response time; raise that knob for a product whose players sit on the card. The
`CONTACT_FORM_URL`/`contact_url` fallback is otherwise unchanged.

**A transient model failure does NOT escalate.** When the OpenAI call fails outright (retries +
failover exhausted — e.g. a provider outage), `chat_service` returns a localized model-free
"technical hiccup, please resend" nudge (`_MODEL_ERROR_REPLY`), persists **no** turn (the player
just resends), logs the failed call to `ai_interaction_logs` (invariant §4) and a sampled
`model_error` admin event. Previously this path escalated AND closed the session — an OpenAI blip
killed every live conversation.

**A HARD hand-off ends the bot conversation.** Once a session is `status='escalated'` (or
`resolved`), `app/api/chat.py` `_ensure_open_session` rejects further mutating turns (`/message`,
`/topic`, `/escalate`) with **HTTP 409 `session_closed`** — only an `open` session is chatable.
The widget mirrors this: on an `escalation.active` turn with `final !== false` it shows the
contact card and then calls `endConversation()` (hides the composer, drops the local session
credentials); on a soft card (`final === false`) it shows the card and keeps the composer.

### Topic routing (`[[TOPIC:slug]]` sentinel)
Only the selected topic's KB is loaded (Layer 2), so a question that belongs to a *different*
topic can't be answered well. To bridge this, Layer 3 lists the other topics (`kb.suggestable_topics`,
current topic + `other` excluded — `other` is a visible topic but never a routing *target*) and
instructs the model to prepend `[[TOPIC:slug]]` on its
own first line when the question plainly belongs to one of them. `chat_service` strips the tag
(`prompts.strip_topic_suggestion`, mirrors the `[[ESCALATE]]` strip), validates the slug against the
offered list, and returns `suggested_topic:{slug,title}` in the `/message` response. The topic list is
dynamic data → Layer 3 only; it must never enter `SYSTEM_CORE` (a test asserts the cached prefix stays
byte-stable).

**Routing-only turn — the in-place answer is SUPPRESSED, the switch is AUTOMATIC.** A cross-topic turn
is a *routing decision*, not an answer: the in-place reply the model produced was generated **without**
the target topic's KB loaded, so it is ungrounded (potentially invented numbers/conditions) and must
never reach the player. When `chat_service` resolves a valid `suggested_topic` (and the turn is **not**
an escalation), it short-circuits: it returns `reply=""` + `suggested_topic`, **persists no chat turn**
and does **not** bump the message cap (the re-ask below is the one persisted, counted turn) — but it
**does** log the detect call's token cost via `db.log_ai_interaction` so OpenAI spend stays accounted
(invariant §4: every OpenAI call → an `ai_interaction_logs` row, here without a `chat_messages` pair).
It also writes a `db.log_admin_event('topic_switch', {from, to, trigger, cost_usd, ...})` marker: because
this turn persists no `chat_messages` row, its detect-call cost would otherwise look orphaned in the
admin transcript and the per-turn costs would not sum to the session total. `db.session_detail` returns
these as `events`, and the admin SPA interleaves a "switched X → Y · $cost" marker into the timeline by
`created_at` so the whole path (original ask → switch → grounded answer) is traceable with each step's cost.
The widget (`widget.js` `autoSwitchTopic`) then drops a persistent **"switching to «X»…"** notice into
the transcript (informational, **no button** — it stays as the record of the hand-off), calls
`POST /api/chat/topic`, and after a short legibility pause (`SWITCH_NOTE_MS`) **re-asks** the player's
original question against the new KB — that second `/message` is the grounded answer the player sees.
`applyTurnExtras` carries a `depth` guard (`MAX_AUTO_SWITCHES`) so a misbehaving model can't bounce the
player across topics forever; when the guard trips, the widget shows a localized "couldn't settle on a
topic, please rephrase" fallback (`switchStuck`) instead of ending the turn with no reply at all (the
routing-only response is empty, so without the fallback the chat looked frozen). (This replaced the earlier flow where the wrong-KB answer + a one-tap
"switch topic" button were both shown and the player had to tap to proceed.) Net token cost is unchanged
vs. that flow — still one detect call + one grounded answer call — but no ungrounded text is ever shown.

**One routing regime for every topic (`prompts._topic_routing_directive`).** Every topic — the six
specialized ones **and** the general `other` topic — is routed the same way: the model is *anchored* on
the current topic, answers in-topic questions from the loaded KB (or escalates), and switches **only**
on a genuine mismatch. The decision keys on the player's **intent**, not isolated keyword overlap — so
"how do I withdraw?" asked under Deposits routes to Withdrawals, while a shared term (crypto networks,
verification, limits) that also fits the current topic does **not** trigger a switch. This keeps
cross-topic tracking active without ping-pong.

`other` is **not special** — and it is **never hidden** (there are no hidden topics at all). It is a
normal, player-selectable topic in the server catalogue: `db.list_topics` returns the FULL topic list
with `other` sorted last (its one special treatment — as the always-available escape hatch it closes the
picker), and the widget renders the catalogue as served, only keeping the distinct purple styling for
the `other` slug (plus a client-side fallback button if a catalogue ever arrives without an `other`
row). It has its **own** ~50-entry KB, so it answers from that KB exactly like the others. In
practice it sends players onward to a specialized topic more often (it is the general entry point), but
that falls out of the same intent test, not a separate "route actively / don't answer from your own KB"
mode. An earlier design treated `other` as a thin KB-less catch-all and force-routed everything off it —
that **reversed** the anchor and broke any question whose answer actually lived in the `other` KB (e.g.
"how do I change the language?" was force-routed to Technical, which had no such entry, dead-ending the
chat). That special branch was removed. (`other` IS excluded from `suggestable_topics` — a routing
decision, not visibility: it is never offered as a switch *target*, so the model can route *out of* it
but not dump a player *into* it.)

**Switch boundary (anti-ping-pong):** `set_session_topic` snapshots the current max `chat_messages.id`
into `chat_sessions.context_reset_id`, and prompt-building history (`db.get_history(..., after_id=...)`
in `chat_service`) only feeds the model turns newer than that boundary. Without it, switching topics
re-sent the *whole* prior transcript; the model saw the old topic's conversation (now re-listed as a
suggestable topic) and kept suggesting switching back — an endless loop. After a switch the first turn
carries only the triggering message, so the new topic is the only thing in context. The **full**
transcript is untouched — resume (`GET /session/{id}`) and the admin session view both call
`get_history` without `after_id`, so the player and admins still see everything.

### Suggested follow-up questions + finish-chat (`[[SUGGEST:…]]` / `[[RESOLVED]]` sentinels)
To pull the player toward the exact KB entry their question is closest to, the model emits — along
with its answer — two sentinels (mirroring the `[[TOPIC:slug]]` machinery), both **stripped** before
the reply is shown. Their directives are STATIC, so they ride in the byte-stable Layer-1 block:
- **`[[SUGGEST: q1 | q2]]`** (own LAST line) — up to **two** short follow-up/clarifying *questions*
  phrased **from the player's point of view** (first person), pipe-separated, whose answers ARE in
  the KB. **The closing option is NOT generated by the model**: `chat_service` appends its own
  fixed, localized `closing_suggestion` (`chat_service.closing_suggestion_for` — "Issue solved." /
  «Проблема решена.» / …) whenever guiding questions are shown, so its wording is always exact and
  it reliably ends the chat. `prompts.strip_suggestions` parses + caps at
  `prompts._MAX_SUGGESTIONS` = 2 and keeps only items ending with `?` — a declarative option the
  model still emits out of old habit is **dropped** (an earlier design had the model generate the
  closing option as a third item and normalized the last item to end with a period; that turned a
  third *question* from a non-compliant model into a chat-ending button, so it was replaced by the
  system-supplied option and `prompts.split_closing` was removed). The `/message` response carries
  `suggestions:[…]` (the ≤2 guiding questions) **plus** `closing_suggestion` (the system option, or
  `null` when there are no guiding questions / on escalation / topic switch). The widget renders
  the guiding questions as one-tap **bubbles** (`submitText`) and the closing option as a distinct
  soft-green **closing bubble**: tapping it sends a goodbye turn (Nika still generates a warm
  reply), then marks the session **resolved** (`POST /api/chat/resolve`) and ends the conversation
  — and crucially does **not** then show the green finish button (the player already chose to
  finish). Stale bubbles clear the moment a new turn starts.
- **`[[RESOLVED]]`** (own line) — set when there is nothing more to offer on the current question.
  The trigger is deliberately **broad** (`prompts._RESOLVED_DIRECTIVE`): not only an explicit
  thanks/confirmation, but also when the question is essentially answered and **no suitable KB
  follow-ups remain**. `chat_service` (`prompts.strip_resolved_tag`) returns `resolved:true` and the
  widget surfaces a green **"finish chat"** button below the bubbles. Tapping it calls
  **`POST /api/chat/resolve`** (`db.mark_resolved` → `status='resolved'` + an `admin_events('session_resolved')`
  row) and collapses the panel — gently steering the satisfied player toward ending the chat, and
  dropping the session out of the open-session metric. The close never overrides an **escalated**
  session (a pending hand-off to a human must survive the player tapping finish), and the call is
  best-effort (the panel collapses regardless). The directive tells the model NOT to set the tag while
  still clarifying. **The green button is the MODEL-driven finish; the closing bubble above is the
  PLAYER-driven finish** — the widget shows only one at a time (`resolved` wins, so the two finish
  controls never appear together).

**Lead-forward (no dead end, `prompts._LEAD_FORWARD_DIRECTIVE`).** Earlier the two directives left a
gap: when the exchange was complete but the player hadn't thanked and no good follow-ups existed, the
model emitted **neither** tag, so the reply ended with no bubbles AND no finish button. This STATIC
Layer-1 directive ties them together: whenever the exchange on the current question is complete and the
model is not itself asking a clarifying question, it MUST end with `[[SUGGEST]]` (if good KB follow-ups
exist) **or** `[[RESOLVED]]` (if nothing is left) — and may emit both when there are follow-ups yet the
core question is already resolved. Escalation is the only exception.

On a hand-off both are suppressed in `chat_service` (the player is going to a human, so the
guide-to-KB bubbles and the close nudge are out of place) — the backend guarantee behind the directive's
escalation exception. All three directives ride in the byte-stable Layer-1 block (a test asserts it).
The model-free paths (message-cap, low-content) return neither, so the widget simply shows no
bubbles/finish button there.

### Two layers of injection defense
1. `prompts._sanitize_field` zeroes any `user_context` field containing injection markers
   (only `id, full_name, email, activation_status` are surfaced to the model).
2. `antispam.scan_injection` scans the user message (normalized first, so spacing /
   zero-width / Unicode-confusable obfuscation can't hide a known trigger) and **logs**
   `injection_blocked`. Matching is **word-boundary aware** (`_compile_injection_res`):
   each trigger phrase is a `\b`-anchored regex with `\s*` between tokens, so a stem like
   "act as" is caught as whole words / with the separators obfuscated away but NOT inside
   "contact as" / "react as" / "impact assessment" (plain substring matching, esp. the old
   fully-de-spaced view, hard-blocked ordinary messages like "contact a support agent").
   With `injection_hard_block` (**on by default**, tunable in the `antispam` settings group)
   it also **rejects** the turn with HTTP 400 before the model call, so a jailbreak burns no
   tokens — **except** when the message is ALSO a keyword-escalation trigger
   (`escalation.keyword_trigger`: complaint / fraud / ask-for-a-human): the injection gate
   in `app/api/chat.py` runs BEFORE the pre-model SOFT escalation, so it deliberately does NOT
   hard-block such a message (it would swallow the human hand-off) — it flows through to be
   escalated instead, the audit row still recording the injection signal. `SYSTEM_CORE` +
   the Layer-3 guardrails remain the substantive defence.

### Off-topic / forbidden-topics guardrail (`prompts.FORBIDDEN_TOPICS`)
A Layer-3 line (`prompts._forbidden_topics_directive`) injects the
`prompts.FORBIDDEN_TOPICS` list + `prompts.FORBIDDEN_TOPICS_REFUSAL` wording into the user
message, so the model refuses off-topic and unsafe asks (programming, essays, politics,
medical/legal/financial advice, competitors, "guaranteed-win"/cheat schemes, general
knowledge, etc.) on top of the always-on `_GUARDRAILS` topic restriction. These are
**constants in `prompts.py`** — part of the prompt, so they live in the single source of
truth, not the admin panel. Ships non-empty (off-topic blocking works out of the box); set
`FORBIDDEN_TOPICS = []` in the file to disable it. The refusal is a template the model
localizes to the player's language. Lives in Layer 3 only, so `SYSTEM_CORE` stays byte-stable
(a test asserts it).

### RETENTION BOT + RETENTION AGENT — Telegram second facade (lazily loaded)
A **second front-end over the same AI core**: a Telegram bot where Nika runs
retention only (engagement + photos), plus the event-driven proactive agent and
the idle-ping ladder. Modules: `retention.py`, `retention_v2.py`,
`retention_idle.py`, `telegram_transport.py`, `telegram_format.py`, `delivery.py`,
`media_normalizer.py`, `player_sync.py`, `outcomes.py`. **The full spec is the
`retention-bot` skill (`.claude/skills/retention-bot/SKILL.md`) — load it before
touching any of those modules, the bot's behaviour/copy, proactive or idle
messaging, retention media, or the retention KB/prompt variables.** All
invariants below hold for retention turns too.

### EVENT PIPELINE — the durable leased queue (`retention_v2.py`, `send_worker.py`, `db.py`)
The canonical event feed is a **queue with a lifecycle**, not a log the worker walks:
`retention_events.status` moves `pending` → `processing` → `done`/`dead`, with `attempts`,
`locked_until`, `next_attempt_at`, `priority`, `last_error` and `worker_id` alongside it.

**A claim is a LEASE the caller MUST close.** `db.claim_retention_events` flips a batch to
`processing` with an expiry in the same statement that selects it (`UPDATE … WHERE id IN
(SELECT … FOR UPDATE SKIP LOCKED)`). The old claim stamped `processed_at` at SELECTION time,
so an event whose pipeline then threw — or whose worker a deploy killed mid-batch — counted as
processed and was gone: no retry, no dead-letter, no trace. Now every claimed row is closed by
`db.complete_retention_event` (success) or `db.fail_retention_event` (error: `attempts` + 1,
exponential backoff off `event_backoff_base_sec`, `dead` at `event_max_attempts`), and
anything still owed when the batch unwinds — a stop signal, a cancelled task, an error outside
the per-event guard — goes back with `db.release_retention_events`. `run_product_events`
tracks that as an explicit `owed` set in a `try/finally`; losing that bookkeeping is precisely
how the old pipeline dropped events on the floor. The backstop for a worker that never got to
any of it is `db.reclaim_expired_event_leases`, run first thing in `maintenance_loop` and
again at its start-up (a crash-restart's own leases come back immediately instead of waiting
out `event_lease_sec`). `event_lease_sec` must exceed the agent model timeout with real margin
— a lease expiring mid-decision means the event is processed twice. Dead rows are not silent:
`db.list_dead_retention_events` / `requeue_retention_events` back `GET /admin/retention/v2/
dead-letter` + `POST …/requeue`, and a dead-lettering pass writes an
`admin_events('retention_events_dead_lettered')` row.

**Delivery is AT-LEAST-ONCE, so the decision row is RESERVED before any side effect.** A lease
can expire and a reclaim can replay: "the pipeline ran this event twice" is now a normal
occurrence, and the thing that must never double is what the PLAYER sees. `_process_event`
inserts the `retention_v2_decisions` row **before** the bonus grant and before the send, and
the unique partial index on `(product_id, event_pk) WHERE event_pk IS NOT NULL AND action <>
'skipped'` turns the replay's insert into a no-op: `db.insert_retention_v2_decision` returns
**`None`**, and `None` means *stop, someone already decided this* — the pipeline returns
`"duplicate"` without touching the player. What the touch actually became is written back
afterwards by `db.update_retention_v2_decision` (the action can still change: a failed offer
grant demotes it to `silence`). `action='skipped'` rows sit outside the index on purpose —
they are diagnostics, several per event are fine, and they send nothing. The insert used to
sit AFTER the send, which left nothing between a retry and a second message. **Any new
player-visible side effect goes after the reservation, never before it.**

**Priority lanes and the state-food bypass are decided at INGEST.** `player_sync.EVENT_PRIORITY`
/ `event_priority()` stamp a lane on the row (1 = transactional — deposit / withdrawal / KYC …
5 = state food — settled bets, session pings), so the claim can serve lanes without a join and
a backlog can be shed by lane. `player_sync.should_queue()` goes further: a state-food event is
stored **COMPLETE** and never enters the drain at all — it exists to move the activity counters
and feed the deterministic state resolver, and both read the stored row directly. Otherwise
queue depth is a function of casino traffic instead of decision work, and the events that
deserve a reaction queue up behind spins. Two escapes: anything the product listed in
`v2_decision_events` (a deliberate operator promotion) and `bet_settled` while a loss threshold
is configured (it is the input to the 24h loss window). The **backpressure ladder**
(`_max_priority_for_lag`) reads `db.retention_queue_lag_by_lane` and lowers the claim's
`max_priority` as it grows: past `queue_degrade_p3_sec` state food stops
being claimed, past `queue_degrade_p2_sec` only the transactional lanes move, and past
`queue_degrade_idle_sec` the idle ladder pauses for that product — re-engaging quiet players
must never compete with reacting to live ones. A degraded pass logs and writes a sampled
`retention_queue_degraded` admin event.

**Two rules make that ladder measure the right thing** (it shipped violating both, which pinned
every busy product at `max_priority=2` permanently). **Lag is HOW LATE work is, not how old it
is**: the claim will not take a row until its humanizing send delay has elapsed (300..900s by
default) and past any retry backoff, so the lag query both applies the claim's due predicate
(`db._QUEUE_DUE`) and measures `now() - (created_at + delay)` (`db._QUEUE_OVERDUE`), clamped at
0. Filtering alone is not enough — every row that survives the filter has by construction waited
at least `delay_min`, which IS the p3 rung, so reporting age would leave the ladder pinned even
with the filter in place. A row that has just become claimable is 0 seconds late. And **each rung
reads the lanes it does NOT shed** — the rung that stops claiming lane 5 reads lanes 1-3, the rung that
stops claiming lane 3 reads lanes 1-2. Keyed on the whole queue, shedding was a one-way door:
the shed lanes stayed pending, so they stayed the oldest rows and held the ladder down until
the pruner deleted them. `retention_queue_lag_by_lane` returns `{2, 3, 5} -> seconds` in one
query for exactly this; `retention_queue_lag` is the scalar the admin view and
`retention_queue_stats.lag_sec` report. `_max_priority_for_lag` still accepts a plain int,
meaning "every lane that far behind". The **idle-ladder pause follows the same rule** — it reads
`lag_by_lane[3]`, the lanes the drain is still serving, so a state-food backlog the ladder has
deliberately shed cannot pause re-engagement forever.

The activity-timestamp bridge, the busiest write in the
system, is debounced (`activity_debounce_sec`); re-stamping "active" seconds later buys nothing.

**Parallelism — and why per-player grouping is CORRECTNESS, not speed.** The one global
advisory lock is gone. It made the tick duration the SUM over every product, put every
maintenance sweep on the critical path of reacting to a deposit, and gave a second service
instance no background work at all. `run_due_events` drains products **concurrently**
(`worker_product_concurrency`); inside a product, `_group_by_player` splits the claimed batch
into per-player chains that run concurrently (`worker_player_concurrency`) but **strictly
serially within a player** — two decisions for the same player read the same guard counters
before either writes, which is how one player collects two messages for two events that should
have produced one. Do not flatten the groups for throughput. A failure inside a chain **stops
that chain** (the player's later events assume the failed one landed) and leaves the rest to
the retry.

Grouping alone only serializes ONE worker's own batch, so the same rule is enforced **in the
claim SQL**: a player who already has an event `processing` anywhere is skipped whole, and a
transaction-scoped `pg_try_advisory_xact_lock` on `(product, player)` — taken AFTER the `LIMIT`,
so a claim can never lock a whole backlog — closes the instant where two claims both see "none
in flight". The lock dies with the claim's transaction; from then on the `processing` rows are
what keep the other worker out, and because the lock is re-entrant every event of the winning
player still arrives in the same batch, which is what the grouping wants. That pair is what
makes the mutual exclusion the CLAIM rather than a worker-local convention — so several workers
may drain one product safely, and so may the admin «Process queue now» button
(`run_product_events_locked` keeps its name and its `WorkerBusy` escape hatch but no longer
locks, and can no longer 409 just because the sweep is mid-pass). Background model calls are
additionally bounded per process by `_model_slot()` (`agent_model_concurrency`) so a burst
cannot open hundreds of completions.

**The queue SQL cannot be validated by the test suite.** `tests/conftest.py` stubs asyncpg, so
a test can assert which statement a helper issues but never that Postgres accepts it — and
three statements in this pipeline shipped broken past a green suite (asyncpg infers a bare `$n`
inside `make_interval(secs => $n)` from the function signature, but inside an EXPRESSION —
`GREATEST($3,$4)`, `$2 - $4` — it has nothing to infer from, prepares `unknown`, and Postgres
rejects the call at prepare time). `scripts/check_queue_sql.py` runs the lifecycle, the lane
ceiling, the player exclusion, the pacing table, the token bucket, the send queue and the
upgrade-from-a-legacy-database path against a real server. It is not in `preflight.sh` (no
asyncpg, no database there) — run it by hand, against a scratch database, whenever you touch
queue SQL.

**Maintenance runs off the event path, paced in Postgres.** `maintenance_loop` owns lease
reclaim, the idle ladder, attribution, scoring, activity profiles, journeys and — only while
the send worker is off — delivery retries. Each sweep is paced PER PRODUCT through
`retention_worker_jobs`: `db.claim_worker_job` flips `next_run_at` atomically, so exactly one
worker runs a given (product, job) per interval, and `db.finish_worker_job` records
status/duration (which is also the admin's background-jobs health view). The in-process dicts
this replaces were wrong twice over — they reset on every deploy, and two instances each kept
their own, so every sweep ran once per instance. Each sweep swallows its own errors: the whole
point of taking them off the event path is that a slow or broken one stops mattering.

**The SEND STAGE** (`send_worker.py`, behind `retention.send_worker_enabled`, **OFF** by
default; with it off `_send_touch` sends inline exactly as before and the loop finds nothing).
Deciding and sending used to be one loop, so a 10k broadcast was minutes during which no
deposit got a reaction, and a send that failed was retried by nobody. With the flag on, a
decision only ENQUEUES: `retention_deliveries` doubles as the send queue (`payload` = whatever
the channel adapter needs, `priority` inherited from the triggering event, `scheduled_at`
absorbing the humanizing delay and Smart Send Time, `locked_until`/`worker_id` under the same
lease discipline, and a deterministic `delivery_id` so a replayed decision cannot enqueue
twice). `send_loop` then claims a batch best-lane-first, takes a token from the **Postgres
token bucket** (`db.take_rate_token` over `retention_rate_budget`, one row per scope —
`tg:<product>`, `tg:chat:<chat>`, `email:<product>`) and **reschedules rather than sleeps**
when the bucket is empty (a held lease is a worker slot doing nothing), then opens the
attribution row and marks the decision delivered; a transient failure backs off [1m, 5m, 30m],
a permanent one (the player blocked the bot) never retries. The bucket is in Postgres because
the limit must hold across worker instances — Telegram allows ~30 msg/s per bot and ~1/s per
chat, and an in-process limiter is per replica. Its per-chat scopes grow one row per player
ever written to, so the maintenance loop prunes idle buckets (a bucket nobody touched is full
by definition).

**Process roles.** `config.SERVICE_ROLE` (`web` | `worker` | `all`, default `all`) splits the
service into two Railway services built from the SAME image: `uvicorn app.main:app` serves
HTTP; `python -m app.worker` runs the pipeline (drain, maintenance, send) plus the quality
judge and nothing else. A request-serving process and a background pipeline want opposite
things from a deploy — the web process must come up fast and never hold a connection for
minutes, the worker holds leases and long model calls — and sharing one process meant a web
redeploy killed the drain mid-batch. `main._background_plan` decides what a process owns:
`web` never starts the pipeline whatever `RETENTION_SCHEDULER_ENABLED` says (that switch is
the WORKER's master kill switch now), `all` is the pre-split single-process mode so an
existing deployment upgrades without a worker service, and an unknown role falls back to
`all` **with a warning** rather than silently running every sweep twice. **The media
normalizer stays on WEB**: it owns the FILES on the local media dir (on Railway a Volume
mounted to the service serving admin uploads), so it follows the volume, not the pipeline.
The worker serves `/healthz` on `$PORT` from a plain thread — deliberately not on the event
loop, since a wedged loop is exactly the failure the probe exists to report — and reports
each loop's own heartbeat, because a background process with no traffic looks identical
whether it is working or wedged. Shutdown is a **DRAIN, not a kill**: SIGTERM raises a stop
flag, every loop wakes out of `retention_v2._sleep_or_stop`, finishes the batch it is in and
closes its leases within `WORKER_DRAIN_TIMEOUT_SEC` (25s, under Railway's 30s SIGKILL);
skipping it would leave a lease-length hole in the queue's reaction time after every deploy.
A loop that returns or raises brings the whole process down (half-dead but healthy-looking is
worse than restarting — the leases are reclaimed either way).

**The knobs** are hot `retention`-group keys with env defaults in `config.py`
(`RETENTION_EVENT_*`, `..._WORKER_*_CONCURRENCY`, `..._AGENT_MODEL_CONCURRENCY`,
`..._QUEUE_DEGRADE_*`, `..._ACTIVITY_DEBOUNCE_SEC`, `..._SEND_*`, `..._TELEGRAM_RATE_*`, the
`*_INTERVAL_SEC` cadences, `..._EVENT_KEEP_DAYS[_STATE]` — the event log is pruned on a split
schedule because state food is 90%+ of the rows and worthless once the resolver's windows
passed). The deploy-wide ones are listed in `settings.GLOBAL_ONLY_FIELDS["retention"]`: a
product-layer value for a lease length or a fan-out width would be stored and never read.
Worker-side readers use `settings.global_retention_int/_bool/_raw` — the int variant collapses
a stored `0` into the default, which is why a knob whose point is being switchable OFF must go
through the raw/bool pair. Queue health is `db.retention_queue_stats` / `retention_queue_lag` /
`retention_latency_percentiles`, surfaced with the paced jobs on the Retention → Agent header.
Tests: `tests/test_retention_v2.py`, `tests/test_service_roles.py`,
`tests/test_send_worker.py`, `tests/test_queue_backpressure.py`.

**The queued touch carries its DECISION.** `_send_touch` passes the reserved `decision_id`
into `db.enqueue_delivery`, because with the send worker on the send happens in another
process minutes later and that process is the only one that knows whether the touch landed.
Without the link `send_worker._deliver_row` can write nothing back: every delivered touch
stays `queued` in `retention_v2_decisions` forever and the Agent ledger reports zero
deliveries for a bot that is sending normally.

### OUTCOME ATTRIBUTION — the measured feedback loop (`app/retention/outcomes.py`)
The stack could always say what it SPENT and what it SENT; this is what says
whether the sending WORKED, and it is the data every "which X performs" surface
reads. **One `retention_outcomes` row per DELIVERED touch** — an agent event
reaction, an idle-ladder ping, a photo/video handed to the player, or a dialogue
reply carrying a site-map CTA button (a plain text answer inside a live chat has
no outcome to attribute: the player is already talking). The row carries the
touch's DIMENSIONS denormalized (event / idle rung / tone / photo / link / cost)
on purpose — `retention_events` are pruned and media can be deleted, so an
attribution recomputed later would silently change. A **sweep** (`run_product_attribution`,
riding the retention worker tick, self-paced, no switch of its own — measuring
what already went out is not something a product opts out of) then fills in what
happened: did the player answer and how fast, how much he wrote, did he come back
to the casino, did he deposit. Windows are DEPLOY constants
(`RETENTION_OUTCOME_REPLY_WINDOW_HOURS` 48 / `..._CONVERSION_WINDOW_HOURS` 72):
they define what the stored numbers MEAN, so a per-product override would make the
history incomparable. A row whose windows elapsed is `closed` and never re-read —
that is what makes the figures stable. Recording is **best-effort by contract**
(`outcomes.record` swallows everything): analytics must never break a send that
already reached the player. Reads: `db.recent_touch_outcomes` (the agent's
feedback, below), `db.outcome_summary/_by_media/_by_link/_by_idle_rule/_by_event`
(the admin cuts + cost per reply/return/deposit), `db.top_links_by_outcome` (the
nudge's link hint) and `db._PHOTO_OUTCOME_SCORE_SQL`, which orders the photo
candidate feed by a **smoothed** reply rate (Laplace prior: an unproven item
scores a neutral 0.5, so one lucky send can't jump the queue) *after* stage and
freshness — the model still chooses, it just sees the better performers first.
Deleting a Telegram conversation purges the player's outcome rows with the rest of
his analytics footprint. Tests: `tests/test_outcomes.py`.

**The loop closes in the prompt.** The decision system prompt always said "if the
last proactive touches went unanswered, lean to silence" — the agent had no way
to know. Now `_decide` passes the player's last touches WITH their measured
reaction into `prompts.build_retention_v2_decision_messages`
(`prompts._touch_history_block`: "deposit_confirmed / message (celebrate) -> HE
ANSWERED in 4 min, 3 messages; he deposited afterwards"), and the ping WRITER gets
the actionable half (`prompts._touch_feedback_hint`) only when two-plus settled
touches in a row went unanswered: change the angle, do not repeat what already
failed. Both are Layer 3 (per-request data — never the byte-stable core), both
degrade to nothing when there is no history, and the touch lines are
machine-generated from the ledger, so no player-written text enters them. The
play-nudge's link rotation gets the same treatment: `prompts._proven_links_line`
adds the CTA pages that actually earned responses as an explicit **hint, not an
order** (the rotation rule and fitting the moment still win) — it only breaks the
tie the blind rotation was guessing at.

### RETENTION ORCHESTRATOR — measurement / RG / frequency / scoring / offers / journeys / channels
The DOC-0..DOC-7 layer over the proactive agent: eight additive mechanics, each
its own module in `app/retention/`, each behind a hot `retention`-group switch
(precedence product → global → env → default), each shipping OFF/dry-run EXCEPT
the RG guard (protection, ON) and the holdout (15% by owner decision). With the
switches off, behaviour is bit-for-bit pre-orchestrator. Admin surface: the
**Retention → Orchestrator** page (tabs per mechanic,
`admin/src/pages/Orchestrator.jsx`, API `app/api/orchestrator.py`) + the
on/off knobs in Retention → Settings (schema section `orchestrator`).

- **Measurement (`measurement.py`)** — the deterministic holdout control group:
  `sha256(product:player:salt) % 100 < holdout_pct` (cache on
  `retention_users.holdout_group/.holdout_salt`; salt rotation re-buckets
  lazily = a NEW experiment). Holdout is a GUARD: `guard_check` returns
  `held_out` BEFORE any model call and opens a **virtual outcome row**
  (`retention_outcomes.kind='holdout'`, `holdout_group_at_time`) so the control
  group has a measurable base rate; the idle sweep does the same per matched
  rung. `db.retention_uplift` cuts conversions (return / deposit) by the group
  AT touch time → `GET /admin/retention/uplift`. A player with no `player_id`
  is always treatment (cannot convert in the casino).
- **RG guard (`rg_guard.py`)** — responsible gaming, FIRST in the guard order,
  beats everything including holdout. The CASINO is the source of truth:
  `rg_status` (`ok/cool_off/rg_hold/self_exclude`) arrives via player-update
  (+ `marketing_consent`, `rg_flags`, `rg_status_until`) or is set manually
  (global-admin bridge while the feed is unwired). `self_exclude`/`rg_hold` =
  permanent block (no touch, no offer, ever); `cool_off` until expiry (lazy
  auto-clear) and a missing consent (gate `rg_require_consent`, OFF at MVP) =
  conditional: GAME triggers (`offer_grant`, `loss_recovery`, `idle_ping` —
  a come-back invite is game marketing) are blocked, a general event reaction
  passes WITH the no-play-talk constraint. Behavioral signals are config rows
  (`rg_signal_config`, shipped disabled; `computed` from the feed or
  `casino_flag` accepted precomputed). EVERY evaluation — pass included —
  lands in append-only `rg_guard_audit` (never pruned; read = global admin,
  the MVP compliance role). Dialogue side: `rg_guard.dialogue_suppression_due`
  → `rg_suppress` through `handle_retention_message` →
  `build_retention_dynamic_prompt` injects the RG PROTECTION block and drops
  the play nudge.
- **Adaptive frequency + Smart Send Time (`frequency.py`)** — when
  `adaptive_frequency_enabled` is ON, `guard_check`'s static gap/cap checks
  are replaced by the priority/cohort-aware gate: touch priorities P1..P5
  (`retention_touch_priority` + code defaults; P1/P2 are NEVER cut by a cap),
  cap matrix per channel × cohort (`retention_frequency_caps` + defaults;
  the VIP cohort runs relaxed rows; **email rides its own row** — by owner
  decision it never consumes the intrusive-touch budget, intrusive = push +
  Telegram). SST (`smart_send_time_enabled`) shifts NON-timing-critical
  touches (journey steps) into the player's active hours: deterministic
  activity profiles (`retention_activity_profile`, swept from the event feed)
  clamped to hint ± `sst_max_shift_hours`, never violating quiet hours.
  Player timezone: `retention_users.tz` from player-update (casino geo-IP) →
  else the product `quiet_hours_utc_offset`.
- **Scoring (`scoring.py`)** — dormancy cohorts
  (active/d7/d10/d14/d21/d30/lost, EPIC-5 boundaries, config-driven), banded
  deterministic RFM, value tiers by lifetime deposits, `vip_segment` mapped
  from the casino loyalty class (EPIC-7 classes player..platinum→mass,
  vip→vip, vip_plus→vip_plus; `vip_mapping` config). Cache on
  `retention_users`, recomputed by the paced sweep; the resolver appends the
  dimensions ADDITIVELY when `scoring_enabled` (OFF = snapshot unchanged).
  Return detection is HOT-path (`mark_recovered` on activity events);
  transitions log to `retention_cohort_transitions` (one per player/cohort/
  day). `scoring.is_vip` is the ONE VIP predicate (offers + frequency read it).
- **Offer engine (`offers.py`)** — the bonus-CMS-ID model (owner decision A5):
  a catalog row (`retention_offer_catalog`) references the casino's bonus by
  `partner_bonus_id`; granting = POST to the product's `offer_grant_url`
  ("credit bonus ID to player X"), idempotent by `offer_grant_id`
  (`og_` + sha1(product:player:decision-ref)) on both sides. Deterministic
  resolve BEFORE the model (trigger enabled → RG → VIP suppression on
  loss_high → eligibility → cooldown/lifetime → the SEPARATE stimulus budget,
  0 = blocked): only then does `grant_offer` enter `allowed_actions` with a
  constraint line describing the gift. Order invariant: create → partner
  confirms → ONLY THEN the persona mentions it; `fraud_hold` → message
  without the bonus, `failed` → silence. A high-loss VIP routes to
  `retention_host_tasks` instead of an auto-bonus. Triple-guarded defaults:
  `offers_enabled` OFF + `offer_dry_run` ON + zero budget.
- **Journeys (`journeys.py`)** — declarative multi-step trajectories as data
  (`retention_journeys`: trigger `{type: event|scheduled}`, entry/exit
  conditions, steps with mandatory `channel`, delays, per-step conditions).
  Event matching runs at the tail of `_process_event` (enrollment only —
  never an immediate touch); scheduled matching (recovery by cohort, weekly
  by day, cashier abandonment by the `deposit_initiated_at` timer) + the
  due-step drain ride the worker sweep. EVERY step passes the full
  `guard_check`. Blocked-step semantics (owner-approved Б4): frequency-class
  reasons defer the step (+2h, retried); terminal reasons (RG, opt-out,
  holdout, unsubscribed) exit the journey (`exited_terminal`). Exits: goal
  (`exit_conditions`, empty list = no goal), return (activity after
  enrollment; default for scheduled journeys, event journeys opt in via
  `metadata.exit_on_return`), completion. `eval_conditions` returns **None on
  an unresolvable field** — fail-safe: no enrollment / `blocked_unresolvable`
  exit, never a silent pass. One active enrollment per (player, journey)
  (partial unique index), capped by `journey_max_active_per_player`. That index
  is PARTIAL (`WHERE status='active'`), so it says nothing about a journey the
  player already FINISHED — and a scheduled trigger re-derives its candidates
  from live state that is still true the moment the enrollment completes. The
  re-entry cooldown (`_reentry_cooldown_days` + `db.last_enrollment_at`) is what
  stops the sweep re-enrolling — and re-sending — every couple of minutes: it
  defaults from the trigger's own granularity (weekly `day_of_week` → 7d,
  cashier abandonment → 1d, everything scheduled →
  `RETENTION_JOURNEY_REENTRY_COOLDOWN_DAYS`; an event journey is already gated
  by a real event, so 0) and a journey may state its own via
  `metadata.reentry_cooldown_days`. The cooldown counts only runs that actually
  TOUCHED the player and did not end in `exited_return` — a journey must not be
  punished for working, and a run that exited on its first step (a transient RG
  cool-off, a muted bot) must not exclude the player for a month.
  `_REENTRY_FLOOR_DAYS` (`db.last_enrollment_started_at`) is the hard floor
  under those exclusions, so "excluded from the cooldown" never means
  "re-enrollable on the next sweep".
  `guard_check` runs BEFORE the channel is resolved: the router refuses on the
  same telegram consents the guard classifies as TERMINAL, so resolving first
  reported them as `channel_unavailable` — which `drain_due_steps` counts as
  executed and advances past, marching a muted player's whole journey to
  'completed' without one touch. A step's channel is then resolved through
  `channels.route_channel` (never `executable_channels` alone — that is a
  product-level answer and would send to a player who refused the channel), and
  the guard's `constraints` + `comfort` travel into the brief; on a `fraud_hold` grant the brief is explicitly
  negated, since the template text is what promises the gift.
- **Scenario/template library (`scenario_library.py`)** — templates are
  BRIEFS by default (`persona_brief`: ops controls intent, the persona
  writes; `verbatim` = exact copy behind an explicit flag); journey steps
  resolve `template_key` → intent via `step_intent`. Starter packs (recovery
  × 5 cohorts, loss, FTD spine, weekly, abandonment) seed via
  `POST /admin/retention/scenarios/seed` as draft + dry-run + `is_starter`
  (idempotent — an operator-edited row is never overwritten). Activating a
  recovery journey returns the **ladder-overlap warning** (A7): the idle
  rungs on the same quiet days should be disabled by the operator.
- **Channels (`channels.py` + `partner_out.py`)** — the router is
  deterministic code with **STRICT opt-in** (never a non-consented channel,
  not even as fallback; nothing consented = `undeliverable`). It is the ONE
  entry point — `journeys._resolve_step_channel` goes through it, and a RETRY
  re-checks `opted_in` before every attempt, because a retry is a send and the
  first one was authorized hours ago. `multichannel_enabled` OFF narrows the
  executable set to telegram, so an explicit non-telegram step reports
  `channel_unavailable` rather than being silently rewritten. Adapters:
  telegram (the existing `delivery.py` seam), email (Customer.io App API
  transactional send, `POST https://api[-eu].customer.io/v1/send/email`,
  App API key = encrypted product secret `email_api_key`, region/from in the
  channel config row), push/in_app (DELEGATED: we POST a delivery order to
  the product's `delivery_endpoint_url`, the casino delivers on-device and
  reports back via `POST /partner/{id}/delivery-status` — statuses never move
  backwards), vip_host (a task in the queue, never a bot message). Lifecycle
  + backoff retries ([1m, 5m, 30m], permanent failures never retry) in
  `retention_deliveries` — but ONLY for `_RETRY_CHANNELS`: telegram rows belong
  to `send_worker`'s leased claim, and a delivery with two owners is closed by
  whichever got there first, on whichever transport it happened to speak. **Outbound partner calls** (offer-grant + deliver)
  are orchestrator→casino ("partner" = the operator running a casino on the
  platform): per-product URLs on the product row, Bearer =
  `partner_out_key` (encrypted), SSRF-guarded + DNS-pinned exactly like the
  Player-API pull.
- **Idle ladder trigger kinds** — a rule now carries `trigger_kinds` (list;
  legacy `trigger_kind` kept in sync): a rung fires when ANY of its
  dimensions (chat / casino / deposit inactivity) crosses the threshold, the
  most-idle one wins and names the reason; the anti-cascade memory counts a
  multi-kind rung toward every kind it watches. DEFAULT = all three (owner
  decision; boot backfill flipped existing rules, `create_retention_rule`
  defaults new ones, the starter ladder ships all-three).
- **Integration checklist** — the deploy-level list of what external teams
  still owe us (`integration_checklist`, seeded insert-only on boot:
  bonus-CMS contract, RG feed, player-update extensions, push delivery
  endpoint, Customer.io, VIP-host process, BI export), edited on the System →
  Integration checklist page (`GET/PUT/POST /admin/integration-checklist*`,
  writes global-admin).
- **Partner contract additions (all additive):** player-update accepts
  `rg_status`/`rg_status_until`/`marketing_consent`/`rg_flags`/`timezone` +
  per-channel consents (`email_opt_in`, `email_verified`, `push_opt_in`,
  `push_available`, `in_app_available`, `sms_opt_in`, `channel_prefs`);
  new inbound `POST /partner/{id}/delivery-status`; new OUTBOUND contracts
  the partner implements: the offer-grant endpoint and the delivery-order
  endpoint (idempotent by our `offer_grant_id`/`delivery_id`).
- These sweeps are no longer the event drain's tail: they ride
  `retention_v2.maintenance_loop`, in this order (each paced per product
  through `retention_worker_jobs`, each swallowing its own errors) — idle
  pings → attribution → scoring → activity profiles → journeys (scheduled
  matching + step drain) → delivery retries (skipped once the send worker is
  on, which claims failed rows itself). See "Event pipeline".
- Guard order (fixed): **RG → hard denies (sub/opt-out/blocked) → holdout →
  frequency (adaptive or legacy static) → budget → same-event cooldown →
  comfort.** Tests: `tests/test_retention_measurement.py`, `test_rg_guard.py`,
  `test_retention_frequency.py`, `test_retention_scoring.py`,
  `test_retention_offers.py`, `test_retention_journeys.py`,
  `test_retention_scenarios.py`, `test_retention_channels.py`,
  `test_idle_trigger_kinds.py`.

### SPEND ATTRIBUTION — whose money is it (`ai_interaction_logs.consumer/.source`)
Every OpenAI call is logged (invariant §4), but only a DIALOGUE turn carries a
`session_id` — the quality judge, the proactive agent's decision call and the media
cataloguer all log with `session_id NULL`. The dashboards used to infer the spender from
that NULL ("session-less ⇒ photo metadata"), which charged the judge and the agent to the
media bucket and put reviews of SUPPORT conversations on the Telegram dashboard. So each
row now carries its own labels, denormalized for the same reason `product_id` is:
**`consumer`** ('web' | 'telegram') = which FACADE the money belongs to — for a quality
review it is the facade of the REVIEWED conversation, so a support review is support spend
and a Telegram review retention spend — and **`source`** ('chat' | 'agent' | 'review' |
'media') = what the call was. Writers: `db.persist_turn` (dialogue, `consumer` from the
caller), `db.persist_ping_turn` (always telegram/agent), and `db.log_ai_interaction`, whose
`consumer=`/`source=` arguments every new call site must pass. Readers go through
`db._LOG_SOURCE` / `_LOG_IS_SUPPORT` / `_LOG_IS_RETENTION` (they need
`LEFT JOIN chat_sessions s ON s.id = l.session_id`) — never re-derive the spender from
`session_id IS NULL`. Rows written before the columns existed are classified **at read
time** (session-bound ⇒ 'chat', session-less ⇒ 'legacy', counted where the old dashboards
already counted them) so no backfill scan runs on boot and history keeps its totals. The
support dashboard reports DIALOGUE spend as `cost_usd_total` (the number every per-session
metric divides) with the judge's passes broken out as `cost_review_usd`; the retention
dashboard splits its total into dialogue / agent / media / review (+ the legacy remainder,
whose chart series hides itself once it is all zero). The PLATFORM-WIDE view is
`db.ai_cost_timeseries` → `GET /admin/ai-costs`: daily spend of EVERY call in scope —
facade-blind, both bots plus all background passes, so the buckets sum to the whole OpenAI
bill — split by `source`; rendered as the "AI cost by call type" histogram under the
AI-model group on System → Settings (`admin/src/components/AiCostsPanel.jsx`), whose scope
filter (whole platform / partner / product, seeded from the header product) and call-type
filter are independent of the header switcher. Tests: `tests/test_cost_attribution.py`.

### QUALITY REVIEW — the LLM-as-judge over finished conversations (`app/ai/reviewer.py`)
A cheap background pass that reads FINISHED conversations of **both** facades
(support widget + Telegram) and stores one verdict each: a 1..5 score, tags from
the CLOSED `prompts.REVIEW_TAGS` taxonomy (closed on purpose — free-form tags
cannot be counted, and "which failure mode is most common this week" is the whole
point), a one-line summary, quoted issues and the player questions the KB could
not answer. The wording lives in `prompts.build_conversation_review_messages`
(single source of truth, with a per-facade framing: routing support OUT is correct
for retention Nika and wrong for the widget). **It changes nothing** — the
verdicts feed the admin **Common → Quality** page (`/admin/quality/*`,
`app/api/quality.py`), where a human decides what to fix; an automated judge that
edited the KB or the settings would be a second, unreviewable author. Bounded by
construction: "finished" = resolved/escalated or dormant `QUALITY_REVIEW_IDLE_MINUTES`,
long enough to judge (`general.quality_review_min_messages`), at most
`general.quality_review_daily_max` per product per UTC day, and a chat is
re-reviewed only after it GREW since its last verdict (`reviewed_msg_count`, unique
per session). Runs on the product's own keys/model group; every call lands in
`ai_interaction_logs` with `session_id=NULL` (invariant §4) so the reviewed
session's own per-turn costs stay clean — labelled `source='review'` with the
`consumer` of the conversation it judged, so the spend surfaces in THAT facade's
analytics (see "Spend attribution"). It runs on the BACKGROUND process
(`app/worker.py` under `RETENTION_SCHEDULER_ENABLED`, or `main.py`'s lifespan in
the single-process `all` role), advisory-locked — it takes no stop flag, so a
drain cancels it rather than waiting: losing a half-finished review costs one
cheap pass and the conversation is picked up next sweep. Tests:
`tests/test_quality_review.py`.

## Invariants (these break silently — do not violate)

1. The Layer-1 block (`get_system_core()` = `SYSTEM_CORE` + the static directives,
   rendered with the prompt variables from the in-process settings cache) is
   byte-stable between requests WITHIN a product scope (it changes only on an admin
   prompt-variables save; different products legitimately render different brands);
   per-request data lives only in the user message (Layer 3).
2. KB is injected per topic (within the session's product) from Postgres — never
   baked into the core.
3. Persisting a turn is one atomic transaction (messages + counters + AI log).
4. Every message → `chat_messages`; every OpenAI call → `ai_interaction_logs`; every state
   transition (escalation, failover, rate-limit, injection) → `admin_events`. Per-turn/
   per-session rows carry the session's `product_id` (per-product dashboards depend on it),
   and every AI log row carries its **spend attribution** — `consumer` ('web'|'telegram',
   the facade the money belongs to) + `source` ('chat'|'agent'|'review'|'media', what the
   call was). A background call has no session to join, so an unlabelled row cannot be told
   apart from any other at read time (see "Spend attribution" below).
5. Two-key failover races the fallback after the switch timeout; log every failover.
   The keys are the PRODUCT's own (encrypted at rest) when set, else the deploy env keys.
6. No ORM, no migrations: schema is `init_db()`; new columns via guarded `ALTER`; all DB
   access through `db.*` helpers. A BACKFILL of existing rows never runs in `init_db` (one
   transaction, 30s command timeout — it would roll the boot back forever): batch it
   post-boot and make the reader correct before it finishes.
7. Model-facing prompt is English (token-efficient); KB may be in any language; answers
   in the resolved language. User-facing copy + user-input detectors stay multilingual.
8. Never request card numbers / CVV / passwords / 2FA codes / seed phrases; never invent
   player-facing facts — KB uses `{{PLACEHOLDER}}` tokens the owner replaces.
9. `_PRICING` is "verify before trusting"; cost is derived, not ground truth.
10. **Every claimed queue row is closed by its claimer** — an event lease with
    `complete_retention_event` / `fail_retention_event` / `release_retention_events`, a
    delivery lease likewise. A claim is a lease, not a completion; a row nobody closes is
    invisible until its lease expires, and a code path that can return without closing one
    is a dropped reaction. Bracket the batch in `try/finally`.
11. **A decision row is RESERVED before anything the player can see.** Event delivery is
    at-least-once, so the `retention_v2_decisions` insert (unique per `(product_id,
    event_pk)`) comes BEFORE the offer grant and the send, and a `None` return means replay
    — return, do not send. New player-visible side effects go after the reservation.
12. **Queue lag is the SLA metric** (`db.retention_queue_lag` = age of the oldest OVERDUE
    event — past its send delay and any retry backoff — not queue depth, and not work that
    is merely queued): it is what the backpressure ladder keys on and what says the
    pipeline is healthy. Work that must not fall behind belongs in a low-numbered priority
    lane; work that would inflate the lag without deserving a reaction should not enter the
    queue at all (`player_sync.should_queue`). A shedding rung reads only the lanes it does
    NOT shed (`retention_queue_lag_by_lane`) — otherwise the backlog it creates justifies
    itself and the degradation never lifts.
13. **A queued delivery has exactly ONE owner, and it is decided by CHANNEL.** A claim is
    destructive — whoever takes a row closes it — so `send_worker` claims only the transports
    it can actually put on the wire (`_SEND_WORKER_CHANNELS`, the Telegram seam) and
    `channels.drain_delivery_retries` owns the rest (`_RETRY_CHANNELS`). The two run
    concurrently and must stay disjoint. Deliveries carry the same dead-letter ceiling events
    do (`send_max_attempts`), and the generation's cost is billed on the FIRST attempt only:
    the model call happened once, however many times the queue retries the send.
14. **Consent is re-checked at every send, including retries, and the router is the only way
    to pick a channel.** `channels.route_channel` is the one entry point (strict opt-in: never
    a non-consented channel, not even as a fallback, not even for a critical touch);
    `executable_channels` answers a PRODUCT question and knows nothing about the player, so it
    is never a substitute. A guard verdict is not just `allow`: its `constraints` (the
    conditional RG "no play/bonus talk", the loss-comfort register) must reach the writer, and
    an offer that came back `fraud_hold` must be explicitly negated in the brief — the brief
    that asked for the bonus is not self-correcting.

## Admin / management (lazily loaded)
Admin auth + the roles/memberships model, user management, the settings groups,
the dashboard data API, KB + KB-variable editing, the effective-prompt preview,
translations, the test player profile, Logs + audit, the MCP facade
(`mcp_server/`), and the React Admin SPA in `admin/`. **The full map is the
`admin-surface` skill (`.claude/skills/admin-surface/SKILL.md`) — load it before
adding or changing an `/admin/*` endpoint, touching admin authorization or
scoping, or editing the admin SPA.** Authorization always goes through the
`app/api/admin_auth.py` choke points (`require_admin` + the scope helpers).

§16 decisions: unresolved analysis = topic-grouped (no embeddings); contact form =
host-site button only; admin auth = named `admin_users` accounts only (email + password,
role-driven; no password-only owner login).

### Multi-tenancy rules of thumb (see the "MULTI-TENANCY" section at the top)
The tenanting is BUILT — partners → products, membership authorization, per-product
settings/secrets/KB/copy, the header switcher. When extending, keep these rules:
- **Everything brand/product-specific lives in the product-scoped stores**:
  `prompt_variables`, `retention_prompt_variables` (the Telegram persona;
  a SEPARATE prompt with its own defaults, no support inheritance), `translations` (incl. the per-language
  `contact_url`), `site_map` (the official pages the model may link to, shared by both bots),
  the KB (topics + texts + `kb_variables`) — all keyed by
  product. Don't scatter new brand-specific values outside these.
- **Technical/operational knobs stay in the settings groups** (`general`, `antispam`,
  `model`, `language`) — resolvable at both the global and the product layer. When
  adding a knob, put it in the group it belongs to (or `general`), never hard-code
  it, so both layers keep working.
- **Authorization decisions go through the `app/api/admin_auth.py` choke points**
  (`require_admin` + the scope helpers). A new admin route must authorize against
  the product/partner it touches — never trust a bare "is admin somewhere" check
  (`require_admin_write` alone is only the coarse pre-filter).
- **New per-turn/per-session data must carry `product_id`** (copy it from the
  session, like `ai_interaction_logs` does) so per-product dashboards stay whole.
- The **prompt template** (`prompts.py`) stays the one shared, deploy-level
  artifact — brands differ only via prompt variables + KB + translations, which is
  what makes white-label/multi-product reuse possible without per-tenant prompt forks.

## Conventions

- Stdlib-only JWT (`auth.py`) — HS256 via `hmac`/`hashlib`/`base64`, no PyJWT.
- **Security response headers** (`main.py` `security_headers` middleware): `X-Content-Type-Options:
  nosniff` on everything, plus `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` +
  `Referrer-Policy: no-referrer` on **`/admin` paths only**. The admin SPA's ordinary buttons do
  destructive things (delete a product, rotate a widget key, mint a `sak_` key), so a third-party
  page framing it could clickjack a logged-in operator. The frame deny must NOT go global — the
  widget, `test.html` and the `integration-*` pages are meant to be embedded/opened by partner
  sites, which is the whole point of the service. Set with `setdefault`, so a handler that chose
  its own value keeps it.
- The widget front-end is vanilla ES modules with **no build step**; widget classes
  are prefixed `npchat-` to avoid host-page collisions. The admin SPA is the React
  Admin app in `admin/` (its own Vite build — the exception to "no build step",
  since it deploys as its own static site).
- **Assistant replies render a small, safe Markdown subset** (`widget.js`
  `renderMarkdown`): the model formats answers with light Markdown on its own
  (`**bold**`, numbered/bulleted lists, `code`, links), so rendering them as plain text
  leaked the literal markers to the screen. The renderer HTML-escapes the model text
  **first**, then re-introduces only a whitelist — `**bold**`/`__bold__`, `*italic*`/
  `_italic_`, `` `code` ``, `[label](url)` (http(s)/mailto only, `rel="noopener"`), ATX
  headings, and `<ul>`/`<ol>` lists — so no raw HTML from the model ever survives. Code
  spans and links are stashed behind private-use sentinels before the bold/italic passes
  so a URL's underscores can't be re-chewed. Only assistant turns go through it
  (`setMsgBody`); **user input is always rendered literally** via `textContent`.
- Deploy is Railway via the single `Dockerfile` (`python:3.11-slim`) + `railway.toml`; the
  CMD reads `$PORT`, no `startCommand` override. Health check is `/healthz`. `railway.toml`
  configures the WEB service only — the worker is a SECOND Railway service off the same
  image and repo, created in the dashboard with `python -m app.worker`, `SERVICE_ROLE=worker`
  and `RETENTION_SCHEDULER_ENABLED=1` (the header comment in `railway.toml` is the checklist;
  the media Volume stays mounted on web). Leaving `SERVICE_ROLE` unset is the pre-split
  single-process mode, so an existing deployment upgrades without a worker service.
- Env var reference lives in `README.md` (§ "Environment variables").
- **Two docs, two audiences:** `README.md` is the human-facing overview; **`CLAUDE.md`
  (this file) is the LLM/agent guidance** — architecture, invariants, conventions. They are
  no longer mirrored or auto-synced (the old `docs-sync` hook/Action and `scripts/sync_readme.sh`
  were removed). Edit each for its audience: update `CLAUDE.md` when you change architecture or
  invariants, and `README.md` when the human-facing overview or env table changes. The root
  test page (`main.py` `/`) serves a static `frontend/test.html` (a short feature summary), not
  this file.
