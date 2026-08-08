"""Process memory + runtime introspection, stdlib only.

The service's memory envelope is a tuned, documented property (the Dockerfile
pins `MALLOC_ARENA_MAX` / `MALLOC_MMAP_THRESHOLD_`, the media normalizer decodes
at reduced scale), but until now nothing in the process could say what it was
actually holding. Railway's graph shows ONE number, only for the service you are
looking at, and only from the Railway dashboard — the worker has no admin
surface at all, and neither half could tell you whether a climb was Python
objects or allocator retention.

This module is the measurement. Two consumers:
  - `loops.log_flush_loop` logs `snapshot()` once a minute (`role=` included:
    both roles log under the same logger name into the same `app_logs` table),
    so the trend is readable from the admin System-logs view.
  - `GET /admin/diagnostics/memory` returns it on demand, plus the tracemalloc
    top-N when the process was booted with MEMORY_TRACEMALLOC=1.

Everything here must be CHEAP and must never raise: it runs on a loop that also
drains the log buffer, and on the worker's health thread. Notably absent:
`len(gc.get_objects())`, which builds a list of every tracked object — O(heap)
time plus a multi-MB temporary, i.e. measuring memory by allocating memory.
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import threading
from typing import Any, Optional

from app.core import config

log = logging.getLogger(__name__)

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

# Latch: /proc does not exist off Linux (a developer's macOS). Fail once and
# stop retrying rather than opening a missing path every minute forever.
_statm_available = True


def rss_bytes() -> Optional[int]:
    """CURRENT resident set size, or None where /proc is unavailable.

    /proc/self/statm over /proc/self/status: one line of integers instead of a
    ~55-line text scan, for the same number. Field 1 is resident PAGES.
    """
    global _statm_available
    if not _statm_available:
        return None
    try:
        with open("/proc/self/statm", "rb") as fh:
            return int(fh.read().split()[1]) * _PAGE_SIZE
    except (FileNotFoundError, NotADirectoryError):
        # Only THIS means "there is no /proc here" (macOS). A transient failure
        # — EMFILE under fd pressure, say — must not permanently blind the probe
        # for the life of the process, which is exactly the situation you would
        # want it working in.
        _statm_available = False
        return None
    except (OSError, ValueError, IndexError):
        return None


def _rss_anon_bytes() -> Optional[int]:
    """The ANONYMOUS (heap + stacks) part of RSS — `RssAnon` in /proc/self/status.

    Worth its own field: RSS climbing WITH this climbing is heap/allocator
    retention, the failure mode `MALLOC_ARENA_MAX=2` exists to prevent. RSS
    climbing while this stays flat is file-backed (mapped .so/.pyc) and
    harmless.

    Deliberately NOT statm field 5: that one is "data + stack" as a VIRTUAL
    size (it tracks VmData, measured), so it counts address space the process
    reserved but never touched — with glibc arenas that runs far ahead of what
    is actually resident, which is the exact number this field exists to give.
    Costs one ~55-line read; `RssAnon` needs Linux 4.5+ and simply degrades to
    None on an older kernel or off Linux.
    """
    if not _statm_available:  # same /proc availability question
        return None
    try:
        with open("/proc/self/status", "rb") as fh:
            for line in fh:
                if line.startswith(b"RssAnon:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def peak_rss_bytes() -> Optional[int]:
    """High-water mark RSS.

    `ru_maxrss` is a PEAK that never decreases, so it can never show a leak
    being fixed — it is here to catch a spike that has already come back down
    (an hourly media sweep), which the current number would miss between
    samples. Its unit is kilobytes on Linux and the BSDs but BYTES on macOS.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return None
    try:
        val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001 - a probe must never raise
        return None
    return int(val) if sys.platform == "darwin" else int(val) * 1024


# The bounded in-process caches worth watching. Every one of them has a prune
# rule; this is how you see a prune that stopped working. Read through
# sys.modules, never `import`: this runs in BOTH roles, and importing
# app.ai.openai_client (the openai SDK) or app.retention.retention into a worker
# that never touched them would inflate the very number being reported — the
# same trap loops.py records for `import app.main`.
_CACHES: tuple[tuple[str, str, str], ...] = (
    ("logbuf", "app.core.logcapture", "_pending"),
    ("ip_hits", "app.chat.antispam", "_ip_hits"),
    ("cooldown", "app.chat.antispam", "_last_message_at"),
    ("subs", "app.retention.retention", "_sub_cache"),
    ("oai_clients", "app.ai.openai_client", "_product_clients"),
    ("settings_products", "app.core.settings", "_product_cache"),
)


def cache_sizes() -> dict[str, int]:
    """`len()` of each already-loaded bounded cache. Never creates one."""
    out: dict[str, int] = {}
    for label, mod_name, attr in _CACHES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        try:
            # len() only — `_ip_hits` is a defaultdict, so any subscript here
            # would GROW the cache this is meant to watch.
            out[label] = len(getattr(mod, attr))
        except Exception:  # noqa: BLE001 - a probe must never raise
            continue
    return out


def _db_pool_stats() -> dict[str, Any]:
    """Pool occupancy, when a pool exists. Exhaustion is a memory-shaped
    symptom too: connections waiting on `DB_ACQUIRE_TIMEOUT_SEC` hold request
    state alive."""
    mod = sys.modules.get("app.core.db")
    pool = getattr(mod, "_pool", None) if mod is not None else None
    if pool is None:
        return {}
    out: dict[str, Any] = {}
    for key, meth in (("size", "get_size"), ("idle", "get_idle_size"),
                      ("max", "get_max_size")):
        fn = getattr(pool, meth, None)
        if fn is None:
            continue
        try:
            out[key] = int(fn())
        except Exception:  # noqa: BLE001 - fakes in tests, and a closing pool
            continue
    return out


def _mb(value: Optional[int]) -> Optional[int]:
    """Bytes -> whole MB. Ints, never floats: a `%.1f` against a None RSS raises
    INSIDE logging, which swallows the record entirely — the worst failure mode
    for a memory probe is silently not logging."""
    return None if value is None else round(value / 1048576)


def snapshot(*, in_event_loop: bool = True) -> dict[str, Any]:
    """One cheap reading of everything worth watching.

    `in_event_loop=False` for the worker's health thread: `asyncio.all_tasks()`
    raises RuntimeError with no running loop, and that thread runs off the loop
    on purpose.
    """
    counts = gc.get_count()
    out: dict[str, Any] = {
        "role": config.SERVICE_ROLE,
        "rss_mb": _mb(rss_bytes()),
        "peak_mb": _mb(peak_rss_bytes()),
        "anon_mb": _mb(_rss_anon_bytes()),
        # CPython's live pymalloc block count. The single most valuable field
        # next to RSS: it moves INDEPENDENTLY of it. Blocks rising with RSS =
        # Python objects are accumulating. RSS rising with blocks flat = the
        # allocator is holding freed buffers, which is what the Dockerfile's
        # MALLOC_* pair addresses.
        "blocks": sys.getallocatedblocks(),
        "gc_counts": list(counts),
        "threads": threading.active_count(),
        "caches": cache_sizes(),
    }
    try:
        stats = gc.get_stats()
        out["gc_gen2_collections"] = int(stats[2].get("collections", 0))
        out["gc_uncollectable"] = sum(int(s.get("uncollectable", 0))
                                      for s in stats)
    except Exception:  # noqa: BLE001 - a probe must never raise
        pass
    if in_event_loop:
        try:
            import asyncio
            out["tasks"] = len(asyncio.all_tasks())
        except RuntimeError:
            pass
    pool = _db_pool_stats()
    if pool:
        out["db_pool"] = pool
    return out


def log_line(extra: Optional[dict[str, Any]] = None) -> None:
    """Emit the one-line `process_memory` sample. Never raises.

    Every value goes through `%s`. The line is captured by logcapture like any
    application record and reaches `app_logs`; `role=` is what lets the admin
    System-logs view tell the web line from the worker line, since both
    processes log under the same logger name into the same table.
    """
    try:
        snap = snapshot()
        if extra:
            snap.update(extra)
        log.info(
            "process_memory role=%s rss_mb=%s peak_mb=%s anon_mb=%s blocks=%s "
            "gc=%s gen2=%s uncollectable=%s tasks=%s threads=%s db_pool=%s "
            "logs_per_sample=%s caches=%s",
            snap.get("role"), snap.get("rss_mb"), snap.get("peak_mb"),
            snap.get("anon_mb"), snap.get("blocks"), snap.get("gc_counts"),
            snap.get("gc_gen2_collections"), snap.get("gc_uncollectable"),
            snap.get("tasks"), snap.get("threads"), snap.get("db_pool"),
            snap.get("logs_per_sample"), snap.get("caches"))
    except Exception:  # noqa: BLE001 - a memory probe must never break its loop
        pass


def start_tracemalloc() -> bool:
    """Arm tracemalloc when the process was booted with MEMORY_TRACEMALLOC=1.

    Called first thing in both entry points, so it covers everything the process
    does at RUNTIME — which is what a climb-over-hours investigation needs.

    It cannot see IMPORT-time allocation: by the time either entry point's body
    runs, its own module imports (FastAPI, the openai SDK, Pillow) have already
    happened. If that is what you are chasing, use CPython's own
    `PYTHONTRACEMALLOC=3` env var instead — the interpreter arms tracing before
    the first import, and `tracemalloc_top` reports it the same way.
    """
    if not config.MEMORY_TRACEMALLOC:
        return False
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            tracemalloc.start(config.MEMORY_TRACEMALLOC_FRAMES)
        log.warning("tracemalloc_enabled frames=%s — diagnostic mode, expect "
                    "higher memory and slower allocation",
                    config.MEMORY_TRACEMALLOC_FRAMES)
        return True
    except Exception as exc:  # noqa: BLE001 - never block boot on a diagnostic
        log.warning("tracemalloc_start_failed error=%s: %s",
                    exc.__class__.__name__, exc)
        return False


def tracemalloc_top(limit: int = 10) -> dict[str, Any]:
    """Top allocation sites, or an explanation of why there are none.

    Returns `{"enabled": False, ...}` rather than an error when the process was
    not booted with tracing: "nothing here" and "you did not turn it on" are
    different answers, and the operator needs to be told which one they got.
    """
    try:
        import tracemalloc
    except ImportError:  # pragma: no cover
        return {"enabled": False, "reason": "tracemalloc unavailable"}
    if not tracemalloc.is_tracing():
        return {"enabled": False,
                "reason": "set MEMORY_TRACEMALLOC=1 and restart the service; "
                          "tracing must be armed at boot to see anything"}
    current, peak = tracemalloc.get_traced_memory()
    stats = tracemalloc.take_snapshot().statistics("lineno")[:max(limit, 1)]
    return {
        "enabled": True,
        "traced_mb": _mb(current),
        "traced_peak_mb": _mb(peak),
        "top": [{"where": str(s.traceback[0]), "size_kb": round(s.size / 1024),
                 "count": s.count} for s in stats],
    }
