"""Logging setup + the two infra loops that BOTH process roles run.

These live outside `app.main` on purpose. The web half and the worker half each
need the same two things — a settings/KB cache that re-pulls from the DB, and a
drain of the in-memory log buffer into `app_logs` — but the worker got them by
importing `app.main`, which builds the ENTIRE FastAPI application as a side
effect: every router, dependency and pydantic model of the admin, chat,
retention, orchestrator and quality surfaces the background process never
serves. Measured, that import cost the worker ~23MB of RSS for two coroutines.

Keeping them here means one copy of each loop (the drift the old comment in
worker.py worried about is still avoided) without the worker paying for an HTTP
app it does not serve.
"""
from __future__ import annotations

import asyncio
import logging

from app.core import config
from app.core import db
from app.core import logcapture
from app.core import meminfo
from app.core import settings

log = logging.getLogger(config.SERVICE_NAME)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def install_logging() -> None:
    """Set the process-wide log format and start capturing records in memory.

    Both entry points call this before anything else logs. The capture handler
    is what feeds the admin System-logs view — a process that skips it still
    logs to stdout but never appears in the panel — see logcapture.py for the
    root-logger/denylist rationale.
    """
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)
    logcapture.install()


_SETTINGS_REFRESH_SEC = 60


async def settings_refresh_loop() -> None:
    """Re-pull the settings caches from the DB every minute (multi-instance)."""
    while True:
        await asyncio.sleep(_SETTINGS_REFRESH_SEC)
        try:
            await settings.reload()
            # Drop the per-process KB caches on the same cadence: they are only
            # invalidated by writes on THIS instance, so without this a KB/topic/
            # variable edit on another instance stayed invisible here until
            # restart. Cheap — the next request re-fetches the small KB rows.
            db.clear_kb_caches()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a transient DB error must not kill the loop
            log.exception("settings_refresh_failed")


_LOG_FLUSH_SEC = 3
_LOG_KEEP_ROWS = 5000
_LOG_PRUNE_EVERY = 20  # prune once every N flushes (~1 min)
# The append-only retention_events log has no size cap; reap old rows on a
# coarse cadence (~hourly) from the same loop. The retention is SPLIT: the
# high-volume "state food" lane (spins, session pings) is the overwhelming
# majority of the rows and is worth nothing once the state resolver's windows
# have passed, so keeping it as long as the decision events is what makes the
# table grow without bound. Both spans are hot settings — an operator watching
# the disk fill should not need a redeploy to act.
_RETENTION_EVENTS_PRUNE_EVERY = 1200  # ~1h at _LOG_FLUSH_SEC


def _memory_sample_every() -> int:
    """Ticks between `process_memory` samples; 0 = the sample is off.

    Derived from MEMORY_LOG_INTERVAL_SEC rather than being its own tick
    constant, so the env var means seconds to the operator no matter what
    _LOG_FLUSH_SEC is. Floored at one tick: a cadence FASTER than the flush is
    not expressible here, and would flood the very buffer it is measuring.
    """
    interval = max(int(config.MEMORY_LOG_INTERVAL_SEC), 0)
    if not interval:
        return 0
    return max(round(interval / _LOG_FLUSH_SEC), 1)


async def log_flush_loop() -> None:
    """Drain captured log records into app_logs; periodically prune the table.

    Keeps the admin System-logs view fed without the logging hot path ever
    touching the DB (logcapture buffers in memory; this loop is the only writer).
    Also the home of the periodic `process_memory` sample: it is the one loop
    BOTH roles run that already carries tick arithmetic, and a memory line that
    lands in `app_logs` is the only way the worker's footprint is visible from
    the admin panel at all.
    """
    ticks = 0
    drained = 0
    while True:
        await asyncio.sleep(_LOG_FLUSH_SEC)
        # BEFORE the DB work, and outside the try below: a memory sample that
        # only happens after a successful INSERT goes silent during a database
        # outage — precisely when a process is most likely to be climbing (the
        # log buffer stops draining, every failing tick logs) and least likely
        # to be observable any other way. It reaches stdout/Railway regardless;
        # only its trip to app_logs depends on the DB.
        ticks += 1
        mem_every = _memory_sample_every()
        if mem_every and ticks % mem_every == 0:
            # Records drained since the last sample — NOT the buffer depth,
            # which drain() zeroes by construction. It is the honest "how loud
            # is this process" number, and a spike in it explains an app_logs
            # history that suddenly got shorter.
            meminfo.log_line({"logs_per_sample": drained})
            drained = 0
        try:
            items = logcapture.drain()
            # Counted at drain time, BEFORE the insert: drain() has already
            # consumed the records, so a failing insert (DB outage — the one
            # scenario the sample above is hoisted to survive) must not zero
            # the very "how loud is this process" number it reports.
            drained += len(items)
            if items:
                await db.insert_app_logs(items)
            if ticks % _LOG_PRUNE_EVERY == 0:
                await db.prune_app_logs(_LOG_KEEP_ROWS)
            if ticks % _RETENTION_EVENTS_PRUNE_EVERY == 0:
                removed = await db.prune_retention_events(
                    settings.global_retention_int(
                        "event_keep_days", config.RETENTION_EVENT_KEEP_DAYS,
                        1, 3650),
                    state_keep_days=settings.global_retention_int(
                        "event_keep_days_state",
                        config.RETENTION_EVENT_KEEP_DAYS_STATE, 1, 3650))
                if removed:
                    log.info("retention_events_pruned rows=%s", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let a DB hiccup kill the loop
            # One line, not log.exception: this loop's own records are captured
            # and re-flushed by itself, and a down DB would otherwise stack a
            # full traceback into the buffer every few seconds. But NOT silent —
            # a prune that times out and rolls back on every pass (the exact
            # C2 failure mode) must be visible in the logs.
            log.warning("log_flush_tick_failed error=%s: %s",
                        exc.__class__.__name__, exc)
