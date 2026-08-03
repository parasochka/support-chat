"""Background-only entrypoint: `python -m app.worker`.

The second half of the process split. `app.main` serves HTTP; this process runs
the retention event pipeline (drain, maintenance, send) and the quality judge,
and nothing else. They are separate Railway services built from the SAME image,
told apart by `SERVICE_ROLE` — see railway.toml.

Why split at all: a request-serving process and a background pipeline want
opposite things from a deploy. The web process must come up fast and never hold
a connection for minutes; the worker holds LEASES (a claimed event is a lease
the claimer must close), runs long model calls, and is sized for concurrency
rather than latency. Sharing one process meant a web redeploy killed the drain
mid-batch and a busy drain competed with player traffic for the same pool.

Shutdown is a DRAIN, not a kill: SIGTERM raises the stop flag, every loop wakes
out of its sleep, finishes the batch it is in and closes its leases. That is
what makes a restart cheap — see `_drain`.

The health endpoint on $PORT is not decoration: a background process with no
traffic looks identical whether it is working or wedged, so the platform
healthcheck reads the loops' own heartbeats and fails the deploy when one stops
ticking.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from app.core import config
from app.core import db
from app.core import settings

# The settings-refresh and log-flush loops are main.py's and are reused as-is: a
# worker whose settings cache never refreshes silently freezes every hot knob,
# and a worker whose logs never reach app_logs is a worker nobody can debug from
# the admin panel. Importing main also builds its (unused) ASGI app, which is
# cheap next to two divergent copies of the same loop drifting on prune cadence.
# It is also what installs logcapture + the log format for this process.
from app.main import _log_flush_loop, _settings_refresh_loop  # noqa: E402

log = logging.getLogger(f"{config.SERVICE_NAME}.worker")

# A loop is considered wedged when it has not ticked for this multiple of its
# own interval. Generous on purpose: a tick that overruns is normal (a model
# call takes 90s), only a tick that never comes is a fault.
_STALE_INTERVAL_MULTIPLE = 5
# Floor under the window above, so a 5-second cadence does not 503 on the first
# slow pass — and so the maintenance loop's one-time lifecycle backfill (which
# runs before its first tick) cannot fail the boot healthcheck.
_MIN_STALE_SEC = 300


# --- stop flag ----------------------------------------------------------------
# Module-level so the signal handlers can reach it without a closure.
STOP = asyncio.Event()


class _StopFlag(asyncio.Event):
    """A loop's stop flag — and, incidentally, its heartbeat.

    Every pipeline loop sleeps by awaiting its stop event once per iteration
    (`retention_v2._sleep_or_stop`), so `wait()` is the one place a loop's
    progress is observable from OUTSIDE the loop, without the loop knowing
    anything about health checks. A loop wedged inside a hung DB call or a
    completion that never returns simply stops calling it, and the health
    endpoint goes 503 instead of the process sitting there looking alive while
    processing nothing.
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.last_tick = time.monotonic()

    async def wait(self) -> bool:
        self.last_tick = time.monotonic()
        return await super().wait()


@dataclass
class _Loop:
    name: str
    task: asyncio.Task
    # None for loops that take no stop flag (the reviewer, the two infra loops):
    # they cannot be drained or heartbeated, only supervised and cancelled.
    stop: Optional[_StopFlag] = None
    interval: Optional[Callable[[], float]] = None


_LOOPS: list[_Loop] = []
_WORKER_ID = ""


def request_stop(reason: str) -> None:
    """Raise the process-wide stop flag and fan it out to every loop."""
    if STOP.is_set():
        return
    log.info("worker_stop_requested reason=%s", reason)
    STOP.set()
    for loop in _LOOPS:
        if loop.stop is not None:
            loop.stop.set()


# --- health -------------------------------------------------------------------
def health_snapshot() -> tuple[dict[str, Any], bool]:
    """Per-loop liveness + the verdict the healthcheck acts on.

    A worker with NO pipeline is unhealthy, not merely quiet. The infra loops
    (settings refresh, log flush) run in every process, so counting loops alone
    reported "ok" for a worker service whose RETENTION_SCHEDULER_ENABLED was
    left at the web service's 0 — the exact misconfiguration the healthcheck
    exists to surface, and one that otherwise looks identical to a product with
    nothing to do.
    """
    now = time.monotonic()
    loops: dict[str, Any] = {}
    healthy = any(loop.stop is not None for loop in _LOOPS)
    for loop in _LOOPS:
        running = not loop.task.done()
        entry: dict[str, Any] = {"running": running}
        if not running:
            healthy = False
        if loop.stop is not None:
            stale_after = _MIN_STALE_SEC
            if loop.interval is not None:
                try:
                    stale_after = max(
                        float(loop.interval()) * _STALE_INTERVAL_MULTIPLE,
                        _MIN_STALE_SEC)
                except Exception:  # noqa: BLE001 - a settings read must not 500 the probe
                    pass
            age = now - loop.stop.last_tick
            entry["last_tick_sec"] = round(age, 1)
            entry["stale_after_sec"] = round(stale_after, 1)
            if running and age > stale_after:
                healthy = False
        loops[loop.name] = entry
    return ({
        "status": "ok" if healthy else "unhealthy",
        "service": config.SERVICE_NAME,
        "role": "worker",
        "worker_id": _WORKER_ID,
        "draining": STOP.is_set(),
        "pipeline_enabled": config.RETENTION_SCHEDULER_ENABLED,
        "loops": loops,
    }, healthy)


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "support-chat-worker"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = self.path.split("?", 1)[0]
        if path not in ("/", "/healthz"):
            self.send_error(404)
            return
        payload, healthy = health_snapshot()
        body = json.dumps(payload).encode("utf-8")
        # A draining worker still answers 200 while it finishes: the platform
        # already knows it is going away, and a 503 here would only muddy the
        # deploy log with a failure that is really an orderly shutdown.
        self.send_response(200 if (healthy or STOP.is_set()) else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        # The platform probes every few seconds; one access line per probe would
        # bury the pipeline's own logs (and re-enter the app_logs buffer).
        pass


def _start_health_server() -> Optional[ThreadingHTTPServer]:
    """Serve the snapshot on $PORT from a plain thread.

    Deliberately NOT on the event loop: a loop blocked by a runaway sync call is
    exactly the failure this endpoint exists to report, and an in-loop server
    would go silent with it (a timeout the platform cannot distinguish from a
    network blip).
    """
    port = int(os.environ.get("PORT") or 8080)
    try:
        server = ThreadingHTTPServer(("", port), _HealthHandler)
    except OSError as exc:
        # Losing the probe is bad; refusing to run the pipeline over it is worse.
        log.warning("worker_health_server_failed port=%s error=%s", port, exc)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="worker-health",
                     daemon=True).start()
    log.info("worker_health_server_listening port=%s", port)
    return server


# --- loops --------------------------------------------------------------------
def _maintenance_interval() -> int:
    # Mirrors maintenance_loop's own read so the staleness window tracks the
    # cadence the operator actually set, not the one that shipped.
    return settings.global_retention_int(
        "maintenance_interval_sec", config.RETENTION_MAINTENANCE_INTERVAL_SEC,
        5, 3600)


def _spawn(name: str, factory: Callable[..., Any], *,
           stoppable: bool = True,
           interval: Optional[Callable[[], float]] = None) -> _Loop:
    flag = _StopFlag(name) if stoppable else None
    coro = factory(flag) if stoppable else factory()
    task = asyncio.create_task(coro, name=f"worker-{name}")
    loop = _Loop(name=name, task=task, stop=flag, interval=interval)
    _LOOPS.append(loop)

    def _on_done(t: asyncio.Task) -> None:
        if STOP.is_set() or t.cancelled():
            return
        # A loop that returns or raises while the worker is meant to be running
        # leaves the process half-dead: still healthy-looking, doing part of the
        # job. Bring the whole process down and let the platform restart it —
        # the leases it held are reclaimed, nothing is lost.
        exc = t.exception()
        if exc is not None:
            log.error("worker_loop_crashed loop=%s error=%s: %s",
                      name, exc.__class__.__name__, exc, exc_info=exc)
        else:
            log.error("worker_loop_exited loop=%s", name)
        request_stop(f"loop_exited:{name}")

    task.add_done_callback(_on_done)
    return loop


def _start_loops() -> None:
    from app.retention import retention_v2

    global _WORKER_ID
    _WORKER_ID = retention_v2.worker_id()

    # RETENTION_SCHEDULER_ENABLED keeps its meaning after the split: it is the
    # master kill switch of the background half, which is now this process. With
    # it off the worker still boots and answers the healthcheck (so the service
    # does not crash-loop) — it just runs no pipeline.
    if config.RETENTION_SCHEDULER_ENABLED:
        from app.ai import reviewer
        from app.retention import send_worker

        _spawn("agent", retention_v2.scheduler_loop,
               interval=retention_v2.worker_interval_sec)
        _spawn("maintenance", retention_v2.maintenance_loop,
               interval=_maintenance_interval)
        # The send stage drains on the same cadence as the drain loop; it is a
        # no-op until `retention.send_worker_enabled` is turned on.
        _spawn("send", send_worker.send_loop,
               interval=retention_v2.worker_interval_sec)
        # The quality judge takes no stop flag, so it is cancelled at shutdown
        # rather than drained — losing a half-finished review costs one cheap
        # pass and the conversation is picked up again next sweep.
        _spawn("review", reviewer.scheduler_loop, stoppable=False)
    else:
        log.warning("RETENTION_SCHEDULER_ENABLED is off; this worker runs no "
                    "background pipeline (set it to 1 on the worker service).")

    # The MEDIA NORMALIZER is deliberately NOT here. It owns the files on the
    # local media dir — on Railway a Volume mounted to the service that serves
    # the admin uploads — so it stays on the web process; running it here would
    # sweep a directory that does not hold the library.

    # Infra loops, one per process (both are main.py's; see the import above).
    _spawn("settings_refresh", _settings_refresh_loop, stoppable=False)
    _spawn("log_flush", _log_flush_loop, stoppable=False)


async def _drain() -> None:
    """Let the loops finish the batch in flight, then cancel what is left.

    A claimed event is a LEASE the claimer is expected to close. Dying mid-batch
    is safe (that is the point of the lease) but not free: the rows sit in
    `processing` until the reclaimer decides the worker is gone —
    `RETENTION_EVENT_LEASE_SEC`, five minutes by default — before anyone retries
    them, and every deploy would leave a five-minute hole in the queue's
    reaction time. Spending a few seconds here closing the leases properly is
    what makes a restart cheap. Railway SIGKILLs 30s after SIGTERM, so
    WORKER_DRAIN_TIMEOUT_SEC stays well under that.
    """
    # Loops with no stop flag cannot be asked to finish, and one of them sleeps
    # for half an hour — cancel them at once instead of spending the whole drain
    # budget waiting for a sleep to elapse.
    for loop in _LOOPS:
        if loop.stop is None:
            loop.task.cancel()
    drainable = [lp.task for lp in _LOOPS
                 if lp.stop is not None and not lp.task.done()]
    timeout = max(int(config.WORKER_DRAIN_TIMEOUT_SEC), 0)
    if drainable and timeout:
        done, pending = await asyncio.wait(drainable, timeout=timeout)
        if pending:
            log.warning("worker_drain_timeout loops=%s timeout_sec=%s",
                        len(pending), timeout)
    tasks = [lp.task for lp in _LOOPS]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _install_signal_handlers() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop, sig.name)
        except (NotImplementedError, RuntimeError, ValueError):
            # Windows / a non-main thread: no loop-integrated signals. SIGINT
            # still surfaces as KeyboardInterrupt around asyncio.run, which is
            # enough for local development — production is Linux.
            log.warning("worker_signal_handler_unavailable signal=%s", sig.name)


async def run() -> None:
    log.info("worker booting role=%s pipeline=%s drain_timeout_sec=%s",
             config.SERVICE_ROLE, config.RETENTION_SCHEDULER_ENABLED,
             config.WORKER_DRAIN_TIMEOUT_SEC)
    if config.SERVICE_ROLE == "web":
        # Not fatal — the loops work fine — but the web process is then also
        # running them, so every sweep happens twice.
        log.warning("SERVICE_ROLE=web on the worker entrypoint; set "
                    "SERVICE_ROLE=worker on this service.")
    await db.init_db()
    await settings.reload()
    _install_signal_handlers()
    _start_loops()
    server = _start_health_server()
    log.info("worker_started worker_id=%s loops=%s",
             _WORKER_ID, ",".join(lp.name for lp in _LOOPS))
    try:
        await STOP.wait()
    finally:
        await _drain()
        if server is not None:
            server.shutdown()
        await db.close()
        log.info("worker_stopped worker_id=%s", _WORKER_ID)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover - local Ctrl-C
        pass


if __name__ == "__main__":
    main()
