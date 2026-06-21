"""Topic-switch loop fix: after a switch the prompt carries only post-boundary
turns, so the previous topic's transcript can't re-trigger a [[TOPIC:...]]
suggestion back to it (the ping-pong the user hit).

`chat_service.handle_message` must feed `db.get_history` the session's
`context_reset_id` as `after_id`, and the assembled prompt must contain none of
the pre-switch conversation.
"""
from __future__ import annotations

import chat_service
import db
import kb
import openai_client
import prompt_store


def _wire(monkeypatch, *, captured):
    async def _get_topic(tid):
        return {"slug": "withdrawals", "id": tid}

    async def _get_history(session_id, limit=20, after_id=0):
        captured["after_id"] = after_id
        # The DB holds the old deposit chat, but a correct boundary filters it
        # out: emulate that by returning [] when after_id skips past it.
        full = [
            {"role": "user", "content": "как пополнить счёт?"},
            {"role": "assistant", "content": "вот как пополнить депозит"},
        ]
        return [] if after_id > 0 else full

    async def _set_lang(*a, **k):
        pass

    async def _suggestable(exclude_topic_id=None, lang="en"):
        return [{"slug": "deposits", "title": "Депозиты"}]

    async def _kb_block(topic_id, lang="en"):
        return "KB по выводам"

    async def _core(version_id):
        return "CORE"

    async def _persist(**kwargs):
        captured["persisted_user"] = kwargs.get("user_text")
        return 3

    async def _log(*a, **k):
        pass

    captured["messages"] = None

    class _FakeClient:
        async def complete(self, messages, session_id=None, on_failover=None):
            captured["messages"] = messages
            return openai_client.ChatResult(
                text="Чтобы вывести средства, ...", lang="ru",
                tokens_in=10, tokens_out=5, cached_in=0,
                model="gpt-test", key_used="primary", latency_ms=1,
            )

    monkeypatch.setattr(db, "get_topic_by_id", _get_topic)
    monkeypatch.setattr(db, "get_history", _get_history)
    monkeypatch.setattr(db, "set_session_lang", _set_lang)
    monkeypatch.setattr(db, "persist_turn", _persist)
    monkeypatch.setattr(db, "log_admin_event", _log)
    monkeypatch.setattr(kb, "suggestable_topics", _suggestable)
    monkeypatch.setattr(kb, "kb_block_for_topic", _kb_block)
    monkeypatch.setattr(prompt_store, "core_for_version", _core)
    monkeypatch.setattr(openai_client, "get_client", lambda: _FakeClient())


async def test_post_switch_prompt_excludes_previous_topic(monkeypatch):
    captured: dict = {}
    _wire(monkeypatch, captured=captured)

    # Session just switched to "withdrawals": context_reset_id points past the
    # old deposit turns.
    session = {
        "id": "sess-1", "topic_id": 2, "context_reset_id": 9,
        "user_context": {}, "message_count": 2, "lang": "ru",
    }
    await chat_service.handle_message(session, "как вывести деньги?")

    # Boundary forwarded to get_history…
    assert captured["after_id"] == 9
    # …and the assembled prompt carries no pre-switch deposit conversation.
    convo = "\n".join(m["content"] for m in captured["messages"])
    assert "пополнить" not in convo  # the old deposit transcript is gone
    # only the system message + the single triggering user turn
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]
    assert "как вывести деньги?" in captured["messages"][-1]["content"]


async def test_no_boundary_keeps_full_history(monkeypatch):
    """A normal turn (no switch) still threads the whole conversation."""
    captured: dict = {}
    _wire(monkeypatch, captured=captured)

    session = {
        "id": "sess-2", "topic_id": 2, "context_reset_id": 0,
        "user_context": {}, "message_count": 2, "lang": "ru",
    }
    await chat_service.handle_message(session, "а ещё вопрос")

    assert captured["after_id"] == 0
    roles = [m["role"] for m in captured["messages"]]
    # system + prior user/assistant pair + new user turn
    assert roles == ["system", "user", "assistant", "user"]
