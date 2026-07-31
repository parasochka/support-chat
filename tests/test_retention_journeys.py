"""Journey engine (DOC-6a): conditions, delays, enrollment, the blocked-step
semantics (frequency defers / terminal exits), exit-on-goal/return."""
from __future__ import annotations

import datetime as _dt

from app.core import db
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

    monkeypatch.setattr(db, "count_active_enrollments", _count)
    monkeypatch.setattr(db, "create_enrollment", _create)
    monkeypatch.setattr(db, "set_enrollment_next_step", _next)
    monkeypatch.setattr(db, "log_admin_event", _log)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is True
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is False

    async def _count_max(pid, player):
        return 3

    monkeypatch.setattr(db, "count_active_enrollments", _count_max)
    assert await journeys._enroll(PRODUCT, _ru(), _journey(), _cfg()) is False


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
    ch = await journeys._resolve_step_channel(1, step, _cfg())
    assert ch == "telegram"
