"""config._enforce_production_secrets: in production the three purpose-specific
secrets must not silently reuse SESSION_JWT_SECRET (fail fast on boot)."""
from __future__ import annotations

import pytest

import config


def _set(monkeypatch, *, production, test_mode,
         admin_fb, master_fb, telegram_fb):
    monkeypatch.setattr(config, "IS_PRODUCTION", production)
    monkeypatch.setattr(config, "_TEST_MODE", test_mode)
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET_IS_FALLBACK", admin_fb)
    monkeypatch.setattr(config, "SECRETS_MASTER_KEY_IS_FALLBACK", master_fb)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK", telegram_fb)


def test_production_with_reused_secrets_refuses_to_boot(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_production_secrets()
    msg = str(exc.value)
    assert "ADMIN_JWT_SECRET" in msg
    assert "SECRETS_MASTER_KEY" in msg
    assert "TELEGRAM_WEBHOOK_SECRET" in msg


def test_production_reports_only_the_reused_ones(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False,
         admin_fb=False, master_fb=True, telegram_fb=False)
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_production_secrets()
    msg = str(exc.value)
    assert "SECRETS_MASTER_KEY" in msg
    assert "ADMIN_JWT_SECRET" not in msg
    assert "TELEGRAM_WEBHOOK_SECRET" not in msg


def test_production_with_distinct_secrets_boots(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False,
         admin_fb=False, master_fb=False, telegram_fb=False)
    config._enforce_production_secrets()  # must not raise


def test_development_only_warns_not_fatal(monkeypatch):
    # Dev keeps working with zero secret config — no raise even on full reuse.
    _set(monkeypatch, production=False, test_mode=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    config._enforce_production_secrets()  # must not raise


def test_test_mode_short_circuits(monkeypatch):
    # SUPPORT_CHAT_TEST_MODE placeholders are always fallbacks; never fatal.
    _set(monkeypatch, production=True, test_mode=True,
         admin_fb=True, master_fb=True, telegram_fb=True)
    config._enforce_production_secrets()  # must not raise
