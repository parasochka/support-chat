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

# --- Sessions / limits ------------------------------------------------------
SESSION_TTL_HOURS: int = _env_int("SESSION_TTL_HOURS", 24)
MAX_MESSAGES_PER_SESSION: int = _env_int("MAX_MESSAGES_PER_SESSION", 30)
MAX_INPUT_CHARS: int = _env_int("MAX_INPUT_CHARS", 2000)
RATE_LIMIT_WINDOW_SEC: int = _env_int("RATE_LIMIT_WINDOW_SEC", 600)
RATE_LIMIT_MAX_PER_IP: int = _env_int("RATE_LIMIT_MAX_PER_IP", 20)
MESSAGE_COOLDOWN_SEC: int = _env_int("MESSAGE_COOLDOWN_SEC", 2)

# --- Low-content / junk guard -----------------------------------------------
# Stops messages with no answerable content (a lone character, symbol/emoji-only
# spam, or one character mashed over and over) BEFORE the model call so a bot or
# idle user typing one char at a time in a loop can't keep burning OpenAI tokens.
# LOW_CONTENT_BLOCK is the master switch; MIN_MEANINGFUL_CHARS is how many
# letters/digits a message must carry to be worth answering.
LOW_CONTENT_BLOCK: bool = _env_bool("LOW_CONTENT_BLOCK", True)
MIN_MEANINGFUL_CHARS: int = _env_int("MIN_MEANINGFUL_CHARS", 2)

# --- reCaptcha --------------------------------------------------------------
RECAPTCHA_SECRET: str | None = _env_opt("RECAPTCHA_SECRET")
RECAPTCHA_MIN_SCORE: float = _env_float("RECAPTCHA_MIN_SCORE", 0.5)

# --- Escalation -------------------------------------------------------------
CONTACT_FORM_URL: str | None = _env_opt("CONTACT_FORM_URL")

# --- Language ---------------------------------------------------------------
DEFAULT_LANGUAGE: str = _env("DEFAULT_LANGUAGE", "en")
SUPPORTED_LANGUAGES: list[str] = [
    code.strip().lower()
    for code in _env("SUPPORTED_LANGUAGES", "en,es,ru,tr,pt").split(",")
    if code.strip()
]

# --- Owner / debug ----------------------------------------------------------
OWNER_TOKEN: str | None = _env_opt("OWNER_TOKEN")

# --- Admin dashboard (Phase 2) ----------------------------------------------
# ADMIN_PASSWORD gates the dashboard: if unset, the dashboard/admin API is
# disabled (login always 503). ADMIN_JWT_SECRET signs admin tokens; it falls
# back to SESSION_JWT_SECRET only so dev runs work without extra config — set a
# distinct secret in production.
ADMIN_PASSWORD: str | None = _env_opt("ADMIN_PASSWORD")
ADMIN_JWT_SECRET: str = _env_opt("ADMIN_JWT_SECRET") or SESSION_JWT_SECRET
# True when ADMIN_JWT_SECRET is not set on its own and silently reuses
# SESSION_JWT_SECRET — fine for dev, flagged at startup in production.
ADMIN_JWT_SECRET_IS_FALLBACK: bool = _env_opt("ADMIN_JWT_SECRET") is None
ADMIN_TOKEN_TTL_MIN: int = _env_int("ADMIN_TOKEN_TTL_MIN", 480)

# --- Secure front-end handshake (Phase 2) -----------------------------------
# HMAC secret used to verify signed user_context blobs from real host sites.
# When unset, signed mode is unavailable and unsigned context is zeroed.
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
# headers may be trusted. Empty by default: direct client-supplied XFF is ignored.
TRUSTED_PROXY_IPS: list[str] = [
    p.strip() for p in _env("TRUSTED_PROXY_IPS", "").split(",") if p.strip()
]

# --- CORS -------------------------------------------------------------------
# Comma-separated list of allowed origins; "*" allows all (dev only).
CORS_ALLOW_ORIGINS: list[str] = [
    o.strip() for o in _env("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()
]

# Convenience: a name shown in logs / health.
SERVICE_NAME = "nowplix-support-chat"
