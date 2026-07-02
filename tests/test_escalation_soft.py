"""Soft (keyword) escalation: word-boundary matching, pre-model trigger, and the
transient model-failure path that must NOT escalate.

The hard/soft split: keyword triggers (fraud/legal stems, explicit ask for a
human) fire BEFORE the model call, show the contact card (final=False) and keep
the session OPEN — a fuzzy stem false positive must never kill a live chat and
never burns model tokens. Hard triggers (model [[ESCALATE]], message cap,
explicit tap) still close the session.
"""
from __future__ import annotations

import asyncio

import chat_service
import db
import escalation
import openai_client


# ---------------------------------------------------------------------------
# Word-boundary keyword matching
# ---------------------------------------------------------------------------
def test_stems_match_at_word_start_only():
    # "поддержк" is a long stem: word-prefix match.
    assert escalation.user_requests_human("позовите поддержку") is True
    # ...but not buried mid-word.
    assert escalation.user_requests_human("суперподдержканутый бонус",
                                          keywords=("поддержк",)) is False


def test_short_stems_require_whole_word():
    # "суд" (3 chars) fires only as a whole word: real legal threats still hit...
    assert escalation.is_high_risk("я подам в суд") is True
    # ...but everyday words containing it do not (the old substring matcher
    # escalated and closed the chat on any of these).
    assert escalation.is_high_risk("судя по всему, бонус не начислился") is False
    assert escalation.is_high_risk("какая судьба у моего вывода") is False
    assert escalation.is_high_risk("рассудите нас") is False


def test_phrases_still_match_as_substrings():
    assert escalation.is_high_risk("это какой-то обман и мошенничество") is True
    assert escalation.user_requests_human("i want a human agent") is True


def test_obfuscated_keywords_still_caught():
    # The normalization pass (zero-width strip / de-spacing) still applies.
    assert escalation.user_requests_human("о п е р а т о р") is True


# ---------------------------------------------------------------------------
# decide() — post-model HARD triggers only
# ---------------------------------------------------------------------------
def test_decide_cap_and_model_signal():
    d = escalation.decide(model_signalled=True, message_count=1)
    assert d.active and d.reason == "model_signalled"
    d = escalation.decide(model_signalled=False, message_count=10_001)
    assert d.active and d.reason == "message_cap"
    d = escalation.decide(model_signalled=False, message_count=1)
    assert not d.active


def test_build_payload_final_flag():
    assert escalation.build_payload("en")["final"] is True
    assert escalation.build_payload("en", final=False)["final"] is False


# ---------------------------------------------------------------------------
# chat_service: keyword turn short-circuits BEFORE the model call
# ---------------------------------------------------------------------------
def _session(**over):
    base = {"id": "sess-soft", "topic_id": 1, "context_reset_id": 0,
            "user_context": {}, "message_count": 0, "lang": "ru",
            "status": "open", "escalated": False}
    base.update(over)
    return base


def test_keyword_turn_is_soft_and_skips_the_model(monkeypatch):
    called = {"model": 0, "soft": 0, "hard": 0}
    persisted = {}

    async def _persist(**kwargs):
        persisted.update(kwargs)
        return 1

    async def _soft(sid):
        called["soft"] += 1

    async def _hard(sid):
        called["hard"] += 1

    async def _noop(*a, **k):
        pass

    class _FakeClient:
        async def complete(self, *a, **k):
            called["model"] += 1
            raise AssertionError("model must not be called on a keyword turn")

    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "mark_escalated_soft", _soft)
    monkeypatch.setattr(db, "mark_escalated", _hard)
    monkeypatch.setattr(db, "log_admin_event", _noop)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())

    reply = asyncio.run(
        chat_service.handle_message(_session(), "позовите оператора, пожалуйста"))

    assert called["model"] == 0          # no tokens burned
    assert called["soft"] == 1           # flagged for metrics/queue...
    assert called["hard"] == 0           # ...but the session is NOT closed
    assert reply.escalation["active"] is True
    assert reply.escalation["final"] is False
    assert reply.reply == ""             # copy rides in the card, not a bubble
    assert persisted["assistant_text"]   # ...but the transcript has it
    assert persisted["ai_meta"] is None  # no OpenAI call -> no AI log row


def test_model_failure_returns_nudge_and_does_not_escalate(monkeypatch):
    """A transient OpenAI failure must not escalate/close the session — the
    player gets a localized 'try again' nudge and can simply resend."""
    events = []

    async def _get_topic(tid):
        return {"slug": "deposits", "id": tid, "title": {"ru": "Депозиты"}}

    async def _get_history(session_id, limit=20, after_id=0):
        return []

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return []

    async def _kb_block(topic_id):
        return "KB"

    async def _persist(**kwargs):
        raise AssertionError("a failed turn must not be persisted")

    async def _log_ai(*a, **k):
        events.append("ai_log")

    async def _log_event(sid, type_, payload=None):
        events.append(type_)

    async def _mark(*a, **k):
        raise AssertionError("a transient failure must not escalate")

    class _FailingClient:
        async def complete(self, *a, **k):
            raise TimeoutError("provider down")

    import kb as kb_mod
    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "log_ai_interaction", _log_ai)
    monkeypatch.setattr(db, "log_admin_event_sampled", _log_event)
    monkeypatch.setattr(db, "mark_escalated", _mark)
    monkeypatch.setattr(db, "mark_escalated_soft", _mark)
    monkeypatch.setattr(kb_mod, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb_mod, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FailingClient())

    reply = asyncio.run(
        chat_service.handle_message(_session(), "не приходит депозит на счёт"))

    assert reply.escalation["active"] is False
    assert reply.reply == chat_service._model_error_reply("ru")
    assert "ai_log" in events        # the failed call is still accounted
    assert "model_error" in events   # ...and surfaced as an admin event
