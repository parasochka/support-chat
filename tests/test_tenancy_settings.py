"""Multi-tenancy settings resolution: product_settings > app_settings > env >
default, driven by the request's tenancy scope (contextvar). No scope set ==
the pre-tenancy behaviour (global layer only)."""
from __future__ import annotations

import pytest

import settings
import tenancy


@pytest.fixture(autouse=True)
def _clean_scope(monkeypatch):
    # Each test starts unscoped and with empty caches.
    tenancy.set_current_product(None)
    monkeypatch.setattr(settings, "_cache", {})
    monkeypatch.setattr(settings, "_product_cache", {})
    yield
    tenancy.set_current_product(None)


def test_product_overrides_global_field_level(monkeypatch):
    monkeypatch.setattr(settings, "_cache",
                        {"antispam": {"cooldown_sec": 5, "max_input_chars": 700}})
    monkeypatch.setattr(settings, "_product_cache",
                        {7: {"antispam": {"cooldown_sec": 9}}})

    # Unscoped: the global layer only.
    assert settings.antispam()["cooldown_sec"] == 5
    assert settings.antispam()["max_input_chars"] == 700

    # Scoped to product 7: its stored field wins, everything else inherits.
    tenancy.set_current_product(7)
    assert settings.antispam()["cooldown_sec"] == 9
    assert settings.antispam()["max_input_chars"] == 700

    # A different product has no overrides -> global values.
    tenancy.set_current_product(8)
    assert settings.antispam()["cooldown_sec"] == 5


def test_env_default_when_no_layers(monkeypatch):
    import config
    monkeypatch.setattr(config, "MESSAGE_COOLDOWN_SEC", 2)
    tenancy.set_current_product(3)
    assert settings.antispam()["cooldown_sec"] == 2


def test_prompt_variables_resolve_per_product(monkeypatch):
    monkeypatch.setattr(settings, "_cache",
                        {"prompt_variables": {"brand_name": "GlobalBet"}})
    monkeypatch.setattr(settings, "_product_cache",
                        {5: {"prompt_variables": {"brand_name": "LuckyFive"}}})
    assert settings.prompt_variables()["brand_name"] == "GlobalBet"
    tenancy.set_current_product(5)
    resolved = settings.prompt_variables()
    assert resolved["brand_name"] == "LuckyFive"
    # Unset keys still fall back to the file defaults.
    assert resolved["persona_name"] == "Nika"


def test_translations_merge_per_language(monkeypatch):
    # The product overrides ONE key of one language; the global override of a
    # sibling key in the same language must survive the merge.
    monkeypatch.setattr(settings, "_cache", {"translations": {
        "ru": {"support": "Глобальная поддержка", "send": "Отправить!"}}})
    monkeypatch.setattr(settings, "_product_cache", {2: {"translations": {
        "ru": {"support": "Поддержка казино"},
        "en": {"support": "Casino support"}}}})
    tenancy.set_current_product(2)
    merged = settings.translations()
    assert merged["ru"]["support"] == "Поддержка казино"   # product wins
    assert merged["ru"]["send"] == "Отправить!"            # global survives
    assert merged["en"]["support"] == "Casino support"     # product-only lang


def test_prompt_core_varies_per_product_but_stable_within(monkeypatch):
    """Layer 1 renders per product (each casino gets its brand) and stays
    byte-stable between requests WITHIN one product scope."""
    import prompts
    monkeypatch.setattr(settings, "_cache", {})
    monkeypatch.setattr(settings, "_product_cache",
                        {1: {"prompt_variables": {"brand_name": "AlphaBet"}},
                         2: {"prompt_variables": {"brand_name": "BravoBet"}}})
    tenancy.set_current_product(1)
    core_a1 = prompts.get_system_core()
    core_a2 = prompts.get_system_core()
    assert core_a1 == core_a2
    assert "AlphaBet" in core_a1
    tenancy.set_current_product(2)
    core_b = prompts.get_system_core()
    assert "BravoBet" in core_b and "AlphaBet" not in core_b
