"""Runtime-tunable settings with precedence: app_settings (DB) > env > default.

Phase 1 read knobs straight from `config`. Phase 2 layers an `app_settings`
table on top so the owner can tune behaviour live (no redeploy). Resolved values
are read through the sync getters below, which merge the in-process DB cache over
the env-backed defaults from `config`. The cache is populated at startup
(`reload()`) and re-populated whenever the admin writes a setting, so edits are
hot.

Reading env defaults at call time (not import time) keeps tests that monkeypatch
`config` working, and means an empty cache transparently falls back to env.
"""
from __future__ import annotations

from typing import Any

import config
import db
import escalation as _escalation
import prompts

# Raw DB values keyed by setting group (e.g. 'antispam' -> {...}). Empty until
# reload(); an empty cache means every getter falls back to env defaults.
_cache: dict[str, Any] = {}

# The settings groups the admin may write, and which validator guards each.
SETTING_KEYS = ("escalation", "forbidden_topics", "language", "antispam")

# Test/dev sandbox profile. In a real deployment the host site supplies the
# player's `user_context` over the signed handshake; in test/dev (no
# WIDGET_HANDSHAKE_SECRET) there is no host, so this stored profile stands in for
# it. It feeds the same Layer-3 fields the model sees (id/full_name/email/
# activation_status) plus two language knobs:
#   - profile_language: the account language; seeds the default answer language
#     BELOW the browser locale (same precedence as a real handshake's language).
#   - force_lang: a hard answer/UI language for the whole session, applied with
#     top priority (like a manual switch) so the test environment can be pinned
#     to one language regardless of the browser — '' means "Auto" (browser/profile).
# `enabled` gates the whole thing: off ⇒ fall back to the widget-supplied context.
_DEFAULT_TEST_PROFILE: dict[str, Any] = {
    "enabled": True,
    "id": "demo-12345",
    "full_name": "Test Player",
    "email": "test.player@example.com",
    "activation_status": "active",
    "profile_language": "",
    "force_lang": "",
}

# String fields that round-trip the player context (mirrors prompts._CONTEXT_FIELDS
# plus the account-language seed). Validated as plain strings.
_TEST_PROFILE_STR_FIELDS = (
    "id", "full_name", "email", "activation_status", "profile_language",
)


async def reload() -> None:
    """(Re)load all settings from the DB into the in-process cache."""
    global _cache
    _cache = await db.get_all_settings()


def invalidate() -> None:
    _cache.clear()


def _group(key: str) -> dict[str, Any]:
    val = _cache.get(key)
    return val if isinstance(val, dict) else {}


# ---------------------------------------------------------------------------
# Resolved getters (sync; env default <- DB override)
# ---------------------------------------------------------------------------
def antispam() -> dict[str, Any]:
    db_v = _group("antispam")
    return {
        "rate_limit_max_per_ip": db_v.get("rate_limit_max_per_ip",
                                          config.RATE_LIMIT_MAX_PER_IP),
        "window_sec": db_v.get("window_sec", config.RATE_LIMIT_WINDOW_SEC),
        "cooldown_sec": db_v.get("cooldown_sec", config.MESSAGE_COOLDOWN_SEC),
        "max_input_chars": db_v.get("max_input_chars", config.MAX_INPUT_CHARS),
    }


def escalation() -> dict[str, Any]:
    db_v = _group("escalation")
    return {
        "max_messages_per_session": db_v.get("max_messages_per_session",
                                             config.MAX_MESSAGES_PER_SESSION),
        "unresolved_turns_before_escalate": db_v.get(
            "unresolved_turns_before_escalate", _escalation.OTHER_MAX_TURNS),
        "high_risk_keywords": db_v.get("high_risk_keywords",
                                       list(_escalation._HIGHRISK_KEYWORDS)),
    }


def language() -> dict[str, Any]:
    db_v = _group("language")
    return {
        "default": db_v.get("default", config.DEFAULT_LANGUAGE),
        "supported": db_v.get("supported", list(config.SUPPORTED_LANGUAGES)),
    }


def forbidden_topics() -> dict[str, Any]:
    db_v = _group("forbidden_topics")
    return {
        "topics": db_v.get("topics", []),
        "refusal": db_v.get("refusal", ""),
    }


def system_prompt() -> dict[str, str]:
    """Resolved Layer-1 core sections: stored overrides merged over the shipped
    defaults. The runtime core itself is served from the published prompt
    version; this is the editor's source of truth, so it always round-trips a
    full set of section keys.
    """
    db_v = _group("system_prompt")
    stored = db_v.get("sections")
    out = prompts.default_sections()
    if isinstance(stored, dict):
        for key, body in stored.items():
            if key in out and isinstance(body, str) and body.strip():
                out[key] = body
    return out


def test_profile() -> dict[str, Any]:
    """Resolved test/dev sandbox profile: stored overrides merged over defaults.

    Always returns a full set of keys so the admin editor round-trips cleanly and
    `api/chat.create_session` can read it without `.get(...)` guards.
    """
    db_v = _group("test_profile")
    out = dict(_DEFAULT_TEST_PROFILE)
    if isinstance(db_v, dict):
        for key in out:
            if key in db_v:
                out[key] = db_v[key]
    return out


def resolved_all() -> dict[str, Any]:
    return {
        "escalation": escalation(),
        "forbidden_topics": forbidden_topics(),
        "language": language(),
        "antispam": antispam(),
    }


# ---------------------------------------------------------------------------
# Validation (pure; raises ValueError on bad input)
# ---------------------------------------------------------------------------
def _require_int(d: dict, field: str, lo: int, hi: int) -> None:
    if field in d:
        v = d[field]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{field} must be an integer")
        if not (lo <= v <= hi):
            raise ValueError(f"{field} must be between {lo} and {hi}")


def _require_str_list(d: dict, field: str) -> None:
    if field in d:
        v = d[field]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{field} must be a list of strings")


def validate_setting(key: str, value: Any) -> dict[str, Any]:
    """Validate a settings write. Returns the value on success; raises ValueError."""
    if key not in SETTING_KEYS:
        raise ValueError(f"unknown setting key: {key!r}")
    if not isinstance(value, dict):
        raise ValueError("setting value must be a JSON object")

    if key == "antispam":
        _require_int(value, "rate_limit_max_per_ip", 1, 100_000)
        _require_int(value, "window_sec", 1, 86_400)
        _require_int(value, "cooldown_sec", 0, 3_600)
        _require_int(value, "max_input_chars", 1, 100_000)
    elif key == "escalation":
        _require_int(value, "max_messages_per_session", 1, 10_000)
        _require_int(value, "unresolved_turns_before_escalate", 1, 1_000)
        _require_str_list(value, "high_risk_keywords")
    elif key == "language":
        if "default" in value and not isinstance(value["default"], str):
            raise ValueError("default must be a string")
        _require_str_list(value, "supported")
    elif key == "forbidden_topics":
        _require_str_list(value, "topics")
        if "refusal" in value and not isinstance(value["refusal"], str):
            raise ValueError("refusal must be a string")
    return value


def validate_test_profile(value: Any) -> dict[str, Any]:
    """Validate a test-profile write. Returns the merged value; raises ValueError.

    Stored separately from SETTING_KEYS (its own admin endpoint), so it never
    appears in the generic settings editor. `force_lang`/`profile_language` must
    be empty (Auto / none) or a supported language code.
    """
    if not isinstance(value, dict):
        raise ValueError("test_profile value must be a JSON object")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ValueError("enabled must be a boolean")
    for field in _TEST_PROFILE_STR_FIELDS:
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{field} must be a string")
    supported = set(config.SUPPORTED_LANGUAGES)
    for field in ("force_lang", "profile_language"):
        if field in value:
            code = (value[field] or "").strip().lower()
            if code and code not in supported:
                raise ValueError(f"{field} must be empty or a supported language")
            value[field] = code
    # Merge over defaults so the stored row is always a complete, clean object.
    out = dict(_DEFAULT_TEST_PROFILE)
    for key in out:
        if key in value:
            out[key] = value[key]
    return out
