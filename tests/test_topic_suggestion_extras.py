"""Cross-topic prompts must be the only next-step UI for that turn."""
from __future__ import annotations

import asyncio

import chat_service
import db
import kb
import openai_client


def test_topic_suggestion_suppresses_followup_bubbles(monkeypatch):
    async def _get_topic(tid):
        return {"slug": "bonuses", "id": tid, "title": {"ru": "Бонусы"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return [{"slug": "deposits", "title": "Депозиты"}]

    async def _kb_block(topic_id):
        return "KB про бонусы"

    async def _persist(**kwargs):
        return 1

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
    monkeypatch.setattr(kb, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())

    session = {
        "id": "sess-topic-suggest", "topic_id": 1, "context_reset_id": 0,
        "user_context": {}, "message_count": 0, "lang": "ru",
    }

    reply = asyncio.run(chat_service.handle_message(session, "как пополнить счёт?"))

    assert reply.suggested_topic == {"slug": "deposits", "title": "Депозиты"}
    assert reply.suggestions == []
    assert reply.closing_suggestion is None
    assert reply.resolved is False


def test_normal_turn_splits_closing_suggestion(monkeypatch):
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
    assert reply.closing_suggestion == "Всё понятно, спасибо."
    assert reply.suggested_topic is None
