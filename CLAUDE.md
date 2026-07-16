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
  tests unaffected).
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
- **Authorization** (`api/admin_auth.py`): accounts (`admin_users`) get roles via
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
SUPPORT_CHAT_TEST_MODE=1 python -m pytest tests/test_antispam.py::test_rate_limit -q

# Run locally (needs a real Postgres + OpenAI key)
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/supportchat
export OPENAI_API_KEY=sk-... SESSION_JWT_SECRET=$(openssl rand -hex 32)
uvicorn main:app --reload --port 8080   # test page at http://localhost:8080/
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
- **`ruff`** config in `pyproject.toml` is conservative on purpose (real-bug rules
  F/E9 only; line length and semicolons off) — don't broaden it into a restyle.
- **`scripts/docs_check.py`** (skill `/docs-check`) is the manual replacement for
  the removed docs-sync: it diffs the working tree vs `origin/main` and flags the
  docs a change of that shape usually needs — architecture `.py`/`api/` →
  `CLAUDE.md`; `config.py` → the README env table; a public API file → its
  `frontend/integration-*.html` page; any integration/widget change →
  `frontend/test.html`. Advisory (exit 0), since whether an edit warrants a doc
  change is a judgment call.
- **Skills** in `.claude/skills/` scaffold the recurring cross-file changes so no
  touch-point is missed: `/preflight`, `/docs-check`, `/add-setting`,
  `/add-translation`, `/add-db-column`, `/add-admin-endpoint`. Reach for them when
  doing that kind of change.

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
  catalogue, the conversation history, the new user turn, and the recency guardrails /
  forbidden-topics block (kept **last**, after the player's message, on purpose — an
  anti-injection / anti-off-topic reminder bites hardest closest to the input).

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

### Request flow
`api/chat.py` (thin HTTP handlers + gate ordering) → `chat_service.handle_message`
(orchestration) → `prompts.build_messages` + `openai_client.complete` → `db.persist_turn`.
`chat_service` keeps handlers thin: it resolves language, builds the prompt, calls the
model with failover, strips the `[[ESCALATE]]` sentinel, decides escalation, computes cost,
and persists the turn — all in one place.

### Data layer — no ORM, no migrations (`db.py`)
The schema *is* the code in `db.init_db()` (run on startup via `main.py` lifespan). To change
schema, edit the `_SCHEMA` string. **A new column on an existing table will NOT be applied by
`CREATE TABLE IF NOT EXISTS`** — add an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
to `_ensure_columns()`. Every table read/write goes through a `db.<name>(...)` async helper;
nothing else touches tables directly.

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
widget redeploy. The admin panel itself stays English.

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

The default model is the **GPT-5 mini reasoning family** (`gpt-5-mini`). Reasoning models
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

The tuning knobs (model name, reasoning effort, verbosity, max output tokens, request timeout,
key-switch timeout, max attempts, per-key concurrency) are NOT read from env directly — they
come from the hot-reloaded `model` settings group (`settings.model()`, precedence
`app_settings` → env → default). Model/reasoning-effort/verbosity/max-tokens/switch-timeout/
attempts are read **live per call**; `request_timeout_sec` and `max_concurrent_per_key` are
bound when the client is built, so a `model` write also calls `openai_client.reset()` to
rebuild the singleton (no effect on the OpenAI-side prefix cache). API keys themselves stay
secrets in env.

### Anti-spam gate order (`antispam.py`, enforced in `api/chat.py`)
`POST /api/chat/message` checks in this exact order: verify session token (401) →
**open-session check (409 `session_closed` if resolved/escalated)** → IP rate-limit (429 + log)
→ cooldown (429) → input length (400) → **low-content guard** → injection scan (always audits;
**hard-blocks with 400 by default**, settings-gated via `injection_hard_block`) → message-cap
fast path (forces an escalation response with no model call) → **pre-model keyword-escalation
gate in `chat_service`** (soft hand-off, no model call — see "Escalation") → build/call/persist.
The cooldown gate only CHECKS; the stamp is armed (`antispam.arm_cooldown`) after every
reject-gate passes, so a rejected message (too long / low-content / injection-blocked) never
throttles the player's immediate corrected resend.
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

The **IP key** comes from `api/client_ip.py` `client_ip()`, which trusts `X-Forwarded-For` **only**
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

The browser locale is still the **starting** answer language. Deterministic priority for the
session's base/UI code: `locale` (e.g. `es-MX`→`es`; this is where the browser's
`navigator.language` lands) → persisted `session_lang` (the locale resolved at session
create) → `AUTO` (→ `DEFAULT_LANGUAGE`). `create_session` resolves it and stores it on
`chat_sessions.lang` (the browser/UI language — never overwritten by the drift below).

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
- **HARD (`final=true`)** — the model's `[[ESCALATE]]` sentinel, the message cap, or the explicit
  `/escalate` tap. `db.mark_escalated` sets `status='escalated'` (+ `escalated=TRUE`), the widget
  ends the conversation, and further turns 409. `decide()` covers exactly these post-model
  triggers (cap first, then `model_signalled`); the old `already_escalated` auto-trigger branch is
  **gone** — a soft-escalated session keeps chatting normally.
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
from two places. The constants in `escalation.py` are only the built-in defaults. The cap
fires on the turn whose prospective count (current + 1) reaches `max_messages_per_session` — a
technical limit that lives in the **`general`** settings group (the legacy
`escalation.max_messages_per_session` DB override is still honoured as a fallback); the
model-free fast path in `api/chat.py` is the cheap belt-and-suspenders for a session already
at/over the cap — complementary, not a duplicate. The button URL is **per-language**: the
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
`resolved`), `api/chat.py` `_ensure_open_session` rejects further mutating turns (`/message`,
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
   in `api/chat.py` runs BEFORE the pre-model SOFT escalation, so it deliberately does NOT
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

### RETENTION BOT — Telegram second facade (`retention.py`, `telegram_transport.py`)
A **second front-end over the same AI core**: from the site a player deep-links into a
Telegram bot where **Nika runs retention only** (warm, flirtatious engagement + photos under
the player's profile). She does **not** handle support — any support PROBLEM (complaint,
account block, stuck/failed deposit-withdrawal, responsible gaming, ask-for-a-human) is routed
**out** via the hand-off CHOICE message (personal manager in Telegram and/or the site's support
chat — see the `[[HANDOFF]]` bullet below). A simple **navigation** question ("how do I
deposit?", "where do I play X?") is NOT a hand-off: she answers it herself and attaches the
matching SITE MAP page as a `[[LINK:url]]` button — the problem-vs-directions split is stated
in the retention core, the SITE LINK BUTTON directive AND the Layer-3 `_RETENTION_GUARDRAILS`
(the guardrail rides last, so a blanket "money → HANDOFF" there used to override the
navigation exception — the "как задепать → саппорт" bug). This section IS the spec (the
old `RETENTION_BOT_SPEC.md`/`RETENTION_SETUP.md` files were removed); the operator's setup
checklist lives in the admin — the **Retention → How it works** page.

- **Transport vs. brain vs. AI turn are separated on purpose** so the transport can be lifted
  into its own service later: `telegram_transport.py` (HTTP to the Bot API + update parsing,
  holds no logic), `retention.py` (the orchestration: nonce exchange, subscription gate, entry
  menu, photo selection/gating, manager round-robin, progression), `chat_service.handle_retention_message`
  (the AI turn: build prompt → model → strip sentinels → persist).
- **Channel = the existing `consumer` column** (`'web'` → `'telegram'`), NOT a new `channel`; the
  mode is derived from it (telegram ⇒ retention). Support is never duplicated in Telegram.
  **Telegram chats are logged APART from support chats**: the support admin surfaces
  (`db.list_sessions`, `db.unresolved_by_topic` — the Conversations + Unresolved views) exclude
  `consumer='telegram'` entirely; the Telegram chats live in their own **Retention →
  Conversations** page (`GET /admin/retention/sessions` → `db.list_retention_sessions`, joined
  with the `retention_users` identity + summed cost; the transcript opens via the shared
  `GET /admin/session/{id}`, same scope check). **Deleting a Telegram conversation
  (`DELETE /admin/session/{id}` → `db.delete_session`) also PURGES the linked player**: after
  the transcript rows (chat_messages / session-linked ai_interaction_logs / admin_events /
  chat_sessions) go, `_purge_retention_player` deletes that player's `retention_photo_views`,
  `retention_pings` and the `retention_users` row (keyed by the session's product + `tg_user_id`,
  so it fires even for an old rolled-over session). Without this the player kept showing up in
  the retention dashboards after their chat was deleted — the analytics draw from
  `retention_users`/`retention_photo_views`/`retention_pings`, not the transcript. Product-level
  historical counters logged session-less (funnel `retention_deeplink_created`/`retention_start`,
  the photo-metadata generation cost) are NOT attributable to one player and stay. Support
  session deletes need no such extra step — every support metric is keyed to `session_id`, so the
  transcript delete already zeroes them out.
- **Telegram chat lifecycle — idle rollover + returning-player continuity.** A Telegram
  conversation has no "close the widget" moment, so a chat "ends" by INACTIVITY: on the next
  incoming message `retention._ensure_session` reuses the linked session only while it is
  `open` and not idle past the `retention.session_idle_minutes` knob (default 360; 0 = never —
  the old endless-session behaviour). An idle (or already-closed) chat with messages is closed
  lazily — `db.close_retention_session` sets `status='resolved'` + logs
  `admin_events('retention_session_closed')` — and a FRESH session is created pointing back via
  `chat_sessions.prev_session_id` (guarded ALTER; an empty open session is simply reused, no
  churn). **Continuity:** on the first turn of the fresh session,
  `chat_service.handle_retention_message` pulls the tail of the previous chat
  (`carry_context_turns` knob, default 10, 0 = off) and passes it to
  `prompts.build_retention_messages(previous_history=…)`, which renders a Layer-3
  `RETURNING PLAYER — PREVIOUS CONVERSATION (context only)` block (messages truncated to ~240
  chars, rough "N hours/days ago" recency): greet back warmly like someone she knows, never
  re-introduce, don't re-answer the old messages — and when the old chat left a concrete thread
  (his plans, his mood, a game), ask warmly how it went (the short-term memory the player
  actually FEELS). It rides ONLY on the first turn (never as
  message history — it is a new chat); durable state (stage progression, seen photos, manager,
  language, profile) lives on `retention_users` and survives rollover by construction. Tests:
  `tests/test_retention_lifecycle.py`.
- **Retention prompt mode (`prompts.py`)** is a SECOND Layer-1 assembly — `SYSTEM_CORE_RETENTION`
  + retention static directives (`get_retention_system_core()`), byte-stable per **product × mode**
  (a test asserts it, mirroring the support core). It shares the persona but swaps support
  behaviour for engagement + photos + route-out. **No** KB-grounding / escalation-restraint /
  topic-routing / suggestions here — and its OWN **light** Telegram formatting: retention
  replies are sent with `parse_mode=HTML`, so the retention Layer 1 carries its OWN
  `_RETENTION_FORMATTING_DIRECTIVE` (a TOUCH of `**bold**`/`*italic*` allowed, no
  lists/headings/tables/link-markup, bare URLs, and — a hard rule — NO em/en dashes or
  guillemet/angle quotes) instead of the support `_FORMATTING_DIRECTIVE`. The persona's
  emphasis is rendered by **`telegram_format.to_html`** (Markdown-subset → balanced,
  HTML-escaped Telegram HTML; bare URLs + code spans stashed so their punctuation survives),
  applied at every retention AI-text send site (`retention._send_ai_text`, photo captions,
  the ping worker) with a plain-text fallback so a bad-HTML send never silently drops. The
  "AI-tell" typography the model keeps emitting despite the rule is ALSO scrubbed
  deterministically after the model turn (`telegram_format.normalize_punctuation` in
  `chat_service` — em/en dashes → `-`, guillemet/curly quotes → straight ASCII), so the
  persisted transcript and the sent message match. **Liveliness rules (static, Layer 1)** —
  tuned after a live transcript read like a bot: emoji in ordinary TEXT messages are **banned
  outright** (a repeated 😉 was the loudest bot-tell; support Nika uses none either) — the ONLY
  two allowed emoji are chrome-level exceptions with a strict priority: a PHOTO caption may end
  with a SINGLE emoji picked from THAT photo's own content/mood, and a plain-text message
  carrying a site-link button ends with the single 👇 hand — never both (a photo with a button
  keeps only the caption's mood emoji; the 👇 is never added on a photo); replies default to
  1-2 short sentences with varied length and rhythm (longer only when asked for a
  story/details), and a reply MAY arrive as a burst of consecutive Telegram messages (blank-line
  split in the model text, delivered as separate sends with a typing pause —
  `retention._split_reply_parts`: usually one message, sometimes two, rarely three; an inline
  button rides on the LAST part); the "do you want X or Y?" two-option
  closer is explicitly banned as a template, and question-ending is rationed (at most one
  message in two-three ends with a question); the ENGAGEMENT directive **bans self-initiated
  play invitations outright** — the model may talk games/bonuses ONLY when the player raises
  the subject or the Layer-3 PLAY NUDGE block orders one invitation (so the nudge cadence knob
  is the ONE pacing control; the old "invite every so often when it flows naturally" permission
  made the model pitch slots from the first reply and was removed), orders comfort-mode with
  NO play talk after the player says he lost money (even a due nudge is skipped), and demands
  concrete call-backs to what the player said earlier instead of generic lines plus
  freshly-invented (never recycled) small "life details"; photo
  captions must be UNIQUE and grounded in the current moment + the chosen photo's description
  (stock lines like "just for you" repeated per photo are named as the failure mode). The retention core
  renders with the **retention prompt-variable set**
  (`prompts.render_retention_prompt_variables` — retention override > retention default, a
  SEPARATE prompt with NO support inheritance, incl. its OWN tone `{retention_tone_of_voice}`)
  — see "Prompt variables"; the
  bot's model-free chrome (`retention._persona_name`) resolves the same way, so the menu
  greeting matches the persona the prompt runs. Layer 2 = the **whole** retention-KB (`db.retention_kb_block`,
  NOT `kb_topics`). **The retention KB is edited as ONE free-text document per product** (like a
  support topic's KB text): stored as a single `retention_kb` row with the sentinel title
  `db.RETENTION_KB_DOC_TITLE` (its body enters the prompt verbatim, no header);
  `db.get_retention_kb_text`/`set_retention_kb_text` are the document read/write (the write
  replaces the product's whole KB in one transaction), exposed via
  `GET/PUT /admin/retention/kb/text`. Legacy structured rows (the old per-entry editor) still
  render in the prompt and are folded into the document text on the first save; the per-entry
  CRUD endpoints remain for API consumers. New products are seeded with
  `starter_kb.STARTER_RETENTION_KB`. Layer 3 (`build_retention_dynamic_prompt`) = full profile
  personalization + language directive + the **appearance block** (`prompts._appearance_directive`,
  fed by `db.retention_appearance_context`: a stable sample of the product's photo-library
  descriptions + the photo THIS player saw last — the persona's looks are grounded in the REAL
  photos even on turns where no photo is sendable, so the model can never invent contradicting
  hair/outfit; fetched best-effort in `retention._run_nika_turn`) + the **photo-candidate
  list** (whose empty-state text steers away from the "I have no photos" flat refusal and
  toward an appearance-grounded tease + a once-per-chat progression hint) + a lighter
  retention guardrail.
  **Retention personalization is its OWN directive** (`prompts._retention_personalization_directive`,
  NOT the support one): in Telegram the bot chrome has ALREADY greeted the player by name
  TWICE before the first model turn (the `rtn_menu_greeting` menu message + the
  `rtn_nika_start` opener), so where the support widget ORDERS a first-reply by-name greeting,
  retention orders the OPPOSITE — an explicit first-turn suppression imperative ("the menu
  already greeted; do NOT greet or introduce yourself"), later turns get the name-sparing
  wording, and a RETURNING player's fresh session (rollover) is the one case where a greeting
  happens: the personalization defers to the continuity block's short welcome-back. The
  `rtn_nika_start` copy is greeting-free BY CONTRACT (a conversation opener, not a hello) —
  it used to open with "Привет!", stacking a triple greeting on the player's screen.
- **Retention sentinels** (stripped like the support ones): `[[PHOTO:id]]` (send a photo from the
  candidate list the model was shown — backend re-validates the id), `[[STAGE_UP]]` (a hint the
  player is ready for the next explicitness stage — the backend gate decides), `[[HANDOFF]]`
  (route out; writes `admin_events('retention_handoff')`), `[[LINK:url]]` (a site-map CTA button —
  next bullet), `[[LANG:xx]]` (as everywhere). Strip helpers: `prompts.strip_photo_tag` /
  `strip_stage_up_tag` / `strip_handoff_tag` / `strip_link_tag`.
- **Site-map CTA button (`[[LINK:url]]`) + the periodic play reminder.** When retention Nika
  invites the player somewhere concrete on the site (come play, deposit, check the balance), she
  emits `[[LINK:url]]` with a URL copied EXACTLY from the Layer-1 SITE MAP block (static directive
  `prompts._RETENTION_LINK_DIRECTIVE`; at most one per reply, never pasted into the visible text).
  The backend re-validates it (`chat_service.resolve_site_link` — an EXACT match against
  `settings.site_map()`, so the model can never button-ify an invented address; the page `title`
  becomes the button label, falling back to the url) and the message ships with ONE inline
  url-button (`retention._run_nika_turn`; `_send_ai_text`/`_send_photo` and the transport photo
  senders all take `reply_markup`). On a plain TEXT message carrying a link button the directive
  makes Nika end the reply with a single 👇 hand pointing at the button — the ONE emoji allowed on
  an ordinary text reply, and never added on a photo (a photo caption already carries its own
  single mood emoji, so the hand would collide). A `[[HANDOFF]]` turn drops the link (the player is
  leaving for support). Play invitations are **nudge-only** (self-initiated invites are banned by the
  engagement directive — see the liveliness bullet): the **`retention.play_reminder_every_msgs`
  knob** (default 5, 0 = off; env `RETENTION_PLAY_REMINDER_EVERY_MSGS`) is the ONE pacing
  control. `chat_service.play_nudge_due` keys on the session's `message_count` (one bump per
  persisted turn; never the very first reply) and the cadence **DRIFTS ±2 around N**
  (cumulative schedule, jitter keyed on session_id + cycle via `_nudge_jitter` — stateless,
  reproducible, gaps always within N±2): a strictly periodic every-5th-message invitation
  was a pattern a player could clock. The due reply carries the Layer-3
  `prompts._PLAY_NUDGE_DIRECTIVE` — explicitly framed as "the ONE permission you get to
  invite": continue the conversation normally, weave in ONE light in-context invitation to
  play, attach the best-fitting site-map page as the button — **and ROTATE the
  destination**: the attached `[[LINK:url]]` is persisted on the message row
  (`chat_messages.link_url`) and rendered into the retention prompt history
  ("[with this message you attached a site page button: …]",
  `prompts._retention_history_content`), and the nudge directive orders a DIFFERENT
  fitting page than the previous invitation (main page / casino / slots / tournaments)
  — without the history note the model could not see which button it already sent and
  pinned the same page every time. Skip the invitation entirely in a
  complaint/money/just-lost/sensitive moment. Tests: `tests/test_retention_cta.py`,
  `tests/test_naturalness.py`.
- **Media library + file_id cache**: `retention_photos` gates by `level_min` (VIP-tier ordinal) ×
  `stage` (explicitness). **Both values are bounded to the product's real ranges on EVERY write**
  — `stage` to 1..`max_stage`, `level_min` to 0..(last tier ordinal) — whether the value is
  AI-generated OR hand-entered/API-posted (`api.retention._clamp_photo_gate`, applied in
  `create_photo` + `update_photo`; the SPA Media pickers offer only in-range choices), so a
  photo can never gate outside what the delivery gate can serve (no stage 0/6, no tier past the
  ladder). The first send uploads the binary from the media dir (Railway Volume,
  `RETENTION_MEDIA_DIR`); Telegram returns a `file_id` cached on the row so later sends skip the
  re-upload/egress. **Uploads are auto-normalized for Telegram** (`media_normalizer.py`): content
  managers upload originals as they come (multi-MB JPEGs at 8000×4000), but Telegram re-compresses
  every photo to ~2560px anyway, so a periodic sweep (hourly by default; own asyncio task from
  `main.py` lifespan under the same `RETENTION_SCHEDULER_ENABLED` switch, own advisory lock)
  re-encodes every .jpg/.png (and any oversized .webp) to WebP at
  `retention.media_max_side_px` (default 2048) × `media_webp_quality` (82), re-points the row
  (`db.set_retention_photo_storage_ref`) and **deletes the heavy original** — GIFs are left alone
  (possibly animated), the cached `telegram_file_id` is KEPT (the already-uploaded copy stays
  valid), and the row is re-pointed BEFORE the delete so a crash can orphan a file but never break
  a photo. Knobs in the hot `retention` group (`media_normalize_enabled` per product;
  `media_normalize_interval_sec` global-only — one loop serves every product); the Media tab's
  «Normalize now» button (`POST /admin/retention/photos/normalize`) runs one product's sweep
  immediately, bypassing the enabled switch. Requires `Pillow` (requirements.txt). Tests:
  `tests/test_media_normalizer.py`. **Upload is bulk-friendly** (`POST /admin/retention/photos` takes any number
  of `files` in one request; the single `file` field stays for older consumers) and metadata is
  **AI-generated on demand**: `POST /admin/retention/photos/generate-metadata` (`{ids: […]}`,
  ≤20/request — the SPA chunks bigger selections) runs one vision call per photo through the
  product's OWN OpenAI client (`client_for_product`) + the product-resolved `model` settings
  group, using the prompt in `prompts.build_photo_meta_messages` (wording in `prompts.py`, the
  single source of truth), and fills `description`/`tags`/`stage`/`level_min`; the reply is
  strict JSON, parsed + **clamped against the product's real `vip_tiers`/`max_stage`**
  (`api.retention._parse_photo_meta`, sharing the same bounds as the write-time
  `_clamp_photo_gate`) so a hallucinated number can never unlock a photo beyond
  the delivery gate, every call lands in `ai_interaction_logs` (invariant §4, `session_id=NULL`),
  and one failed photo never kills the batch. Descriptions are demanded in plain everyday words
  (hair = colour + length, simple clothing terms — no haircut names / fashion-catalogue jargon:
  the persona voices this text in chat). The batch runs in **waves of 5** with the library's
  current stage/level distribution injected into the prompt (`prompts._PHOTO_META_BALANCE`,
  counts refreshed between waves from the fresh ratings) so borderline calls land on the
  under-filled levels and the library spreads evenly across the whole ladder instead of
  clustering on one-two values. The SPA Media tab adds checkbox selection +
  "Generate metadata" and client-side filters (search/stage/level/status).
  **Candidate selection is pre-model** (`retention.select_photo_candidates`):
  unseen, tier×stage-gated (current stage + 1 teaser, capped by the tier ceiling), bounded by the
  **daily cap** (hard, reactive included) and the **proactive cooldown** (bypassed when the player
  explicitly asks — `is_photo_request`). Empty candidate set ⇒ the model is told to keep chatting
  with text and not promise a photo. The model's reply text becomes the photo **caption**, grounded
  on the candidate descriptions it was shown (one call — no separate caption round-trip).
  **Introduction photo (`retention.intro_photo_due`)**: a BRAND-NEW player — never received a
  photo (`db.has_photo_views`), within his first `intro_photo_within_msgs` meaningful messages
  (default 3) — gets one proactively: the selection bypasses the proactive cooldown for that turn
  (daily cap + tier×stage still gate) and, when candidates exist, Layer 3 carries the IMPERATIVE
  `_INTRO_PHOTO_DIRECTIVE` ("you MUST send one photo from the candidates this turn" — imperative
  on purpose, the greeting-hygiene lesson: a conditional permission loses to the static restraint
  rules) with a model-written "this is me — let's get to know each other" caption (localized,
  grounded in the chosen photo's description — never a canned string), so the player learns from
  the very start that chatting comes with photos. Knobs `intro_photo_enabled` (ships ON) /
  `intro_photo_within_msgs` in the hot `retention` group (Retention → Settings → Parameters; env
  defaults `RETENTION_INTRO_PHOTO_*`). The view row lands in the same transaction as the send, so
  the rule can never refire after a delivery.
- **Progression is backend-decided** (`retention.maybe_advance_stage`): the model only hints;
  the actual `unlocked_stage` advance needs the engagement threshold (`stage_advance_msgs`) **and**
  the tier ceiling (`max_stage_by_tier`) **and** spacing (`stage_advance_min_hours`). VIP tier is
  mapped from the free-text `vip_level` via the ordered `vip_tiers` list. All knobs are in the
  **`retention` settings group** (`settings.retention()`, in `SETTING_KEYS` — per-product tunable).
  **Progression is player-visible now, on two sides.** (1) A REAL advance is **celebrated**:
  after `maybe_advance_stage` unlocks a stage, `retention._send_stage_up_note` (gated by the
  `stage_up_notify` knob, default on) generates a persona follow-up via the ping stack
  (`chat_service.generate_retention_ping(stage_up=…)` → `prompts._RETENTION_STAGE_UP_TASK`: "we
  just got closer — more daring photos from now on", plus a keep-chatting hint unless the new
  stage is the player's current ceiling), sends it right after the turn's reply and persists it
  via `db.persist_ping_turn` with `ping_context="stage_up: …"` — so the prompt history renders it
  with its trigger (the player asking «что это было?» gets a real answer) and the admin
  transcript shows the ⚡ proactive marker; an `admin_events('retention_stage_up')` row is logged.
  Best-effort: any failure only skips the note (the advance is already committed). (2) Nika can
  **explain the system**: every dialogue turn's Layer 3 carries a `=== PROGRESSION ===` block
  (`retention.progression_context` → `prompts._progression_directive`: unlocked stage, tier
  ceiling, VIP level, meaningful-message count and the next threshold — the same maths the gate
  enforces, so what she says matches what the backend does), and the static
  `_RETENTION_STAGE_DIRECTIVE` now states the WAY progression works is not a secret (chat more →
  closer → more daring photos; VIP raises the ceiling) — only the machinery (tags, counters,
  "stage" as a system term) stays internal. Tests: `tests/test_stage_progression.py`.
- **Entry = deeplink + one-time nonce** (`retention_nonces`): the site posts a handshake to
  `POST /api/retention/deeplink` → `{nonce, deep_link}`; `/start <nonce>` redeems it (single-use,
  TTL-bounded, **product-scoped** — a nonce minted for brand B's bot never redeems on brand A's,
  so a cross-tenant profile leak is impossible), fixes the **`tg_user_id ↔ player_id` link** + a
  `_CONTEXT_FIELDS` profile snapshot
  in `retention_users`, and sets `entry_type` (`retention` | `escalation`). **The nonce also
  carries the conversation LANGUAGE**: `retention.create_deeplink(..., lang=)` stores a supported
  code in the nonce payload (the widget escalation passes the turn's answer language automatically;
  the site endpoint takes an optional `lang` body field, code or locale) and `/start` adopts it as
  the retention user's `conv_lang` — so a player who chatted in Russian lands in a Russian bot
  (greeting, menu, buttons AND Nika's replies), not the Telegram-client/default language. Without
  it the language falls back to the client `language_code` → default (`resolve_user_lang`); after
  every AI turn `_run_nika_turn` syncs the answer-language drift back onto the `retention_users`
  row so the model-free chrome follows the conversation. No valid nonce ⇒ the
  bot refuses (no organic entry). Then the **channel subscription gate** (`getChatMember`, the bot
  must be a channel admin) before any menu; a product with no channel configured skips the gate.
  After the gate, the entry menu opens with a **personalized persona greeting**
  (`retention._menu_text`: `rtn_menu_greeting`/`_noname` — the persona name from the product's
  `persona_name` prompt variable + the player's first name from the profile snapshot) above the
  `rtn_menu_prompt` line; all `rtn_*` copy supports a `{persona}` placeholder
  (`retention._rtn_text`), and the default button labels carry emoji icons (📢/✅/👤/💬) so the
  buttons read at a glance. The menu ships **structured**: `retention._menu_html` sends the
  greeting as a bold HTML line above the plain prompt (both HTML-escaped — the copy is
  admin-edited text and the name is player data), with an automatic plain-text resend if
  Telegram rejects the HTML.
  Two things mint that deeplink: (1) the **support-chat widget's escalation button** — when the
  product runs retention, every escalation hand-off routes the player INTO the bot on the
  **escalation entry** (`escalation=True` → the manager option in the menu), via
  `escalation.build_payload_for_session` (see the Escalation section). This is the PRIMARY path —
  the widget is the main channel. (2) the optional site buttons below (secondary integration).
- **Profile freshness degrades softly** — all three levels ship: snapshot + re-handshake;
  **lazy pull** (`retention.maybe_pull_profile`, gated by `profile_pull_ttl_sec`) — before a turn,
  if the snapshot is stale and the product has a `player_api_url` + encrypted key, GET the fresh
  profile and update the snapshot (best-effort: a failure leaves the snapshot untouched; the
  outbound connection is **DNS-pinned** — `player_sync.resolve_pinned_outbound` vets the
  resolution once and connects to that literal IP with the original Host/SNI, so a low-TTL
  rebinding domain can't pass the SSRF guard and then reconnect to an internal address); and
  **push webhook** `POST /partner/{product_id}/player-update` (authorized with the product's
  handshake secret as the shared partner secret). Partial updates only. A product with no Player
  API just lives on the snapshot — the schema degrades, never breaks. Both pull and push now
  also accept the **casino activity timestamps** `last_login_at` / `last_played_at` /
  `last_deposit_at` (ISO-8601, parsed + validated in `db.update_retention_profile`; unparsable
  values are dropped) — the agent's state resolver keys on them.
- **Proactive contact is the RETENTION AGENT** (see the "RETENTION AGENT" section
  below) — the one place the bot ever writes FIRST, with TWO triggers: casino
  EVENTS (`retention_v2.py`) and player INACTIVITY (`retention_idle.py` — the
  admin-managed idle rules ladder in `retention_rules`, the successor of the old
  v1 "ping matrix"; see the agent section). The shared send
  machinery: a proactive message goes out with the localized italic
  `rtn_ping_header` line ("✨ Hey, it's {persona}", translations registry)
  above the generated text — an EVENT reaction merges its localized occasion
  phrase into that same line ("✨ Привет, это Ника! Спасибо за депозит 10 USD",
  the `rtn_trig_*` registry keys) — the header is chrome, only the model text is
  persisted (`db.persist_ping_turn`, assistant-only atomic variant) — a validated
  `[[LINK:url]]` site-map page rides under it as ONE inline button (and is
  recorded on the message row, `chat_messages.link_url`), every attempt
  lands in the `retention_pings` ledger (+ per-player counters via
  `db.record_retention_ping`), the `/stop` opt-out (`pings_muted`; `/resume`
  re-enables) and the blocked-bot flag (`unreachable`, set on a Telegram 403,
  cleared when the player writes again) are honoured on every send.
- **Delivery + gate knobs** (both in the hot `retention` settings group, edited in
  Retention → Settings → Parameters): `silent_notifications` (proactive sends go
  out with Telegram `disable_notification` — no sound on the player's phone;
  dialogue replies always notify normally; plumbed through
  `telegram_transport.send_*`/`retention._send_ai_text`/`_send_photo` and read in
  the agent's send site) and `subscription_cache_ttl_sec` (how long a positive
  `getChatMember` check is cached; 0 = re-check live every message — the old
  hardcoded 600s constant remains only as the fallback default).
- **Temporal naturalness at the send site (`retention.py`)**: a dialogue turn
  runs under a native Telegram **typing indicator** (`retention._typing` — a
  task re-sending `sendChatAction` every ~4.5s while the model thinks, so a
  long reasoning turn shows «печатает…» instead of dead silence; purely
  cosmetic, failures never drop the reply), and a model reply carrying BLANK
  lines is delivered as a **burst of separate messages**
  (`retention._split_reply_parts` in `_send_ai_text`, capped by the hot
  `retention.max_reply_parts` knob — default 3, 1 = never split —, extra
  chunks collapse into the last part; typing + a length-proportional pause
  between parts; an inline button always rides on the LAST part; photo
  captions are never split). The persona's RESPONSE STYLE core invites the
  split: usually one message, sometimes two, rarely three.
- **Telegram anti-spam gate** (`retention._handle_message`, mirrors the widget gate): per-user
  rate limit with its OWN chat-paced allowance — `antispam.check_rate_limit("tg:{pid}:{uid}",
  cfg["tg_rate_limit_max_per_user"])` (`antispam` group knob, env `TG_RATE_LIMIT_MAX_PER_USER`,
  default 60 per shared `window_sec`; the widget's per-IP 20/10min throttled a live human
  dialogue mid-flow — a real player's messages silently vanished). A block is no longer fully
  silent: the FIRST blocked message of a streak gets a localized in-persona `rtn_rate_limited`
  notice (in-memory `_rl_notified`, cleared when a message passes — one notice per window, so a
  hammering bot can't amplify into Telegram sends), and every gate drop logs a Railway line
  (`retention_rate_limited`/`retention_injection` WARNING; `retention_low_content`/
  `retention_need_deeplink`/`retention_subscription_gate` INFO) — the gates used to drop with
  no log line, making "my messages stopped arriving" undiagnosable from Railway logs. Then:
  overlong input truncated (not rejected), low-content guard → localized model-free nudge
  (`rtn_low_content_reply`), injection scan → sampled audit + (with `injection_hard_block`) a
  model-free in-persona deflection (`rtn_injection_reply`). The other `antispam` settings
  knobs are shared with the widget. The **subscription check is cached** (positive results only, 10 min,
  `retention._sub_cache`; the explicit "I subscribed" button re-checks live with
  `use_cache=False`). `is_photo_request` matches stems at word START (regex `\b`), so "epic"
  can't bypass the photo cooldown. A photo turn never sends a bare image — an empty caption
  falls back to `rtn_photo_caption`.
- **Hand-off is a CHOICE message (`retention._send_handoff_choice`)**: on `[[HANDOFF]]` —
  regardless of the entry type — the bot sends **only** the structured choice message (bold
  `rtn_handoff_title` + `rtn_handoff_choice` body, HTML with a plain fallback). The model's own
  route-out line is **suppressed** (persisted to the transcript, not sent): it duplicated the
  choice card's intro, so the player used to see two messages. The card carries up to TWO
  url-buttons: the player's personal manager (`assign_round_robin_manager`, sticky; a pool/DB
  failure degrades gracefully instead of killing the hand-off) and **support on the site**
  (`retention._site_support_url(lang, product)`: the product's own `site_url` (its public main
  page — the dedicated Structure field) when set, else the per-language `contact_url`, else the
  site's MAIN PAGE derived as the origin of the first site-map entry — the widget lives on the
  site, so the origin is a safe landing. `site_url` is first on purpose: the "support on the
  site" button must land on the site, not a Telegram/contact link an operator set as
  `contact_url`). With only one destination configured it falls back to
  the matching single-option copy (`rtn_manager_intro` / `rtn_handoff_support` + button); with
  neither, the plain `rtn_handoff_support` line — a hand-off never dead-ends. The
  `retention_handoff` admin event records the offered target
  (`manager+site`/`manager`/`site`/`none`). Tests: `tests/test_retention_cta.py`.
- **Managers** (`retention_managers`): round-robin, **sticky** (a returning player keeps their
  manager); the hand-off is a `t.me/<username>` link; only the fact is logged
  (`retention_manager_handoff`).
- **Per-product Telegram config** lives on the `products` row: `telegram_bot_token_enc` /
  `player_api_key_enc` (secretbox-encrypted, like the OpenAI keys — `has_*` flags only out),
  `telegram_bot_username`, `telegram_webhook_secret` (non-secret webhook routing token, the
  Telegram analogue of `widget_key` — resolves an update to its product), `telegram_channel_id`,
  `telegram_channel_url`, `player_api_url`, `site_url` (public main-site URL / home page, edited in
  Structure; the hand-off's "support on the site" button lands here), `retention_enabled`. Webhook
  auth is two-layer: the
  routing token in the path + the deploy-wide `TELEGRAM_WEBHOOK_SECRET` in the
  `X-Telegram-Bot-Api-Secret-Token` header (NOT in the URL).
- **Retention analytics** (`db.retention_overview` / `retention_funnel` /
  `retention_timeseries`): the overview separates LIFETIME player-base numbers (`users` block:
  total/subscribed/muted/unreachable/avg stage) from RANGE activity (`range` block: active/new
  players, player messages, photos, handoffs, pings sent/failed, **ping reply rate** — a sent
  ping answered by a player message within 48h —, **telegram AI cost** `cost_usd` split into
  `cost_dialog_usd` + `cost_photo_usd`, the latter the session-less photo-metadata vision
  calls, so the whole Telegram spend the support dashboard excludes lands here) plus a per-stage
  `stage_distribution`; the **funnel** (deeplinks → starts → linked → subscribed → engaged →
  photo receivers → handoffs) is backed by durable `retention_deeplink_created` /
  `retention_start` admin events (the nonce table is reaped on expiry, so it can never be the
  denominator); the **timeseries** is daily messages/actives/photos/pings/cost (cost also split
  `cost_dialog_usd`/`cost_photo_usd` per day — the `TelegramCostCharts` panels). Endpoints
  `GET /admin/retention/overview|funnel|timeseries` take `from`/`to` + an OPTIONAL
  `product_id`/`partner_id` — omitted, they aggregate the caller's whole accessible scope
  (the global dashboard's retention block), following the support dashboard's
  `resolve_scope_filter` convention.
- **Admin**: the sidebar **Retention** section — one menu entry per surface, no
  page-wide tab strip: **How it works** (the setup-guide checklist that replaced
  `RETENTION_SETUP.md`; the section's landing page), **Knowledge base** — the
  one-document text editor —, **Prompt** (Prompt preview + **Prompt variables**
  — the Telegram-persona editor, `GET/PUT /admin/retention/prompt-variables`;
  empty = the retention default — a SEPARATE prompt, no support inheritance,
  see "Prompt variables" — as an in-page 2-tab strip), **Media** — bulk upload
  + AI metadata + filters —, the **Proactive agent** page (its own route — see
  the "RETENTION AGENT" section; idle pings are a tab there), **Conversations**
  — the Telegram chat list + transcript dialog, see the lifecycle bullet above
  —, **Settings** (`/retention-settings`: Telegram config · Managers · the
  `retention` settings group as its Parameters tab; legacy
  `/settings?module=retention` and `/retention?tab=config|managers` links
  redirect there), and **Analytics**;
  API under `/admin/retention/*` (`api/retention.py`, guarded per
  product) + the `retention` group via the generic `/admin/settings/retention`. Retention copy
  (menu/gate/handoff strings, `rtn_*` keys) is in the translations registry (scope `retention`).
  **Prompt preview** (`GET /admin/retention/effective-prompt`, the SPA's Retention → Prompt
  preview tab) mirrors the support `GET /admin/effective-prompt`: the whole assembled retention
  prompt (retention Layer 1 + the KB document as Layer 2 in the system message; the Layer-3
  user message with the Test-sandbox player, an illustrative photo-candidate row and the
  guardrails), read-only, per product. It also returns the retention prompt variables
  (`prompts.RETENTION_PROMPT_VARIABLES` — raw override + retention default + resolved value per key);
  the SPA shows them read-only with a link to their ONE editor, the Retention → Prompt
  variables tab (no duplicate editor).
- **All existing invariants hold**: retention turns persist atomically as normal
  `chat_messages` + `ai_interaction_logs`, carry the session's `product_id`, use the product's own
  (encrypted) OpenAI keys with the same failover, and DB access stays behind `db.*` helpers.

### RETENTION AGENT — the event-driven proactive loop (`retention_v2.py`, `player_sync.py`)
The ONE proactive regime (the old v1 "ping matrix" — `retention_pings.py`, the
`retention_rules` CRUD, the Pings tab, `pings_enabled`, the starter rule ladder
— was removed; the historic **`v2_` prefix survives only in internal
identifiers** — settings keys, `/admin/retention/v2/*` endpoint paths, the
`retention_v2_*` tables/admin-event types and the module name — for stored-data
compatibility. Every user-visible surface says "agent"). Per-product switch:
`retention.v2_enabled` (hot, ships **ON**) — off means NO proactive messages at
all (the dialogue bot still answers). `retention.v2_dry_run` ships **ON**: the
agent decides and logs but sends nothing until the owner flips it. The worker
starts from `main.py` lifespan under the `RETENTION_SCHEDULER_ENABLED` deploy
switch and wakes every **`retention.worker_interval_sec`** (hot, global-layer,
default 5s, clamped 5..3600 — read live each tick, so the cadence is tuned from
Settings without a redeploy; env default `RETENTION_WORKER_INTERVAL_SEC`), under
an advisory lock. **Event pickup is an atomic claim**
(`db.claim_retention_events`: UPDATE … FOR UPDATE SKIP LOCKED stamps
`processed_at` in the same statement that selects the batch) — the worker
sweep, the admin «Process queue now» button and a second instance can run
concurrently and an event still reaches the pipeline exactly once. This fixed
the duplicate-send bug where one `deposit_confirmed` produced two thank-you
messages: the old plain-SELECT + mark-after-processing let two concurrent
drainers pick up the same event.

- **Data sync is ONE module now (`player_sync.py`)** — the rewritten seam every
  piece of casino data enters through: the profile push webhook, the lazy
  Player-API pull (moved from `retention.py`; thin delegating wrappers +
  `is_safe_outbound_url` re-export keep the old names/tests working), the
  handshake snapshot, and the NEW canonical-event feed. **Events**:
  `POST /partner/{product_id}/event` (same partner-secret Bearer auth as
  player-update; single event or `{events:[…]}` batch ≤500), validated against
  the fixed taxonomy (`player_sync.CANONICAL_EVENTS`, 22 names:
  `deposit_confirmed`, `bet_settled`, `session_started`, `level_up`, …),
  idempotent by `(product_id, event_id)` (`retention_events`, append-only;
  duplicates counted, not stored). An event may carry an optional
  **`tg_user_id`** (top-level or in payload; validated + normalized into the
  payload — no extra column): the explicit Telegram recipient for when one
  `player_id` is linked to several Telegram accounts (multi-tester setups).
  Without it the v2 send resolves the player's most recently updated link
  (`db.get_retention_user_by_player`, `ORDER BY updated_at DESC`); with it the
  exact account is targeted, and an unknown target SKIPS with a ledgered
  reason — never a silent fallback to another account. The admin simulator
  exposes it as the «Telegram recipient» picker (fed by
  `GET /admin/retention/users`; picking an account also fills its player id)
  and the Decisions ledger shows the actual recipient's `@username` under the
  player name. Every stored event also bumps the **activity timestamps** the
  state resolver reads: `deposit_confirmed`→`last_deposit_at`,
  `session_started/ended`→`last_login_at`, `bet_settled`→`last_played_at`
  (forward-only via GREATEST — out-of-order delivery never rewinds a
  timestamp), plus profile-ish payload fields into the snapshot.
- **The pipeline** (`retention_v2._process_event`): event → deterministic
  **state resolver** (`resolve_player_state`: user_status / risk_state /
  lifecycle_stage + the 24h net-loss window from `bet_settled` payloads) →
  deterministic **guards** (`guard_check`: the per-player anti-annoyance state
  on `retention_users` — daily cap `ping_daily_cap` (default 3) / min gap
  `ping_min_gap_hours` (default 2h, 0 = off) / quiet hours / `/stop` /
  unreachable / subscription — plus the per-product **daily AI budget**
  (`v2_daily_budget_usd`, read from
  the decision ledger), the **same-event cooldown** (one reaction per event
  TYPE per player per window — the hot `v2_same_event_cooldown_hours` knob,
  default 5h, **0 = off**: the repeat-testing mode), and the **loss comfort
  window** (`v2_loss_comfort_hours` after a loss signal or `v2_loss_high_usd`
  net loss in 24h: photo removed from the permitted actions, a hard comfort
  constraint injected)) → **agent decision** (one cheap strict-JSON call,
  `prompts.build_retention_v2_decision_messages`; urgency tactics banned,
  silence explicitly first-class; `parse_decision` clamps — anything malformed
  or non-permitted degrades to silence, the guard verdict always wins) →
  **send** via the normal persona stack (`chat_service.generate_retention_ping`
  with `occasion=`/`comfort=`: the `_RETENTION_V2_TOUCH_TASK` event-reaction
  wording + `_RETENTION_COMFORT_BLOCK`; delivery = `rtn_ping_header`, HTML +
  plain fallback, 403 ⇒ unreachable — and `db.record_retention_ping` bumps the
  per-player counters the guards read). The touch task demands the message
  NAME the occasion in natural words (never a vague congratulation, still
  never amounts); `retention_v2.occasion_for` folds whitelisted non-money
  payload details into it (`level_up`→level, `class_up`→class, bonus type,
  `deposit_failed` reason — `_OCCASION_DETAIL_KEYS`). **The trigger travels
  with the turn**: (1) the sent message ALWAYS opens with the persona header
  + a localized human occasion phrase merged onto ONE line
  (`retention_v2._proactive_header`: «✨ Привет, это Ника! Спасибо за депозит
  10 USD» — the `rtn_trig_<event>` translations keys, admin-editable per
  language; `{detail}` carries the safe payload detail and, in CHROME only,
  the amount; a comfort touch gets the bare header, photo sends prepend the
  line to the caption — the old raw «⚡ Trigger: …» line and its
  `v2_show_trigger` knob were removed); (2) the trigger +
  occasion are ALWAYS persisted on the message row
  (`chat_messages.ping_context`, via `db.persist_ping_turn`), so the prompt
  history renders the proactive turn with an inline "[you sent this
  PROACTIVELY - trigger: …]" note (`prompts._retention_history_content`, also
  in the returning-player continuity block) — the persona later KNOWS why it
  wrote and can answer «это ты о чем?» instead of deflecting — and the admin
  transcripts (Conversations + Retention) show a "⚡ proactive: …" marker on
  the turn. Every retention Layer 3 (dialogue
  turns and agent touches) carries a **CURRENT TIME block**
  (`prompts._current_time_directive`, fed with
  `retention.quiet_hours_utc_offset` — the audience clock the quiet hours
  already run on): local weekday + HH:MM + part of day, with a hard "match
  the clock or drop the time-of-day wording" rule — without it the model
  guessed («наслаждайся вечером» sent at 10:00). Tuning the offset knob
  (Retention → Settings) tunes both quiet hours and this block. Only decision-worthy events wake the
  agent — the set is `retention.v2_decision_events` (`None`/unset = the built-in
  `DECISION_EVENTS`, resolved via `retention_v2.effective_decision_events`;
  `bet_settled` stays special-cased: only when the loss window crosses
  `v2_loss_high_usd`, never toggleable). The set is deliberately NOT editable
  from the panel (the agent page's old Triggers tab was removed — the defaults
  are not meant to be tuned; an API consumer can still PUT the `retention`
  group). Everything else is state food, marked
  processed silently — no model call, no ledger row (the agent's guide tab
  explains exactly this, so "why is my event not in Decisions?" is self-serve).
  **Humanizing send delay:** an event is reacted to a per-event pseudo-random
  `v2_send_delay_min_sec`..`v2_send_delay_max_sec` (defaults 300/900 — 5–15
  min, ~10 avg) AFTER it arrived — an instant thank-you three seconds after a
  deposit reads as transaction surveillance. Implemented at CLAIM time
  (`db.claim_retention_events` skips events younger than their id-keyed
  delay), so it survives restarts and instances; the admin «Process queue now»
  button bypasses it (`ignore_send_delay=True`).
- **Idle re-engagement (`retention_idle.py`)** — the agent's INACTIVITY
  trigger: the admin-managed rules ladder in `retention_rules` («player quiet
  N days → Nika writes first»; triggers `bot_inactivity` /
  `casino_inactivity` / `no_deposit`, per-rule action message|photo, English
  `intent` hint (ensure_english-guarded), VIP-tier filter, per-player
  `cooldown_days`, priority). Swept from the SAME worker loop (called at the
  end of `run_product_events`, self-paced per product by the hot
  `retention.idle_sweep_interval_sec` knob — default 600s), bounded by the
  SAME machinery: `db.eligible_ping_users` prefilters (subscribed / not muted
  / not unreachable / `ping_min_gap_hours` / `ping_daily_cap`), quiet hours,
  the daily AI budget, and **`v2_dry_run`** (a matched rule logs a
  `trigger_kind='idle'` ledger row and sends nothing). A delivered idle ping
  persists via `db.persist_ping_turn` with an `idle_reengagement: …`
  ping_context, lands in BOTH ledgers (`retention_pings` with `rule_id` — the
  per-rule cooldown reads it — and `retention_v2_decisions`), and the message
  text comes from the normal persona ping stack (`_RETENTION_PING_TASK` idle
  wording + `rtn_ping_header`). Per-product master switch
  `retention.idle_pings_enabled` (hot, ships ON; env
  `RETENTION_IDLE_PINGS_ENABLED`); NEW products are seeded with the
  production-tuned 3–60-day starter ladder (`retention_idle.seed_starter_idle_rules`, called from
  `db.create_product`, only when the product has no rules). Admin: the
  **Idle pings tab of the Proactive agent page** (`/retention-agent?tab=idle`;
  the legacy `/retention?tab=idle` link redirects — rules
  CRUD, enable switches, a «Run now» test sweep that skips quiet hours/pacing,
  and the send ledger) over `GET/POST/PUT/DELETE /admin/retention/idle/rules*`,
  `GET /admin/retention/idle/ledger`, `POST /admin/retention/idle/run`.
  Tests: `tests/test_naturalness.py`.
- **The decision ledger (`retention_v2_decisions`)** is the audit trail: ONE
  row per decision whatever the outcome — state snapshot, guard verdict +
  reasons, the agent's action/tone/intent/reason, dry-run flag, delivery,
  summed cost (decision + generation; each model call still lands in
  `ai_interaction_logs`, invariant §4, session-less like the photo-metadata
  calls). The daily budget reads this ledger.
- **Admin**: the sidebar **Proactive agent** page
  (`admin/src/pages/RetentionAgent.jsx`, route `/retention-agent` with a
  legacy `/retention-v2` alias, RequireProduct-gated) — status header
  (enabled/dry-run/budget/queue **plus the worker-liveness row**: the deploy
  scheduler switch + sweep interval and a DB-derived activity snapshot — last
  event / last processed / last decision / today's decision mix — via
  `db.retention_v2_activity`, correct across instances because it reads the
  durable tables, not an in-process heartbeat), the **event simulator**
  (inject any canonical event as `source='simulator'` — exercise the whole
  pipeline before the partner integration exists; **per-event sample
  payloads**, several variants each (`PAYLOAD_SAMPLES` in the page),
  auto-filled on event change with field names mirroring what the pipeline
  actually reads, plus a chip saying whether the picked event wakes the agent
  or is state food), «Process queue now», the event log and the decision
  ledger — both **deletable** for live-testing cleanup (row delete + «Clear
  all»): deleting an event nulls the ledger's `event_pk` links first (NB the
  event log feeds the state resolver, so deletes rewrite the loss window);
  deleting a decision "refunds" its cost from today's budget and re-arms the
  same-event cooldown (both read the ledger); every delete logs a
  `retention_v2_*` admin event. Two more tabs: **System log**
  (`GET /admin/retention/v2/logs` → `db.list_retention_v2_logs`: the durable
  `retention_v2_*` admin events, the admin-readable mirror of the Railway
  lines — the pipeline emits one structured line per decision
  (`retention_v2_decision`), per guard block (`retention_v2_guard_blocked`)
  and per failed send (`retention_v2_send_failed`)), and **How it works &
  testing** (the operator's guide: the pipeline, the on/off + dry-run + worker
  interval knobs, where persona/tone/KB/header/photos/language come from,
  which events wake the agent — fed live from `/v2/status`'s
  `decision_events` / `photo_events` split so the guide always matches the
  code —, the guard-reason → settings-knob table **with the product's CURRENT
  effective values** (from `/v2/status`'s `guards` block), a step-by-step
  testing checklist and the cost model). API:
  `/admin/retention/v2/status|events|decisions|logs|simulate-event|run` +
  the four DELETE routes (product-scoped via the admin_auth choke points).
  The agent knobs are normal `retention`-group settings (Retention → Settings
  → Parameters → «Proactive agent» + «Send-frequency guards» sections; the
  send-frequency guards — daily cap, min gap, same-event cooldown, quiet
  hours, budget, loss window — are THE dials for how often one player may be
  written to). Tests: `tests/test_retention_v2.py`.

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
   per-session rows carry the session's `product_id` (per-product dashboards depend on it).
5. Two-key failover races the fallback after the switch timeout; log every failover.
   The keys are the PRODUCT's own (encrypted at rest) when set, else the deploy env keys.
6. No ORM, no migrations: schema is `init_db()`; new columns via guarded `ALTER`; all DB
   access through `db.*` helpers.
7. Model-facing prompt is English (token-efficient); KB may be in any language; answers
   in the resolved language. User-facing copy + user-input detectors stay multilingual.
8. Never request card numbers / CVV / passwords / 2FA codes / seed phrases; never invent
   player-facing facts — KB uses `{{PLACEHOLDER}}` tokens the owner replaces.
9. `_PRICING` is "verify before trusting"; cost is derived, not ground truth.

## Admin / management

Map of what lives where:

- **Admin auth + roles** (`api/admin_auth.py`, `auth.py`): `POST /admin/login` **requires `email`
  + password** — every admin signs in as a named `admin_users` account. The password is checked
  against the salted **PBKDF2-HMAC-SHA256** hash in `admin_users`
  (`auth.hash_password`/`verify_password`, stdlib only) → the user's stored role; a missing email
  is a 400, bad credentials a non-enumerating 401. Login is rate-limited and logs
  `admin_login_failed`. The token is signed with `ADMIN_JWT_SECRET` and carries `role` + `email`.
  **`ADMIN_JWT_SECRET` signs admin sessions — set a distinct strong value in prod** (it falls back
  to `SESSION_JWT_SECRET`, flagged at startup). **There is no password-only owner login and no
  `ADMIN_PASSWORD` env var** (both removed); the legacy `owner` role is gone. **Two roles:**
  `admin` may write; `manager` is **read-only**. The dashboard is no longer gated by an env switch
  — it is always mounted and protected by named-user login (an empty `admin_users` means nobody can
  log in, so there is **no bootstrap path** — seed the first account against a live DB).
  `require_admin` guards every `/admin/*` route: it verifies the JWT **and re-checks the named
  account against `admin_users` on every request** (a JWT has no revocation, so without this a
  deactivated/deleted admin kept full access until token expiry — up to `ADMIN_TOKEN_TTL_MIN`);
  the DB `role` is authoritative over the token's role claim, so a demotion applies immediately,
  and a token without an `email` claim is rejected. **`require_admin_write`** (role in
  `WRITE_ROLES = ("admin",)`, else **403**) guards every mutating route (KB, settings, variables,
  test profile, user management); mutating writes record `updated_by` as the account **email**
  (falling back to the role for safety). PBKDF2 verify/hash run in `asyncio.to_thread` so the
  ~100ms CPU burn never blocks the event loop. `GET /admin/me` returns the caller's role/email so
  the SPA can role-gate its UI (managers lose the Settings / Users tabs and all edit controls —
  cosmetic; the server is authoritative).
- **User management** (`api/admin.py` `/admin/users*`, the **Users** tab, admins only):
  CRUD over `admin_users` (email + password) **plus the membership editor** — WHAT an account
  may touch is its `admin_memberships` (role `admin`/`manager` × scope global/partner/product).
  The SPA create form picks the initial role × scope (partner/product pickers fed by
  `GET /admin/structure`; the create body carries `scope_type`/`partner_id`/`product_id` —
  omitted, the backend defaults to a GLOBAL membership); the edit form hosts the
  **Access (role × scope)** panel — the memberships table with grant/revoke over
  `POST/DELETE /admin/users/{email}/memberships`. Granting the same scope again replaces its
  role (`db.add_membership` upserts). The caller may grant/revoke only scopes it holds an
  ADMIN role over (`_require_scope_admin`), may not change its OWN memberships, and manages
  only accounts whose ENTIRE membership set lies inside its reach (`_can_manage_user`; an
  account with NO memberships is manageable only globally). The SPA edit form deliberately has
  **no flat role field**: the legacy `PUT /users/{email}` `role` writes a GLOBAL membership
  and requires global write, so role changes go through the memberships panel. No email
  delivery, no reset flows — an admin sets passwords directly. A user can't
  demote/deactivate/delete **itself** (self-lockout guard). With no owner recovery path,
  **keep at least two `admin` accounts** so a forgotten password can't lock everyone out. The
  password hash never leaves `db.py` (`_row_to_admin_user` drops it).
- **Settings** (`settings.py`, `app_settings` table): hot-reloaded runtime tuning with
  precedence `app_settings` (DB) → env → default. A sync in-process cache (populated at
  startup, reloaded on write, and **re-pulled every 60s** by `main._settings_refresh_loop`
  so a write made by another instance — or directly in the DB — applies without a restart)
  is read by `antispam`/`escalation`/`openai_client`/`language`/
  `auth`/api; writes validate hard and log `setting_updated`. **GLOBAL-ONLY fields**
  (`settings.GLOBAL_ONLY_FIELDS`: `retention.worker_interval_sec`,
  `general.admin_token_ttl_min`, `general.body_max_bytes`) are read by deploy-wide
  machinery that runs OUTSIDE any product scope (the agent worker loop, admin-token
  minting, the body-cap middleware), so a product-layer override of them can never
  apply — `_group()` ignores them on the product layer, `PUT /admin/settings/{key}`
  strips them from product-layer saves (self-healing older stored junk on the next
  save), and the SPA locks the field with a "switch to All products" hint when a
  product is selected. This fixed «I changed the worker interval on a product and
  nothing happened». Groups: `escalation`
  (`high_risk_keywords`, `human_request_keywords` — content tuning, so its ONLY editor is the
  Common → Escalation keywords page; the Settings tab skips this group to avoid a duplicate
  editor. `max_messages_per_session` moved to `general`; a legacy `escalation` override is still
  read as a fallback),
  `language` (default + supported
  set **+ `names`** — custom display names for languages added beyond the built-in
  `language.LANG_NAMES`; every language read goes through `language.default_code()`/
  `supported_codes()`/`all_language_names()`. Adding a language is ISO-validated: the admin
  Language tab picks from `language.ISO_639_1` (the full ISO 639-1 catalogue), so a new
  language only enters with a correct code + name, and `settings.validate_setting` rejects any
  supported/`names` code not in that catalogue. `GET /admin/meta` exposes `languages`
  (selectable catalogue), `supported`, `default_language`, and `iso_catalog` for the picker),
  `antispam` (rate limit/window/cooldown/input cap **plus**
  `injection_hard_block`, and the low-content guard `low_content_block` /
  `min_meaningful_chars`), `model` (OpenAI tuning — see the failover section), and `general`
  (technical operational knobs with no other home: `session_ttl_hours`, `admin_token_ttl_min`
  — the admin login lifetime, env `ADMIN_TOKEN_TTL_MIN` as default —, `max_messages_per_session`,
  `history_max_turns` — how many recent turns feed the model's prompt history, env
  `HISTORY_MAX_TURNS`/20 default; the full transcript is always persisted —, and
  `body_max_bytes`. `contact_form_url` is a dead legacy field: a value stored by old builds is
  auto-migrated on boot into the default product's Translations (`db._migrate_legacy_contact_url`)
  and deleted; `settings.general()` still resolves the key, but only the `CONTACT_FORM_URL` env
  default can feed it now — used solely as the default product's contact-button fallback). Three more app_settings keys live OUTSIDE `SETTING_KEYS` (each with its
  own admin endpoint, so they never appear in the generic Settings editor): `test_profile`,
  `prompt_variables` and `translations`. **The prompt WORDING is NOT a settings group** — it
  lives in `prompts.py` (the single source of truth), not `app_settings`; only the
  prompt-variable VALUES are stored. The goal is that every non-secret *operational*
  knob lives in the admin panel and only true secrets (API keys, JWT secrets, `DATABASE_URL`,
  handshake/Turnstile secrets) — plus the network-perimeter deploy
  vars (`CORS_ALLOW_ORIGINS`, `TRUSTED_PROXY_COUNT`) — stay in Railway env. There is no seed:
  an empty `app_settings` resolves through env → default, and the owner's first write to a
  group persists that override in the DB.
- **Dashboard data API** (`api/admin.py` + `db.py` aggregation + `metrics.py` derived
  rates): overview/timeseries/by-topic/by-language/sessions/session/unresolved.
  `resolution_rate` is a documented PROXY (counts "not escalated", incl. abandoned →
  `sessions_open` tracked separately). **The support dashboard is SUPPORT-only: every
  aggregate excludes `consumer='telegram'`** so retention/Telegram spend and sessions
  never inflate it. Session counts filter `consumer <> 'telegram'`; the cost aggregates
  (`overview_aggregates`, `timeseries` `cost`/`cost_per_session`) **join `chat_sessions`**
  so they count only non-telegram turns AND drop the `session_id IS NULL` photo-metadata
  vision calls (those are retention); `by_topic`/`by_language` add the same exclusion. The
  Telegram module has its own home — `retention_overview`/`retention_timeseries` — whose
  cost is scoped on the LOG row's product so it INCLUDES the session-less photo-metadata
  calls, and is split into `cost_dialog_usd` (engagement turns) + `cost_photo_usd`
  (photo-metadata generation), summing to `cost_usd`. The SPA renders that split as the two
  **Telegram cost** panels (`components/charts.jsx` `TelegramCostCharts`: total-over-time +
  cost-by-source stacked bars), shown on both the dashboard Retention block and Retention →
  Analytics. The overview also carries AI-API health:
  `avg_latency_ms` (mean end-to-end latency of the SUCCESSFUL OpenAI calls — failures
  carry no meaningful latency, so they are excluded from the average), `ai_calls_total`
  and `failed_calls` (from `ai_interaction_logs`). The SPA renders the KPI tiles as two
  rows of six, grouped by meaning (sessions/engagement, then AI/cost/performance). **Cost**
  is surfaced per row: `by-topic`, `by-language`,
  and `sessions` each carry a `cost_usd_total` (summed from `ai_interaction_logs` via a join/CTE)
  rendered in the SPA tables. **Date ranges** are half-open and a date-only `to=YYYY-MM-DD` is
  made **inclusive** of that whole day (`api.admin._range` adds one day), so "today" isn't dropped.
  The **Unresolved** queue lists engaged sessions that still need attention — both `escalated` and
  abandoned `open` chats with ≥1 user turn (resolved excluded), grouped by topic. It carries the
  **same per-session fields as the Sessions tab** (created, lang, status, msgs, cost) + the first
  message, so a triager can scan and pick (`db.unresolved_by_topic` joins lang + cost; CSV export
  mirrors them). **Timestamps render in the viewer's local timezone** — the API returns tz-aware
  ISO strings and the SPA formats them client-side via `fmtDateTime`/`toLocaleString` (a UTC `06:00`
  shows as `09:00` for a UTC+3 admin), so the dashboard always reads in the operator's own time.
- **KB Variables sub-tab** (`api/admin.py` `/admin/kb/variables`, the **Knowledge base →
  Variables** sub-view in the SPA): list + edit the admin-managed `{placeholder}` registry (see
  "KB variables" above). Read returns `updated_at` as an isoformat string so `JSONResponse` can
  serialize it.
- **The prompt WORDING is the file `prompts.py` (single source of truth, a dry template, NOT
  editable from admin).** The Layer-1 core (`SYSTEM_CORE` — Nika's tone-of-voice + the
  absolute/escalation/responsible-gaming/links rules), the STATIC Layer-1 directives (greeting,
  formatting, KB-grounding, escalation restraint, suggested questions, finish-chat, lead-forward),
  the DYNAMIC Layer-3 directives (language, personalization, topic routing) + the recency
  guardrails, and the forbidden-topics list/refusal are constants in that file. To change the
  wording you edit `prompts.py` and redeploy — there is no admin editor, no `prompt_versions`
  table, no A/B split, no `system_prompt`/`layer3_prompt` settings group. (This replaced an
  earlier design where the core was versioned in the DB and edited from the panel; it was removed
  so there's exactly one place the prompt comes from.) The brand-specific VALUES the template
  renders with ARE admin-editable — see "Prompt variables" above and the **Prompt → Prompt
  variables** sub-tab (`GET/PUT /admin/prompt-variables`), which also hosts the escalation
  keyword lists (over the `escalation` settings group) and the test player profile blocks.
  **Read-only effective-prompt view** (`api.admin._build_effective_preview` +
  `GET /admin/effective-prompt`, the **Prompt → Preview** sub-tab in the SPA): so the owner can
  always SEE the whole assembled prompt, this endpoint reuses `prompts.build_messages` with a
  sample player + a sample specialized topic's KB and returns the complete prompt split into the
  system message (Layer 1 core + static directives + Layer 2 KB) and the user message (the
  dynamic Layer-3 directives + player context + recency guardrails), prompt variables already
  substituted. The SPA renders it as read-only blocks. It is resilient — if topics/KB can't load
  it still renders Layer 1 + the Layer-3 block, never breaking the page. (Layer 2, the per-topic
  KB, is the one prompt input still edited in the admin — in the Knowledge-base tab — because
  it's answer content, not instructions.)
- **Translations tab** (`translations.py`, `api/admin.py` `GET/PUT /admin/translations`, public
  `GET /api/chat/i18n`): per-language editing of every user-facing widget string — chrome copy,
  server-generated service replies, the per-language escalation contact-button URL (the
  `contact_url` key, http(s)-validated; empty = no button link — only the default product
  falls back to the `CONTACT_FORM_URL` env default), and the
  per-language topic titles (via the existing
  `POST /admin/kb/topics` upsert). See "Translations" above. The SPA renders the registry in
  FOUR fixed blocks (`Translations.jsx` `SECTIONS`, keyed on scope + the client-side
  `SERVICE_KEYS` list): the general widget interface, the support bot's messages to the player,
  the Telegram retention bot's messages, and the service/error notices — so the owner tunes the
  bots' actual voice without wading through technical fallbacks. A new registry key lands in a
  bot-messages block automatically unless it is added to `SERVICE_KEYS` (do that for any new
  error/guard nudge). The admin panel itself stays English.
- **KB editing** (`db.*` helpers, `api/admin.py` `/admin/kb/*`): **one KB text per topic**,
  single-language. `GET /admin/kb/content?topic_id=` reads it, `PUT /admin/kb/content` sets it
  (updates the topic's active entry in place, or inserts one), `DELETE /admin/kb/content?topic_id=`
  soft-clears it (`active=false`). No versioning, no per-language entries — the Layer-3 language
  directive still makes the model answer in the player's language regardless of the KB language.
- **Escalation** (`escalation.build_payload`): returns the localized contact-button payload
  (copy AND the per-language button URL from the translations registry). No ticket snapshot,
  no Telegram notifier — the hand-off is the contact button only.
- **Signed handshake** (`auth.sign_handshake`/`verify_handshake`, `api/chat.create_session`):
  with `WIDGET_HANDSHAKE_SECRET` set, only a valid signed blob is trusted for
  `user_context`; raw browser context is ignored. No secret ⇒ dev behaviour. The
  injection sanitizer runs in every mode.
- **Test player profile** (`settings.test_profile`/`validate_test_profile`,
  `app_settings['test_profile']`, `api.admin` `GET/PUT /admin/test-profile`, the **Common →
  Test player profile** page — the old Test sandbox tab, then a block on Prompt variables,
  now its own page in the shared Common section):
  in test/dev (**no** `WIDGET_HANDSHAKE_SECRET`) there is no host
  site to sign a handshake, so this stored profile stands in for it at `create_session`. It
  drives the Layer-3 player data the model sees (`id, full_name, email, activation_status,
  country, balance, vip_level, registration_date` — the `prompts._CONTEXT_FIELDS` whitelist) so
  the owner can test name personalization. There are **no** language knobs — the session
  language always follows the browser. `enabled=false` ⇒ fall back to the widget's built-in
  context. The profile is **ignored** when a handshake secret is set (the host site is
  authoritative then). This is the single seam for "manage the test player on test, the real
  site supplies it later".
- **Admin SPA** (`admin/` at the repo root): a React Admin (marmelab) + Vite app.
  The two-stage Dockerfile builds it (node stage → `admin/dist`) and `main.py`
  serves it at `/admin` (hash router; hashed assets under `/admin/assets`,
  vite `base: '/admin/'`) — same origin as the `/admin/*` JSON API, so the
  admin needs no CORS and no `VITE_API_URL` (relative URLs). The old
  hand-rolled SPA (`frontend/admin/`, `/admin-static`) was removed. The custom
  dataProvider (`admin/src/dataProvider.js`) maps react-admin resources onto
  the real endpoints; auth is `POST /admin/login` → Bearer JWT; the header
  carries the Partner → Product switcher (selection in localStorage, sent as
  `product_id`/`partner_id` query params). Local dev: `npm run dev` in
  `admin/` (set `VITE_API_URL` + allow the dev origin in
  `CORS_ALLOW_ORIGINS`); a separate static deploy also still works.
  **Sidebar** (`App.jsx`): three collapsible sections (Support chat / Telegram ·
  Retention / System) whose open state persists in localStorage; the Retention
  sub-tabs are exposed as sub-menu entries that deep-link `/retention?tab=…`
  (the page reads `?tab=`, like the Prompt page). **Product-scoped surfaces are
  gated** by `components/RequireProduct` — KB, KB variables, Prompt, Translations,
  Retention and the Conversations / Unresolved lists (incl. the conversation
  detail view) refuse to render without a concrete product selected in the header
  (otherwise they'd silently edit/show the default product's data), showing a
  "select a product" notice instead; this applies to admins and managers alike.
  Dashboard, Structure and Users stay usable at the all/partner scope. **Settings** (`pages/Settings.jsx` + `settingsSchema.js`) is a
  typed, tabbed editor (one tab per group + a Languages tab with an ISO-picker
  add-language / default / custom-name editor) — not a raw-JSON textarea — with a
  scope banner (global defaults vs the selected product). **Settings are split into
  three MODULE surfaces**: Support chat → Chat settings (`?module=support` — widget
  anti-spam + chat limits) and System → Settings (`?module=core` — model, languages,
  technical limits) on the standalone Settings page, plus the retention module (the
  whole `retention` group + the Telegram rate-limit slice of `antispam`) embedded as
  the **Parameters tab of Retention → Settings** (`/retention-settings?tab=params`;
  the exported `SettingsModule` component — legacy `?module=retention` links
  redirect there).
  The split is presentation-only — schema fields carry a `module` tag
  (`settingsSchema.js` `GROUP_MODULE`/`fieldsForModule`) and a group is still
  SAVED whole (the form round-trips unseen fields unchanged). Each module page
  opens with a plain-language "How it works" accordion (an intro + concrete
  bullet points linking to the deeper guide pages); long field explanations
  render as an (i) tooltip instead of a helper line. **Operator guides**: the
  Support chat sidebar opens with a full "How it works" page
  (`pages/SupportGuide.jsx`, route `/support-guide`) — the support twin of the
  Proactive agent's "How it works & testing" tab: the message pipeline, the
  content map ("where do I fix this text?"), topic routing, escalation, the
  testing checklist and costs. **The admin chrome is
  bilingual (EN/RU)**: `src/i18n.js` is a gettext-style dictionary keyed by the
  English source strings, `t()` wraps render sites, and the AppBar carries an
  EN/RU toggle (persisted in localStorage; switching reloads). Long guide/help
  prose with inline markup stays ONE dictionary string via `components/Rich.jsx`
  (`rich(t('…'))` renders a tiny subset: backtick code spans, `**bold**`,
  `[label](url)` links) — EVERY user-visible admin string routes through `t()`,
  including the guide pages, tables, confirms and toasts. Only the chrome is
  translated — the CONTENT stays English (see the English-only guard below).
  **Bundle is code-split**: pages load via `React.lazy` (per-page chunks) and
  vite `manualChunks` splits recharts/mui/react-admin/vendor, so the entry chunk
  is ~55 KB instead of a 1.5 MB monolith. Sidebar custom entries all render
  through ONE `SubItem` component with an EXACT pathname matcher (an earlier
  `startsWith('/retention')` bug lit up "Telegram config" while the
  /retention-agent page was open, and the differently-rendered Menu.Item sat out
  of line with its ListItemButton siblings). **Token/cost counters**: prompt and
  KB editors (support KB, retention KB, both prompt-variable editors, both
  prompt previews) render a live `TextStats` line — characters, estimated
  tokens, and the uncached-input cost for the CURRENT model, priced from
  `GET /admin/meta`'s `model_pricing` block (`openai_client.pricing_for_model`). **Topic titles are
  single-sourced** in Translations → Topic names; the KB form keeps only the
  canonical English title (the prompt is English-only) and links there. **SET-state
  is explicit**: `components/SetBadge` shows a green check for configured secrets
  and `components/SecretField` adds a Clear button so an operator can save an empty
  value (fall back to env) — used in Structure + Retention config; the test-profile
  handshake notice links to Structure to clear the product's handshake secret.

- **Account page + slim header** (`admin/src/pages/Account.jsx`, `/account`,
  opened from the AppBar user menu): shows the caller's email, active status,
  role and access groups (memberships) + registration date (from `GET
  /admin/me`, which now returns `created_at`/`active`). The **light/dark theme
  toggle and the EN/RU admin-language switch moved here** from the AppBar
  (`useTheme()` / `setAdminLang`), so the header carries only the product
  switcher + refresh + user menu. The user menu is a custom right-anchored
  `Menu` (react-admin's default popover opened off-screen in this RA version);
  react-admin's built-in LocalesMenuButton is suppressed (`i18nProvider.getLocales
  = () => []`) so it isn't a redundant second language control.
- **Observability — System → Logs** (`admin/src/pages/Logs.jsx`, `/logs`,
  admin-only, red unread badge in the sidebar). Two tabs:
  - **System logs** = the app's own runtime logs (the "Railway logs") mirrored
    in-app. `logcapture.py` attaches a buffer handler to the **ROOT** logger with
    a denylist filter (framework noise — uvicorn access log, httpx, asyncpg, … —
    is dropped): every app module logs via `getLogger(__name__)` (sibling loggers
    under root, NOT descendants of the service logger), so attaching only to
    `config.SERVICE_NAME` captured just main.py/health.py and dropped every
    escalation / failover / retention decision / model error the view promises.
    The logging hot path only appends to an in-memory deque (thread-safe, no DB,
    no recursion); a background flush loop in `main.py` (`_log_flush_loop`)
    batch-inserts into the bounded `app_logs` table (`db.insert_app_logs`) and
    prunes to the newest 5000 (`db.prune_app_logs`). `GET /admin/logs`
    (level/text filters), `GET /admin/logs/unread` (WARNING+ since the caller's
    per-admin marker in `app_log_reads`), `POST /admin/logs/read`. **GLOBAL-scope
    only** (`_require_global_viewer` → `admin_auth.global_role`): `app_logs` is one
    deploy-wide table with no `product_id`, so a product/partner-scoped admin must
    NOT read other tenants' operational data — a global manager (read-only
    hub-wide) may, product/partner admins 403. The sidebar badge polls the unread
    count.
  - **Activity (audit)** = who changed what. An audit middleware in `main.py`
    (`audit_admin_actions`) writes one `admin_audit_log` row per SUCCESSFUL
    mutating `/admin/*` request: the actor (stashed on `request.state` by
    `require_admin`), a friendly action label, the product/partner scope (from
    `?product_id=` or a `/products/{id}` path) and time. Best-effort — never
    affects the response. `GET /admin/audit` applies **tiered visibility**
    (`db.list_audit`): SCOPE (products within the viewer's reach / the selected
    product/partner) × ROLE (a manager sees only manager-authored actions, an
    admin sees everything in reach); hub-global actions (user mgmt, system
    settings) show only to a global viewer. NB only admins can mutate today, so
    audit actors are admins — the manager/admin split is future-proofing.
- **Sidebar IA — flat sections, one entry per surface** (`admin/src/App.jsx`):
  four collapsible sections and NO page-wide tab strips (the earlier cascading
  hubs — Support's Content entry with its `RouteTabs` strip and the retention
  page's top section strip — were flattened; `contentTabs.js` is gone). **Support
  chat**: How it works · Conversations · Escalations · Knowledge base (with its
  KB ↔ Variables sub-strip) · Prompt (Preview/Variables in-page tabs) · Chat
  settings · Analytics. **Common** — the cross-module surfaces shared by BOTH
  bots: Translations · Site map · Escalation keywords · Test player profile.
  **Retention**: How it works · Knowledge base · Prompt · Media · Proactive
  agent (events/decisions/idle pings/logs/guide tabs) · Conversations · Settings
  (`/retention-settings`) · Analytics. **System**: Structure · Settings · Logs ·
  Users · API keys. All sidebar entries share one 40px icon column (RA's
  MenuItemLink width) so labels align.

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
- **Authorization decisions go through the `api/admin_auth.py` choke points**
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
  CMD reads `$PORT`, no `startCommand` override. Health check is `/healthz`.
- Env var reference lives in `README.md` (§ "Environment variables").
- **Two docs, two audiences:** `README.md` is the human-facing overview; **`CLAUDE.md`
  (this file) is the LLM/agent guidance** — architecture, invariants, conventions. They are
  no longer mirrored or auto-synced (the old `docs-sync` hook/Action and `scripts/sync_readme.sh`
  were removed). Edit each for its audience: update `CLAUDE.md` when you change architecture or
  invariants, and `README.md` when the human-facing overview or env table changes. The root
  test page (`main.py` `/`) serves a static `frontend/test.html` (a short feature summary), not
  this file.
