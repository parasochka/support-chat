# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone FastAPI microservice serving an AI customer-support chat for **NikaBet**
(casino + sportsbook on the NowPlix B2B platform). It is API-isolated: other modules
talk to it over HTTP/JSON by `session_id` (UUID), and the contract is consumer-agnostic
so multiple front-ends can plug in. The admin dashboard, hot-reloaded tuning, KB editing,
and the signed front-end handshake are all built (see "Admin / management" below).
Escalation is a contact-button hand-off (no in-app form, no live agent).

**The prompt lives in one place: the file `prompts.py` (the single source of truth).**
The Layer-1 core (`SYSTEM_CORE` — Nika's tone-of-voice + the absolute/escalation/
responsible-gaming/links rules), every behavioural directive (greeting, formatting, KB-grounding,
escalation restraint, suggestions, finish-chat, lead-forward — STATIC, in Layer 1; language,
personalization, topic-routing — DYNAMIC, in Layer 3), and the forbidden-topics list are
constants in that file. The prompt is **not** editable from the admin panel — to change
it you edit `prompts.py` and redeploy. The admin **Prompt** tab is a **read-only** view of
the whole assembled prompt (all layers, as sent) so you can always see exactly how it's
formed. (The per-topic knowledge base — Layer 2 — is the one prompt input that stays
editable in the admin, since it's answer content, not instructions.) There is no
system-prompt versioning, no A/B split, and no admin prompt editor — those were removed in
favour of this single source of truth.

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
low-content nudge, widget chrome) and the user-input detectors (injection / escalation keyword
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
language**. The tone rides in the byte-stable core, so it is cached and consistent. To change
the voice you edit `SYSTEM_CORE` and redeploy.

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
models otherwise open *every* reply with "Привет, <имя>!" / "Здравствуйте!", which reads robotic
in a running chat. Since the conversation history is in the prompt, the model can tell whether the
chat has already started; the directive tells it to greet exactly once — in the first reply — and
otherwise skip the greeting (and the leading name) and go straight to the answer. The *when* to
greet lives here; `_personalization_directive` (Layer 3) only supplies the name and the "use it
sparingly" rule.

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
`escalation.decide()` triggers (keyword/cap/`[[ESCALATE]]`) are unchanged — this only makes the model
emit the sentinel more deliberately.

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
so it never overwrites an admin-edited value — boot only fills keys that don't exist yet.

### KB variables — `{placeholder}` registry (`db.py` + `kb.render_variables`)
KB texts may contain `{key}` placeholders (e.g. `{min_deposit}`). The `kb_variables` table holds
one admin-managed `value` (+ description) per key. `kb.kb_block_for_topic` runs
`kb.render_variables` over the topic's KB before it enters Layer 2, substituting each `{key}` with
its registry value (unknown placeholders are left **as-is** so missing entries are visible in the
prompt preview). The admin **Variables** tab (`GET/PUT /admin/kb/variables`) lists + edits them.
NB: `list_kb_variables`/`set_kb_variable` must return `updated_at` as an **isoformat string**
(via `db._row_to_kb_variable`) — a raw `datetime` cannot be serialized by `JSONResponse` and 500s
the tab (the bug that shipped with the feature).

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
call). `chat_service` keeps a backstop: if the reply is still empty and it's neither an escalation
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
fast path (forces an escalation response with no model call) → build/call/persist. Rate-limit
and cooldown use **in-memory dicts** — fine for Phase 1 but they do not span multiple instances.
reCaptcha is verified at session create and skips gracefully (logged) when `RECAPTCHA_SECRET`
is unset.

The **IP key** comes from `api/client_ip.py` `client_ip()`, which trusts `X-Forwarded-For` **only**
when the immediate socket peer (the TCP source, which a public client cannot forge) is in
`config.TRUSTED_PROXY_IPS`; then it reads `TRUSTED_PROXY_COUNT` hops from the RIGHT. The default
trust list is the **private/reserved ranges** (RFC1918 + CGNAT + loopback/ULA), so on Railway/most
PaaS the real client IP resolves correctly out of the box without trusting spoofable XFF — an
attacker on the public internet has a public peer IP and is never trusted. This is a
**network-perimeter deploy var → Railway env, not the admin panel** (like `CORS_ALLOW_ORIGINS` /
`TRUSTED_PROXY_COUNT`): a compromised admin must not be able to disable spoofing protection.

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

### Escalation (`escalation.py`)
Escalation returns a contact-button payload only (no form, no live agent, no ticket/Telegram
notifier). `decide()` triggers on: high-risk keywords (fraud/legal), explicit human requests,
message cap, or the model's `[[ESCALATE]]` sentinel — checked in that order. The two keyword
scans run on a normalized copy of the message (NFKC + zero-width strip, via
`antispam._normalize_for_scan`) so obfuscation can't hide a trigger; the keyword lists are
stems (`поддержк`, `мошенн`, …) so inflected forms still match. Both lists are tunable live from
the admin `escalation` settings group — `high_risk_keywords` and `human_request_keywords`; the
constants in `escalation.py` (`_HIGHRISK_KEYWORDS` / `_HUMAN_KEYWORDS`) are only the built-in
defaults used until the owner overrides them. The cap fires on the turn whose
prospective count (current + 1) reaches `max_messages_per_session`; the model-free fast path in
`api/chat.py` is the cheap belt-and-suspenders for a session already at/over the cap (e.g. after
the owner lowers it mid-session) — complementary, not a duplicate. (There is **no**
`unresolved_turns_before_escalate` knob: the catch-all `other` is routed like every other topic,
so it escalates on the same triggers — the old per-`other` turn threshold was removed with that
redesign.) On escalation, `chat_sessions.status=
'escalated'` and an `admin_events('escalation')` row is written, and `build_payload(lang)`
returns the localized message + contact button. The button URL is `CONTACT_FORM_URL`
(via the `general` settings group).

**A hand-off ends the bot conversation.** Once a session is `escalated` (or `resolved`),
`api/chat.py` `_ensure_open_session` rejects further mutating turns (`/message`, `/topic`,
`/escalate`) with **HTTP 409 `session_closed`** — only an `open` session is chatable. The
widget mirrors this: on an `escalation.active` turn it shows the contact card and then calls
`endConversation()` (hides the composer, drops the local session credentials), so the player
can only chat again by starting a **new** conversation. (This means the old `already_escalated`
branch in `decide()` is now reachable only on the resume edge, not via fresh `/message` calls.)

### Topic routing (`[[TOPIC:slug]]` sentinel)
Only the selected topic's KB is loaded (Layer 2), so a question that belongs to a *different*
topic can't be answered well. To bridge this, Layer 3 lists the other topics (`kb.suggestable_topics`,
current topic + hidden `other` excluded) and instructs the model to prepend `[[TOPIC:slug]]` on its
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
player across topics forever. (This replaced the earlier flow where the wrong-KB answer + a one-tap
"switch topic" button were both shown and the player had to tap to proceed.) Net token cost is unchanged
vs. that flow — still one detect call + one grounded answer call — but no ungrounded text is ever shown.

**One routing regime for every topic (`prompts._topic_routing_directive`).** Every topic — the six
specialized ones **and** the general `other` topic — is routed the same way: the model is *anchored* on
the current topic, answers in-topic questions from the loaded KB (or escalates), and switches **only**
on a genuine mismatch. The decision keys on the player's **intent**, not isolated keyword overlap — so
"how do I withdraw?" asked under Deposits routes to Withdrawals, while a shared term (crypto networks,
verification, limits) that also fits the current topic does **not** trigger a switch. This keeps
cross-topic tracking active without ping-pong.

`other` is **not special**. It is a normal, player-selectable topic (the always-available escape hatch
in the picker — added client-side in `widget.js`, since `db.list_topics` filters its slug out of the
dynamic catalogue) with its **own** ~50-entry KB, so it answers from that KB exactly like the others. In
practice it sends players onward to a specialized topic more often (it is the general entry point), but
that falls out of the same intent test, not a separate "route actively / don't answer from your own KB"
mode. An earlier design treated `other` as a thin KB-less catch-all and force-routed everything off it —
that **reversed** the anchor and broke any question whose answer actually lived in the `other` KB (e.g.
"how do I change the language?" was force-routed to Technical, which had no such entry, dead-ending the
chat). That special branch was removed. (`other` is still excluded from `suggestable_topics`, so it is
never offered as a switch *target* — the model can route *out of* it but not dump a player *into* it.)

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
- **`[[SUGGEST: q1 | q2 | q3]]`** (own LAST line) — **three** options phrased **from the player's
  point of view** (first person), pipe-separated. The first **two** are short follow-up/clarifying
  *questions* whose answers ARE in the KB; the **third is a declarative closing/resolution option**
  ("Issue solved.") ending with a **period, not a `?`** — that period is the machine signal that marks
  it as the closing option. `chat_service` (`prompts.strip_suggestions`) parses + caps at
  `prompts._MAX_SUGGESTIONS` = 3 and normalizes the closing option to end with a period, then
  `prompts.split_closing` peels the declarative last item off into a separate field: the `/message`
  response carries `suggestions:[…]` (the ≤2 guiding questions) **plus** `closing_suggestion` (the
  closing option, or `null`). The widget renders the guiding questions as one-tap **bubbles**
  (`submitText`) and the closing option as a distinct soft-green **closing bubble**: tapping it sends a
  goodbye turn (Nika still generates a warm reply), then marks the session **resolved**
  (`POST /api/chat/resolve`) and ends the conversation — and crucially does **not** then show the green
  finish button (the player already chose to finish). Stale bubbles clear the moment a new turn starts.
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

## Invariants (these break silently — do not violate)

1. The Layer-1 block (`get_system_core()` = `SYSTEM_CORE` + the static directives) is
   byte-stable; per-request data lives only in the user message (Layer 3).
2. KB is injected per topic from Postgres — never baked into the core.
3. Persisting a turn is one atomic transaction (messages + counters + AI log).
4. Every message → `chat_messages`; every OpenAI call → `ai_interaction_logs`; every state
   transition (escalation, failover, rate-limit, injection) → `admin_events`.
5. Two-key failover races the fallback after the switch timeout; log every failover.
6. No ORM, no migrations: schema is `init_db()`; new columns via guarded `ALTER`; all DB
   access through `db.*` helpers.
7. Model-facing prompt is English (token-efficient); KB may be in any language; answers
   in the resolved language. User-facing copy + user-input detectors stay multilingual.
8. Never request card numbers / CVV / passwords / 2FA codes / seed phrases; never invent
   player-facing facts — KB uses `{{PLACEHOLDER}}` tokens the owner replaces.
9. `_PRICING` is "verify before trusting"; cost is derived, not ground truth.

## Admin / management

Map of what lives where:

- **Admin auth** (`api/admin_auth.py`, `auth.py`): `POST /admin/login` (constant-time
  password compare against `ADMIN_PASSWORD`, rate-limited, failures → `admin_login_failed`)
  issues an admin JWT signed with `ADMIN_JWT_SECRET` and carrying a `role` claim. The
  `require_admin` dependency guards every `/admin/*` data route.
- **Settings** (`settings.py`, `app_settings` table): hot-reloaded runtime tuning with
  precedence `app_settings` (DB) → env → default. A sync in-process cache (populated at
  startup, reloaded on write) is read by `antispam`/`escalation`/`openai_client`/`language`/
  `auth`/api; writes validate hard and log `setting_updated`. Groups: `escalation`
  (incl. `max_messages_per_session`, `high_risk_keywords`, `human_request_keywords`),
  `language` (default + supported
  set — every language read goes through `language.default_code()`/`supported_codes()`),
  `antispam` (rate limit/window/cooldown/input cap **plus** `recaptcha_min_score`,
  `injection_hard_block`, and the low-content guard `low_content_block` /
  `min_meaningful_chars`), `model` (OpenAI tuning — see the failover section), and `general`
  (operational knobs with no other home: `session_ttl_hours`, `contact_form_url`,
  `body_max_bytes`). **The prompt is NOT a settings group** — it lives in `prompts.py` (the
  single source of truth), not `app_settings`. The goal is that every non-secret *operational*
  knob lives in the admin panel and only true secrets (API keys, JWT secrets, `DATABASE_URL`,
  `ADMIN_PASSWORD`, handshake/reCaptcha secrets) — plus the network-perimeter deploy
  vars (`CORS_ALLOW_ORIGINS`, `TRUSTED_PROXY_COUNT`) — stay in Railway env. There is no seed:
  an empty `app_settings` resolves through env → default, and the owner's first write to a
  group persists that override in the DB.
- **Dashboard data API** (`api/admin.py` + `db.py` aggregation + `metrics.py` derived
  rates): overview/timeseries/by-topic/by-language/sessions/session/unresolved.
  `resolution_rate` is a documented PROXY (counts "not escalated", incl. abandoned →
  `sessions_open` tracked separately). **Cost** is surfaced per row: `by-topic`, `by-language`,
  and `sessions` each carry a `cost_usd_total` (summed from `ai_interaction_logs` via a join/CTE)
  rendered in the SPA tables. **Date ranges** are half-open and a date-only `to=YYYY-MM-DD` is
  made **inclusive** of that whole day (`api.admin._range` adds one day), so "today" isn't dropped.
  The **Unresolved** queue lists engaged sessions that still need attention — both `escalated` and
  abandoned `open` chats with ≥1 user turn (resolved excluded), grouped by topic.
- **Variables tab** (`api/admin.py` `/admin/kb/variables`, the **Variables** view in the SPA):
  list + edit the admin-managed `{placeholder}` registry (see "KB variables" above). Read returns
  `updated_at` as an isoformat string so `JSONResponse` can serialize it.
- **The prompt is the file `prompts.py` (single source of truth, NOT editable from admin).**
  The Layer-1 core (`SYSTEM_CORE` — Nika's tone-of-voice + the absolute/escalation/
  responsible-gaming/links rules), the STATIC Layer-1 directives (greeting, formatting,
  KB-grounding, escalation restraint, suggested questions, finish-chat, lead-forward), the DYNAMIC
  Layer-3 directives (language, personalization, topic routing) + the recency guardrails, and the
  forbidden-topics list/refusal are constants in that file. To change the prompt you edit
  `prompts.py` and redeploy — there is no admin editor, no `prompt_versions` table, no A/B split, no
  `system_prompt`/`layer3_prompt` settings group. (This replaced an earlier design where the core was
  versioned in the DB and edited from the panel; it was removed so there's exactly one place the
  prompt comes from.) **Read-only effective-prompt view** (`api.admin._build_effective_preview` +
  `GET /admin/effective-prompt`, the **Prompt** tab in the SPA): so the owner can always SEE the
  whole assembled prompt, this endpoint reuses `prompts.build_messages` with a sample player + a
  sample specialized topic's KB and returns the complete prompt split into the system message
  (Layer 1 core + static directives + Layer 2 KB) and the user message (the dynamic Layer-3
  directives + player context + recency guardrails). The SPA renders it as read-only blocks. It is
  resilient — if topics/KB can't load it still renders Layer 1 + the Layer-3 block, never breaking
  the page. (Layer 2, the per-topic KB, is the one prompt input still edited in the admin — in the
  Knowledge-base tab — because it's answer content, not instructions.)
- **KB editing** (`db.*` helpers, `api/admin.py` `/admin/kb/*`): **one KB text per topic**,
  single-language. `GET /admin/kb/content?topic_id=` reads it, `PUT /admin/kb/content` sets it
  (updates the topic's active entry in place, or inserts one), `DELETE /admin/kb/content?topic_id=`
  soft-clears it (`active=false`). No versioning, no per-language entries — the Layer-3 language
  directive still makes the model answer in the player's language regardless of the KB language.
- **Escalation** (`escalation.build_payload`): returns the localized contact-button payload.
  No ticket snapshot, no Telegram notifier — the hand-off is the contact button only.
- **Signed handshake** (`auth.sign_handshake`/`verify_handshake`, `api/chat.create_session`):
  with `WIDGET_HANDSHAKE_SECRET` set, only a valid signed blob is trusted for
  `user_context`; raw browser context is ignored. No secret ⇒ dev behaviour. The
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
  `npchat-` to avoid host-page collisions. The admin SPA uses `npadmin-`.
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
