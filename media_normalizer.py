"""Media normalizer — periodic re-compression of retention photos for Telegram.

Content managers upload originals as they come (5 MB JPEGs at 8000x4000);
Telegram re-compresses every photo to ~2560px on the longest side anyway, so
storing the originals only burns Volume space and upload time on the first
send. This worker sweeps the library on a schedule (hourly by default) and
brings every photo to the delivery format: WebP, longest side capped at
`retention.media_max_side_px`. The heavy original is DELETED after the row is
re-pointed — the normalized file becomes the one stored binary.

Rules of the sweep (per product, inside its settings scope):
- .jpg/.jpeg/.png  -> always re-encoded to WebP (resized when oversized).
- .webp            -> re-encoded only when the longest side exceeds the cap.
- .gif             -> left alone (may be animated; re-encoding kills it).
- A missing file, an unreadable image or a failed write SKIPS that photo and
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
`media_max_side_px`, `media_webp_quality`. The admin Media tab's «Normalize
now» button (POST /admin/retention/photos/normalize) runs one product's sweep
on demand.
"""
from __future__ import annotations

import asyncio
import logging
import os
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
