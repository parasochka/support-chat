#!/usr/bin/env python3
"""Exercise the event/send queue SQL against a REAL Postgres.

WHY THIS EXISTS. `tests/conftest.py` stubs asyncpg, so the unit suite can
assert which statements a helper issues but never that Postgres accepts them.
That gap is not theoretical: the queue's statements shipped three failures the
whole suite was blind to — asyncpg prepares a bare `$n` inside
`make_interval(secs => $n)` from the function signature, but inside an
EXPRESSION (`GREATEST($3, $4)`, `$2 - $4`) it has nothing to infer from,
prepares `unknown`, and Postgres rejects the call at prepare time. Every one of
those would have been a hard runtime error on the first real tick.

It is deliberately NOT part of `scripts/preflight.sh` (which installs no
asyncpg and has no database): run it by hand when you touch queue SQL, and
against a scratch database only — it writes and prunes.

    createdb queuecheck
    DATABASE_URL=postgresql://localhost/queuecheck python scripts/check_queue_sql.py

Exit code 0 = every check passed.
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("SUPPORT_CHAT_TEST_MODE", "1")
os.environ.setdefault("SESSION_JWT_SECRET", "x" * 32)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL"):
    print("DATABASE_URL is required — point it at a SCRATCH database.")
    raise SystemExit(2)

from app.core import db  # noqa: E402

_FAILED: list[str] = []


def check(name: str, ok: bool, extra: object = "") -> None:
    print(("  ok   " if ok else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not ok:
        _FAILED.append(name)


async def _lifecycle(pid: int) -> None:
    print("\nqueue lifecycle")
    async with db._acquire() as c:
        # A pre-lifecycle row: 'pending' by column default, but processed_at
        # says the old worker finished with it. It must never be re-claimed.
        await c.execute(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts, processed_at) "
            "VALUES ($1, 'legacy', 'deposit_confirmed', 'pL', now(), now())",
            pid)
    for i, (name, prio, player) in enumerate([
            ("bet_settled", 5, "pA"), ("level_up", 2, "pA"),
            ("deposit_confirmed", 1, "pB"), ("kyc_started", 3, "pC")]):
        await db.ingest_retention_event(
            pid, event_id=f"e{i}", event_name=name, player_id=player,
            ts="2026-01-01T00:00:00Z", payload={}, priority=prio, queue=True)

    got = await db.claim_retention_events(pid, limit=10, lease_sec=60,
                                          worker_id="w1")
    check("legacy processed rows are never re-claimed",
          all(g["event_id"] != "legacy" for g in got))
    check("a claim LEASES (processing + lease + attempt), never completes",
          all(g["status"] == "processing" and g["processed_at"] is None
              and g["attempts"] == 1 and g["worker_id"] == "w1" for g in got))
    check("a concurrent claim cannot take leased rows",
          await db.claim_retention_events(pid, limit=10, lease_sec=60,
                                          worker_id="w2") == [])

    by = {g["event_id"]: g for g in got}
    await db.complete_retention_event(by["e2"]["id"])
    check("a first failure retries rather than dying",
          await db.fail_retention_event(by["e3"]["id"], "boom", max_attempts=3,
                                        backoff_base_sec=30) == "pending")
    await db.release_retention_events([by["e0"]["id"], by["e1"]["id"]])
    async with db._acquire() as c:
        rows = {r["event_id"]: dict(r) for r in await c.fetch(
            "SELECT event_id, status, attempts, next_attempt_at, processed_at "
            "FROM retention_events WHERE product_id = $1", pid)}
    check("completed rows are done and stamped",
          rows["e2"]["status"] == "done" and rows["e2"]["processed_at"])
    check("a failed row waits behind its backoff",
          rows["e3"]["status"] == "pending" and rows["e3"]["next_attempt_at"])
    check("a released row carries no attempt penalty",
          rows["e0"]["status"] == "pending" and rows["e0"]["attempts"] == 0)
    check("a backed-off row is not claimable yet",
          all(g["event_id"] != "e3" for g in await db.claim_retention_events(
              pid, limit=10, lease_sec=60, worker_id="w1")))

    async with db._acquire() as c:
        await c.execute("UPDATE retention_events SET status='pending', "
                        "locked_until=NULL, attempts=0 "
                        "WHERE product_id=$1 AND status='processing'", pid)
    lanes = await db.claim_retention_events(pid, limit=10, lease_sec=60,
                                            worker_id="w1", max_priority=2)
    check("the lane ceiling sheds low-priority work",
          {g["event_id"] for g in lanes} == {"e1"},
          [g["event_id"] for g in lanes])

    async with db._acquire() as c:
        await c.execute("UPDATE retention_events SET status='pending', "
                        "attempts=3, locked_until=NULL "
                        "WHERE event_id='e1' AND product_id=$1", pid)
    ev = (await db.claim_retention_events(pid, limit=1, lease_sec=60,
                                          worker_id="w1"))[0]
    check("the attempt ceiling dead-letters",
          await db.fail_retention_event(ev["id"], "again",
                                        max_attempts=3) == "dead")
    check("dead letters are listable",
          (await db.list_dead_retention_events(pid))["total"] == 1)
    check("requeue puts them back", await db.requeue_retention_events(pid) == 1)

    ev = (await db.claim_retention_events(pid, limit=1, lease_sec=60,
                                          worker_id="w1"))[0]
    async with db._acquire() as c:
        await c.execute("UPDATE retention_events SET locked_until = now() - "
                        "interval '1 hour' WHERE id = $1", ev["id"])
    check("an abandoned lease comes back",
          await db.reclaim_expired_event_leases(max_attempts=99) == 1)


async def _observability(pid: int) -> None:
    print("\nobservability")
    async with db._acquire() as c:
        await c.execute("UPDATE retention_events SET created_at = now() - "
                        "interval '5 minutes' WHERE product_id = $1", pid)
    check("queue lag reports the oldest untouched event",
          await db.retention_queue_lag(pid) >= 300)
    stats = await db.retention_queue_stats(pid)
    check("stats break the backlog down by lane",
          stats["pending"] >= 1 and "p1_p2" in stats["pending_by_priority"],
          stats)
    check("the SLA percentile query runs on an empty ledger",
          (await db.retention_latency_percentiles(pid))["samples"] == 0)


async def _replay_guard(pid: int) -> None:
    print("\nreplay guard")
    async with db._acquire() as c:
        pk = await c.fetchval(
            "SELECT id FROM retention_events WHERE product_id=$1 LIMIT 1", pid)
    kw = dict(retention_user_id=None, player_id="pA", trigger_kind="event",
              event_pk=pk, event_name="deposit_confirmed", state={}, guard={})
    first = await db.insert_retention_v2_decision(pid, action="message", **kw)
    replay = await db.insert_retention_v2_decision(pid, action="message", **kw)
    check("a replayed event cannot decide twice",
          first is not None and replay is None, (first, replay))
    check("'skipped' diagnostics stay outside the guard",
          await db.insert_retention_v2_decision(
              pid, action="skipped", **kw) is not None)
    await db.update_retention_v2_decision(first, delivered=True, detail="sent")


async def _pacing_and_shaping(pid: int) -> None:
    print("\njob pacing + token bucket")
    check("the first claim of a job wins",
          await db.claim_worker_job(pid, "scoring", 60, lease_sec=600) is True)
    check("a claim inside the lease loses",
          await db.claim_worker_job(pid, "scoring", 60) is False)
    async with db._acquire() as c:
        leased = await c.fetchval("SELECT next_run_at FROM retention_worker_jobs "
                                  "WHERE product_id=$1 AND job='scoring'", pid)
    await db.finish_worker_job(pid, "scoring", status="ok", duration_ms=12,
                               interval_sec=60)
    async with db._acquire() as c:
        anchored = await c.fetchval("SELECT next_run_at FROM retention_worker_jobs "
                                    "WHERE product_id=$1 AND job='scoring'", pid)
    check("finishing pulls the next run back off the lease", anchored < leased)

    taken = [await db.take_rate_token("tg:1", rate_per_sec=1.0, burst=3.0)
             for _ in range(5)]
    check("the bucket grants exactly the burst, then refuses",
          taken[:3] == [True] * 3 and taken[3] is False, taken)
    check("an unlimited scope costs no round trip",
          await db.take_rate_token("tg:0", rate_per_sec=0, burst=0) is True)
    await asyncio.sleep(1.1)
    check("the bucket refills with elapsed time",
          await db.take_rate_token("tg:1", rate_per_sec=1.0, burst=3.0) is True)


async def _send_queue(pid: int) -> None:
    print("\nsend queue")
    did = await db.enqueue_delivery(pid, "dl_x", player_id="pA",
                                    retention_user_id=None, channel="telegram",
                                    priority=1, payload={"text": "hi"})
    dup = await db.enqueue_delivery(pid, "dl_x", player_id="pA",
                                    retention_user_id=None, channel="telegram")
    check("a replayed decision cannot enqueue twice",
          did is not None and dup is None)
    rows = await db.claim_deliveries(pid, limit=5, lease_sec=60, worker_id="w1")
    check("the delivery claim leases and round-trips its payload",
          len(rows) == 1 and rows[0]["status"] == "sending"
          and rows[0]["payload"] == {"text": "hi"})
    await db.reschedule_delivery(rows[0]["id"], delay_sec=0.01)
    await asyncio.sleep(0.1)
    again = await db.claim_deliveries(pid, limit=5, lease_sec=60,
                                      worker_id="w1")
    check("an empty-bucket reschedule comes back", len(again) == 1)
    await db.mark_delivery_failed(again[0]["id"], "blocked", permanent=True)
    check("a permanent failure never retries",
          await db.claim_deliveries(pid, limit=5, lease_sec=60,
                                    worker_id="w1") == [])


async def _upgrade_path() -> None:
    """The riskiest path: a database that predates the lifecycle."""
    print("\nupgrade from a pre-lifecycle database")
    async with db._acquire() as c:
        pid = await c.fetchval("SELECT id FROM products LIMIT 1")
        await c.execute("DELETE FROM retention_v2_decisions")
        await c.execute("DELETE FROM retention_events")
        for idx in ("idx_retention_events_ready", "idx_retention_events_lease",
                    "idx_retention_events_dead",
                    "idx_retention_v2_decisions_event"):
            await c.execute(f"DROP INDEX IF EXISTS {idx}")
        for col in ("status", "attempts", "locked_until", "next_attempt_at",
                    "priority", "last_error", "worker_id"):
            await c.execute("ALTER TABLE retention_events "
                            f"DROP COLUMN IF EXISTS {col}")
        await c.execute(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts, processed_at, created_at) "
            "SELECT $1, 'old_' || g, 'deposit_confirmed', 'p' || g, "
            "  now() - interval '10 days', now() - interval '10 days', "
            "  now() - interval '10 days' FROM generate_series(1, 500) g", pid)
        await c.execute(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts) VALUES ($1, 'fresh', 'deposit_confirmed', "
            " 'pNew', now())", pid)
        pk = await c.fetchval("SELECT id FROM retention_events "
                              "WHERE event_id='fresh'")
        # Two decisions on ONE event: the duplicate the unique index is
        # defensive about. Boot must survive it rather than refuse to start.
        for _ in range(2):
            await c.execute(
                "INSERT INTO retention_v2_decisions (product_id, trigger_kind, "
                " event_pk, event_name, action) "
                "VALUES ($1,'event',$2,'x','message')", pid, pk)
    await db.close()

    await db.init_db()
    check("a legacy database boots", True)
    async with db._acquire() as c:
        has_idx = await c.fetchval(
            "SELECT count(*) FROM pg_indexes "
            "WHERE indexname='idx_retention_v2_decisions_event'")
    check("pre-existing duplicates skip the index instead of killing boot",
          has_idx == 0)
    claimed = await db.claim_retention_events(pid, limit=1000, lease_sec=60,
                                              worker_id="w1")
    check("the claim ignores un-backfilled history",
          [c["event_id"] for c in claimed] == ["fresh"], len(claimed))
    await db.release_retention_events([c["id"] for c in claimed])
    check("the batched backfill converges",
          await db.backfill_event_lifecycle(batch=100) == 500)
    check("a second backfill pass is a no-op",
          await db.backfill_event_lifecycle(batch=100) == 0)
    await db.close()
    await db.init_db()
    check("init_db stays idempotent on the upgraded database", True)


async def main() -> int:
    await db.init_db()
    print("init_db OK (schema + guarded ALTERs + the defensive unique index)")
    async with db._acquire() as c:
        pid = await c.fetchval("SELECT id FROM products LIMIT 1")
    await _lifecycle(pid)
    await _observability(pid)
    await _replay_guard(pid)
    await _pacing_and_shaping(pid)
    await _send_queue(pid)
    await _upgrade_path()
    await db.close()
    print("\n" + ("ALL QUEUE SQL CHECKS PASSED" if not _FAILED
                  else f"FAILURES: {_FAILED}"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
