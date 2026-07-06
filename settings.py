"""Runtime-tunable settings with precedence:
product_settings (per product) > app_settings (global) > env > default.

Phase 1 read knobs straight from `config`. Phase 2 layers an `app_settings`
table on top so the owner can tune behaviour live (no redeploy). Multi-tenancy
adds a third layer: per-product overrides in `product_settings`, resolved for
the request's product (the `tenancy` contextvar, set by the API layer). Resolved
values are read through the sync getters below, which merge the in-process DB
caches over the env-backed defaults from `config`, field by field. The caches
are populated at startup (`reload()`) and re-populated whenever the admin writes
a setting, so edits are hot.

With no tenancy scope set (tests, scripts, global admin views) resolution stops
at the global layer — exactly the pre-tenancy behaviour.

Reading env defaults at call time (not import time) keeps tests that monkeypatch
`config` working, and means an empty cache transparently falls back to env.
"""
from __future__ import annotations

from typing import Any, Optional

import config
import db
import escalation as _escalation
import prompts as _prompts
import tenancy

# Raw DB values keyed by setting group (e.g. 'antispam' -> {...}). Empty until
# reload(); an empty cache means every getter falls back to env defaults.
_cache: dict[str, Any] = {}

# Per-product overrides: product_id -> {group key -> {...}}. Populated together
# with _cache in reload(); consulted only when the request carries a product
# scope (tenancy.current_product_id()).
_product_cache: dict[int, dict[str, Any]] = {}

# The settings groups the admin may write, and which validator guards each.
# `model` carries the OpenAI tuning knobs (model name, sampling, timeouts,
# concurrency); `general` carries operational knobs that don't fit another group
# (session TTL, contact-button URL, request body cap). Both live in the admin
# panel instead of Railway env. NOTE: the PROMPT itself (Layer 1 core, the
# Layer-3 directives, and the forbidden-topics list) is NOT here — it lives in
# `prompts.py`, the single source of truth, and is not editable from the admin.
SETTING_KEYS = ("escalation", "language", "antispam", "model", "general",
                "retention")

# Retention-bot defaults (photo progression, limits, proactivity, VIP tiers).
# The ordered `vip_tiers` list turns a free-text vip_level string into a numeric
# tier ordinal (its index) so a photo's `level_min` (int) gates by tier. Every
# scalar has an env-backed default in config; the maps/lists default here.
_DEFAULT_RETENTION: dict[str, Any] = {
    "vip_tiers": ["none", "bronze", "silver", "gold", "platinum", "diamond"],
    # tier name (lowercased) -> the highest photo stage that tier may unlock.
    "max_stage_by_tier": {
        "none": 2, "bronze": 2, "silver": 3, "gold": 4,
        "platinum": 4, "diamond": 4,
    },
    # accumulated meaningful player messages required for stages 2 / 3 / 4 ...
    "stage_advance_msgs": [20, 45, 80],
}

# Test/dev sandbox profile. In a real deployment the host site supplies the
# player's `user_context` over the signed handshake; in test/dev (no
# WIDGET_HANDSHAKE_SECRET) there is no host, so this stored profile stands in for
# it. It feeds the same Layer-3 fields the model sees (id/full_name/email/
# activation_status/...). The session language is always the browser language,
# so there are no language knobs here.
# `enabled` gates the whole thing: off ⇒ fall back to the widget-supplied context.
_DEFAULT_TEST_PROFILE: dict[str, Any] = {
    "enabled": True,
    "id": "demo-12345",
    "full_name": "Test Player",
    "email": "test.player@example.com",
    "activation_status": "active",
    "country": "Germany",
    "balance": "1500.00 EUR",
    "vip_level": "Silver",
    "registration_date": "2024-01-15",
}

# String fields that round-trip the player context (mirrors prompts._CONTEXT_FIELDS).
# Validated as plain strings. To surface a new field to the model, add it here AND
# to prompts._CONTEXT_FIELDS AND to the user_context built in
# api/chat.create_session AND to the admin Test-sandbox form.
_TEST_PROFILE_STR_FIELDS = (
    "id", "full_name", "email", "activation_status",
    "country", "balance", "vip_level", "registration_date",
)


async def reload() -> None:
    """(Re)load all settings (global + per-product) into the in-process caches."""
    global _cache, _product_cache
    _cache = await db.get_all_settings()
    _product_cache = await db.get_all_product_settings()


def invalidate() -> None:
    _cache.clear()
    _product_cache.clear()


def _group(key: str, product_id: Optional[int] = None) -> dict[str, Any]:
    """The stored override object for a group: product layer merged over global.

    The merge is FIELD-level ({**global, **product}), so a product only shadows
    the knobs it actually stores and inherits the rest from the deploy-wide
    override (which in turn falls back to env/defaults in the getters below).
    `product_id` defaults to the request's tenancy scope; None ⇒ global only.
    """
    val = _cache.get(key)
    out = dict(val) if isinstance(val, dict) else {}
    pid = product_id if product_id is not None else tenancy.current_product_id()
    if pid is not None:
        prod = (_product_cache.get(pid) or {}).get(key)
        if isinstance(prod, dict):
            out.update(prod)
    return out


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
        "recaptcha_min_score": db_v.get("recaptcha_min_score",
                                        config.RECAPTCHA_MIN_SCORE),
        "injection_hard_block": db_v.get("injection_hard_block",
                                         config.INJECTION_HARD_BLOCK),
        "low_content_block": db_v.get("low_content_block",
                                      config.LOW_CONTENT_BLOCK),
        "min_meaningful_chars": db_v.get("min_meaningful_chars",
                                         config.MIN_MEANINGFUL_CHARS),
    }


def model() -> dict[str, Any]:
    """Resolved OpenAI tuning knobs: app_settings override over env defaults.

    Read live by `openai_client` on every call (model/reasoning_effort/verbosity/
    max tokens/switch timeout/attempts) so edits are hot. `request_timeout_sec` and
    `max_concurrent_per_key` are bound when the client is constructed, so the
    admin write also calls `openai_client.reset()` to rebuild it.
    """
    db_v = _group("model")
    return {
        "model": db_v.get("model", config.OPENAI_MODEL),
        "reasoning_effort": db_v.get("reasoning_effort",
                                     config.OPENAI_REASONING_EFFORT),
        "verbosity": db_v.get("verbosity", config.OPENAI_VERBOSITY),
        "max_output_tokens": db_v.get("max_output_tokens",
                                      config.OPENAI_MAX_OUTPUT_TOKENS),
        "request_timeout_sec": db_v.get("request_timeout_sec",
                                        config.OPENAI_REQUEST_TIMEOUT_SEC),
        "key_switch_timeout_sec": db_v.get("key_switch_timeout_sec",
                                           config.OPENAI_KEY_SWITCH_TIMEOUT_SEC),
        "max_attempts": db_v.get("max_attempts", config.OPENAI_MAX_ATTEMPTS),
        "max_concurrent_per_key": db_v.get("max_concurrent_per_key",
                                           config.OPENAI_MAX_CONCURRENT_PER_KEY),
    }


def escalation() -> dict[str, Any]:
    """The pre-model keyword-trigger lists — edited from the admin Prompt →
    Prompt variables sub-tab (content tuning, alongside the prompt), NOT from
    the Settings tab. The technical message cap moved to the `general` group
    (general() reads a legacy `escalation.max_messages_per_session` override
    as a fallback, so old stored rows keep working)."""
    db_v = _group("escalation")
    return {
        "high_risk_keywords": db_v.get("high_risk_keywords",
                                       list(_escalation._HIGHRISK_KEYWORDS)),
        "human_request_keywords": db_v.get("human_request_keywords",
                                           list(_escalation._HUMAN_KEYWORDS)),
    }


def language() -> dict[str, Any]:
    db_v = _group("language")
    return {
        "default": db_v.get("default", config.DEFAULT_LANGUAGE),
        "supported": db_v.get("supported", list(config.SUPPORTED_LANGUAGES)),
        # Custom display names for languages added from the admin panel beyond the
        # built-in `language.LANG_NAMES` set (code -> name). Empty by default.
        "names": db_v.get("names", {}),
    }


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


def prompt_variables() -> dict[str, str]:
    """Resolved prompt variables: admin overrides over the defaults in prompts.py.

    The prompt in prompts.py is a dry template; these values (persona name, brand,
    products, tone of voice, …) uniquify it per brand. Stored under its own
    app_settings key (like test_profile) with its own admin endpoint, so it never
    appears in the generic settings editor. An empty override falls back to the
    default, so clearing a field in the admin restores the built-in wording.
    """
    db_v = _group("prompt_variables")
    out = {key: default for key, _desc, default in _prompts.PROMPT_VARIABLES}
    if isinstance(db_v, dict):
        for key in out:
            v = db_v.get(key)
            if isinstance(v, str) and v.strip():
                out[key] = v.strip()
    return out


def translations() -> dict[str, Any]:
    """Raw per-language copy overrides ({lang: {key: text}}), empty by default.

    Defaults live in translations.py; resolution (override > default > English)
    happens there via translations.text(). Stored under its own app_settings /
    product_settings key with its own admin endpoint (the Translations tab).

    Unlike the flat groups, the product layer merges PER LANGUAGE: a product
    that overrides only `ru.greeting` still inherits the global `ru.support`
    override — a plain object spread would silently drop it.
    """
    glob = _cache.get("translations")
    glob = glob if isinstance(glob, dict) else {}
    pid = tenancy.current_product_id()
    prod: dict[str, Any] = {}
    if pid is not None:
        p = (_product_cache.get(pid) or {}).get("translations")
        prod = p if isinstance(p, dict) else {}
    if not prod:
        return glob
    out: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v
                           for k, v in glob.items()}
    for lang, entries in prod.items():
        if isinstance(entries, dict):
            merged = out.get(lang)
            merged = dict(merged) if isinstance(merged, dict) else {}
            merged.update(entries)
            out[lang] = merged
        else:
            out[lang] = entries
    return out


def general() -> dict[str, Any]:
    """Resolved operational knobs that don't belong to another group: session
    and admin-token lifetimes, the per-session message cap, the prompt history
    window, and the request body cap.

    `max_messages_per_session` used to live in the `escalation` group (its UI
    home was the raw Settings JSON editor); it is a technical limit, so it now
    resolves here — with the legacy `escalation` override still honoured so an
    existing DB row keeps working until the owner re-saves.

    `contact_form_url` is a LEGACY fallback only (not shown in the Settings UI):
    the per-language contact-button URL is edited in the admin Translations tab
    (the `contact_url` key); escalation.build_payload falls back to this value
    (old DB override → env CONTACT_FORM_URL) when no per-language URL is set.
    """
    db_v = _group("general")
    legacy_esc = _group("escalation")
    return {
        "session_ttl_hours": db_v.get("session_ttl_hours", config.SESSION_TTL_HOURS),
        "admin_token_ttl_min": db_v.get("admin_token_ttl_min",
                                        config.ADMIN_TOKEN_TTL_MIN),
        "max_messages_per_session": db_v.get(
            "max_messages_per_session",
            legacy_esc.get("max_messages_per_session",
                           config.MAX_MESSAGES_PER_SESSION)),
        "history_max_turns": db_v.get("history_max_turns", config.HISTORY_MAX_TURNS),
        "contact_form_url": db_v.get("contact_form_url", config.CONTACT_FORM_URL),
        "body_max_bytes": db_v.get("body_max_bytes", config.BODY_MAX_BYTES),
    }


def retention() -> dict[str, Any]:
    """Resolved retention-bot knobs (photo progression, limits, proactivity).

    Precedence product_settings > app_settings > env/default, like every group,
    so a partner can tune its own casino's pacing without touching another's.
    """
    db_v = _group("retention")
    return {
        "daily_photo_cap": db_v.get("daily_photo_cap",
                                    config.RETENTION_DAILY_PHOTO_CAP),
        "proactive_photo_cooldown_msgs": db_v.get(
            "proactive_photo_cooldown_msgs",
            config.RETENTION_PROACTIVE_COOLDOWN_MSGS),
        "candidate_list_size": db_v.get("candidate_list_size",
                                        config.RETENTION_CANDIDATE_LIST_SIZE),
        "stage_advance_msgs": db_v.get("stage_advance_msgs",
                                       list(_DEFAULT_RETENTION["stage_advance_msgs"])),
        "stage_advance_min_hours": db_v.get(
            "stage_advance_min_hours", config.RETENTION_STAGE_ADVANCE_MIN_HOURS),
        "max_stage": db_v.get("max_stage", config.RETENTION_MAX_STAGE),
        "max_stage_by_tier": db_v.get("max_stage_by_tier",
                                      dict(_DEFAULT_RETENTION["max_stage_by_tier"])),
        "vip_tiers": db_v.get("vip_tiers",
                              list(_DEFAULT_RETENTION["vip_tiers"])),
        "nonce_ttl_sec": db_v.get("nonce_ttl_sec", config.RETENTION_NONCE_TTL_SEC),
        "profile_pull_ttl_sec": db_v.get("profile_pull_ttl_sec",
                                         config.RETENTION_PROFILE_PULL_TTL_SEC),
    }


def resolved_all() -> dict[str, Any]:
    return {
        "escalation": escalation(),
        "language": language(),
        "antispam": antispam(),
        "model": model(),
        "general": general(),
        "retention": retention(),
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


def _require_float(d: dict, field: str, lo: float, hi: float) -> None:
    if field in d:
        v = d[field]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{field} must be a number")
        if not (lo <= float(v) <= hi):
            raise ValueError(f"{field} must be between {lo} and {hi}")


def _require_bool(d: dict, field: str) -> None:
    if field in d and not isinstance(d[field], bool):
        raise ValueError(f"{field} must be a boolean")


def _require_nonempty_str(d: dict, field: str) -> None:
    if field in d and (not isinstance(d[field], str) or not d[field].strip()):
        raise ValueError(f"{field} must be a non-empty string")


def _require_str_list(d: dict, field: str) -> None:
    if field in d:
        v = d[field]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{field} must be a list of strings")


def _require_choice(d: dict, field: str, choices: tuple[str, ...],
                    allow_empty: bool = False) -> None:
    """Field, if present, must be a string in `choices` (or "" when allowed)."""
    if field in d:
        v = d[field]
        if not isinstance(v, str):
            raise ValueError(f"{field} must be a string")
        if allow_empty and v == "":
            return
        if v not in choices:
            allowed = ", ".join(choices)
            raise ValueError(f"{field} must be one of: {allowed}")


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
        _require_float(value, "recaptcha_min_score", 0.0, 1.0)
        _require_bool(value, "injection_hard_block")
        _require_bool(value, "low_content_block")
        _require_int(value, "min_meaningful_chars", 1, 100)
    elif key == "model":
        _require_nonempty_str(value, "model")
        # GPT-5 reasoning knobs; "" ⇒ omit the parameter (use model default).
        # "minimal" is the GPT-5 family's lowest tier — almost no hidden reasoning
        # tokens, which is what a KB-grounded support answer usually needs.
        _require_choice(value, "reasoning_effort",
                        ("minimal", "low", "medium", "high"), allow_empty=True)
        _require_choice(value, "verbosity", ("low", "medium", "high"),
                        allow_empty=True)
        _require_int(value, "max_output_tokens", 1, 128_000)
        _require_int(value, "request_timeout_sec", 1, 600)
        _require_int(value, "key_switch_timeout_sec", 1, 600)
        _require_int(value, "max_attempts", 1, 10)
        _require_int(value, "max_concurrent_per_key", 1, 1_000)
    elif key == "general":
        _require_int(value, "session_ttl_hours", 1, 8_760)        # <= 1 year
        _require_int(value, "admin_token_ttl_min", 5, 10_080)     # 5 min..1 week
        _require_int(value, "max_messages_per_session", 1, 10_000)
        _require_int(value, "history_max_turns", 1, 200)
        _require_int(value, "body_max_bytes", 1_024, 104_857_600)  # 1 KiB..100 MiB
        # Legacy: the per-language contact URL now lives in translations
        # (`contact_url`); this field is still accepted so old writes don't 400.
        if "contact_form_url" in value:
            v = value["contact_form_url"]
            if v is not None and not isinstance(v, str):
                raise ValueError("contact_form_url must be a string or null")
    elif key == "escalation":
        # Legacy: the cap moved to `general`; still accepted (and honoured as a
        # fallback in general()) so an old stored row / client doesn't break.
        _require_int(value, "max_messages_per_session", 1, 10_000)
        _require_str_list(value, "high_risk_keywords")
        _require_str_list(value, "human_request_keywords")
    elif key == "retention":
        _require_int(value, "daily_photo_cap", 0, 10_000)
        _require_int(value, "proactive_photo_cooldown_msgs", 1, 10_000)
        _require_int(value, "candidate_list_size", 1, 50)
        _require_int(value, "stage_advance_min_hours", 0, 8_760)
        _require_int(value, "max_stage", 1, 20)
        _require_int(value, "nonce_ttl_sec", 10, 3_600)
        _require_int(value, "profile_pull_ttl_sec", 0, 604_800)  # <= 1 week
        if "stage_advance_msgs" in value:
            v = value["stage_advance_msgs"]
            if (not isinstance(v, list)
                    or not all(isinstance(x, int) and not isinstance(x, bool)
                               and x >= 0 for x in v)):
                raise ValueError("stage_advance_msgs must be a list of "
                                 "non-negative integers")
        _require_str_list(value, "vip_tiers")
        if "max_stage_by_tier" in value:
            m = value["max_stage_by_tier"]
            if not isinstance(m, dict) or not all(
                    isinstance(k, str) and isinstance(x, int)
                    and not isinstance(x, bool) and 1 <= x <= 20
                    for k, x in m.items()):
                raise ValueError("max_stage_by_tier must be a map of "
                                 "tier name -> stage (1..20)")
    elif key == "language":
        import language as _language  # lazy: avoid import cycle at module load
        if "default" in value and not isinstance(value["default"], str):
            raise ValueError("default must be a string")
        _require_str_list(value, "supported")
        # Every supported code must be a real ISO 639-1 code (built-in, in the ISO
        # catalogue, or carrying a custom name in this same write) — so languages
        # are always added with a correct code, never free-typed junk.
        names = value.get("names", {})
        if "names" in value:
            if not isinstance(names, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in names.items()):
                raise ValueError("names must be a map of language code -> name")
            for code in names:
                if code.strip().lower() not in _language.ISO_639_1:
                    raise ValueError(f"{code!r} is not a valid ISO 639-1 language code")
        known = set(_language.ISO_639_1) | {k.strip().lower() for k in names}
        for code in value.get("supported", []):
            if code.strip().lower() not in known:
                raise ValueError(f"{code!r} is not a valid ISO 639-1 language code")
    return value


def validate_test_profile(value: Any) -> dict[str, Any]:
    """Validate a test-profile write. Returns the merged value; raises ValueError.

    Stored separately from SETTING_KEYS (its own admin endpoint), so it never
    appears in the generic settings editor.
    """
    if not isinstance(value, dict):
        raise ValueError("test_profile value must be a JSON object")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ValueError("enabled must be a boolean")
    for field in _TEST_PROFILE_STR_FIELDS:
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"{field} must be a string")
    # Merge over defaults so the stored row is always a complete, clean object.
    out = dict(_DEFAULT_TEST_PROFILE)
    for key in out:
        if key in value:
            out[key] = value[key]
    return out


def validate_prompt_variables(value: Any) -> dict[str, str]:
    """Validate a prompt-variables write. Returns the cleaned map; raises ValueError.

    Only keys registered in prompts.PROMPT_VARIABLES are accepted (the template
    is the single source of truth for which placeholders exist); values must be
    strings. Empty strings are dropped so the resolved value falls back to the
    built-in default. Stored separately from SETTING_KEYS (its own admin endpoint).
    """
    if not isinstance(value, dict):
        raise ValueError("prompt_variables value must be a JSON object")
    known = {key for key, _desc, _default in _prompts.PROMPT_VARIABLES}
    out: dict[str, str] = {}
    for key, v in value.items():
        if key not in known:
            raise ValueError(f"unknown prompt variable: {key!r}")
        if not isinstance(v, str):
            raise ValueError(f"{key} must be a string")
        if v.strip():
            out[key] = v.strip()
    return out


def validate_translations(value: Any) -> dict[str, dict[str, str]]:
    """Validate a translations write ({lang: {key: text}}); raises ValueError.

    Language codes must be real ISO 639-1 codes; copy keys must be registered in
    translations.KEYS. Empty strings are dropped so the resolved copy falls back
    to the built-in default for that language (then English).
    """
    import language as _language     # lazy: avoid import cycles at module load
    import translations as _translations

    if not isinstance(value, dict):
        raise ValueError("translations value must be a JSON object")
    known = {key for key, _scope, _desc in _translations.KEYS}
    out: dict[str, dict[str, str]] = {}
    for lang, entries in value.items():
        code = str(lang).strip().lower()
        if code not in _language.ISO_639_1:
            raise ValueError(f"{lang!r} is not a valid ISO 639-1 language code")
        if not isinstance(entries, dict):
            raise ValueError(f"translations for {code!r} must be a JSON object")
        clean: dict[str, str] = {}
        for key, v in entries.items():
            if key not in known:
                raise ValueError(f"unknown translation key: {key!r}")
            if not isinstance(v, str):
                raise ValueError(f"{code}.{key} must be a string")
            # The contact-button URL must actually be a link — a typo here
            # would ship a dead button to every player in that language.
            if key == "contact_url" and v.strip() and \
                    not v.strip().startswith(("http://", "https://")):
                raise ValueError(
                    f"{code}.contact_url must be an http(s) URL")
            if v.strip():
                clean[key] = v
        if clean:
            out[code] = clean
    return out
