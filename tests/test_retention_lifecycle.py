"""Telegram chat lifecycle: idle rollover + returning-player continuity.

An idle Telegram chat is closed (status='resolved') and the player's next
message starts a FRESH chat session pointing at the old one (prev_session_id);
the first prompt of the fresh chat carries the previous conversation's tail as
a Layer-3 returning-player block. Covers retention.session_expired,
retention._ensure_session rollover wiring, and the prompt block itself.
"""
from __future__ import annotations

import datetime as dt

import prompts
import retention
import settings


def _session(**over):
    base = {
        "id": "old-sess", "status": "open", "message_count": 4,
        "updated_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12),
        "product_id": 1,
    }
    base.update(over)
    return base


def _set_idle_minutes(monkeypatch, minutes):
    cfg = dict(settings.retention())
    cfg["session_idle_minutes"] = minutes
    monkeypatch.setattr(retention.settings, "retention", lambda: cfg)


# ---------------------------------------------------------------------------
# session_expired
# ---------------------------------------------------------------------------
def test_fresh_session_is_not_expired(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    s = _session(updated_at=dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(minutes=5))
    assert retention.session_expired(s) is False


def test_idle_session_expires(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    assert retention.session_expired(_session()) is True  # 12h idle > 6h


def test_zero_disables_the_lifecycle(monkeypatch):
    _set_idle_minutes(monkeypatch, 0)
    assert retention.session_expired(_session()) is False


def test_empty_session_never_expires(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    assert retention.session_expired(_session(message_count=0)) is False


def test_isoformat_updated_at_is_handled(monkeypatch):
    _set_idle_minutes(monkeypatch, 60)
    stamp = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=3)).isoformat()
    assert retention.session_expired(_session(updated_at=stamp)) is True


# ---------------------------------------------------------------------------
# _ensure_session rollover
# ---------------------------------------------------------------------------
def _wire_db(monkeypatch, existing, store):
    async def _get_session(sid):
        if sid == existing.get("id"):
            return existing
        return store.get(sid)
    monkeypatch.setattr(retention.db, "get_session", _get_session)

    async def _close(sid, product_id=None, reason="idle"):
        store["closed"] = (sid, reason)
    monkeypatch.setattr(retention.db, "close_retention_session", _close)

    async def _create(**kw):
        store["created"] = kw
        store[kw["session_id"]] = dict(
            id=kw["session_id"], status="open", message_count=0,
            prev_session_id=kw.get("prev_session_id"),
            updated_at=dt.datetime.now(dt.timezone.utc))
        return kw["session_id"]
    monkeypatch.setattr(retention.db, "create_session", _create)

    async def _link(rid, sid):
        store["linked"] = sid
    monkeypatch.setattr(retention.db, "set_retention_session", _link)


RU = {"id": 9, "tg_user_id": 42, "player_id": "p1", "session_id": "old-sess"}


async def test_live_session_is_reused(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    existing = _session(updated_at=dt.datetime.now(dt.timezone.utc))
    store: dict = {}
    _wire_db(monkeypatch, existing, store)

    got = await retention._ensure_session(1, dict(RU), "en")

    assert got["id"] == "old-sess"
    assert "created" not in store and "closed" not in store


async def test_idle_session_rolls_over(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    existing = _session()  # 12h idle, 4 msgs
    store: dict = {}
    _wire_db(monkeypatch, existing, store)

    got = await retention._ensure_session(1, dict(RU), "en")

    assert store["closed"] == ("old-sess", "idle")
    assert got["id"] != "old-sess"
    assert store["created"]["prev_session_id"] == "old-sess"
    assert store["linked"] == got["id"]


async def test_already_closed_session_starts_fresh_with_anchor(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    existing = _session(status="resolved")
    store: dict = {}
    _wire_db(monkeypatch, existing, store)

    got = await retention._ensure_session(1, dict(RU), "en")

    assert "closed" not in store  # already closed - nothing to close
    assert store["created"]["prev_session_id"] == "old-sess"
    assert got["prev_session_id"] == "old-sess"


async def test_idle_but_empty_open_session_is_reused(monkeypatch):
    _set_idle_minutes(monkeypatch, 360)
    existing = _session(message_count=0)
    store: dict = {}
    _wire_db(monkeypatch, existing, store)

    got = await retention._ensure_session(1, dict(RU), "en")

    assert got["id"] == "old-sess"
    assert "created" not in store


# ---------------------------------------------------------------------------
# Returning-player continuity block (Layer 3, first turn only)
# ---------------------------------------------------------------------------
_PREV = [
    {"role": "user", "content": "привет, расскажи про бонус",
     "created_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)},
    {"role": "assistant", "content": "конечно, лови детали...",
     "created_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)},
]


def test_first_turn_carries_previous_context():
    msgs = prompts.build_retention_messages(
        session={"user_context": {}}, kb_block=None, history=[],
        user_text="я вернулся", resolved_lang="ru",
        previous_history=_PREV,
    )
    body = msgs[-1]["content"]
    assert "RETURNING PLAYER" in body
    assert "расскажи про бонус" in body
    assert "3 days ago" in body  # rough recency rendered


def test_later_turns_drop_previous_context():
    history = [{"role": "user", "content": "hi"},
               {"role": "assistant", "content": "hey"}]
    msgs = prompts.build_retention_messages(
        session={"user_context": {}}, kb_block=None, history=history,
        user_text="ещё вопрос", resolved_lang="ru",
        previous_history=_PREV,
    )
    assert "RETURNING PLAYER" not in msgs[-1]["content"]


def test_no_previous_history_no_block():
    msgs = prompts.build_retention_messages(
        session={"user_context": {}}, kb_block=None, history=[],
        user_text="привет", resolved_lang="ru",
    )
    assert "RETURNING PLAYER" not in msgs[-1]["content"]


def test_carried_messages_are_truncated():
    long_prev = [{"role": "user", "content": "x" * 1000,
                  "created_at": dt.datetime.now(dt.timezone.utc)}]
    block = prompts._previous_context_directive(long_prev)
    line = [l for l in block.splitlines() if l.startswith("player:")][0]
    assert len(line) < 300
