"""Suggested follow-up questions + the resolved/close signal.

Along with its answer the model emits:
  - [[SUGGEST: q1 | q2 | q3]] — up to three short guide-to-KB follow-up questions
    (player's POV) the widget renders as one-tap bubbles by the input field, and
  - [[RESOLVED]] — once the question looks fully resolved, so the widget can offer
    a "finish chat" button.
Both tags are stripped from the visible reply and their directives live in Layer
3 only — the cached SYSTEM_CORE prefix must stay byte-identical.
"""
from __future__ import annotations

import prompts


# ---------------------------------------------------------------------------
# strip_suggestions
# ---------------------------------------------------------------------------
def test_strip_suggestions_parses_pipe_list_and_cleans_text():
    raw = (
        "Вот как пополнить счёт картой.\n"
        "[[SUGGEST: Какие лимиты на депозит? | Как пополнить криптой? | Где найти бонус?]]"
    )
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == [
        "Какие лимиты на депозит?",
        "Как пополнить криптой?",
        "Где найти бонус?",
    ]
    assert "[[SUGGEST" not in clean
    assert clean == "Вот как пополнить счёт картой."


def test_strip_suggestions_none_when_absent():
    clean, sugg = prompts.strip_suggestions("Обычный ответ без тега.")
    assert sugg == []
    assert clean == "Обычный ответ без тега."


def test_strip_suggestions_caps_at_three_and_drops_blanks():
    raw = "[[SUGGEST: a | b |  | c | d ]]"
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["a", "b", "c"]  # blanks dropped, capped at 3
    assert clean == ""


def test_strip_suggestions_keeps_inline_remainder():
    raw = "Готово. [[SUGGEST: ещё вопрос?]]"
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["ещё вопрос?"]
    assert clean == "Готово."


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
# Directives live in Layer 3 only (cached prefix untouched)
# ---------------------------------------------------------------------------
def test_suggestions_directive_in_layer3_only():
    core_before = prompts.get_system_core()
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block="KB", history=[], user_text="hi",
        resolved_lang="en",
    )
    last = msgs[-1]["content"]
    assert "Наводящие вопросы:" in last
    assert "[[SUGGEST:" in last
    # Stays in Layer 3 only; the cached core is untouched.
    assert "Наводящие вопросы:" not in msgs[0]["content"]
    assert msgs[0]["content"].split("=== БАЗА ЗНАНИЙ", 1)[0].rstrip("\n") == core_before


def test_resolved_directive_in_layer3_only():
    core_before = prompts.get_system_core()
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block=None, history=[], user_text="hi",
        resolved_lang="en",
    )
    last = msgs[-1]["content"]
    assert "Завершение чата:" in last
    assert "[[RESOLVED]]" in last
    assert "Завершение чата:" not in msgs[0]["content"]
    assert msgs[0]["content"] == core_before
