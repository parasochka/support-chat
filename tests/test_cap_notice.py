"""Soft message cap -> the long-chat cap notice.

The cap (`general.max_messages_per_session`) no longer force-closes the chat.
The turn that reaches it (and every turn after) is still answered by the model,
but the reply swaps the suggestion bubbles for `cap_notice` — a stable localized
explanation plus the two ways forward the widget renders as buttons: escalate to
a human (POST /escalate, the normal flow) or finish the chat (POST /resolve).
Only the HARD ceiling (cap x escalation.HARD_CAP_MULTIPLIER) force-escalates.
"""
from __future__ import annotations

import asyncio
import types

from app.chat import chat_service
from app.chat import escalation
from app.core import db
from app.core import settings
from app.ai import kb as kb_mod
from app.ai import openai_client


def _session(**over):
    base = {"id": "sess-cap", "topic_id": 1, "context_reset_id": 0,
            "user_context": {}, "message_count": 0, "lang": "en",
            "conv_lang": None, "product_id": None,
            "status": "open", "escalated": False}
    base.update(over)
    return base


def _result(text: str):
    return types.SimpleNamespace(
        text=text, model="gpt-test", key_used="primary",
        tokens_in=10, tokens_out=10, cached_in=0, latency_ms=5,
    )


def _wire(monkeypatch, model_text: str):
    """Mock the DB/KB/model surface for a full handle_message run."""
    persisted: dict = {}

    async def _get_topic(tid):
        return {"slug": "deposits", "id": tid, "title": {"en": "Deposits"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return []

    async def _kb_block(topic_id):
        return "KB"

    async def _persist(**kwargs):
        persisted.update(kwargs)
        return kwargs.get("_count", 99)

    async def _noop(*a, **k):
        return None

    class _FakeClient:
        async def complete(self, *a, **k):
            return _result(model_text)

    async def _client_for_product(pid):
        return _FakeClient()

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "log_admin_event", _noop)
    monkeypatch.setattr(db, "log_ai_interaction", _noop)
    monkeypatch.setattr(db, "mark_escalated", _noop)
    monkeypatch.setattr(db, "set_conv_lang", _noop)
    monkeypatch.setattr(kb_mod, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb_mod, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "client_for_product", _client_for_product)
    return persisted


def test_decide_soft_cap_does_not_close_but_hard_ceiling_does(monkeypatch):
    monkeypatch.setitem(settings._cache, "general",
                        {"max_messages_per_session": 5})
    # At/past the soft cap the session stays open...
    assert escalation.decide(model_signalled=False, message_count=5).active is False
    assert escalation.decide(model_signalled=False, message_count=9).active is False
    # ...and the hard ceiling (5 x 2) still force-escalates.
    d = escalation.decide(model_signalled=False, message_count=10)
    assert d.active and d.reason == "message_cap"


def test_cap_notice_payload_is_localized():
    p = escalation.cap_notice_payload("ru")
    assert p["escalate_label"] == "Передать вопрос человеку"
    assert p["finish_label"] == "Завершить чат"
    assert p["message"]


def test_turn_reaching_cap_gets_answer_plus_notice_and_no_bubbles(monkeypatch):
    monkeypatch.setitem(settings._cache, "general",
                        {"max_messages_per_session": 5})
    _wire(monkeypatch,
          "Here is the answer.\n[[SUGGEST: What about limits? | And fees?]]")
    # prospective count = 4 + 1 = 5 -> reaches the soft cap.
    reply = asyncio.run(
        chat_service.handle_message(_session(message_count=4), "still not working"))
    assert reply.escalation["active"] is False       # the chat stays open
    assert reply.reply == "Here is the answer."      # ...and still answered
    assert reply.cap_notice is not None
    assert set(reply.cap_notice) == {"message", "escalate_label", "finish_label"}
    # The notice replaces the bubbles / finish nudge entirely.
    assert reply.suggestions == []
    assert reply.closing_suggestion is None
    assert reply.resolved is False


def test_turn_past_cap_keeps_carrying_the_notice(monkeypatch):
    monkeypatch.setitem(settings._cache, "general",
                        {"max_messages_per_session": 5})
    _wire(monkeypatch, "Another answer.")
    # The player chose to keep chatting: every further turn repeats the notice.
    reply = asyncio.run(
        chat_service.handle_message(_session(message_count=7), "one more thing"))
    assert reply.escalation["active"] is False
    assert reply.cap_notice is not None


def test_under_cap_has_no_notice(monkeypatch):
    monkeypatch.setitem(settings._cache, "general",
                        {"max_messages_per_session": 5})
    _wire(monkeypatch, "Normal answer.\n[[SUGGEST: What about limits?]]")
    reply = asyncio.run(
        chat_service.handle_message(_session(message_count=1), "a question"))
    assert reply.cap_notice is None
    assert reply.suggestions == ["What about limits?"]


def test_model_escalation_at_cap_wins_over_the_notice(monkeypatch):
    monkeypatch.setitem(settings._cache, "general",
                        {"max_messages_per_session": 5})
    _wire(monkeypatch, "[[ESCALATE]] I cannot resolve this here.")
    reply = asyncio.run(
        chat_service.handle_message(_session(message_count=6), "help"))
    # A hand-off is already the ending — no notice on top of the contact card.
    assert reply.escalation["active"] is True
    assert reply.cap_notice is None
