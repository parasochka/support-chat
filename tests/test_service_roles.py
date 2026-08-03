"""The process split: which half of the service a role owns.

The failure these pin down is silent either way — a 'web' process that still
runs the pipeline doubles every sweep against the real worker, and a 'web'
process that drops the media normalizer leaves uploaded photos un-normalized on
the volume nobody else has mounted.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core import config
from app import main
from app import worker


@pytest.mark.parametrize("role,enabled,pipeline,media", [
    # 'web' never runs the pipeline, whatever the switch says — but it keeps
    # the media normalizer, which follows the volume, not the pipeline.
    ("web", True, False, True),
    ("web", False, False, True),
    # 'all' is the pre-split single-process behaviour, still gated by the switch.
    ("all", True, True, True),
    ("all", False, False, False),
    # main.py is not the worker's entrypoint: started with the worker role it
    # must not duplicate the loops the real worker service is running.
    ("worker", True, False, False),
    # A typo falls back to the pre-split behaviour rather than silently
    # disabling every background loop.
    ("wrker", True, True, True),
])
def test_lifespan_role_plan(role, enabled, pipeline, media):
    assert main._background_plan(role, enabled) == (pipeline, media)


def test_stop_flag_records_a_heartbeat():
    async def _go():
        flag = worker._StopFlag("agent")
        flag.last_tick = time.monotonic() - 60
        # Every loop sleeps by awaiting its stop flag, which is the only tick
        # observable from outside the loop.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(flag.wait(), timeout=0.01)
        assert time.monotonic() - flag.last_tick < 5

    asyncio.run(_go())


def test_health_snapshot_503s_on_a_stale_loop():
    async def _go():
        started = asyncio.Event()

        async def _never_ticks(stop):
            started.set()
            await stop.wait()

        worker._LOOPS.clear()
        worker.STOP.clear()
        try:
            loop = worker._spawn("agent", _never_ticks, interval=lambda: 5)
            await started.wait()
            payload, healthy = worker.health_snapshot()
            assert healthy and payload["loops"]["agent"]["running"]
            # A loop that stopped ticking must fail the deploy's healthcheck
            # instead of sitting there looking alive and processing nothing.
            loop.stop.last_tick -= worker._MIN_STALE_SEC + 60
            payload, healthy = worker.health_snapshot()
            assert not healthy and payload["status"] == "unhealthy"
        finally:
            worker.request_stop("test")
            await asyncio.gather(*(lp.task for lp in worker._LOOPS),
                                 return_exceptions=True)
            worker._LOOPS.clear()
            worker.STOP.clear()

    asyncio.run(_go())


def test_drain_lets_a_loop_finish_its_batch():
    async def _go():
        finished = []

        async def _batching(stop):
            while not await _sleep(stop):
                pass
            finished.append(True)

        async def _sleep(stop):
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.01)
                return True
            except asyncio.TimeoutError:
                return False

        worker._LOOPS.clear()
        worker.STOP.clear()
        try:
            worker._spawn("agent", _batching)
            await asyncio.sleep(0.02)
            worker.request_stop("test")
            await worker._drain()
            # Drained, not cancelled: the loop got to close its leases.
            assert finished == [True]
        finally:
            worker._LOOPS.clear()
            worker.STOP.clear()

    asyncio.run(_go())


def test_worker_drain_budget_stays_under_the_platform_sigkill():
    # Railway SIGKILLs 30s after SIGTERM; a longer drain would be cut off
    # mid-batch, which is exactly what the drain exists to avoid.
    assert 0 < config.WORKER_DRAIN_TIMEOUT_SEC < 30


async def test_lifespan_actually_gates_on_the_plan(monkeypatch):
    """The plan helper is only worth having if the lifespan OBEYS it.

    Testing `_background_plan` alone left the guards themselves uncovered:
    deleting `if runs_pipeline:` would start the drain, the maintenance loop
    and the send worker inside a 'web' process running beside the real worker
    service — every sweep twice, every product — and the matrix test would
    still be green.
    """
    from app.core import config as cfg_mod
    from app.core import db as db_mod
    from app.core import settings as settings_mod

    started: list[str] = []

    async def _noop_db():
        return None

    async def _noop_settings():
        return None

    def _fake_create_task(coro, *a, **kw):
        # By MODULE + name: the agent, the media normalizer and the quality
        # judge all export a coroutine called `scheduler_loop`, so the bare
        # name cannot tell "the drain started" from "the normalizer started".
        mod = getattr(coro, "cr_frame", None)
        mod = mod.f_globals.get("__name__", "?") if mod else "?"
        started.append(f"{mod}.{getattr(coro, '__name__', coro)}")
        coro.close()          # never actually run a loop in a unit test
        task = asyncio.Future()
        task.cancel()
        return task

    monkeypatch.setattr(db_mod, "init_db", _noop_db)
    monkeypatch.setattr(db_mod, "close", _noop_db)
    monkeypatch.setattr(settings_mod, "reload", _noop_settings)
    monkeypatch.setattr(main.asyncio, "create_task", _fake_create_task)
    monkeypatch.setattr(cfg_mod, "RETENTION_SCHEDULER_ENABLED", True)

    async def _run(role: str) -> list[str]:
        started.clear()
        monkeypatch.setattr(cfg_mod, "SERVICE_ROLE", role)
        # lifespan is an @asynccontextmanager, so enter/exit it rather than
        # driving the generator by hand.
        async with main.lifespan(None):
            pass
        return list(started)

    web = await _run("web")
    assert "app.retention.retention_v2.scheduler_loop" not in web, web
    assert "app.retention.retention_v2.maintenance_loop" not in web, web
    assert "app.retention.send_worker.send_loop" not in web, web
    # The media normalizer follows the VOLUME, not the pipeline.
    assert "app.retention.media_normalizer.scheduler_loop" in web, web

    every = await _run("all")
    for name in ("app.retention.retention_v2.scheduler_loop",
                 "app.retention.retention_v2.maintenance_loop",
                 "app.retention.send_worker.send_loop",
                 "app.retention.media_normalizer.scheduler_loop"):
        assert name in every, (name, every)


# ---------------------------------------------------------------------------
# Dead-letter routes: multi-tenant authorization
# ---------------------------------------------------------------------------
async def test_dead_letter_routes_authorize_against_the_product(monkeypatch):
    """A dead letter is a reaction a player never got, and requeueing it makes
    the bot write to someone else's players — so both routes must check the
    PRODUCT, not merely "is this account an admin somewhere".

    Dropping either scope call leaves a manager, or an admin scoped to another
    partner, able to read one tenant's failed events and replay them.
    """
    from app.api import retention as retention_api

    calls: list[tuple[str, int]] = []

    async def _read(admin, pid):
        calls.append(("read", int(pid)))

    async def _write(admin, pid):
        calls.append(("write", int(pid)))

    async def _list(pid, page=1, page_size=50):
        return {"items": [], "total": 0}

    async def _requeue(pid, pks=None):
        return 2

    async def _event(*a, **kw):
        return None

    monkeypatch.setattr(retention_api.admin_auth, "require_product_read", _read)
    monkeypatch.setattr(retention_api.admin_auth, "require_product_write",
                        _write)
    monkeypatch.setattr(retention_api.db, "list_dead_retention_events", _list)
    monkeypatch.setattr(retention_api.db, "requeue_retention_events", _requeue)
    monkeypatch.setattr(retention_api.db, "log_admin_event", _event)

    admin = {"email": "a@b.c"}
    await retention_api.v2_dead_letter(7, admin=admin)
    assert calls == [("read", 7)]

    calls.clear()
    body = retention_api.RequeueEventsReq(event_pks=[1, 2])
    resp = await retention_api.v2_requeue_dead_letter(7, body, admin=admin)
    assert calls == [("write", 7)], calls
    assert b'"requeued":2' in resp.body.replace(b" ", b"")


async def test_the_idle_sweep_closes_its_own_job_row(monkeypatch):
    """The ladder claims its job slot inside run_product_idle_pings, so the
    maintenance pass must CLOSE it — the four sweeps that go through
    _run_maintenance_job get that for free, this one does not.

    Observed in production after the first deploy: the `idle` row sat at
    last_status='running' with no duration forever, which is precisely the
    signal the background-jobs panel exists to give about a wedged sweep.
    """
    from app.core import db as db_mod
    from app.core import settings as settings_mod
    from app.retention import retention_idle, retention_v2

    finished: list[tuple] = []

    async def _lag(pid, **_kw):
        return 0

    async def _idle(product, cfg, **kw):
        return {"sent": 1}

    async def _finish(pid, job, *, status, duration_ms=None, error=None,
                      interval_sec=None):
        finished.append((job, status, duration_ms is not None))

    async def _skip(*a, **kw):
        return {"skipped": "paced"}

    monkeypatch.setattr(settings_mod, "retention",
                        lambda: {"idle_pings_enabled": True})
    monkeypatch.setattr(db_mod, "retention_queue_lag", _lag)
    monkeypatch.setattr(db_mod, "finish_worker_job", _finish)
    monkeypatch.setattr(retention_idle, "run_product_idle_pings", _idle)
    monkeypatch.setattr(retention_v2, "_run_maintenance_job", _skip)
    monkeypatch.setattr(retention_v2.settings, "global_retention_bool",
                        lambda key, default: True)

    await retention_v2.run_product_maintenance({"id": 1})
    assert ("idle", "ok", True) in finished, finished

    # A sweep that only got paced out has not run, so it must not overwrite the
    # row of whoever is actually running it.
    finished.clear()
    monkeypatch.setattr(retention_idle, "run_product_idle_pings", _skip)
    await retention_v2.run_product_maintenance({"id": 1})
    assert not [f for f in finished if f[0] == "idle"], finished
