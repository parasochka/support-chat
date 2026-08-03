"""Queue SHAPE: which events enter the drain, how they are sharded, what is shed.

Three mechanics that decide whether the pipeline reacts to a deposit in seconds
or in an hour, and none of which is visible from a single event's happy path:

  - the INGEST lanes — high-volume "state food" is stored complete and never
    enters the drain, so queue depth tracks decision work instead of casino
    traffic;
  - the per-player sharding of a claimed batch — parallel across players,
    strictly serial within one;
  - the backpressure ladder — as the lag grows the drain stops claiming the
    cheap lanes so transactional events keep moving.
"""
from __future__ import annotations

from app.core import config
from app.core import db
from app.retention import player_sync
from app.retention import retention_v2
from tests.conftest import FakeConn, FakePool


def _bypass_cfg(**over):
    cfg = {"queue_bypass_state_events": True, "v2_loss_high_usd": 0}
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------------------
# Lanes at ingest
# ---------------------------------------------------------------------------
def test_event_priority_lanes():
    """Money and identity ride P1, progression P2, state food P5. The lane is
    stamped at ingest because the drain serves lanes with no join — a wrong
    lane here is a deposit queued behind a spin."""
    assert player_sync.event_priority("deposit_confirmed") == 1
    assert player_sync.event_priority("kyc_rejected") == 1
    assert player_sync.event_priority("level_up") == 2
    assert player_sync.event_priority("bonus_completed") == 2
    assert player_sync.event_priority("kyc_started") == 3
    assert player_sync.event_priority("bet_settled") == \
        player_sync.STATE_FOOD_PRIORITY
    assert player_sync.event_priority("session_started") == \
        player_sync.STATE_FOOD_PRIORITY
    # An event added to the taxonomy without a lane lands in the DEFAULT one —
    # processed, just not ahead of P1. Silently demoting it to state food would
    # drop it out of the drain entirely.
    assert player_sync.event_priority("newly_invented_event") == 3


def test_state_food_never_enters_the_drain():
    """A spin and a session ping exist to move counters and feed the state
    resolver, both of which read the stored row. Letting them into the queue
    buries the events that deserve a reaction and makes the depth a function of
    casino traffic."""
    cfg = _bypass_cfg()
    assert player_sync.should_queue("session_started", cfg) is False
    assert player_sync.should_queue("session_ended", cfg) is False
    assert player_sync.should_queue("xp_granted", cfg) is False
    assert player_sync.should_queue("deposit_initiated", cfg) is False
    # …while everything above the state-food lane always does.
    assert player_sync.should_queue("deposit_confirmed", cfg) is True
    assert player_sync.should_queue("kyc_started", cfg) is True


def test_bet_settled_enters_only_while_a_loss_threshold_is_configured():
    """`bet_settled` is the one high-volume event that can turn into a reaction
    (the 24h loss window). With no threshold configured that reaction is
    impossible, so queueing every settled bet would be pure noise; with one, a
    bypassed bet_settled would make the loss/comfort path dead code."""
    assert player_sync.should_queue("bet_settled", _bypass_cfg()) is False
    assert player_sync.should_queue(
        "bet_settled", _bypass_cfg(v2_loss_high_usd=100.0)) is True
    # A garbage threshold must not throw at ingest.
    assert player_sync.should_queue(
        "bet_settled", _bypass_cfg(v2_loss_high_usd="nonsense")) is False


def test_an_operator_listed_event_always_reaches_the_drain():
    """`v2_decision_events` is how an operator promotes a lane deliberately;
    the bypass must never override that choice."""
    cfg = _bypass_cfg(v2_decision_events=["session_started"])
    assert player_sync.should_queue("session_started", cfg) is True
    # …and an event NOT on the operator's list stays bypassed.
    assert player_sync.should_queue("xp_granted", cfg) is False


def test_every_decision_event_reaches_the_drain():
    """The agent can only react to what it is handed. Demoting a decision event
    into the state-food lane would silently stop every reaction to it — no
    error, no queue depth, just a bot that went quiet.

    Asserted on the LANE, not on should_queue: with `v2_decision_events` unset
    should_queue consults DECISION_EVENTS itself, so checking it against the
    same set is a tautology that survives demoting every one of them."""
    for name in sorted(retention_v2.DECISION_EVENTS):
        assert player_sync.event_priority(name) < \
            player_sync.STATE_FOOD_PRIORITY, name
    # And with the operator's list unset, the drain really does take them.
    cfg = _bypass_cfg(v2_loss_high_usd=100.0)
    for name in sorted(retention_v2.DECISION_EVENTS):
        assert player_sync.should_queue(name, cfg) is True, name


def test_the_bypass_switch_puts_everything_back_in():
    """`queue_bypass_state_events` off = the pre-bypass behaviour, verbatim: a
    product that wants to react to spins can have it (at the cost of depth)."""
    cfg = {"queue_bypass_state_events": False}
    for name in sorted(player_sync.CANONICAL_EVENTS):
        assert player_sync.should_queue(name, cfg) is True, name


async def test_a_bypassed_event_is_stored_complete(monkeypatch):
    """"Bypassed" must mean STORED AND CLOSED, not dropped: the state resolver
    reads these rows. `queue=False` writes status 'done' with processed_at in
    the same INSERT, so the row can never be claimed and never pollutes the lag
    metric."""
    conn = FakeConn(row={"id": 12})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.ingest_retention_event(
        3, event_id="e1", event_name="bet_settled", player_id="p1",
        ts=None, payload={}, priority=5, queue=False)

    sql, args = conn.calls[0]
    assert "CASE WHEN $10 = 'done' THEN now() ELSE NULL END" in sql
    assert args[8] == 5 and args[9] == "done"

    conn2 = FakeConn(row={"id": 13})
    monkeypatch.setattr(db, "_pool", FakePool(conn2))
    await db.ingest_retention_event(
        3, event_id="e2", event_name="deposit_confirmed", player_id="p1",
        ts=None, payload={}, priority=1, queue=True)
    assert conn2.args[0][9] == "pending"


async def test_activity_debounce_spares_the_hot_row_but_never_deposits(
        monkeypatch):
    """The activity bridge is the busiest write in the system (one hot-row
    UPDATE per spin), and re-stamping "active" three seconds later buys nothing
    — every reader works on scales of hours. A DEPOSIT timestamp is the
    exception: the loss/recency maths keys on it directly, so it is always
    exact.
    """
    touches: list[tuple] = []

    async def _ingest(product_id, **kw):
        return 1

    async def _touch(product_id, player_id, field, ts, debounce_sec=0):
        touches.append((field, debounce_sec))
        return 1

    async def _noop(*a, **kw):
        return 0

    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "touch_retention_activity", _touch)
    monkeypatch.setattr(db, "update_retention_profile", _noop)
    monkeypatch.setattr(db, "arm_deposit_initiated", _noop)
    monkeypatch.setattr(db, "clear_deposit_initiated_by_player", _noop)

    cfg = _bypass_cfg(activity_debounce_sec=120)
    for i, name in enumerate(("bet_settled", "session_started",
                              "deposit_confirmed")):
        await player_sync.ingest_event(
            1, {"event_id": f"e{i}", "event_name": name, "player_id": "p1"},
            cfg=cfg)

    assert touches == [("last_played_at", 120), ("last_login_at", 120),
                       ("last_deposit_at", 0)]


async def test_the_debounce_is_enforced_in_sql(monkeypatch):
    """Skipping the write has to happen in the statement's WHERE, not in
    Python: two workers (or two events a second apart) both read a stale row,
    so a client-side check would let the hot-row update through anyway."""
    conn = FakeConn(execute_result="UPDATE 0")
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.touch_retention_activity(1, "p1", "last_played_at",
                                      "2026-07-20T10:00:00+00:00",
                                      debounce_sec=60)

    sql, args = conn.executed[0]
    assert "$4::float8 <= 0" in sql               # 0 = never debounce
    assert "make_interval(secs => $4::float8)" in sql
    # Forward-only, and EVERY parameter in an expression carries an explicit
    # cast. Uncast, `$3 - make_interval(...)` resolves $3 to the other operand's
    # type (interval) and Postgres rejects the statement at PREPARE time — on
    # every call, debouncing or not. asyncpg is stubbed here, so only the shape
    # is checkable; scripts/check_queue_sql.py runs it against a real server.
    assert ("GREATEST(COALESCE(last_played_at, $3::timestamptz), "
            "$3::timestamptz)") in sql
    assert "$3::timestamptz" in sql.split("make_interval")[0].split("WHERE")[1]
    assert args[3] == 60.0


# ---------------------------------------------------------------------------
# Per-player sharding of a claimed batch
# ---------------------------------------------------------------------------
def test_groups_are_per_player_and_kept_in_id_order():
    """Two concurrent decisions for ONE player read the same guard counters
    before either writes — that is how one player collects two messages for two
    events that should have produced one. Within a chain the order must be the
    arrival order, or a reaction assumes an event that has not run yet."""
    events = [{"id": 5, "player_id": "a"}, {"id": 2, "player_id": "b"},
              {"id": 3, "player_id": "a"}, {"id": 9, "player_id": "b"},
              {"id": 1, "player_id": "a"}]

    groups = retention_v2._group_by_player(events)

    assert {g[0]["player_id"]: [e["id"] for e in g] for g in groups} == {
        "a": [1, 3, 5], "b": [2, 9]}
    # Every claimed event ends up in exactly one chain — a batch member lost
    # here is a lease nobody closes.
    assert sorted(e["id"] for g in groups for e in g) == [1, 2, 3, 5, 9]


def test_events_without_a_player_share_one_chain():
    """No player id = no way to tell whether two events are the same person, so
    they run serially. Fanning them out would be the unsafe direction."""
    groups = retention_v2._group_by_player(
        [{"id": 2, "player_id": None}, {"id": 1, "player_id": ""}])
    assert len(groups) == 1 and [e["id"] for e in groups[0]] == [1, 2]


def test_grouping_an_empty_batch_is_empty():
    assert retention_v2._group_by_player([]) == []


# ---------------------------------------------------------------------------
# The backpressure ladder
# ---------------------------------------------------------------------------
def test_the_ladder_sheds_the_cheap_lanes_as_the_lag_grows():
    """A backlog is not an excuse to fall behind on what a player is waiting
    for: state food goes first, then the default lane."""
    cfg = {"queue_degrade_p3_sec": 300, "queue_degrade_p2_sec": 900}
    ladder = [(0, 5), (299, 5), (300, 3), (899, 3), (900, 2)]
    assert [retention_v2._max_priority_for_lag(lag, cfg)
            for lag, _ in ladder] == [want for _, want in ladder]


def test_transactional_lanes_are_never_shed():
    """P1/P2 keep moving at any lag — the ladder protects them, it does not
    include them. A rung that returned 1 (or 0) would stall deposits exactly
    when the queue is worst."""
    cfg = {"queue_degrade_p3_sec": 300, "queue_degrade_p2_sec": 900}
    assert retention_v2._max_priority_for_lag(10 ** 9, cfg) == 2


def test_the_thresholds_come_from_the_config():
    """The rungs are hot knobs, not constants: a product that tolerates a
    longer lag must be able to say so without a redeploy."""
    tight = {"queue_degrade_p3_sec": 30, "queue_degrade_p2_sec": 60}
    assert retention_v2._max_priority_for_lag(30, tight) == 3
    assert retention_v2._max_priority_for_lag(60, tight) == 2
    # …and the same lag under the wide config is still healthy.
    wide = {"queue_degrade_p3_sec": 3000, "queue_degrade_p2_sec": 9000}
    assert retention_v2._max_priority_for_lag(60, wide) == 5


def test_an_unset_config_falls_back_to_the_deploy_defaults():
    """A cfg missing the keys (an older override set) must not read 0 and shed
    every lane at a lag of zero."""
    assert retention_v2._max_priority_for_lag(0, {}) == 5
    assert retention_v2._max_priority_for_lag(
        config.RETENTION_QUEUE_DEGRADE_P3_SEC, {}) == 3
    assert retention_v2._max_priority_for_lag(
        config.RETENTION_QUEUE_DEGRADE_P2_SEC, {}) == 2
