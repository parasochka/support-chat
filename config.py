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
# Default model is the GPT-5 mini reasoning family. Reasoning models take
# `max_completion_tokens` (not `max_tokens`), do NOT accept `temperature`, and
# expose `reasoning_effort` (low/medium/high) + `verbosity` (low/medium/high)
# instead — see openai_client._KeyClient.call and the `model` settings group.
OPENAI_API_KEY_FALLBACK: str | None = _env_opt("OPENAI_API_KEY_FALLBACK")
OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-5-mini")
OPENAI_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_REQUEST_TIMEOUT_SEC", 40)
OPENAI_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_KEY_SWITCH_TIMEOUT_SEC", 25)
OPENAI_MAX_ATTEMPTS: int = _env_int("OPENAI_MAX_ATTEMPTS", 3)
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
MAX_MESSAGES_PER_SESSION: int = _env_int("MAX_MESSAGES_PER_SESSION", 30)
# How many recent turns from the current topic context feed the model's prompt
# history (the full transcript is always persisted; this only bounds the prompt).
HISTORY_MAX_TURNS: int = _env_int("HISTORY_MAX_TURNS", 20)
MAX_INPUT_CHARS: int = _env_int("MAX_INPUT_CHARS", 2000)
RATE_LIMIT_WINDOW_SEC: int = _env_int("RATE_LIMIT_WINDOW_SEC", 600)
RATE_LIMIT_MAX_PER_IP: int = _env_int("RATE_LIMIT_MAX_PER_IP", 20)
# The Telegram retention chat is a LIVELY human dialogue (short messages every
# 15-30s are normal), so it gets its own, higher per-user allowance over the
# same window — the widget's 20/window killed a real conversation mid-flow.
TG_RATE_LIMIT_MAX_PER_USER: int = _env_int("TG_RATE_LIMIT_MAX_PER_USER", 60)
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

# --- reCaptcha --------------------------------------------------------------
# Deploy-level DEFAULT pair. Each product (domain) can carry its OWN reCaptcha
# site key + secret (products.recaptcha_site_key / recaptcha_secret_enc, edited
# in the admin Structure tab); these env values are only the fallback for
# products that haven't configured their own (and for the default product).
RECAPTCHA_SECRET: str | None = _env_opt("RECAPTCHA_SECRET")
RECAPTCHA_SITE_KEY: str | None = _env_opt("RECAPTCHA_SITE_KEY")
RECAPTCHA_MIN_SCORE: float = _env_float("RECAPTCHA_MIN_SCORE", 0.5)

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
ADMIN_TOKEN_TTL_MIN: int = _env_int("ADMIN_TOKEN_TTL_MIN", 480)

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
RETENTION_MEDIA_DIR: str = _env("RETENTION_MEDIA_DIR",
                                os.path.join(os.path.dirname(__file__), "media"))
# One-time deeplink nonce lifetime (seconds). Also a `retention` settings knob;
# this env value is the default.
RETENTION_NONCE_TTL_SEC: int = _env_int("RETENTION_NONCE_TTL_SEC", 120)
# Max size of a retention media upload (bytes). The JSON body cap (BODY_MAX_BYTES,
# 64 KiB) is far too small for an image, so the media-upload path uses this cap
# instead (see main.body_size_cap). Default 10 MiB.
RETENTION_MAX_UPLOAD_BYTES: int = _env_int("RETENTION_MAX_UPLOAD_BYTES",
                                           10 * 1024 * 1024)
# Photo-progression / proactivity knobs — env defaults for the `retention`
# settings group (hot-reloadable per product from the admin panel).
RETENTION_DAILY_PHOTO_CAP: int = _env_int("RETENTION_DAILY_PHOTO_CAP", 10)
RETENTION_PROACTIVE_COOLDOWN_MSGS: int = _env_int(
    "RETENTION_PROACTIVE_COOLDOWN_MSGS", 6)
RETENTION_CANDIDATE_LIST_SIZE: int = _env_int("RETENTION_CANDIDATE_LIST_SIZE", 6)
RETENTION_STAGE_ADVANCE_MIN_HOURS: int = _env_int(
    "RETENTION_STAGE_ADVANCE_MIN_HOURS", 24)
RETENTION_MAX_STAGE: int = _env_int("RETENTION_MAX_STAGE", 4)
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
    "RETENTION_CARRY_CONTEXT_TURNS", 6)

# --- Proactive pings (the "retention matrix") --------------------------------
# Env defaults for the `retention` settings group's ping knobs (hot-reloadable
# per product). PINGS master switch ships OFF: a product opts in from the admin.
RETENTION_PINGS_ENABLED: bool = _env_bool("RETENTION_PINGS_ENABLED", False)
# Hard per-player caps so the matrix can never spam: at most this many pings a
# day and never two pings closer than the gap, regardless of how many rules match.
RETENTION_PING_DAILY_CAP: int = _env_int("RETENTION_PING_DAILY_CAP", 1)
RETENTION_PING_MIN_GAP_HOURS: int = _env_int("RETENTION_PING_MIN_GAP_HOURS", 48)
# Local quiet hours (no pings sent between start and end, e.g. 22 -> 9). The
# offset shifts "local" from UTC for the product's audience.
RETENTION_QUIET_HOURS_START: int = _env_int("RETENTION_QUIET_HOURS_START", 22)
RETENTION_QUIET_HOURS_END: int = _env_int("RETENTION_QUIET_HOURS_END", 9)
RETENTION_QUIET_HOURS_UTC_OFFSET: int = _env_int(
    "RETENTION_QUIET_HOURS_UTC_OFFSET", 0)
# How many players one worker run may ping per product (cost guard), and how
# often the worker wakes up. The scheduler switch is deploy-level (not a
# setting): it decides whether this instance runs the loop at all.
RETENTION_PING_BATCH_SIZE: int = _env_int("RETENTION_PING_BATCH_SIZE", 30)
RETENTION_PING_INTERVAL_SEC: int = _env_int("RETENTION_PING_INTERVAL_SEC", 300)
RETENTION_SCHEDULER_ENABLED: bool = _env_bool("RETENTION_SCHEDULER_ENABLED", True)

# Serve /docs, /redoc and /openapi.json (they describe the WHOLE API surface,
# /admin included) — off by default; enable only on dev/stage deployments.
EXPOSE_API_DOCS: bool = _env_bool("EXPOSE_API_DOCS", False)

# Convenience: a name shown in logs / health.
SERVICE_NAME = "nowplix-support-chat"
