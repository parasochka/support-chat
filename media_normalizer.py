"""Media normalizer — re-compression of retention photos + videos for Telegram.

Content managers upload originals as they come (5 MB JPEGs at 8000x4000,
phone videos at 4K); Telegram re-compresses photos to ~2560px anyway and a
bot upload is capped at 50 MB, so storing the originals only burns Volume
space and upload time on the first send. This module brings every stored
binary to the delivery format:
- photos -> WebP, longest side capped at `retention.media_max_side_px`;
- videos -> MP4 (H.264 + AAC, ffmpeg), longest side capped at
  `RETENTION_MEDIA_VIDEO_MAX_SIDE_PX` (1080p-class — a vertical phone reel
  keeps its native 1080x1920), CRF-encoded, plus a
  poster frame (`<base>.poster.webp`) used by the admin preview and the
  AI metadata generation.
The heavy original is DELETED after the row is re-pointed — the normalized
file becomes the one stored binary.

Runs on TWO triggers: immediately after an admin upload
(`schedule_product_normalization` — a background task the upload endpoint
fires, so new media is delivery-ready right away) and the periodic sweep
(hourly by default) as the catch-up for anything the instant run missed.
Both paths share the same advisory lock, so they never double-process a file.

Rules of the sweep (per product, inside its settings scope):
- .jpg/.jpeg/.png  -> always re-encoded to WebP (resized when oversized).
- .webp            -> re-encoded only when the longest side exceeds the cap.
- .gif             -> left alone (may be animated; re-encoding kills it).
- video extensions -> re-encoded to `<base>.tg.mp4` unless already carrying
  that suffix (the marker of a finished normalization). Encoding runs ONE
  file at a time, at low OS priority and with a small ffmpeg thread cap, so
  a bulk upload never starves the event loop or the chat turns.
- A missing file, an unreadable input or a failed write SKIPS that item and
  never kills the sweep; the DB row is re-pointed only AFTER the new file is
  fully written, and the original is deleted only AFTER the row points away
  from it — a crash mid-sweep can leave an extra file, never a broken photo.
- `telegram_file_id` is KEPT: it references the already-uploaded copy on
  Telegram's side, which stays valid; only future first-uploads use the new
  binary.

The loop runs from main.py lifespan under the same RETENTION_SCHEDULER_ENABLED
deploy switch as the agent worker, under its own advisory lock (multi-instance
safe). Knobs (hot `retention` group): `media_normalize_enabled` (per product),
`media_normalize_interval_sec` (global — one loop serves every product),
`media_max_side_px`, `media_webp_quality`; the video caps are deploy env
constants (`RETENTION_MEDIA_VIDEO_MAX_SIDE_PX` / `RETENTION_MEDIA_VIDEO_CRF`).
The admin Media tab's «Optimize» button (POST /admin/retention/photos/
normalize) runs one product's sweep on demand.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from typing import Any, Optional

import config
import db
import settings
import tenancy

log = logging.getLogger(__name__)

# Distinct from retention_v2's lock — the two sweeps may run concurrently.
_ADVISORY_LOCK_KEY = 0x52504E4D  # "RPNM" — retention photo normalizer

# Extensions the normalizer converts. GIFs are excluded on purpose (possibly
# animated — Pillow would flatten them to one frame).
_CONVERT_EXTS = (".jpg", ".jpeg", ".png")

# Video source extensions the upload endpoint accepts and the normalizer
# re-encodes. A finished normalization is marked by the _VIDEO_NORM_SUFFIX
# filename, so the sweep can tell "already ours" apart without probing.
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")
_VIDEO_NORM_SUFFIX = ".tg.mp4"
_POSTER_SUFFIX = ".poster.webp"

# Telegram's bot-upload hard cap; a normalized video still over it can never
# be delivered, so the sweep flags it loudly (it stays stored — the operator
# decides whether to trim/replace it).
_TG_VIDEO_MAX_BYTES = 50 * 1024 * 1024


def interval_sec() -> int:
    """The hot sweep cadence (`retention.media_normalize_interval_sec`).

    Global-layer read (one loop serves every product), clamped 300s..24h.
    """
    return settings.global_retention_int(
        "media_normalize_interval_sec",
        config.RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC, 300, 86_400)


def _probe(path: str) -> Optional[tuple[str, int, int]]:
    """(ext, width, height) of an image file, or None when unreadable.

    Opening reads only the header — cheap enough to run over the whole
    library every sweep.
    """
    from PIL import Image
    ext = os.path.splitext(path)[1].lower()
    try:
        with Image.open(path) as im:
            return ext, im.width, im.height
    except Exception:  # noqa: BLE001 - not an image / corrupt file
        return None


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def needs_normalization(path: str, max_side: int) -> bool:
    """True when the stored binary should be re-encoded for delivery."""
    probed = _probe(path)
    if probed is None:
        return False
    ext, w, h = probed
    if ext in _CONVERT_EXTS:
        return True
    if ext == ".webp":
        return max(w, h) > max_side
    return False  # .gif and anything exotic: leave alone


def normalize_file(src_path: str, dst_path: str, *, max_side: int,
                   quality: int) -> tuple[int, int]:
    """Re-encode one image to WebP at <= max_side px. Returns (width, height).

    EXIF orientation is baked in (Telegram ignores EXIF on re-compress);
    alpha survives (WebP supports it), everything else lands in RGB.
    Synchronous + CPU-bound — call via asyncio.to_thread.
    """
    from PIL import Image, ImageOps
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(dst_path, "WEBP", quality=quality, method=4)
        return im.size


def is_video_ref(ref: Optional[str]) -> bool:
    """Is this storage_ref a video file (by extension)?"""
    return bool(ref) and os.path.splitext(ref)[1].lower() in VIDEO_EXTS


def video_needs_normalization(ref: str) -> bool:
    """A video is normalized exactly once — the .tg.mp4 suffix is the marker."""
    return is_video_ref(ref) and not ref.lower().endswith(_VIDEO_NORM_SUFFIX)


def video_target_refs(ref: str) -> tuple[str, str]:
    """(normalized_ref, poster_ref) for a video storage_ref.

    'X.mov' -> ('X.tg.mp4', 'X.poster.webp'); an already-normalized
    'X.tg.mp4' keeps its name and maps to the same 'X.poster.webp'.
    """
    low = ref.lower()
    base = (ref[:-len(_VIDEO_NORM_SUFFIX)] if low.endswith(_VIDEO_NORM_SUFFIX)
            else os.path.splitext(ref)[0])
    return base + _VIDEO_NORM_SUFFIX, base + _POSTER_SUFFIX


def poster_ref_for(ref: Optional[str]) -> Optional[str]:
    """The poster-frame filename for a video storage_ref (None for photos)."""
    if not ref or not is_video_ref(ref):
        return None
    return video_target_refs(ref)[1]


def _ffmpeg_cmd(args: list[str]) -> list[str]:
    """An ffmpeg invocation at low OS priority (when `nice` exists) so a
    transcode never competes with the serving process for CPU."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "10", *cmd]
    return cmd


def _video_scale_filter(max_side: int) -> str:
    # min() keeps small inputs unscaled (no upscale); force_divisible_by=2 is
    # required by yuv420p H.264. Single quotes protect the commas from
    # ffmpeg's filter-graph parser.
    return (f"scale=w='min({max_side},iw)':h='min({max_side},ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2")


def normalize_video_file(src_path: str, dst_path: str, *, max_side: int,
                         crf: int, preset: str = "medium") -> None:
    """Re-encode one video to Telegram-friendly MP4 (H.264 + AAC, faststart).

    Synchronous + CPU-bound — call via asyncio.to_thread; the caller
    serializes encodes (one at a time). `-threads 2` bounds ffmpeg's CPU
    grab; raises on a non-zero exit (the ffmpeg stderr rides in the message).
    A slower `preset` yields more quality per CRF for a longer encode.
    """
    cmd = _ffmpeg_cmd([
        "-i", src_path,
        "-vf", _video_scale_filter(max_side),
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "96k",
        "-threads", "2",
        dst_path,
    ])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not os.path.exists(dst_path):
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[:500]}")


def extract_poster(src_path: str, poster_path: str, *, max_side: int) -> bool:
    """Write one representative frame of a video as a WebP poster.

    Tries the 1-second mark first (the first frame is often black/blurred),
    falling back to frame 0 for sub-second clips. Returns True when the
    poster file exists afterwards; a failure only costs the preview/AI
    metadata, never the video itself.
    """
    for seek in ("1", "0"):
        cmd = _ffmpeg_cmd([
            "-ss", seek, "-i", src_path, "-frames:v", "1",
            "-vf", _video_scale_filter(max_side), poster_path,
        ])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120)
        except Exception:  # noqa: BLE001 - poster is best-effort
            return False
        if proc.returncode == 0 and os.path.exists(poster_path) \
                and os.path.getsize(poster_path) > 0:
            return True
    return False


async def normalize_product_photos(product_id: int, *,
                                   force: bool = False) -> dict[str, Any]:
    """One product's sweep: convert every heavy photo, re-point rows, delete
    originals. Returns counters; one bad photo never kills the sweep.

    `force` (the admin «Normalize now» button) bypasses the per-product
    enabled switch — pressing the button IS the opt-in for that run.
    """
    tenancy.set_current_product(product_id)
    cfg = settings.retention()
    if not force and not cfg.get("media_normalize_enabled", True):
        return {"skipped": "media_normalize_disabled"}
    max_side = int(cfg.get("media_max_side_px")
                   or config.RETENTION_MEDIA_MAX_SIDE_PX)
    quality = int(cfg.get("media_webp_quality")
                  or config.RETENTION_MEDIA_WEBP_QUALITY)
    stats = {"checked": 0, "normalized": 0, "failed": 0,
             "bytes_saved": 0}
    for photo in await db.list_retention_photos(product_id):
        ref = photo.get("storage_ref")
        if not ref:
            continue
        # Bare filename by contract; never allow a path outside the media dir.
        safe = os.path.basename(ref)
        path = os.path.join(config.RETENTION_MEDIA_DIR, safe)
        if not os.path.exists(path):
            continue
        stats["checked"] += 1
        try:
            if is_video_ref(safe):
                new_ref, poster_ref = video_target_refs(safe)
                poster_path = os.path.join(config.RETENTION_MEDIA_DIR,
                                           poster_ref)
                if not video_needs_normalization(safe):
                    # Already normalized; backfill a missing poster only.
                    if not os.path.exists(poster_path):
                        await asyncio.to_thread(
                            extract_poster, path, poster_path,
                            max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX)
                    continue
                new_path = os.path.join(config.RETENTION_MEDIA_DIR, new_ref)
                old_size = os.path.getsize(path)
                # One encode at a time by construction: this loop is
                # sequential and every entry point holds the advisory lock.
                await asyncio.to_thread(
                    normalize_video_file, path, new_path,
                    max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX,
                    crf=config.RETENTION_MEDIA_VIDEO_CRF,
                    preset=config.RETENTION_MEDIA_VIDEO_PRESET)
                await asyncio.to_thread(
                    extract_poster, new_path, poster_path,
                    max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX)
                if os.path.getsize(new_path) > _TG_VIDEO_MAX_BYTES:
                    log.warning(
                        "media_normalize_video_over_tg_cap photo_id=%s ref=%s "
                        "bytes=%s - Telegram bots cannot upload files over "
                        "50 MB; trim or replace this video",
                        photo.get("id"), new_ref, os.path.getsize(new_path))
                if new_ref != safe:
                    await db.set_retention_photo_storage_ref(photo["id"],
                                                             new_ref)
                    _remove_quietly(path)
                stats["normalized"] += 1
                stats["bytes_saved"] += max(
                    0, old_size - os.path.getsize(new_path))
                continue
            if not await asyncio.to_thread(needs_normalization, path, max_side):
                continue
            new_ref = os.path.splitext(safe)[0] + ".webp"
            new_path = os.path.join(config.RETENTION_MEDIA_DIR, new_ref)
            old_size = os.path.getsize(path)
            await asyncio.to_thread(normalize_file, path, new_path,
                                    max_side=max_side, quality=quality)
            # Re-point the row FIRST, then delete the original — a failure in
            # between leaves an orphan file, never a photo without a binary.
            if new_ref != safe:
                await db.set_retention_photo_storage_ref(photo["id"], new_ref)
                _remove_quietly(path)
            stats["normalized"] += 1
            stats["bytes_saved"] += max(
                0, old_size - os.path.getsize(new_path))
        except Exception:  # noqa: BLE001 - one bad file must not kill the sweep
            stats["failed"] += 1
            log.exception("media_normalize_failed photo_id=%s ref=%s",
                          photo.get("id"), ref)
    if stats["normalized"] or stats["failed"]:
        log.info("media_normalize_done product=%s stats=%s", product_id, stats)
        await db.log_admin_event(None, "retention_media_normalized",
                                 {**stats, "max_side_px": max_side},
                                 product_id=product_id)
    return stats


# Products with an instant post-upload run already queued in THIS process —
# a second upload while one runs just rides the queued sweep (the sweep
# re-lists the library when it actually runs, so it picks up both batches).
_pending_products: set[int] = set()


async def _run_product_locked(product_id: int) -> dict[str, Any]:
    """One product's sweep under the shared advisory lock.

    The same lock the periodic sweep takes, so an instant post-upload run can
    never process a file concurrently with the hourly sweep (or another
    instance) — pg_advisory_lock WAITS here (unlike the sweep's try-lock):
    the upload already happened, the work must run, a short wait is fine.
    """
    pool = db.pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            return await normalize_product_photos(product_id, force=True)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)


def schedule_product_normalization(product_id: int) -> None:
    """Fire-and-forget: normalize a product's media RIGHT AFTER an upload.

    Called by the upload endpoint so photos are WebP'd and videos are
    MP4-transcoded (+ poster) within moments of landing, instead of waiting
    for the hourly sweep (which stays as the catch-up). Deduped per product
    per process; any failure is logged and left for the periodic sweep.
    """
    if product_id in _pending_products:
        return
    _pending_products.add(product_id)

    async def _run() -> None:
        try:
            await _run_product_locked(product_id)
        except Exception:  # noqa: BLE001 - the hourly sweep is the backstop
            log.exception("media_normalize_post_upload_failed product=%s",
                          product_id)
        finally:
            _pending_products.discard(product_id)

    asyncio.get_running_loop().create_task(_run())


async def run_normalization() -> dict[str, Any]:
    """One sweep across all products (advisory-locked, multi-instance safe)."""
    pool = db.pool()
    async with pool.acquire() as conn:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                  _ADVISORY_LOCK_KEY)
        if not got:
            return {"skipped": "another instance holds the lock"}
        try:
            totals = {"products": 0, "checked": 0, "normalized": 0,
                      "failed": 0, "bytes_saved": 0}
            for product in await db.list_products():
                stats = await normalize_product_photos(int(product["id"]))
                if stats.get("skipped"):
                    continue
                totals["products"] += 1
                for k in ("checked", "normalized", "failed", "bytes_saved"):
                    totals[k] += stats.get(k, 0)
            return totals
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)


async def scheduler_loop() -> None:
    """Sweep the media library on the hot-reloaded cadence (hourly default)."""
    log.info("media_normalizer_started interval_sec=%s", interval_sec())
    while True:
        await asyncio.sleep(interval_sec())
        try:
            totals = await run_normalization()
            if totals.get("normalized") or totals.get("failed"):
                log.info("media_normalize_sweep_done totals=%s", totals)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            log.exception("media_normalize_sweep_failed")
