"""Topic-routing suggestion: the [[TOPIC:slug]] sentinel + the Layer-3 catalogue.

The model may route a question that belongs to a different topic (whose KB isn't
loaded) by prepending [[TOPIC:slug]]. The tag is stripped from the visible reply,
the slug is captured, and the available-topics list lives in Layer 3 only — the
cached SYSTEM_CORE prefix must stay byte-identical.
"""
from __future__ import annotations

import prompts


_TOPICS = [
    {"slug": "bonuses", "title": "Bonuses & promotions"},
    {"slug": "withdrawals", "title": "Withdrawals"},
]


def test_strip_topic_suggestion_extracts_slug_and_cleans_text():
    raw = "[[TOPIC:bonuses]]\nПохоже, ваш вопрос про бонусы. Хотите сменить тему?"
    clean, slug = prompts.strip_topic_suggestion(raw)
    assert slug == "bonuses"
    assert "[[TOPIC:" not in clean
    assert clean.startswith("Похоже")


def test_strip_topic_suggestion_none_when_absent():
    clean, slug = prompts.strip_topic_suggestion("Обычный ответ без тега.")
    assert slug is None
    assert clean == "Обычный ответ без тега."


def test_strip_topic_suggestion_inline_tag_keeps_remainder():
    raw = "[[TOPIC:withdrawals]] это про выводы"
    clean, slug = prompts.strip_topic_suggestion(raw)
    assert slug == "withdrawals"
    assert clean == "это про выводы"


def test_strip_topic_suggestion_lowercases_slug():
    _, slug = prompts.strip_topic_suggestion("[[TOPIC:Bonuses]] hi")
    assert slug == "bonuses"


def test_available_topics_listed_in_layer3_user_message():
    session = {"user_context": {}}
    msgs = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="как вывести бонус?",
        resolved_lang="en", available_topics=_TOPICS,
    )
    last_user = msgs[-1]["content"]
    assert "bonuses — Bonuses & promotions" in last_user
    assert "withdrawals — Withdrawals" in last_user
    assert "[[TOPIC:slug]]" in last_user  # the routing instruction


def test_current_topic_named_in_layer3():
    """The loaded topic is stated so the model answers in-topic questions from the
    current KB instead of bouncing the player to another branch (the bug where a
    deposit-network question was routed to Withdrawals)."""
    session = {"user_context": {}}
    msgs = prompts.build_messages(
        session, kb_block="KB", history=[],
        user_text="какие сети для пополнения депозита есть?",
        resolved_lang="ru", available_topics=_TOPICS,
        current_topic={"slug": "deposits", "title": "Депозиты"},
    )
    last_user = msgs[-1]["content"]
    assert "deposits — Депозиты" in last_user
    # Conservative instruction: only switch when clearly NOT the current topic.
    assert "ТОЛЬКО" in last_user
    # The current-topic line must stay in Layer 3, never in the cached prefix.
    assert "deposits — Депозиты" not in msgs[0]["content"]


def test_no_current_topic_line_when_absent():
    """Picker's first turn / callers without a topic: no current-topic line, and
    the routing list still renders."""
    session = {"user_context": {}}
    last_user = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="q",
        resolved_lang="en", available_topics=_TOPICS,
    )[-1]["content"]
    assert "Текущая тема" not in last_user
    assert "withdrawals — Withdrawals" in last_user


def test_topic_catalogue_stays_out_of_system_core():
    """The dynamic topic list must never leak into the byte-stable prefix."""
    session = {"user_context": {}}
    core_before = prompts.get_system_core()
    msgs = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="q",
        resolved_lang="en", available_topics=_TOPICS,
    )
    system = msgs[0]["content"]
    assert "bonuses" not in system
    assert "[[TOPIC:" not in system
    # prefix up to the KB separator is unchanged
    assert system.split("=== БАЗА ЗНАНИЙ", 1)[0].rstrip("\n") == core_before


def test_no_topic_section_when_list_empty():
    """Existing callers (and the picker's first turn) pass no topics: no section,
    and the Layer-3 block is identical to omitting the argument entirely."""
    session = {"user_context": {}}
    with_empty = prompts.build_messages(
        session, kb_block=None, history=[], user_text="q",
        resolved_lang="en", available_topics=[],
    )[-1]["content"]
    without = prompts.build_messages(
        session, kb_block=None, history=[], user_text="q", resolved_lang="en",
    )[-1]["content"]
    assert "ДРУГИЕ ТЕМЫ ПОДДЕРЖКИ" not in with_empty
    assert with_empty == without
