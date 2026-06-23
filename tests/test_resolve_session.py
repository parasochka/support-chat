"""POST /api/chat/resolve: the player ending the chat from the 'finish chat' nudge.

Closes the session (status='resolved') and logs the close, but never overrides a
pending escalation (a hand-off to a human must not be closed by the player).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import auth
import chat_service
import db
import kb
from api import chat as chat_api


def _payload(resp):
    return json.loads(bytes(resp.body))


def _stub_auth(monkeypatch, session):
    """Make _auth_session resolve to `session` for any token."""
    monkeypatch.setattr(auth, "extract_bearer", lambda h: "tok")
    monkeypatch.setattr(auth, "verify_session_token", lambda t, s: {"sid": s})

    async def fake_get_session(sid):
        return session

    monkeypatch.setattr(db, "get_session", fake_get_session)


async def test_resolve_marks_session_and_logs(monkeypatch):
    calls = {"resolved": None, "event": None}

    async def fake_mark_resolved(sid):
        calls["resolved"] = sid

    async def fake_log(sid, type_, meta):
        calls["event"] = (sid, type_, meta)

    _stub_auth(monkeypatch, {"id": "s1", "status": "open", "message_count": 4})
    monkeypatch.setattr(db, "mark_resolved", fake_mark_resolved)
    monkeypatch.setattr(db, "log_admin_event", fake_log)

    resp = await chat_api.resolve(chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer tok")
    data = _payload(resp)
    assert data == {"ok": True, "status": "resolved"}
    assert calls["resolved"] == "s1"
    assert calls["event"][1] == "session_resolved"
    assert calls["event"][2]["message_count"] == 4


async def test_resolve_does_not_close_escalated_session(monkeypatch):
    """A pending hand-off to a human must survive the player tapping finish."""
    touched = {"resolved": False, "logged": False}

    async def fake_mark_resolved(sid):
        touched["resolved"] = True

    async def fake_log(sid, type_, meta):
        touched["logged"] = True

    _stub_auth(monkeypatch, {"id": "s1", "status": "escalated", "message_count": 2})
    monkeypatch.setattr(db, "mark_resolved", fake_mark_resolved)
    monkeypatch.setattr(db, "log_admin_event", fake_log)

    resp = await chat_api.resolve(chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer tok")
    assert _payload(resp)["ok"] is True
    # Neither the close nor the event fired for an escalated session.
    assert touched == {"resolved": False, "logged": False}


async def test_resolve_idempotent_when_already_resolved(monkeypatch):
    touched = {"resolved": False}

    async def fake_mark_resolved(sid):
        touched["resolved"] = True

    async def fake_log(sid, type_, meta):
        pass

    _stub_auth(monkeypatch, {"id": "s1", "status": "resolved", "message_count": 1})
    monkeypatch.setattr(db, "mark_resolved", fake_mark_resolved)
    monkeypatch.setattr(db, "log_admin_event", fake_log)

    resp = await chat_api.resolve(chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer tok")
    assert _payload(resp)["ok"] is True
    # No second write for an already-resolved session.
    assert touched["resolved"] is False


async def test_message_to_resolved_session_is_rejected_before_generation(monkeypatch):
    touched = {"handled": False, "persisted": False}

    async def fake_handle_message(session, user_text):
        touched["handled"] = True

    async def fake_persist_turn(**kwargs):
        touched["persisted"] = True

    _stub_auth(monkeypatch, {"id": "s1", "status": "resolved", "message_count": 4})
    monkeypatch.setattr(chat_service, "handle_message", fake_handle_message)
    monkeypatch.setattr(db, "persist_turn", fake_persist_turn)

    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    resp = await chat_api.send_message(
        req,
        chat_api.MessageSend(session_id="s1", text="какие провайдеры игр есть?"),
        authorization="Bearer tok",
    )

    data = _payload(resp)
    assert resp.status_code == 409
    assert data["error"] == "session_closed"
    assert touched == {"handled": False, "persisted": False}


async def test_topic_change_on_resolved_session_is_rejected(monkeypatch):
    touched = {"topic_lookup": False, "set_topic": False}

    async def fake_topic_by_slug(slug):
        touched["topic_lookup"] = True
        return {"id": 2, "slug": slug}

    async def fake_set_session_topic(sid, topic_id):
        touched["set_topic"] = True

    _stub_auth(monkeypatch, {"id": "s1", "status": "resolved", "message_count": 4})
    monkeypatch.setattr(kb, "topic_by_slug", fake_topic_by_slug)
    monkeypatch.setattr(db, "set_session_topic", fake_set_session_topic)

    resp = await chat_api.select_topic(
        chat_api.TopicSelect(session_id="s1", topic_slug="betting_games"),
        authorization="Bearer tok",
    )

    data = _payload(resp)
    assert resp.status_code == 409
    assert data["error"] == "session_closed"
    assert touched == {"topic_lookup": False, "set_topic": False}
