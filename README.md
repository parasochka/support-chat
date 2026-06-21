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

### Phase 2 (admin dashboard, tuning, escalation, handshake)

| Variable | Default | Notes |
|---|---|---|
| `ADMIN_PASSWORD` | — | **Required to enable the dashboard.** If unset, `/admin` data API + login return 503. |
| `ADMIN_JWT_SECRET` | `SESSION_JWT_SECRET` | HS256 secret for admin tokens (set a *distinct* value in production). |
| `ADMIN_TOKEN_TTL_MIN` | `480` | Admin session length (minutes). |
| `TELEGRAM_BOT_TOKEN` | — | Bot API token for the escalation notifier. Unset ⇒ button-only escalation. |
| `TELEGRAM_AGENT_CHAT_ID` | — | Chat/group id that receives escalation tickets. |
| `WIDGET_HANDSHAKE_SECRET` | — | HMAC secret for signed `user_context` from host sites. Set ⇒ unsigned browser context is ignored. |
| `WIDGET_HANDSHAKE_MAX_AGE_SEC` | `300` | Max age (seconds) of a signed handshake (anti-replay window). |
| `PUBLIC_BASE_URL` | — | Used for deep links in Telegram tickets (`/admin#/session/{id}`). |

Runtime tunables (`escalation`, `forbidden_topics`, `language`, `antispam`) are
also editable live from the dashboard **Settings** tab and stored in the
`app_settings` table. Precedence is `app_settings` (DB) → env → hardcoded default,
and edits are hot (no redeploy).

## API contract

```
POST /api/chat/session       { consumer?, player_id?, user_context?, signed_context?, lang?, locale?, recaptcha_token? }
                          ->  { session_id, token, topics:[{slug,title}], lang, languages:[...] }
                                signed_context (HMAC blob) is the only trusted user_context source when
                                WIDGET_HANDSHAKE_SECRET is set; otherwise raw user_context is dev-only
POST /api/chat/topic         (Bearer)  { session_id, topic_slug } -> { ok:true }
POST /api/chat/lang          (Bearer)  { session_id, lang } -> { ok:true, lang, topics:[{slug,title}] }
                                       manual language switch; locks the answer + UI language
POST /api/chat/message       (Bearer)  { session_id, text }
                          ->  { reply, lang, escalation:{active,message?,button?}, message_count,
                                suggested_topic:{slug,title}|null }
                                suggested_topic is set when the model judged the question belongs to
                                another topic whose KB isn't loaded; the widget offers a 1-tap switch
GET  /api/chat/session/{id}  (Bearer)  resume: history + state
POST /api/chat/escalate      (Bearer)  { session_id } -> { escalation:{...} }
GET  /healthz                liveness; checks DB connectivity
```

### Admin API (Phase 2, all `/admin/*` data routes require an admin Bearer)

```
POST /admin/login                       { password } -> { token, ttl_min, role }
GET  /admin/overview?from&to            KPIs (rates, cost, cache-hit, counters)
GET  /admin/timeseries?metric&bucket    sessions | cost | escalation_rate over time
GET  /admin/by-topic | /admin/by-language
GET  /admin/sessions?from&to&topic&lang&status&escalated&q&page   paginated list
GET  /admin/session/{id}                full transcript + user_context + logs + cost
GET  /admin/unresolved?format=json|csv  escalated sessions grouped by topic (KB growth)
GET  /admin/ab/results                  per prompt_version outcome metrics
GET/POST/PUT /admin/prompts ...         version list / create draft / edit draft
POST /admin/prompts/{id}/publish        make default (deliberate cache reset; UI warns)
POST /admin/prompts/ab                  { weights:[{id,weight}] } set A/B split
POST /admin/prompts/{id}/archive
GET/POST /admin/kb/topics               topic CRUD (with entry counts)
GET/POST/PUT/DELETE /admin/kb/entries   versioned entry CRUD (soft-delete)
POST /admin/kb/import                   multipart JSON | CSV | Markdown bulk import
GET  /admin/settings | PUT /admin/settings/{key}   hot-reloaded runtime tuning
GET  /admin                             dashboard SPA (login -> admin JWT)
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

## Phase 2 (built)

Implemented on the same stack (no new infra): admin dashboard SPA under `/admin`
(vanilla ES modules, `npadmin-` prefix, hand-rolled SVG charts — no CDN chart
lib), single-owner admin auth with a future-proof `role` claim, hot-reloaded
`app_settings` tuning, system-prompt versioning + deterministic weighted A/B
(attributed via `chat_sessions.prompt_version_id`), KB CRUD + JSON/CSV/Markdown
bulk import, a one-way Telegram escalation notifier with `escalation_tickets`
(contact button always retained), a signed (HMAC) front-end handshake, and the
unresolved-query view (topic-grouped, CSV export).

§16 decisions chosen: unresolved analysis = topic-grouped (no embeddings);
contact form = host-site button only; admin auth = single owner. Charting =
hand-rolled inline SVG (no external dependency).

Still out of scope (clean seams left): two-way live-agent chat, multi-admin RBAC,
external helpdesk integration, and embeddings-based unresolved clustering. KB
content remains **placeholder** — every player-facing number is a
`{{PLACEHOLDER}}` token for the owner to replace via the dashboard.
