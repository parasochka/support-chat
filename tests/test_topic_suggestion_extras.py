"""Cross-topic prompts must be the only next-step UI for that turn."""
from __future__ import annotations

import asyncio

import chat_service
import db
import kb
import openai_client


def test_topic_suggestion_is_routing_only_and_suppresses_answer(monkeypatch):
    """A cross-topic turn is routing-ONLY: the ungrounded in-place answer is never
    persisted or returned (the widget auto-switches and re-asks against the right
    KB), no chat turn is written, the message cap is not bumped, but the detect
    call's token cost IS logged so OpenAI spend stays accounted (invariant §4)."""
    calls = {"persist": 0, "ai_log": 0, "events": []}

    async def _get_topic(tid):
        return {"slug": "bonuses", "id": tid, "title": {"ru": "Бонусы"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return [{"slug": "deposits", "title": "Депозиты"}]

    async def _kb_block(topic_id):
        return "KB про бонусы"

    async def _persist(**kwargs):
        calls["persist"] += 1
        return 1

    async def _log_ai(*args, **kwargs):
        calls["ai_log"] += 1

    async def _log_event(session_id, type_, payload=None):
        calls["events"].append((type_, payload or {}))

    class _FakeClient:
        async def complete(self, messages, session_id=None, on_failover=None):
            return openai_client.ChatResult(
                text=(
                    "[[TOPIC:deposits]]\n"
                    "Похоже, ваш вопрос относится к теме «Депозиты».\n"
                    "[[SUGGEST: Как пополнить картой | Как пополнить крипто | Всё ясно, спасибо]]"
                ),
                lang="ru", tokens_in=10, tokens_out=5, cached_in=0,
                model="gpt-test", key_used="primary", latency_ms=1,
            )

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "log_ai_interaction", _log_ai)
    monkeypatch.setattr(db, "log_admin_event", _log_event)
    monkeypatch.setattr(kb, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())

    session = {
        "id": "sess-topic-suggest", "topic_id": 1, "context_reset_id": 0,
        "user_context": {}, "message_count": 3, "lang": "ru",
    }

    reply = asyncio.run(chat_service.handle_message(session, "как пополнить счёт?"))

    assert reply.suggested_topic == {"slug": "deposits", "title": "Депозиты"}
    # The ungrounded in-place answer must NOT leak to the player.
    assert reply.reply == ""
    # No bubbles/finish ride alongside a switch.
    assert reply.suggestions == []
    assert reply.closing_suggestion is None
    assert reply.resolved is False
    # No chat turn persisted, cap not bumped, but the detect call's cost is logged.
    assert calls["persist"] == 0
    assert calls["ai_log"] == 1
    assert reply.message_count == 3
    # The switch is recorded as a traceable marker carrying from/to + this detect
    # call's cost, so the admin session view can interleave it into the timeline.
    assert calls["events"][0][0] == "topic_switch"
    assert calls["events"][0][1]["from"] == "bonuses"
    assert calls["events"][0][1]["to"] == "deposits"
    assert "cost_usd" in calls["events"][0][1]


def test_normal_turn_appends_system_closing_suggestion(monkeypatch):
    async def _get_topic(tid):
        return {"slug": "deposits", "id": tid, "title": {"ru": "Депозиты"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return []

    async def _kb_block(topic_id):
        return "KB про депозиты"

    async def _persist(**kwargs):
        return 1

    async def _noop(*args, **kwargs):
        pass

    class _FakeClient:
        async def complete(self, messages, session_id=None, on_failover=None):
            return openai_client.ChatResult(
                text=(
                    "Пополнить можно картой или криптой.\n"
                    "[[SUGGEST: Какой минимальный депозит? | Есть ли комиссия? | Всё понятно, спасибо.]]"
                ),
                lang="ru", tokens_in=10, tokens_out=5, cached_in=0,
                model="gpt-test", key_used="primary", latency_ms=1,
            )

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "set_conv_lang", _noop)
    monkeypatch.setattr(kb, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())

    session = {
        "id": "sess-closing", "topic_id": 2, "context_reset_id": 0,
        "user_context": {}, "message_count": 0, "lang": "ru",
    }

    reply = asyncio.run(chat_service.handle_message(session, "как пополнить счёт?"))

    assert reply.suggestions == ["Какой минимальный депозит?", "Есть ли комиссия?"]
    # The declarative option the model emitted is dropped; the closing bubble is
    # SYSTEM-supplied with fixed localized wording.
    assert reply.closing_suggestion == "Проблема решена."
    assert reply.suggested_topic is None
