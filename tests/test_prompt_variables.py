"""Prompt variables: the prompt is a dry template; the admin-tunable values
(persona/brand/platform/tone) render into it via render_prompt_variables, with
precedence app_settings override > the defaults in prompts.PROMPT_VARIABLES."""
from __future__ import annotations

import pytest

import prompts
import settings


@pytest.fixture(autouse=True)
def _clean():
    settings.invalidate()
    yield
    settings.invalidate()


def test_defaults_render_the_shipped_brand():
    core = prompts.get_system_core()
    assert "You are Nika" in core
    assert "NikaBet" in core
    assert "NowPlix" in core
    # No unresolved registered placeholders survive.
    for key, _desc, _default in prompts.PROMPT_VARIABLES:
        assert "{%s}" % key not in core


def test_override_rebrands_the_whole_prompt():
    settings._cache["prompt_variables"] = {
        "persona_name": "Lola", "brand_name": "LuckyBet",
    }
    core = prompts.get_system_core()
    assert "You are Lola" in core
    assert "LuckyBet" in core
    assert "Nika," not in core.splitlines()[0]
    assert "NikaBet" not in core

    # Layer 3 (guardrails + forbidden-topics refusal) re-brands too.
    msgs = prompts.build_messages({"user_context": {}}, kb_block=None, history=[],
                                  user_text="hi", resolved_lang="en")
    last = msgs[-1]["content"]
    assert "LuckyBet product support" in last
    assert "NikaBet" not in last


def test_empty_override_falls_back_to_default():
    settings._cache["prompt_variables"] = {"brand_name": "   "}
    assert "NikaBet" in prompts.get_system_core()


def test_unknown_placeholders_left_as_is():
    # A brace-token that isn't a registered prompt variable is left untouched
    # (mirrors the KB-variables behaviour: missing keys stay visible).
    assert prompts.render_prompt_variables("see {min_deposit}") == "see {min_deposit}"


def test_player_text_is_never_substituted():
    # A {placeholder} typed by the player must reach the model literally.
    msgs = prompts.build_messages({"user_context": {}}, kb_block=None, history=[],
                                  user_text="what is {brand_name}?",
                                  resolved_lang="en")
    assert "what is {brand_name}?" in msgs[-1]["content"]


def test_core_stays_byte_stable_between_requests_with_overrides():
    settings._cache["prompt_variables"] = {"tone_of_voice": "Be extremely formal."}
    a = prompts.get_system_core()
    b = prompts.get_system_core()
    assert a == b
    assert "Be extremely formal." in a


def test_validate_prompt_variables():
    v = settings.validate_prompt_variables(
        {"brand_name": "LuckyBet", "persona_name": "  Lola  ", "products": ""})
    assert v == {"brand_name": "LuckyBet", "persona_name": "Lola"}  # empties dropped
    with pytest.raises(ValueError):
        settings.validate_prompt_variables({"nope": "x"})       # unknown key
    with pytest.raises(ValueError):
        settings.validate_prompt_variables({"brand_name": 1})   # not a string
    with pytest.raises(ValueError):
        settings.validate_prompt_variables("not-a-dict")
