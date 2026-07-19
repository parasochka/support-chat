"""Admin-restructure additions: new retention knobs (silent notifications,
subscription-cache TTL), the English-only guard for model-facing content, and
the public model-pricing accessor behind the admin token/cost counters."""
from __future__ import annotations

import pytest

import openai_client
import retention
import settings


# ---------------------------------------------------------------------------
# new retention knobs
# ---------------------------------------------------------------------------
def test_retention_settings_carry_new_knobs():
    cfg = settings.retention()
    assert cfg["silent_notifications"] is False  # env default: off
    assert cfg["subscription_cache_ttl_sec"] == 600


def test_validate_retention_accepts_new_knobs():
    settings.validate_setting("retention", {
        "silent_notifications": True,
        "subscription_cache_ttl_sec": 0,
    })


@pytest.mark.parametrize("payload", [
    {"silent_notifications": "yes"},
    {"subscription_cache_ttl_sec": -1},
    {"subscription_cache_ttl_sec": 100_000},
])
def test_validate_retention_rejects_bad_new_knobs(payload):
    with pytest.raises(ValueError):
        settings.validate_setting("retention", payload)


# ---------------------------------------------------------------------------
# subscription-cache TTL honoured (0 = never cache)
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.calls = 0

    async def subscription_state(self, channel, tg_user_id):
        self.calls += 1
        return True

    async def is_subscribed(self, channel, tg_user_id):
        return bool(await self.subscription_state(channel, tg_user_id))


async def test_check_subscription_ttl_zero_never_caches(monkeypatch):
    retention._sub_cache.clear()
    cfg = dict(settings.retention())
    cfg["subscription_cache_ttl_sec"] = 0
    monkeypatch.setattr(retention.settings, "retention", lambda: cfg)
    client = _FakeClient()
    product = {"id": 991, "telegram_channel_id": "@chan"}
    assert await retention.check_subscription(client, product, 5)
    assert await retention.check_subscription(client, product, 5)
    assert client.calls == 2  # no positive-result caching with TTL 0


async def test_check_subscription_caches_with_ttl(monkeypatch):
    retention._sub_cache.clear()
    cfg = dict(settings.retention())
    cfg["subscription_cache_ttl_sec"] = 600
    monkeypatch.setattr(retention.settings, "retention", lambda: cfg)
    client = _FakeClient()
    product = {"id": 992, "telegram_channel_id": "@chan"}
    assert await retention.check_subscription(client, product, 5)
    assert await retention.check_subscription(client, product, 5)
    assert client.calls == 1
    retention._sub_cache.clear()


# ---------------------------------------------------------------------------
# English-only guard for model-facing content
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ok", [
    "", "Hello, VIP player!", "café señor über 100% (bonus)",
    "Line one\nLine two — with {placeholder} and https://a.io",
])
def test_ensure_english_accepts_latin(ok):
    settings.ensure_english(ok, "field")


@pytest.mark.parametrize("bad", ["Привет", "hello мир", "宝物", "مرحبا", "γειά"])
def test_ensure_english_rejects_non_latin(bad):
    with pytest.raises(ValueError) as exc:
        settings.ensure_english(bad, "my field")
    assert "my field" in str(exc.value)
    assert "English" in str(exc.value)


def test_prompt_variables_reject_non_english():
    with pytest.raises(ValueError):
        settings.validate_prompt_variables({"brand_name": "Бренд"})


def test_retention_prompt_variables_reject_non_english():
    with pytest.raises(ValueError):
        settings.validate_retention_prompt_variables(
            {"retention_brand_name": "Бренд"})


def test_site_map_rejects_non_english_title():
    with pytest.raises(ValueError):
        settings.validate_site_map(
            [{"title": "Касса", "url": "https://x.io/cashier"}])


def test_site_map_accepts_english():
    out = settings.validate_site_map(
        [{"title": "Cashier", "url": "https://x.io/cashier",
          "purpose": "deposits"}])
    assert out[0]["title"] == "Cashier"


# ---------------------------------------------------------------------------
# public pricing accessor (admin token/cost counters)
# ---------------------------------------------------------------------------
def test_pricing_for_model_known():
    p = openai_client.pricing_for_model("gpt-5-mini")
    assert p == {"input_per_1m": 0.25, "cached_input_per_1m": 0.025,
                 "output_per_1m": 2.00}


def test_pricing_for_model_unknown():
    assert openai_client.pricing_for_model("no-such-model") is None
