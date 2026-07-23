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
> sync (signed handshake, lazy pull, push webhook, activity timestamps),
> **`/integration-chat-api`** documents the public Chat API + the mandatory
> client logic for a custom UI, **`/integration-telegram`** covers the Telegram
> retention bot (deeplink contract, the proactive agent, admin setup; its
> step-by-step setup checklist lives in the admin panel — **Retention ·
> Telegram → Setup guide**), and **`/integration-admin`** documents integrating
> an external "master" admin panel with the `/admin` API (roles model, service
> API keys, scoping, endpoint reference).

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
  per product via the header switcher. **Retention analytics** live in the Retention
  section (`GET /admin/retention/overview` / `funnel` / `timeseries`): lifetime +
  in-range KPIs (engagement, photos, proactive sends + reply rate, cost, stage
  distribution),
  the entry funnel (deeplinks → starts → new users → subscribed → engaged → photo
  receivers → handoffs) and daily series.
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
| `SESSION_JWT_SECRET` | yes | — | Signs front-end session tokens (and the root of the fallback chain below). On a real deployment it **must be ≥32 chars** (e.g. `openssl rand -hex 32`) or the app refuses to boot. |
| `OPENAI_API_KEY_FALLBACK` | no | — | Second key for the two-key failover. Both env keys are the deploy-level fallback for products without their own keys (set per product in Structure). |
| `APP_ENV` | no | `development` | Deployment environment. Secret hygiene is **fail-closed**: on a real deployment — `APP_ENV=production`/`prod` **or a non-local `DATABASE_URL`** — the app **refuses to boot** if `ADMIN_JWT_SECRET`, `SECRETS_MASTER_KEY`, or `TELEGRAM_WEBHOOK_SECRET` is unset and would reuse `SESSION_JWT_SECRET`, or if any secret is shorter than 32 chars. Only a genuinely local run (loopback DB, not production) stays lenient — set `APP_ENV=development` to opt into leniency against a remote DB. |
| `ADMIN_JWT_SECRET` | no | `SESSION_JWT_SECRET` | Signs admin tokens; set a distinct **≥32-char** value (**required on a real deployment** — see `APP_ENV`). |
| `ADMIN_TOKEN_TTL_MIN` | no | `10080` | Admin login **inactivity window** (minutes; default 1 week). The session **slides** — an active operator's token is auto-renewed past its half-life, so daily use never logs you out, while an account left untouched for this long expires. Also a live `general` settings knob (admin **Settings** tab). |
| `SECRETS_MASTER_KEY` | no | `SESSION_JWT_SECRET` | Master key encrypting per-product secrets (OpenAI keys, handshake secrets) at rest. Set a distinct strong **≥32-char** value (**required on a real deployment**); rotating it invalidates stored product secrets (re-enter them in the admin). |
| `WIDGET_HANDSHAKE_SECRET` | no | — | Deploy-level HMAC secret for signed host-site `user_context`. A product's own handshake secret (Structure tab) takes precedence. Neither set ⇒ dev mode. |
| `WIDGET_HANDSHAKE_MAX_AGE_SEC` | no | `300` | Max age (seconds) tolerated for a signed handshake blob — defence in depth alongside the payload's explicit `exp`. |
| `TURNSTILE_SECRET` | no | — | Deploy-level fallback Cloudflare Turnstile secret, verified at session create. A product's own secret (Structure tab, stored encrypted) takes precedence; neither set ⇒ the check is skipped. Advisory: a missing client token or a verifier outage also skips (fail-open) — only an explicit "invalid token" verdict blocks. |
| `TURNSTILE_SITE_KEY` | no | — | Deploy-level fallback Turnstile **site key** (create the Turnstile widget as **Invisible** in the Cloudflare dashboard), served to the chat widget via `GET /api/chat/i18n`. Fallback pair to `TURNSTILE_SECRET`: each product should carry its own per-domain site key + secret (Structure tab); these env values apply only to products without their own. |
| `CONTACT_FORM_URL` | no | — | Optional deploy-level fallback URL behind the escalation contact button — applies to the **default product only**, never to other products. The URL's real home is the admin Translations tab (`contact_url`, per product/per language); a value stored by old builds in the DB is auto-migrated there on boot. |
| `DEFAULT_LANGUAGE` / `SUPPORTED_LANGUAGES` | no | `en` / `en,es,ru,tr,pt` | Language defaults. |
| `CORS_ALLOW_ORIGINS` | no | `*` | Comma-separated allowed origins (restrict in prod). |
| `TRUSTED_PROXY_COUNT` | no | `1` | Trusted proxy hops to read from the right of `X-Forwarded-For`. |
| `TRUSTED_PROXY_IPS` | no | private/reserved ranges | Comma-separated immediate proxy IPs/CIDRs whose `X-Forwarded-For` may be trusted. Defaults to the private/reserved ranges (RFC1918 + CGNAT + loopback/ULA), which is correct on Railway and most PaaS — the platform proxy connects from a private peer IP that a public client cannot forge. Tighten to your edge's exact CIDR if you know it. |
| `TELEGRAM_WEBHOOK_SECRET` | no | `SESSION_JWT_SECRET` | Retention bot: verifies the `X-Telegram-Bot-Api-Secret-Token` header on `/telegram/webhook/{secret}` (NOT in the URL). Set a distinct **≥32-char** value (**required on a real deployment** — see `APP_ENV`). |
| `DB_CONNECT_TIMEOUT_SEC` | no | `10` | Cap (seconds) on establishing a new Postgres connection, so a down DB fails fast instead of hanging on connect. |
| `DB_ACQUIRE_TIMEOUT_SEC` | no | `10` | Cap (seconds) on waiting for a free pooled connection on the hot request paths — pool exhaustion surfaces as a retryable error, not an unbounded hang. |
| `DB_HEALTHCHECK_TIMEOUT_SEC` | no | `5` | Cap (seconds) on the `/healthz` DB probe. `/healthz` is a liveness probe (200 while the process is up, even if the DB is momentarily down) so a DB blip can't drive a restart loop; add `?deep=1` for a strict readiness check that 503s when the DB is down. |
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
| `RETENTION_PLAY_REMINDER_EVERY_MSGS` | no | `5` | Every N-th assistant reply in a Telegram retention chat weaves in a light in-context invitation to play, with a one-tap site-map button picked by intent (0 = off; also a `retention` settings knob, `play_reminder_every_msgs`). |
| `RETENTION_INTRO_PHOTO_ENABLED` | no | `true` | Introduction photo: a brand-new player (never received a photo) gets one proactively within his first meaningful messages, with a model-written "this is me — let's get to know each other" caption, so he learns early that chatting comes with photos (also a `retention` settings knob, `intro_photo_enabled`). |
| `RETENTION_INTRO_PHOTO_WITHIN_MSGS` | no | `3` | How many of the player's first meaningful messages count as the acquaintance window for the introduction photo (also a `retention` settings knob, `intro_photo_within_msgs`). |
| `RETENTION_MAX_REPLY_PARTS` | no | `3` | Default for `retention.max_reply_parts` — max Telegram messages one model reply may be split into (blank-line burst delivery; extra chunks collapse into the last part, `1` = never split). |
| `RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC` | no | `3600` | How often the media-normalizer sweep runs (one deploy-wide loop). Normalization is always-on and code-owned — there is no admin knob or on/off switch (the whole sweep is still gated by `RETENTION_SCHEDULER_ENABLED`). |
| `RETENTION_MEDIA_MAX_SIDE_PX` | no | `2560` | Longest photo side after normalization (matches the ~2560 px Telegram re-compresses photos to). Code-owned, no admin knob. |
| `RETENTION_MEDIA_WEBP_QUALITY` | no | `90` | WebP quality of the normalized photo. Code-owned, no admin knob. |
| `RETENTION_MEDIA_VIDEO_MAX_SIDE_PX` | no | `1920` | Longest side of a normalized retention VIDEO (uploads are re-encoded to Telegram-friendly MP4/H.264 by ffmpeg right after upload). 1920 keeps a vertical 1080×1920 phone reel at native resolution — the CRF re-encode still crushes a bloated source bitrate (a 50 MB 17s reel lands around 5–9 MB). |
| `RETENTION_MEDIA_VIDEO_CRF` | no | `23` | H.264 CRF quality target for normalized retention videos (lower = better quality / bigger file; ~-6 CRF doubles the size — watch the 50 MB Telegram bot cap on long clips). |
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
| `EXPOSE_API_DOCS` | no | — | Set to `1` to publish `/docs`, `/redoc` and `/openapi.json` (they describe the whole surface, `/admin` included, so they are **disabled by default**). Dev/stage only. |

The retention bot's per-product config (bot token, channel, player-API key) lives on the
product row in the admin **Retention · Telegram** section, not in env; secrets there are
encrypted at rest via `SECRETS_MASTER_KEY`. Photo-progression / limit knobs
(`daily_photo_cap`, `stage_advance_msgs`, `max_stage_by_tier`, …) live in the `retention`
settings group (defaults seeded from `RETENTION_*` env). Setup checklist: the admin
**Retention · Telegram → Setup guide** tab; architecture: the retention section in `CLAUDE.md`.

Most operational knobs (rate limits, cooldowns, model tuning, escalation thresholds,
session TTL, body cap, etc.) are tunable live from the admin **Settings** tab and only need
an env var to seed an initial value. True secrets stay in env. See `config.py` for the full
list.
