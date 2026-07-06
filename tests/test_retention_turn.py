"""chat_service.handle_retention_message — the retention (Telegram) AI turn.

Covers the parts unique to this second facade: retention sentinel stripping
([[PHOTO]]/[[STAGE_UP]]/[[HANDOFF]]/[[LANG]]), photo-id re-validation against the
allowed candidate set, atomic persistence, and the transient-model-failure path
(no persist, ok=False). The prompt build + model call are stubbed; the assertions
are on how the reply is decoded and what gets persisted.
"""
from __future__ import annotations

import chat_service
import db
import openai_client
import prompts


def _fake_result(text: str):
    return openai_client.ChatResult(
        text=text, lang="en", tokens_in=10, tokens_out=5, cached_in=0,
        model="gpt-5-mini", key_used="primary", latency_ms=1,
    )


class _FakeClient:
    def __init__(self, text):
        self._text = text

    async def complete(self, messages, session_id=None, on_failover=None):
        return _fake_result(self._text)


class _FailingClient:
    async def complete(self, messages, session_id=None, on_failover=None):
        raise RuntimeError("provider down")


def _wire(monkeypatch, client, *, capture: dict):
    async def _kb(pid):
        return ""  # empty -> render_variables skipped
    monkeypatch.setattr(db, "retention_kb_block", _kb)

    async def _history(sid, limit=20, after_id=0):
        return []
    monkeypatch.setattr(db, "get_history", _history)

    monkeypatch.setattr(prompts, "build_retention_messages",
                        lambda **kw: [{"role": "system", "content": "x"}])

    async def _client_for(pid):
        return client
    monkeypatch.setattr(openai_client, "client_for_product", _client_for)

    async def _persist(**kwargs):
        capture.update(kwargs)
        return 7
    monkeypatch.setattr(db, "persist_turn", _persist)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(db, "set_conv_lang", _noop)
    monkeypatch.setattr(db, "log_ai_interaction", _noop)
    monkeypatch.setattr(db, "log_admin_event_sampled", _noop)


def _session(**over):
    base = {"id": "sess-1", "product_id": 1, "user_context": {},
            "lang": "en", "conv_lang": None, "message_count": 0}
    base.update(over)
    return base


async def test_photo_id_from_candidates_is_honoured(monkeypatch):
    cap: dict = {}
    _wire(monkeypatch, _FakeClient("[[PHOTO:55]] here you go"), capture=cap)
    candidates = [{"id": 55, "stage": 2, "description": "beach"}]

    reply = await chat_service.handle_retention_message(
        _session(), "покажи фото", photo_candidates=candidates)

    assert reply.photo_id == 55
    assert reply.reply == "here you go"
    assert reply.ok is True
    assert cap["assistant_text"] == "here you go"


async def test_photo_id_outside_candidates_is_rejected(monkeypatch):
    cap: dict = {}
    _wire(monkeypatch, _FakeClient("[[PHOTO:99]] not allowed"), capture=cap)
    candidates = [{"id": 55, "description": "beach"}]

    reply = await chat_service.handle_retention_message(
        _session(), "another photo", photo_candidates=candidates)

    # 99 is not in the allowed set -> dropped; the caption text still shows.
    assert reply.photo_id is None
    assert reply.reply == "not allowed"


async def test_handoff_and_stage_up_and_lang_sentinels(monkeypatch):
    cap: dict = {}
    _wire(monkeypatch,
          _FakeClient("[[LANG:ru]]\n[[HANDOFF]]\n[[STAGE_UP]]\nдавай передам тебя"),
          capture=cap)

    reply = await chat_service.handle_retention_message(
        _session(), "хочу поговорить с менеджером", photo_candidates=[])

    assert reply.handoff is True
    assert reply.stage_up_hint is True
    assert reply.lang == "ru"
    assert reply.reply == "давай передам тебя"
    # answer language drift persisted
    assert cap["assistant_lang"] == "ru"


async def test_transient_model_failure_does_not_persist(monkeypatch):
    cap: dict = {}
    _wire(monkeypatch, _FailingClient(), capture=cap)

    reply = await chat_service.handle_retention_message(
        _session(message_count=4), "hey", photo_candidates=[])

    assert reply.ok is False
    assert reply.message_count == 4  # unchanged: nothing persisted
    assert reply.reply  # a localized "technical hiccup" nudge
    assert cap == {}  # persist_turn never called
