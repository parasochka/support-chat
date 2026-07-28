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

from app.core import db
from tests.conftest import FakeConn, FakePool


async def test_delete_photo_hard_deletes_row_and_views(monkeypatch):
    conn = FakeConn(row={"storage_ref": "clip.tg.mp4",
                         "media_type": "video"})
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
    conn = FakeConn(row=None)
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.delete_retention_photo(999) is None
    # Nothing deleted when the row doesn't exist.
    assert conn.executed == []


async def test_delete_endpoint_unlinks_binary_and_poster(monkeypatch, tmp_path):
    """The API layer of the hard delete: after the row is gone, the on-disk
    binary is removed too — and a video also drops its poster frame."""
    from app.api import retention as api_retention
    from app.core import config

    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(tmp_path))
    (tmp_path / "clip.tg.mp4").write_bytes(b"v")
    (tmp_path / "clip.poster.webp").write_bytes(b"p")

    async def _get(pid):
        return {"id": 42, "product_id": 1, "media_type": "video",
                "storage_ref": "clip.tg.mp4"}
    monkeypatch.setattr(api_retention.db, "get_retention_photo", _get)

    async def _delete(pid):
        return {"storage_ref": "clip.tg.mp4", "media_type": "video"}
    monkeypatch.setattr(api_retention.db, "delete_retention_photo", _delete)

    async def _scope(admin, product_id):
        return None
    monkeypatch.setattr(api_retention.admin_auth, "require_product_write",
                        _scope)

    resp = await api_retention.delete_photo(42, admin={"email": "t@x"})
    assert resp.status_code == 200
    assert not (tmp_path / "clip.tg.mp4").exists()
    assert not (tmp_path / "clip.poster.webp").exists()
