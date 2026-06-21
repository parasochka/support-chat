# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone FastAPI microservice serving an AI customer-support chat for **NikaBet**
(casino + sportsbook on the NowPlix B2B platform). It is API-isolated: other modules
talk to it over HTTP/JSON by `session_id` (UUID), and the contract is consumer-agnostic
so multiple front-ends can plug in. **Phase 1 is fully implemented**; Phase 2 (admin
dashboard, tuning, Telegram escalation, prompt versioning) is specified but **not yet
built** — see "Phase 2 roadmap" below.

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
KB are Russian; only the answer language varies (the model is told "answer in {LANG}").

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

### Anti-spam gate order (`antispam.py`, enforced in `api/chat.py`)
`POST /api/chat/message` checks in this exact order: verify session token (401) → IP
rate-limit (429 + log) → cooldown (429) → input length (400) → injection scan (log only,
does not reject) → message-cap fast path (forces an escalation response with no model call)
→ build/call/persist. Rate-limit and cooldown use **in-memory dicts** — fine for Phase 1
but they do not span multiple instances. reCaptcha is verified at session create and skips
gracefully (logged) when `RECAPTCHA_SECRET` is unset.

### Language resolution (`language.py`)
Deterministic priority, never asks the user: explicit `lang` → `locale` (e.g. `es-MX`→`es`)
→ persisted `session_lang` → `AUTO` sentinel (model mirrors the user's language; the
detected language is persisted on the session after the first turn) → `DEFAULT_LANGUAGE`.

### Escalation (`escalation.py`)
Phase 1 returns a contact-button payload only (no form, no live agent). `decide()` triggers
on: high-risk keywords (fraud/legal), explicit human requests, message cap, or the model's
`[[ESCALATE]]` sentinel. On escalation, `chat_sessions.status='escalated'` and an
`admin_events('escalation')` row is written. The button URL is `CONTACT_FORM_URL`.

### Two layers of injection defense
1. `prompts._sanitize_field` zeroes any `user_context` field containing injection markers
   (only `id, full_name, email, activation_status` are surfaced to the model).
2. `antispam.scan_injection` scans the user message and **logs** `injection_blocked` but
   does not reject — `SYSTEM_CORE` already hardens against it.

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

## Phase 2 roadmap (specified, NOT yet implemented)

`CLAUDE_CODE_PROMPT_support_chat_phase2.md` extends — does not rebuild — Phase 1 on the same
stack. Planned additions: admin dashboard SPA under `/admin`, admin JWT auth (separate
`ADMIN_JWT_SECRET`), an `app_settings` table for hot-reloaded runtime tuning (precedence:
DB → env → default), system-prompt versioning + A/B (`prompt_versions`, `chat_sessions.
prompt_version_id`), KB CRUD + bulk import, Telegram escalation notifier + `escalation_tickets`,
a signed (HMAC) front-end handshake to replace the hardcoded `user_context`, and an
unresolved-query view. When building it, reuse all Phase 1 tables/helpers/conventions and
keep every invariant above (especially: editing the prompt creates a *draft*; publishing is
a deliberate one-time cache reset).

## Conventions

- Stdlib-only JWT (`auth.py`) — HS256 via `hmac`/`hashlib`/`base64`, no PyJWT.
- Front-end is vanilla ES modules with **no build step**; widget classes are prefixed
  `npchat-` to avoid host-page collisions. Phase 2 admin SPA should use `npadmin-`.
- Deploy is Railway via the single `Dockerfile` (`python:3.11-slim`) + `railway.toml`; the
  CMD reads `$PORT`, no `startCommand` override. Health check is `/healthz`.
- Develop on branch `claude/pensive-tesla-opotpt`; do not push to other branches.
- Env var reference lives in `README.md` (§ "Environment variables").
