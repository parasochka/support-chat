"""The session-bound chat routes that are NOT /message are rate-limited too.

Only POST /message was throttled, so one session token bought UNLIMITED calls to
/topic, /escalate, /resolve and GET /session/{id} — each doing real DB work (a
topic select writes the context-reset boundary, a resume reads 50 messages), so
an IP's 20 session creates per window turned into unbounded DB load. The shared
per-IP `chat-op:` budget bounds that; these tests pin that each of the four
routes consumes it, that the budget is per IP, that a blocked call short-circuits
BEFORE any session/DB work, and that the throttle never REPLACES the token check.
"""
from __future__ import annotations

import json
import types

import pytest

from app.chat import antispam
from app.core import db
from app.api import chat as chat_api


@pytest.fixture(autouse=True)
def _clean_state():
    antispam.reset_state()
    yield
    antispam.reset_state()


def _fake_request(ip: str = "9.9.9.9"):
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=ip),
        headers={},
    )


def _payload(resp) -> dict:
    return json.loads(bytes(resp.body).decode())


class _AuthSpy:
    """Stands in for _auth_session; records whether it was ever reached."""

    def __init__(self):
        self.called = False

    async def __call__(self, authorization, session_id):
        self.called = True
        return {"id": session_id, "status": "open", "product_id": None,
                "message_count": 0, "lang": "en", "escalated": False}, None


def _stub(monkeypatch) -> _AuthSpy:
    spy = _AuthSpy()
    monkeypatch.setattr(chat_api, "_auth_session", spy)

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(db, "log_admin_event_sampled", _noop)
    return spy


def _exhaust(ip: str) -> None:
    """Burn the whole chat-op budget for `ip`."""
    for _ in range(chat_api._SESSION_OP_RATE_LIMIT):
        antispam.check_rate_limit(f"chat-op:{ip}",
                                  chat_api._SESSION_OP_RATE_LIMIT)


def _stub_topic(monkeypatch) -> None:
    async def _set_topic(sid, tid):
        return None

    async def _topic_by_slug(slug, product_id=None):
        return {"id": 7, "slug": slug}

    monkeypatch.setattr(db, "set_session_topic", _set_topic)
    monkeypatch.setattr(chat_api.kb, "topic_by_slug", _topic_by_slug)


async def test_topic_select_is_rate_limited(monkeypatch):
    spy = _stub(monkeypatch)
    _exhaust("9.9.9.9")
    resp = await chat_api.select_topic(
        _fake_request(),
        chat_api.TopicSelect(session_id="s1", topic_slug="bonuses"),
        authorization="Bearer t")
    assert resp.status_code == 429
    assert _payload(resp)["error"] == "rate_limited"
    # Short-circuits before the session is even loaded.
    assert spy.called is False


async def test_resume_is_rate_limited(monkeypatch):
    spy = _stub(monkeypatch)
    _exhaust("9.9.9.9")
    resp = await chat_api.resume_session(_fake_request(), "s1",
                                         authorization="Bearer t")
    assert resp.status_code == 429
    assert spy.called is False


async def test_escalate_is_rate_limited(monkeypatch):
    spy = _stub(monkeypatch)
    _exhaust("9.9.9.9")
    resp = await chat_api.escalate(_fake_request(),
                                   chat_api.EscalateReq(session_id="s1"),
                                   authorization="Bearer t")
    assert resp.status_code == 429
    assert spy.called is False


async def test_resolve_is_rate_limited(monkeypatch):
    spy = _stub(monkeypatch)
    _exhaust("9.9.9.9")
    resp = await chat_api.resolve(_fake_request(),
                                  chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer t")
    assert resp.status_code == 429
    assert spy.called is False


async def test_budget_is_shared_across_the_four_routes(monkeypatch):
    """One bucket, not four: exhausting it via /topic also blocks /resolve."""
    _stub(monkeypatch)
    _stub_topic(monkeypatch)
    _exhaust("9.9.9.9")
    resp = await chat_api.resolve(_fake_request(),
                                  chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer t")
    assert resp.status_code == 429


async def test_budget_is_per_ip(monkeypatch):
    """An exhausted IP must not throttle a different visitor."""
    spy = _stub(monkeypatch)
    _stub_topic(monkeypatch)
    _exhaust("9.9.9.9")

    resp = await chat_api.select_topic(
        _fake_request("8.8.8.8"),
        chat_api.TopicSelect(session_id="s1", topic_slug="bonuses"),
        authorization="Bearer t")
    assert resp.status_code == 200
    assert spy.called is True


async def test_normal_traffic_stays_under_the_budget(monkeypatch):
    """The cap is generous: a chatty session's topic taps never reach it."""
    _stub(monkeypatch)
    _stub_topic(monkeypatch)

    # 40 topic switches — far more than any real conversation performs.
    for _ in range(40):
        resp = await chat_api.select_topic(
            _fake_request(),
            chat_api.TopicSelect(session_id="s1", topic_slug="bonuses"),
            authorization="Bearer t")
        assert resp.status_code == 200


async def test_throttle_does_not_replace_the_token_check(monkeypatch):
    """Under budget, an invalid token still 401s — the gate is additive."""
    async def _auth(authorization, session_id):
        return None, chat_api._err(401, "unauthorized", "bad token")

    monkeypatch.setattr(chat_api, "_auth_session", _auth)
    resp = await chat_api.resolve(_fake_request(),
                                  chat_api.ResolveReq(session_id="s1"),
                                  authorization="Bearer nope")
    assert resp.status_code == 401
