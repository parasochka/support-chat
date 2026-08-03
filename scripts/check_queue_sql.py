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


async def _player_exclusion(pid: int) -> None:
    """One player is never decided twice at once — ACROSS workers.

    Grouping a claimed batch by player only serializes one worker's own events.
    Two replicas (or the admin's «process queue now» alongside the sweep) claim
    independently, so without the exclusion in the SQL they can each take a
    different event of the same player, read the same guard counters, and send
    him two messages for what should have been one.
    """
    print("\nplayer exclusion")
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_v2_decisions")
        await c.execute("DELETE FROM retention_events")
    for i in range(3):
        await db.ingest_retention_event(
            pid, event_id=f"px{i}", event_name="deposit_confirmed",
            player_id="pSame", ts="2026-01-01T00:00:00Z", payload={},
            priority=1, queue=True)
    await db.ingest_retention_event(
        pid, event_id="pother", event_name="deposit_confirmed",
        player_id="pOther", ts="2026-01-01T00:00:00Z", payload={}, priority=1,
        queue=True)

    first = await db.claim_retention_events(pid, limit=1, lease_sec=60,
                                            worker_id="wA")
    check("a claim takes at least one event", len(first) >= 1)
    second = await db.claim_retention_events(pid, limit=10, lease_sec=60,
                                             worker_id="wB")
    players_left = {e["player_id"] for e in second}
    check("a second worker cannot touch a player already in flight",
          "pSame" not in players_left, players_left)
    check("a different player is still claimable", "pOther" in players_left)

    for e in first + second:
        await db.complete_retention_event(e["id"])
    async with db._acquire() as c:
        await c.execute("UPDATE retention_events SET status='pending', "
                        "processed_at=NULL, attempts=0")
    batch = await db.claim_retention_events(pid, limit=10, lease_sec=60,
                                            worker_id="wA")
    check("with nothing in flight, all of a player's events come together",
          len([e for e in batch if e["player_id"] == "pSame"]) == 3,
          [e["event_id"] for e in batch])


async def _observability(pid: int) -> None:
    print("\nobservability")
    # Its own backlog: sections before this one leave their events LEASED, and
    # lag only counts what nobody has picked up.
    async with db._acquire() as c:
        await c.execute(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts, created_at, priority) "
            "VALUES ($1, 'lagcheck', 'deposit_confirmed', 'pLag', now(), "
            "        now() - interval '5 minutes', 1)", pid)
    check("queue lag reports the oldest untouched event",
          await db.retention_queue_lag(pid) >= 300)
    stats = await db.retention_queue_stats(pid)
    check("stats break the backlog down by lane",
          stats["pending"] >= 1 and "p1_p2" in stats["pending_by_priority"],
          stats)
    check("the SLA percentile query runs on an empty ledger",
          (await db.retention_latency_percentiles(pid))["samples"] == 0)

    # THE LAG MUST MEAN "OVERDUE", NOT "QUEUED". The claim will not take a row
    # until its humanizing send delay has elapsed, so counting rows inside that
    # window made every busy product read 300..900s of lag — already past both
    # degrade rungs — and the shedding then latched, because the lanes it
    # stopped claiming stayed pending and stayed the oldest rows.
    check("a row still inside its send delay is not lag",
          await db.retention_queue_lag(pid, delay_min_sec=600,
                                       delay_max_sec=900) == 0)
    # …and the number is HOW LATE, not HOW OLD. The 'lagcheck' row is 5 min old
    # with a 4-min delay, so it is exactly ~1 min overdue. Reporting its age
    # (300s) instead is what left the ladder pinned below lane 5 forever.
    late = await db.retention_queue_lag(pid, delay_min_sec=240,
                                        delay_max_sec=240)
    check("the lag is overdue-ness, not queue age", 30 <= late <= 120, late)
    check("a just-due row reads as zero lag",
          await db.retention_queue_lag(pid, delay_min_sec=299,
                                       delay_max_sec=299) <= 5)
    lanes = await db.retention_queue_lag_by_lane(pid)
    check("lag is reported per lane ceiling",
          set(lanes) == {2, 3, 5} and lanes[2] >= 300 and lanes[5] >= 300,
          lanes)
    async with db._acquire() as c:
        await c.execute(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts, created_at, priority) "
            "VALUES ($1, 'lagfood', 'bet_settled', 'pFood', now(), "
            "        now() - interval '9 hours', 5)", pid)
        await c.execute(
            "DELETE FROM retention_events WHERE event_id = 'lagcheck'")
    lanes = await db.retention_queue_lag_by_lane(pid)
    check("a mountain of state food cannot argue for shedding state food",
          lanes[5] >= 9 * 3600 and lanes[2] == 0 and lanes[3] == 0, lanes)
    async with db._acquire() as c:
        await c.execute(
            "DELETE FROM retention_events WHERE event_id = 'lagfood'")


async def _replay_guard(pid: int) -> None:
    print("\nreplay guard")
    async with db._acquire() as c:
        pk = await c.fetchval(
            "INSERT INTO retention_events (product_id, event_id, event_name, "
            " player_id, ts) VALUES ($1, 'replaycheck', 'deposit_confirmed', "
            " 'pReplay', now()) RETURNING id", pid)
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
    # Self-clearing: the section asserts on a fixed delivery_id, and the whole
    # point of that id is that a second insert is refused — so a re-run against
    # the same scratch database would otherwise measure the leftovers.
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_deliveries")
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

    # OWNERSHIP BY CHANNEL. The send worker can only speak the Telegram seam,
    # and a claim is destructive — whoever takes a row closes it. An email row
    # it claimed was killed as 'opted_out_before_send' by the telegram consent
    # check and never seen again by the channel-aware retry sweep.
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_deliveries")
    await db.enqueue_delivery(pid, "dl_tg", player_id="pA",
                              retention_user_id=None, channel="telegram",
                              payload={"text": "hi"})
    await db.enqueue_delivery(pid, "dl_mail", player_id="pA",
                              retention_user_id=None, channel="email",
                              payload={"text": "hi"})
    tg = await db.claim_deliveries(pid, limit=9, lease_sec=60, worker_id="w1",
                                   channels=("telegram",))
    check("the claim can be scoped to one transport",
          [r["channel"] for r in tg] == ["telegram"],
          [r["channel"] for r in tg])
    check("the retry sweep sees only the channels it can send",
          [r["delivery_id"] for r in await db.due_delivery_retries(
              pid, channels=("email", "push"))] == [])

    # THE DEAD-LETTER CEILING. Without one a row whose channel is broken is
    # re-claimed on the saturated backoff rung forever, re-billing the same
    # generation into ai_interaction_logs on every attempt.
    await db.mark_delivery_failed(tg[0]["id"], "boom", backoff_sec=1)
    await asyncio.sleep(1.1)
    check("a row at the ceiling is not claimed again",
          await db.claim_deliveries(pid, limit=9, lease_sec=60,
                                    worker_id="w1", max_attempts=1,
                                    channels=("telegram",)) == [])
    check("…and it is closed out so it stops looking pending",
          await db.dead_letter_stale_deliveries(pid, 1) == 1)
    check("a second dead-letter pass is a no-op",
          await db.dead_letter_stale_deliveries(pid, 1) == 0)
    check("under the ceiling it is still claimable",
          len(await db.claim_deliveries(pid, limit=9, lease_sec=60,
                                        worker_id="w1", max_attempts=9,
                                        channels=("email",))) == 1)

    # A lease reclaimed on the last permitted attempt comes back as 'queued'
    # WITHOUT losing an attempt, so it is unclaimable — the dead-letter pass
    # has to close it out or it sits there forever looking like pending work.
    async with db._acquire() as c:
        await c.execute(
            "UPDATE retention_deliveries SET status='queued', "
            "permanent_fail=FALSE, attempts=9 WHERE delivery_id='dl_tg'")
    check("a reclaimed row at the ceiling is closed out too",
          await db.dead_letter_stale_deliveries(
              pid, 5, channels=("telegram",)) == 1)
    async with db._acquire() as c:
        await c.execute(
            "UPDATE retention_deliveries SET status='failed', "
            "permanent_fail=FALSE, attempts=9 WHERE delivery_id='dl_mail'")
    check("…and the ceiling never reaches across the channel boundary",
          await db.dead_letter_stale_deliveries(
              pid, 5, channels=("telegram",)) == 0)


async def _journey_reentry(pid: int) -> None:
    """The scheduled matcher re-derives candidates from live state, so a
    finished enrollment matched again on the very next sweep (the partial
    unique index only covers status='active')."""
    print("\njourney re-entry")
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_journey_enrollments "
                        "WHERE player_id = 'pJ'")
    check("no prior enrollment reads as None",
          await db.last_enrollment_at(pid, "pJ", "recovery_d7_soft") is None)
    eid = await db.create_enrollment(pid, player_id="pJ",
                                     retention_user_id=None,
                                     journey_key="recovery_d7_soft",
                                     journey_version=1)
    check("the first enrollment is created", eid is not None)
    check("a run that never executed a step does not consume the cooldown",
          await db.last_enrollment_at(pid, "pJ", "recovery_d7_soft") is None)
    check("...but it is visible to the floor",
          await db.last_enrollment_started_at(
              pid, "pJ", "recovery_d7_soft") is not None)
    await db.advance_enrollment(pid, eid, current_step=1)
    await db.finish_enrollment(pid, eid, "completed", reason=None)
    check("a COMPLETED run that touched the player consumes the cooldown",
          await db.last_enrollment_at(pid, "pJ",
                                      "recovery_d7_soft") is not None)
    async with db._acquire() as c:
        await c.execute("UPDATE retention_journey_enrollments "
                        "SET status = \'exited_return\' WHERE id = $1", eid)
    check("a run the player RETURNED from does not lock him out",
          await db.last_enrollment_at(pid, "pJ", "recovery_d7_soft") is None)
    check("another journey is unaffected",
          await db.last_enrollment_at(pid, "pJ", "weekly_x") is None)


async def _activity_bridge(pid: int) -> None:
    """The casino-activity bridge — same untyped-`$n`-in-an-expression class.

    `touch_retention_activity` fires on every high-volume event and its
    debounce clause compares the timestamp parameter against
    `$n - make_interval(...)`. With the parameter uncast, Postgres resolves it
    to `interval` (the type of the other operand) and the statement dies at
    PREPARE time — on EVERY call, debouncing or not. `player_sync._ingest`
    wraps the bridge in a broad `except`, so the only symptom was activity
    timestamps that silently never moved.
    """
    print("\ncasino-activity bridge")
    import datetime as _dt
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_users WHERE tg_user_id = 987654")
        await c.execute(
            "INSERT INTO retention_users (product_id, tg_user_id, player_id) "
            "VALUES ($1, 987654, 'pACT')", pid)
    now = _dt.datetime.now(_dt.timezone.utc)
    for field in db._RETENTION_ACTIVITY_FIELDS:
        for debounce in (0, 60):
            try:
                await db.touch_retention_activity(pid, "pACT", field, now,
                                                  debounce_sec=debounce)
                ok, err = True, ""
            except Exception as exc:  # noqa: BLE001
                ok, err = False, f"{exc.__class__.__name__}: {exc}"
            check(f"{field} bumps with debounce={debounce}", ok, err)
    check("a forward bump lands",
          await db.touch_retention_activity(
              pid, "pACT", "last_login_at",
              now + _dt.timedelta(hours=1)) == 1)
    check("an older event never rewinds the timestamp",
          await db.touch_retention_activity(
              pid, "pACT", "last_login_at",
              now - _dt.timedelta(hours=1), debounce_sec=60) == 0)


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


async def _reset() -> None:
    """Start from a known queue state.

    Every section asserts on exact counts and on fixed ids whose whole purpose
    is that a second insert is refused, so leftovers from a previous run would
    be read as failures. Clearing the queue tables (and only those) makes the
    checker deterministic on any scratch database instead of only a virgin one
    — a checker that cries wolf on its second run is a checker nobody trusts.
    """
    async with db._acquire() as c:
        await c.execute("DELETE FROM retention_outcomes")
        await c.execute("DELETE FROM retention_journey_enrollments")
        await c.execute("DELETE FROM retention_deliveries")
        await c.execute("DELETE FROM retention_v2_decisions")
        await c.execute("DELETE FROM retention_events")
        await c.execute("DELETE FROM retention_worker_jobs")
        await c.execute("DELETE FROM retention_rate_budget")
        # The upgrade section deliberately ends with the replay-guard index
        # ABSENT (that is what it proves: duplicates make boot skip it rather
        # than refuse to start). Now that the duplicates are gone, put it back —
        # otherwise a re-run measures a database with no replay guard at all.
        await c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_retention_v2_decisions_event "
            "ON retention_v2_decisions (product_id, event_pk) "
            "WHERE event_pk IS NOT NULL AND action <> 'skipped'")


async def main() -> int:
    await db.init_db()
    print("init_db OK (schema + guarded ALTERs + the defensive unique index)")
    await _reset()
    async with db._acquire() as c:
        pid = await c.fetchval("SELECT id FROM products LIMIT 1")
    await _lifecycle(pid)
    await _player_exclusion(pid)
    await _observability(pid)
    await _replay_guard(pid)
    await _pacing_and_shaping(pid)
    await _send_queue(pid)
    await _activity_bridge(pid)
    await _journey_reentry(pid)
    await _upgrade_path()
    await db.close()
    print("\n" + ("ALL QUEUE SQL CHECKS PASSED" if not _FAILED
                  else f"FAILURES: {_FAILED}"))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
