"""config secret hygiene: on a real deployment the three purpose-specific
secrets must not silently reuse SESSION_JWT_SECRET, and no secret may be weak —
both fail fast on boot. The trigger is fail-closed: it fires on
APP_ENV=production OR a non-local DATABASE_URL, so a forgotten APP_ENV can't
disable the check."""
from __future__ import annotations

import pytest

import config


def _set(monkeypatch, *, production, test_mode, db_remote,
         admin_fb, master_fb, telegram_fb):
    monkeypatch.setattr(config, "IS_PRODUCTION", production)
    monkeypatch.setattr(config, "_TEST_MODE", test_mode)
    monkeypatch.setattr(config, "_DB_IS_REMOTE", db_remote)
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET_IS_FALLBACK", admin_fb)
    monkeypatch.setattr(config, "SECRETS_MASTER_KEY_IS_FALLBACK", master_fb)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK", telegram_fb)


def test_production_with_reused_secrets_refuses_to_boot(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_production_secrets()
    msg = str(exc.value)
    assert "ADMIN_JWT_SECRET" in msg
    assert "SECRETS_MASTER_KEY" in msg
    assert "TELEGRAM_WEBHOOK_SECRET" in msg


def test_production_reports_only_the_reused_ones(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=False, master_fb=True, telegram_fb=False)
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_production_secrets()
    msg = str(exc.value)
    assert "SECRETS_MASTER_KEY" in msg
    assert "ADMIN_JWT_SECRET" not in msg
    assert "TELEGRAM_WEBHOOK_SECRET" not in msg


def test_production_with_distinct_secrets_boots(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=False, master_fb=False, telegram_fb=False)
    config._enforce_production_secrets()  # must not raise


def test_remote_db_enforces_even_without_production_flag(monkeypatch):
    """Fail-closed: a non-local DATABASE_URL alone triggers enforcement, so a
    real deploy that forgot APP_ENV=production is still protected."""
    _set(monkeypatch, production=False, test_mode=False, db_remote=True,
         admin_fb=True, master_fb=False, telegram_fb=False)
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_production_secrets()
    assert "ADMIN_JWT_SECRET" in str(exc.value)


def test_local_dev_only_warns_not_fatal(monkeypatch):
    # A genuinely local run (loopback DB, not production, not test mode) keeps
    # working with zero secret config — no raise even on full reuse.
    _set(monkeypatch, production=False, test_mode=False, db_remote=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    config._enforce_production_secrets()  # must not raise


def test_test_mode_short_circuits(monkeypatch):
    # SUPPORT_CHAT_TEST_MODE placeholders are always fallbacks; never fatal.
    _set(monkeypatch, production=True, test_mode=True, db_remote=True,
         admin_fb=True, master_fb=True, telegram_fb=True)
    config._enforce_production_secrets()  # must not raise


# --- secret strength --------------------------------------------------------
_STRONG = "0123456789abcdef0123456789abcdef"  # 32 chars


def test_strength_rejects_short_session_secret(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    monkeypatch.setattr(config, "SESSION_JWT_SECRET", "short")
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_secret_strength()
    assert "SESSION_JWT_SECRET" in str(exc.value)


def test_strength_rejects_short_explicit_secret(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=False, master_fb=True, telegram_fb=True)
    monkeypatch.setattr(config, "SESSION_JWT_SECRET", _STRONG)
    monkeypatch.setattr(config, "ADMIN_JWT_SECRET", "weak")
    with pytest.raises(config.ConfigError) as exc:
        config._enforce_secret_strength()
    assert "ADMIN_JWT_SECRET" in str(exc.value)


def test_strength_passes_with_strong_secrets(monkeypatch):
    _set(monkeypatch, production=True, test_mode=False, db_remote=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    monkeypatch.setattr(config, "SESSION_JWT_SECRET", _STRONG)
    config._enforce_secret_strength()  # must not raise


def test_strength_skipped_in_local_dev(monkeypatch):
    _set(monkeypatch, production=False, test_mode=False, db_remote=False,
         admin_fb=True, master_fb=True, telegram_fb=True)
    monkeypatch.setattr(config, "SESSION_JWT_SECRET", "short")
    config._enforce_secret_strength()  # local dev stays lenient


def test_db_host_is_local_classification():
    assert config._db_host_is_local("postgresql://u:p@localhost:5432/db")
    assert config._db_host_is_local("postgresql://u:p@127.0.0.1/db")
    assert not config._db_host_is_local(
        "postgresql://u:p@db.internal.railway.app:5432/db")
