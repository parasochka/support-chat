"""Retention prompt variables: the Telegram persona is a SEPARATE prompt, tuned
FULLY INDEPENDENTLY from the support chat. Every key ships its own retention
default (no inheritance); an empty override falls back to that default, never to
a support value, and a support edit never leaks into the bot. The 4th registry
field (`renders_as`) is a template RENDER target - which base placeholder the
variable fills ({persona_name}, …) - not a value link. Stored under
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
    # Every key ships a concrete retention default (no None = no inheritance).
    for _k, _d, default, renders_as in prompts.RETENTION_PROMPT_VARIABLES:
        assert default, f"{_k}: retention default must be non-empty"
        # renders_as, when set, is a base placeholder the template fills.
        if renders_as:
            assert renders_as in support


def test_defaults_are_independent_of_support():
    resolved = settings.retention_prompt_variables()
    # The retention persona ships its OWN defaults; the role must NOT read as a
    # support agent (the "customer-support assistant" leak that motivated the split).
    assert "support" not in resolved["retention_persona_role"].lower()
    # The retention tone is its own, bolder voice — it must explicitly reject
    # the support-agent register rather than inherit it.
    assert "Never sound like customer support" in resolved["retention_tone_of_voice"]
    # Registry defaults resolve verbatim when nothing is overridden.
    reg = {k: d for k, _desc, d, _r in prompts.RETENTION_PROMPT_VARIABLES}
    for key, default in reg.items():
        assert resolved[key] == default


def test_support_override_does_not_leak_into_retention():
    # A support prompt-variable edit must NOT change the Telegram persona.
    settings._cache["prompt_variables"] = {"persona_name": "Lola",
                                           "brand_name": "LuckyBet"}
    core = prompts.get_retention_system_core()
    assert "You are Nika" in core          # the retention default, unchanged
    assert "Lola" not in core
    assert "LuckyBet" not in core


def test_retention_forbidden_topics_no_support_leak():
    # The forbidden-topics directive is shared, but rendered for retention it must
    # NOT carry support-voice wording or the support-only {support_scope}
    # placeholder (which the retention variable set cannot resolve — it would
    # otherwise reach the model as a literal `{support_scope}`).
    ret = prompts._forbidden_topics_directive(
        renderer=prompts.render_retention_prompt_variables,
        refusal=prompts.FORBIDDEN_TOPICS_REFUSAL_RETENTION,
        decline_clause=("politely decline and warmly steer the player back to "
                        "your chat with {persona_name}"))
    assert "{support_scope}" not in ret          # no unrendered placeholder
    assert "support assistant" not in ret.lower()  # not the support persona
    assert "support-related question" not in ret.lower()
    assert "Nika" in ret                          # retention persona resolved

    # And the full retention Layer-3 message (as sent) is clean too.
    body = prompts.build_retention_dynamic_prompt(
        user_context={"full_name": "Andrey"}, resolved_lang="en",
        user_text="write me some python code")
    assert "{support_scope}" not in body
    assert "support assistant" not in body.lower()


def test_support_forbidden_topics_unchanged():
    # The support default output must be byte-identical to the pre-refactor text
    # (the parameterization keeps support untouched).
    sup = prompts._forbidden_topics_directive()
    assert "the NikaBet support assistant" in sup
    assert "deposits and withdrawals" in sup     # {support_scope} rendered
    assert "offer to ask a NikaBet support-related question" in sup


def test_retention_override_wins_over_default():
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


def test_empty_override_falls_back_to_retention_default():
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
