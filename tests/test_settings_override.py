"""Runtime settings: app_settings overrides env; invalid writes rejected."""
from __future__ import annotations

import pytest

import config
import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    yield
    settings.invalidate()


def test_env_default_when_cache_empty(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_PER_IP", 42)
    assert settings.antispam()["rate_limit_max_per_ip"] == 42


def test_db_override_wins_over_env(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_PER_IP", 42)
    settings._cache["antispam"] = {"rate_limit_max_per_ip": 999}
    cfg = settings.antispam()
    assert cfg["rate_limit_max_per_ip"] == 999          # DB override
    assert cfg["max_input_chars"] == config.MAX_INPUT_CHARS  # untouched -> env


def test_escalation_override():
    settings._cache["escalation"] = {"max_messages_per_session": 5,
                                     "high_risk_keywords": ["boom"]}
    cfg = settings.escalation()
    assert cfg["max_messages_per_session"] == 5
    assert cfg["high_risk_keywords"] == ["boom"]


def test_validate_accepts_good():
    v = settings.validate_setting("antispam", {"rate_limit_max_per_ip": 10,
                                              "cooldown_sec": 0})
    assert v["rate_limit_max_per_ip"] == 10


def test_validate_rejects_bad():
    with pytest.raises(ValueError):
        settings.validate_setting("antispam", {"rate_limit_max_per_ip": "lots"})
    with pytest.raises(ValueError):
        settings.validate_setting("antispam", {"window_sec": 0})  # below min
    with pytest.raises(ValueError):
        settings.validate_setting("unknown_key", {})
    with pytest.raises(ValueError):
        settings.validate_setting("escalation", {"high_risk_keywords": [1, 2]})
    with pytest.raises(ValueError):
        settings.validate_setting("language", "not-a-dict")


# --- model tuning group (migrated from Railway env) -------------------------
def test_model_env_default_when_cache_empty(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-x")
    monkeypatch.setattr(config, "OPENAI_TEMPERATURE", 0.7)
    m = settings.model()
    assert m["model"] == "gpt-x"
    assert m["temperature"] == 0.7
    assert m["max_attempts"] == config.OPENAI_MAX_ATTEMPTS


def test_model_db_override_wins_over_env(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-x")
    settings._cache["model"] = {"model": "gpt-tuned", "temperature": 0.1}
    m = settings.model()
    assert m["model"] == "gpt-tuned"                 # DB override
    assert m["temperature"] == 0.1
    assert m["max_output_tokens"] == config.OPENAI_MAX_OUTPUT_TOKENS  # untouched


def test_model_validate_accepts_good():
    v = settings.validate_setting("model", {
        "model": "gpt-4o-mini", "temperature": 0.3, "max_output_tokens": 700,
        "request_timeout_sec": 40, "key_switch_timeout_sec": 25,
        "max_attempts": 3, "max_concurrent_per_key": 4,
    })
    assert v["model"] == "gpt-4o-mini"


def test_model_validate_rejects_bad():
    with pytest.raises(ValueError):
        settings.validate_setting("model", {"model": ""})          # empty name
    with pytest.raises(ValueError):
        settings.validate_setting("model", {"temperature": 5})     # > 2.0
    with pytest.raises(ValueError):
        settings.validate_setting("model", {"max_attempts": 0})    # below min


# --- antispam fields folded in from env ------------------------------------
def test_antispam_new_fields_default_to_env(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_MIN_SCORE", 0.9)
    monkeypatch.setattr(config, "INJECTION_HARD_BLOCK", True)
    cfg = settings.antispam()
    assert cfg["recaptcha_min_score"] == 0.9
    assert cfg["injection_hard_block"] is True


def test_antispam_validate_new_fields():
    settings.validate_setting("antispam", {"recaptcha_min_score": 0.5,
                                           "injection_hard_block": True})
    with pytest.raises(ValueError):
        settings.validate_setting("antispam", {"recaptcha_min_score": 2})  # > 1.0
    with pytest.raises(ValueError):
        settings.validate_setting("antispam", {"injection_hard_block": "yes"})


# --- general group (session TTL / contact URL / body cap) ------------------
def test_general_env_default_when_cache_empty(monkeypatch):
    monkeypatch.setattr(config, "SESSION_TTL_HOURS", 12)
    monkeypatch.setattr(config, "CONTACT_FORM_URL", "https://x/support")
    g = settings.general()
    assert g["session_ttl_hours"] == 12
    assert g["contact_form_url"] == "https://x/support"
    assert g["body_max_bytes"] == config.BODY_MAX_BYTES


def test_general_db_override_wins(monkeypatch):
    monkeypatch.setattr(config, "SESSION_TTL_HOURS", 12)
    settings._cache["general"] = {"session_ttl_hours": 48}
    assert settings.general()["session_ttl_hours"] == 48


def test_general_validate():
    settings.validate_setting("general", {"session_ttl_hours": 24,
                                          "body_max_bytes": 65536,
                                          "contact_form_url": "https://x"})
    settings.validate_setting("general", {"contact_form_url": None})  # null ok
    with pytest.raises(ValueError):
        settings.validate_setting("general", {"session_ttl_hours": 0})  # below min
    with pytest.raises(ValueError):
        settings.validate_setting("general", {"body_max_bytes": 1})     # below min
    with pytest.raises(ValueError):
        settings.validate_setting("general", {"contact_form_url": 123})  # not str


def test_language_accessors_follow_settings(monkeypatch):
    import language
    monkeypatch.setattr(config, "DEFAULT_LANGUAGE", "ru")
    assert language.default_code() == "ru"          # env default via settings
    settings._cache["language"] = {"default": "es", "supported": ["es", "en"]}
    assert language.default_code() == "es"           # DB override wins
    assert language.supported_codes() == ["es", "en"]
