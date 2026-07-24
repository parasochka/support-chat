"""Media normalizer: WebP re-encode of heavy retention photos for Telegram.

Covers the decision function, the re-encode itself (resize + format), the
per-product sweep (row re-point + original delete + one-bad-file isolation)
and the code-owned (no-admin-knob) sweep cadence / always-on behaviour.
"""
from __future__ import annotations

import asyncio
import os
import time

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
        self.video_meta = {}   # photo_id -> (w, h, dur, "cleared"/"kept")
        self.file_id_cleared = []  # ids passed to clear_photo_file_id
        self.events = []

    async def list_retention_photos(self, product_id, **kw):
        return self.photos

    async def set_retention_photo_storage_ref(self, photo_id, ref):
        self.repointed[photo_id] = ref

    async def set_retention_video_normalized(self, photo_id, *, storage_ref,
                                             width, height, duration_sec):
        self.repointed[photo_id] = storage_ref
        self.video_meta[photo_id] = (width, height, duration_sec, "cleared")

    async def set_retention_video_meta(self, photo_id, *, width, height,
                                       duration_sec, clear_file_id=False):
        self.video_meta[photo_id] = (
            width, height, duration_sec,
            "cleared" if clear_file_id else "kept")

    async def clear_photo_file_id(self, photo_id):
        self.file_id_cleared.append(photo_id)

    async def log_admin_event(self, session_id, kind, payload, product_id=None):
        self.events.append((kind, payload, product_id))


@pytest.fixture
def fake_db(monkeypatch):
    def _install(photos):
        fake = _FakeDb(photos)
        for name in ("list_retention_photos", "set_retention_photo_storage_ref",
                     "set_retention_video_normalized",
                     "set_retention_video_meta", "clear_photo_file_id",
                     "log_admin_event"):
            monkeypatch.setattr(media_normalizer.db, name, getattr(fake, name))
        return fake
    return _install


async def test_sweep_converts_repoints_and_deletes(media_dir, fake_db,
                                                   monkeypatch):
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


async def test_sweep_runs_unconditionally(media_dir, fake_db, monkeypatch):
    """Normalization is always-on and code-owned — there is no enabled switch
    to skip it and no force flag to bypass one."""
    jpg = media_dir / "p3.jpg"
    _make_image(str(jpg), (3000, 1500), "JPEG")
    fake = fake_db([{"id": 20, "storage_ref": "p3.jpg"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and fake.repointed == {20: "p3.webp"}
    assert not jpg.exists()


def test_media_knobs_are_not_admin_settings():
    """The media-normalizer knobs left the retention settings group — values
    that used to be rejected there are now inert (normalization is code-owned,
    not admin-tunable)."""
    for was_bad in ({"media_normalize_interval_sec": 10},
                    {"media_max_side_px": 100},
                    {"media_webp_quality": 10},
                    {"media_normalize_enabled": "yes"}):
        # No longer a settings key -> no validation error.
        settings.validate_setting("retention", was_bad)


def test_interval_is_code_owned_and_clamped(monkeypatch):
    monkeypatch.setattr(config, "RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC", 60)
    assert media_normalizer.interval_sec() == 300  # clamped low
    monkeypatch.setattr(config, "RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC", 7200)
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


def test_video_scale_filter_display_terms_square_pixels():
    """The scale runs in DISPLAY terms (iw*sar) and forces square pixels —
    an anamorphic source would otherwise render squished in Telegram (which
    ignores SAR) while looking fine in a browser (which honors it)."""
    f = media_normalizer._video_scale_filter(1280)
    assert "min(1,min(1280/(iw*sar),1280/ih))" in f  # no upscale, display-term
    assert f.endswith("setsar=1")
    assert "/2)*2" in f  # even dimensions for yuv420p H.264


async def test_sweep_transcodes_video_and_extracts_poster(media_dir, fake_db,
                                                          monkeypatch):
    """A video upload is re-encoded to .tg.mp4 + poster, re-pointed, original
    deleted — with ffmpeg stubbed out (not installed in CI)."""
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
    monkeypatch.setattr(media_normalizer, "probe_video_meta",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration_sec": 6, "square_pixels": True})
    fake = fake_db([{"id": 30, "storage_ref": "p9_v.mov"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and stats["failed"] == 0
    assert fake.repointed == {30: "p9_v.tg.mp4"}
    # The probed sendVideo attrs land on the row and the cached file_id is
    # dropped (the new binary is not the copy Telegram may hold).
    assert fake.video_meta == {30: (1080, 1920, 6, "cleared")}
    assert not src.exists()
    assert (media_dir / "p9_v.tg.mp4").exists()
    assert (media_dir / "p9_v.poster.webp").exists()
    assert calls[0][0] == "transcode" and calls[0][3] == \
        config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX
    # The poster is taken from the NORMALIZED file (the one that stays).
    assert calls[1][0] == "poster" and calls[1][1].endswith("p9_v.tg.mp4")


async def test_sweep_skips_normalized_video_but_backfills_poster(
        media_dir, fake_db, monkeypatch):
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


async def test_sweep_repairs_anamorphic_normalized_video(media_dir, fake_db,
                                                         monkeypatch):
    """Self-heal: a .tg.mp4 from the pre-fix encoder carries the source's
    non-square SAR (Telegram rendered it squished). The sweep re-encodes it in
    place, refreshes the poster/attrs and DROPS the cached file_id — Telegram's
    copy is the squished one."""
    done = media_dir / "p9_sar.tg.mp4"
    done.write_bytes(b"anamorphic-mp4")
    (media_dir / "p9_sar.poster.webp").write_bytes(b"poster")
    probes = []

    def fake_probe(path):
        probes.append(path)
        if len(probes) == 1:  # pre-repair: anamorphic
            return {"width": 1080, "height": 1920, "duration_sec": 6,
                    "square_pixels": False}
        return {"width": 1440, "height": 1920, "duration_sec": 6,
                "square_pixels": True}

    def fake_transcode(src, dst, **kw):
        with open(dst, "wb") as fh:
            fh.write(b"fixed-mp4")

    def fake_poster(src, poster_path, **kw):
        with open(poster_path, "wb") as fh:
            fh.write(b"poster2")
        return True

    monkeypatch.setattr(media_normalizer, "probe_video_meta", fake_probe)
    monkeypatch.setattr(media_normalizer, "normalize_video_file",
                        fake_transcode)
    monkeypatch.setattr(media_normalizer, "extract_poster", fake_poster)
    fake = fake_db([{"id": 40, "storage_ref": "p9_sar.tg.mp4",
                     "telegram_file_id": "old-id"}])
    # The file_id must be dropped BEFORE the on-disk swap: a crash between the
    # two leaves the squished file with no pin, so the repair converges on the
    # next sweep instead of leaving Telegram's broken copy cached forever.
    cleared_at = []

    async def _clear(photo_id):
        cleared_at.append((photo_id, done.read_bytes()))
    monkeypatch.setattr(media_normalizer.db, "clear_photo_file_id", _clear)
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 1 and stats["failed"] == 0
    assert done.read_bytes() == b"fixed-mp4"   # re-encoded in place
    assert not fake.repointed                  # the ref stays the same
    assert fake.video_meta == {40: (1440, 1920, 6, "cleared")}
    assert cleared_at == [(40, b"anamorphic-mp4")]  # cleared pre-swap
    assert (media_dir / "p9_sar.poster.webp").read_bytes() == b"poster2"


async def test_sweep_backfills_video_attrs(media_dir, fake_db, monkeypatch):
    """A video normalized before the attrs shipped gets its sendVideo attrs
    probed and stored, and the cached file_id is CLEARED: the pre-attrs upload
    may be pinned in the broken download-first/00:00 presentation, and a
    file_id send cannot attach attrs — one re-upload with explicit attrs is
    the fix. Once the row carries attrs, the sweep leaves it alone."""
    done = media_dir / "p9_meta.tg.mp4"
    done.write_bytes(b"mp4")
    (media_dir / "p9_meta.poster.webp").write_bytes(b"poster")
    monkeypatch.setattr(media_normalizer, "probe_video_meta",
                        lambda p: {"width": 1080, "height": 1920,
                                   "duration_sec": 8, "square_pixels": True})
    fake = fake_db([{"id": 41, "storage_ref": "p9_meta.tg.mp4"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["normalized"] == 0 and stats["failed"] == 0
    assert fake.video_meta == {41: (1080, 1920, 8, "cleared")}
    # Attrs present -> the next sweep is a no-op.
    fake.photos[0].update(tg_width=1080, tg_height=1920)
    fake.video_meta.clear()
    await media_normalizer.normalize_product_photos(1)
    assert not fake.video_meta


async def test_sweep_failed_video_isolated(media_dir, fake_db, monkeypatch):
    (media_dir / "p9_bad.mov").write_bytes(b"junk")

    def boom(*a, **kw):
        raise RuntimeError("ffmpeg failed: boom")

    monkeypatch.setattr(media_normalizer, "normalize_video_file", boom)
    fake = fake_db([{"id": 32, "storage_ref": "p9_bad.mov"}])
    stats = await media_normalizer.normalize_product_photos(1)
    assert stats["failed"] == 1 and not fake.repointed
    assert (media_dir / "p9_bad.mov").exists()  # original never deleted


async def test_orphan_cleanup(media_dir, fake_db, monkeypatch):
    """Files no DB row references (crash/race leftovers — a surviving raw
    original, a deleted row's encode output, a failed repair's tmp) are swept
    once older than a day; referenced files and young files stay."""
    fake_db([{"id": 1, "storage_ref": "p1_v.tg.mp4"}])

    async def _products():
        return [{"id": 1}]
    monkeypatch.setattr(media_normalizer.db, "list_products", _products)

    old_ts = time.time() - 2 * 86_400
    orphan = media_dir / "p1_old.mov"          # crash leftover -> removed
    orphan.write_bytes(b"x")
    os.utime(orphan, (old_ts, old_ts))
    young = media_dir / "p1_new.fix.mp4"       # in-flight-aged -> kept
    young.write_bytes(b"x")
    kept = media_dir / "p1_v.tg.mp4"           # referenced -> kept
    kept.write_bytes(b"x")
    os.utime(kept, (old_ts, old_ts))
    poster = media_dir / "p1_v.poster.webp"    # implied by the video row
    poster.write_bytes(b"x")
    os.utime(poster, (old_ts, old_ts))

    removed = await media_normalizer._cleanup_orphans()
    assert removed == 1
    assert not orphan.exists()
    assert young.exists() and kept.exists() and poster.exists()


async def test_sweep_locks_per_product(monkeypatch):
    """The periodic sweep takes the (key, product_id) advisory lock per
    product and SKIPS a product whose lock is busy (an instant post-upload run
    in flight) instead of blocking or double-processing."""
    class FakeConn:
        def __init__(self):
            self.unlocked = []

        async def fetchval(self, sql, key, pid):
            assert "pg_try_advisory_lock($1, $2)" in sql
            return pid != 2  # product 2 is busy elsewhere

        async def execute(self, sql, key, pid):
            assert "pg_advisory_unlock($1, $2)" in sql
            self.unlocked.append(pid)

        async def close(self):
            pass

    conn = FakeConn()

    async def _conn():
        return conn
    monkeypatch.setattr(media_normalizer.db, "dedicated_connection", _conn)

    async def _products():
        return [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(media_normalizer.db, "list_products", _products)

    ran = []

    async def _norm(pid):
        ran.append(pid)
        return {"checked": 1, "normalized": 0, "failed": 0, "bytes_saved": 0}
    monkeypatch.setattr(media_normalizer, "normalize_product_photos", _norm)

    async def _orphans():
        return 0
    monkeypatch.setattr(media_normalizer, "_cleanup_orphans", _orphans)

    totals = await media_normalizer.run_normalization()
    assert ran == [1] and conn.unlocked == [1]  # busy product skipped
    assert totals["products"] == 1


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


async def test_post_upload_schedule_holds_ref_and_reruns(monkeypatch):
    """The fire-and-forget post-upload run keeps a strong task reference (the
    documented create_task GC gotcha), and a batch landing MID-RUN marks a
    re-run — a pass that already listed the library can't see rows created
    after it, so without the re-run that batch waited for the hourly sweep."""
    runs = []
    gate = asyncio.Event()

    async def fake_locked(pid):
        runs.append(pid)
        await gate.wait()
        return {}

    monkeypatch.setattr(media_normalizer, "_run_product_locked", fake_locked)
    media_normalizer.schedule_product_normalization(7)
    await asyncio.sleep(0)  # let the task start: the first run is now in flight
    assert media_normalizer._bg_tasks  # strong ref held while running
    assert 7 in media_normalizer._pending_products
    media_normalizer.schedule_product_normalization(7)  # an upload mid-run
    gate.set()
    for _ in range(50):
        if not media_normalizer._pending_products:
            break
        await asyncio.sleep(0)
    assert runs == [7, 7]  # the mid-run batch got its own pass
    assert not media_normalizer._pending_products
    # The done-callback runs one loop tick after the task finishes.
    for _ in range(10):
        if not media_normalizer._bg_tasks:
            break
        await asyncio.sleep(0)
    assert not media_normalizer._bg_tasks  # done-callback dropped the ref
