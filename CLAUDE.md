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
  handshake secret and the **reCAPTCHA secret** live on the product row,
  **encrypted at rest** via
  `secretbox.py` (stdlib HMAC-CTR keystream + encrypt-then-MAC; master key =
  `SECRETS_MASTER_KEY` env, falling back to `SESSION_JWT_SECRET` with a startup
  warning). They are write-only through the API (`PUT /admin/products/{id}/secrets`
  → only `has_*` flags come back); `db.get_product_openai_keys` /
  `get_product_handshake_secret` / `get_product_recaptcha_secret` are the only
  decrypting readers. A product without
  its own keys falls back to the deploy env keys
  (`openai_client.client_for_product`, cached per product + key fingerprint).
- **Per-product reCAPTCHA**: each product (domain) runs its own reCAPTCHA v3
  property — the PUBLIC `recaptcha_site_key` on the product row (edited in
  Structure; `PUT /admin/products/{id}` body field) is served to the widget via
  `GET /api/chat/i18n` and adopted automatically (`widget.js fetchI18n` — no
  embed change; a host page may still pin its own via `mount()`), and the
  secret is a normal encrypted product secret. `create_session` resolves the
  product FIRST and verifies against the product secret
  (`antispam.verify_recaptcha(secret=...)`); the deploy env
  `RECAPTCHA_SITE_KEY`/`RECAPTCHA_SECRET` pair is only the fallback.
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
  widget: snippet, widget key, reCaptcha, CORS), `GET /integration-data` (player
  data transfer & sync — the ONE home for the whitelist fields, signed-handshake
  format + signing samples, the lazy-pull Player API contract, the push webhook
  and the activity timestamps; other pages link here instead of duplicating the
  contracts), `GET /integration-chat-api` (the public Chat API reference + the
  mandatory client logic for a custom UI), `GET /integration-telegram` (the
  Telegram retention bot: deeplink contract, subscription gate, ping matrix,
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
- **Skills** in `.claude/skills/` scaffold the recurring cross-file changes so no
  touch-point is missed: `/preflight`, `/add-setting`, `/add-translation`,
  `/add-db-column`, `/add-admin-endpoint`. Reach for them when doing that kind of
  change.

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
before the owner translates/uniquifies: (1) the kb_variables registry; (2) the generic starter
topics + KB texts from `starter_kb.STARTER_TOPICS` — brand-neutral, English, Q&A-style casino
support content, **seven topics** mirroring the live picker (deposits, withdrawals, account &
verification, bonuses, betting & games, technical + `other` last) that asserts **no**
brand-specific facts (no names, URLs, amounts, timeframes — it
points the player at the cashier/terms/game-info UI instead) and deliberately copies nothing
from any live product's KB; (3) the FULL `prompt_variables` set into `product_settings`
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
bot also works out of the box; and (5) the **starter ping-matrix rules**
(`starter_kb.STARTER_RETENTION_RULES` via `db.seed_starter_retention_rules`) — an escalating
`bot_inactivity` re-engagement ladder (9 rules from a 3-day check-in through a 60-day last
warm reach, message + photo actions, `priority` climbing with the idle window so the longest
silence wins) with brand-neutral English `intent` hints only. The casino triggers
(`casino_inactivity`, `no_deposit`) are deliberately **not** seeded — they stay silent until
the partner feeds `last_login_at`/`last_played_at`/`last_deposit_at`. Unlike the other seeds,
`seed_starter_retention_rules` runs BOTH at `create_product` AND **at boot for every existing
product** (in `init_db`, after the tenancy migration): it is idempotent + additive — a rule is
inserted only when the product has no rule of that exact name, so a hand-made or edited rule is
never touched or duplicated (same never-overwrite contract as `seed_kb_variables`), and an
already-live product picks up newly-shipped starter pings on the next deploy. Translations and
the `retention`/other settings groups need
**no** per-product seed: their shipped defaults resolve for every product until overridden.
`db.seed_starter_kb` is idempotent-safe: it inserts only
topics the product doesn't have and writes a KB entry only for a topic it just created — it
can never overwrite existing content. The boot-seeded default product's KB/prompt-variables are
untouched (it goes through `_migrate_tenancy`, not `create_product`; the ping-rules top-up is
the one seed that DOES reach it, additively). Tests in `tests/test_starter_kb.py` pin
the no-brand-leak contract + the priority-monotonicity invariant (support + retention + ping
starters alike).

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
`settings.validate_prompt_variables`; empty values fall back to the defaults). That sub-tab also
hosts two sibling blocks: the **escalation keyword lists** (a friendlier one-per-line editor over
the existing `escalation` settings group — the multilingual trigger stems stay multilingual, they
scan the player's raw message, not the prompt) and the **test player profile** (the old Test
sandbox tab, moved here since it exists to test the prompt's personalization; the legacy `#test`
hash redirects).

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
Rate-limit and cooldown use **in-memory dicts** — fine for Phase 1 but they do not span multiple
instances. reCaptcha is verified at session create and skips gracefully (logged) when
`RECAPTCHA_SECRET` is unset. **High-volume block events are SAMPLED**
(`db.log_admin_event_sampled`: `rate_limited`, `injection_blocked`, `low_content_blocked`,
`recaptcha_skipped`, `model_error` — max 20 per type per 5 min, in-memory): each rejected request
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
`POST /session` (reCaptcha + token + DB row) fires only when the player actually picks a topic
(`onTopic`), NOT on panel open — the old open-time warm-up minted a DB session (and burned the
per-IP `session:` budget) for every visitor who opened and closed the widget without engaging.
The topic picker still paints instantly from the session-free cached `GET /topics`. The tap
itself is **optimistic**: `onTopic` shows the conversation view + the canned greeting bubble
immediately (both are client-side) and runs the slow setup — reCaptcha token + `POST /session` +
`POST /topic` — in the background (`state.setupPromise`); the player's first `sendMessage` awaits
that promise, so the send transparently waits for the token instead of failing (it used to await
the whole session create BEFORE showing the chat, freezing the picker for seconds after the tap).
A failed setup returns the player to the picker with the localized start error. The reCaptcha
script itself is pre-loaded at widget mount (`loadRecaptcha` in `buildUI`) — it's a third-party
fetch and was the slowest piece of the tap-time setup.

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
admin editor is the **Prompt → Prompt variables** sub-tab (content tuning, next to the prompt);
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
   `injection_blocked`. With `injection_hard_block` (now **on by default**, tunable in the
   `antispam` settings group) it also **rejects** the turn with HTTP 400 before the model
   call, so a jailbreak attempt burns no tokens; `SYSTEM_CORE` + the Layer-3 guardrails
   remain the substantive defence.

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
the player's profile). She does **not** handle support — any support/complaint/account-block/
deposit-withdrawal/responsible-gaming/ask-for-a-human topic is routed **out** (to a manager on
an escalation entry, back to site support on a retention entry). This section IS the spec (the
old `RETENTION_BOT_SPEC.md`/`RETENTION_SETUP.md` files were removed); the operator's setup
checklist lives in the admin — the **Retention · Telegram → Setup guide** tab.

- **Transport vs. brain vs. AI turn are separated on purpose** so the transport can be lifted
  into its own service later: `telegram_transport.py` (HTTP to the Bot API + update parsing,
  holds no logic), `retention.py` (the orchestration: nonce exchange, subscription gate, entry
  menu, photo selection/gating, manager round-robin, progression), `chat_service.handle_retention_message`
  (the AI turn: build prompt → model → strip sentinels → persist).
- **Channel = the existing `consumer` column** (`'web'` → `'telegram'`), NOT a new `channel`; the
  mode is derived from it (telegram ⇒ retention). Support is never duplicated in Telegram.
  **Telegram chats are logged APART from support chats**: the support admin surfaces
  (`db.list_sessions`, `db.unresolved_by_topic` — the Conversations + Unresolved views) exclude
  `consumer='telegram'` entirely; the Telegram chats live in their own **Retention · Telegram →
  Conversations** tab (`GET /admin/retention/sessions` → `db.list_retention_sessions`, joined
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
  (`carry_context_turns` knob, default 6, 0 = off) and passes it to
  `prompts.build_retention_messages(previous_history=…)`, which renders a Layer-3
  `RETURNING PLAYER — PREVIOUS CONVERSATION (context only)` block (messages truncated to ~240
  chars, rough "N hours/days ago" recency): greet back warmly like someone she knows, never
  re-introduce, don't re-answer the old messages. It rides ONLY on the first turn (never as
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
  tuned after a live transcript read like a bot: emoji are RARE (at most one, in roughly one
  message out of 3-4, varied — a 😉 on every message is called out as a forbidden bot-tell;
  support Nika still uses none); replies default to 1-2 short sentences with varied length and
  rhythm (longer only when asked for a story/details); the "do you want X or Y?" two-option
  closer is explicitly banned as a template; the ENGAGEMENT directive forbids steering to
  games/bonuses in the OPENING turns of a chat (get to know the player's mood first) and
  demands concrete call-backs to what the player said earlier instead of generic lines; photo
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
  personalization + language directive + the **photo-candidate list** + a lighter retention guardrail.
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
  (route out; writes `admin_events('retention_handoff')`), `[[LANG:xx]]` (as everywhere). Strip
  helpers: `prompts.strip_photo_tag` / `strip_stage_up_tag` / `strip_handoff_tag`.
- **Media library + file_id cache**: `retention_photos` gates by `level_min` (VIP-tier ordinal) ×
  `stage` (explicitness). **Both values are bounded to the product's real ranges on EVERY write**
  — `stage` to 1..`max_stage`, `level_min` to 0..(last tier ordinal) — whether the value is
  AI-generated OR hand-entered/API-posted (`api.retention._clamp_photo_gate`, applied in
  `create_photo` + `update_photo`; the SPA Media pickers offer only in-range choices), so a
  photo can never gate outside what the delivery gate can serve (no stage 0/6, no tier past the
  ladder). The first send uploads the binary from the media dir (Railway Volume,
  `RETENTION_MEDIA_DIR`); Telegram returns a `file_id` cached on the row so later sends skip the
  re-upload/egress. **Upload is bulk-friendly** (`POST /admin/retention/photos` takes any number
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
  and one failed photo never kills the batch. The SPA Media tab adds checkbox selection +
  "Generate metadata" and client-side filters (search/stage/level/status).
  **Candidate selection is pre-model** (`retention.select_photo_candidates`):
  unseen, tier×stage-gated (current stage + 1 teaser, capped by the tier ceiling), bounded by the
  **daily cap** (hard, reactive included) and the **proactive cooldown** (bypassed when the player
  explicitly asks — `is_photo_request`). Empty candidate set ⇒ the model is told to keep chatting
  with text and not promise a photo. The model's reply text becomes the photo **caption**, grounded
  on the candidate descriptions it was shown (one call — no separate caption round-trip).
- **Progression is backend-decided** (`retention.maybe_advance_stage`): the model only hints;
  the actual `unlocked_stage` advance needs the engagement threshold (`stage_advance_msgs`) **and**
  the tier ceiling (`max_stage_by_tier`) **and** spacing (`stage_advance_min_hours`). VIP tier is
  mapped from the free-text `vip_level` via the ordered `vip_tiers` list. All knobs are in the
  **`retention` settings group** (`settings.retention()`, in `SETTING_KEYS` — per-product tunable).
- **Entry = deeplink + one-time nonce** (`retention_nonces`): the site posts a handshake to
  `POST /api/retention/deeplink` → `{nonce, deep_link}`; `/start <nonce>` redeems it (single-use,
  TTL-bounded), fixes the **`tg_user_id ↔ player_id` link** + a `_CONTEXT_FIELDS` profile snapshot
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
  buttons read at a glance.
  Two things mint that deeplink: (1) the **support-chat widget's escalation button** — when the
  product runs retention, every escalation hand-off routes the player INTO the bot on the
  **escalation entry** (`escalation=True` → the manager option in the menu), via
  `escalation.build_payload_for_session` (see the Escalation section). This is the PRIMARY path —
  the widget is the main channel. (2) the optional site buttons below (secondary integration).
- **Profile freshness degrades softly** — all three levels ship: snapshot + re-handshake;
  **lazy pull** (`retention.maybe_pull_profile`, gated by `profile_pull_ttl_sec`) — before a turn,
  if the snapshot is stale and the product has a `player_api_url` + encrypted key, GET the fresh
  profile and update the snapshot (best-effort: a failure leaves the snapshot untouched); and
  **push webhook** `POST /partner/{product_id}/player-update` (authorized with the product's
  handshake secret as the shared partner secret). Partial updates only. A product with no Player
  API just lives on the snapshot — the schema degrades, never breaks. Both pull and push now
  also accept the **casino activity timestamps** `last_login_at` / `last_played_at` /
  `last_deposit_at` (ISO-8601, parsed + validated in `db.update_retention_profile`; unparsable
  values are dropped) — the ping matrix keys on them.
- **PING MATRIX — proactive re-engagement (`retention_pings.py`)**: the one place the bot ever
  writes FIRST. Admin-managed `retention_rules` per product (Retention → Pings tab):
  `trigger_kind` (`bot_inactivity` / `casino_inactivity` / `no_deposit` — the casino triggers
  only fire when the partner feeds the activity timestamps above), `inactivity_days`, `action`
  (`message` | `photo`), free-text English `intent` for the model, `vip_tiers` filter,
  `cooldown_days` (per player per rule, tracked in the `retention_pings` ledger), `priority`.
  Every product ships with a **starter ladder of `bot_inactivity` rules** seeded from
  `starter_kb.STARTER_RETENTION_RULES` (see the starter-baseline paragraph) — additive + never
  overwriting, so the owner tunes/extends them freely.
  A worker loop (started in `main.py` lifespan, deploy switch `RETENTION_SCHEDULER_ENABLED`,
  interval `RETENTION_PING_INTERVAL_SEC`) sweeps under a Postgres **advisory lock** (multi-
  instance safe); each product opts in via the hot `retention.pings_enabled` setting.
  **Anti-annoyance is enforced by the worker, not trusted to rules**: per-player daily cap
  (`ping_daily_cap`) + minimum gap (`ping_min_gap_hours`) via `db.eligible_ping_users`, local
  **quiet hours** (`quiet_hours_start/end` + `quiet_hours_utc_offset`), batch bound
  (`ping_batch_size`), the `/stop` opt-out (`pings_muted`; `/resume` re-enables — model-free
  command handling in `_handle_message`), and the blocked-bot flag (`unreachable`, set on a
  Telegram 403 via `send_message_verbose`, cleared when the player writes again). The message is
  generated by the SAME retention prompt stack (`prompts.build_retention_ping_messages` — normal
  Layer 1 + KB + history, the Layer-3 PROACTIVE TASK block with idle days/reason/intent;
  `chat_service.generate_retention_ping` returns a `PingDraft` WITHOUT persisting) — the worker
  sends first and persists only a delivered message (`db.persist_ping_turn`, assistant-only
  atomic variant); an undelivered draft still gets its AI cost logged (invariant §4). Every
  attempt lands in the `retention_pings` ledger + a `retention_ping` admin event. Manual bounded
  test run: `POST /admin/retention/pings/run` (ignores quiet hours only). A photo-action rule
  bypasses the proactive photo cooldown (`select_photo_candidates(bypass_cooldown=True)`) but
  never the daily photo cap, and falls back to a text ping when no candidate is sendable.
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
  falls back to `rtn_photo_caption`. The retention-entry `[[HANDOFF]]` route-out now carries the
  per-language `contact_url` as an inline button when configured.
- **Managers** (`retention_managers`): round-robin, **sticky** (a returning player keeps their
  manager); the hand-off is a `t.me/<username>` link; only the fact is logged
  (`retention_manager_handoff`).
- **Per-product Telegram config** lives on the `products` row: `telegram_bot_token_enc` /
  `player_api_key_enc` (secretbox-encrypted, like the OpenAI keys — `has_*` flags only out),
  `telegram_bot_username`, `telegram_webhook_secret` (non-secret webhook routing token, the
  Telegram analogue of `widget_key` — resolves an update to its product), `telegram_channel_id`,
  `telegram_channel_url`, `player_api_url`, `retention_enabled`. Webhook auth is two-layer: the
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
- **Admin**: the SPA **Retention · Telegram** view (sub-tabs: Setup guide — the static
  "how to connect the bot" checklist that replaced `RETENTION_SETUP.md` —, Telegram config,
  Retention KB — the one-document text editor —, **Prompt variables** — the Telegram-persona
  editor (`GET/PUT /admin/retention/prompt-variables`; empty = the retention default — a
  SEPARATE prompt, no support inheritance, see "Prompt variables") —, **Prompt preview**,
  Media — bulk upload + AI metadata + filters —,
  Managers, **Pings** — the ping-matrix rules editor + ledger + run-now —,
  **Conversations** — the Telegram chat list + transcript dialog, see the lifecycle bullet
  above —, Analytics);
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
  minimal CRUD over `admin_users` (email + password + role `admin`/`manager`). No email delivery,
  no reset flows — an admin sets passwords directly. A user can't demote/deactivate/delete
  **itself** (self-lockout guard). With no owner recovery path, **keep at least two `admin`
  accounts** so a forgotten password can't lock everyone out. The password hash never leaves
  `db.py` (`_row_to_admin_user` drops it).
- **Settings** (`settings.py`, `app_settings` table): hot-reloaded runtime tuning with
  precedence `app_settings` (DB) → env → default. A sync in-process cache (populated at
  startup, reloaded on write) is read by `antispam`/`escalation`/`openai_client`/`language`/
  `auth`/api; writes validate hard and log `setting_updated`. Groups: `escalation`
  (`high_risk_keywords`, `human_request_keywords` — content tuning, so its ONLY editor is the
  Prompt → Prompt variables sub-tab; the Settings tab skips this group to avoid a duplicate
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
  `antispam` (rate limit/window/cooldown/input cap **plus** `recaptcha_min_score`,
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
  handshake/reCaptcha secrets) — plus the network-perimeter deploy
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
  `POST /admin/kb/topics` upsert). See "Translations" above. The admin panel itself stays English.
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
  `app_settings['test_profile']`, `api.admin` `GET/PUT /admin/test-profile`, the **Test
  player** block in the Prompt → Prompt variables sub-tab — the old top-level Test sandbox
  tab was folded in there, since the profile exists to test the prompt's personalization):
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
  scope banner (global defaults vs the selected product). **Topic titles are
  single-sourced** in Translations → Topic names; the KB form keeps only the
  canonical English title (the prompt is English-only) and links there. **SET-state
  is explicit**: `components/SetBadge` shows a green check for configured secrets
  and `components/SecretField` adds a Clear button so an operator can save an empty
  value (fall back to env) — used in Structure + Retention config; the test-profile
  handshake notice links to Structure to clear the product's handshake secret.

§16 decisions: unresolved analysis = topic-grouped (no embeddings); contact form =
host-site button only; admin auth = named `admin_users` accounts only (email + password,
role-driven; no password-only owner login).

### Multi-tenancy rules of thumb (see the "MULTI-TENANCY" section at the top)
The tenanting is BUILT — partners → products, membership authorization, per-product
settings/secrets/KB/copy, the header switcher. When extending, keep these rules:
- **Everything brand/product-specific lives in the product-scoped stores**:
  `prompt_variables`, `retention_prompt_variables` (the Telegram persona;
  a SEPARATE prompt with its own defaults, no support inheritance), `translations` (incl. the per-language
  `contact_url`), the KB (topics + texts + `kb_variables`) — all keyed by
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
