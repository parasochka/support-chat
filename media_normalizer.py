"""Media normalizer — re-compression of retention photos + videos for Telegram.

Content managers upload originals as they come (5 MB JPEGs at 8000x4000,
phone videos at 4K); Telegram re-compresses photos to ~2560px anyway and a
bot upload is capped at 50 MB, so storing the originals only burns Volume
space and upload time on the first send. This module brings every stored
binary to the delivery format:
- photos -> WebP, longest side capped at `RETENTION_MEDIA_MAX_SIDE_PX`;
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
- `telegram_file_id` is KEPT for photos (the already-uploaded copy stays
  valid; Telegram re-compresses photos anyway) but CLEARED for videos on
  every re-point/repair: a video file_id pins the exact binary Telegram
  holds, so keeping it would serve the pre-normalization copy forever.
- Videos are probed (ffprobe) after each encode: width/height/duration land
  on the row (`tg_width`/`tg_height`/`tg_duration_sec`) and ride the
  sendVideo call — without explicit attrs Telegram may fail to detect them
  and deliver the message as a download-first file with 00:00 duration.
  The encoder scales in DISPLAY terms and forces square pixels (setsar=1);
  an already-normalized .tg.mp4 that still carries a non-square SAR (the
  pre-fix output — rendered squished by Telegram) is re-encoded in place by
  the sweep (self-heal), with a poster + attrs refresh.

The loop runs from main.py lifespan under the same RETENTION_SCHEDULER_ENABLED
deploy switch as the agent worker, under its own advisory lock (multi-instance
safe). Normalization is ALWAYS ON and fully code-owned — there is deliberately
NO admin knob and NO enabled switch. Every parameter is a deploy-level constant
in config.py: `RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC`, the photo target
(`RETENTION_MEDIA_MAX_SIDE_PX` / `RETENTION_MEDIA_WEBP_QUALITY`) and the video
target (`RETENTION_MEDIA_VIDEO_MAX_SIDE_PX` / `RETENTION_MEDIA_VIDEO_CRF` /
`RETENTION_MEDIA_VIDEO_PRESET`). POST /admin/retention/photos/normalize runs one
product's sweep on demand (API-only, no UI button).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from typing import Any, Optional

import config
import db
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
# be delivered, so the sweep flags it loudly, the candidate feed drops it
# (retention._sendable_media) and a direct send falls back to the caption as
# text (it stays stored — the operator decides whether to trim/replace it).
TG_VIDEO_MAX_BYTES = 50 * 1024 * 1024


def interval_sec() -> int:
    """The sweep cadence — a code-owned deploy constant, clamped 300s..24h.

    Not an admin setting: normalization is always-on and fully code-owned
    (`config.RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC`), so there is no per-product
    or hot override to resolve.
    """
    return max(300, min(86_400, config.RETENTION_MEDIA_NORMALIZE_INTERVAL_SEC))


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
    # Scale in DISPLAY terms (iw*sar, not raw storage width) and force square
    # pixels on the output (setsar=1). An anamorphic source (SAR != 1) would
    # otherwise pass its SAR through the encode: browsers honor it (the admin
    # preview looked fine) but Telegram renders raw storage pixels, so the
    # player saw a horizontally squished video. min(1, ...) keeps small inputs
    # unscaled (no upscale); trunc(x/2)*2 keeps dimensions even for yuv420p
    # H.264. Single quotes protect the commas from ffmpeg's filter-graph
    # parser.
    f = f"min(1,min({max_side}/(iw*sar),{max_side}/ih))"
    return (f"scale=w='trunc({f}*iw*sar/2)*2':h='trunc({f}*ih/2)*2',"
            "setsar=1")


def probe_video_meta(path: str) -> Optional[dict[str, Any]]:
    """ffprobe one video: {width, height, duration_sec, square_pixels}.

    width/height are the STORAGE dimensions; `square_pixels` is False when the
    stream carries a non-square sample aspect ratio (an anamorphic file — the
    pre-fix normalizer let those through, and Telegram renders them squished).
    Returns None when ffprobe fails — callers treat that as "nothing to say"
    (no attrs stored, no repair), never as an error.
    """
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries",
           "stream=width,height,sample_aspect_ratio:format=duration",
           "-of", "json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return None
        parsed = json.loads(proc.stdout)
        stream = (parsed.get("streams") or [{}])[0]
        width, height = stream.get("width"), stream.get("height")
        if not width or not height:
            return None
        duration = float((parsed.get("format") or {}).get("duration") or 0)
        sar = (stream.get("sample_aspect_ratio") or "1:1").replace("/", ":")
        square = sar in ("", "N/A", "0:1", "1:1")
        return {"width": int(width), "height": int(height),
                "duration_sec": max(1, round(duration)) if duration else None,
                "square_pixels": square}
    except Exception:  # noqa: BLE001 - probing is best-effort
        return None


def _meta_attrs(meta: Optional[dict[str, Any]]) -> dict[str, Any]:
    """probe_video_meta result -> db attr kwargs (all None when unprobed)."""
    meta = meta or {}
    return {"width": meta.get("width"), "height": meta.get("height"),
            "duration_sec": meta.get("duration_sec")}


def make_video_thumbnail(poster_bytes: bytes) -> Optional[bytes]:
    """A Telegram-conformant video thumbnail from the poster frame.

    Bot API requires JPEG, <=320px, <200 kB — the stored poster is a WebP at
    delivery resolution, so it is downscaled/re-encoded here (at send time,
    first upload only; file_id sends carry the thumbnail already). None on any
    failure — the thumbnail is cosmetic, never worth failing a send over.
    """
    import io
    from PIL import Image
    try:
        with Image.open(io.BytesIO(poster_bytes)) as im:
            im = im.convert("RGB")
            im.thumbnail((320, 320), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - cosmetic only
        return None


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


async def normalize_product_photos(product_id: int) -> dict[str, Any]:
    """One product's sweep: convert every heavy photo, re-point rows, delete
    originals. Returns counters; one bad photo never kills the sweep.

    Normalization is unconditional — there is no enabled switch; the size /
    quality targets are code-owned deploy constants (`config.RETENTION_MEDIA_*`),
    not admin settings.
    """
    tenancy.set_current_product(product_id)
    max_side = int(config.RETENTION_MEDIA_MAX_SIDE_PX)
    quality = int(config.RETENTION_MEDIA_WEBP_QUALITY)
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
                    meta = await asyncio.to_thread(probe_video_meta, path)
                    if meta and not meta["square_pixels"]:
                        # Self-heal: a .tg.mp4 produced by the pre-fix encoder
                        # kept the source's non-square SAR (Telegram rendered
                        # it squished). Re-encode in place to square pixels,
                        # refresh the poster and DROP the cached file_id —
                        # Telegram's copy is the squished one.
                        tmp_path = path + ".fix.mp4"
                        await asyncio.to_thread(
                            normalize_video_file, path, tmp_path,
                            max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX,
                            crf=config.RETENTION_MEDIA_VIDEO_CRF,
                            preset=config.RETENTION_MEDIA_VIDEO_PRESET)
                        await asyncio.to_thread(os.replace, tmp_path, path)
                        await asyncio.to_thread(
                            extract_poster, path, poster_path,
                            max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX)
                        meta = await asyncio.to_thread(probe_video_meta, path)
                        await db.set_retention_video_meta(
                            photo["id"], **_meta_attrs(meta),
                            clear_file_id=True)
                        log.info("media_normalize_video_sar_repaired "
                                 "photo_id=%s ref=%s", photo.get("id"), safe)
                        stats["normalized"] += 1
                        continue
                    # Already fine; backfill a missing poster / missing attrs.
                    if not os.path.exists(poster_path):
                        await asyncio.to_thread(
                            extract_poster, path, poster_path,
                            max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX)
                    if meta and not (photo.get("tg_width")
                                     and photo.get("tg_height")):
                        await db.set_retention_video_meta(
                            photo["id"], **_meta_attrs(meta))
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
                if os.path.getsize(new_path) > TG_VIDEO_MAX_BYTES:
                    log.warning(
                        "media_normalize_video_over_tg_cap photo_id=%s ref=%s "
                        "bytes=%s - Telegram bots cannot upload files over "
                        "50 MB; trim or replace this video",
                        photo.get("id"), new_ref, os.path.getsize(new_path))
                if new_ref != safe:
                    meta = await asyncio.to_thread(probe_video_meta, new_path)
                    # Re-point + store the sendVideo attrs + clear any cached
                    # file_id in one write: the new binary is not the copy
                    # Telegram may hold, so the next send must re-upload.
                    await db.set_retention_video_normalized(
                        photo["id"], storage_ref=new_ref, **_meta_attrs(meta))
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


# Products with an instant post-upload run queued or in flight in THIS
# process; _rerun_products marks the ones that got ANOTHER upload while their
# run was already going — the run re-lists the library once more when it
# finishes, because a pass that already called list_retention_photos cannot
# see rows created after it.
_pending_products: set[int] = set()
_rerun_products: set[int] = set()
# Strong refs to the fire-and-forget tasks: the event loop keeps only WEAK
# references, so an unreferenced task can be garbage-collected mid-run (the
# documented asyncio.create_task gotcha).
_bg_tasks: set[asyncio.Task] = set()


async def _run_product_locked(product_id: int) -> dict[str, Any]:
    """One product's sweep under the shared advisory lock.

    The same lock the periodic sweep takes, so an instant post-upload run can
    never process a file concurrently with the hourly sweep (or another
    instance) — pg_advisory_lock WAITS here (unlike the sweep's try-lock):
    the upload already happened, the work must run, a short wait is fine.
    The lock rides a DEDICATED connection (db.dedicated_connection), not a
    pool slot: video encodes hold it for minutes, and the pool's
    command_timeout would also kill a blocking pg_advisory_lock wait.
    """
    conn = await db.dedicated_connection()
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            return await normalize_product_photos(product_id)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)
    finally:
        # Closing the session also releases the advisory lock, so a failed
        # explicit unlock can never wedge the sweep for other instances.
        await conn.close()


def schedule_product_normalization(product_id: int) -> None:
    """Fire-and-forget: normalize a product's media RIGHT AFTER an upload.

    Called by the upload endpoint so photos are WebP'd and videos are
    MP4-transcoded (+ poster) within moments of landing, instead of waiting
    for the hourly sweep (which stays as the catch-up). Deduped per product
    per process — a batch landing while a run is in flight marks a re-run, so
    it is still picked up immediately after the current pass; any failure is
    logged and left for the periodic sweep.
    """
    if product_id in _pending_products:
        _rerun_products.add(product_id)
        return
    _pending_products.add(product_id)

    async def _run() -> None:
        try:
            while True:
                _rerun_products.discard(product_id)
                await _run_product_locked(product_id)
                if product_id not in _rerun_products:
                    break
        except Exception:  # noqa: BLE001 - the hourly sweep is the backstop
            log.exception("media_normalize_post_upload_failed product=%s",
                          product_id)
        finally:
            _pending_products.discard(product_id)
            _rerun_products.discard(product_id)

    task = asyncio.get_running_loop().create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def run_normalization() -> dict[str, Any]:
    """One sweep across all products (advisory-locked, multi-instance safe).

    The lock (and only the lock) rides a dedicated connection — a sweep with
    video encodes can run for many minutes, and parking a pool slot for the
    whole run would starve the 10-connection request pool.
    """
    conn = await db.dedicated_connection()
    try:
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
    finally:
        await conn.close()


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
