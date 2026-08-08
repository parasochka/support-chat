# Support Chat (multi-tenant)

A standalone FastAPI microservice serving an AI customer-support chat for casino /
sportsbook brands. It is API-isolated: other modules talk to it over HTTP/JSON by
`session_id` (UUID), so multiple front-ends can plug in.

> Developer/agent guidance lives in **[`CLAUDE.md`](./CLAUDE.md)** — architecture,
> invariants, and conventions. This README is the human-facing overview.
> Integration docs for partner/CMS dev teams are served by the app itself as a
> family of same-style pages: **`/integration`** is the hub (overview,
> architecture, env vars, docs index), **`/integration-widget`** covers embedding
> the ready-made widget, **`/integration-data`** covers player data transfer &
> sync (signed handshake, lazy pull, push webhook, activity timestamps) plus the
> outbound contracts the casino implements (offer-grant by bonus-CMS ID,
> delegated push/in-app delivery, the delivery-status callback),
> **`/integration-chat-api`** documents the public Chat API + the mandatory
> client logic for a custom UI, **`/integration-telegram`** covers the Telegram
> retention bot (deeplink contract, the proactive agent, admin setup; its
> step-by-step setup checklist lives in the admin panel — **Retention ·
> Telegram → Setup guide**), and **`/integration-admin`** documents integrating
> an external "master" admin panel with the `/admin` API (roles model, service
> API keys, scoping, endpoint reference). Two of the pages are generated from the
> code by `scripts/build_api_reference.py`: **`/integration-reference`** (one
> filterable table of every integration unit that crosses a system boundary) and
> **`/integration-variables`** (the text surface inside the product — KB and
> prompt variables, the copy registry, the model's control sentinels and the
> editable text blocks, with where each is edited and in what language). Both
> download as the two sheets of the same Excel file.

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
  suspected fraud/legal threats, or when the model can't help. The per-session message cap
  is **soft**: on reaching it the turn is still answered, but the response carries a
  localized `cap_notice` — the widget explains the chat has stalled and offers two buttons,
  escalate or finish; only the hard ceiling (cap × 2, the cost backstop) force-closes.
  The button's default target is the per-language `contact_url` (a form / support group /
  chat). **When the product runs the Telegram retention bot, the escalation button instead
  routes the player straight into the bot** — a one-time escalation-entry deeplink that
  subscribes them to the channel on the way in and offers a live manager. The widget is the
  primary channel, so this hand-off happens from the widget itself; it falls back to the
  static `contact_url` whenever retention is off.
- **Proactive retention agent (Telegram)** — an event-driven agent per product (the
  sidebar «Proactive agent» page): canonical casino events (deposit, level-up, big loss,
  KYC passed, …) arrive via `POST /partner/{product_id}/event` (or the admin simulator),
  a deterministic guard layer decides whether contact is allowed, one cheap AI decision
  call picks message / photo / silence, and the retention persona writes the actual text.
  Every decision — silence and blocks included — lands in an auditable ledger. Hard
  anti-annoyance guards the model can never override: per-player daily cap, minimum gap
  between messages, same-event cooldown, local quiet hours, a daily AI budget, a
  post-loss comfort window, a `/stop` opt-out (`/resume` to re-enable), and blocked-bot
  detection — all live settings (Settings → Retention bot → «Send-frequency guards»).
  Ships enabled in dry-run (decides + logs, sends nothing) until the owner flips it.
- **Retention orchestrator** — a measurement/safety/targeting layer over the proactive
  agent, per product, each mechanic behind its own hot `retention`-group switch (all OFF
  or dry-run by default except the responsible-gaming guard and the 15% holdout):
  a deterministic **holdout control group** + uplift reporting; a **responsible-gaming
  guard** (casino-fed `rg_status`, first in the guard order, append-only audit);
  **adaptive frequency caps** (channel × cohort, priorities P1..P5) and **smart send
  time**; player **scoring** (dormancy cohorts, RFM, value tiers, VIP mapping);
  a **bonus-offer engine** keyed to the casino's bonus-CMS IDs (idempotent grant call,
  separate stimulus budget); declarative **journeys** with a scenario/template library;
  and a **multichannel router** with strict opt-in (Telegram, email via Customer.io,
  delegated push/in-app through the casino's delivery endpoint + status callback, and a
  VIP-host task queue). Contracts live on `/integration-data`; the admin surface is the
  **Retention → Orchestrator** page.
- **Anti-spam** before any model call: IP rate limiting, per-message cooldown, an input
  length cap, a low-content/junk guard, and a prompt-injection scan (hard-block by default).
  Inbound Telegram bot messages run the same gauntlet with a higher, chat-paced per-user
  rate limit (`tg_rate_limit_max_per_user`, env `TG_RATE_LIMIT_MAX_PER_USER`, default 60 per
  window — a live dialogue outpaces the widget's per-IP budget): the first blocked message
  gets a one-time in-persona "give me a moment" notice (further ones in the same window are
  dropped silently), and low-content/injection get model-free canned replies; the other
  `antispam` settings knobs are shared.
- **Per-product Cloudflare Turnstile** (invisible mode) — each product/domain gets its own
  site key (Structure tab; served to the widget via `GET /api/chat/i18n` and adopted
  automatically, no embed change) and its own secret (stored encrypted, write-only via
  product secrets). The deploy env `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET` pair remains only
  a fallback. Verification is **advisory (fail-open)**: if the Turnstile script is blocked or
  unreachable for a player (it happens in some regions), the check is simply skipped and the
  other anti-spam layers still gate the request — a player never loses the chat over a
  blocked Cloudflare.
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
  test player profile), a **Site map** tab (the product's official pages the assistant is
  allowed to link to — injected into both the support and the retention bot's prompt so it
  links real pages instead of inventing URLs), a **Translations** tab for every user-facing
  widget string (chrome, service replies, topic names) per language, a **Structure** tab
  (partners/products, widget keys + embed snippets, per-product OpenAI/handshake
  secrets), and a **Users** tab with per-scope memberships. Everything is edited
  per product via the header switcher. The **Retention → Orchestrator** page manages the
  orchestrator mechanics (tabs: Measurement, RG guard, Segmentation, Frequency, Offers,
  Journeys, Templates, Channels), and **System → Integration checklist** tracks the
  deploy-level list of what external teams still owe (statuses edited as agreements land). **Retention analytics** live in the Retention
  section (`GET /admin/retention/overview` / `funnel` / `timeseries`): lifetime +
  in-range KPIs (engagement, photos, proactive sends + reply rate, cost, stage
  distribution),
  the entry funnel (deeplinks → starts → new users → subscribed → engaged → photo
  receivers → handoffs) and daily series. AI spend is **attributed per facade and per
  call type** (dialogue / proactive agent / media cataloguing / AI quality judge), and a
  platform-wide **AI cost by call type** histogram (`GET /admin/ai-costs`) sits under the
  AI-model settings with its own platform / partner / product and call-type filters.
- **Service API keys** for machine access to the `/admin` API (an external "master"
  admin panel, partner backends): `sak_…` Bearer tokens minted in **System → API keys**
  (shown once; only a hash is stored), each carrying one role (`admin`/`manager`) at one
  scope (global/partner/product) — used exactly like the human JWT on any `/admin/*`
  endpoint. Deactivation applies immediately; only human admin accounts manage keys.
  See `/integration-admin` for the full guide.

## Architecture in one paragraph

Each request is assembled as a **3-layer, prefix-cache-optimised prompt**: a byte-stable
**English** system block (Layer 1 — the "Nika" persona/tone-of-voice plus every static
behavioural directive), the selected topic's KB block (Layer 2), and only per-request data —
player context, language directive, topic routing, history, the new turn (Layer 3, in the
user message). The whole model-facing prompt is English for token efficiency; the language
directive still makes the model **answer in the player's language**, and the KB may be in any
language. The prompt WORDING is the file **`app/ai/prompts.py`** (the single source of truth) — a
dry template that is not editable from the admin; the admin **Prompt** tab shows a read-only
view of the assembled prompt, and its **Prompt variables** sub-tab edits the `{placeholder}`
values (persona name, brand, products, tone of voice) that uniquify the template per brand.
The data layer is direct `asyncpg` (no ORM, no migration files): the schema *is*
`db.init_db()`. See `CLAUDE.md` for the full design and the invariants.

## Repository layout

All backend Python lives in the `app/` package: `app/main.py` (the FastAPI entry point),
`app/api/` (HTTP routes), `app/core/` (config, settings, data layer, tenancy, auth),
`app/ai/` (the prompt template, OpenAI client, KB), `app/i18n/` (language + translations),
`app/chat/` (the support-chat flow) and `app/retention/` (the Telegram retention bot
plus the orchestrator modules: `measurement.py`, `rg_guard.py`, `frequency.py`,
`scoring.py`, `offers.py`, `journeys.py`, `scenario_library.py`, `channels.py`,
`partner_out.py`; their admin API is `app/api/orchestrator.py`).
Around it at the repo root: `admin/` (the React Admin SPA), `frontend/` (the no-build
widget, test page and integration docs), `mcp_server/` (the admin-API MCP facade),
`scripts/` (preflight + checks) and `tests/`.

## Run

```bash
# Tests (stubs OpenAI + asyncpg; no real DB/API key needed)
SUPPORT_CHAT_TEST_MODE=1 python -m pytest -q

# Locally (needs a real Postgres + OpenAI key)
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@localhost:5432/supportchat
export OPENAI_API_KEY=sk-... SESSION_JWT_SECRET=$(openssl rand -hex 32)
uvicorn app.main:app --reload --port 8080   # test page at http://localhost:8080/
```

The database is the source of truth for runtime settings and the KB once the owner edits
them in the admin. There is **no seed step**: on a fresh/empty database, create topics and
their KB from the admin panel; runtime settings fall back to env → built-in defaults until
overridden.

## MCP — connect an AI agent to the admin API

`mcp_server/` is a Model Context Protocol server that lets an agent (Claude Code and
friends) work with a running deployment: read the runtime logs, inspect the assembled
prompt of any product, go through conversations and the retention agent's decisions, and —
with an `admin`-role key — edit the knowledge base, prompt variables, translations, site map
and settings.

It is a standalone **client** of this service: it holds a service API key (`sak_…`) and
calls the same `/admin/*` endpoints the panel does, so the key's role × scope is enforced
server-side and every write lands in the audit trail as `apikey:<name>`. It imports nothing
from the service and needs no dependency beyond `httpx`.

Set it up from the admin panel — **System → MCP** mints the key and prints the config
filled in — or by hand:

```bash
export SUPPORT_ADMIN_URL=https://your-deployment.example
export SUPPORT_ADMIN_KEY=sak_...            # System → MCP, or System → API keys
claude mcp add support-admin \
  -e SUPPORT_ADMIN_URL=$SUPPORT_ADMIN_URL -e SUPPORT_ADMIN_KEY=$SUPPORT_ADMIN_KEY \
  -- python3 -m mcp_server
```

The repo's `.mcp.json` already declares the server, so exporting the two variables before
starting a session is enough. Optional knobs: `SUPPORT_ADMIN_PRODUCT_ID` (default product
for tools called without one), `SUPPORT_ADMIN_ALLOW_WRITES=0` (hide the write tools
entirely), `SUPPORT_ADMIN_REDACT_PII=0` (stop masking player names/emails),
`SUPPORT_ADMIN_MAX_RESPONSE_CHARS`, `SUPPORT_ADMIN_TIMEOUT_SEC`.

Deleting users, products, sessions or media, rotating widget keys and reading secrets have
**no tool** — the agent's whole surface is the curated catalogue in `mcp_server/catalog.py`,
plus a read-only `admin_get` escape hatch bounded to `/admin/*`.

## Deploy

Railway via the single `Dockerfile` (`python:3.11-slim`) + `railway.toml`; the CMD reads
`$PORT`. Health check is `/healthz`.

### Two-service topology

The service has two halves — the HTTP API and the retention background pipeline — and they
want opposite things from a deploy: the web process must come up fast and never hold a
connection for minutes, the worker holds event **leases**, runs long model calls and is
sized for concurrency. `SERVICE_ROLE` picks which half a process is; both run from the
**same image**, so they can never drift apart.

| | Web service | Worker service |
|---|---|---|
| Start command | the Dockerfile CMD (`uvicorn app.main:app`) | `python -m app.worker` |
| `SERVICE_ROLE` | `web` | `worker` |
| `RETENTION_SCHEDULER_ENABLED` | `0` | `1` |
| Runs | HTTP, admin SPA, widget, Telegram webhook, the media normalizer | event drain, maintenance sweeps, send stage, quality judge |
| Health check | `/healthz` | `/healthz` on `$PORT` — per-loop heartbeats, 503 when a loop stops ticking |
| Volume | the retention media Volume mounts **here** | none |

Everything else — `DATABASE_URL`, the OpenAI keys, every secret — is identical on both.
Create the worker in the Railway dashboard from the same repo/Dockerfile with the start
command and env above.

**Leaving `SERVICE_ROLE` unset keeps the single-process behaviour** (`all`: HTTP plus every
background loop), so an existing deployment upgrades in place and only splits when you
create the worker service. Note that the media normalizer follows the **volume**, not the
pipeline: it owns the uploaded files, so it runs on the web process even with
`RETENTION_SCHEDULER_ENABLED=0`.

Shutdown is a drain, not a kill: `SIGTERM` raises the worker's stop flag, each loop wakes
out of its sleep, finishes the batch in flight and closes its leases within
`WORKER_DRAIN_TIMEOUT_SEC`. Zero-downtime deploys are not needed — an event whose lease is
never closed is simply reclaimed and retried.

### Memory footprint

Both halves idle at roughly 70–90 MB. Four things keep them there, and every one is easy
to undo by accident:

- **`MALLOC_ARENA_MAX=2` and `MALLOC_MMAP_THRESHOLD_=131072` are set in the `Dockerfile`**,
  not in Railway. Both processes hand multi-MB buffers to background threads (image
  decodes, media file reads, the base64 data URLs the vision calls send). Left at glibc's
  defaults, that memory is parked in up to `8 × ncores` per-thread arenas and never returned
  to the OS, so the process climbs to several hundred MB and stays there. These two
  variables are what make it fall back to idle instead. Keep them on the image.
- **The worker does not import `app.main`.** It takes its shared loops from
  `app/core/loops.py`; importing `app.main` would build the whole HTTP application — every
  router and model — in a process that serves no HTTP, for ~23 MB it never uses.
- **Uploaded images are decoded at reduced scale**, not at full resolution (see
  `media_normalizer.normalize_file`). A 24 MP phone photo costs ~28 MB to process instead
  of ~220 MB.
- **Outbound HTTP goes through one shared client per process** (`app/core/http.py`), so a
  Telegram send no longer builds an SSL context and a TLS connection per message. The two
  DNS-pinned calls (Player API pull, partner webhook) deliberately keep a client per call —
  see the module docstring; pooling them would reuse a TLS session across tenants.

### Watching memory

You do not have to read Railway's graph to see the trend:

- Both processes log a `process_memory` line once a minute
  (`MEMORY_LOG_INTERVAL_SEC`, `0` disables it) into `app_logs`, so **System → Logs** shows
  the curve for both halves — they are told apart by `role=`. The line carries current and
  peak RSS, the resident anonymous (heap) share, CPython's live block count, gc counters,
  task and thread counts, DB-pool occupancy and the entry count of six bounded in-process
  caches (log buffer, the two anti-spam maps, the subscription cache, the per-product
  OpenAI clients, the per-product settings cache). `rss_mb` rising while `blocks` stays
  flat is allocator/buffer retention (the first bullet above); both rising together means
  Python objects are accumulating; `anon_mb` says how much of the climb is heap rather
  than mapped files. Read them together — one number alone cannot tell those apart.
- `GET /admin/diagnostics/memory` returns the same snapshot on demand for the **web**
  process (global-scope admins only). The worker serves its own copy inside the `memory`
  block of its `/healthz`.
- For a real investigation, redeploy with `MEMORY_TRACEMALLOC=1` and the endpoint also
  returns the top allocation sites. It must be armed at boot to see anything, it roughly
  doubles allocation cost, and it is deliberately env-only — never an admin setting. Turn
  it off again afterwards. It covers **runtime** allocation; to see what the *imports*
  allocate, use CPython's own `PYTHONTRACEMALLOC=3` instead, which arms tracing before the
  first import. Either way the report itself takes seconds on a real heap, so the endpoint
  runs it on a worker thread rather than stalling the event loop.

If you see the web service climb into the hundreds of MB and plateau, check the first item
first — it is the usual cause, and it is a build-time setting, so it will not show up in
the Railway variables list.

### Process start

The image precompiles the stdlib and the application to `.pyc`
(`python -m compileall`, two `Dockerfile` layers). `python:3.11-slim` ships the stdlib as
source only, and `PYTHONDONTWRITEBYTECODE=1` means a container never caches the compile it
does at every start. Measured on the import set this service actually loads, precompiling
saves ~460 ms of stdlib parsing (588 ms → 125 ms) plus ~230 ms (web) / ~100 ms (worker) of
application parsing on every boot. Keep the application `compileall` as
the **last** step that touches `.py` files: a later `COPY` over a source file leaves its
`.pyc` stale, which is harmless (the import silently falls back to the source) but loses
the speedup. Never add `-O`/`PYTHONOPTIMIZE` to a start command without also compiling with
`-o 1` — the loader would look for `*.opt-1.pyc`, find nothing, and recompile everything.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Postgres DSN (`postgres://` is normalised to `postgresql://`). |
| `OPENAI_API_KEY` | yes | — | Primary OpenAI key. |
| `SESSION_JWT_SECRET` | yes | — | Signs front-end session tokens (and the root of the fallback chain below). On a real deployment it **must be ≥32 chars** (e.g. `openssl rand -hex 32`) or the app refuses to boot. |
| `OPENAI_API_KEY_FALLBACK` | no | — | Second key for the two-key failover. Both env keys are the deploy-level fallback for products without their own keys (set per product in Structure). |
| `OPENAI_MODEL` | no | `gpt-5.6-luna` | Default model (the cheapest GPT-5.6 reasoning tier). Boot default of the hot-reloaded `model` settings group, like every OpenAI knob below — change it live in the admin. |
| `OPENAI_REQUEST_TIMEOUT_SEC` / `OPENAI_KEY_SWITCH_TIMEOUT_SEC` | no | `30` / `15` | Interactive (chat) request timeout and when to race the fallback key. |
| `OPENAI_AGENT_REQUEST_TIMEOUT_SEC` / `OPENAI_AGENT_KEY_SWITCH_TIMEOUT_SEC` | no | `90` / `0` | Same pair for the proactive retention agent (background: race off — the fallback engages only on a real error). |
| `OPENAI_REVIEW_REQUEST_TIMEOUT_SEC` / `OPENAI_REVIEW_KEY_SWITCH_TIMEOUT_SEC` | no | `120` / `0` | Same pair for the quality-review judge. |
| `OPENAI_MEDIA_REQUEST_TIMEOUT_SEC` / `OPENAI_MEDIA_KEY_SWITCH_TIMEOUT_SEC` | no | `120` / `0` | Same pair for photo/video cataloguing (vision). |
| `OPENAI_MAX_ATTEMPTS` | no | `3` | Retry attempts per completion (transient errors, exponential backoff). |
| `OPENAI_REASONING_EFFORT` / `OPENAI_VERBOSITY` | no | `low` / `low` | Reasoning-model params; an empty string omits the param so the model default applies. |
| `OPENAI_MAX_OUTPUT_TOKENS` | no | `2000` | Sent as `max_completion_tokens`; reasoning tokens count against it, so it ships higher than a non-reasoning model would need. |
| `OPENAI_MAX_CONCURRENT_PER_KEY` | no | `4` | Per-key concurrency semaphore. |
| `APP_ENV` | no | `development` | Deployment environment. Secret hygiene is **fail-closed**: on a real deployment — `APP_ENV=production`/`prod` **or a non-local `DATABASE_URL`** — the app **refuses to boot** if `ADMIN_JWT_SECRET`, `SECRETS_MASTER_KEY`, or `TELEGRAM_WEBHOOK_SECRET` is unset and would reuse `SESSION_JWT_SECRET`, or if any secret is shorter than 32 chars. Only a genuinely local run (loopback DB, not production) stays lenient — set `APP_ENV=development` to opt into leniency against a remote DB. |
| `ADMIN_JWT_SECRET` | no | `SESSION_JWT_SECRET` | Signs admin tokens; set a distinct **≥32-char** value (**required on a real deployment** — see `APP_ENV`). |
| `ADMIN_TOKEN_TTL_MIN` | no | `10080` | Admin login **inactivity window** (minutes; default 1 week). The session **slides** — an active operator's token is auto-renewed past its half-life, so daily use never logs you out, while an account left untouched for this long expires. Also a live `general` settings knob (admin **Settings** tab). |
| `SECRETS_MASTER_KEY` | no | `SESSION_JWT_SECRET` | Master key encrypting per-product secrets (OpenAI keys, handshake secrets) at rest. Set a distinct strong **≥32-char** value (**required on a real deployment**); rotating it invalidates stored product secrets (re-enter them in the admin). |
| `WIDGET_HANDSHAKE_SECRET` | no | — | Deploy-level HMAC secret for signed host-site `user_context`. Applies to the **default product only** — every other product must set its own handshake secret (Structure tab), so a deploy-wide secret can't sign player data for another partner's casino. Neither set ⇒ dev mode. |
| `WIDGET_HANDSHAKE_MAX_AGE_SEC` | no | `300` | Max age (seconds) tolerated for a signed handshake blob — defence in depth alongside the payload's explicit `exp`. |
| `TURNSTILE_SECRET` | no | — | Deploy-level fallback Cloudflare Turnstile secret, verified at session create. A product's own secret (Structure tab, stored encrypted) takes precedence; neither set ⇒ the check is skipped. Advisory: a missing client token or a verifier outage also skips (fail-open) — only an explicit "invalid token" verdict blocks. |
| `TURNSTILE_SITE_KEY` | no | — | Deploy-level fallback Turnstile **site key** (create the Turnstile widget as **Invisible** in the Cloudflare dashboard), served to the chat widget via `GET /api/chat/i18n`. Fallback pair to `TURNSTILE_SECRET`: each product should carry its own per-domain site key + secret (Structure tab); these env values apply only to products without their own. |
| `CONTACT_FORM_URL` | no | — | Optional deploy-level fallback URL behind the escalation contact button — applies to the **default product only**, never to other products. The URL's real home is the admin Translations tab (`contact_url`, per product/per language); a value stored by old builds in the DB is auto-migrated there on boot. |
| `DEFAULT_LANGUAGE` / `SUPPORTED_LANGUAGES` | no | `en` / `en,es,ru,tr,pt` | Language defaults. |
| `SESSION_TTL_HOURS` | no | `24` | Chat-session token lifetime (also a `general` settings knob). |
| `MAX_MESSAGES_PER_SESSION` | no | `15` | The SOFT per-session message cap (the cap notice; hard close at ×2). Also a `general` settings knob. |
| `HISTORY_MAX_TURNS` | no | `15` | Recent turns fed to the prompt (the full transcript is always persisted). |
| `MAX_INPUT_CHARS` | no | `500` | Player-message length cap. |
| `RATE_LIMIT_WINDOW_SEC` / `RATE_LIMIT_MAX_PER_IP` | no | `600` / `20` | Widget anti-spam window and per-IP message allowance. |
| `MESSAGE_COOLDOWN_SEC` | no | `2` | Minimum pause between two messages from one session. |
| `LOW_CONTENT_BLOCK` / `MIN_MEANINGFUL_CHARS` | no | `true` / `2` | Low-content guard: a message must carry at least N distinct letters/digits to reach the model (junk gets a model-free nudge). |
| `INJECTION_HARD_BLOCK` | no | `true` | Reject known prompt-injection patterns with HTTP 400 before the model call (always audited either way). |
| `BODY_MAX_BYTES` | no | `65536` | Request-body cap middleware (chunked bodies are rejected with 411). |
| `ADMIN_LOGIN_RATE_LIMIT` | no | `10` | Per-IP `/admin/login` attempts per window (PBKDF2 brute-force budget). |
| `CORS_ALLOW_ORIGINS` | no | `*` | Comma-separated allowed origins (restrict in prod). |
| `TRUSTED_PROXY_COUNT` | no | `1` | Trusted proxy hops to read from the right of `X-Forwarded-For`. |
| `TRUSTED_PROXY_IPS` | no | private/reserved ranges | Comma-separated immediate proxy IPs/CIDRs whose `X-Forwarded-For` may be trusted. Defaults to the private/reserved ranges (RFC1918 + CGNAT + loopback/ULA), which is correct on Railway and most PaaS — the platform proxy connects from a private peer IP that a public client cannot forge. Tighten to your edge's exact CIDR if you know it. |
| `TELEGRAM_WEBHOOK_SECRET` | no | `SESSION_JWT_SECRET` | Retention bot: verifies the `X-Telegram-Bot-Api-Secret-Token` header on `/telegram/webhook/{secret}` (NOT in the URL). Set a distinct **≥32-char** value (**required on a real deployment** — see `APP_ENV`). |
| `DB_CONNECT_TIMEOUT_SEC` | no | `10` | Cap (seconds) on establishing a new Postgres connection, so a down DB fails fast instead of hanging on connect. |
| `DB_ACQUIRE_TIMEOUT_SEC` | no | `10` | Cap (seconds) on waiting for a free pooled connection on the hot request paths — pool exhaustion surfaces as a retryable error, not an unbounded hang. |
| `DB_HEALTHCHECK_TIMEOUT_SEC` | no | `5` | Cap (seconds) on the `/healthz` DB probe. `/healthz` is a liveness probe (200 while the process is up, even if the DB is momentarily down) so a DB blip can't drive a restart loop; add `?deep=1` for a strict readiness check that 503s when the DB is down. |
| `SERVICE_ROLE` | no | `all` | Which half this process is: `web` (HTTP only — the background pipeline belongs to the worker service), `worker` (`python -m app.worker`: the background loops only), or `all` (single process, the pre-split behaviour). See § "Two-service topology". |
| `WORKER_DRAIN_TIMEOUT_SEC` | no | `25` | Seconds a shutting-down worker may spend finishing the batch in flight and closing its event leases. Railway `SIGKILL`s 30s after `SIGTERM`, so stay under that. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | no | `1` / `30` on the worker, `10` elsewhere | Postgres pool bounds. The worker runs many player shards concurrently; the web process serves requests. |
| `HTTP_MAX_CONNECTIONS` / `HTTP_MAX_KEEPALIVE` | no | `100` / `20` | Bounds on the shared outbound HTTP pool (`app/core/http.py`) used by the Telegram Bot API, Turnstile and Customer.io calls. |
| `HTTP_KEEPALIVE_EXPIRY_SEC` | no | `30` | How long an idle outbound connection is kept for reuse. `0` turns reuse off entirely (the escape hatch if a provider proves hostile to pooled connections) — the SSL context stays shared either way. |
| `HTTP_DEFAULT_TIMEOUT_SEC` | no | `15` | Fallback deadline for the shared client. Every call site passes its own per-request timeout; this only applies if one ever forgets. |
| `MEMORY_LOG_INTERVAL_SEC` | no | `60` | Cadence of the `process_memory` line both roles log into `app_logs` (visible in System → Logs, `role=` tells the halves apart). `0` disables it. |
| `MEMORY_TRACEMALLOC` / `MEMORY_TRACEMALLOC_FRAMES` | no | `false` / `3` | Arm `tracemalloc` at boot so `GET /admin/diagnostics/memory` can report top allocation sites. Deploy-level switch, **not** an admin setting; it roughly doubles allocation cost, so turn it on for an investigation and off again. |
| `OPENAI_BREAKER_FAIL_THRESHOLD` | no | `5` | Consecutive fully-failed completions before the OpenAI circuit breaker opens and further calls fail fast (returning the localized nudge in ms) instead of each paying the full failover cost during an outage. `0` disables the breaker. Keyed per key source, so one product's bad key can't trip it for everyone. |
| `OPENAI_BREAKER_COOLDOWN_SEC` | no | `30` | How long the breaker stays open before allowing one half-open trial request to probe recovery. |
| `PUBLIC_BASE_URL` | no | — | Retention bot: public base URL of this service (e.g. `https://chat.example.com`), used to build the webhook URL when registering it with Telegram. Required to auto-register the webhook from the admin. |
| `RETENTION_MEDIA_DIR` | no | `./media` | Retention bot: on-disk path for uploaded media. On Railway set it to the mount path of an attached **Volume** so photos survive redeploys. |
| `RETENTION_MAX_UPLOAD_BYTES` | no | `536870912` | Max size (bytes) of one retention media-upload request — the whole batch, photos AND videos (the JSON body cap is far smaller; the media-upload path uses this instead). Default 512 MiB, sized for raw phone-video originals; the normalizer transcodes them down after upload. |
| `RETENTION_MAX_PHOTO_BYTES` | no | `10485760` | Per-file cap for an uploaded photo (default 10 MiB). Enforced server-side by byte size and pre-checked in the admin Media tab (with the resolution/duration caps) before the upload starts. |
| `RETENTION_MAX_PHOTO_SIDE_PX` | no | `8000` | Max longest side (px) of an uploaded photo; larger is rejected in the Media tab. Photos are downscaled to `RETENTION_MEDIA_MAX_SIDE_PX` on delivery anyway, so this only guards against absurd/decompression-bomb originals. |
| `RETENTION_MAX_VIDEO_BYTES` | no | `104857600` | Per-file cap for an uploaded video (default 100 MiB). Enforced server-side by byte size and pre-checked in the Media tab. |
| `RETENTION_MAX_VIDEO_DURATION_SEC` | no | `60` | Max duration (seconds) of an uploaded video; longer is rejected in the Media tab (checked in the browser before upload). |
| `RETENTION_NONCE_TTL_SEC` | no | `120` | Retention deeplink nonce lifetime (also a `retention` settings knob). |
| `RETENTION_PROFILE_PULL_TTL_SEC` | no | `3600` | If a profile snapshot is older than this and the product has a Player API, pull a fresh profile before a turn (also a `retention` settings knob). |
| `RETENTION_SESSION_IDLE_MINUTES` | no | `360` | Minutes of inactivity before a Telegram chat closes; the player's next message starts a fresh chat (0 = never; also a `retention` settings knob). |
| `RETENTION_CARRY_CONTEXT_TURNS` | no | `10` | Trailing turns of the previous (closed) Telegram chat shown to the model on the first turn of the fresh one, so a returning player is greeted with continuity (0 = off; also a `retention` settings knob). |
| `RETENTION_STAGE_UP_NOTIFY` | no | `true` | When a player actually unlocks the next photo/closeness stage, the persona follows up with a short celebratory note (persisted with its trigger so she can later explain it); also a `retention` settings knob, `stage_up_notify`. |
| `RETENTION_MAX_STAGE` | no | `5` | Top explicitness stage a photo can gate on / a player can reach in the Telegram retention bot; photo `stage` and stage progression are clamped to `1..RETENTION_MAX_STAGE` (also a `retention` settings knob, `max_stage`). |
| `RETENTION_PLAY_REMINDER_EVERY_MSGS` | no | `8` | Every N-th assistant reply in a Telegram retention chat weaves in a light in-context invitation to play, with a one-tap site-map button picked by intent (0 = off; also a `retention` settings knob, `play_reminder_every_msgs`). |
| `RETENTION_DAILY_PHOTO_CAP` | no | `5` | Photos per player per day in the Telegram bot (also a `retention` settings knob). |
| `RETENTION_PROACTIVE_COOLDOWN_MSGS` | no | `5` | Player messages between two proactive photo offers inside a dialogue. |
| `RETENTION_CANDIDATE_LIST_SIZE` | no | `6` | Photo candidates shown to the model when it picks what to send. |
| `RETENTION_STAGE_ADVANCE_MIN_HOURS` | no | `12` | Minimum hours between two closeness-stage advances. |
| `RETENTION_INTRO_PHOTO_ENABLED` | no | `true` | Introduction photo: a brand-new player (never received a photo) gets one proactively within his first meaningful messages, with a model-written "this is me — let's get to know each other" caption, so he learns early that chatting comes with photos (also a `retention` settings knob, `intro_photo_enabled`). |
| `RETENTION_INTRO_PHOTO_WITHIN_MSGS` | no | `3` | How many of the player's first meaningful messages count as the acquaintance window for the introduction photo (also a `retention` settings knob, `intro_photo_within_msgs`). |
| `RETENTION_MAX_REPLY_PARTS` | no | `3` | Default for `retention.max_reply_parts` — max Telegram messages one model reply may be split into (blank-line burst delivery; extra chunks collapse into the last part, `1` = never split). |
| `RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC` | no | `3600` | How often the media-normalizer sweep runs (one deploy-wide loop). Normalization is always-on and code-owned — there is no admin knob or on/off switch (the whole sweep is still gated by `RETENTION_SCHEDULER_ENABLED`). |
| `RETENTION_MEDIA_MAX_SIDE_PX` | no | `2560` | Longest photo side after normalization (matches the ~2560 px Telegram re-compresses photos to). Code-owned, no admin knob. |
| `RETENTION_MEDIA_WEBP_QUALITY` | no | `90` | WebP quality of the normalized photo. Code-owned, no admin knob. |
| `RETENTION_MEDIA_VIDEO_MAX_SIDE_PX` | no | `1920` | Longest side of a normalized retention VIDEO (uploads are re-encoded to Telegram-friendly MP4/H.264 by ffmpeg right after upload). 1920 keeps a vertical 1080×1920 phone reel at native resolution — the CRF re-encode still crushes a bloated source bitrate (a 50 MB 17s reel lands around 5–9 MB). |
| `RETENTION_MEDIA_VIDEO_CRF` | no | `26` | H.264 CRF quality target for normalized retention videos (lower = better quality / bigger file; ~-6 CRF doubles the size — watch the 50 MB Telegram bot cap on long clips). |
| `RETENTION_MEDIA_VIDEO_PRESET` | no | `medium` | x264 speed/compression preset for normalized retention videos (`ultrafast`…`veryslow`). A slower preset squeezes more quality out of the same CRF at a longer encode time; transcodes run in the background, so `medium` favours quality over speed. |
| `TG_RATE_LIMIT_MAX_PER_USER` | no | `60` | Retention bot: max Telegram messages from one player per rate-limit window (`RATE_LIMIT_WINDOW_SEC`). Higher than the widget's per-IP limit because a live chat is faster; also an `antispam` settings knob (`tg_rate_limit_max_per_user`). |
| `RETENTION_SCHEDULER_ENABLED` | no | `true` | Whether this instance runs the retention-agent worker loop at all (deploy-level switch, not a setting). |
| `RETENTION_WORKER_INTERVAL_SEC` | no | `5` | Default for `retention.worker_interval_sec` — how often the agent worker drains the event queues (hot setting, read live each tick; clamped 5..3600; advisory-locked + atomic event claim, so multiple instances never double-send). |
| `RETENTION_V2_ENABLED` | no | `true` | Default for `retention.v2_enabled` — the per-product agent switch. Off ⇒ no proactive messages at all (the dialogue bot still answers). The historic `V2` name survives for stored-override compatibility. |
| `RETENTION_V2_DRY_RUN` | no | `true` | Default for `retention.v2_dry_run` — shadow mode: the agent decides and logs to the decision ledger but sends nothing until the owner turns it off. |
| `RETENTION_V2_SEND_DELAY_MIN_SEC` / `RETENTION_V2_SEND_DELAY_MAX_SEC` | no | `300` / `900` | Humanizing send delay: an event is reacted to a per-event random min..max seconds after it arrived, never instantly (an instant thank-you reads as transaction surveillance). Both `0` = react immediately; the admin «Process queue now» button always bypasses the delay. Also `retention` settings knobs. |
| `RETENTION_IDLE_PINGS_ENABLED` | no | `true` | Default for `retention.idle_pings_enabled` — the agent's inactivity trigger (the Idle pings rules ladder, «quiet N days → the persona writes first»). Off ⇒ the agent reacts to casino events only. |
| `RETENTION_IDLE_SWEEP_INTERVAL_SEC` | no | `600` | Default for `retention.idle_sweep_interval_sec` — how often the idle-rules ladder is re-evaluated per product (the rules move on a scale of days, so 10 min is plenty; the admin «Run now» bypasses it). |
| `RETENTION_V2_DAILY_BUDGET_USD` | no | `5.0` | Default for `retention.v2_daily_budget_usd` — the per-product daily AI budget for agent decisions+sends; reached ⇒ the loop goes quiet until tomorrow (0 = no budget). |
| `RETENTION_PING_DAILY_CAP` | no | `3` | Hard per-player cap: at most this many proactive messages a day, no matter how many events fire (also a `retention` settings knob, like every guard below). |
| `RETENTION_PING_MIN_GAP_HOURS` | no | `2` | Minimum gap between any two proactive messages to the same player (0 = off). |
| `RETENTION_V2_SAME_EVENT_COOLDOWN_HOURS` | no | `5` | One reaction per event TYPE per player per window (a webhook retry or five deposits get one note). `0` = off, handy while testing the pipeline with repeated simulator events. |
| `RETENTION_QUIET_HOURS_START` / `RETENTION_QUIET_HOURS_END` | no | `22` / `9` | Local quiet hours — no proactive messages inside the window (equal values = no quiet hours). |
| `RETENTION_QUIET_HOURS_UTC_OFFSET` | no | `0` | Shifts "local" from UTC for the product's audience when evaluating quiet hours (and the prompt's current-time block). |
| `RETENTION_PING_BATCH_SIZE` | no | `30` | Max events processed per product per worker sweep (cost guard). |
| `RETENTION_SILENT_NOTIFICATIONS` | no | `false` | Default for `retention.silent_notifications` — deliver PROACTIVE Telegram messages silently (no sound/vibration); dialogue replies always notify normally. |
| `RETENTION_SUB_CACHE_TTL_SEC` | no | `600` | Default for `retention.subscription_cache_ttl_sec` — how long a positive channel-subscription check is cached (0 = re-check on every message). |
| `RETENTION_V2_LOSS_COMFORT_HOURS` | no | `24` | Default for `retention.v2_loss_comfort_hours` — after a big-loss signal: empathetic tone only, no play CTA, no photos, no links for this many hours. |
| `RETENTION_V2_LOSS_HIGH_USD` | no | `100.0` | Default for `retention.v2_loss_high_usd` — 24h net loss that marks the player critical and starts the comfort window. |
| `RETENTION_OUTCOME_REPLY_WINDOW_HOURS` | no | `48` | Outcome attribution: how long after a delivered touch a player message still counts as a reply. Deploy-level (not per product) — it defines what the stored numbers MEAN, so changing it per tenant would make the history incomparable. |
| `RETENTION_OUTCOME_CONVERSION_WINDOW_HOURS` | no | `72` | Same, for coming back to the casino / depositing after a touch (a deposit follows with more lag than a chat reply). |
| `RETENTION_OUTCOME_SWEEP_INTERVAL_SEC` | no | `300` | How often the attribution sweep settles open outcome rows per product (it rides the retention worker tick; this only paces it). |
| `RETENTION_OUTCOME_SWEEP_BATCH` | no | `500` | Max open outcome rows one sweep pass settles per product. |
| `RETENTION_HOLDOUT_PCT` | no | `15` | Orchestrator (measurement): share of players deterministically held out of ALL proactive touches (the control group uplift is measured against). 0 = off. Ships at 15 by business decision. |
| `RETENTION_HOLDOUT_SALT` | no | `default` | The experiment salt — rotating it re-buckets every player (a new experiment). |
| `RETENTION_RG_ENABLED` | no | `true` | RG guard (responsible gaming) — ON by default; self-excluded players never get proactive touches or offers. |
| `RETENTION_RG_REQUIRE_CONSENT` | no | `false` | Require `marketing_consent=true` for game touches (Tier-1 markets). OFF at MVP. |
| `RETENTION_RG_UNKNOWN_STATUS_POLICY` | no | `warn` | How to treat a player whose RG status was never reported: `warn` (pass with an audit mark) or `block`. |
| `RETENTION_RG_DIALOGUE_SUPPRESSION` | no | `true` | Strip game CTAs from dialogue replies to RG-restricted players. |
| `RETENTION_ADAPTIVE_FREQUENCY_ENABLED` | no | `false` | Priority/cohort-aware frequency caps (P1/P2 never cut; email rides its own budget). OFF = the static guards apply unchanged. |
| `RETENTION_SMART_SEND_TIME_ENABLED` | no | `false` | Shift non-urgent touches into the player's active hours (their timezone from the casino, else the product offset). |
| `RETENTION_SCORING_ENABLED` | no | `false` | Player scoring: dormancy cohorts, RFM, value tiers, VIP segment. |
| `RETENTION_OFFERS_ENABLED` | no | `false` | Offer engine master switch (grants real bonuses by their bonus-CMS IDs via the casino endpoint). |
| `RETENTION_OFFER_DRY_RUN` | no | `true` | Offers decide + log without calling the casino. |
| `RETENTION_OFFER_DAILY_BUDGET_USD` | no | `0` | Daily stimulus budget; 0 = granting blocked (safe default). |
| `RETENTION_JOURNEYS_ENABLED` | no | `false` | Journey engine (multi-step trajectories; each journey still seeds draft + dry-run). |
| `RETENTION_MULTICHANNEL_ENABLED` | no | `false` | Channel router beyond Telegram (email via Customer.io, delegated push/in-app). Strict per-channel opt-in. |
| `RETENTION_UPLIFT_WINDOW_DAYS` | no | `28` | Measurement: the uplift report window (deploy constant, keeps history comparable). |
| `RETENTION_SST_MAX_SHIFT_HOURS` / `RETENTION_SST_MIN_SAMPLE` | no | `2` / `20` | Smart send time: max shift from the hinted send moment, and the observations an activity profile needs before it is trusted. |
| `RETENTION_ACTIVITY_PROFILE_SWEEP_INTERVAL_SEC` | no | `3600` | How often player activity profiles are rebuilt from the event feed. |
| `RETENTION_SCORING_SWEEP_INTERVAL_SEC` / `RETENTION_RFM_WINDOW_DAYS` | no | `3600` / `30` | Scoring sweep cadence and the RFM window. |
| `RETENTION_OFFER_COOLDOWN_HOURS` / `RETENTION_OFFER_LIFETIME_CAP` | no | `72` / `0` | Per-player offer cooldown and lifetime cap (0 = unlimited). |
| `RETENTION_OFFER_GRANT_TIMEOUT_SEC` | no | `10` | Timeout of the outbound offer-grant call to the casino. |
| `RETENTION_JOURNEYS_DRY_RUN_DEFAULT` | no | `true` | New journeys seed as dry-run. |
| `RETENTION_JOURNEY_STEP_SWEEP_INTERVAL_SEC` / `RETENTION_JOURNEY_MAX_ACTIVE_PER_PLAYER` | no | `300` / `3` | Due-step drain cadence and the concurrent-journeys cap per player. |
| `RETENTION_JOURNEY_REENTRY_COOLDOWN_DAYS` | no | `30` | Default gap before a SCHEDULED journey may take the same player again. Its candidates come from live state (a dormancy cohort lasts days), and the one-active-enrollment index is partial, so without a gap a finished enrollment is re-created — and re-sent — on the very next sweep. A journey may override it with `metadata.reentry_cooldown_days`; the weekly (`day_of_week`) and cashier-abandonment trigger shapes derive their own. |
| `RETENTION_SCENARIO_AUTOSEED` | no | `false` | Auto-seed the starter scenario packs on boot (manual seed button otherwise). |
| `RETENTION_ABANDONMENT_DELAY_HOURS` | no | `2` | Cashier-abandonment timer (`deposit_initiated` without a confirm). |
| `RETENTION_CHANNEL_AUTO_PRIORITY` | no | `push,in_app,email` | Router fallback order when the step says `auto`. |
| `RETENTION_DELIVERY_RETRY_ENABLED` / `RETENTION_PUSH_DELIVERY_TIMEOUT_SEC` | no | `true` / `10` | Delivery-ledger backoff retries (1m/5m/30m) and the delegated push/in-app call timeout. |
| `RETENTION_EVENT_LEASE_SEC` | no | `300` | Event pipeline: how long a claimed event may stay in flight before the reclaimer assumes the worker died and returns it to the queue. Must exceed the agent model timeout (90s) with margin — a lease expiring mid-decision means the event is processed twice. |
| `RETENTION_EVENT_MAX_ATTEMPTS` / `RETENTION_EVENT_BACKOFF_BASE_SEC` | no | `5` / `30` | Retries before an event is dead-lettered (visible + requeueable in the admin) and the base of the exponential backoff between them. |
| `RETENTION_WORKER_PRODUCT_CONCURRENCY` / `RETENTION_WORKER_PLAYER_CONCURRENCY` | no | `4` / `8` | How many products drain in parallel, and how many players in parallel inside one product. One player's events are always strictly serial (two concurrent decisions race the guard counters and produce two messages). |
| `RETENTION_AGENT_MODEL_CONCURRENCY` | no | `8` | Fleet-wide ceiling on concurrent background model calls, so an event burst cannot open hundreds of completions at once. |
| `RETENTION_QUEUE_DEGRADE_P3_SEC` / `_P2_SEC` / `_IDLE_SEC` | no | `300` / `900` / `3600` | Backpressure ladder keyed on queue lag (age of the oldest event that is OVERDUE — past its humanizing send delay and any retry backoff): shed the low priority lanes, then everything but the transactional ones, then pause the idle ladder for that product until the lag recovers. Each rung reads the lag of the lanes it does NOT shed, so a lane backing up cannot argue for its own shedding. |
| `RETENTION_QUEUE_BYPASS_STATE_EVENTS` | no | `true` | Store high-volume "state food" (spins, session pings) COMPLETE without queueing it, so queue depth tracks decision work rather than casino traffic. |
| `RETENTION_ACTIVITY_DEBOUNCE_SEC` | no | `60` | Debounce for the activity-timestamp bridge — the busiest write in the system, and re-stamping "active" seconds later buys nothing. |
| `RETENTION_SEND_WORKER_ENABLED` | no | `false` | Default for `retention.send_worker_enabled` — the send stage as its own worker (a decision enqueues, the send worker delivers under a token bucket). Off = decisions send inline exactly as before. |
| `RETENTION_SEND_CONCURRENCY` / `RETENTION_SEND_LEASE_SEC` / `RETENTION_SEND_BATCH_SIZE` | no | `8` / `120` / `50` | Send-stage parallelism, lease length and how many queued touches one pass claims. |
| `RETENTION_SEND_MAX_ATTEMPTS` | no | `6` | Dead-letter ceiling for a delivery, the counterpart of `RETENTION_EVENT_MAX_ATTEMPTS`. Without one a transiently-failing row (a rotated bot token, a partner endpoint that is down) is re-claimed on the saturated backoff rung forever. |
| `RETENTION_TELEGRAM_RATE_PER_SEC` / `RETENTION_TELEGRAM_BURST` / `RETENTION_TELEGRAM_CHAT_RATE_PER_SEC` | no | `25` / `25` / `1` | Token bucket held in Postgres (so the limit holds however many workers run): per bot and per chat, sized under Telegram's own limits so a broadcast drains instead of collecting 429s. |
| `RETENTION_EMAIL_RATE_PER_SEC` | no | `50` | Same bucket for the email channel. |
| `RETENTION_MAINTENANCE_INTERVAL_SEC` | no | `30` | How often the maintenance loop looks for work (lease reclaim first, then the paced sweeps below). Each sweep is paced per product through `retention_worker_jobs`, so its interval survives a deploy and holds across workers. |
| `RETENTION_ATTRIBUTION_INTERVAL_SEC` / `RETENTION_SCORING_INTERVAL_SEC` / `RETENTION_PROFILE_INTERVAL_SEC` / `RETENTION_JOURNEY_INTERVAL_SEC` | no | `300` / `900` / `3600` / `120` | Per-product pacing of the attribution, scoring, activity-profile and journey sweeps. |
| `RETENTION_EVENT_KEEP_DAYS` / `RETENTION_EVENT_KEEP_DAYS_STATE` | no | `90` / `14` | Split retention for the event log: state food is 90%+ of the rows and worth nothing once the resolver's windows have passed. |
| `QUALITY_REVIEW_ENABLED` | no | `true` | Default for `general.quality_review_enabled` — the LLM-as-judge pass that scores finished conversations (both facades). The verdicts feed the admin Quality page; the judge never changes anything itself. |
| `QUALITY_REVIEW_DAILY_MAX` | no | `100` | Default for `general.quality_review_daily_max` — cost cap: reviews per product per UTC day (0 = pause). |
| `QUALITY_REVIEW_MIN_MESSAGES` | no | `4` | Default for `general.quality_review_min_messages` — shorter conversations are skipped (nothing to judge in a one-liner). |
| `QUALITY_REVIEW_INTERVAL_SEC` | no | `1800` | How often the review worker sweeps (deploy constant; gated by `RETENTION_SCHEDULER_ENABLED` like every background worker). |
| `QUALITY_REVIEW_IDLE_MINUTES` | no | `60` | How long a conversation must be quiet to count as "finished" when it was never explicitly closed (a widget chat is usually abandoned, a Telegram chat has no close button). |
| `EXPOSE_API_DOCS` | no | — | Set to `1` to publish `/docs`, `/redoc` and `/openapi.json` (they describe the whole surface, `/admin` included, so they are **disabled by default**). Dev/stage only. |

The retention bot's per-product config (bot token, channel, player-API key) lives on the
product row in the admin **Retention · Telegram** section, not in env; secrets there are
encrypted at rest via `SECRETS_MASTER_KEY`. Photo-progression / limit knobs
(`daily_photo_cap`, `stage_advance_msgs`, `max_stage_by_tier`, …) live in the `retention`
settings group (defaults seeded from `RETENTION_*` env). Setup checklist: the admin
**Retention · Telegram → Setup guide** tab; architecture: the retention section in `CLAUDE.md`.

Most operational knobs (rate limits, cooldowns, model tuning, escalation thresholds,
session TTL, body cap, etc.) are tunable live from the admin **Settings** tab and only need
an env var to seed an initial value. True secrets stay in env. See `app/core/config.py` for
the full list.

The OpenAI **timeouts are per call purpose** (System settings → Model): the live chat
(support widget + Telegram replies) keeps the short interactive timeout and races the
fallback key when the primary goes silent, while the background blocks — the proactive
agent, the quality-review judge and photo/video cataloguing — get their own, longer request
timeouts and ship with the key race **off** (`0`), since nobody is waiting on them and a
speculative second call would just bill the same work twice.
