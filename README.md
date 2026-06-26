# NikaBet Support Chat

A standalone FastAPI microservice serving an AI customer-support chat for **NikaBet**
(casino + sportsbook on the NowPlix B2B platform). It is API-isolated: other modules
talk to it over HTTP/JSON by `session_id` (UUID), so multiple front-ends can plug in.

> Developer/agent guidance lives in **[`CLAUDE.md`](./CLAUDE.md)** — architecture,
> invariants, and conventions. This README is the human-facing overview.

## What it does

- **AI support chat** for NikaBet, answering from a per-topic knowledge base (KB).
- **Follows the player's language** automatically each turn; the widget chrome starts
  in the browser language and re-localizes as the conversation drifts.
- **Topic routing** — routes a question to the right topic, **suggests follow-up
  questions**, and offers a **"finish chat"** action once the issue looks resolved.
- **Escalation** to human support (a contact button) on explicit request, complaints,
  suspected fraud/legal threats, the per-session message cap, or when the model can't help.
- **Anti-spam** before any model call: IP rate limiting, per-message cooldown, an input
  length cap, a low-content/junk guard, and a prompt-injection scan (hard-block by default).
- **Two-key OpenAI failover** — a fallback API key is raced in after a switch timeout so a
  silent primary key doesn't stall answers.
- **Admin dashboard** (`/admin`) — overview metrics, per-topic/-language breakdowns
  (with per-row cost), session browsing (with per-session cost), unresolved-cluster
  export, hot-reloaded runtime settings, knowledge-base editing, and a **Variables**
  tab for the admin-managed `{placeholder}` values injected into KB answers.

## Architecture in one paragraph

Each request is assembled as a **3-layer, prefix-cache-optimised prompt**: a byte-stable
**English** system block (Layer 1 — the "Nika" persona/tone-of-voice plus every static
behavioural directive), the selected topic's KB block (Layer 2), and only per-request data —
player context, language directive, topic routing, history, the new turn (Layer 3, in the
user message). The whole model-facing prompt is English for token efficiency; the language
directive still makes the model **answer in the player's language**, and the KB may be in any
language. The prompt is the file **`prompts.py`** (the single source of truth) — it is not
editable from the admin; the admin **Prompt** tab is a read-only view of the assembled
prompt. The data layer is direct `asyncpg` (no ORM, no migration files): the schema *is*
`db.init_db()`. See `CLAUDE.md` for the full design and the invariants.

## Run

```bash
# Tests (stubs OpenAI + asyncpg; no real DB/API key needed)
SUPPORT_CHAT_TEST_MODE=1 python -m pytest -q

# Locally (needs a real Postgres + OpenAI key)
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/supportchat
export OPENAI_API_KEY=sk-... SESSION_JWT_SECRET=$(openssl rand -hex 32)
uvicorn main:app --reload --port 8080   # test page at http://localhost:8080/
```

The database is the source of truth for runtime settings and the KB once the owner edits
them in the admin. There is **no seed step**: on a fresh/empty database, create topics and
their KB from the admin panel; runtime settings fall back to env → built-in defaults until
overridden.

## Deploy

Railway via the single `Dockerfile` (`python:3.11-slim`) + `railway.toml`; the CMD reads
`$PORT`. Health check is `/healthz`.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Postgres DSN (`postgres://` is normalised to `postgresql://`). |
| `OPENAI_API_KEY` | yes | — | Primary OpenAI key. |
| `SESSION_JWT_SECRET` | yes | — | Signs front-end session tokens. |
| `OPENAI_API_KEY_FALLBACK` | no | — | Second key for the two-key failover. |
| `ADMIN_JWT_SECRET` | no | `SESSION_JWT_SECRET` | Signs admin tokens; set a distinct value in prod. |
| `WIDGET_HANDSHAKE_SECRET` | no | — | HMAC secret for signed host-site `user_context`. Unset ⇒ dev mode. |
| `RECAPTCHA_SECRET` | no | — | Enables reCaptcha v3 at session create; unset ⇒ skipped. |
| `CONTACT_FORM_URL` | no | — | URL behind the escalation contact button. |
| `DEFAULT_LANGUAGE` / `SUPPORTED_LANGUAGES` | no | `en` / `en,es,ru,tr,pt` | Language defaults. |
| `CORS_ALLOW_ORIGINS` | no | `*` | Comma-separated allowed origins (restrict in prod). |
| `TRUSTED_PROXY_COUNT` | no | `1` | Trusted proxy hops to read from the right of `X-Forwarded-For`. |
| `TRUSTED_PROXY_IPS` | no | private/reserved ranges | Comma-separated immediate proxy IPs/CIDRs whose `X-Forwarded-For` may be trusted. Defaults to the private/reserved ranges (RFC1918 + CGNAT + loopback/ULA), which is correct on Railway and most PaaS — the platform proxy connects from a private peer IP that a public client cannot forge. Tighten to your edge's exact CIDR if you know it. |

Most operational knobs (rate limits, cooldowns, model tuning, escalation thresholds,
session TTL, body cap, etc.) are tunable live from the admin **Settings** tab and only need
an env var to seed an initial value. True secrets stay in env. See `config.py` for the full
list.
