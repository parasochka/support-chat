"""delete_session: a Telegram conversation delete also purges the linked player.

db.* calls are normally not unit-tested (no real Postgres in the suite), but the
retention player purge is destructive and irreversible, so we pin its shape with
a tiny fake connection that records the executed statements.
"""
from __future__ import annotations

from app.core import db
from tests.conftest import FakeConn, FakePool


async def test_delete_telegram_session_purges_player(monkeypatch):
    conn = FakeConn(row={"consumer": "telegram", "product_id": 7,
                         "tg_user_id": 555},
                    rows=[{"id": 3}, {"id": 9}])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session("00000000-0000-0000-0000-000000000000") is True

    sqls = [s for s, _ in conn.executed]
    joined = " ".join(sqls)
    # The player footprint is purged, children (FK to retention_users) first.
    assert any("DELETE FROM retention_photo_views" in s for s in sqls)
    assert any("DELETE FROM retention_pings" in s for s in sqls)
    assert any("DELETE FROM retention_users WHERE id" in s for s in sqls)
    # photo_views must be deleted before the retention_users row (FK NOT NULL).
    order = [i for i, s in enumerate(sqls)
             if "retention_photo_views" in s or "DELETE FROM retention_users WHERE id" in s]
    assert sqls[order[0]].__contains__("retention_photo_views")
    # The resolved player ids are the ones deleted.
    for s, a in conn.executed:
        if ("retention_photo_views" in s or "retention_pings" in s
                or "DELETE FROM retention_users WHERE id" in s):
            assert a[0] == [3, 9]
    # Transcript rows still go too.
    assert "DELETE FROM chat_sessions WHERE id" in joined


async def test_every_reference_to_the_player_is_cleared_before_the_delete(
        monkeypatch):
    """`retention_users` is referenced by FIVE tables. Two of them —
    `retention_deliveries` and `retention_journey_enrollments` — were missed:
    both are nullable FKs with NO `ON DELETE` clause, so the final DELETE hit a
    foreign-key violation, rolled the whole transaction back and the admin
    endpoint 500'd having deleted NOTHING. That becomes the normal case the
    moment the send worker or the journey engine is on for a product.
    """
    conn = FakeConn(row={"consumer": "telegram", "product_id": 7,
                         "tg_user_id": 555},
                    rows=[{"id": 3}, {"id": 9}])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session(
        "00000000-0000-0000-0000-000000000000") is True

    sqls = [s for s, _ in conn.executed]
    final = next(i for i, s in enumerate(sqls)
                 if "DELETE FROM retention_users WHERE id" in s)
    before = " ".join(sqls[:final])
    for table in ("retention_photo_views", "retention_pings",
                  "retention_outcomes", "retention_v2_decisions",
                  "retention_deliveries", "retention_journey_enrollments"):
        assert table in before, f"{table} still references the deleted player"

    # An in-flight delivery is CLOSED, not just detached — an orphan row left
    # claimable would be picked up by the send worker for a player that is gone.
    assert any("retention_deliveries" in s and "permanent_fail = TRUE" in s
               for s in sqls)
    # An active enrollment stops rather than staying due forever.
    assert any("retention_journey_enrollments" in s and "exited_terminal" in s
               for s in sqls)


async def test_delete_web_session_keeps_retention_tables(monkeypatch):
    conn = FakeConn(row={"consumer": "web", "product_id": 7,
                         "tg_user_id": None},
                    rows=[{"id": 3}])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session("00000000-0000-0000-0000-000000000000") is True

    sqls = " ".join(s for s, _ in conn.executed)
    # A support session never touches the retention player footprint.
    assert "DELETE FROM retention_photo_views" not in sqls
    assert "DELETE FROM retention_pings" not in sqls
    assert "DELETE FROM retention_users WHERE id" not in sqls


async def test_delete_missing_session_returns_false(monkeypatch):
    conn = FakeConn(row=None, rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session("00000000-0000-0000-0000-000000000000") is False
    assert conn.executed == []
