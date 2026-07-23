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


# --- video normalization ----------------------------------------------------

def test_video_ref_helpers():
    assert media_normalizer.is_video_ref("a.mp4")
    assert media_normalizer.is_video_ref("a.MOV")
    assert not media_normalizer.is_video_ref("a.webp")
    assert not media_normalizer.is_video_ref(None)

    # Normalized-once marker: the .tg.mp4 suffix ends the pipeline.
    assert media_normalizer.video_needs_normalization("v.mov")
    assert media_normalizer.video_needs_normalization("v.mp4")
    assert not media_normalizer.video_needs_normalization("v.tg.mp4")
    assert not media_normalizer.video_needs_normalization("photo.jpg")

    assert media_normalizer.video_target_refs("v.mov") == (
        "v.tg.mp4", "v.poster.webp")
    assert media_normalizer.video_target_refs("v.tg.mp4") == (
        "v.tg.mp4", "v.poster.webp")
    assert media_normalizer.poster_ref_for("v.webm") == "v.poster.webp"
    assert media_normalizer.poster_ref_for("photo.jpg") is None


def test_video_scale_filter_no_upscale():
    f = media_normalizer._video_scale_filter(1280)
    assert "min(1280,iw)" in f and "min(1280,ih)" in f
    assert "force_divisible_by=2" in f


async def test_sweep_transcodes_video_and_extracts_poster(media_dir, fake_db,
                                                          monkeypatch):
    """A video upload is re-encoded to .tg.mp4 + poster, re-pointed, original
    deleted — with ffmpeg stubbed out (not installed in CI)."""
    _retention_cfg(monkeypatch)
    src = media_dir / "p9_v.mov"
    src.write_bytes(b"fake-video-bytes" * 1000)
    calls = []

    def fake_transcode(src_path, dst_path, *, max_side, crf, preset="medium"):
        calls.append(("transcode", src_path, dst_path, max_side, crf, preset))
        with open(dst_path, "wb") as fh:
            fh.write(b"small-mp4")

    def fake_poster(src_path, poster_path, *, max_side):
        calls.append(("poster", src_path, poster_path))
        with open(poster_path, "wb") as fh:
            fh.write(b"poster")
        return True

    monkeypatch.setattr(media_normalizer, "normalize_video_file",
                        fake_transcode)
    monkeypatch.setattr(media_normalizer, "extract_poster", fake_poster)
    fake = fake_db([{"id": 30, "storage_ref": "p9_v.mov"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and stats["failed"] == 0
    assert fake.repointed == {30: "p9_v.tg.mp4"}
    assert not src.exists()
    assert (media_dir / "p9_v.tg.mp4").exists()
    assert (media_dir / "p9_v.poster.webp").exists()
    assert calls[0][0] == "transcode" and calls[0][3] == \
        config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX
    # The poster is taken from the NORMALIZED file (the one that stays).
    assert calls[1][0] == "poster" and calls[1][1].endswith("p9_v.tg.mp4")


async def test_sweep_skips_normalized_video_but_backfills_poster(
        media_dir, fake_db, monkeypatch):
    _retention_cfg(monkeypatch)
    done = media_dir / "p9_done.tg.mp4"
    done.write_bytes(b"mp4")
    posters = []

    def fake_poster(src_path, poster_path, *, max_side):
        posters.append(poster_path)
        with open(poster_path, "wb") as fh:
            fh.write(b"poster")
        return True

    monkeypatch.setattr(media_normalizer, "extract_poster", fake_poster)
    fake = fake_db([{"id": 31, "storage_ref": "p9_done.tg.mp4"}])
    stats = await media_normalizer.normalize_product_photos(1)
    # No re-encode (normalized: 0), but the missing poster was backfilled.
    assert stats["normalized"] == 0 and stats["failed"] == 0
    assert not fake.repointed
    assert done.exists()
    assert posters and posters[0].endswith("p9_done.poster.webp")
    # Second sweep: poster exists now — nothing to do.
    posters.clear()
    await media_normalizer.normalize_product_photos(1)
    assert not posters


async def test_sweep_failed_video_isolated(media_dir, fake_db, monkeypatch):
    _retention_cfg(monkeypatch)
    (media_dir / "p9_bad.mov").write_bytes(b"junk")

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg failed: boom")

    monkeypatch.setattr(media_normalizer, "normalize_video_file", boom)
    fake = fake_db([{"id": 32, "storage_ref": "p9_bad.mov"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["failed"] == 1 and not fake.repointed
    assert (media_dir / "p9_bad.mov").exists()  # original never deleted


def test_video_slot_cap():
    import db as _db
    assert _db._video_slot_cap(6) == 2   # the default feed: 4 photos + 2 videos
    assert _db._video_slot_cap(5) == 2
    assert _db._video_slot_cap(4) == 2   # never below 2 while the list has room
    assert _db._video_slot_cap(3) == 1   # 2 photos + 1 video
    assert _db._video_slot_cap(2) == 1
    assert _db._video_slot_cap(1) == 0   # 1-slot list stays photo-first
    assert _db._video_slot_cap(9) == 3   # larger lists scale at ~a third
    assert _db._video_slot_cap(12) == 4
    assert _db._video_slot_cap(0) == 0


def test_candidate_list_default_is_six():
    import config as _config
    assert _config.RETENTION_CANDIDATE_LIST_SIZE == 6
    assert settings.retention()["candidate_list_size"] == 6
