"""Worker liveness heartbeat (worker_heartbeats + the admin chip's source).

After the web/worker split the web service serving /admin runs with
RETENTION_SCHEDULER_ENABLED=0 by design, so the admin's worker chip can no
longer read this process's env to answer "is the pipeline running?" — it keys
on the durable heartbeat the scheduler loop stamps instead.
"""
import asyncio

from app.core import db
from app.retention import retention_v2


def _reset_beat_throttle():
    retention_v2._last_beat_monotonic = 0.0


# --- heartbeat_alive (the reader's staleness verdict) -------------------------

def test_no_heartbeat_is_not_alive():
    assert retention_v2.heartbeat_alive(None) is False


def test_fresh_heartbeat_is_alive():
    hb = {"worker_id": "w-1", "role": "worker", "interval_sec": 30,
          "age_sec": 10.0}
    assert retention_v2.heartbeat_alive(hb) is True


def test_stale_heartbeat_is_dead():
    # stale_after = interval * 3 + 30 = 120 for a 30s cadence
    hb = {"worker_id": "w-1", "role": "worker", "interval_sec": 30,
          "age_sec": 121.0}
    assert retention_v2.heartbeat_alive(hb) is False


def test_staleness_keys_on_the_writers_own_cadence():
    # An hourly-tick worker must not be declared dead between legitimate beats.
    hb = {"worker_id": "w-1", "role": "worker", "interval_sec": 3600,
          "age_sec": 5000.0}
    assert retention_v2.heartbeat_alive(hb) is True


# --- the beat writer ----------------------------------------------------------

async def test_beat_writes_and_throttles(monkeypatch):
    _reset_beat_throttle()
    calls = []

    async def _beat(worker_id, role, interval_sec):
        calls.append((worker_id, role, interval_sec))

    monkeypatch.setattr(db, "beat_worker_heartbeat", _beat)
    await retention_v2._beat_heartbeat()
    await retention_v2._beat_heartbeat()  # inside the throttle window: no write
    assert len(calls) == 1
    # the stored cadence is the loop tick floored at the 30s minimum
    assert calls[0][2] >= retention_v2._HEARTBEAT_MIN_SEC


async def test_beat_failure_never_raises(monkeypatch):
    _reset_beat_throttle()

    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "beat_worker_heartbeat", _boom)
    await retention_v2._beat_heartbeat()  # must swallow


async def test_scheduler_loop_beats_on_start(monkeypatch):
    _reset_beat_throttle()
    calls = []

    async def _beat(worker_id, role, interval_sec):
        calls.append(worker_id)

    monkeypatch.setattr(db, "beat_worker_heartbeat", _beat)
    stop = asyncio.Event()
    stop.set()  # exit on the first _sleep_or_stop
    await retention_v2.scheduler_loop(stop=stop)
    assert len(calls) == 1  # the boot beat happened before the loop exited
