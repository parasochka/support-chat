"""Environment parsing and constants for the NowPlix support-chat service.

`require_env` fails fast at import time if a required variable is missing, so the
container will not boot in a half-configured state. Optional variables have sane
defaults that allow local/dev runs without a full secret set.
"""
from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """Raised at import time when a required environment variable is missing."""


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        raise ConfigError(
            f"Required environment variable {name!r} is not set. "
            "Refer to the README env table."
        )
    return val


def _env(name: str, default: str) -> str:
    val = os.environ.get(name)
    return default if val is None or val.strip() == "" else val


def _env_opt(name: str) -> str | None:
    val = os.environ.get(name)
    return None if val is None or val.strip() == "" else val


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"Environment variable {name!r} must be an integer, got {raw!r}")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"Environment variable {name!r} must be a float, got {raw!r}")


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Truthy: 1/true/yes/on; falsy: 0/false/no/off
    (case-insensitive). Empty/unset -> default. Avoids the brittle
    `x not in ("0","false",...)` checks that silently treated "FALSE"/"no"/"off"
    as true."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"Environment variable {name!r} must be a boolean (1/0/true/false/yes/no/on/off), got {raw!r}"
    )


def _normalize_db_url(url: str) -> str:
    # asyncpg wants postgresql://, but many providers hand out postgres://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


# ---------------------------------------------------------------------------
# Are we in a test/import-only context? Tests stub native deps and must not
# require real secrets. When SUPPORT_CHAT_TEST_MODE=1 the required vars get
# harmless placeholders so modules import cleanly.
# ---------------------------------------------------------------------------
_TEST_MODE = os.environ.get("SUPPORT_CHAT_TEST_MODE") == "1"

if _TEST_MODE:
    os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("SESSION_JWT_SECRET", "test-secret")


# ---------------------------------------------------------------------------
# Deployment environment marker. In "production" the app REFUSES to boot when a
# purpose-specific secret would silently reuse SESSION_JWT_SECRET (see
# _enforce_production_secrets below); anything else (dev/test) only warns, so
# local runs keep working with zero secret config.
# ---------------------------------------------------------------------------
APP_ENV: str = _env("APP_ENV", "development").strip().lower()
IS_PRODUCTION: bool = APP_ENV in ("production", "prod")


# --- Required ---------------------------------------------------------------
DATABASE_URL: str = _normalize_db_url(require_env("DATABASE_URL"))
OPENAI_API_KEY: str = require_env("OPENAI_API_KEY")
SESSION_JWT_SECRET: str = require_env("SESSION_JWT_SECRET")

# --- OpenAI -----------------------------------------------------------------
# Default model is GPT-5.6 Luna — the cheapest tier of the GPT-5.6 reasoning
# family, built for high-volume latency-sensitive chat. Reasoning models take
# `max_completion_tokens` (not `max_tokens`), do NOT accept `temperature`, and
# expose `reasoning_effort` (low/medium/high) + `verbosity` (low/medium/high)
# instead — see openai_client._KeyClient.call and the `model` settings group.
OPENAI_API_KEY_FALLBACK: str | None = _env_opt("OPENAI_API_KEY_FALLBACK")
OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_REQUEST_TIMEOUT_SEC", 30)
OPENAI_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_KEY_SWITCH_TIMEOUT_SEC", 15)
OPENAI_MAX_ATTEMPTS: int = _env_int("OPENAI_MAX_ATTEMPTS", 3)
# --- per-purpose latency profiles -------------------------------------------
# The two timeouts above are the INTERACTIVE profile: a player is waiting, so a
# primary key that stays silent for 15s is worth racing against the fallback.
# A BACKGROUND pass has nobody waiting — there the race just pays for the same
# work twice (the losing request is already processed and billed by OpenAI, and
# its usage rides in a response we never receive, so it cannot even be logged),
# and the 30s request timeout is too short for a call that reads a whole
# transcript or a multi-MB image. Each block that talks to the model therefore
# carries its own profile, resolved through the `model` settings group:
#   chat   — support widget turns + Telegram bot replies (a player is waiting)
#   agent  — the proactive retention agent: event decisions + ping writing
#   review — the quality-review judge (a whole conversation per call)
#   media  — photo/video cataloguing (vision, multi-MB data URLs)
# A key-switch timeout of 0 DISABLES the race for that purpose: the fallback key
# is then engaged only when the primary actually fails (invariant §5 holds — the
# failover itself is never lost, only the speculative second call is).
OPENAI_AGENT_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_AGENT_REQUEST_TIMEOUT_SEC", 90)
OPENAI_AGENT_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_AGENT_KEY_SWITCH_TIMEOUT_SEC", 0)
OPENAI_REVIEW_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_REVIEW_REQUEST_TIMEOUT_SEC", 120)
OPENAI_REVIEW_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_REVIEW_KEY_SWITCH_TIMEOUT_SEC", 0)
OPENAI_MEDIA_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_MEDIA_REQUEST_TIMEOUT_SEC", 120)
OPENAI_MEDIA_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_MEDIA_KEY_SWITCH_TIMEOUT_SEC", 0)
# Reasoning effort and output verbosity. Empty string ⇒ omit the parameter from
# the request (use the model's own default). "low" keeps support answers fast,
# cheap, and concise.
OPENAI_REASONING_EFFORT: str = _env("OPENAI_REASONING_EFFORT", "low")
OPENAI_VERBOSITY: str = _env("OPENAI_VERBOSITY", "low")
# Output cap (sent as `max_completion_tokens`). Reasoning tokens are billed as
# output and counted against this budget, so it needs more headroom than a
# non-reasoning model — too low and the visible answer can come back empty.
OPENAI_MAX_OUTPUT_TOKENS: int = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 2000)
OPENAI_MAX_CONCURRENT_PER_KEY: int = _env_int("OPENAI_MAX_CONCURRENT_PER_KEY", 4)
# Circuit breaker on the OpenAI dependency. Without it a sustained provider
# outage makes EVERY request pay the full timeout×attempts×failover cost
# (~2.5 min worst case) and pile up unbounded coroutines behind the per-key
# semaphore. After OPENAI_BREAKER_FAIL_THRESHOLD consecutive fully-failed
# completions the breaker OPENS and completions fail fast (the caller returns
# the localized "technical hiccup" nudge in milliseconds) for
# OPENAI_BREAKER_COOLDOWN_SEC, after which ONE trial request is allowed through
# to probe recovery. 0 threshold disables the breaker.
OPENAI_BREAKER_FAIL_THRESHOLD: int = _env_int("OPENAI_BREAKER_FAIL_THRESHOLD", 5)
OPENAI_BREAKER_COOLDOWN_SEC: int = _env_int("OPENAI_BREAKER_COOLDOWN_SEC", 30)

# --- Sessions / limits ------------------------------------------------------
SESSION_TTL_HOURS: int = _env_int("SESSION_TTL_HOURS", 24)
MAX_MESSAGES_PER_SESSION: int = _env_int("MAX_MESSAGES_PER_SESSION", 15)
# How many recent turns from the current topic context feed the model's prompt
# history (the full transcript is always persisted; this only bounds the prompt).
HISTORY_MAX_TURNS: int = _env_int("HISTORY_MAX_TURNS", 15)
MAX_INPUT_CHARS: int = _env_int("MAX_INPUT_CHARS", 500)
RATE_LIMIT_WINDOW_SEC: int = _env_int("RATE_LIMIT_WINDOW_SEC", 600)
RATE_LIMIT_MAX_PER_IP: int = _env_int("RATE_LIMIT_MAX_PER_IP", 20)
# The Telegram retention chat is a LIVELY human dialogue (short messages every
# 15-30s are normal), so it gets its own, higher per-user allowance over the
# same window — the widget's 20/window killed a real conversation mid-flow.
TG_RATE_LIMIT_MAX_PER_USER: int = _env_int("TG_RATE_LIMIT_MAX_PER_USER", 60)
# The partner EVENT FEED is a server-to-server firehose, not a browser, and it
# shared the widget's per-IP allowance (20 requests per window) — which caps a
# casino at 20 pushes per 10 minutes and would throttle the whole pipeline at
# its very first hop. Its own allowance, over the same window: 600 requests,
# and each may carry 500 events.
PARTNER_RATE_LIMIT_MAX: int = _env_int("PARTNER_RATE_LIMIT_MAX", 600)
MESSAGE_COOLDOWN_SEC: int = _env_int("MESSAGE_COOLDOWN_SEC", 2)

# --- Database pool / health -------------------------------------------------
# Bounds on the asyncpg pool so a slow/down Postgres degrades instead of hanging
# every request forever. DB_CONNECT_TIMEOUT_SEC caps establishing a NEW backend
# connection (a dead DB then fails fast instead of blocking on connect);
# DB_ACQUIRE_TIMEOUT_SEC caps waiting for a free pooled connection on the hot
# request paths (pool exhaustion surfaces as an error the client can retry, not
# an unbounded hang); DB_HEALTHCHECK_TIMEOUT_SEC keeps /healthz fail-fast so a
# stalled DB can't hold the liveness probe open for the platform's whole
# healthcheck window (which would otherwise drive a restart/crash loop).
DB_CONNECT_TIMEOUT_SEC: int = _env_int("DB_CONNECT_TIMEOUT_SEC", 10)
DB_ACQUIRE_TIMEOUT_SEC: int = _env_int("DB_ACQUIRE_TIMEOUT_SEC", 10)
DB_HEALTHCHECK_TIMEOUT_SEC: int = _env_int("DB_HEALTHCHECK_TIMEOUT_SEC", 5)

# --- Low-content / junk guard -----------------------------------------------
# Stops messages with no answerable content (a lone character, symbol/emoji-only
# spam, or one character mashed over and over) BEFORE the model call so a bot or
# idle user typing one char at a time in a loop can't keep burning OpenAI tokens.
# LOW_CONTENT_BLOCK is the master switch; MIN_MEANINGFUL_CHARS is how many
# letters/digits a message must carry to be worth answering.
LOW_CONTENT_BLOCK: bool = _env_bool("LOW_CONTENT_BLOCK", True)
MIN_MEANINGFUL_CHARS: int = _env_int("MIN_MEANINGFUL_CHARS", 2)

# --- Cloudflare Turnstile ----------------------------------------------------
# Deploy-level DEFAULT pair. Each product (domain) can carry its OWN Turnstile
# site key + secret (products.turnstile_site_key / turnstile_secret_enc, edited
# in the admin Structure tab); these env values are only the fallback for
# products that haven't configured their own (and for the default product).
# The Turnstile widget must be created as an INVISIBLE widget in the Cloudflare
# dashboard — the chat widget never shows a challenge UI. Verification is
# ADVISORY: a missing token (Turnstile blocked/unreachable on the client) or a
# verifier outage SKIPS the check instead of blocking the player — the other
# anti-spam layers (rate limit, cooldown, low-content, injection) still apply.
TURNSTILE_SECRET: str | None = _env_opt("TURNSTILE_SECRET")
TURNSTILE_SITE_KEY: str | None = _env_opt("TURNSTILE_SITE_KEY")

# --- Escalation -------------------------------------------------------------
# Default contact-button URL. Per-language URLs are set in the admin
# Translations tab (the `contact_url` key); this env value (or a legacy
# `general.contact_form_url` app_settings override) is the fallback when no
# per-language URL is configured.
CONTACT_FORM_URL: str | None = _env_opt("CONTACT_FORM_URL")

# --- Language ---------------------------------------------------------------
DEFAULT_LANGUAGE: str = _env("DEFAULT_LANGUAGE", "en")
SUPPORTED_LANGUAGES: list[str] = [
    code.strip().lower()
    for code in _env("SUPPORTED_LANGUAGES", "en,es,ru,tr,pt").split(",")
    if code.strip()
]

# --- Admin dashboard (Phase 2) ----------------------------------------------
# Admins sign in as named `admin_users` accounts (email + password); there is no
# password-only owner login. ADMIN_JWT_SECRET signs admin tokens; it falls back
# to SESSION_JWT_SECRET only so dev runs work without extra config — set a
# distinct secret in production.
ADMIN_JWT_SECRET: str = _env_opt("ADMIN_JWT_SECRET") or SESSION_JWT_SECRET
# True when ADMIN_JWT_SECRET is not set on its own and silently reuses
# SESSION_JWT_SECRET — fine for dev, flagged at startup in production.
ADMIN_JWT_SECRET_IS_FALLBACK: bool = _env_opt("ADMIN_JWT_SECRET") is None
# Admin login lifetime. The session SLIDES: an active operator's token is
# re-minted with a fresh full TTL past its half-life (see auth.refresh_admin_token),
# so this is really the "inactivity window" — a week by default, so daily use
# never logs you out while an untouched account expires after a week.
ADMIN_TOKEN_TTL_MIN: int = _env_int("ADMIN_TOKEN_TTL_MIN", 10_080)

# Per-IP allowance for /admin/login within the shared antispam window. Decoupled
# from the widget's rate_limit_max_per_ip so raising the (hot) widget limit can't
# silently widen the unauthenticated PBKDF2-CPU-burn budget on the login endpoint.
# Deliberately tight — a human logs in a handful of times, a brute-forcer many.
ADMIN_LOGIN_RATE_LIMIT: int = _env_int("ADMIN_LOGIN_RATE_LIMIT", 10)

# --- Secrets encryption master key (multi-tenancy) ---------------------------
# Encrypts per-product secrets at rest (OpenAI keys, handshake secrets) via
# secretbox.py, so a DB dump alone never reveals a client's keys. Falls back to
# SESSION_JWT_SECRET only so dev runs work without extra config — set a distinct
# strong value in production (flagged at startup like ADMIN_JWT_SECRET).
# NB: changing this value makes previously stored product secrets undecryptable
# (they must be re-entered from the admin panel).
SECRETS_MASTER_KEY: str = _env_opt("SECRETS_MASTER_KEY") or SESSION_JWT_SECRET
SECRETS_MASTER_KEY_IS_FALLBACK: bool = _env_opt("SECRETS_MASTER_KEY") is None

# --- Secure front-end handshake (Phase 2) -----------------------------------
# HMAC secret used to verify signed user_context blobs from real host sites.
# Deploy-level DEFAULT: a product with its own handshake secret (admin
# Structure tab, stored encrypted) uses that instead. When neither is set,
# signed mode is unavailable and unsigned context is zeroed.
WIDGET_HANDSHAKE_SECRET: str | None = _env_opt("WIDGET_HANDSHAKE_SECRET")
# Max age (seconds) tolerated between a handshake's `iat`/`exp` — defence in
# depth alongside the explicit `exp` in the signed payload.
WIDGET_HANDSHAKE_MAX_AGE_SEC: int = _env_int("WIDGET_HANDSHAKE_MAX_AGE_SEC", 300)

# --- Request body cap -------------------------------------------------------
BODY_MAX_BYTES: int = _env_int("BODY_MAX_BYTES", 65536)

# --- Injection / jailbreak hard block ---------------------------------------
# When ON, a message matching a known jailbreak pattern is rejected with HTTP 400
# BEFORE it ever reaches the model — defence in depth on top of the system prompt
# + Layer-3 guardrails, and it stops the attempt from burning OpenAI tokens.
# Enabled by default; the matcher is conservative (normalized known triggers), so
# false positives are rare. Toggle live from the admin panel (antispam group) or
# override here with INJECTION_HARD_BLOCK=0.
INJECTION_HARD_BLOCK: bool = _env_bool("INJECTION_HARD_BLOCK", True)

# --- Proxy / client IP ------------------------------------------------------
# Number of trusted reverse proxies in front of the app (Railway edge = 1).
# Only used after the immediate peer matches TRUSTED_PROXY_IPS; then the real
# client IP is taken this many hops from the RIGHT of X-Forwarded-For.
TRUSTED_PROXY_COUNT: int = _env_int("TRUSTED_PROXY_COUNT", 1)
# Comma-separated IPs/CIDRs for immediate reverse proxies whose X-Forwarded-For
# headers may be trusted. We honour XFF only when the request's *immediate* socket
# peer (the TCP source, which a public client cannot forge) falls in this set.
#
# Default = the private/reserved ranges. On Railway (and any standard PaaS / load
# balancer) the platform proxy connects to the app from a private address, so this
# default makes the real client IP resolve correctly out of the box WITHOUT
# trusting attacker-supplied XFF: an attacker on the public internet has a public
# peer IP and is never trusted. Override (or tighten to the exact proxy CIDR) via
# the TRUSTED_PROXY_IPS env var when you know your edge's address range.
_DEFAULT_TRUSTED_PROXY_IPS = (
    "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,"
    "100.64.0.0/10,::1/128,fc00::/7,fe80::/10"
)
TRUSTED_PROXY_IPS: list[str] = [
    p.strip()
    for p in _env("TRUSTED_PROXY_IPS", _DEFAULT_TRUSTED_PROXY_IPS).split(",")
    if p.strip()
]

# --- CORS -------------------------------------------------------------------
# Comma-separated list of allowed origins; "*" allows all (dev only).
CORS_ALLOW_ORIGINS: list[str] = [
    o.strip() for o in _env("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()
]

# --- Retention / Telegram bot (second facade over the same AI core) ---------
# The retention bot is a Telegram webhook consumer of the same FastAPI service.
# Per-product Telegram config (bot token, channel, player-API key) lives on the
# product row (encrypted where it is a secret); these env values are only the
# deploy-level defaults / knobs shared by every product.
#
# TELEGRAM_WEBHOOK_SECRET guards POST /telegram/webhook/{secret}: Telegram is
# told this path secret when the webhook is registered, so a public caller that
# doesn't know it can't inject fake updates. It also rides in the
# X-Telegram-Bot-Api-Secret-Token header (defence in depth). Falls back to
# SESSION_JWT_SECRET so dev runs work without extra config.
TELEGRAM_WEBHOOK_SECRET: str = _env_opt("TELEGRAM_WEBHOOK_SECRET") or SESSION_JWT_SECRET
TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK: bool = _env_opt("TELEGRAM_WEBHOOK_SECRET") is None


# --- Production secret hygiene (fail fast) ----------------------------------
# ADMIN_JWT_SECRET, SECRETS_MASTER_KEY and TELEGRAM_WEBHOOK_SECRET each fall back
# to SESSION_JWT_SECRET when unset (dev convenience). Left unset in production
# that single key would sign admin sessions, encrypt every product's at-rest
# secrets, AND authenticate the Telegram webhook all at once — leaking any one
# use compromises the others. So we refuse to boot half-secured, mirroring
# require_env's fail-fast philosophy; a genuinely local run still warns only.
#
# The trigger is FAIL-CLOSED. The old control fired only on APP_ENV=production,
# but APP_ENV defaults to "development" — so a real deploy that simply forgot to
# set it silently collapsed every purpose-specific secret onto SESSION_JWT_SECRET
# with nothing but a log line. We now ALSO enforce whenever DATABASE_URL points
# at a non-local host (a strong signal this is a real deployment, independent of
# APP_ENV), so a forgotten APP_ENV can no longer disable the check. Only a
# genuinely local run (loopback DB, not test mode) stays lenient.
def _db_host_is_local(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 - a URL we can't parse is treated as remote
        return False
    if host in ("", "localhost", "127.0.0.1", "::1"):
        return True
    return host.endswith(".local")


# True when the DB is NOT loopback/local — used to fail-closed on secret hygiene
# even if APP_ENV was left at its "development" default on a real deployment.
_DB_IS_REMOTE: bool = not _db_host_is_local(DATABASE_URL)

# Minimum length for a real (non-fallback) secret. 32 chars comfortably covers
# `openssl rand -hex 32` (64 chars), `-hex 16` (32) or `-base64 24` (32) and
# rejects obvious weak values ("secret", "changeme") that are offline
# brute-forceable against any issued HS256 token.
MIN_SECRET_LENGTH: int = 32


def _secret_hygiene_active() -> bool:
    """Whether to hard-enforce secret hygiene at boot (fail-closed)."""
    if _TEST_MODE:
        return False
    return IS_PRODUCTION or _DB_IS_REMOTE


def _enforce_production_secrets() -> None:
    if not _secret_hygiene_active():
        return
    reused = [name for name, is_fallback in (
        ("ADMIN_JWT_SECRET", ADMIN_JWT_SECRET_IS_FALLBACK),
        ("SECRETS_MASTER_KEY", SECRETS_MASTER_KEY_IS_FALLBACK),
        ("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK),
    ) if is_fallback]
    if reused:
        raise ConfigError(
            "This looks like a real deployment (APP_ENV=production or a non-local "
            "DATABASE_URL) but these secrets are unset and would reuse "
            f"SESSION_JWT_SECRET: {', '.join(reused)}. Set a DISTINCT strong "
            "value for each (e.g. `openssl rand -hex 32`) — see the README env "
            "table. (Set APP_ENV=development for an intentional local run.)"
        )


def _enforce_secret_strength() -> None:
    """Reject weak secrets on a real deployment (fail-closed like distinctness).

    Checks SESSION_JWT_SECRET (the root of the fallback chain) plus every
    purpose-specific secret that was set explicitly. A fallback secret is not
    re-checked here — it equals SESSION_JWT_SECRET, which is already checked, and
    _enforce_production_secrets separately forbids the fallback in this context.
    """
    if not _secret_hygiene_active():
        return
    candidates = [("SESSION_JWT_SECRET", SESSION_JWT_SECRET)]
    if not ADMIN_JWT_SECRET_IS_FALLBACK:
        candidates.append(("ADMIN_JWT_SECRET", ADMIN_JWT_SECRET))
    if not SECRETS_MASTER_KEY_IS_FALLBACK:
        candidates.append(("SECRETS_MASTER_KEY", SECRETS_MASTER_KEY))
    if not TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK:
        candidates.append(("TELEGRAM_WEBHOOK_SECRET", TELEGRAM_WEBHOOK_SECRET))
    if WIDGET_HANDSHAKE_SECRET:
        candidates.append(("WIDGET_HANDSHAKE_SECRET", WIDGET_HANDSHAKE_SECRET))
    weak = [name for name, value in candidates
            if len(value or "") < MIN_SECRET_LENGTH]
    if weak:
        raise ConfigError(
            "These secrets are too short to be safe against offline brute force "
            f"(min {MIN_SECRET_LENGTH} chars): {', '.join(weak)}. Generate strong "
            "values with `openssl rand -hex 32` — see the README env table."
        )


_enforce_production_secrets()
_enforce_secret_strength()
# Public base URL of THIS service (e.g. https://chat.example.com), used to build
# the webhook URL when registering it with Telegram and the media-serving URL.
# Empty in dev; set on Railway.
PUBLIC_BASE_URL: str | None = _env_opt("PUBLIC_BASE_URL")
# Where uploaded retention media (photos) are stored on disk. On Railway this is
# the mount path of the attached Volume so binaries survive redeploys; locally it
# defaults to a directory under the repo.
RETENTION_MEDIA_DIR: str = _env(
    "RETENTION_MEDIA_DIR",
    # Default to <repo root>/media (this module lives in app/core/), matching
    # the .dockerignore `media/` exclusion.
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "media"))
# One-time deeplink nonce lifetime (seconds). Also a `retention` settings knob;
# this env value is the default.
RETENTION_NONCE_TTL_SEC: int = _env_int("RETENTION_NONCE_TTL_SEC", 120)
# Max size of a retention media upload (bytes). The JSON body cap (BODY_MAX_BYTES,
# 64 KiB) is far too small for media, so the media-upload path uses this cap
# instead (see main.body_size_cap). Default 512 MiB: the library takes VIDEOS
# too, and a raw phone video original runs to hundreds of MB (the normalizer
# transcodes it down before anything reaches Telegram). NB a 413 on this cap
# aborts the connection mid-upload, which browsers surface as a bare network
# error — the SPA pre-checks file sizes against this value (via /admin/meta)
# to give the operator a real message instead.
RETENTION_MAX_UPLOAD_BYTES: int = _env_int("RETENTION_MAX_UPLOAD_BYTES",
                                           512 * 1024 * 1024)
# Per-FILE upload limits for retention media (the admin Media tab). The batch
# cap above bounds the whole request; these bound each file by type. Enforced
# server-side by byte size (below) AND client-side in the SPA, which also checks
# the resolution / duration caps in the browser before the upload starts (the
# server would have to decode every file to verify those, so they stay a
# client-side guard — the Media tab is admin-only). Photos are downscaled to
# RETENTION_MEDIA_MAX_SIDE_PX on delivery anyway, so the side cap only rejects absurd /
# decompression-bomb originals. Exposed via /admin/meta for the SPA + tooltip.
RETENTION_MAX_PHOTO_BYTES: int = _env_int("RETENTION_MAX_PHOTO_BYTES",
                                          10 * 1024 * 1024)
RETENTION_MAX_PHOTO_SIDE_PX: int = _env_int("RETENTION_MAX_PHOTO_SIDE_PX", 8000)
RETENTION_MAX_VIDEO_BYTES: int = _env_int("RETENTION_MAX_VIDEO_BYTES",
                                          100 * 1024 * 1024)
RETENTION_MAX_VIDEO_DURATION_SEC: int = _env_int(
    "RETENTION_MAX_VIDEO_DURATION_SEC", 60)
# Photo-progression / proactivity knobs — env defaults for the `retention`
# settings group (hot-reloadable per product from the admin panel).
RETENTION_DAILY_PHOTO_CAP: int = _env_int("RETENTION_DAILY_PHOTO_CAP", 5)
RETENTION_PROACTIVE_COOLDOWN_MSGS: int = _env_int(
    "RETENTION_PROACTIVE_COOLDOWN_MSGS", 5)
RETENTION_CANDIDATE_LIST_SIZE: int = _env_int("RETENTION_CANDIDATE_LIST_SIZE", 6)
RETENTION_STAGE_ADVANCE_MIN_HOURS: int = _env_int(
    "RETENTION_STAGE_ADVANCE_MIN_HOURS", 12)
RETENTION_MAX_STAGE: int = _env_int("RETENTION_MAX_STAGE", 5)
# Lazy profile-pull freshness: if the snapshot is older than this and the product
# has a player_api_url + key, refresh it from the casino before a turn (§8 level 2).
RETENTION_PROFILE_PULL_TTL_SEC: int = _env_int(
    "RETENTION_PROFILE_PULL_TTL_SEC", 3600)
# Telegram chat lifecycle: an idle Telegram conversation is closed and the next
# message starts a FRESH chat session (0 = never close — one endless session).
RETENTION_SESSION_IDLE_MINUTES: int = _env_int(
    "RETENTION_SESSION_IDLE_MINUTES", 360)
# How many trailing turns of the PREVIOUS (closed) Telegram chat are shown to
# the model on the first turn of the fresh one, so Nika greets a returning
# player with continuity instead of starting cold (0 = no carry-over).
RETENTION_CARRY_CONTEXT_TURNS: int = _env_int(
    "RETENTION_CARRY_CONTEXT_TURNS", 10)
# Celebrate a just-unlocked photo/closeness stage with a follow-up persona
# note ("we just got closer - more daring photos from now on; keep chatting").
RETENTION_STAGE_UP_NOTIFY: bool = _env_bool("RETENTION_STAGE_UP_NOTIFY", True)
# Every N-th assistant reply in a Telegram retention chat carries a light,
# in-context invitation to come play on the site (with a one-tap site-map
# button when a fitting page exists). 0 = the periodic nudge is off; the
# model may still invite organically per the engagement directive.
# Every 5 replies read as an advert in the live logs (an invitation landed
# right after a confession and right after a boundary deflection), so the
# default is spaced out; the nudge directive also skips those moments.
RETENTION_PLAY_REMINDER_EVERY_MSGS: int = _env_int(
    "RETENTION_PLAY_REMINDER_EVERY_MSGS", 8)
# Introduction photo: a brand-new player (never received a photo) gets one
# proactively within his first N meaningful messages — a "this is me, let's
# get to know each other" opener so he learns from the start that chatting
# comes with photos. The flag turns the rule off; the window is how many first
# meaningful messages count as "the start of the acquaintance".
RETENTION_INTRO_PHOTO_ENABLED: bool = _env_bool(
    "RETENTION_INTRO_PHOTO_ENABLED", True)
RETENTION_INTRO_PHOTO_WITHIN_MSGS: int = _env_int(
    "RETENTION_INTRO_PHOTO_WITHIN_MSGS", 3)

# --- Proactive-contact guard rails (shared per-player protection) ------------
# Env defaults for the `retention` settings group's guard knobs (hot-reloadable
# per product). Hard per-player caps so the agent can never spam: at most this
# many proactive messages a day and never two closer than the gap, regardless
# of how many events fire.
RETENTION_PING_DAILY_CAP: int = _env_int("RETENTION_PING_DAILY_CAP", 3)
RETENTION_PING_MIN_GAP_HOURS: int = _env_int("RETENTION_PING_MIN_GAP_HOURS", 2)
# Local quiet hours (no proactive messages between start and end, e.g. 22 -> 9).
# The offset shifts "local" from UTC for the product's audience.
RETENTION_QUIET_HOURS_START: int = _env_int("RETENTION_QUIET_HOURS_START", 22)
RETENTION_QUIET_HOURS_END: int = _env_int("RETENTION_QUIET_HOURS_END", 9)
RETENTION_QUIET_HOURS_UTC_OFFSET: int = _env_int(
    "RETENTION_QUIET_HOURS_UTC_OFFSET", 0)
# How many queued events one worker run may drain per product (cost guard), and
# how often the worker wakes up (default for the hot `worker_interval_sec`
# setting). The scheduler switch is deploy-level (not a setting): it decides
# whether this instance runs the loop at all.
RETENTION_PING_BATCH_SIZE: int = _env_int("RETENTION_PING_BATCH_SIZE", 100)
# Deliver PROACTIVE messages silently (Telegram disable_notification — the
# message lands without a sound/vibration on the player's phone). Dialogue
# replies are never silent: the player just wrote and expects the buzz.
RETENTION_SILENT_NOTIFICATIONS: bool = _env_bool(
    "RETENTION_SILENT_NOTIFICATIONS", False)
# How long a positive channel-subscription check is cached before the next
# getChatMember round-trip (0 = re-check live on every message).
RETENTION_SUB_CACHE_TTL_SEC: int = _env_int("RETENTION_SUB_CACHE_TTL_SEC", 600)
RETENTION_WORKER_INTERVAL_SEC: int = _env_int("RETENTION_WORKER_INTERVAL_SEC", 5)
RETENTION_SCHEDULER_ENABLED: bool = _env_bool("RETENTION_SCHEDULER_ENABLED", True)

# --- Retention agent (event-driven proactive loop) ---------------------------
# The decision loop over canonical casino events — the ONE proactive regime.
# Per-product switch (`v2_enabled` in the hot `retention` group; the historic
# `v2_` key prefix survives for stored-override compatibility).
RETENTION_V2_ENABLED: bool = _env_bool("RETENTION_V2_ENABLED", True)
# Dry-run ships ON: a freshly-enabled product logs full agent decisions to the
# ledger without sending anything until the owner flips the switch.
RETENTION_V2_DRY_RUN: bool = _env_bool("RETENTION_V2_DRY_RUN", True)
# Per-product daily AI budget for agent decisions+sends (USD). The worker stops
# deciding for the day once the ledger's summed cost reaches it. 0 = no budget.
RETENTION_V2_DAILY_BUDGET_USD: float = _env_float(
    "RETENTION_V2_DAILY_BUDGET_USD", 25.0)
# After a loss signal (high 24h net loss from bet_settled events) the comfort
# window applies: no play CTA, no reward photos, empathetic tone only.
RETENTION_V2_LOSS_COMFORT_HOURS: int = _env_int(
    "RETENTION_V2_LOSS_COMFORT_HOURS", 24)
# 24h net-loss threshold (USD) that classifies risk_state=critical and starts
# the comfort window (the EPIC-5 "loss high" tier).
RETENTION_V2_LOSS_HIGH_USD: float = _env_float("RETENTION_V2_LOSS_HIGH_USD", 100.0)
# One reaction per event TYPE per player per window: a partner retrying
# webhooks or five deposits in an evening get ONE warm note. 0 = off — handy
# while testing the pipeline (re-inject the same event and get a fresh
# decision instead of a same_event_cooldown block).
RETENTION_V2_SAME_EVENT_COOLDOWN_HOURS: int = _env_int(
    "RETENTION_V2_SAME_EVENT_COOLDOWN_HOURS", 5)
# Humanizing send delay: a proactive reaction goes out a RANDOM 5–15 minutes
# (by default) after the event arrived, per event — an instant thank-you three
# seconds after a deposit reads as "the system is watching my transactions",
# not "she thought of me". The worker simply doesn't claim an event before its
# per-event delay has elapsed; the admin «Process queue now» button bypasses
# the delay (testing). min = max pins an exact delay; both 0 = instant.
RETENTION_V2_SEND_DELAY_MIN_SEC: int = _env_int(
    "RETENTION_V2_SEND_DELAY_MIN_SEC", 300)
RETENTION_V2_SEND_DELAY_MAX_SEC: int = _env_int(
    "RETENTION_V2_SEND_DELAY_MAX_SEC", 900)
# The agent's INACTIVITY trigger: the admin-managed idle rules ladder
# (retention_idle / the Idle pings tab). Off = the agent reacts to events only.
RETENTION_IDLE_PINGS_ENABLED: bool = _env_bool(
    "RETENTION_IDLE_PINGS_ENABLED", True)
# How often the idle-ping ladder is evaluated per product (the event worker
# calls the sweep on every tick; this only throttles the rule evaluation).
# Bounds idle-ping responsiveness — a rule fires on this granularity at best.
RETENTION_IDLE_SWEEP_INTERVAL_SEC: int = _env_int(
    "RETENTION_IDLE_SWEEP_INTERVAL_SEC", 600)
# Max Telegram messages one model reply may be split into (blank-line burst
# delivery, retention._split_reply_parts). Extra chunks collapse into the
# last part; 1 = never split.
RETENTION_MAX_REPLY_PARTS: int = _env_int("RETENTION_MAX_REPLY_PARTS", 3)
# OUTCOME ATTRIBUTION (retention/outcomes.py): every touch the bot sends —
# a proactive agent/idle message, a delivered photo or video, a message
# carrying a site-map CTA button — gets one retention_outcomes row, and a
# sweep fills in what happened afterwards: did the player answer, did he come
# back, did he deposit. The windows are deploy-level constants (like the media
# normalizer's): they define the MEANING of the stored numbers, so changing
# them per product would make the stored history incomparable. The reply
# window matches the 48h the retention overview's ping-reply-rate already
# uses; the conversion window is longer because a deposit follows a nudge with
# more lag than a chat reply does.
RETENTION_OUTCOME_REPLY_WINDOW_HOURS: int = _env_int(
    "RETENTION_OUTCOME_REPLY_WINDOW_HOURS", 48)
RETENTION_OUTCOME_CONVERSION_WINDOW_HOURS: int = _env_int(
    "RETENTION_OUTCOME_CONVERSION_WINDOW_HOURS", 72)
# How often the attribution sweep runs per product (it rides the retention
# worker tick, this only paces it) and how many open rows one pass settles.
RETENTION_OUTCOME_SWEEP_INTERVAL_SEC: int = _env_int(
    "RETENTION_OUTCOME_SWEEP_INTERVAL_SEC", 300)
RETENTION_OUTCOME_SWEEP_BATCH: int = _env_int(
    "RETENTION_OUTCOME_SWEEP_BATCH", 500)
# QUALITY REVIEW (ai/reviewer.py): the LLM-as-judge pass that scores finished
# conversations (support widget AND Telegram). The cadence + the dormancy
# threshold that makes an open conversation reviewable are deploy constants;
# the on/off switch, the daily cap and the minimum length are admin settings
# (the `general` group) because they are cost dials the owner tunes.
QUALITY_REVIEW_INTERVAL_SEC: int = _env_int("QUALITY_REVIEW_INTERVAL_SEC", 1800)
QUALITY_REVIEW_IDLE_MINUTES: int = _env_int("QUALITY_REVIEW_IDLE_MINUTES", 60)
QUALITY_REVIEW_ENABLED: bool = _env_bool("QUALITY_REVIEW_ENABLED", True)
QUALITY_REVIEW_DAILY_MAX: int = _env_int("QUALITY_REVIEW_DAILY_MAX", 100)
QUALITY_REVIEW_MIN_MESSAGES: int = _env_int("QUALITY_REVIEW_MIN_MESSAGES", 4)
# Media normalizer (media_normalizer.py): the periodic sweep that re-encodes
# uploaded retention photos to WebP at Telegram-appropriate dimensions and
# deletes the heavy originals. Normalization is ALWAYS ON and fully code-owned —
# there is deliberately no admin knob and no on/off switch (the whole sweep loop
# is still gated by the deploy-wide RETENTION_SCHEDULER_ENABLED that governs all
# background workers). These are deploy-level constants, not admin settings.
RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC: int = _env_int(
    "RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC", 3600)
RETENTION_MEDIA_MAX_SIDE_PX: int = _env_int(
    "RETENTION_MEDIA_MAX_SIDE_PX", 2560)
RETENTION_MEDIA_WEBP_QUALITY: int = _env_int(
    "RETENTION_MEDIA_WEBP_QUALITY", 90)
# Video normalization (ffmpeg -> Telegram-friendly MP4/H.264). Deploy-level
# constants on purpose (no admin knobs yet): 1080p-class longest side (a
# vertical phone reel keeps its native 1080x1920 - photos are served at
# Telegram's max resolution too, so video quality matches) + a CRF quality
# target that still crushes the bloated source bitrate. Lower CRF = better
# quality + bigger file (~-6 CRF doubles the size); the `medium` preset squeezes
# more quality out of the same CRF than `veryfast` at a longer encode time —
# fine here since transcodes run in the background and quality matters more than
# speed. Watch the 50 MB Telegram bot cap when lowering CRF on long clips.
RETENTION_MEDIA_VIDEO_MAX_SIDE_PX: int = _env_int(
    "RETENTION_MEDIA_VIDEO_MAX_SIDE_PX", 1920)
RETENTION_MEDIA_VIDEO_CRF: int = _env_int(
    "RETENTION_MEDIA_VIDEO_CRF", 26)
RETENTION_MEDIA_VIDEO_PRESET: str = _env(
    "RETENTION_MEDIA_VIDEO_PRESET", "medium")

# --- RETENTION ORCHESTRATOR (measurement / RG / frequency / scoring /
# offers / journeys / scenario library / channels) -------------------------
# Every knob below is the ENV-DEFAULT layer of a hot `retention`-group setting
# (precedence product -> global -> env -> default), except where noted.
#
# MEASUREMENT (holdout + uplift). The holdout percentage ships at 15 BY
# BUSINESS DECISION (the owner wants a live control group everywhere from day
# one to measure real retention effect); 0 disables it. The salt names the
# experiment — rotating it re-buckets every player (a NEW experiment).
RETENTION_HOLDOUT_PCT: int = _env_int("RETENTION_HOLDOUT_PCT", 15)
RETENTION_HOLDOUT_SALT: str = _env("RETENTION_HOLDOUT_SALT", "default")
# RG GUARD (responsible gaming). ON by default — it is protection, not a
# feature. MVP policy (owner decision): only casino-reported self-exclusion is
# a hard permanent block; the consent gate ships OFF (no licensed-market
# requirements at MVP) and unknown status passes with an audit mark ('warn').
RETENTION_RG_ENABLED: bool = _env_bool("RETENTION_RG_ENABLED", True)
RETENTION_RG_REQUIRE_CONSENT: bool = _env_bool(
    "RETENTION_RG_REQUIRE_CONSENT", False)
RETENTION_RG_UNKNOWN_STATUS_POLICY: str = _env(
    "RETENTION_RG_UNKNOWN_STATUS_POLICY", "warn")  # 'warn' | 'block'
RETENTION_RG_DIALOGUE_SUPPRESSION: bool = _env_bool(
    "RETENTION_RG_DIALOGUE_SUPPRESSION", True)
# ADAPTIVE FREQUENCY + SMART SEND TIME. Both OFF by default: when off the
# legacy static guards (ping_daily_cap / ping_min_gap_hours) and the plain
# humanizing delay apply bit-for-bit.
RETENTION_ADAPTIVE_FREQUENCY_ENABLED: bool = _env_bool(
    "RETENTION_ADAPTIVE_FREQUENCY_ENABLED", False)
RETENTION_SMART_SEND_TIME_ENABLED: bool = _env_bool(
    "RETENTION_SMART_SEND_TIME_ENABLED", False)
RETENTION_SST_MAX_SHIFT_HOURS: int = _env_int("RETENTION_SST_MAX_SHIFT_HOURS", 2)
RETENTION_SST_MIN_SAMPLE: int = _env_int("RETENTION_SST_MIN_SAMPLE", 20)
RETENTION_ACTIVITY_PROFILE_SWEEP_INTERVAL_SEC: int = _env_int(
    "RETENTION_ACTIVITY_PROFILE_SWEEP_INTERVAL_SEC", 3600)
# PLAYER SCORING (dormancy cohorts / RFM / value tiers). OFF -> the state
# snapshot keeps exactly its pre-orchestrator fields.
RETENTION_SCORING_ENABLED: bool = _env_bool("RETENTION_SCORING_ENABLED", False)
RETENTION_SCORING_SWEEP_INTERVAL_SEC: int = _env_int(
    "RETENTION_SCORING_SWEEP_INTERVAL_SEC", 3600)
RETENTION_RFM_WINDOW_DAYS: int = _env_int("RETENTION_RFM_WINDOW_DAYS", 30)
# OFFER ENGINE. Triple-guarded against accidental real-money grants: master
# switch OFF + dry-run ON + a zero budget (0 = granting blocked).
RETENTION_OFFERS_ENABLED: bool = _env_bool("RETENTION_OFFERS_ENABLED", False)
RETENTION_OFFER_DRY_RUN: bool = _env_bool("RETENTION_OFFER_DRY_RUN", True)
RETENTION_OFFER_DAILY_BUDGET_USD: float = _env_float(
    "RETENTION_OFFER_DAILY_BUDGET_USD", 0.0)
RETENTION_OFFER_COOLDOWN_HOURS: int = _env_int(
    "RETENTION_OFFER_COOLDOWN_HOURS", 72)
RETENTION_OFFER_LIFETIME_CAP: int = _env_int("RETENTION_OFFER_LIFETIME_CAP", 0)
RETENTION_OFFER_GRANT_TIMEOUT_SEC: int = _env_int(
    "RETENTION_OFFER_GRANT_TIMEOUT_SEC", 10)
# JOURNEY ENGINE. OFF -> only the idle ladder + single reactions run.
RETENTION_JOURNEYS_ENABLED: bool = _env_bool("RETENTION_JOURNEYS_ENABLED", False)
RETENTION_JOURNEYS_DRY_RUN_DEFAULT: bool = _env_bool(
    "RETENTION_JOURNEYS_DRY_RUN_DEFAULT", True)
RETENTION_JOURNEY_STEP_SWEEP_INTERVAL_SEC: int = _env_int(
    "RETENTION_JOURNEY_STEP_SWEEP_INTERVAL_SEC", 300)
RETENTION_JOURNEY_MAX_ACTIVE_PER_PLAYER: int = _env_int(
    "RETENTION_JOURNEY_MAX_ACTIVE_PER_PLAYER", 3)
# Default gap before a SCHEDULED journey may take the same player again. Its
# candidates come from live state (a dormancy cohort lasts days), so without a
# gap a finished enrollment is re-created on the very next sweep. A journey may
# override it with `metadata.reentry_cooldown_days`; the weekly and
# cashier-abandonment trigger shapes derive their own (see
# journeys._reentry_cooldown_days).
RETENTION_JOURNEY_REENTRY_COOLDOWN_DAYS: int = _env_int(
    "RETENTION_JOURNEY_REENTRY_COOLDOWN_DAYS", 30)
# SCENARIO / TEMPLATE LIBRARY.
RETENTION_SCENARIO_AUTOSEED: bool = _env_bool("RETENTION_SCENARIO_AUTOSEED",
                                              False)
RETENTION_ABANDONMENT_DELAY_HOURS: int = _env_int(
    "RETENTION_ABANDONMENT_DELAY_HOURS", 2)
# CHANNEL ABSTRACTION. OFF -> the router always answers 'telegram' and no
# non-Telegram adapter is active (behaviour bit-for-bit as before).
RETENTION_MULTICHANNEL_ENABLED: bool = _env_bool(
    "RETENTION_MULTICHANNEL_ENABLED", False)
RETENTION_CHANNEL_AUTO_PRIORITY: str = _env(
    "RETENTION_CHANNEL_AUTO_PRIORITY", "push,in_app,email")
RETENTION_DELIVERY_RETRY_ENABLED: bool = _env_bool(
    "RETENTION_DELIVERY_RETRY_ENABLED", True)
RETENTION_PUSH_DELIVERY_TIMEOUT_SEC: int = _env_int(
    "RETENTION_PUSH_DELIVERY_TIMEOUT_SEC", 10)
# Attribution windows for uplift conversion types (deploy constants — changing
# them retroactively makes stored uplift slices incomparable).
RETENTION_UPLIFT_WINDOW_DAYS: int = _env_int("RETENTION_UPLIFT_WINDOW_DAYS", 28)

# --- EVENT PIPELINE: queue durability, parallelism, send shaping -------------
# Env defaults for the `retention` group's pipeline knobs. The queue is a
# LEASED Postgres queue (see the retention_events comment in db._SCHEMA): a
# claim is a lease, not a completion, so nothing is lost when a worker dies.
# How long a claimed event may stay in flight before the reclaimer assumes the
# worker is gone. Must exceed the agent model timeout (90s) with real margin —
# a lease that expires mid-decision means the event is processed twice.
RETENTION_EVENT_LEASE_SEC: int = _env_int("RETENTION_EVENT_LEASE_SEC", 300)
RETENTION_EVENT_MAX_ATTEMPTS: int = _env_int("RETENTION_EVENT_MAX_ATTEMPTS", 5)
RETENTION_EVENT_BACKOFF_BASE_SEC: int = _env_int(
    "RETENTION_EVENT_BACKOFF_BASE_SEC", 30)
# Parallelism. Products are drained concurrently (each under its own advisory
# lock) and, inside a product, events are grouped BY PLAYER: one player's
# events are strictly serial (two concurrent decisions for the same player race
# on the guard counters and produce two messages), different players run in
# parallel.
RETENTION_WORKER_PRODUCT_CONCURRENCY: int = _env_int(
    "RETENTION_WORKER_PRODUCT_CONCURRENCY", 4)
RETENTION_WORKER_PLAYER_CONCURRENCY: int = _env_int(
    "RETENTION_WORKER_PLAYER_CONCURRENCY", 8)
# Fleet-wide ceiling on concurrent background model calls, independent of which
# API key serves them: a burst must not open hundreds of completions at once.
RETENTION_AGENT_MODEL_CONCURRENCY: int = _env_int(
    "RETENTION_AGENT_MODEL_CONCURRENCY", 16)
# Backpressure ladder, keyed on the queue lag (age of the oldest pending
# event). Past the first threshold the drain stops claiming state food, past
# the second only transactional lanes move, past the third the idle ladder
# pauses for that product until the lag recovers.
RETENTION_QUEUE_DEGRADE_P3_SEC: int = _env_int(
    "RETENTION_QUEUE_DEGRADE_P3_SEC", 300)
RETENTION_QUEUE_DEGRADE_P2_SEC: int = _env_int(
    "RETENTION_QUEUE_DEGRADE_P2_SEC", 900)
RETENTION_QUEUE_DEGRADE_IDLE_SEC: int = _env_int(
    "RETENTION_QUEUE_DEGRADE_IDLE_SEC", 3600)
# High-volume events (spins, session pings) are stored COMPLETE and never enter
# the drain — they only move counters and feed the state resolver.
RETENTION_QUEUE_BYPASS_STATE_EVENTS: bool = _env_bool(
    "RETENTION_QUEUE_BYPASS_STATE_EVENTS", True)
# Debounce for the activity-timestamp bridge: the busiest write in the system,
# and re-stamping "active" seconds later buys nothing.
RETENTION_ACTIVITY_DEBOUNCE_SEC: int = _env_int(
    "RETENTION_ACTIVITY_DEBOUNCE_SEC", 60)
# SEND STAGE. Off = decisions send inline exactly as before (the rollout flag);
# on = a decision only enqueues and the send worker delivers it.
RETENTION_SEND_WORKER_ENABLED: bool = _env_bool(
    "RETENTION_SEND_WORKER_ENABLED", False)
RETENTION_SEND_CONCURRENCY: int = _env_int("RETENTION_SEND_CONCURRENCY", 16)
RETENTION_SEND_LEASE_SEC: int = _env_int("RETENTION_SEND_LEASE_SEC", 120)
# Dead-letter ceiling for a delivery, the counterpart of
# RETENTION_EVENT_MAX_ATTEMPTS. Without one a transiently-failing row (a
# rotated bot token, a partner endpoint that is down) is re-claimed on the
# saturated 30-minute backoff step forever, and every attempt re-bills the
# original generation's tokens into ai_interaction_logs.
RETENTION_SEND_MAX_ATTEMPTS: int = _env_int("RETENTION_SEND_MAX_ATTEMPTS", 6)
RETENTION_SEND_BATCH_SIZE: int = _env_int("RETENTION_SEND_BATCH_SIZE", 200)
# Token bucket per channel. Telegram allows ~30 msg/s per bot and ~1/s per
# chat; we sit under both so a broadcast drains instead of collecting 429s.
RETENTION_TELEGRAM_RATE_PER_SEC: float = _env_float(
    "RETENTION_TELEGRAM_RATE_PER_SEC", 30.0)
RETENTION_TELEGRAM_BURST: float = _env_float("RETENTION_TELEGRAM_BURST", 30.0)
RETENTION_TELEGRAM_CHAT_RATE_PER_SEC: float = _env_float(
    "RETENTION_TELEGRAM_CHAT_RATE_PER_SEC", 1.0)
RETENTION_EMAIL_RATE_PER_SEC: float = _env_float(
    "RETENTION_EMAIL_RATE_PER_SEC", 50.0)
# Delegated push / in_app are POSTs at the CASINO's delivery endpoint. Unshaped,
# a broadcast is a denial of service against our own partner, who then throttles
# or drops us — so the delegated channels get a bucket too. Deliberately modest
# until the partner states what their endpoint accepts.
RETENTION_PARTNER_RATE_PER_SEC: float = _env_float(
    "RETENTION_PARTNER_RATE_PER_SEC", 20.0)
# How long one product may hold a send pass before handing the worker slot back.
# The queue is durable, so the next tick resumes where this one stopped; the
# bound only stops one product's burst from starving the others.
RETENTION_SEND_PASS_MAX_SEC: int = _env_int("RETENTION_SEND_PASS_MAX_SEC", 30)
# Maintenance-loop cadences (each sweep is paced per product through
# retention_worker_jobs, so these are how often a worker even looks).
RETENTION_MAINTENANCE_INTERVAL_SEC: int = _env_int(
    "RETENTION_MAINTENANCE_INTERVAL_SEC", 30)
RETENTION_ATTRIBUTION_INTERVAL_SEC: int = _env_int(
    "RETENTION_ATTRIBUTION_INTERVAL_SEC", 300)
RETENTION_SCORING_INTERVAL_SEC: int = _env_int(
    "RETENTION_SCORING_INTERVAL_SEC", 900)
RETENTION_PROFILE_INTERVAL_SEC: int = _env_int(
    "RETENTION_PROFILE_INTERVAL_SEC", 3600)
RETENTION_JOURNEY_INTERVAL_SEC: int = _env_int(
    "RETENTION_JOURNEY_INTERVAL_SEC", 120)
# Split retention for the event log: state food is 90%+ of the rows and worth
# nothing once the resolver's windows have passed.
RETENTION_EVENT_KEEP_DAYS: int = _env_int("RETENTION_EVENT_KEEP_DAYS", 90)
RETENTION_EVENT_KEEP_DAYS_STATE: int = _env_int(
    "RETENTION_EVENT_KEEP_DAYS_STATE", 14)

# --- PROCESS ROLE ------------------------------------------------------------
# Which half of the service this process is. 'web' serves HTTP only; 'worker'
# runs the background loops only (its own Railway service from the same image);
# 'all' is the single-process mode (local dev, and the pre-split default so an
# existing deployment keeps working until its worker service exists).
SERVICE_ROLE: str = _env("SERVICE_ROLE", "all").strip().lower()
# Seconds a shutting-down worker may spend finishing the batch in flight.
# Railway sends SIGKILL 30s after SIGTERM, so stay under that.
WORKER_DRAIN_TIMEOUT_SEC: int = _env_int("WORKER_DRAIN_TIMEOUT_SEC", 25)
# Connection-pool bounds, per role: the worker runs many concurrent player
# shards, the web process serves requests.
DB_POOL_MIN: int = _env_int("DB_POOL_MIN", 1)
DB_POOL_MAX: int = _env_int("DB_POOL_MAX", 30 if SERVICE_ROLE == "worker" else 10)

# Serve /docs, /redoc and /openapi.json (they describe the WHOLE API surface,
# /admin included) — off by default; enable only on dev/stage deployments.
EXPOSE_API_DOCS: bool = _env_bool("EXPOSE_API_DOCS", False)

# Convenience: a name shown in logs / health.
SERVICE_NAME = "nowplix-support-chat"
