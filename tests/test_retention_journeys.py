"""Journey engine (DOC-6a): conditions, delays, enrollment, the blocked-step
semantics (frequency defers / terminal exits), exit-on-goal/return."""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import channels
from app.retention import journeys


def _cfg(**over):
    base = {
        "journeys_enabled": True, "journeys_dry_run_default": True,
        "journey_step_sweep_interval_sec": 300,
        "journey_max_active_per_player": 3,
        "v2_dry_run": False,
        "smart_send_time_enabled": False,
        "holdout_pct": 0, "rg_enabled": False,
        "quiet_hours_utc_offset": 0,
    }
    base.update(over)
    return base


def _iso_ago(hours: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=hours)).isoformat()


def _ru(**over):
    base = {"id": 10, "product_id": 1, "player_id": "p1", "subscribed": True,
            "pings_muted": False, "unreachable": False,
            "last_login_at": _iso_ago(24 * 10),
            "last_played_at": _iso_ago(24 * 10),
            "last_deposit_at": _iso_ago(24 * 20)}
    base.update(over)
    return base


def _journey(**over):
    base = {"id": 1, "journey_key": "jk", "name": "J", "version": 1,
            "status": "active",
            "trigger": {"type": "scheduled",
                        "match": {"dormancy_cohort": "dormant_d10"}},
            "entry_conditions": [], "exit_conditions": [],
            "steps": [{"step_id": 1, "type": "send_message",
                       "channel": "telegram", "intent": "hi",
                       "priority": 3}],
            "dry_run": False, "priority": 3, "metadata": {}}
    base.update(over)
    return base


PRODUCT = {"id": 1, "name": "Casino"}


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------
def test_parse_delay():
    assert journeys.parse_delay("24h") == _dt.timedelta(hours=24)
    assert journeys.parse_delay("30m") == _dt.timedelta(minutes=30)
    assert journeys.parse_delay("2d") == _dt.timedelta(days=2)
    assert journeys.parse_delay(None) == _dt.timedelta(0)
    assert journeys.parse_delay("garbage") == _dt.timedelta(0)


def test_eval_conditions():
    state = {"dormancy_cohort": "dormant_d10", "idle_days": 11,
             "rfm": {"score": 3}}
    assert journeys.eval_conditions([], state) is True
    assert journeys.eval_conditions(
        [{"field": "dormancy_cohort", "operator": "eq",
          "value": "dormant_d10"}], state) is True
    assert journeys.eval_conditions(
        [{"field": "idle_days", "operator": "gte", "value": 14}],
        state) is False
    assert journeys.eval_conditions(
        [{"field": "rfm.score", "operator": "lte", "value": 3}],
        state) is True
    # Unresolvable field -> None (fail-safe), never True/False.
    assert journeys.eval_conditions(
        [{"field": "daily_card.day_1_status", "operator": "eq",
          "value": "x"}], state) is None
    assert journeys.eval_conditions(
        [{"field": "idle_days", "operator": "in", "value": [11, 12]}],
        state) is True


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------
async def test_enroll_idempotent_and_capped(monkeypatch):
    created = []

    async def _count(pid, player):
        return 0

    async def _create(pid, **kw):
        if created:
            return None  # unique index: already actively enrolled
        created.append(kw)
        return 55

    async def _next(pid, eid, next_step_at):
        pass

    async def _log(*a, **kw):
        pass

    async def _last(pid, player, journey_key):
        return None  # never enrolled before

    monkeypatch.setattr(db, "count_active_enrollments", _count)
    monkeypatch.setattr(db, "create_enrollment", _create)
    monkeypatch.setattr(db, "set_enrollment_next_step", _next)
    monkeypatch.setattr(db, "log_admin_event", _log)
    monkeypatch.setattr(db, "last_enrollment_at", _last)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is True
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is False

    async def _count_max(pid, player):
        return 3

    monkeypatch.setattr(db, "count_active_enrollments", _count_max)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is False


def test_the_reentry_cooldown_comes_from_the_trigger_shape():
    """A scheduled journey re-derives its candidates from live state, so a
    finished enrollment matches again on the very next sweep. The gap defaults
    from what the trigger actually means; a journey may state its own."""
    cohort = _journey()                       # dormancy_cohort, days wide
    weekly = _journey(trigger={"type": "scheduled",
                               "match": {"day_of_week": "wed"}})
    cashier = _journey(trigger={"type": "scheduled",
                                "match": {"deposit_initiated_older_than_h": 2}})
    evt = _journey(trigger={"type": "event",
                            "event_name": "deposit_confirmed"})

    assert journeys._reentry_cooldown_days(weekly) == 7
    assert journeys._reentry_cooldown_days(cashier) == 1
    assert journeys._reentry_cooldown_days(cohort) > 1
    # An event journey is already gated by a real event arriving.
    assert journeys._reentry_cooldown_days(evt) == 0
    # An explicit value always wins, including 0 = the pre-fix behaviour.
    assert journeys._reentry_cooldown_days(
        _journey(metadata={"reentry_cooldown_days": 3})) == 3
    assert journeys._reentry_cooldown_days(
        dict(weekly, metadata={"reentry_cooldown_days": 0})) == 0


async def test_a_finished_enrollment_is_not_recreated_next_sweep(monkeypatch):
    """The one-active-enrollment index is PARTIAL (status='active'), so it says
    nothing about a journey the player already completed. Without the cooldown
    the sweep re-enrolled — and re-sent — every couple of minutes for the whole
    cohort window."""
    created = []

    async def _count(pid, player):
        return 0

    async def _create(pid, **kw):
        created.append(kw)
        return 55

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(db, "count_active_enrollments", _count)
    monkeypatch.setattr(db, "create_enrollment", _create)
    monkeypatch.setattr(db, "set_enrollment_next_step", _noop)
    monkeypatch.setattr(db, "log_admin_event", _noop)

    async def _just_now(pid, player, journey_key):
        return _iso_ago(0.05)

    monkeypatch.setattr(db, "last_enrollment_at", _just_now)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is False
    assert created == []

    # …and once the cooldown has elapsed the player is eligible again.
    async def _long_ago(pid, player, journey_key):
        return _iso_ago(24 * 365)

    monkeypatch.setattr(db, "last_enrollment_at", _long_ago)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is True
    assert len(created) == 1


async def test_event_matching_enrolls_on_conditions(monkeypatch):
    enrolled = []

    async def _list(pid, trigger_type=None):
        return [_journey(trigger={"type": "event",
                                  "event_name": "deposit_confirmed"},
                         entry_conditions=[{"field": "user_status",
                                            "operator": "eq",
                                            "value": "active"}])]

    async def _enroll(product, ru, j, cfg):
        enrolled.append(j["journey_key"])
        return True

    monkeypatch.setattr(db, "list_active_journeys", _list)
    monkeypatch.setattr(journeys, "_enroll", _enroll)
    n = await journeys.match_event_journeys(
        PRODUCT, {"event_name": "deposit_confirmed"}, _ru(),
        {"user_status": "active"}, _cfg())
    assert n == 1 and enrolled == ["jk"]
    # Wrong event name / failed condition -> no enrollment.
    n = await journeys.match_event_journeys(
        PRODUCT, {"event_name": "level_up"}, _ru(),
        {"user_status": "active"}, _cfg())
    assert n == 0
    n = await journeys.match_event_journeys(
        PRODUCT, {"event_name": "deposit_confirmed"}, _ru(),
        {"user_status": "dormant"}, _cfg())
    assert n == 0


async def test_journeys_disabled_no_matching(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("no journey reads when disabled")

    monkeypatch.setattr(db, "list_active_journeys", _boom)
    assert await journeys.match_event_journeys(
        PRODUCT, {"event_name": "deposit_confirmed"}, _ru(), {},
        _cfg(journeys_enabled=False)) == 0


# ---------------------------------------------------------------------------
# Step semantics (Б4)
# ---------------------------------------------------------------------------
def _wire_drain(monkeypatch, *, journey, enr, ru, state,
                step_outcome=None):
    from app.retention import retention_v2

    finished = []
    scheduled = []
    logged = []
    advanced = []

    async def _due(pid, limit=50):
        return [enr]

    async def _get_j(pid, key, version=None):
        return journey

    async def _get_ru(pid, rid):
        return ru

    async def _get_ru_p(pid, player):
        return ru

    async def _state(pid, r, cfg):
        return state

    async def _finish(pid, eid, status, reason=None):
        finished.append((status, reason))

    async def _next(pid, eid, next_step_at):
        scheduled.append(next_step_at)

    async def _log_step(pid, eid, sid, outcome, decision_id, detail):
        logged.append(outcome)

    async def _advance_db(pid, eid, current_step):
        advanced.append(current_step)

    async def _log(*a, **kw):
        pass

    monkeypatch.setattr(db, "due_enrollments", _due)
    monkeypatch.setattr(db, "get_journey", _get_j)
    monkeypatch.setattr(db, "get_retention_user_by_id", _get_ru)
    monkeypatch.setattr(db, "get_retention_user_by_player", _get_ru_p)
    monkeypatch.setattr(retention_v2, "resolve_player_state", _state)
    monkeypatch.setattr(db, "finish_enrollment", _finish)
    monkeypatch.setattr(db, "set_enrollment_next_step", _next)
    monkeypatch.setattr(db, "log_journey_step", _log_step)
    monkeypatch.setattr(db, "advance_enrollment", _advance_db)
    monkeypatch.setattr(db, "log_admin_event", _log)
    if step_outcome is not None:
        async def _exec(product, r, e, j, step, st, cfg):
            return step_outcome

        monkeypatch.setattr(journeys, "_execute_step", _exec)
    return finished, scheduled, logged, advanced


def _enr(**over):
    base = {"id": 5, "journey_key": "jk", "journey_version": 1,
            "current_step": 0, "player_id": "p1", "retention_user_id": 10,
            "enrolled_at": _iso_ago(48), "status": "active"}
    base.update(over)
    return base


async def test_frequency_block_defers_step(monkeypatch):
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=_journey(), enr=_enr(), ru=_ru(),
        state={}, step_outcome=("deferred_frequency", "daily_cap_reached"))
    stats = await journeys.drain_due_steps(PRODUCT, _cfg())
    assert stats["deferred"] == 1
    assert logged == ["deferred_frequency"]
    assert scheduled and not finished  # rescheduled, journey alive


async def test_terminal_block_exits_journey(monkeypatch):
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=_journey(), enr=_enr(), ru=_ru(),
        state={}, step_outcome=("blocked_terminal", "rg_permanent_self_exclude"))
    stats = await journeys.drain_due_steps(PRODUCT, _cfg())
    assert stats["exited"] == 1
    assert finished == [("exited_terminal", "rg_permanent_self_exclude")]


async def test_exit_on_goal_before_step(monkeypatch):
    journey = _journey(exit_conditions=[{"field": "user_status",
                                         "operator": "eq",
                                         "value": "active"}])
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=journey, enr=_enr(), ru=_ru(),
        state={"user_status": "active"}, step_outcome=("sent", None))
    await journeys.drain_due_steps(PRODUCT, _cfg())
    assert finished == [("exited_goal", "exit_conditions_met")]
    assert not logged  # the step never ran


async def test_exit_on_return_for_scheduled_journey(monkeypatch):
    # Activity AFTER enrollment -> exited_return for a scheduled journey.
    ru = _ru(last_login_at=_iso_ago(1))
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=_journey(), enr=_enr(enrolled_at=_iso_ago(48)),
        ru=ru, state={}, step_outcome=("sent", None))
    await journeys.drain_due_steps(PRODUCT, _cfg())
    assert finished == [("exited_return", "player_returned")]


async def test_event_journey_ignores_return_by_default(monkeypatch):
    journey = _journey(trigger={"type": "event",
                                "event_name": "deposit_confirmed"})
    ru = _ru(last_login_at=_iso_ago(1))
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=journey, enr=_enr(enrolled_at=_iso_ago(48)),
        ru=ru, state={}, step_outcome=("sent", None))
    stats = await journeys.drain_due_steps(PRODUCT, _cfg())
    assert stats["executed"] == 1 and not finished or \
        finished == [("completed", None)]


async def test_step_condition_skip_and_unresolvable(monkeypatch):
    journey = _journey(steps=[
        {"step_id": 1, "type": "send_message", "channel": "telegram",
         "conditions": [{"field": "idle_days", "operator": "gte",
                         "value": 100}], "on_skip": "exit"}])
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=journey, enr=_enr(), ru=_ru(),
        state={"idle_days": 5}, step_outcome=("sent", None))
    await journeys.drain_due_steps(PRODUCT, _cfg())
    assert logged == ["skipped_condition"]
    assert finished == [("skipped_exit", "step_condition_exit")]
    # Unresolvable per-step condition -> blocked_unresolvable, fail-safe exit.
    journey2 = _journey(steps=[
        {"step_id": 1, "type": "send_message", "channel": "telegram",
         "conditions": [{"field": "daily_card.status", "operator": "eq",
                         "value": "x"}]}])
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=journey2, enr=_enr(), ru=_ru(),
        state={}, step_outcome=("sent", None))
    await journeys.drain_due_steps(PRODUCT, _cfg())
    assert logged == ["blocked_unresolvable"]
    assert finished == [("exited_terminal", "blocked_unresolvable")]


async def test_completed_after_last_step(monkeypatch):
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=_journey(), enr=_enr(), ru=_ru(),
        state={}, step_outcome=("sent", None))
    stats = await journeys.drain_due_steps(PRODUCT, _cfg())
    assert stats["executed"] == 1
    assert finished == [("completed", None)]  # single-step journey done


async def test_channel_unavailable_no_crash(monkeypatch):
    # An email step before multichannel: no fallback -> channel_unavailable.
    journey = _journey(steps=[{"step_id": 1, "type": "send_message",
                               "channel": "email"}])
    finished, scheduled, logged, advanced = _wire_drain(
        monkeypatch, journey=journey, enr=_enr(), ru=_ru(), state={})
    stats = await journeys.drain_due_steps(PRODUCT, _cfg())
    assert logged == ["channel_unavailable"]
    assert stats["executed"] == 1  # advanced past, journey completed
    # With a telegram fallback the step resolves to telegram.
    step = {"step_id": 1, "channel": "email", "channel_fallback": "telegram"}
    ch = await journeys._resolve_step_channel(1, _ru(), step, _cfg())
    assert ch == "telegram"


async def test_a_step_never_resolves_to_a_channel_the_player_refused():
    """STRICT OPT-IN is the module's hard rule and it was unenforced: the step
    channel was resolved from the PRODUCT's enabled channels alone, so a player
    with email_opt_in=false still got marketing email."""
    cfg = _cfg(multichannel_enabled=True, channel_auto_priority="telegram,email")
    step = {"step_id": 1, "channel": "email"}

    async def _rows(product_id):
        return [{"channel": "email", "enabled": True}]
    channels._cfg_cache.clear()
    channels._cfg_cache[1] = (float("inf"), [{"channel": "email",
                                              "enabled": True}])
    try:
        consented = _ru(email="p@example.com", email_opt_in=True,
                        email_verified=True)
        assert await journeys._resolve_step_channel(1, consented, step,
                                                    cfg) == "email"

        refused = _ru(email="p@example.com", email_opt_in=False)
        assert await journeys._resolve_step_channel(1, refused, step,
                                                    cfg) is None
        # Not even as a fallback, and not by falling through to telegram.
        with_fb = dict(step, channel_fallback="email")
        assert await journeys._resolve_step_channel(1, refused, with_fb,
                                                    cfg) is None
    finally:
        channels._cfg_cache.clear()


# ---------------------------------------------------------------------------
# What the WRITER is actually told (the guard verdict is not just allow/deny)
# ---------------------------------------------------------------------------
def _wire_step(monkeypatch, *, guard, grant_status=None, offer=None):
    """Drive `_execute_step` with the guard/offer/channel seams faked, and
    capture the brief the persona would be handed."""
    from app.retention import offers
    from app.retention import retention_v2
    sent = {}

    async def _guard(pid, ru, evt, state, cfg):
        return guard

    async def _route(product_id, ru, cfg, **kw):
        return "telegram"

    async def _send(product, ru, cfg, *, channel, intent, journey_key,
                    step_id, priority=3, comfort=False):
        sent.update({"intent": intent, "comfort": comfort,
                     "channel": channel})
        return True, None, 99

    async def _resolve(pid, ru, key, cfg):
        return (offer or {"offer_key": key, "description": "50 free spins"},
                None)

    async def _grant(product, ru, offer_row, ref, cfg):
        return {"status": grant_status, "offer_grant_id": "og_x"}

    async def _intent(pid, step):
        return step.get("intent") or "mention the free spins credited"

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(retention_v2, "guard_check", _guard)
    monkeypatch.setattr(channels, "route_channel", _route)
    monkeypatch.setattr(channels, "send_journey_touch", _send)
    monkeypatch.setattr(offers, "resolve_offer_by_key", _resolve)
    monkeypatch.setattr(offers, "do_offer_grant", _grant)
    monkeypatch.setattr(db, "get_offer_by_key", _noop)
    monkeypatch.setattr(db, "link_offer_grant_decision", _noop)
    from app.retention import scenario_library
    monkeypatch.setattr(scenario_library, "step_intent", _intent)
    return sent


async def test_a_conditional_guard_pass_carries_its_constraints_to_the_writer(
        monkeypatch):
    """A conditional RG verdict ALLOWS the touch only without play/bonus talk,
    and the loss-comfort window only in an empathetic register. Reading
    `allow` and discarding `constraints` sent an RG-restricted player the
    step's unmodified marketing brief."""
    rg = ("RG protection: no play invitations, no deposit suggestions, "
          "no bonus/promo talk, no game links")
    sent = _wire_step(monkeypatch, guard={
        "allow": True, "reasons": [], "allowed_actions": ["message"],
        "constraints": [rg], "comfort": True})

    outcome, _detail = await journeys._execute_step(
        PRODUCT, _ru(), _enr(), _journey(),
        {"step_id": 1, "type": "send_message", "channel": "telegram",
         "intent": "invite them to the weekend race"},
        {}, _cfg())

    assert outcome == "sent"
    assert rg in sent["intent"]
    assert sent["comfort"] is True


async def test_a_held_grant_never_leaves_the_bonus_promise_in_the_brief(
        monkeypatch):
    """`fraud_hold` means nothing was credited, but the step's TEMPLATE brief
    is the thing that promises the gift and it is unchanged by the grant
    failing. Shipped verbatim it tells the player about free spins that do not
    exist — the one rail this stack must never break."""
    sent = _wire_step(monkeypatch, guard={
        "allow": True, "reasons": [], "allowed_actions": ["message"],
        "constraints": [], "comfort": False}, grant_status="fraud_hold")

    outcome, _detail = await journeys._execute_step(
        PRODUCT, _ru(), _enr(), _journey(),
        {"step_id": 1, "type": "grant_offer", "channel": "telegram",
         "offer_key": "fs50"},
        {}, _cfg())

    assert outcome == "sent"          # the note still goes out…
    intent = sent["intent"].lower()
    assert "do not mention" in intent  # …but without the bonus
    assert "no bonus, gift, free spins or promotion was credited" in intent


async def test_a_granted_offer_still_says_the_gift_is_real(monkeypatch):
    """The other half of the same rail: order is create -> partner confirms ->
    only THEN the persona may mention it."""
    sent = _wire_step(monkeypatch, guard={
        "allow": True, "reasons": [], "allowed_actions": ["message"],
        "constraints": [], "comfort": False}, grant_status="granted")

    outcome, _detail = await journeys._execute_step(
        PRODUCT, _ru(), _enr(), _journey(),
        {"step_id": 1, "type": "grant_offer", "channel": "telegram",
         "offer_key": "fs50"},
        {}, _cfg())

    assert outcome == "offer_granted"
    assert "JUST credited" in sent["intent"]
    assert "do NOT mention" not in sent["intent"]
