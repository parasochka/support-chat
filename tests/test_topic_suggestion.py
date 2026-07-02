"""Topic-routing suggestion: the [[TOPIC:slug]] sentinel + the Layer-3 catalogue.

The model may route a question that belongs to a different topic (whose KB isn't
loaded) by prepending [[TOPIC:slug]]. The tag is stripped from the visible reply,
the slug is captured, and the available-topics list lives in Layer 3 only - the
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
    assert "bonuses - Bonuses & promotions" in last_user
    assert "withdrawals - Withdrawals" in last_user
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
    assert "deposits - Депозиты" in last_user
    # Conservative instruction: only switch when clearly NOT the current topic.
    assert "ONLY" in last_user
    # The current-topic line must stay in Layer 3, never in the cached prefix.
    assert "deposits - Депозиты" not in msgs[0]["content"]


def test_other_topic_uses_the_same_regime_as_specialized():
    """«Другое» (slug 'other') is a normal player-selectable topic with its own KB,
    so it is routed EXACTLY like the specialized topics: answer from the loaded KB,
    switch only on a genuine mismatch - no separate active-routing mode (the bug
    where a question answerable from the general KB, e.g. changing the language, was
    force-routed to a topic that didn't have the answer)."""
    session = {"user_context": {}}
    last_user = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="как сменить язык?",
        resolved_lang="ru", available_topics=_TOPICS,
        current_topic={"slug": "other", "title": "Другое"},
    )[-1]["content"]
    # The current topic is named with the standard "knowledge base is loaded" anchor.
    assert "other - Другое" in last_user
    assert "knowledge base is loaded for you" in last_user
    # Standard conservative regime: answer from current KB, switch only on mismatch.
    assert "ONLY" in last_user
    assert "INTENT" in last_user
    # The old active-routing framing is gone.
    assert "general section" not in last_user
    # The catalogue is still offered so a genuine mismatch can route onward.
    assert "[[TOPIC:slug]]" in last_user
    assert "bonuses - Bonuses & promotions" in last_user
    # Routing copy must stay in Layer 3, never in the cached prefix.
    msgs = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="как сменить язык?",
        resolved_lang="ru", available_topics=_TOPICS,
        current_topic={"slug": "other", "title": "Другое"},
    )
    assert "other - Другое" not in msgs[0]["content"]


def test_specialized_topic_routes_on_intent_not_keywords():
    """A specialized topic anchors the model but keys the switch on the player's
    intent, so e.g. a withdrawal question asked under Deposits is routed across —
    the cross-topic tracking the owner asked to tighten."""
    session = {"user_context": {}}
    last_user = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="как вывести деньги?",
        resolved_lang="ru", available_topics=_TOPICS,
        current_topic={"slug": "deposits", "title": "Депозиты"},
    )[-1]["content"]
    # Conservative anchor retained (only switch on a genuine mismatch)...
    assert "ONLY" in last_user
    # ...but now framed around the player's intent + a concrete cross-topic example.
    assert "INTENT" in last_user
    assert "WITHDRAW" in last_user


def test_no_current_topic_line_when_absent():
    """Picker's first turn / callers without a topic: no current-topic line, and
    the routing list still renders."""
    session = {"user_context": {}}
    last_user = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="q",
        resolved_lang="en", available_topics=_TOPICS,
    )[-1]["content"]
    assert "Current topic" not in last_user
    assert "withdrawals - Withdrawals" in last_user


def test_topic_catalogue_stays_out_of_system_core():
    """The dynamic topic list must never leak into the byte-stable prefix."""
    session = {"user_context": {}}
    core_before = prompts.get_system_core()
    msgs = prompts.build_messages(
        session, kb_block="KB", history=[], user_text="q",
        resolved_lang="en", available_topics=_TOPICS,
    )
    system = msgs[0]["content"]
    # The dynamic catalogue line + the routing block must not leak into the prefix.
    # (The byte-stable MACHINE TAGS block in SYSTEM_CORE names [[TOPIC:slug]] once as
    # a static placement rule, so we check the dynamic routing instruction - which
    # carries the per-request offer - rather than the bare tag literal.)
    assert "bonuses - Bonuses & promotions" not in system
    assert "offer to switch" not in system
    assert "TOPIC ROUTING" not in system
    # prefix up to the KB separator is unchanged
    assert system.split("=== KNOWLEDGE BASE", 1)[0].rstrip("\n") == core_before


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
    assert "=== TOPIC ROUTING ===" not in with_empty
    assert with_empty == without
