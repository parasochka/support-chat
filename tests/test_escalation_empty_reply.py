"""Escalation turns must not produce blank assistant replies."""
from __future__ import annotations

import asyncio

import chat_service
import db
import kb
import openai_client


def test_control_only_escalation_gets_visible_handoff_copy(monkeypatch):
    captured: dict = {}

    async def _get_topic(tid):
        return {"slug": "other", "id": tid, "title": {"ru": "Другое"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return []

    async def _kb_block(topic_id):
        return "KB"

    async def _persist(**kwargs):
        captured["assistant_text"] = kwargs["assistant_text"]
        return 1

    async def _noop(*args, **kwargs):
        pass

    class _FakeClient:
        async def complete(self, messages, session_id=None, on_failover=None):
            return openai_client.ChatResult(
                text="[[ESCALATE]]\n[[LANG:ru]]", lang="ru",
                tokens_in=10, tokens_out=2, cached_in=0,
                model="gpt-test", key_used="primary", latency_ms=1,
            )

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "mark_escalated", _noop)
    monkeypatch.setattr(db, "log_admin_event", _noop)
    monkeypatch.setattr(db, "set_conv_lang", _noop)
    monkeypatch.setattr(kb, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())

    session = {
        "id": "sess-blank-escalation", "topic_id": 1, "context_reset_id": 0,
        "user_context": {}, "message_count": 0, "lang": "ru",
    }

    # NB: deliberately keyword-free text — a support/human keyword would now
    # SOFT-escalate BEFORE the model call, while this test exercises the
    # model-signalled ([[ESCALATE]]) hard path.
    reply = asyncio.run(chat_service.handle_message(session, "мой вопрос никак не решается"))

    assert reply.escalation["active"] is True
    assert reply.escalation["final"] is True  # a model hand-off closes the chat
    assert reply.reply == "Я передам ваш вопрос в службу поддержки. Они помогут дальше."
    assert captured["assistant_text"] == reply.reply
