# Support Chat (multi-tenant)

A standalone FastAPI microservice serving an AI customer-support chat for casino /
sportsbook brands. It is API-isolated: other modules talk to it over HTTP/JSON by
`session_id` (UUID), so multiple front-ends can plug in.

> Developer/agent guidance lives in **[`CLAUDE.md`](./CLAUDE.md)** — architecture,
> invariants, and conventions. This README is the human-facing overview.
> Integration guide for partner/CMS dev teams is served by the app itself at
> **`/integration`** (embed contract, handshake signing, API reference), and the
> Telegram retention bot has its own guide at **`/integration-telegram`** (deeplink
> contract, player profile sync, admin setup). Setup/run steps for the retention
> bot are in **[`RETENTION_SETUP.md`](./RETENTION_SETUP.md)**.

## Multi-tenancy

The service is a commercial multi-tenant product: **partners** own casino
**products**, and each product is a fully separate tenant — its own knowledge
base, prompt persona/brand values, translations, settings, **own OpenAI keys**
(1–2, with the same failover) and handshake secret (both stored encrypted).
A product is identified by its public **widget key** (`wk_…`, issued and rotatable
in the admin **Structure** tab); the embed snippet passes it via
`data-widget-key`. Admin access is scoped by **memberships**: a role
(`admin`/`manager`) per scope — global, per partner, or per product — and the
admin panel header carries a **Partner → Product switcher** that re-scopes every
tab. On first boot after the upgrade, existing single-tenant data is adopted into
a `default` partner/product automatically. A newly created product starts with a
**brand-neutral starter knowledge base** — seven generic casino topics: deposits,
withdrawals, account & verification, bonuses, betting & games, technical + «Other»
(a normal, never-hidden topic that closes the picker) — and baseline prompt
variables (`brand_name` = the product's name), so its chat works immediately;
the owner then translates and uniquifies the content per brand from the admin
panel.

## What it does

- **AI support chat** per casino product, answering from a per-topic knowledge base (KB).
- **Follows the player's language** automatically each turn; the widget chrome starts
  in the browser language and re-localizes as the conversation drifts.
- **Topic routing** — routes a question to the right topic, **suggests follow-up
  questions**, and offers a **"finish chat"** action once the issue looks resolved.
- **Escalation** to human support (a contact button) on explicit request, complaints,
  suspected fraud/legal threats, the per-session message cap, or when the model can't help.
  The button's default target is the per-language `contact_url` (a form / support group /
  chat). **When the product runs the Telegram retention bot, the escalation button instead
  routes the player straight into the bot** — a one-time escalation-entry deeplink that
  subscribes them to the channel on the way in and offers a live manager. The widget is the
  primary channel, so this hand-off happens from the widget itself; it falls back to the
  static `contact_url` whenever retention is off.
- **Anti-spam** before any model call: IP rate limiting, per-message cooldown, an input
  length cap, a low-content/junk guard, and a prompt-injection scan (hard-block by default).
- **Two-key OpenAI failover** — a fallback API key is raced in after a switch timeout so a
  silent primary key doesn't stall answers.
- **Admin dashboard** (`/admin` — the React Admin SPA in `admin/`, compiled by the
  two-stage Docker build and served by this same service over the `/admin/*` API;
  see `admin/README.md`) — overview metrics, per-topic/-language breakdowns
  (with per-row cost), session browsing (with per-session cost), unresolved-cluster
  export, hot-reloaded runtime settings, knowledge-base editing (with a **Variables**
  sub-tab for the `{placeholder}` values injected into KB answers), a **Prompt** view
  (read-only assembled prompt + a **Prompt variables** sub-tab that re-brands the prompt
  template — persona/brand/products/tone — and hosts the escalation keyword lists and the
  test player profile), a **Translations** tab for every user-facing widget string
  (chrome, service replies, topic names) per language, a **Structure** tab
  (partners/products, widget keys + embed snippets, per-product OpenAI/handshake
  secrets), and a **Users** tab with per-scope memberships. Everything is edited
  per product via the header switcher.

## Architecture in one paragraph

Each request is assembled as a **3-layer, prefix-cache-optimised prompt**: a byte-stable
**English** system block (Layer 1 — the "Nika" persona/tone-of-voice plus every static
behavioural directive), the selected topic's KB block (Layer 2), and only per-request data —
player context, language directive, topic routing, history, the new turn (Layer 3, in the
user message). The whole model-facing prompt is English for token efficiency; the language
directive still makes the model **answer in the player's language**, and the KB may be in any
language. The prompt WORDING is the file **`prompts.py`** (the single source of truth) — a
dry template that is not editable from the admin; the admin **Prompt** tab shows a read-only
view of the assembled prompt, and its **Prompt variables** sub-tab edits the `{placeholder}`
values (persona name, brand, products, tone of voice) that uniquify the template per brand.
The data layer is direct `asyncpg` (no ORM, no migration files): the schema *is*
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
| `OPENAI_API_KEY_FALLBACK` | no | — | Second key for the two-key failover. Both env keys are the deploy-level fallback for products without their own keys (set per product in Structure). |
| `APP_ENV` | no | `development` | Deployment environment. When `production` (or `prod`) the app **refuses to boot** if `ADMIN_JWT_SECRET`, `SECRETS_MASTER_KEY`, or `TELEGRAM_WEBHOOK_SECRET` is unset and would reuse `SESSION_JWT_SECRET`. Dev/test only logs a warning. |
| `ADMIN_JWT_SECRET` | no | `SESSION_JWT_SECRET` | Signs admin tokens; set a distinct value in prod (**required when `APP_ENV=production`**). |
| `SECRETS_MASTER_KEY` | no | `SESSION_JWT_SECRET` | Master key encrypting per-product secrets (OpenAI keys, handshake secrets) at rest. Set a distinct strong value in prod (**required when `APP_ENV=production`**); rotating it invalidates stored product secrets (re-enter them in the admin). |
| `WIDGET_HANDSHAKE_SECRET` | no | — | Deploy-level HMAC secret for signed host-site `user_context`. A product's own handshake secret (Structure tab) takes precedence. Neither set ⇒ dev mode. |
| `RECAPTCHA_SECRET` | no | — | Enables reCaptcha v3 at session create; unset ⇒ skipped. |
| `CONTACT_FORM_URL` | no | — | Optional deploy-level fallback URL behind the escalation contact button — applies to the **default product only**, never to other products. The URL's real home is the admin Translations tab (`contact_url`, per product/per language); a value stored by old builds in the DB is auto-migrated there on boot. |
| `DEFAULT_LANGUAGE` / `SUPPORTED_LANGUAGES` | no | `en` / `en,es,ru,tr,pt` | Language defaults. |
| `CORS_ALLOW_ORIGINS` | no | `*` | Comma-separated allowed origins (restrict in prod). |
| `TRUSTED_PROXY_COUNT` | no | `1` | Trusted proxy hops to read from the right of `X-Forwarded-For`. |
| `TRUSTED_PROXY_IPS` | no | private/reserved ranges | Comma-separated immediate proxy IPs/CIDRs whose `X-Forwarded-For` may be trusted. Defaults to the private/reserved ranges (RFC1918 + CGNAT + loopback/ULA), which is correct on Railway and most PaaS — the platform proxy connects from a private peer IP that a public client cannot forge. Tighten to your edge's exact CIDR if you know it. |
| `TELEGRAM_WEBHOOK_SECRET` | no | `SESSION_JWT_SECRET` | Retention bot: verifies the `X-Telegram-Bot-Api-Secret-Token` header on `/telegram/webhook/{secret}` (NOT in the URL). Set a distinct value in prod (**required when `APP_ENV=production`**). |
| `PUBLIC_BASE_URL` | no | — | Retention bot: public base URL of this service (e.g. `https://chat.example.com`), used to build the webhook URL when registering it with Telegram. Required to auto-register the webhook from the admin. |
| `RETENTION_MEDIA_DIR` | no | `./media` | Retention bot: on-disk path for uploaded media. On Railway set it to the mount path of an attached **Volume** so photos survive redeploys. |
| `RETENTION_MAX_UPLOAD_BYTES` | no | `10485760` | Max size of a retention media upload (the JSON body cap is far smaller; the media-upload path uses this instead). |
| `RETENTION_NONCE_TTL_SEC` | no | `120` | Retention deeplink nonce lifetime (also a `retention` settings knob). |
| `RETENTION_PROFILE_PULL_TTL_SEC` | no | `3600` | If a profile snapshot is older than this and the product has a Player API, pull a fresh profile before a turn (also a `retention` settings knob). |

The retention bot's per-product config (bot token, channel, player-API key) lives on the
product row in the admin **Retention · Telegram** section, not in env; secrets there are
encrypted at rest via `SECRETS_MASTER_KEY`. Photo-progression / limit knobs
(`daily_photo_cap`, `stage_advance_msgs`, `max_stage_by_tier`, …) live in the `retention`
settings group (defaults seeded from `RETENTION_*` env). See `RETENTION_BOT_SPEC.md`.

Most operational knobs (rate limits, cooldowns, model tuning, escalation thresholds,
session TTL, body cap, etc.) are tunable live from the admin **Settings** tab and only need
an env var to seed an initial value. True secrets stay in env. See `config.py` for the full
list.
