"""Escalation Phase 2: ticket row created; Telegram failure still returns the
button; delivered flag tracks the Bot API result (stubbed)."""
from __future__ import annotations

import pytest

import config
import db
import escalation
from notifiers import telegram


@pytest.fixture
def _wire(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "TELEGRAM_AGENT_CHAT_ID", "-100123")
    calls = {"tickets": [], "delivered": [], "events": []}

    async def _get_topic(tid):
        return {"slug": "deposits"}

    async def _get_history(sid, limit=20):
        return [{"role": "user", "content": "help me"},
                {"role": "assistant", "content": "sure"}]

    async def _create_ticket(session_id, reason, channel, delivered, payload):
        calls["tickets"].append({"reason": reason, "channel": channel,
                                 "delivered": delivered, "payload": payload})
        return 11

    async def _mark(ticket_id):
        calls["delivered"].append(ticket_id)

    async def _log(sid, type_, payload=None):
        calls["events"].append(type_)

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "create_escalation_ticket", _create_ticket)
    monkeypatch.setattr(db, "mark_ticket_delivered", _mark)
    monkeypatch.setattr(db, "log_admin_event", _log)
    return calls


_SESSION = {"id": "sess-1", "topic_id": 1, "player_id": "p1",
            "user_context": {"id": "p1", "full_name": "A"}}


async def test_ticket_created_and_delivered(monkeypatch, _wire):
    async def _send(payload):
        return True
    monkeypatch.setattr(telegram, "send_escalation", _send)

    payload = await escalation.open_ticket(_SESSION, "user_request", "en")
    assert payload["active"] is True
    assert payload["button"]["label"]                       # button always present
    assert _wire["tickets"][0]["channel"] == "telegram"
    assert _wire["delivered"] == [11]                       # marked delivered
    assert "telegram_notify_failed" not in _wire["events"]


async def test_telegram_failure_still_returns_button(monkeypatch, _wire):
    async def _send(payload):
        return False
    monkeypatch.setattr(telegram, "send_escalation", _send)

    payload = await escalation.open_ticket(_SESSION, "cap_reached", "ru")
    assert payload["active"] is True
    assert payload["button"]["label"]                       # user never stranded
    assert _wire["delivered"] == []                         # not delivered
    assert "telegram_notify_failed" in _wire["events"]


async def test_button_only_when_telegram_unconfigured(monkeypatch, _wire):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", None)
    payload = await escalation.open_ticket(_SESSION, "keyword", "en")
    assert payload["active"] is True
    assert _wire["tickets"][0]["channel"] == "button"
    assert _wire["delivered"] == []


def test_format_message_includes_context_and_link(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://x.example")
    msg = telegram.format_message({
        "session_id": "abc", "reason": "user_request", "topic": "deposits",
        "lang": "en", "user_context": {"id": "p9", "full_name": "Bob"},
        "transcript": [{"role": "user", "content": "hi"}],
    })
    assert "deposits" in msg and "p9" in msg
    assert "https://x.example/admin#/session/abc" in msg
