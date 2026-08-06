"""app/core/meminfo.py — the memory probe.

A probe that raises, that costs what it measures, or that silently emits
nothing is worse than no probe at all, so that is what these pin down.
"""
from __future__ import annotations

import logging
import sys

from app.core import config
from app.core import meminfo


def test_snapshot_has_the_fields_the_line_prints():
    snap = meminfo.snapshot()
    for key in ("role", "rss_mb", "peak_mb", "anon_mb", "blocks", "gc_counts",
                "threads", "caches"):
        assert key in snap, key
    # blocks is CPython's live pymalloc block count — the field that separates
    # "Python objects are piling up" from "the allocator is holding buffers".
    assert isinstance(snap["blocks"], int) and snap["blocks"] > 0


def test_snapshot_is_json_safe():
    """It is returned straight from a JSONResponse handler, where a stray
    datetime or a set 500s the route (the kb_variables `updated_at` bug)."""
    import json
    json.dumps(meminfo.snapshot())


def test_rss_degrades_to_none_without_proc(monkeypatch):
    """Local dev can be macOS, where /proc does not exist. The line must still
    be emitted with rss_mb=None rather than not emitted at all."""
    monkeypatch.setattr(meminfo, "_statm_available", True)

    def _missing(*a, **k):
        raise FileNotFoundError("no /proc here")

    monkeypatch.setattr("builtins.open", _missing)
    assert meminfo.rss_bytes() is None
    # Latched off, so it does not retry a missing path every minute forever.
    assert meminfo._statm_available is False


def test_a_transient_read_error_does_not_blind_the_probe(monkeypatch):
    """Only "there is no /proc" latches. An EMFILE under fd pressure must not
    silently kill the probe for the life of the process — that is exactly when
    you want it working."""
    monkeypatch.setattr(meminfo, "_statm_available", True)

    def _transient(*a, **k):
        raise OSError(24, "Too many open files")

    monkeypatch.setattr("builtins.open", _transient)
    assert meminfo.rss_bytes() is None
    assert meminfo._statm_available is True
    monkeypatch.undo()
    assert meminfo.rss_bytes() is not None  # recovers on the next sample


def test_peak_uses_the_right_unit_per_platform(monkeypatch):
    """ru_maxrss is kilobytes on Linux but BYTES on macOS — a silent 1024x lie
    on a developer's laptop."""
    import resource

    class _RU:
        ru_maxrss = 1000

    monkeypatch.setattr(resource, "getrusage", lambda who: _RU())
    monkeypatch.setattr(sys, "platform", "linux")
    assert meminfo.peak_rss_bytes() == 1000 * 1024
    monkeypatch.setattr(sys, "platform", "darwin")
    assert meminfo.peak_rss_bytes() == 1000


def test_cache_sizes_never_import_a_module():
    """Reading a cache must not DRAG IN the module that owns it: pulling
    app.ai.openai_client (the openai SDK) into a worker that never used it would
    inflate the very number being reported."""
    sys.modules.pop("app.retention.retention", None)
    before = set(sys.modules)
    meminfo.cache_sizes()
    assert set(sys.modules) - before == set()


def test_cache_sizes_do_not_grow_a_defaultdict():
    """antispam._ip_hits is a defaultdict — a subscript in the probe would
    CREATE the entry it is supposed to be counting."""
    from app.chat import antispam
    antispam.reset_state()
    meminfo.cache_sizes()
    meminfo.cache_sizes()
    assert len(antispam._ip_hits) == 0


def test_log_line_emits_one_record(caplog):
    with caplog.at_level(logging.INFO, logger="app.core.meminfo"):
        meminfo.log_line({"logs_per_sample": 7})
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("process_memory")]
    assert len(lines) == 1
    # role= is what tells the web line from the worker line: both roles log
    # under the same logger name into the one shared app_logs table.
    assert "role=" in lines[0] and "logs_per_sample=7" in lines[0]


def test_log_line_survives_a_none_rss(monkeypatch, caplog):
    """The trap: a %.1f against None raises INSIDE logging, which swallows the
    record entirely — a memory probe that silently stops logging."""
    monkeypatch.setattr(meminfo, "rss_bytes", lambda: None)
    monkeypatch.setattr(meminfo, "peak_rss_bytes", lambda: None)
    with caplog.at_level(logging.INFO, logger="app.core.meminfo"):
        meminfo.log_line()
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("process_memory")]
    assert len(lines) == 1 and "rss_mb=None" in lines[0]


def test_log_line_never_raises(monkeypatch):
    """It runs inside log_flush_loop's shared try; an escape would become a
    `log_flush_tick_failed` line every 3 seconds, into the buffer it measures."""
    monkeypatch.setattr(meminfo, "snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    meminfo.log_line()  # must not raise


def test_tracemalloc_off_says_so_rather_than_looking_empty():
    out = meminfo.tracemalloc_top(5)
    if not out.get("enabled"):
        assert "MEMORY_TRACEMALLOC" in out.get("reason", "")


def test_tracemalloc_start_is_a_no_op_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_TRACEMALLOC", False)
    assert meminfo.start_tracemalloc() is False


def test_snapshot_off_the_event_loop_omits_tasks():
    """The worker's health server runs on a plain thread on purpose;
    asyncio.all_tasks() raises RuntimeError there."""
    assert "tasks" not in meminfo.snapshot(in_event_loop=False)


def test_anon_tracks_a_real_heap_allocation():
    """The field exists to separate heap growth from file-backed RSS — the
    question the Dockerfile's MALLOC_* pair is an answer to. statm field 5
    ("data + stack") would NOT do this: it is a virtual size, so it counts
    address space that was reserved and never touched."""
    before = meminfo._rss_anon_bytes()
    if before is None:
        return  # no /proc (macOS): the field degrades to None by design
    buf = bytearray(40 * 1024 * 1024)
    buf[::4096] = b"x" * (len(buf) // 4096)   # touch it, so it is RESIDENT
    during = meminfo._rss_anon_bytes()
    del buf
    assert during - before > 30 * 1024 * 1024
