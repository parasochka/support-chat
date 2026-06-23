"""Capture the current env-resolved tuning into `app_settings` (migration).

The `antispam` and `model` setting groups resolve `app_settings > env > default`.
To let the owner DELETE these vars from Railway without any behaviour change, we
snapshot the values currently in effect (env, or the built-in default) into the
DB the first time each field is seen — after which they live in the admin panel.

Idempotent and non-destructive: any field already present in `app_settings`
(i.e. the owner edited it in the panel, or a previous boot seeded it) is left
untouched; only missing fields are filled. Run BEFORE removing the env vars so
the real Railway values are captured.
"""
from __future__ import annotations

from typing import Any, Callable

import config
import db

# Each group maps a stored field name -> a thunk reading the current effective
# value from config (env override, else the in-code default). Mirrors the field
# sets resolved in settings.antispam() / settings.model().
_GROUPS: dict[str, dict[str, Callable[[], Any]]] = {
    "antispam": {
        "rate_limit_max_per_ip": lambda: config.RATE_LIMIT_MAX_PER_IP,
        "window_sec": lambda: config.RATE_LIMIT_WINDOW_SEC,
        "cooldown_sec": lambda: config.MESSAGE_COOLDOWN_SEC,
        "max_input_chars": lambda: config.MAX_INPUT_CHARS,
        "recaptcha_min_score": lambda: config.RECAPTCHA_MIN_SCORE,
        "injection_hard_block": lambda: config.INJECTION_HARD_BLOCK,
        "low_content_block": lambda: config.LOW_CONTENT_BLOCK,
        "min_meaningful_chars": lambda: config.MIN_MEANINGFUL_CHARS,
    },
    "model": {
        "model": lambda: config.OPENAI_MODEL,
        "reasoning_effort": lambda: config.OPENAI_REASONING_EFFORT,
        "verbosity": lambda: config.OPENAI_VERBOSITY,
        "max_output_tokens": lambda: config.OPENAI_MAX_OUTPUT_TOKENS,
        "request_timeout_sec": lambda: config.OPENAI_REQUEST_TIMEOUT_SEC,
        "key_switch_timeout_sec": lambda: config.OPENAI_KEY_SWITCH_TIMEOUT_SEC,
        "max_attempts": lambda: config.OPENAI_MAX_ATTEMPTS,
        "max_concurrent_per_key": lambda: config.OPENAI_MAX_CONCURRENT_PER_KEY,
    },
    # Operational knobs that don't fit another group.
    "general": {
        "session_ttl_hours": lambda: config.SESSION_TTL_HOURS,
        "contact_form_url": lambda: config.CONTACT_FORM_URL,
        "body_max_bytes": lambda: config.BODY_MAX_BYTES,
    },
    # Only the env-backed field is snapshotted here; the other escalation knobs
    # (unresolved-turns, high-risk keywords) are in-code defaults, not env, so
    # leaving them unset lets settings.escalation() resolve them as before.
    "language": {
        "default": lambda: config.DEFAULT_LANGUAGE,
        "supported": lambda: list(config.SUPPORTED_LANGUAGES),
    },
    "escalation": {
        "max_messages_per_session": lambda: config.MAX_MESSAGES_PER_SESSION,
    },
}


# Models whose stored `model` override is rewritten to config.OPENAI_MODEL on
# boot. The generic snapshot above only fills MISSING fields, so a deployment
# that already seeded the legacy default would stay pinned to it; this one-time
# migration actively flips it to the new family. Owner-chosen GPT-5 models (or
# anything not in this set) are left untouched.
_LEGACY_MODEL_PREFIXES = ("gpt-4", "gpt-3")


async def _migrate_model_group(existing: dict) -> None:
    """Move an existing `model` group onto the GPT-5 reasoning knobs.

    Idempotent: drops the dead `temperature` key, flips a legacy `model`
    override to the new default, and backfills reasoning_effort/verbosity. A row
    already on a GPT-5 model with no temperature key is left as-is.
    """
    current = dict(existing.get("model") or {})
    if not current:
        return  # nothing stored yet; the generic snapshot seeds clean values
    changed = False
    if "temperature" in current:           # dead knob — reasoning models reject it
        current.pop("temperature")
        changed = True
    name = current.get("model")
    if isinstance(name, str) and name.lower().startswith(_LEGACY_MODEL_PREFIXES):
        current["model"] = config.OPENAI_MODEL
        changed = True
    for field, default in (("reasoning_effort", config.OPENAI_REASONING_EFFORT),
                           ("verbosity", config.OPENAI_VERBOSITY)):
        if field not in current:
            current[field] = default
            changed = True
    if changed:
        await db.set_setting("model", current, updated_by="migration")


async def run() -> None:
    existing = await db.get_all_settings()
    for group, fields in _GROUPS.items():
        current = dict(existing.get(group) or {})
        changed = False
        for field, read in fields.items():
            if field not in current:
                current[field] = read()
                changed = True
        if changed:
            await db.set_setting(group, current, updated_by="migration")
    # Run after the generic snapshot so a freshly-seeded `model` row is also
    # normalised (legacy default flipped, temperature dropped).
    existing = await db.get_all_settings()
    await _migrate_model_group(existing)
