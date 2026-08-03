"""The SEND stage: a leased delivery queue shaped by a Postgres token bucket.

Deciding and sending used to be one loop, so ten thousand queued touches were
seven minutes during which the decision pipeline reacted to nothing, and a send
that failed was retried by nobody. The send worker fixes both — but only if
every claimed row is CLOSED (sent / failed / rescheduled / released), if a full
bucket parks a touch instead of burning its attempts, and if a blocked bot is
final rather than retried forever against a player who cannot receive anything.

These exercise `run_product_sends` end to end with the surrounding seams faked,
plus the token bucket's own SQL through the shared fake pool from conftest.
"""
from __future__ import annotations

import asyncio

from app.core import config
from app.core import db
from app.core import settings
from app.retention import outcomes
from app.retention import retention_v2
from app.retention import send_worker
from tests.conftest import FakeConn, FakePool


def _cfg(**over):
    base = {
        "send_worker_enabled": True, "send_batch_size": 25,
        "send_lease_sec": 90, "send_concurrency": 4,
        "worker_product_concurrency": 4,
        "telegram_rate_per_sec": 25.0, "telegram_burst": 25.0,
        "telegram_chat_rate_per_sec": 1.0, "email_rate_per_sec": 50.0,
        # The RG feed is exercised on its own below; off here so the fixture
        # does not need the whole responsible-gaming stack.
        "rg_enabled": False,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {"id": 5, "player_id": "p1", "tg_user_id": 555, "subscribed": True,
            "pings_muted": False, "unreachable": False}
    base.update(over)
    return base


def _row(**over):
    base = {"id": 11, "retention_user_id": 5, "decision_id": 4,
            "channel": "telegram", "attempts": 1, "priority": 3,
            "payload": {"event_name": "deposit_confirmed", "action": "message",
                        "tone": "celebrate", "gen_cost": 0.004,
                        "retention_user_id": 5}}
    base.update(over)
    return base


def _patch_send_stage(monkeypatch, *, rows, deliver=None, tokens=True,
                      ru=None, cfg=None):
    """Fake every seam around the send loop and record what it did."""
    rec: dict[str, list] = {"claimed": [], "sent": [], "failed": [],
                            "rescheduled": [], "released": [], "outcomes": [],
                            "decisions": [], "scopes": [], "delivered": []}
    cfg = cfg or _cfg()
    the_ru = _ru() if ru is None else ru

    async def _claim(product_id, *, limit, lease_sec, worker_id,
                     max_priority=5):
        rec["claimed"].append({"product_id": product_id, "limit": limit,
                               "lease_sec": lease_sec, "worker_id": worker_id})
        # A real queue hands rows out ONCE — the pass keeps claiming until it
        # comes back empty (that is what makes the channel's rate limit the
        # send rate instead of the worker's tick), so a fake that returns the
        # same rows forever would just run the pass out to its deadline.
        if len(rec["claimed"]) > 1:
            return []
        return list(rows)

    async def _get_ru(product_id, rid):
        return the_ru

    async def _take(scope, *, rate_per_sec, burst, n=1.0):
        rec["scopes"].append(scope)
        return tokens(scope) if callable(tokens) else tokens

    async def _deliver(product, ru_arg, payload, cfg_arg):
        rec["delivered"].append(payload)
        if deliver is None:
            return True, None, {"session_id": "s-1"}
        return deliver(payload)

    async def _record(product_id, ru_arg, **kw):
        rec["outcomes"].append(dict(kw, product_id=product_id))
        return 77

    async def _sent(pk, *, provider_ref=None, outcome_id=None):
        rec["sent"].append((pk, outcome_id))

    async def _failed(pk, reason, *, permanent=False, backoff_sec=None):
        rec["failed"].append({"id": pk, "reason": reason,
                              "permanent": permanent,
                              "backoff_sec": backoff_sec})

    async def _reschedule(pk, *, delay_sec):
        rec["rescheduled"].append((pk, delay_sec))

    async def _release(pks):
        rec["released"].append(list(pks))
        return len(pks)

    async def _decision(decision_id, **kw):
        rec["decisions"].append((decision_id, kw))

    monkeypatch.setattr(settings, "retention", lambda: cfg)
    monkeypatch.setattr(db, "claim_deliveries", _claim)
    monkeypatch.setattr(db, "get_retention_user_by_id", _get_ru)
    monkeypatch.setattr(db, "take_rate_token", _take)
    monkeypatch.setattr(db, "mark_delivery_sent", _sent)
    monkeypatch.setattr(db, "mark_delivery_failed", _failed)
    monkeypatch.setattr(db, "reschedule_delivery", _reschedule)
    monkeypatch.setattr(db, "release_deliveries", _release)
    monkeypatch.setattr(db, "update_retention_v2_decision", _decision)
    monkeypatch.setattr(retention_v2, "deliver_payload", _deliver)
    monkeypatch.setattr(outcomes, "record", _record)
    return rec


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------
async def test_a_delivered_touch_is_marked_sent_and_attributed_once(
        monkeypatch):
    """One delivered row -> the lease closes as 'sent', EXACTLY ONE attribution
    row opens, and the decision that queued it is marked delivered.

    Attribution is what every "which touch performs" surface reads, and the
    send stage is now the only place that knows a queued decision actually
    reached the player — a second row (or none) silently corrupts every cut.
    """
    rec = _patch_send_stage(monkeypatch, rows=[_row()])

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats == {"sent": 1, "failed": 0, "deferred": 0, "batches": 1}
    assert rec["sent"] == [(11, 77)]
    assert len(rec["outcomes"]) == 1
    outcome = rec["outcomes"][0]
    assert outcome["kind"] == "proactive"
    assert outcome["decision_id"] == 4
    assert outcome["event_name"] == "deposit_confirmed"
    assert outcome["action"] == "message" and outcome["tone"] == "celebrate"
    # The generation cost was paid when the touch was WRITTEN, in another pass;
    # it rides in the payload so cost-per-outcome survives the split.
    assert outcome["cost_usd"] == 0.004
    assert rec["decisions"] == [(4, {"delivered": True, "detail": "sent"})]
    assert rec["failed"] == [] and rec["released"] == []


async def test_the_claim_reads_the_hot_batch_and_lease_knobs(monkeypatch):
    """Batch size and lease are runtime knobs: a stuck send stage has to be
    tunable without a redeploy, and a lease shorter than a slow Telegram call
    means two workers deliver the same touch."""
    rec = _patch_send_stage(monkeypatch, rows=[],
                            cfg=_cfg(send_batch_size=7, send_lease_sec=45))

    assert await send_worker.run_product_sends({"id": 3}) == {}

    claim = rec["claimed"][0]
    assert claim["product_id"] == 3 and claim["limit"] == 7
    assert claim["lease_sec"] == 45 and claim["worker_id"]


# ---------------------------------------------------------------------------
# Rate shaping
# ---------------------------------------------------------------------------
async def test_an_empty_bucket_reschedules_instead_of_failing(monkeypatch):
    """A full bucket is pacing, not a failure.

    Marking it failed would burn one of the three retry attempts of a touch
    that was never even attempted (and eventually dead-letter a perfectly good
    message during a broadcast); sleeping on the claim instead would hold a
    lease — a worker slot doing nothing — while 10k touches drain at 25/s.
    """
    # The real wait is seconds — a row genuinely tries for a token
    # before giving up. Shorten it so the suite does not pay for it.
    monkeypatch.setattr(send_worker, "_TOKEN_WAIT_SEC", 0.01)
    rec = _patch_send_stage(monkeypatch, rows=[_row()], tokens=False)

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats == {"sent": 0, "failed": 0, "deferred": 1, "batches": 1}
    assert rec["rescheduled"] == [(11, send_worker._BUCKET_RETRY_SEC)]
    assert rec["failed"] == [] and rec["sent"] == []
    assert rec["delivered"] == []   # nothing reached the transport
    assert rec["outcomes"] == []    # and nothing was attributed


async def test_the_per_chat_bucket_defers_too(monkeypatch):
    """Telegram throttles a single conversation at roughly one message a
    second, so a burst that respects only the per-bot rate still trips it
    whenever two touches for one player land together."""
    rec = _patch_send_stage(monkeypatch, rows=[_row()],
                            tokens=lambda scope: not scope.startswith("tg:chat"))

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats["deferred"] == 1
    assert rec["scopes"] == ["tg:1", "tg:chat:555"]
    assert rec["delivered"] == []


async def test_delegated_channels_are_shaped_too(monkeypatch):
    """Push and in-app are POSTs at the CASINO's delivery endpoint, so they get
    a bucket like every other channel.

    Unshaped, a 10k broadcast is a denial of service against our own partner —
    who then throttles or drops us, and the touches are lost on their side
    where we cannot even retry them. The per-chat bucket is Telegram-only:
    there is no chat here, and the partner paces per player themselves."""
    scopes: list[str] = []

    async def _take(scope, *, rate_per_sec, burst, n=1.0):
        scopes.append(scope)
        return True

    monkeypatch.setattr(db, "take_rate_token", _take)
    assert await send_worker._take_tokens(1, "push", None, _cfg()) is True
    assert await send_worker._take_tokens(1, "in_app", 555, _cfg()) is True
    assert scopes == ["push:1", "in_app:1"]
    # vip_host is a task in a queue, never a message on a wire.
    scopes.clear()
    assert await send_worker._take_tokens(1, "vip_host", None, _cfg()) is True
    assert scopes == []


async def test_email_rides_its_own_bucket(monkeypatch):
    scopes: list[str] = []

    async def _take(scope, *, rate_per_sec, burst, n=1.0):
        scopes.append(scope)
        return True

    monkeypatch.setattr(db, "take_rate_token", _take)
    assert await send_worker._take_tokens(9, "email", None, _cfg()) is True
    assert scopes == ["email:9"]


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
async def test_a_blocked_bot_is_permanent_and_never_retried(monkeypatch):
    """Retrying a player who blocked the bot burns the product's send budget
    forever on a message that can never arrive."""
    rec = _patch_send_stage(
        monkeypatch, rows=[_row()],
        deliver=lambda payload: (False, "403 Forbidden: bot was blocked", {}))

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats == {"sent": 0, "failed": 1, "deferred": 0, "batches": 1}
    assert rec["failed"][0]["permanent"] is True
    assert rec["sent"] == [] and rec["outcomes"] == []
    # The ledger row learns why, so the admin sees a reason and not a silence.
    assert rec["decisions"][0][0] == 4
    assert "send_failed" in rec["decisions"][0][1]["detail"]


async def test_an_unreachable_player_is_permanent(monkeypatch):
    """The delivery seam flags the player unreachable on its way out; the send
    stage must honour that flag even when the failure detail is not a 403."""
    rec = _patch_send_stage(
        monkeypatch, rows=[_row()], ru=_ru(unreachable=True),
        deliver=lambda payload: (False, "chat not found", {}))

    await send_worker.run_product_sends({"id": 1})

    assert rec["failed"][0]["permanent"] is True


async def test_a_transient_failure_backs_off_and_stays_retryable(monkeypatch):
    """A provider blip must not be terminal — the row keeps its place in the
    queue with a delay."""
    rec = _patch_send_stage(
        monkeypatch, rows=[_row(attempts=2)],
        deliver=lambda payload: (False, "500 internal error", {}))

    await send_worker.run_product_sends({"id": 1})

    assert rec["failed"][0]["permanent"] is False
    assert rec["failed"][0]["backoff_sec"] == 300  # the 2nd rung of the ladder


def test_the_backoff_ladder_advances_with_the_attempt_count():
    """The attempt count is stamped by the CLAIM, so the ladder walks forward
    on its own; a flat delay would hammer a failing channel and an unbounded
    one would park a touch past its usefulness."""
    assert send_worker._backoff_for({"attempts": 1}) == 60
    assert send_worker._backoff_for({"attempts": 2}) == 300
    assert send_worker._backoff_for({"attempts": 3}) == 1800
    assert send_worker._backoff_for({"attempts": 99}) == 1800  # capped
    # A row with no (or a nonsense) count starts at the first rung rather than
    # indexing off the end of the ladder.
    assert send_worker._backoff_for({}) == 60
    assert send_worker._backoff_for({"attempts": 0}) == 60


async def test_a_row_whose_send_explodes_is_closed_not_leased(monkeypatch):
    """One bad row must not wedge the queue — and must not sit leased until it
    expires either. The guard closes it as a failure."""
    def _explode(payload):
        raise RuntimeError("transport exploded")

    rec = _patch_send_stage(monkeypatch, rows=[_row(), _row(id=12)],
                            deliver=_explode)

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats["failed"] == 2
    assert sorted(f["id"] for f in rec["failed"]) == [11, 12]
    assert "transport exploded" in rec["failed"][0]["reason"]
    assert rec["released"] == []  # every claim was accounted for


async def test_a_missing_player_link_is_permanent(monkeypatch):
    """A delivery whose player row is gone can never be sent; retrying it is a
    queue slot spent on nothing."""
    rec = _patch_send_stage(monkeypatch, rows=[_row()])

    async def _no_ru(product_id, rid):
        return None

    monkeypatch.setattr(db, "get_retention_user_by_id", _no_ru)
    await send_worker.run_product_sends({"id": 1})

    assert rec["failed"][0]["permanent"] is True
    assert rec["delivered"] == []


# ---------------------------------------------------------------------------
# What may change between DECIDING and SENDING
# ---------------------------------------------------------------------------
async def test_consent_is_rechecked_at_send_time(monkeypatch):
    """The guards ran when the touch was decided, which is minutes ago — long
    enough for the player to have muted or blocked the bot. Sending anyway
    because "it was allowed when we wrote it" is exactly the failure a queue
    introduces."""
    rec = _patch_send_stage(monkeypatch, rows=[_row()],
                            ru=_ru(pings_muted=True))

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats["failed"] == 1
    assert rec["delivered"] == []
    assert rec["failed"][0]["permanent"] is True
    assert rec["failed"][0]["reason"] == "opted_out_before_send"


async def test_a_responsible_gaming_flag_suppresses_a_queued_touch(monkeypatch):
    """RG is the compliance half of the same re-check: a player the casino
    flagged between the decision and the send must not receive the queued
    message."""
    from app.retention import rg_guard

    rec = _patch_send_stage(monkeypatch, rows=[_row()])

    async def _gate(product_id, ru, trigger, cfg):
        return {"deny": True, "reason": "self_exclude"}

    monkeypatch.setattr(rg_guard, "gate", _gate)
    await send_worker.run_product_sends({"id": 1})

    assert rec["delivered"] == []
    assert rec["failed"][0]["reason"] == "rg:self_exclude"
    assert rec["failed"][0]["permanent"] is True


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
async def test_unfinished_claims_are_released_on_shutdown(monkeypatch):
    """A deploy lands mid-batch: everything claimed but not closed goes STRAIGHT
    back to 'queued' rather than waiting out its lease. Without this the touches
    of the batch are invisible for the whole lease window — and the lease
    reclaimer, the backstop, runs on the maintenance cadence."""
    stop = asyncio.Event()

    def _deliver_then_stop(payload):
        # The deploy lands while the batch is in flight: the first row goes
        # out, the flag rises, and the rest must not be left leased.
        stop.set()
        return True, None, {"session_id": "s-1"}

    rec = _patch_send_stage(monkeypatch, rows=[_row(), _row(id=12)],
                            deliver=_deliver_then_stop)

    stats = await send_worker.run_product_sends({"id": 1}, stop=stop)

    assert stats["sent"] == 1
    assert rec["released"] == [[12]], rec["released"]


async def test_a_release_failure_never_escapes_the_sweep(monkeypatch):
    """The lease reclaimer is the backstop, so a failing release must not take
    the whole product's pass down with it."""
    stop = asyncio.Event()

    def _stop_before_sending(payload):
        stop.set()
        return True, None, {"session_id": "s-1"}

    _patch_send_stage(monkeypatch, rows=[_row(), _row(id=12)],
                      deliver=_stop_before_sending)

    async def _boom(pks):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "release_deliveries", _boom)

    stats = await send_worker.run_product_sends({"id": 1}, stop=stop)
    assert stats["sent"] == 1  # the pass returned instead of raising


async def test_an_empty_queue_costs_one_claim(monkeypatch):
    rec = _patch_send_stage(monkeypatch, rows=[])
    assert await send_worker.run_product_sends({"id": 1}) == {}
    assert len(rec["claimed"]) == 1


async def test_one_products_failure_does_not_stop_the_others(monkeypatch):
    """Products drain concurrently and independently. The old single-lock,
    single-loop shape made one casino's outage everyone's outage; a pass that
    aborted on the first exception would bring that back."""
    monkeypatch.setattr(settings, "retention", lambda: _cfg())

    async def _products():
        return [{"id": 1}, {"id": 2}]

    async def _one(product, *, limit=None, stop=None):
        if int(product["id"]) == 1:
            raise RuntimeError("product 1 is broken")
        return {"sent": 3, "failed": 0, "deferred": 0}

    monkeypatch.setattr(db, "list_retention_products", _products)
    monkeypatch.setattr(send_worker, "run_product_sends", _one)

    totals = await send_worker.run_due_sends()

    assert totals["products"] == 1 and totals["sent"] == 3


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------
def test_the_send_worker_ships_off():
    """The whole stage is a rollout flag: with it off `_send_touch` sends
    inline exactly as before and this loop finds nothing to do."""
    assert config.RETENTION_SEND_WORKER_ENABLED is False


def test_a_stored_false_actually_switches_the_worker_off(monkeypatch):
    """Read through `global_retention_bool`, not the int reader: the int one
    collapses a stored 0/False into its default, which for a switch means the
    operator turns it off in the admin and nothing happens."""
    monkeypatch.setattr(settings, "retention",
                        lambda: {"send_worker_enabled": False})
    assert send_worker.send_worker_enabled() is False
    monkeypatch.setattr(settings, "retention",
                        lambda: {"send_worker_enabled": True})
    assert send_worker.send_worker_enabled() is True
    # Unset falls back to the deploy default.
    monkeypatch.setattr(settings, "retention", lambda: {})
    assert send_worker.send_worker_enabled() is \
        config.RETENTION_SEND_WORKER_ENABLED


# ---------------------------------------------------------------------------
# The delivery queue's own statements (what "never retried" actually means)
# ---------------------------------------------------------------------------
async def test_a_permanent_failure_leaves_the_queue_for_good(monkeypatch):
    """`permanent` has to clear next_attempt_at AND be honoured by the claim.
    Setting only the flag would leave the row due forever and the worker would
    re-pick it every pass."""
    conn = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.mark_delivery_failed(11, "403 blocked", permanent=True)

    sql, args = conn.executed[0]
    assert "status = 'failed'" in sql and "locked_until = NULL" in sql
    assert "next_attempt_at = CASE WHEN $3 THEN NULL" in sql
    assert args == (11, "403 blocked", True, 60.0)


async def test_the_claim_skips_permanently_failed_rows(monkeypatch):
    """The other half: a failed row is retryable by default (that is how the
    backoff ladder works), so the permanent ones must be filtered out here."""
    conn = FakeConn(rows=[])
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.claim_deliveries(1, limit=5, lease_sec=60, worker_id="w-1")

    sql = conn.sql[0]
    assert "status IN ('queued', 'failed')" in sql
    assert "NOT permanent_fail" in sql
    assert "scheduled_at <= now()" in sql
    assert "next_attempt_at <= now()" in sql
    # Best lane first, and never the same row as another worker.
    assert "ORDER BY priority ASC, scheduled_at ASC" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "status = 'sending'" in sql and "attempts = attempts + 1" in sql


async def test_reschedule_costs_the_touch_no_attempt(monkeypatch):
    """The bucket deferral undoes the claim's optimistic attempt increment —
    otherwise a broadcast would walk its own touches up the backoff ladder
    (and into the permanent-failure count) purely by being paced."""
    conn = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.reschedule_delivery(11, delay_sec=1.0)

    sql, args = conn.executed[0]
    assert "status = 'queued'" in sql and "locked_until = NULL" in sql
    assert "attempts = GREATEST(attempts - 1, 0)" in sql
    assert "scheduled_at = now() + make_interval" in sql
    assert args == (11, 1.0)


async def test_mark_sent_closes_the_lease_and_keeps_the_outcome_link(
        monkeypatch):
    """The outcome id is written back onto the delivery row so a touch can be
    traced to its attribution — and the retry fields are cleared, or a sent row
    would still look due."""
    conn = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.mark_delivery_sent(11, outcome_id=77)

    sql, args = conn.executed[0]
    assert "status = 'sent'" in sql and "locked_until = NULL" in sql
    assert "outcome_id = COALESCE($3, outcome_id)" in sql
    assert "next_attempt_at = NULL" in sql
    assert args == (11, None, 77)


async def test_release_only_touches_rows_still_in_flight(monkeypatch):
    """Shutdown hands back what this worker holds. Without the 'sending' guard
    a slow release could resurrect a row another worker already delivered."""
    conn = FakeConn(execute_result="UPDATE 2")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.release_deliveries([11, 12]) == 2

    sql, args = conn.executed[0]
    assert "AND status = 'sending'" in sql
    assert "attempts = GREATEST(attempts - 1, 0)" in sql
    assert args == ([11, 12],)
    # …and releasing nothing is not a query.
    conn2 = FakeConn()
    monkeypatch.setattr(db, "_pool", FakePool(conn2))
    assert await db.release_deliveries([]) == 0
    assert conn2.calls == []


async def test_enqueueing_the_same_touch_twice_is_a_no_op(monkeypatch):
    """The send-side half of at-least-once safety: the caller derives
    `delivery_id` from the decision, so a replayed decision lands on the unique
    (product_id, delivery_id) and enqueues nothing instead of queueing a second
    copy of the same message."""
    conn = FakeConn(row=None)   # ON CONFLICT DO NOTHING returned no row
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    enqueued = await db.enqueue_delivery(
        1, "d-abc", player_id="p1", retention_user_id=5, channel="telegram",
        decision_id=4, payload={"text": "hi"})

    assert enqueued is None
    sql = conn.sql[0]
    assert "ON CONFLICT (product_id, delivery_id) DO NOTHING" in sql
    assert "'queued'" in sql


# ---------------------------------------------------------------------------
# The token bucket itself
# ---------------------------------------------------------------------------
async def test_take_rate_token_is_one_statement(monkeypatch):
    """Refill and take in ONE atomic statement, with the state in Postgres.

    A read-then-write pair races between workers, and an in-process limiter
    multiplies by the replica count exactly when a broadcast is hammering the
    channel — which is the 429 storm this bucket exists to prevent.
    """
    conn = FakeConn(row={"tokens": 4.0})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.take_rate_token("tg:1", rate_per_sec=25.0,
                                    burst=25.0) is True

    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert "INSERT INTO retention_rate_budget" in sql
    assert "ON CONFLICT (scope) DO UPDATE" in sql
    # The refill is derived from elapsed time, so there is no timer to run…
    assert "EXTRACT(EPOCH FROM now() - retention_rate_budget.updated_at)" in sql
    # …and the take only applies when the refilled balance covers it.
    assert "WHERE retention_rate_budget.tokens" in sql
    assert args == ("tg:1", 25.0, 25.0, 1.0)


async def test_an_empty_bucket_answers_false(monkeypatch):
    """No row back = the WHERE refused the take. That is the caller's cue to
    reschedule, not an error."""
    conn = FakeConn(row=None)
    monkeypatch.setattr(db, "_pool", FakePool(conn))
    assert await db.take_rate_token("tg:1", rate_per_sec=25.0,
                                    burst=25.0) is False


async def test_an_unlimited_scope_never_touches_the_database(monkeypatch):
    """rate <= 0 means "no bucket for this scope". It must short-circuit: a
    zero rate reaching the statement would refill nothing and refuse every
    take — turning "unlimited" into "never send"."""
    conn = FakeConn(row=None)
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    assert await db.take_rate_token("tg:1", rate_per_sec=0,
                                    burst=10.0) is True
    assert await db.take_rate_token("tg:1", rate_per_sec=-1.0,
                                    burst=10.0) is True
    assert conn.calls == []


async def test_the_bucket_is_never_smaller_than_one_take(monkeypatch):
    """A burst below the take size would make the bucket permanently unable to
    hold enough tokens, and nothing on that scope would ever send."""
    conn = FakeConn(row={"tokens": 0.0})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.take_rate_token("tg:chat:5", rate_per_sec=1.0, burst=0.0)

    assert conn.args[0][1] == 1.0


async def test_a_queued_touch_stamps_last_ping_at_immediately(monkeypatch):
    """The min-gap guard reads last_ping_at, and the ping ledger only stamps it
    on an actual SEND. Queued, that send is minutes away — long enough for the
    player's next event to pass the gap check and decide a second touch. The
    enqueue path must move the clock itself; deleting that call restores the
    double-messaging the queue split introduced.
    """
    from app.chat import chat_service
    from app.retention import retention_v2

    stamped: list[int] = []
    enqueued: list[dict] = []

    async def _stamp(rid):
        stamped.append(int(rid))

    async def _enqueue(pid, delivery_id, **kw):
        enqueued.append({"delivery_id": delivery_id, **kw})
        return 1

    async def _token(pid):
        return "tok"

    async def _session(pid, ru, lang):
        return {"id": "s-1"}

    async def _ping(*a, **kw):
        return chat_service.PingDraft(text="hi", lang="ru", photo_id=None,
                                      ai_meta={"cost_usd": 0.004})

    async def _touch_history(*a, **kw):
        return []

    monkeypatch.setattr(db, "touch_last_ping", _stamp)
    monkeypatch.setattr(db, "enqueue_delivery", _enqueue)
    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention_v2.retention, "_ensure_session", _session)
    monkeypatch.setattr(retention_v2.retention, "_user_context_from_ru",
                        lambda ru: {})
    monkeypatch.setattr(retention_v2.retention, "fallback_media_caption",
                        lambda *a, **kw: "")
    monkeypatch.setattr(retention_v2.retention, "resolve_user_lang",
                        lambda ru: "ru")
    monkeypatch.setattr(chat_service, "generate_retention_ping", _ping)
    monkeypatch.setattr(retention_v2, "_touch_history", _touch_history)
    monkeypatch.setattr(retention_v2.settings, "global_retention_bool",
                        lambda key, default: True)   # send worker ON

    async def _boom(*a, **kw):
        raise AssertionError("a queued touch must not send inline")
    monkeypatch.setattr(retention_v2, "deliver_payload", _boom)

    delivered, cost, detail, _facts = await retention_v2._send_touch(
        {"id": 1}, {"id": 10, "player_id": "p1", "tg_user_id": 5},
        {"id": 77, "event_name": "deposit_confirmed", "priority": 1,
         "payload": {}},
        {"action": "message", "intent": "hi", "tone": "warm"},
        comfort=False, cfg={})

    assert detail == "queued" and delivered is False
    assert stamped == [10], "the gap clock must move at ENQUEUE, not at send"
    assert len(enqueued) == 1
    # The generation's accounting rides along, or a send in another process
    # loses the ai_interaction_logs row for a call that was already billed.
    assert enqueued[0]["payload"]["ai_meta"]["cost_usd"] == 0.004
    assert enqueued[0]["payload"]["tone"] == "warm"


async def test_a_burst_is_paced_by_the_channel_not_by_the_worker_tick(monkeypatch):
    """THE regression this stage exists to prevent.

    The pass used to claim one batch per worker tick, which silently made the
    send rate `send_batch_size / worker_interval_sec` — 50 rows every 30s, or
    1.7/s, while the token bucket allowed 30/s. A 10k broadcast took an hour
    and a half instead of six minutes, and no metric said why. The pass must
    keep claiming while there is work, so the bucket is the only pacer.
    """
    queue = [_row(id=i) for i in range(1, 251)]
    handed: list[int] = []

    async def _claim(product_id, *, limit, lease_sec, worker_id,
                     max_priority=5):
        chunk = queue[:limit]
        del queue[:limit]
        handed.append(len(chunk))
        return chunk

    _patch_send_stage(monkeypatch, rows=[])
    monkeypatch.setattr(db, "claim_deliveries", _claim)

    stats = await send_worker.run_product_sends({"id": 1})

    assert stats["sent"] == 250, stats
    assert not queue, "the pass must drain the queue, not one batch of it"
    # 250 rows at the configured batch size of 50 = 5 claims + the empty one
    # that ends the pass. One claim would mean the tick is still the pacer.
    # More than one claim in a single pass is the whole point: one claim would
    # mean the worker tick is still the pacer.
    assert len(handed) > 1, handed
    assert stats["batches"] == len([h for h in handed if h]), handed


async def test_a_pass_hands_the_slot_back_at_its_budget(monkeypatch):
    """Draining until empty must not let one product's burst starve the rest:
    the pass stops at `send_pass_max_sec` and the next tick resumes, because
    the queue is durable."""
    async def _claim(product_id, *, limit, lease_sec, worker_id,
                     max_priority=5):
        return [_row(id=1)]

    _patch_send_stage(monkeypatch, rows=[])
    monkeypatch.setattr(db, "claim_deliveries", _claim)
    monkeypatch.setattr(
        send_worker.settings, "global_retention_int",
        lambda key, default, lo, hi: 0 if key == "send_pass_max_sec" else default)

    stats = await send_worker.run_product_sends({"id": 1})
    # An endless queue: without the budget this never returns.
    assert stats["batches"] == 1, stats
