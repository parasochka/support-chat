"""Retention prompt variables: the Telegram persona is tuned INDEPENDENTLY
from the support chat, but by default INHERITS its values — an empty override
resolves to the corresponding support variable (retention_persona_name →
persona_name, …); only the tone carries its own bolder default. Stored under
`retention_prompt_variables` with its own admin editor (Retention → Prompt
variables tab)."""
from __future__ import annotations

import pytest

import prompts
import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    yield
    settings.invalidate()


def test_registry_split():
    # The support registry carries no retention keys and vice versa.
    support = {k for k, _d, _v in prompts.PROMPT_VARIABLES}
    retention = {k for k, _d, _v, _i in prompts.RETENTION_PROMPT_VARIABLES}
    assert not support & retention
    assert "retention_tone_of_voice" in retention
    # Every inherit-source is a registered support key.
    for _k, _d, default, inherits in prompts.RETENTION_PROMPT_VARIABLES:
        if default is None:
            assert inherits in support


def test_defaults_inherit_the_support_values():
    resolved = settings.retention_prompt_variables()
    support = settings.prompt_variables()
    assert resolved["retention_persona_name"] == support["persona_name"]
    assert resolved["retention_brand_name"] == support["brand_name"]
    assert resolved["retention_products"] == support["products"]
    # The tone does NOT inherit: it ships its own bolder retention default.
    assert resolved["retention_tone_of_voice"] != support["tone_of_voice"]
    assert "bolder" in resolved["retention_tone_of_voice"]


def test_support_override_flows_into_retention_by_default():
    settings._cache["prompt_variables"] = {"persona_name": "Lola",
                                           "brand_name": "LuckyBet"}
    core = prompts.get_retention_system_core()
    assert "You are Lola" in core
    assert "LuckyBet" in core
    assert "NikaBet" not in core


def test_retention_override_wins_over_support():
    settings._cache["prompt_variables"] = {"persona_name": "Lola"}
    settings._cache["retention_prompt_variables"] = {
        "retention_persona_name": "Candy",
        "retention_tone_of_voice": "Be very bold.",
    }
    core = prompts.get_retention_system_core()
    assert "You are Candy" in core
    assert "Be very bold." in core
    # The support core is untouched by retention overrides.
    support_core = prompts.get_system_core()
    assert "You are Lola" in support_core
    assert "Candy" not in support_core


def test_retention_guardrails_render_retention_brand():
    settings._cache["retention_prompt_variables"] = {
        "retention_brand_name": "TeleBet",
    }
    msg = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hi")
    assert "TeleBet" in msg
    assert "NikaBet" not in msg


def test_empty_override_falls_back_to_inheritance():
    settings._cache["retention_prompt_variables"] = {
        "retention_persona_name": "   "}
    assert settings.retention_prompt_variables()["retention_persona_name"] == "Nika"


def test_retention_core_stays_byte_stable():
    settings._cache["retention_prompt_variables"] = {
        "retention_tone_of_voice": "Custom tone."}
    a = prompts.get_retention_system_core()
    b = prompts.get_retention_system_core()
    assert a == b
    assert "Custom tone." in a


def test_validate_retention_prompt_variables():
    v = settings.validate_retention_prompt_variables(
        {"retention_persona_name": "  Candy ", "retention_products": ""})
    assert v == {"retention_persona_name": "Candy"}  # empties dropped
    with pytest.raises(ValueError):
        settings.validate_retention_prompt_variables({"persona_name": "x"})
    with pytest.raises(ValueError):
        settings.validate_retention_prompt_variables(
            {"retention_persona_name": 1})
    with pytest.raises(ValueError):
        settings.validate_retention_prompt_variables("not-a-dict")


def test_support_validator_redirects_retention_keys():
    # A retention key written to the SUPPORT endpoint gets a pointed error
    # instead of a generic "unknown variable".
    with pytest.raises(ValueError, match="retention"):
        settings.validate_prompt_variables({"retention_tone_of_voice": "x"})
