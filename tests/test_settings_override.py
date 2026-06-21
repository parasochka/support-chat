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
