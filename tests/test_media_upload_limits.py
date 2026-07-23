"""Server-side per-file byte caps on the retention media upload
(POST /admin/retention/photos): one over-cap or unsupported file rejects the
whole batch BEFORE anything is written to disk or the catalogue."""
from __future__ import annotations

import io
import types

import pytest
from fastapi import HTTPException

import api.retention as api_retention
import config


def _upload(name: str, size: int = 10, spooled: bool = False):
    """A minimal stand-in for starlette's UploadFile: .filename + .size, or —
    with `spooled` — no .size attribute at all, only the underlying file object
    (the client that sends multipart parts without a per-part length)."""
    up = types.SimpleNamespace(filename=name)
    if spooled:
        up.file = io.BytesIO(b"x" * size)
    else:
        up.size = size
    return up


@pytest.fixture
def admin(monkeypatch):
    async def _ok(a, product_id):
        return None
    monkeypatch.setattr(api_retention.admin_auth, "require_product_write", _ok)
    return {"email": "t@example.com"}


async def _call(files, admin):
    return await api_retention.create_photo(
        product_id=1, description="", tags="", level_min=0, stage=1,
        category="", sort_order=0, file=None, files=files, admin=admin)


async def test_over_cap_video_rejects_batch(monkeypatch, admin, tmp_path):
    media = tmp_path / "media"
    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(media))
    monkeypatch.setattr(config, "RETENTION_MAX_VIDEO_BYTES", 100)
    files = [_upload("ok.jpg", 50), _upload("big.mp4", 101)]
    with pytest.raises(HTTPException) as ei:
        await _call(files, admin)
    assert ei.value.status_code == 400
    assert "limit per video" in ei.value.detail
    assert not media.exists()  # rejected before anything touched the disk


async def test_over_cap_photo_rejects_batch(monkeypatch, admin, tmp_path):
    media = tmp_path / "media"
    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(media))
    monkeypatch.setattr(config, "RETENTION_MAX_PHOTO_BYTES", 100)
    with pytest.raises(HTTPException) as ei:
        await _call([_upload("huge.png", 101)], admin)
    assert ei.value.status_code == 400
    assert "limit per photo" in ei.value.detail
    assert not media.exists()


async def test_size_read_from_spool_when_missing(monkeypatch, admin, tmp_path):
    """A part without a per-part length leaves UploadFile.size unset — the cap
    still enforces by measuring the spooled file instead of silently passing."""
    media = tmp_path / "media"
    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(media))
    monkeypatch.setattr(config, "RETENTION_MAX_PHOTO_BYTES", 100)
    with pytest.raises(HTTPException) as ei:
        await _call([_upload("big.jpg", 101, spooled=True)], admin)
    assert ei.value.status_code == 400
    assert "limit per photo" in ei.value.detail
    assert not media.exists()


async def test_unsupported_extension_rejects_batch(admin, tmp_path, monkeypatch):
    media = tmp_path / "media"
    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(media))
    with pytest.raises(HTTPException) as ei:
        await _call([_upload("evil.exe", 5)], admin)
    assert ei.value.status_code == 400
    assert "Unsupported media type" in ei.value.detail
    assert not media.exists()
