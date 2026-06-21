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
OPENAI_API_KEY_FALLBACK: str | None = _env_opt("OPENAI_API_KEY_FALLBACK")
OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_REQUEST_TIMEOUT_SEC: int = _env_int("OPENAI_REQUEST_TIMEOUT_SEC", 40)
OPENAI_KEY_SWITCH_TIMEOUT_SEC: int = _env_int("OPENAI_KEY_SWITCH_TIMEOUT_SEC", 25)
OPENAI_MAX_ATTEMPTS: int = _env_int("OPENAI_MAX_ATTEMPTS", 3)
OPENAI_TEMPERATURE: float = _env_float("OPENAI_TEMPERATURE", 0.3)
OPENAI_MAX_OUTPUT_TOKENS: int = _env_int("OPENAI_MAX_OUTPUT_TOKENS", 700)
OPENAI_MAX_CONCURRENT_PER_KEY: int = _env_int("OPENAI_MAX_CONCURRENT_PER_KEY", 4)

# --- Sessions / limits ------------------------------------------------------
SESSION_TTL_HOURS: int = _env_int("SESSION_TTL_HOURS", 24)
MAX_MESSAGES_PER_SESSION: int = _env_int("MAX_MESSAGES_PER_SESSION", 30)
MAX_INPUT_CHARS: int = _env_int("MAX_INPUT_CHARS", 2000)
RATE_LIMIT_WINDOW_SEC: int = _env_int("RATE_LIMIT_WINDOW_SEC", 600)
RATE_LIMIT_MAX_PER_IP: int = _env_int("RATE_LIMIT_MAX_PER_IP", 20)
MESSAGE_COOLDOWN_SEC: int = _env_int("MESSAGE_COOLDOWN_SEC", 2)

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
ADMIN_TOKEN_TTL_MIN: int = _env_int("ADMIN_TOKEN_TTL_MIN", 480)

# --- Telegram escalation notifier (Phase 2) ---------------------------------
# Both must be set to enable the Telegram channel; otherwise escalation falls
# back to the contact-button only (Phase 1 behaviour).
TELEGRAM_BOT_TOKEN: str | None = _env_opt("TELEGRAM_BOT_TOKEN")
TELEGRAM_AGENT_CHAT_ID: str | None = _env_opt("TELEGRAM_AGENT_CHAT_ID")

# --- Secure front-end handshake (Phase 2) -----------------------------------
# HMAC secret used to verify signed user_context blobs from real host sites.
# When unset, signed mode is unavailable and unsigned context is zeroed.
WIDGET_HANDSHAKE_SECRET: str | None = _env_opt("WIDGET_HANDSHAKE_SECRET")
# Max age (seconds) tolerated between a handshake's `iat`/`exp` — defence in
# depth alongside the explicit `exp` in the signed payload.
WIDGET_HANDSHAKE_MAX_AGE_SEC: int = _env_int("WIDGET_HANDSHAKE_MAX_AGE_SEC", 300)

# --- Public base URL (Phase 2) ----------------------------------------------
# Used to build deep links in Telegram tickets (e.g. dashboard session links).
PUBLIC_BASE_URL: str | None = _env_opt("PUBLIC_BASE_URL")

# --- Request body cap -------------------------------------------------------
BODY_MAX_BYTES: int = _env_int("BODY_MAX_BYTES", 65536)

# --- Injection / jailbreak hard block ---------------------------------------
# By default the injection scan only LOGS (the system prompt + Layer-3 guardrails
# are the real defence). Set INJECTION_HARD_BLOCK=1 to additionally reject a
# message that matches a known jailbreak pattern with HTTP 400 before it ever
# reaches the model — defence in depth at the cost of rare false positives.
INJECTION_HARD_BLOCK: bool = _env("INJECTION_HARD_BLOCK", "0") not in ("0", "false", "False", "")

# --- Proxy / client IP ------------------------------------------------------
# Number of trusted reverse proxies in front of the app (Railway edge = 1).
# The real client IP is taken this many hops from the RIGHT of X-Forwarded-For,
# so a client-supplied (spoofed) left-hand value cannot defeat the rate limiter.
TRUSTED_PROXY_COUNT: int = _env_int("TRUSTED_PROXY_COUNT", 1)

# --- CORS -------------------------------------------------------------------
# Comma-separated list of allowed origins; "*" allows all (dev only).
CORS_ALLOW_ORIGINS: list[str] = [
    o.strip() for o in _env("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()
]

# Convenience: a name shown in logs / health.
SERVICE_NAME = "nowplix-support-chat"
