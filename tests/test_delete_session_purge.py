"""delete_session: a Telegram conversation delete also purges the linked player.

db.* calls are normally not unit-tested (no real Postgres in the suite), but the
retention player purge is destructive and irreversible, so we pin its shape with
a tiny fake connection that records the executed statements.
"""
from __future__ import annotations

import db


class _Tx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *a):
        return False


class _Acq:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakeConn:
    def __init__(self, session_row, user_ids):
        self.session_row = session_row
        self.user_ids = user_ids
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        return self.session_row

    async def fetch(self, sql, *args):
        return [{"id": i} for i in self.user_ids]

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acq(self._conn)


async def test_delete_telegram_session_purges_player(monkeypatch):
    conn = FakeConn(
        {"consumer": "telegram", "product_id": 7, "tg_user_id": 555}, [3, 9]
    )
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


async def test_delete_web_session_keeps_retention_tables(monkeypatch):
    conn = FakeConn(
        {"consumer": "web", "product_id": 7, "tg_user_id": None}, [3]
    )
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session("00000000-0000-0000-0000-000000000000") is True

    sqls = " ".join(s for s, _ in conn.executed)
    # A support session never touches the retention player footprint.
    assert "DELETE FROM retention_photo_views" not in sqls
    assert "DELETE FROM retention_pings" not in sqls
    assert "DELETE FROM retention_users WHERE id" not in sqls


async def test_delete_missing_session_returns_false(monkeypatch):
    conn = FakeConn(None, [])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_session("00000000-0000-0000-0000-000000000000") is False
    assert conn.executed == []
