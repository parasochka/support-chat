"""delete_retention_photo: the admin Delete button HARD-deletes the media row.

The function used to soft-delete (active=false), so the row + on-disk file
stayed and the item merely flipped to "inactive" instead of being removed. This
pins the new shape with a tiny fake connection that records the statements: the
seen-photo ledger goes first (FK NOT NULL to retention_photos), then the row,
and the deleted row's storage info is returned so the endpoint can unlink the
file. db.* calls are not otherwise unit-tested (no real Postgres in the suite),
but a destructive delete is worth pinning.
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
    def __init__(self, photo_row):
        self.photo_row = photo_row
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _Tx()

    async def fetchrow(self, sql, *args):
        return self.photo_row

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self, timeout=None):
        return _Acq(self._conn)


async def test_delete_photo_hard_deletes_row_and_views(monkeypatch):
    conn = FakeConn({"storage_ref": "clip.tg.mp4", "media_type": "video"})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    result = await db.delete_retention_photo(42)

    # The caller gets the storage info so it can unlink the file + poster.
    assert result == {"storage_ref": "clip.tg.mp4", "media_type": "video"}

    sqls = [s for s, _ in conn.executed]
    assert any("DELETE FROM retention_photo_views WHERE photo_id" in s for s in sqls)
    assert any("DELETE FROM retention_photos WHERE id" in s for s in sqls)
    # No soft-delete UPDATE — the row is really gone.
    assert not any("SET active = FALSE" in s for s in sqls)
    # The seen-photo ledger (FK NOT NULL) must be cleared BEFORE the photo row.
    views_at = next(i for i, s in enumerate(sqls) if "retention_photo_views" in s)
    photo_at = next(i for i, s in enumerate(sqls)
                    if "DELETE FROM retention_photos" in s)
    assert views_at < photo_at
    # Both statements target the passed id.
    for _, a in conn.executed:
        assert a[0] == 42


async def test_delete_missing_photo_returns_none(monkeypatch):
    conn = FakeConn(None)
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_retention_photo(999) is None
    # Nothing deleted when the row doesn't exist.
    assert conn.executed == []
