"""Media normalizer: WebP re-encode of heavy retention photos for Telegram.

Covers the decision function, the re-encode itself (resize + format), the
per-product sweep (row re-point + original delete + one-bad-file isolation)
and the settings knobs.
"""
from __future__ import annotations


import pytest
from PIL import Image

import config
import media_normalizer
import settings


def _make_image(path: str, size: tuple[int, int], fmt: str) -> None:
    Image.new("RGB", size, (120, 40, 40)).save(path, fmt)


@pytest.fixture
def media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RETENTION_MEDIA_DIR", str(tmp_path))
    return tmp_path


def test_needs_normalization_decisions(media_dir):
    jpg = str(media_dir / "a.jpg")
    _make_image(jpg, (800, 600), "JPEG")
    assert media_normalizer.needs_normalization(jpg, 2048)  # always convert jpg

    small_webp = str(media_dir / "b.webp")
    _make_image(small_webp, (1024, 768), "WEBP")
    assert not media_normalizer.needs_normalization(small_webp, 2048)

    big_webp = str(media_dir / "c.webp")
    _make_image(big_webp, (3000, 1500), "WEBP")
    assert media_normalizer.needs_normalization(big_webp, 2048)

    gif = str(media_dir / "d.gif")
    _make_image(gif, (4000, 2000), "GIF")
    assert not media_normalizer.needs_normalization(gif, 2048)  # left alone

    not_an_image = str(media_dir / "e.jpg")
    with open(not_an_image, "wb") as fh:
        fh.write(b"not an image at all")
    assert not media_normalizer.needs_normalization(not_an_image, 2048)


def test_normalize_file_resizes_and_converts(media_dir):
    src = str(media_dir / "big.jpg")
    dst = str(media_dir / "big.webp")
    _make_image(src, (4000, 2000), "JPEG")
    w, h = media_normalizer.normalize_file(src, dst, max_side=2048, quality=82)
    assert max(w, h) <= 2048
    with Image.open(dst) as im:
        assert im.format == "WEBP"
        assert max(im.size) <= 2048
    # A small file is converted without upscaling.
    small = str(media_dir / "small.png")
    small_dst = str(media_dir / "small.webp")
    _make_image(small, (640, 480), "PNG")
    assert media_normalizer.normalize_file(
        small, small_dst, max_side=2048, quality=82) == (640, 480)


class _FakeDb:
    def __init__(self, photos):
        self.photos = photos
        self.repointed = {}
        self.events = []

    async def list_retention_photos(self, product_id, **kw):
        return self.photos

    async def set_retention_photo_storage_ref(self, photo_id, ref):
        self.repointed[photo_id] = ref

    async def log_admin_event(self, session_id, kind, payload, product_id=None):
        self.events.append((kind, payload, product_id))


@pytest.fixture
def fake_db(monkeypatch):
    def _install(photos):
        fake = _FakeDb(photos)
        monkeypatch.setattr(media_normalizer.db, "list_retention_photos",
                            fake.list_retention_photos)
        monkeypatch.setattr(media_normalizer.db,
                            "set_retention_photo_storage_ref",
                            fake.set_retention_photo_storage_ref)
        monkeypatch.setattr(media_normalizer.db, "log_admin_event",
                            fake.log_admin_event)
        return fake
    return _install


def _retention_cfg(monkeypatch, **over):
    cfg = {"media_normalize_enabled": True, "media_max_side_px": 2048,
           "media_webp_quality": 82, **over}
    monkeypatch.setattr(settings, "retention", lambda: cfg)


async def test_sweep_converts_repoints_and_deletes(media_dir, fake_db,
                                                   monkeypatch):
    _retention_cfg(monkeypatch)
    big = media_dir / "p1_x.jpg"
    _make_image(str(big), (4000, 2000), "JPEG")
    ok_webp = media_dir / "p1_y.webp"
    _make_image(str(ok_webp), (1024, 768), "WEBP")
    fake = fake_db([
        {"id": 1, "storage_ref": "p1_x.jpg"},
        {"id": 2, "storage_ref": "p1_y.webp"},   # already fine — untouched
        {"id": 3, "storage_ref": "missing.jpg"},  # gone from disk — skipped
        {"id": 4, "storage_ref": None},
    ])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and stats["failed"] == 0
    assert stats["checked"] == 2
    assert fake.repointed == {1: "p1_x.webp"}
    assert not big.exists()                      # heavy original deleted
    assert (media_dir / "p1_x.webp").exists()
    assert ok_webp.exists()
    assert fake.events and fake.events[0][0] == "retention_media_normalized"


async def test_sweep_one_bad_file_does_not_kill_it(media_dir, fake_db,
                                                   monkeypatch):
    _retention_cfg(monkeypatch)
    # A file that PROBES as an image but fails on full decode: truncated jpg.
    good = media_dir / "p2_good.jpg"
    _make_image(str(good), (3000, 1500), "JPEG")
    bad = media_dir / "p2_bad.jpg"
    bad.write_bytes(good.read_bytes()[:len(good.read_bytes()) // 3])
    fake = fake_db([
        {"id": 10, "storage_ref": "p2_bad.jpg"},
        {"id": 11, "storage_ref": "p2_good.jpg"},
    ])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and stats["failed"] == 1
    assert fake.repointed == {11: "p2_good.webp"}
    assert bad.exists()  # the bad original is never deleted


async def test_sweep_respects_enabled_switch_and_force(media_dir, fake_db,
                                                       monkeypatch):
    _retention_cfg(monkeypatch, media_normalize_enabled=False)
    jpg = media_dir / "p3.jpg"
    _make_image(str(jpg), (3000, 1500), "JPEG")
    fake = fake_db([{"id": 20, "storage_ref": "p3.jpg"}])
    assert (await media_normalizer.normalize_product_photos(1)) == {
        "skipped": "media_normalize_disabled"}
    assert jpg.exists() and not fake.repointed
    # force=True (the admin «Normalize now» button) bypasses the switch.
    stats = await media_normalizer.normalize_product_photos(1, force=True)
    assert stats["normalized"] == 1 and fake.repointed == {20: "p3.webp"}


def test_media_settings_validation():
    ok = {"media_normalize_enabled": True, "media_normalize_interval_sec": 3600,
          "media_max_side_px": 2048, "media_webp_quality": 82}
    assert settings.validate_setting("retention", ok) == ok
    for bad in ({"media_normalize_interval_sec": 10},
                {"media_max_side_px": 100},
                {"media_webp_quality": 10},
                {"media_normalize_enabled": "yes"}):
        with pytest.raises(ValueError):
            settings.validate_setting("retention", bad)


def test_interval_reads_global_layer(monkeypatch):
    monkeypatch.setattr(settings, "retention",
                        lambda: {"media_normalize_interval_sec": 60})
    assert media_normalizer.interval_sec() == 300  # clamped low
    monkeypatch.setattr(settings, "retention",
                        lambda: {"media_normalize_interval_sec": 7200})
    assert media_normalizer.interval_sec() == 7200
