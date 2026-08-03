"""The durable event queue: the lease contract and its replay guard.

`retention_events` used to be drained with a SELECT that stamped `processed_at`
at SELECTION time, so an event whose pipeline then threw — or whose worker was
killed mid-batch by a deploy — counted as processed and was gone with no retry,
no dead-letter and no trace. The lifecycle (status / attempts / locked_until /
next_attempt_at / last_error) is what replaced it, and every one of its
guarantees lives in SQL a unit test cannot execute. So these pin the STATEMENTS
through the shared fake connection from conftest: what the claim writes (and,
just as important, what it must NOT write), what closes a lease, what retries
and what dead-letters.

The last block covers the other half of at-least-once delivery: because a lease
can be reclaimed, `_process_event` must be stopped by the reserved decision row
BEFORE anything the player can see.
"""
from __future__ import annotations

from app.core import db
from app.retention import retention_v2
from tests.conftest import FakeConn, FakePool, Row


def _event_row(**over):
    """A claimed row as the RETURNING clause hands it back (Row answers any
    column the helper reads but the test does not care about)."""
    row = Row({"id": 1, "player_id": "p1", "event_name": "deposit_confirmed",
               "status": "processing", "attempts": 1, "priority": 1})
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# Claim = LEASE
# ---------------------------------------------------------------------------
async def test_claim_leases_and_never_stamps_processed_at(monkeypatch):
    """The claim must lease, not consume.

    Stamping `processed_at` in the selecting statement is the exact regression
    that lost events: the row looks finished the instant it is picked up, so a
    crash between claim and send drops the touch silently. A lease (status +
    expiry + attempt count + owner) is retryable; a stamp is not.
    """
    conn = FakeConn(rows=[_event_row(id=5), _event_row(id=2)])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    events = await db.claim_retention_events(
        7, limit=25, delay_min_sec=300, delay_max_sec=900,
        lease_sec=180, worker_id="w-1", max_priority=3)

    sql, args = conn.calls[0]
    assert "status = 'processing'" in sql
    assert "attempts = attempts + 1" in sql
    assert "locked_until = now() + make_interval" in sql
    assert "worker_id = $6" in sql
    # The ONLY mention of processed_at is the read guard below — no assignment.
    assert "processed_at =" not in sql
    assert args == (7, 25, 300.0, 601, 180.0, "w-1", 3)
    # UPDATE ... RETURNING has no ORDER, so the helper sorts; without it a
    # player's events would reach the pipeline out of order.
    assert [e["id"] for e in events] == [2, 5]


async def test_claim_is_safe_on_an_un_backfilled_database(monkeypatch):
    """`status = 'pending' AND processed_at IS NULL`, both.

    Rows written before the lifecycle existed default to status='pending' even
    though the old drain finished with them. Testing status alone would make
    the first boot after the upgrade re-react to the ENTIRE event history —
    every player getting congratulated on months-old deposits. The backfill
    converges on it later; the claim must not wait for that.
    """
    conn = FakeConn(rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.claim_retention_events(1)

    sql = conn.sql[0]
    assert "status = 'pending'" in sql
    assert "processed_at IS NULL" in sql


async def test_claim_serves_the_best_lane_first_and_skips_locked(monkeypatch):
    """Lane order + SKIP LOCKED: the two properties that let several workers
    (and the admin «Process queue now» button) drain one product without ever
    handing the same event to two pipelines, while a deposit still overtakes a
    backlog of state food."""
    conn = FakeConn(rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.claim_retention_events(1, max_priority=2)

    sql = conn.sql[0]
    assert "ORDER BY e.priority ASC, e.id ASC" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "e.priority <= $7" in sql
    # A retry that is not due yet stays out of the batch.
    assert "next_attempt_at <= now()" in sql


async def test_claim_delay_window_and_lease_floor(monkeypatch):
    """The humanizing send delay is a per-event `id % span` offset, and the
    lease can never be zero — a 0-second lease would be expired the moment it
    is taken, so the reclaimer would hand every in-flight event to a second
    worker."""
    conn = FakeConn(rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.claim_retention_events(1, delay_min_sec=0, delay_max_sec=0,
                                    lease_sec=0)

    sql, args = conn.calls[0]
    assert "e.created_at <= now()" in sql and "$3 + (e.id % $4)" in sql
    assert args[2] == 0.0 and args[3] == 1  # modulo divisor is never 0
    assert args[4] == 1.0                   # lease floor


# ---------------------------------------------------------------------------
# Closing a lease
# ---------------------------------------------------------------------------
async def test_complete_closes_the_lease_for_good(monkeypatch):
    """Only the pipeline's success may stamp processed_at — that is what makes
    the stamp mean "handled" instead of "picked up"."""
    conn = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.complete_retention_event(99)

    sql, args = conn.executed[0]
    assert "status = 'done'" in sql and "processed_at = now()" in sql
    assert "locked_until = NULL" in sql and "last_error = NULL" in sql
    assert args == (99,)


async def test_fail_retries_with_exponential_backoff(monkeypatch):
    """A failed event goes back to 'pending' with a growing delay, keeps its
    attempt count, and records why — a bug that reset attempts (or dropped the
    backoff) turns one broken event into a hot loop against OpenAI."""
    conn = FakeConn(row={"status": "pending"})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    status = await db.fail_retention_event(4, "boom", max_attempts=5,
                                           backoff_base_sec=30)

    assert status == "pending"
    sql, args = conn.calls[0]
    assert "attempts >= $2 THEN 'dead' ELSE 'pending'" in sql
    assert "power(2," in sql and "GREATEST(attempts - 1, 0)" in sql
    assert "3600" in sql          # capped: a broken event may not spin
    assert "locked_until = NULL" in sql
    assert args == (4, 5, 30.0, "boom")


async def test_fail_dead_letters_at_the_ceiling(monkeypatch):
    """At the attempt ceiling the row becomes 'dead' with no next attempt and a
    processed_at stamp, so it leaves the queue (and the lag metric) and waits
    for a human in the dead-letter view instead of retrying forever."""
    conn = FakeConn(row={"status": "dead"})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.fail_retention_event(4, "boom") == "dead"

    sql = conn.sql[0]
    assert "processed_at = CASE WHEN attempts >= $2 THEN now()" in sql
    assert "next_attempt_at = CASE WHEN attempts >= $2 THEN NULL" in sql


async def test_fail_truncates_the_stored_error(monkeypatch):
    """A model traceback is not a reason to blow up the row (or the admin
    view) — last_error is bounded."""
    conn = FakeConn(row={"status": "pending"})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.fail_retention_event(4, "x" * 5000)

    assert len(conn.args[0][3]) == 2000


async def test_release_hands_events_back_without_a_penalty(monkeypatch):
    """Graceful shutdown / a foreign player lock: the batch is not a FAILURE,
    so the optimistic attempt increment from the claim is undone. Without that
    a rolling deploy would walk an event to its dead-letter ceiling in a few
    restarts without it ever having been tried."""
    conn = FakeConn(execute_result="UPDATE 2")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.release_retention_events([3, 1]) == 2

    sql, args = conn.executed[0]
    assert "status = 'pending'" in sql
    assert "attempts = GREATEST(attempts - 1, 0)" in sql
    assert "worker_id = NULL" in sql and "locked_until = NULL" in sql
    # Only rows this worker actually holds — never a row someone else re-took.
    assert "AND status = 'processing'" in sql
    assert args == ([3, 1],)


async def test_release_of_nothing_touches_no_database(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))
    assert await db.release_retention_events([]) == 0
    assert conn.calls == []


async def test_reclaim_picks_up_abandoned_leases(monkeypatch):
    """The backstop for a killed worker: an expired lease returns to the queue
    (or dead-letters if it has burned its attempts), bounded and SKIP LOCKED so
    two maintenance loops never fight over the same rows."""
    conn = FakeConn(execute_result="UPDATE 4")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.reclaim_expired_event_leases(limit=1000,
                                                 max_attempts=5) == 4

    sql, args = conn.executed[0]
    assert "status = 'processing' AND locked_until < now()" in sql
    assert "attempts >= $2 THEN 'dead' ELSE 'pending'" in sql
    assert "COALESCE(last_error, 'lease expired')" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert args == (1000, 5)


async def test_requeue_resets_attempts_and_clears_processed_at(monkeypatch):
    """The admin retry of a dead-lettered event. Leaving `processed_at` set (or
    the attempts at the ceiling) would make the requeued row un-claimable — the
    button would report success and nothing would ever run."""
    conn = FakeConn(execute_result="UPDATE 2")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.requeue_retention_events(7, [11, 12]) == 2

    sql, args = conn.executed[0]
    assert "status = 'pending'" in sql and "attempts = 0" in sql
    assert "processed_at = NULL" in sql and "next_attempt_at = NULL" in sql
    assert "locked_until = NULL" in sql
    # Scoped to the product AND to rows that are actually dead.
    assert "product_id = $1 AND status = 'dead'" in sql
    assert "id = ANY($2::bigint[])" in sql
    assert args == (7, [11, 12])


async def test_requeue_all_is_product_scoped_and_empty_is_a_no_op(monkeypatch):
    conn = FakeConn(execute_result="UPDATE 9")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.requeue_retention_events(7) == 9
    sql, args = conn.executed[0]
    assert "id = ANY" not in sql and args == (7,)

    # An explicit empty selection must not become "requeue everything".
    conn2 = FakeConn(execute_result="UPDATE 9")
    monkeypatch.setattr(db, "_pool", FakePool(conn2))
    assert await db.requeue_retention_events(7, []) == 0
    assert conn2.calls == []


# ---------------------------------------------------------------------------
# The one-time lifecycle backfill
# ---------------------------------------------------------------------------
def _batched_result(rows_per_batch: int):
    def _result(sql, args):
        if "SET status = 'done'" in sql:
            return f"UPDATE {rows_per_batch}"
        return "UPDATE 0"
    return _result


async def test_backfill_is_batched_and_stops_early(monkeypatch):
    """A short batch means the work is done — the loop must not keep running
    its `max_batches` anyway. Each batch is its own statement (and its own
    transaction in production) because this UPDATE can cover millions of rows:
    inside init_db's single 30s transaction it would time out and roll the boot
    back forever.
    """
    conn = FakeConn(execute_result=_batched_result(5))
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.backfill_event_lifecycle(batch=20_000, max_batches=50) == 5

    status_stmts = [(s, a) for s, a in conn.executed
                    if "SET status = 'done'" in s]
    assert len(status_stmts) == 1
    sql, args = status_stmts[0]
    assert "LIMIT $1" in sql and args == (20_000,)
    # Only rows the OLD drain finished with — never a genuinely queued event.
    assert "status = 'pending' AND processed_at IS NOT NULL" in sql


async def test_backfill_keeps_batching_while_batches_come_back_full(monkeypatch):
    conn = FakeConn(execute_result=_batched_result(10))
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.backfill_event_lifecycle(batch=10, max_batches=3) == 30

    assert sum("SET status = 'done'" in s for s, _ in conn.executed) == 3


async def test_backfill_lanes_only_the_live_queue(monkeypatch):
    """The lane repair runs over what is still queued and still in the default
    lane; touching finished rows would rewrite history for no benefit and
    unbound a statement whose cost is supposed to track queue depth."""
    conn = FakeConn(execute_result=_batched_result(0))
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.backfill_event_lifecycle()

    lane_sql = next(s for s, _ in conn.executed if "priority = CASE" in s)
    assert ("WHERE status = 'pending' AND processed_at IS NULL "
            "AND priority = 3") in lane_sql


# ---------------------------------------------------------------------------
# What the backpressure ladder reads
# ---------------------------------------------------------------------------
async def test_queue_lag_measures_only_untouched_events(monkeypatch):
    """The lag is about events NOBODY has picked up. Counting in-flight
    ('processing') or legacy rows would make the number grow while the drain is
    keeping up perfectly — and the ladder would shed lanes for nothing."""
    conn = FakeConn(val=42.7)
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.retention_queue_lag(3) == 42

    sql, args = conn.calls[0]
    assert "status = 'pending'" in sql and "processed_at IS NULL" in sql
    assert args == (3, 0.0, 1)


async def test_queue_lag_measures_overdue_ness_not_age(monkeypatch):
    """THE regression the first attempt at this fix shipped.

    Filtering not-yet-due rows out of the lag is only half of it: every row
    that survives the filter has by construction been waiting at least
    `delay_min` — 300s shipped, which IS the p3 rung — so reporting
    `now() - created_at` left the ladder pinned below lane 5 on every tick of
    every product with traffic, exactly as before. The number has to be how
    late a row is, and a row that just became claimable is 0 seconds late.
    """
    conn = FakeConn(val=0)
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.retention_queue_lag(3, delay_min_sec=300, delay_max_sec=900)

    sql, args = conn.calls[0]
    assert "MIN(created_at)" not in sql, "age, not overdue-ness"
    assert "now() - (created_at" in sql and "make_interval" in sql
    # The due filter is still there — an event not yet claimable is not lag.
    assert "next_attempt_at IS NULL OR next_attempt_at <= now()" in sql
    assert "created_at <= now() - make_interval(secs => $2 + (id % $3))" in sql
    # Same (lo, span) shape claim_retention_events uses for its jitter.
    assert args == (3, 300.0, 601)


async def test_queue_lag_never_reports_a_negative(monkeypatch):
    """Postgres returns the overdue expression per row; a clock skew or a row
    that came due mid-query must read as 'not late', never as a negative that
    would compare below every rung."""
    conn = FakeConn(val=-12.5)
    monkeypatch.setattr(db, "_pool", FakePool(conn))
    assert await db.retention_queue_lag(3, delay_min_sec=300) == 0


async def test_queue_lag_by_lane_reports_each_ceiling(monkeypatch):
    """The ladder needs the lag of the lanes it does NOT shed. One query, one
    MIN per ceiling — a second round trip per product per tick is not free."""
    conn = FakeConn(row={"lag2": 5.9, "lag3": 60.0, "lag5": 9_000.0})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.retention_queue_lag_by_lane(3) == {2: 5, 3: 60, 5: 9000}

    sql, _args = conn.calls[0]
    assert "FILTER (WHERE priority <= 2)" in sql
    assert "FILTER (WHERE priority <= 3)" in sql


# ---------------------------------------------------------------------------
# Replay safety: at-least-once delivery of an EVENT, at-most-once of a MESSAGE
# ---------------------------------------------------------------------------
async def test_a_replayed_event_stops_before_the_send(monkeypatch):
    """The reserved decision row is the only thing between a reclaimed lease
    and a second message to the player.

    A lease can expire (a hung model call, an OOM, a deploy), so the same event
    WILL reach `_process_event` twice. `insert_retention_v2_decision` returns
    None on the unique (product_id, event_pk) partial index; that None must
    abort the turn in FRONT of the send, the ledger write-back and the
    attribution row. The positive half runs first so the test cannot pass by
    breaking the pipeline outright.
    """
    from tests.test_retention_v2 import _cfg, _evt, _patch_guard_env, _ru

    _patch_guard_env(monkeypatch)
    reserved: list[int] = []
    sends: list[str] = []
    written_back: list[int] = []

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "message", "tone": "warm", "intent": "hi",
                 "reason": "r"}, 0.001)

    async def _send(product, ru, evt, decision, *, comfort, cfg,
                    decision_id=None):
        sends.append(str(evt.get("event_name")))
        return True, 0.002, None, {"session_id": "s-1"}

    async def _insert(product_id, **kw):
        # First pass reserves the row; the replay collides with the index.
        if reserved:
            return None
        reserved.append(1)
        return 1

    async def _update(decision_id, **kw):
        written_back.append(int(decision_id))

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(db, "insert_retention_v2_decision", _insert)
    monkeypatch.setattr(db, "update_retention_v2_decision", _update)
    monkeypatch.setattr(db, "log_admin_event", _noop)
    monkeypatch.setattr(db, "record_retention_outcome", _noop)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _send)

    cfg = _cfg(v2_dry_run=False)
    assert await retention_v2._process_event({"id": 1}, _evt(), cfg) == "sent"
    assert sends == ["deposit_confirmed"]

    # The replay.
    assert await retention_v2._process_event(
        {"id": 1}, _evt(), cfg) == "duplicate"
    assert sends == ["deposit_confirmed"]   # nothing new went out
    assert written_back == [1]              # and no second ledger write-back


async def test_a_replay_never_reaches_the_bonus_grant(monkeypatch):
    """The same guard has to sit in front of the OFFER too: granting a bonus
    twice for one event is money, not just noise. `do_offer_grant` is
    idempotent on its own key, but the reservation is what stops us calling the
    partner at all."""
    from tests.test_retention_v2 import _cfg, _evt, _patch_guard_env, _ru
    from app.retention import offers

    _patch_guard_env(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        assert "grant_offer" in guard["allowed_actions"]  # the offer is armed
        return ({"action": "grant_offer", "tone": "warm", "intent": "gift",
                 "reason": "r"}, 0.001)

    async def _resolve(product_id, ru, trigger_key, cfg, state):
        return ({"offer_key": "loss_back", "description": "10 free spins"},
                None)

    async def _duplicate(product_id, **kw):
        return None

    async def _boom(*a, **kw):
        raise AssertionError("a replayed event must not reach the partner")

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(db, "insert_retention_v2_decision", _duplicate)
    monkeypatch.setattr(offers, "offers_enabled", lambda cfg: True)
    monkeypatch.setattr(offers, "classify_offer_trigger",
                        lambda evt, state, cfg: "loss_high")
    monkeypatch.setattr(offers, "resolve_offer", _resolve)
    monkeypatch.setattr(offers, "do_offer_grant", _boom)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _boom)

    assert await retention_v2._process_event(
        {"id": 1}, _evt(), _cfg(v2_dry_run=False)) == "duplicate"


async def test_claim_excludes_a_player_already_in_flight(monkeypatch):
    """One player is never decided twice at once — ACROSS worker replicas.

    Grouping a claimed batch by player only serializes ONE worker's events.
    Two replicas (or the admin's «process queue now» running beside the sweep)
    claim independently, so without this the same player's two events land in
    two pipelines, read the same guard counters before either writes, and he
    gets two messages for what should have been one. Two guards in one
    statement: skip a player who already has an event in flight anywhere, and
    take a transaction-scoped advisory lock so two claims at the same instant
    cannot both pass that check.
    """
    conn = FakeConn(rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.claim_retention_events(1)

    sql = conn.sql[0]
    assert "NOT EXISTS" in sql
    assert "b.player_id = e.player_id" in sql
    assert "b.status = 'processing'" in sql
    assert "pg_try_advisory_xact_lock" in sql
    # The lock is applied to the LIMITed candidate set, not to every row the
    # scan touches — otherwise one claim could lock a whole product's backlog.
    assert sql.index("LIMIT $2") < sql.index("pg_try_advisory_xact_lock")
