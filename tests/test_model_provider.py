"""Provider switch (openai | deepseek): settings resolution, request shape,
pricing overrides, env/product key routing."""
from __future__ import annotations

from typing import Any

import pytest

import config
import openai_client
import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    openai_client.reset()
    yield
    settings.invalidate()
    openai_client.reset()


# ---------------------------------------------------------------------------
# settings.model() resolution
# ---------------------------------------------------------------------------
def test_default_provider_is_openai():
    m = settings.model()
    assert m["provider"] == "openai"
    assert m["model"] == config.OPENAI_MODEL
    assert m["base_url"] == ""
    assert m["extra_params"] == {}


def test_deepseek_provider_defaults():
    settings._cache["model"] = {"provider": "deepseek"}
    m = settings.model()
    assert m["provider"] == "deepseek"
    assert m["model"] == config.DEEPSEEK_MODEL
    assert m["base_url"] == config.DEEPSEEK_BASE_URL
    # DeepSeek sends no reasoning knobs by default.
    assert m["reasoning_effort"] == ""
    assert m["verbosity"] == ""


def test_provider_config_json_wins_and_extras_pass_through():
    settings._cache["model"] = {
        "provider": "deepseek",
        "deepseek_config": {
            "model": "deepseek-reasoner",
            "max_output_tokens": 3000,
            "temperature": 1.3,
            "top_p": 0.9,
        },
    }
    m = settings.model()
    assert m["model"] == "deepseek-reasoner"
    assert m["max_output_tokens"] == 3000
    assert m["extra_params"] == {"temperature": 1.3, "top_p": 0.9}


def test_openai_legacy_flat_fields_still_resolve():
    # Pre-provider-split stored rows keep working (provider defaults to openai).
    settings._cache["model"] = {"model": "gpt-tuned", "reasoning_effort": "medium"}
    m = settings.model()
    assert m["provider"] == "openai"
    assert m["model"] == "gpt-tuned"
    assert m["reasoning_effort"] == "medium"


def test_openai_config_json_beats_legacy_flat():
    settings._cache["model"] = {
        "model": "gpt-old-flat",
        "openai_config": {"model": "gpt-from-json"},
    }
    assert settings.model()["model"] == "gpt-from-json"


def test_inactive_provider_config_does_not_leak():
    settings._cache["model"] = {
        "provider": "openai",
        "deepseek_config": {"model": "deepseek-reasoner", "temperature": 1.5},
    }
    m = settings.model()
    assert m["model"] == config.OPENAI_MODEL
    assert m["extra_params"] == {}


def test_pricing_overrides_collected_from_both_configs():
    settings._cache["model"] = {
        "provider": "deepseek",
        "deepseek_config": {
            "model": "deepseek-b4-pro",
            "pricing": {"input_per_1m": 0.5, "cached_input_per_1m": 0.05,
                        "output_per_1m": 1.5},
        },
        "openai_config": {
            "model": "gpt-custom",
            "pricing": {"input_per_1m": 1.0, "cached_input_per_1m": 0.1,
                        "output_per_1m": 4.0},
        },
    }
    m = settings.model()
    assert m["pricing_overrides"]["deepseek-b4-pro"] == (0.5, 0.05, 1.5)
    assert m["pricing_overrides"]["gpt-custom"] == (1.0, 0.1, 4.0)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_validate_accepts_provider_and_configs():
    v = settings.validate_setting("model", {
        "provider": "deepseek",
        "deepseek_config": {
            "model": "deepseek-chat", "max_output_tokens": 2000,
            "temperature": 1.3,
            "pricing": {"input_per_1m": 0.28, "cached_input_per_1m": 0.028,
                        "output_per_1m": 0.42},
        },
    })
    assert v["provider"] == "deepseek"


def test_validate_rejects_bad_provider_and_configs():
    with pytest.raises(ValueError):
        settings.validate_setting("model", {"provider": "anthropic"})
    with pytest.raises(ValueError):
        settings.validate_setting("model", {"openai_config": "not-a-dict"})
    with pytest.raises(ValueError):  # secrets never ride in settings
        settings.validate_setting(
            "model", {"deepseek_config": {"api_key": "sk-x"}})
    with pytest.raises(ValueError):  # bounds shared with the flat fields
        settings.validate_setting(
            "model", {"deepseek_config": {"max_output_tokens": 0}})
    with pytest.raises(ValueError):  # pricing shape is fixed
        settings.validate_setting(
            "model", {"deepseek_config": {"pricing": {"per_token": 1}}})
    with pytest.raises(ValueError):
        settings.validate_setting(
            "model", {"openai_config": {"base_url": "ftp://x"}})


# ---------------------------------------------------------------------------
# request shape per provider (kwargs seen by chat.completions.create)
# ---------------------------------------------------------------------------
class _Capture:
    def __init__(self):
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5,
                               "prompt_tokens_details": None})()
        choice = type("C", (), {
            "message": type("M", (), {"content": "ok"})(),
            "finish_reason": "stop",
        })()
        return type("R", (), {"choices": [choice], "usage": usage})()


def _key_client_with_capture() -> tuple[Any, _Capture]:
    kc = openai_client._KeyClient("primary", "k")
    cap = _Capture()
    kc.client = type("Cl", (), {"chat": type(
        "Ch", (), {"completions": cap})()})()
    return kc, cap


async def test_openai_request_shape():
    settings._cache["model"] = {"provider": "openai",
                                "openai_config": {"model": "gpt-5-mini",
                                                  "max_output_tokens": 700}}
    kc, cap = _key_client_with_capture()
    await kc.call([{"role": "user", "content": "hi"}])
    assert cap.kwargs["max_completion_tokens"] == 700
    assert "max_tokens" not in cap.kwargs
    assert cap.kwargs["store"] is False
    assert cap.kwargs["reasoning_effort"] == config.OPENAI_REASONING_EFFORT


async def test_deepseek_request_shape_and_extras():
    settings._cache["model"] = {
        "provider": "deepseek",
        "deepseek_config": {"model": "deepseek-v4-flash",
                            "max_output_tokens": 900,
                            "temperature": 1.3},
    }
    kc, cap = _key_client_with_capture()
    await kc.call([{"role": "user", "content": "hi"}])
    assert cap.kwargs["model"] == "deepseek-v4-flash"
    assert cap.kwargs["max_tokens"] == 900
    assert "max_completion_tokens" not in cap.kwargs
    assert "store" not in cap.kwargs
    assert "reasoning_effort" not in cap.kwargs
    # Free-form params ride in extra_body (the SDK rejects unknown kwargs).
    assert cap.kwargs["extra_body"] == {"temperature": 1.3}


async def test_deepseek_thinking_mode_params():
    settings._cache["model"] = {
        "provider": "deepseek",
        "deepseek_config": {"model": "deepseek-v4-flash",
                            "reasoning_effort": "high",
                            "thinking": {"type": "enabled"}},
    }
    kc, cap = _key_client_with_capture()
    await kc.call([{"role": "user", "content": "hi"}])
    assert cap.kwargs["extra_body"] == {
        "thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    assert "reasoning_effort" not in cap.kwargs  # body field, not a kwarg


# ---------------------------------------------------------------------------
# pricing / cost accounting
# ---------------------------------------------------------------------------
def test_builtin_deepseek_pricing_known():
    # v4-flash: $0.14 in / $0.28 out per 1M; deepseek-chat aliases it.
    assert openai_client.compute_cost(
        "deepseek-v4-flash", 1_000_000, 0, 0) == pytest.approx(0.14)
    assert openai_client.compute_cost(
        "deepseek-v4-flash", 0, 1_000_000, 0) == pytest.approx(0.28)
    assert openai_client.compute_cost(
        "deepseek-chat", 1_000_000, 0, 0) == pytest.approx(0.14)
    assert openai_client.compute_cost(
        "deepseek-v4-pro", 1_000_000, 0, 0) == pytest.approx(0.435)


def test_pricing_override_feeds_compute_cost():
    settings._cache["model"] = {
        "provider": "deepseek",
        "deepseek_config": {
            "model": "deepseek-b4-pro",
            "pricing": {"input_per_1m": 1.0, "cached_input_per_1m": 0.1,
                        "output_per_1m": 2.0},
        },
    }
    cost = openai_client.compute_cost("deepseek-b4-pro",
                                      1_000_000, 500_000, 0)
    assert cost == pytest.approx(1.0 + 0.5 * 2.0)
    assert openai_client.pricing_for_model("deepseek-b4-pro") == {
        "input_per_1m": 1.0, "cached_input_per_1m": 0.1, "output_per_1m": 2.0}


# ---------------------------------------------------------------------------
# client routing (env + product keys per provider)
# ---------------------------------------------------------------------------
def test_env_client_per_provider(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "ds-env-key")
    settings._cache["model"] = {"provider": "openai"}
    c1 = openai_client.get_client()
    assert c1.key_source == "env"
    settings._cache["model"] = {"provider": "deepseek"}
    c2 = openai_client.get_client()
    assert c2.key_source == "env:deepseek"
    assert c2.primary.api_key == "ds-env-key"
    assert c2.primary.base_url == config.DEEPSEEK_BASE_URL
    assert c1 is not c2


async def test_client_for_product_uses_deepseek_keys(monkeypatch):
    import db

    async def fake_ds_keys(pid):
        return {"primary": "ds-prod-key", "fallback": None}

    async def fail_openai_keys(pid):  # must not be consulted
        raise AssertionError("openai keys read under deepseek provider")

    monkeypatch.setattr(db, "get_product_deepseek_keys", fake_ds_keys)
    monkeypatch.setattr(db, "get_product_openai_keys", fail_openai_keys)
    settings._cache["model"] = {"provider": "deepseek"}
    client = await openai_client.client_for_product(42)
    assert client.key_source == "product:42"
    assert client.primary.api_key == "ds-prod-key"
    assert client.primary.base_url == config.DEEPSEEK_BASE_URL
