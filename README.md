# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone FastAPI microservice serving an AI customer-support chat for **NikaBet**
(casino + sportsbook on the NowPlix B2B platform). It is API-isolated: other modules
talk to it over HTTP/JSON by `session_id` (UUID), and the contract is consumer-agnostic
so multiple front-ends can plug in. **Phase 1 and Phase 2 are both implemented** — the
admin dashboard, hot-reloaded tuning, system-prompt versioning + A/B, KB CRUD/import,
Telegram escalation, and the signed front-end handshake are all built (see "Phase 2"
below).

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

## Architecture — the big picture

### 3-layer prefix-cache-optimised prompt (the central design)
`prompts.py` assembles every request in three layers so the OpenAI prefix cache stays warm:
- **Layer 1 `SYSTEM_CORE`** — a byte-stable Russian system prompt. It is **never** edited
  to add behaviour; it must be byte-identical across requests (a test enforces this).
- **Layer 2** — the KB block for the selected topic, appended to the system message after
  a fixed separator. Changes only when the topic changes (an accepted cache break).
- **Layer 3** — *all* dynamic data (sanitized `user_context`, the resolved language
  directive, conversation history, the new user turn) lives in the **user message**, never
  in the system message. This is what keeps the cached prefix stable.

New rules go into the KB or Layer 3 — **never** into `SYSTEM_CORE`. The source prompt and
KB are Russian; only the answer language varies. The Layer-3 directive tells the model to
**answer in the language of the player's current message** (falling back to the session's
base language when it's too short/ambiguous) — so the answers follow the player if they
switch language mid-chat, while the widget chrome stays fixed to the browser language (see
"Language resolution" below).

**Personalization** also lives in Layer 3 (never `SYSTEM_CORE`): when the sanitized
`user_context` carries a `full_name`, `prompts._personalization_directive` adds a line giving
the model the player's **first name** and telling it to use the name sparingly (not on every
line). No name ⇒ the line is omitted and the prompt is unchanged. The whitelisted context
fields the model ever sees are `prompts._CONTEXT_FIELDS` (`id, full_name, email,
activation_status, country, balance, vip_level, registration_date`) — anything else in
`user_context` is dropped, so adding a model-visible field is a deliberate edit to that list.

**Greeting hygiene** is a separate always-present Layer-3 line (`prompts._GREETING_DIRECTIVE`,
included with or without a name): models otherwise open *every* reply with "Привет, <имя>!" /
"Здравствуйте!", which reads robotic in a running chat. Since the conversation history is in the
prompt, the model can tell whether the chat has already started; the directive tells it to greet
exactly once — in the first reply — and otherwise skip the greeting (and the leading name) and go
straight to the answer. The *when* to greet lives here; `_personalization_directive` only supplies
the name and the "use it sparingly" rule.

**Formatting hygiene** is another always-present Layer-3 line (`prompts._FORMATTING_DIRECTIVE`):
the model reaches for Markdown on its own (`**bold**`, lists, links), and the widget now renders a
small fixed subset of it (`widget.js` `renderMarkdown` — see "Conventions"). Left unguided the model
also emits markup the widget can't render (tables, fenced code blocks, raw HTML), which leaks to the
player as literal characters. This directive pins the model to exactly the rendered subset — bold,
italic, inline `code`, links, and bulleted/numbered lists — and tells it to avoid the rest, so the
two stay in lockstep: whatever the model emits, the widget renders. Lives in Layer 3 so `SYSTEM_CORE`
stays byte-stable.

**KB grounding** is a Layer-3 line (`prompts._KB_GROUNDING_DIRECTIVE`) added for every **specialized**
topic (skipped for the catch-all `other`, which has no KB and whose routing directive already steers
the model to a specialized branch). The KB block (Layer 2) is the single source of truth, but the model
tends to miss a matching entry when the player phrases the question differently from how the KB is
written, then falls back to vague generic prose or invented specifics instead of the exact answer that
IS in the KB (e.g. a player asks about a specific bonus under «Бонусы» worded unlike the KB's example
questions, and gets generic/made-up info though the precise entry exists). The directive tells the
model to match the question to the KB by **meaning/intent**, not literal wording; answer strictly and
precisely from the matched entry; never substitute generic or invented conditions/numbers/dates/names
when concrete ones exist; answer generically only when the question really is generic and the KB has
nothing; and ask one short **clarifying question** to steer the player toward a specific KB answer when
the question is too vague or spans several entries. Lives in Layer 3 so `SYSTEM_CORE` stays byte-stable.

**Escalation restraint** is an always-present Layer-3 line (`prompts._ESCALATION_RESTRAINT_DIRECTIVE`)
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
(try hard to find the answer → don't give up too early). Lives in Layer 3 so `SYSTEM_CORE` stays
byte-stable; the `escalation.decide()` triggers (keyword/cap/`[[ESCALATE]]`) are unchanged — this only
makes the model emit the sentinel more deliberately.

### Request flow
`api/chat.py` (thin HTTP handlers + gate ordering) → `chat_service.handle_message`
(orchestration) → `prompts.build_messages` + `openai_client.complete` → `db.persist_turn`.
`chat_service` keeps handlers thin: it resolves language, builds the prompt, calls the
model with failover, strips the `[[ESCALATE]]` sentinel, decides escalation, computes cost,
and persists the turn — all in one place.

### Data layer — no ORM, no migrations (`db.py`)
The schema *is* the code in `db.init_db()` (run on startup via `main.py` lifespan, then
`seed/kb_seed.run()`). To change schema, edit the `_SCHEMA` string. **A new column on an
existing table will NOT be applied by `CREATE TABLE IF NOT EXISTS`** — add an idempotent
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` to `_ensure_columns()` (the seam is already
there, currently empty). Every table read/write goes through a `db.<name>(...)` async
helper; nothing else touches tables directly.

### Seeds are non-destructive bootstrap (`seed/*.run()`, the seed contract)
On startup `main.py` runs `kb_seed.run()` → `prompt_seed.run()` → `settings_seed.run()`
**on every boot** (the DB persists across redeploys; the seeds re-run each time). The DB —
not the code — is the source of truth once the owner edits anything in the admin panel, so
**every seed MUST be seed-once / non-destructive: create a row only when it is missing, and
never overwrite an existing one.** `prompt_seed` (skips if a default prompt version exists)
and `settings_seed` (fills only missing setting fields) already follow this; `kb_seed` now
does too — it creates a built-in topic + its placeholder KB only when absent, and leaves an
existing topic's `title/order/active` and KB untouched (a test enforces this). This is what
keeps a redeploy from wiping admin edits. Earlier `kb_seed` clobbered the live KB on **every**
restart (it deactivated all entries and re-inserted the placeholder at version 1), silently
reverting the owner's changes back to placeholders — never reintroduce a seed that mutates
existing data. (The old destructive `db.replace_topic_entry` helper was removed for the same
reason; the seed creates fresh entries via `db.create_kb_entry`.) Deleting the `seed/` package
is NOT a fix: it's the bootstrap for a fresh/empty DB (new env, DB recreation, local dev), and
without it such a DB has no topics, KB, default prompt, or settings.

### Atomic turn write (invariant)
`db.persist_turn` writes the user message, the assistant message, the `ai_interaction_logs`
row, and the `chat_sessions.message_count` bump in **one transaction**. Do not split it.
When adding per-turn columns (e.g. Phase 2 `prompt_version_id`), join them into this same
transaction.

### Two-key OpenAI failover (`openai_client.py`)
Primary key first; if it stays silent for `OPENAI_KEY_SWITCH_TIMEOUT_SEC`, the fallback is
launched **in parallel** and whichever responds first wins (loser cancelled). A hard error
(auth/quota/not-found) fails over immediately; transient errors (429/timeout) retry with
exponential backoff up to `OPENAI_MAX_ATTEMPTS`. Every fallback engagement fires an
`on_failover` callback → `admin_events('key_failover')`. Cost is computed from token usage
via `_PRICING` (marked "verify before trusting" — prices may be stale; unknown models cost 0).

The default model is the **GPT-5.4 mini reasoning family** (`gpt-5.4-mini`). Reasoning models
change the request shape: the call sends `max_completion_tokens` (**not** `max_tokens`), does
**not** send `temperature` (rejected by these models), and instead passes `reasoning_effort`
and `verbosity` (each `low`/`medium`/`high`). Both are sent only when set — an empty string in
the `model` group **omits** the parameter so the model's own default applies (and so the owner
can drop a knob a future model rejects without a redeploy). The `max_output_tokens` budget
counts reasoning tokens (billed as output), so it ships higher (2000) than a non-reasoning
model would need — too low and the visible answer can return empty.

The tuning knobs (model name, reasoning effort, verbosity, max output tokens, request timeout,
key-switch timeout, max attempts, per-key concurrency) are NOT read from env directly — they
come from the hot-reloaded `model` settings group (`settings.model()`, precedence
`app_settings` → env → default). Model/reasoning-effort/verbosity/max-tokens/switch-timeout/
attempts are read **live per call**; `request_timeout_sec` and `max_concurrent_per_key` are
bound when the client is built, so a `model` write also calls `openai_client.reset()` to
rebuild the singleton (no effect on the OpenAI-side prefix cache). API keys themselves stay
secrets in env. `seed/settings_seed.py` also runs a one-time migration that flips a stored
legacy `gpt-4`/`gpt-3` `model` override to the new default and drops the dead `temperature`
key, so an existing deployment moves to GPT-5.4 mini on boot without a manual settings edit.

### Anti-spam gate order (`antispam.py`, enforced in `api/chat.py`)
`POST /api/chat/message` checks in this exact order: verify session token (401) → IP
rate-limit (429 + log) → cooldown (429) → input length (400) → **low-content guard** →
injection scan (always audits; **hard-blocks with 400 by default**, settings-gated via
`injection_hard_block`) → message-cap fast path (forces an escalation
response with no model call) → build/call/persist. Rate-limit and cooldown use **in-memory
dicts** — fine for Phase 1 but they do not span multiple instances. reCaptcha is verified at
session create and skips gracefully (logged) when `RECAPTCHA_SECRET` is unset.

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

> The `chat_sessions.lang_locked` column is dead (kept only to avoid a schema migration);
> it is no longer read or written by the chat flow.

### Escalation (`escalation.py`)
Phase 1 returns a contact-button payload only (no form, no live agent). `decide()` triggers
on: high-risk keywords (fraud/legal), explicit human requests, message cap, or the model's
`[[ESCALATE]]` sentinel. On escalation, `chat_sessions.status='escalated'` and an
`admin_events('escalation')` row is written. The button URL is `CONTACT_FORM_URL`.

### Topic routing (`[[TOPIC:slug]]` sentinel)
Only the selected topic's KB is loaded (Layer 2), so a question that belongs to a *different*
topic can't be answered well. To bridge this, Layer 3 lists the other topics (`kb.suggestable_topics`,
current topic + hidden `other` excluded) and instructs the model to prepend `[[TOPIC:slug]]` on its
own first line when the question plainly belongs to one of them. `chat_service` strips the tag
(`prompts.strip_topic_suggestion`, mirrors the `[[ESCALATE]]` strip), validates the slug against the
offered list, and returns `suggested_topic:{slug,title}` in the `/message` response. The widget shows
a soft one-tap "switch topic" prompt that calls `POST /api/chat/topic` and **auto-resends** the
player's original question against the new KB. The topic list is dynamic data → Layer 3 only; it must
never enter `SYSTEM_CORE` (a test asserts the cached prefix stays byte-stable).

**Two routing regimes (`prompts._topic_routing_directive`).** The directive's instruction flips on
whether the current topic is the hidden catch-all `other` (`prompts.OTHER_TOPIC_SLUG`, mirrors
`kb.OTHER_SLUG`):
- **A specialized topic is current** — the model is *anchored* on it: it answers in-topic questions
  from the loaded KB (or escalates) and switches **only** on a genuine mismatch. The decision keys on
  the player's **intent**, not isolated keyword overlap — so "how do I withdraw?" asked under Deposits
  routes to Withdrawals, while a shared term (crypto networks, verification, limits) that also fits the
  current topic does **not** trigger a switch. This keeps cross-topic tracking active without ping-pong.
- **The catch-all `other` is current** — it has no real KB, so the directive *reverses* the default and
  routes **actively**: almost any concrete question really belongs to a specialized topic, so if the
  question plausibly fits one of the listed topics the model suggests the switch instead of answering
  from the thin generic block (and is told not to invent conditions/bonuses/dates/numbers). It answers
  in place only when nothing fits (a generic question, feedback, a one-off), and escalates complaints /
  suspected fraud. This fixes the case where a bonus question asked under «Другое» got a made-up
  in-place answer instead of a one-tap switch to Bonuses.

**Switch boundary (anti-ping-pong):** `set_session_topic` snapshots the current max `chat_messages.id`
into `chat_sessions.context_reset_id`, and prompt-building history (`db.get_history(..., after_id=...)`
in `chat_service`) only feeds the model turns newer than that boundary. Without it, switching topics
re-sent the *whole* prior transcript; the model saw the old topic's conversation (now re-listed as a
suggestable topic) and kept suggesting switching back — an endless loop. After a switch the first turn
carries only the triggering message, so the new topic is the only thing in context. The **full**
transcript is untouched — resume (`GET /session/{id}`) and the escalation ticket snapshot both call
`get_history` without `after_id`, so the player and admins still see everything.

### Suggested follow-up questions + finish-chat (`[[SUGGEST:…]]` / `[[RESOLVED]]` sentinels)
To pull the player toward the exact KB entry their question is closest to, the model emits — along
with its answer — two more Layer-3 sentinels (mirroring the `[[TOPIC:slug]]` machinery), both
**stripped** before the reply is shown:
- **`[[SUGGEST: q1 | q2 | q3]]`** (own LAST line) — up to **three** short follow-up/clarifying
  questions phrased **from the player's point of view** (first person), pipe-separated. The directive
  (`prompts._SUGGESTIONS_DIRECTIVE`) tells the model to pick the *next logical questions whose answers
  ARE in the KB*, so tapping one walks the player onto a concrete KB answer. `chat_service`
  (`prompts.strip_suggestions`) parses them into a list (trimmed, blanks dropped, capped at
  `prompts._MAX_SUGGESTIONS` = 3) and returns `suggestions:[…]` in the `/message` response. The widget
  renders them as one-tap **bubbles by the input field** (one per line); tapping one sends it as the
  next player turn (`submitText`), and the stale bubbles are cleared the moment a new turn starts.
- **`[[RESOLVED]]`** (own line) — set once the question looks fully resolved (the player confirmed /
  thanked / said it's closed). `chat_service` (`prompts.strip_resolved_tag`) returns `resolved:true`
  and the widget surfaces a green **"finish chat"** button below the bubbles. Tapping it calls
  **`POST /api/chat/resolve`** (`db.mark_resolved` → `status='resolved'` + an `admin_events('session_resolved')`
  row) and collapses the panel — gently steering the satisfied player toward ending the chat, and
  dropping the session out of the open-session metric. The close never overrides an **escalated**
  session (a pending hand-off to a human must survive the player tapping finish), and the call is
  best-effort (the panel collapses regardless). The directive (`prompts._RESOLVED_DIRECTIVE`) tells
  the model NOT to set the tag while still clarifying.

On a hand-off both are suppressed in `chat_service` (the player is going to a human, so the
guide-to-KB bubbles and the close nudge are out of place). Both directives live in Layer 3 only, so
`SYSTEM_CORE` stays byte-stable (a test asserts it). The model-free paths (message-cap, low-content)
return neither, so the widget simply shows no bubbles/finish button there.

### Two layers of injection defense
1. `prompts._sanitize_field` zeroes any `user_context` field containing injection markers
   (only `id, full_name, email, activation_status` are surfaced to the model).
2. `antispam.scan_injection` scans the user message (normalized first, so spacing /
   zero-width / Unicode-confusable obfuscation can't hide a known trigger) and **logs**
   `injection_blocked`. With `injection_hard_block` (now **on by default**, tunable in the
   `antispam` settings group) it also **rejects** the turn with HTTP 400 before the model
   call, so a jailbreak attempt burns no tokens; `SYSTEM_CORE` + the Layer-3 guardrails
   remain the substantive defence.

### Off-topic / forbidden-topics guardrail (`forbidden_topics` setting)
A Layer-3 line (`prompts._forbidden_topics_directive`) injects the owner-configured
`forbidden_topics` list + custom refusal wording into the user message, so the model
refuses off-topic and unsafe asks (programming, essays, politics, medical/legal/financial
advice, competitors, "guaranteed-win"/cheat schemes, general knowledge, etc.) on top of the
always-on `_GUARDRAILS` topic restriction. It ships with a **non-empty default set** (so
off-topic blocking works out of the box and the admin panel isn't empty) and is editable in
the `forbidden_topics` settings group; an explicit empty list disables it. The refusal is a
template the model localizes to the player's language. Lives in Layer 3 only, so
`SYSTEM_CORE` stays byte-stable (a test asserts it).

## Invariants (these break silently — do not violate)

1. `SYSTEM_CORE` is byte-stable; dynamic data lives only in the user message.
2. KB is injected per topic from Postgres — never baked into the core.
3. Persisting a turn is one atomic transaction (messages + counters + AI log).
4. Every message → `chat_messages`; every OpenAI call → `ai_interaction_logs`; every state
   transition (escalation, failover, rate-limit, injection) → `admin_events`.
5. Two-key failover races the fallback after the switch timeout; log every failover.
6. No ORM, no migrations: schema is `init_db()`; new columns via guarded `ALTER`; all DB
   access through `db.*` helpers.
7. Source prompt + KB in Russian; answers in the resolved language.
8. Never request card numbers / CVV / passwords / 2FA codes / seed phrases; never invent
   player-facing facts — KB uses `{{PLACEHOLDER}}` tokens the owner replaces.
9. `_PRICING` is "verify before trusting"; cost is derived, not ground truth.

## Phase 2 (implemented)

Built on the same stack, extending — not rebuilding — Phase 1. Map of what lives where:

- **Admin auth** (`api/admin_auth.py`, `auth.py`): `POST /admin/login` (constant-time
  password compare against `ADMIN_PASSWORD`, rate-limited, failures → `admin_login_failed`)
  issues an admin JWT signed with `ADMIN_JWT_SECRET` and carrying a `role` claim. The
  `require_admin` dependency guards every `/admin/*` data route.
- **Settings** (`settings.py`, `app_settings` table): hot-reloaded runtime tuning with
  precedence `app_settings` (DB) → env → default. A sync in-process cache (populated at
  startup, reloaded on write) is read by `antispam`/`escalation`/`openai_client`/`language`/
  `auth`/api; writes validate hard and log `setting_updated`. Groups: `escalation`
  (incl. `max_messages_per_session`), `forbidden_topics` (off-topic/unsafe-request list +
  custom refusal, enforced in Layer 3 — see that section; ships with non-empty defaults),
  `language` (default + supported
  set — every language read goes through `language.default_code()`/`supported_codes()`),
  `antispam` (rate limit/window/cooldown/input cap **plus** `recaptcha_min_score`,
  `injection_hard_block`, and the low-content guard `low_content_block` /
  `min_meaningful_chars`), `model` (OpenAI tuning — see the failover section), and `general`
  (operational knobs with no other home: `session_ttl_hours`, `contact_form_url`,
  `body_max_bytes`). The goal is that every non-secret operational knob lives in the admin
  panel and only true secrets (API keys, JWT secrets, `DATABASE_URL`, `ADMIN_PASSWORD`,
  Telegram/handshake/reCaptcha secrets) — plus the network-perimeter deploy vars
  (`CORS_ALLOW_ORIGINS`, `TRUSTED_PROXY_COUNT`) — stay in Railway env. On startup
  `seed/settings_seed.run()` snapshots the current env-resolved values for the `antispam`,
  `model`, `general`, `language`, `escalation` (max-messages) and `forbidden_topics`
  (shipped defaults) groups into `app_settings`
  once (missing fields only; never clobbers an existing override) so the matching env vars
  can be deleted from Railway with no behaviour change.
- **Dashboard data API** (`api/admin.py` + `db.py` aggregation + `metrics.py` derived
  rates): overview/timeseries/by-topic/by-language/sessions/session/unresolved/ab-results.
  `resolution_rate` is a documented PROXY (counts "not escalated", incl. abandoned →
  `sessions_open` tracked separately).
- **Prompt versioning + A/B** (`prompt_store.py`, `prompt_versions` table,
  `chat_sessions.prompt_version_id`): the live core loads from the DB (cached per version
  → still byte-stable within a version, prefix cache unchanged). Editing makes a *draft*;
  publishing swaps `is_default` (deliberate one-time cache reset; `prompt_store.invalidate()`).
  Assignment at session create is a deterministic weighted hash of the session id.
- **Structured system-prompt editor** (`prompts.SYSTEM_PROMPT_SECTIONS`/`compose_core`,
  `settings.system_prompt`, `api.admin` `GET/PUT /admin/system-prompt`, Settings tab in the
  SPA): Layer 1 is split into named, individually-editable sections — tone of voice (intro),
  absolute rules, escalation rules, injection defense, language, style — so the owner tunes
  the core from **Settings** without hand-editing one blob. Composing the shipped defaults
  reproduces `SYSTEM_CORE` byte-for-byte (a test asserts this), so the cached prefix is
  untouched until a section is deliberately edited. The sections are stored in
  `app_settings['system_prompt']` (the editor's source of truth); **saving composes the core
  and publishes it live** as a new default `prompt_versions` row (apply-live = one deliberate
  cache reset), reusing the version machinery so A/B attribution + audit stay intact. Layer 2
  (KB) stays in the Knowledge-base tab; Layer 3 (player data) is per-request and not editable.
- **KB CRUD + import** (`kb_import.py`, `db.*` helpers): versioned entries (edit = new
  version row, delete = soft `active=false`); JSON/CSV/Markdown bulk import.
- **Escalation Phase 2** (`escalation.open_ticket`, `notifiers/telegram.py`,
  `escalation_tickets` table): snapshots the transcript + context into a ticket, notifies
  Telegram if configured, and ALWAYS returns the Phase 1 contact button (user never
  stranded; delivery failure → `telegram_notify_failed`).
- **Signed handshake** (`auth.sign_handshake`/`verify_handshake`, `api/chat.create_session`):
  with `WIDGET_HANDSHAKE_SECRET` set, only a valid signed blob is trusted for
  `user_context`; raw browser context is ignored. No secret ⇒ Phase 1 dev behaviour. The
  injection sanitizer runs in every mode.
- **Test sandbox profile** (`settings.test_profile`/`validate_test_profile`,
  `app_settings['test_profile']`, `api.admin` `GET/PUT /admin/test-profile`, the **Test
  sandbox** tab in the SPA): in test/dev (**no** `WIDGET_HANDSHAKE_SECRET`) there is no host
  site to sign a handshake, so this stored profile stands in for it at `create_session`. It
  drives the Layer-3 player data the model sees (`id, full_name, email, activation_status,
  country, balance, vip_level, registration_date` — the `prompts._CONTEXT_FIELDS` whitelist) so
  the owner can test name personalization. There are **no** language knobs — the session
  language always follows the browser. `enabled=false` ⇒ fall back to the widget's built-in
  context. The profile is **ignored** when a handshake secret is set (the host site is
  authoritative then). This is the single seam for "manage the test player on test, the real
  site supplies it later".
- **Admin SPA** (`frontend/admin/`, `npadmin-` prefix, hand-rolled inline SVG charts, no
  build step, no CDN): served at `/admin`, assets under `/admin-static`.

§16 decisions: unresolved analysis = topic-grouped (no embeddings); contact form =
host-site button only; admin auth = single owner (token shaped for future multi-admin).

## Conventions

- Stdlib-only JWT (`auth.py`) — HS256 via `hmac`/`hashlib`/`base64`, no PyJWT.
- Front-end is vanilla ES modules with **no build step**; widget classes are prefixed
  `npchat-` to avoid host-page collisions. Phase 2 admin SPA should use `npadmin-`.
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
- Develop on branch `claude/pensive-tesla-opotpt`; do not push to other branches.
- Env var reference lives in `README.md` (§ "Environment variables").
- **`CLAUDE.md` is the single source of truth for docs; `README.md` is a generated
  mirror of it** (kept byte-identical) and the root test page (`main.py` `/`) renders
  `CLAUDE.md` live on every request. Never edit `README.md` by hand — edit `CLAUDE.md`,
  and the sync happens automatically via the `.githooks/pre-commit` hook (enable once
  per clone: `git config core.hooksPath .githooks`). A `docs-sync` GitHub Action fails
  the build if the two ever drift; `sh scripts/sync_readme.sh` re-syncs manually.

> **MANDATORY DOCS-SYNC RULE (Claude must always follow this).** `CLAUDE.md` is the
> single source of truth; `README.md` is a byte-identical generated mirror. **Whenever you
> change `CLAUDE.md`, you MUST regenerate `README.md` in the SAME change** by running
> `sh scripts/sync_readme.sh` (or copying `CLAUDE.md` over `README.md`) and staging both
> files before committing. Never edit `README.md` by hand. The root test page (`main.py`
> `/`) already renders `CLAUDE.md` live, and the `docs-sync` GitHub Action fails the build
> if the two ever drift — so the human does not need to enable any git hook for this to work.
