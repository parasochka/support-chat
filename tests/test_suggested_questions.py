"""Suggested follow-up questions + the resolved/close signal.

Along with its answer the model emits:
  - [[SUGGEST: q1 | q2 | q3]] — up to three short guide-to-KB follow-up questions
    (player's POV), with the third option nudging toward chat completion, that the
    widget renders as one-tap bubbles by the input field, and
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
        "Где найти бонус.",
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
    assert sugg == ["a", "b", "c."]  # blanks dropped, capped at 3; closing option is declarative
    assert clean == ""


def test_strip_suggestions_keeps_inline_remainder():
    raw = "Готово. [[SUGGEST: ещё вопрос?]]"
    clean, sugg = prompts.strip_suggestions(raw)
    assert sugg == ["ещё вопрос?"]
    assert clean == "Готово."


def test_strip_suggestions_normalizes_third_closing_option_to_period():
    raw = "[[SUGGEST: Где кнопка пополнения? | Какой минимум? | Всё ясно, закрыть?]]"

    _, sugg = prompts.strip_suggestions(raw)

    assert sugg == ["Где кнопка пополнения?", "Какой минимум?", "Всё ясно, закрыть."]


# ---------------------------------------------------------------------------
# split_closing — the trailing declarative option becomes the finish-chat bubble
# ---------------------------------------------------------------------------
def test_split_closing_separates_declarative_last_option():
    questions, closing = prompts.split_closing(
        ["Какие лимиты на депозит?", "Как пополнить криптой?", "Всё ясно, закрыть."]
    )
    assert questions == ["Какие лимиты на депозит?", "Как пополнить криптой?"]
    assert closing == "Всё ясно, закрыть."


def test_split_closing_none_when_last_is_a_question():
    questions, closing = prompts.split_closing(["Вопрос один?", "Вопрос два?"])
    assert questions == ["Вопрос один?", "Вопрос два?"]
    assert closing is None


def test_split_closing_empty():
    assert prompts.split_closing([]) == ([], None)


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
    assert "Suggested questions:" in core
    assert "[[SUGGEST:" in core
    assert "third option must ALWAYS be a closing/resolution option" in core
    assert "must end with a period, not a question mark" in core
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block="KB", history=[], user_text="hi",
        resolved_lang="en",
    )
    assert "Suggested questions:" in msgs[0]["content"]
    assert "Suggested questions:" not in msgs[-1]["content"]


def test_resolved_directive_in_layer1_core():
    core = prompts.get_system_core()
    assert "Finishing the chat:" in core
    assert "[[RESOLVED]]" in core
    msgs = prompts.build_messages(
        {"user_context": {}}, kb_block=None, history=[], user_text="hi",
        resolved_lang="en",
    )
    assert "Finishing the chat:" in msgs[0]["content"]
    assert "Finishing the chat:" not in msgs[-1]["content"]


def test_lead_forward_directive_ties_suggest_and_resolved():
    """The lead-forward rule (STATIC, Layer-1) guarantees the reply never ends in a
    dead state: either [[SUGGEST]] bubbles or the [[RESOLVED]] finish nudge."""
    core = prompts.get_system_core()
    assert "lead the player forward" in core.lower()
    assert "[[SUGGEST" in core and "[[RESOLVED]]" in core
