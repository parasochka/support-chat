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
