"""config._env_bool: robust boolean env parsing (replaces brittle `not in (...)`)."""
from __future__ import annotations

import pytest

import config


def test_env_bool_truthy(monkeypatch):
    for raw in ("1", "true", "TRUE", "Yes", "on", "  on  "):
        monkeypatch.setenv("X_FLAG", raw)
        assert config._env_bool("X_FLAG", False) is True


def test_env_bool_falsy(monkeypatch):
    # The cases the old `not in ("0","false","False","")` check got wrong.
    for raw in ("0", "false", "FALSE", "No", "off", "  off  "):
        monkeypatch.setenv("X_FLAG", raw)
        assert config._env_bool("X_FLAG", True) is False


def test_env_bool_default_when_unset(monkeypatch):
    monkeypatch.delenv("X_FLAG", raising=False)
    assert config._env_bool("X_FLAG", True) is True
    assert config._env_bool("X_FLAG", False) is False
    monkeypatch.setenv("X_FLAG", "   ")  # blank -> default
    assert config._env_bool("X_FLAG", True) is True


def test_env_bool_rejects_garbage(monkeypatch):
    monkeypatch.setenv("X_FLAG", "maybe")
    with pytest.raises(config.ConfigError):
        config._env_bool("X_FLAG", False)
