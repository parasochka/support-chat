# NowPlix AI Customer-Support Chat (Phase 1 prototype)

A standalone microservice that serves an AI-powered customer-support chat for
**NikaBet** (casino + sportsbook on the NowPlix B2B platform). Phase 1 is a
working, deployable prototype: open a test page, see a floating chat widget, and
tune the bot's behaviour (prompt, KB, flow, anti-spam) end to end.

The service is **API-isolated**: other NowPlix modules talk to it over HTTP/JSON
by `session_id` (UUID). The public contract is consumer-agnostic so multiple
front-ends can plug in.

## Stack

- Python 3.11, FastAPI + uvicorn
- PostgreSQL via **asyncpg** directly — no ORM, no migrations. The schema *is*
  `db.init_db()` (run on startup).
- OpenAI Python SDK (`gpt-4o-mini` by default), two-key failover.
- JWT (HS256) on pure stdlib — no PyJWT.
- Vanilla ES-module front-end (floating widget + test page), no build step.
- Deploy: Railway via Dockerfile (`python:3.11-slim`) + `railway.toml`.

## Architecture (3-layer prompt, prefix-cache optimised)

```
system message
  LAYER 1  SYSTEM_CORE   byte-stable Russian core (always cached)
  LAYER 2  KB block       injected per selected topic from Postgres
user message
  LAYER 3  dynamic        sanitized user_context + language directive
                          + conversation history + the new user turn
```

`SYSTEM_CORE` is **byte-identical** between requests (a test enforces this);
dynamic data lives only in the user message so the cached prefix never shifts.

## Project layout

```
config.py          env parsing (require_env, fail-fast)
db.py              init_db() schema + all async db.* helpers (no ORM)
auth.py            HS256 JWT on stdlib; session-token issue/verify
openai_client.py   two-key client + failover/race + backoff + cost log
prompts.py         3-layer prompt assembly, byte-stable core, sanitizer
kb.py              KB load helpers; topic catalogue
language.py        language resolution (param > locale > auto > default)
antispam.py        rate-limit, cooldown, caps, recaptcha, injection scan
escalation.py      escalation decision + contact-button payload
chat_service.py    orchestration: build prompt -> call -> log -> reply
main.py            FastAPI app: lifespan(init_db+seed), middleware, routers, static
api/chat.py        /api/chat/* endpoints
api/health.py      /healthz
seed/kb_seed.py    6 placeholder topics + hidden 'other' (Russian KB)
frontend/          widget.js, widget.css, test.html
tests/             pytest suite + conftest stubs (no real DB/API needed)
```

## Running locally

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/supportchat
export OPENAI_API_KEY=sk-...
export SESSION_JWT_SECRET=$(openssl rand -hex 32)
uvicorn main:app --reload --port 8080
# open http://localhost:8080/  (test page with the widget)
```

## Tests

```bash
pip install pytest pytest-asyncio
pytest
```

`tests/conftest.py` stubs `openai`/`asyncpg` and sets `SUPPORT_CHAT_TEST_MODE=1`
so the suite runs without a real database or API key.

## Deploy to Railway

1. Create a Railway project from this repo; it builds the `Dockerfile`.
2. Add a Postgres plugin and reference its `DATABASE_URL`.
3. Set the environment variables below.
4. Health check is `/healthz`. Open `/` for the test page.

The Dockerfile CMD reads `$PORT` (Railway injects it); no `startCommand` override.

## Environment variables

### Required (service fails fast at boot if missing)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres DSN. `postgres://` is auto-rewritten to `postgresql://`. |
| `OPENAI_API_KEY` | Primary OpenAI key. |
| `SESSION_JWT_SECRET` | HS256 secret for session tokens. |

### Important optional (defaults shown)

| Var | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY_FALLBACK` | — | Backup key for failover/race. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model. |
| `OPENAI_REQUEST_TIMEOUT_SEC` | `40` | Per-request timeout. |
| `OPENAI_KEY_SWITCH_TIMEOUT_SEC` | `25` | Silence before racing the other key. |
| `OPENAI_MAX_ATTEMPTS` | `3` | Backoff retries. |
| `OPENAI_TEMPERATURE` | `0.3` | |
| `OPENAI_MAX_OUTPUT_TOKENS` | `700` | Output cap (cost + abuse). |
| `OPENAI_MAX_CONCURRENT_PER_KEY` | `4` | Per-key semaphore. |
| `SESSION_TTL_HOURS` | `24` | Session JWT + row TTL. |
| `MAX_MESSAGES_PER_SESSION` | `30` | Then force escalation. |
| `MAX_INPUT_CHARS` | `2000` | Per user message. |
| `RATE_LIMIT_WINDOW_SEC` | `600` | Sliding window. |
| `RATE_LIMIT_MAX_PER_IP` | `20` | Messages/window/IP. |
| `MESSAGE_COOLDOWN_SEC` | `2` | Min gap between messages in a session. |
| `RECAPTCHA_SECRET` | — | reCaptcha v3 server secret (optional in dev). |
| `RECAPTCHA_MIN_SCORE` | `0.5` | Reject below this. |
| `CONTACT_FORM_URL` | — | Escalation button target. |
| `DEFAULT_LANGUAGE` | `en` | Fallback when nothing resolves. |
| `SUPPORTED_LANGUAGES` | `en,es,ru,tr,pt` | Allowed answer languages. |
| `OWNER_TOKEN` | — | Bearer to gate owner-only/debug endpoints. |
| `BODY_MAX_BYTES` | `65536` | Request body cap (64 KB). |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated allowed origins. |
| `TRUSTED_PROXY_COUNT` | `1` | Reverse proxies in front of the app; the client IP is read this many hops from the right of `X-Forwarded-For` so a spoofed left value can't defeat rate limiting. |

## API contract

```
POST /api/chat/session       { consumer?, player_id?, user_context?, lang?, locale?, recaptcha_token? }
                          ->  { session_id, token, topics:[{slug,title}], lang, languages:[...] }
POST /api/chat/topic         (Bearer)  { session_id, topic_slug } -> { ok:true }
POST /api/chat/lang          (Bearer)  { session_id, lang } -> { ok:true, lang, topics:[{slug,title}] }
                                       manual language switch; locks the answer + UI language
POST /api/chat/message       (Bearer)  { session_id, text }
                          ->  { reply, lang, escalation:{active,message?,button?}, message_count }
GET  /api/chat/session/{id}  (Bearer)  resume: history + state
POST /api/chat/escalate      (Bearer)  { session_id } -> { escalation:{...} }
GET  /healthz                liveness; checks DB connectivity
```

Gate order inside `POST /api/chat/message`: verify token (401) -> IP rate-limit
(429 + log) -> cooldown (429) -> input caps (400) -> message cap (force
escalation) -> build prompt, call model, persist turn atomically, return.

## Invariants (do not violate)

1. `SYSTEM_CORE` is byte-stable; dynamic data lives in the user message only.
2. KB is injected per selected topic from Postgres — never baked into the core.
3. Persisting a turn is one atomic transaction (messages + counters + AI log).
4. Every message -> `chat_messages`; every OpenAI call -> `ai_interaction_logs`;
   every state transition -> `admin_events`.
5. Two-key failover with parallel race after the switch timeout; log failovers.
6. No ORM, no migrations: schema is `init_db()`; all DB access via `db.*`.
7. Source prompt + KB in Russian; answers in the resolved language.
8. Never request card numbers / CVV / passwords / seed phrases; never invent facts.
9. The `_PRICING` table is marked "verify before trusting"; prices may be stale.

## Phase 2 (not built; clean seams left)

Admin dashboard UI, the contact/escalation form itself, real front-end
integration, live-agent chat. KB content here is **placeholder** — every
player-facing number is a `{{PLACEHOLDER}}` token for the owner to replace.
