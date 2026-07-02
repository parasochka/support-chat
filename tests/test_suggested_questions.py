"""Suggested follow-up questions + the resolved/close signal.

Along with its answer the model emits:
  - [[SUGGEST: q1 | q2]] — up to TWO short guide-to-KB follow-up questions
    (player's POV) that the widget renders as one-tap bubbles by the input
    field. The closing "issue solved" bubble is NOT generated: chat_service
    appends a fixed localized option itself, so a declarative item the model
    still emits out of old habit is dropped by the parser.
  - [[RESOLVED]] — once the question looks fully resolved, so the widget can
    offer a "finish chat" button.
Both tags are stripped from the visible reply; their directives are STATIC and
ride in the byte-stable Layer-1 core.
"""
from __future__ import annotations

import chat_service
import prompts


# ---------------------------------------------------------------------------
# strip_suggestions
# ---------------------------------------------------------------------------
def test_strip_suggestions_parses_pipe_list_and_cleans_text():
    raw = (
        "Вот как пополнить счёт картой.\n"
        "[[SUGGEST: Какие лимиты на депозит? | Как пополнить криптой?]]"
    )
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == [
        "Какие лимиты на депозит?",
        "Как пополнить криптой?",
    ]
    assert "[[SUGGEST" not in clean
    assert clean == "Вот как пополнить счёт картой."


def test_strip_suggestions_none_when_absent():
    clean, sugg = prompts.strip_suggestions("Обычный ответ без тега.")
    assert sugg == []
    assert clean == "Обычный ответ без тега."


def test_strip_suggestions_caps_at_two_and_drops_blanks():
    raw = "[[SUGGEST: а? | б? |  | в? | г? ]]"
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["а?", "б?"]  # blanks dropped, capped at 2
    assert clean == ""


def test_strip_suggestions_keeps_inline_remainder():
    raw = "Готово. [[SUGGEST: ещё вопрос?]]"
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["ещё вопрос?"]
    assert clean == "Готово."


def test_strip_suggestions_drops_declarative_closing_option():
    # The closing option is system-supplied now; a declarative item the model
    # still emits out of old habit must not masquerade as a guiding bubble.
    raw = "[[SUGGEST: Какой минимум? | Всё понятно, спасибо.]]"
    _, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["Какой минимум?"]


def test_closing_suggestion_is_localized_with_english_fallback():
    assert chat_service.closing_suggestion_for("ru") == "Проблема решена."
    assert chat_service.closing_suggestion_for("en") == "Issue solved."
    # Unknown / admin-added languages fall back to English.
    assert chat_service.closing_suggestion_for("de") == "Issue solved."


# ---------------------------------------------------------------------------
# strip_resolved_tag
# ---------------------------------------------------------------------------
def test_strip_resolved_tag_detects_and_cleans():
    raw = "Рад был помочь!\n[[RESOLVED]]"
    clean, resolved = prompts.strip_resolved_tag(raw)
    assert resolved is True
    assert "[[RESOLVED]]" not in clean
    assert clean == "Рад был помочь!"


def test_strip_resolved_tag_absent():
    clean, resolved = prompts.strip_resolved_tag("Ещё чем-то помочь?")
    assert resolved is False
    assert clean == "Ещё чем-то помочь?"


def test_strip_resolved_tag_inline_keeps_remainder():
    clean, resolved = prompts.strip_resolved_tag("[[RESOLVED]] Спасибо за обращение!")
    assert resolved is True
    assert clean == "Спасибо за обращение!"


# ---------------------------------------------------------------------------
# Directives are STATIC -> they ride in the byte-stable Layer-1 block
# ---------------------------------------------------------------------------
def test_suggestions_directive_in_layer1_core():
    core = prompts.get_system_core()
    assert "SUGGESTED QUESTIONS:" in core
    assert "[[SUGGEST:" in core
    # The model must NOT generate the closing option — the system supplies it.
    assert "the system appends its own" in core
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block="KB", history=[], user_text="hi",
        resolved_lang="en",
    )
    assert "SUGGESTED QUESTIONS:" in msgs[0]["content"]
    assert "SUGGESTED QUESTIONS:" not in msgs[-1]["content"]


def test_resolved_directive_in_layer1_core():
    core = prompts.get_system_core()
    assert "FINISHING THE CHAT:" in core
    assert "[[RESOLVED]]" in core
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block=None, history=[], user_text="hi",
        resolved_lang="en",
    )
    assert "FINISHING THE CHAT:" in msgs[0]["content"]
    assert "FINISHING THE CHAT:" not in msgs[-1]["content"]


def test_lead_forward_directive_ties_suggest_and_resolved():
    """The lead-forward rule (STATIC, Layer-1) guarantees the reply never ends in a
    dead state: either [[SUGGEST]] bubbles or the [[RESOLVED]] finish nudge."""
    core = prompts.get_system_core()
    assert "lead the player forward" in core.lower()
    assert "[[SUGGEST" in core and "[[RESOLVED]]" in core
