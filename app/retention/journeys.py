"""Journey Engine — declarative multi-step trajectories (DOC-6a).

A journey is DATA (a `retention_journeys` row): a trigger (event or
scheduled), entry conditions, an ordered list of steps (message / grant_offer
/ wait, each with a delay, a channel, per-step conditions), and exit
conditions. The runtime here:

  - ENROLLS players (event triggers in the event pipeline, scheduled triggers
    in the sweep), idempotently — one active pass per (player, journey).
  - DRAINS due steps from the same worker sweep (the delay lives in
    `next_step_at`; no external scheduler), executing each step through the
    FULL guard chain — a journey is a planner of touches, never a privileged
    channel past RG / holdout / frequency / comfort.
  - EXITS on goal (exit conditions), on return (activity after enrollment for
    scheduled journeys), and on terminal guard verdicts.

Blocked-step semantics (owner-approved, Б4): a FREQUENCY block (cap, gap,
burst, budget, cooldown) defers the step to the next sweep pass — the touch
is not lost; a TERMINAL block (RG, opt-out, unsubscribed, blocked bot,
holdout) exits the journey — there is no point queueing touches at a player
who must not receive them.

Steps reference templates (DOC-6b) by `template_key`; until the library
lands / when a key is missing, the step's inline `intent` is the persona
brief. Channel is a mandatory step field from day one; before DOC-7 only
'telegram' is executable — an unexecutable channel falls back to
`channel_fallback` or logs `channel_unavailable` without crashing the pass.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import time
from typing import Any, Optional

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

_last_sweep: dict[int, float] = {}

TERMINAL_GUARD_REASONS = frozenset({
    "not_subscribed", "player_opted_out", "bot_blocked_by_player",
    "held_out",
})

# A frequency-shaped block defers the step by this much (next sweep pass will
# retry; min-gap class blocks clear on their own timescale).
_FREQUENCY_RETRY = _dt.timedelta(hours=2)

_DELAY_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def journeys_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("journeys_enabled")
    return config.RETENTION_JOURNEYS_ENABLED if v is None else bool(v)


def parse_delay(value: Any) -> _dt.timedelta:
    """'24h' / '30m' / '2d' / '90s' -> timedelta (0 on absent/garbage)."""
    if not value:
        return _dt.timedelta(0)
    m = _DELAY_RE.match(str(value))
    if not m:
        return _dt.timedelta(0)
    n, unit = int(m.group(1)), m.group(2).lower()
    return _dt.timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours",
                             "d": "days"}[unit]: n})


def eval_conditions(conditions: Any, state: dict[str, Any]
                    ) -> Optional[bool]:
    """Evaluate a condition list against a state snapshot.

    Returns True/False, or None when ANY referenced field is absent from the
    snapshot — the fail-safe contract: an unresolvable condition never
    matches AND never silently passes (the caller decides: no enrollment / a
    blocked_unresolvable step)."""
    if not conditions:
        return True
    for cond in conditions:
        field = str(cond.get("field") or "")
        op = str(cond.get("operator") or "eq")
        want = cond.get("value")
        cur: Any = state
        for part in field.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        if op == "exists":
            ok = cur is not None
        elif op == "eq":
            ok = cur == want
        elif op == "ne":
            ok = cur != want
        elif op in ("gt", "gte", "lt", "lte"):
            try:
                a, b = float(cur), float(want)
            except (TypeError, ValueError):
                return None
            ok = {"gt": a > b, "gte": a >= b,
                  "lt": a < b, "lte": a <= b}[op]
        elif op == "in":
            ok = cur in (want or [])
        elif op == "not_in":
            ok = cur not in (want or [])
        else:
            return None
        if not ok:
            return False
    return True


def _steps_of(journey: dict[str, Any]) -> list[dict[str, Any]]:
    steps = journey.get("steps") or []
    return steps if isinstance(steps, list) else []


async def _schedule_step(product_id: int, ru: dict[str, Any],
                         enrollment_id: int, journey: dict[str, Any],
                         step_index: int, cfg: dict[str, Any]) -> None:
    """Stamp next_step_at for a step: its own delay + (optionally) the Smart
    Send Time shift for non-urgent steps."""
    steps = _steps_of(journey)
    if step_index >= len(steps):
        return
    step = steps[step_index]
    earliest = (_dt.datetime.now(_dt.timezone.utc)
                + parse_delay(step.get("delay")))
    hint_hour: Optional[int] = None
    send_time = str(step.get("send_time") or "")
    m = re.match(r"^(\d{1,2})", send_time)
    if m:
        hint_hour = int(m.group(1)) % 24
    try:
        from app.retention import frequency
        shifted = await frequency.next_send_time(
            product_id, ru, cfg, touch_type="journey_step",
            hint_hour=hint_hour, earliest=earliest)
        if shifted is not None:
            earliest = shifted
    except Exception:  # noqa: BLE001 - SST is an optimization, never a blocker
        pass
    await db.set_enrollment_next_step(product_id, enrollment_id,
                                      next_step_at=earliest)


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------
async def _enroll(product: dict[str, Any], ru: dict[str, Any],
                  journey: dict[str, Any], cfg: dict[str, Any]) -> bool:
    pid = int(product["id"])
    max_active = int(cfg.get("journey_max_active_per_player")
                     or config.RETENTION_JOURNEY_MAX_ACTIVE_PER_PLAYER)
    active = await db.count_active_enrollments(pid,
                                               str(ru.get("player_id") or ""))
    if active >= max_active:
        return False
    enrollment_id = await db.create_enrollment(
        pid, player_id=str(ru.get("player_id") or ""),
        retention_user_id=int(ru["id"]) if ru.get("id") else None,
        journey_key=str(journey["journey_key"]),
        journey_version=int(journey.get("version") or 1))
    if enrollment_id is None:
        return False  # already actively enrolled (idempotent)
    await _schedule_step(pid, ru, enrollment_id, journey, 0, cfg)
    await db.log_admin_event(
        None, "journey_enrolled",
        {"journey": journey["journey_key"], "player_id": ru.get("player_id")},
        product_id=pid)
    return True


async def match_event_journeys(product: dict[str, Any], evt: dict[str, Any],
                               ru: dict[str, Any], state: dict[str, Any],
                               cfg: dict[str, Any]) -> int:
    """Event-triggered enrollment (runs inside _process_event, after the
    single-reaction pipeline). Returns enrollments made."""
    if not journeys_enabled(cfg):
        return 0
    enrolled = 0
    try:
        for j in await db.list_active_journeys(int(product["id"]),
                                               trigger_type="event"):
            trig = j.get("trigger") or {}
            if str(trig.get("event_name") or "") != evt.get("event_name"):
                continue
            if eval_conditions(j.get("entry_conditions"), state) is not True:
                continue
            if await _enroll(product, ru, j, cfg):
                enrolled += 1
    except Exception:  # noqa: BLE001 - matching must never wedge the pipeline
        log.exception("journey_event_matching_failed product=%s",
                      product.get("id"))
    return enrolled


async def match_scheduled_journeys(product: dict[str, Any],
                                   cfg: dict[str, Any], *,
                                   limit: int = 100) -> int:
    """Scheduled-trigger enrollment (runs in the sweep): recovery by cohort,
    weekly by day-of-week, cashier abandonment by timer."""
    from app.retention import retention_v2
    pid = int(product["id"])
    enrolled = 0
    now = _dt.datetime.now(_dt.timezone.utc)
    for j in await db.list_active_journeys(pid, trigger_type="scheduled"):
        match = (j.get("trigger") or {}).get("match") or {}
        dow = match.get("day_of_week")
        if dow and now.strftime("%a").lower()[:3] != str(dow).lower()[:3]:
            continue
        try:
            candidates = await db.journey_scheduled_candidates(pid, match,
                                                               limit=limit)
        except Exception:  # noqa: BLE001
            log.exception("journey_candidates_failed product=%s journey=%s",
                          pid, j.get("journey_key"))
            continue
        for ru in candidates:
            state = await retention_v2.resolve_player_state(pid, ru, cfg)
            if eval_conditions(j.get("entry_conditions"), state) is not True:
                continue
            if await _enroll(product, ru, j, cfg):
                enrolled += 1
                # Abandonment: one comeback per initiated-deposit attempt.
                if match.get("deposit_initiated_older_than_h") is not None:
                    await db.clear_deposit_initiated(pid, int(ru["id"]))
    return enrolled


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------
async def _resolve_step_channel(product_id: int, step: dict[str, Any],
                                cfg: dict[str, Any]) -> Optional[str]:
    """The executable channel for a step. Before multichannel: telegram or
    the fallback; a non-executable channel returns None (logged, no crash)."""
    from app.retention import channels
    wanted = str(step.get("channel") or "telegram")
    executable = await channels.executable_channels(product_id, cfg)
    if wanted in executable:
        return wanted
    fallback = str(step.get("channel_fallback") or "")
    if fallback and fallback in executable:
        return fallback
    return None


async def _execute_step(product: dict[str, Any], ru: dict[str, Any],
                        enr: dict[str, Any], journey: dict[str, Any],
                        step: dict[str, Any], state: dict[str, Any],
                        cfg: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Run one due step through the guards. Returns (outcome, detail)."""
    from app.retention import offers
    from app.retention import retention_v2

    pid = int(product["id"])
    step_type = str(step.get("type") or "send_message")
    if step_type == "wait":
        return "waited", None

    channel = await _resolve_step_channel(pid, step, cfg)
    if channel is None:
        return "channel_unavailable", str(step.get("channel"))

    synthetic_evt = {"id": None,
                     "event_name": f"journey:{journey['journey_key']}"}
    guard = await retention_v2.guard_check(pid, ru, synthetic_evt, state, cfg)
    if not guard["allow"]:
        reasons = set(guard.get("reasons") or [])
        if reasons & TERMINAL_GUARD_REASONS or any(
                str(r).startswith("rg_") for r in reasons):
            return "blocked_terminal", "; ".join(sorted(reasons))
        return "deferred_frequency", "; ".join(sorted(reasons))

    dry = bool(journey.get("dry_run")) or bool(cfg.get("v2_dry_run"))

    grant = None
    if step_type == "grant_offer":
        offer_key = str(step.get("offer_key") or "")
        offer, deny = await offers.resolve_offer_by_key(pid, ru, offer_key,
                                                        cfg)
        if offer is None:
            return "blocked_guard", deny or "offer_unresolvable"
        grant = await offers.do_offer_grant(
            product, ru, offer, f"j{enr['id']}s{step.get('step_id')}", cfg)
        status = str(grant.get("status"))
        if status == "dry_run":
            return "dry_run", "offer:dry_run"
        if status == "fraud_hold":
            grant = None  # send without any bonus mention
        elif status != "granted":
            return "blocked_guard", f"offer:{status}"

    if dry:
        return "dry_run", None

    # The persona writes the touch (template brief or the step's inline
    # intent); delivery via the channel abstraction.
    from app.retention import scenario_library
    intent = await scenario_library.step_intent(pid, step)
    if grant is not None:
        desc = None
        try:
            offer_row = await db.get_offer_by_key(pid,
                                                  str(step.get("offer_key")))
            desc = (offer_row or {}).get("description")
        except Exception:  # noqa: BLE001
            pass
        intent = (f"{intent} | A real gift was JUST credited to the player's "
                  f"account: {desc or step.get('offer_key')}. Mention it "
                  "warmly; never invent terms.")
    from app.retention import channels
    sent, detail, decision_id = await channels.send_journey_touch(
        product, ru, cfg, channel=channel, intent=intent,
        journey_key=str(journey["journey_key"]),
        step_id=int(step.get("step_id") or 0),
        priority=int(step.get("priority") or 3))
    if grant is not None and decision_id is not None:
        try:
            await db.link_offer_grant_decision(pid, grant["offer_grant_id"],
                                               decision_id)
        except Exception:  # noqa: BLE001
            pass
    if sent:
        return ("offer_granted" if grant is not None else "sent"), detail
    return "blocked_guard", detail or "send_failed"


async def drain_due_steps(product: dict[str, Any], cfg: dict[str, Any], *,
                          limit: int = 50) -> dict[str, Any]:
    """Advance every due enrollment by (at most) one step."""
    from app.retention import retention_v2
    pid = int(product["id"])
    stats = {"executed": 0, "exited": 0, "deferred": 0}
    for enr in await db.due_enrollments(pid, limit=limit):
        journey = await db.get_journey(pid, enr["journey_key"],
                                       version=enr["journey_version"])
        if journey is None or journey.get("status") != "active":
            await db.finish_enrollment(pid, enr["id"], "exited_goal",
                                       reason="journey_inactive")
            continue
        ru = None
        if enr.get("retention_user_id"):
            ru = await db.get_retention_user_by_id(pid,
                                                   int(enr["retention_user_id"]))
        if ru is None:
            ru = await db.get_retention_user_by_player(
                pid, str(enr.get("player_id") or ""))
        if ru is None:
            await db.finish_enrollment(pid, enr["id"], "exited_terminal",
                                       reason="player_unlinked")
            stats["exited"] += 1
            continue
        state = await retention_v2.resolve_player_state(pid, ru, cfg)

        # Exit BEFORE the step: goal reached / player returned. An EMPTY exit
        # list means "no goal condition" (eval_conditions([]) is True by the
        # entry-side contract, which would exit every journey instantly here).
        exit_hit = (eval_conditions(journey.get("exit_conditions"), state)
                    if journey.get("exit_conditions") else False)
        if exit_hit is True:
            await db.finish_enrollment(pid, enr["id"], "exited_goal",
                                       reason="exit_conditions_met")
            await db.log_admin_event(None, "journey_exited_goal",
                                     {"journey": enr["journey_key"],
                                      "player_id": enr.get("player_id")},
                                     product_id=pid)
            stats["exited"] += 1
            continue
        if _returned_since(journey, enr, ru):
            await db.finish_enrollment(pid, enr["id"], "exited_return",
                                       reason="player_returned")
            stats["exited"] += 1
            continue

        steps = _steps_of(journey)
        idx = int(enr.get("current_step") or 0)
        if idx >= len(steps):
            await db.finish_enrollment(pid, enr["id"], "completed",
                                       reason=None)
            continue
        step = steps[idx]

        # Per-step condition (unresolvable = fail-safe: no touch).
        cond = eval_conditions(step.get("conditions"), state)
        if cond is None:
            await db.log_journey_step(pid, enr["id"],
                                      int(step.get("step_id") or idx),
                                      "blocked_unresolvable", None, None)
            await db.finish_enrollment(pid, enr["id"], "exited_terminal",
                                       reason="blocked_unresolvable")
            stats["exited"] += 1
            continue
        if cond is False:
            await db.log_journey_step(pid, enr["id"],
                                      int(step.get("step_id") or idx),
                                      "skipped_condition", None, None)
            if str(step.get("on_skip") or "continue") == "exit":
                await db.finish_enrollment(pid, enr["id"], "skipped_exit",
                                           reason="step_condition_exit")
                stats["exited"] += 1
            else:
                await _advance(product, ru, enr, journey, idx, cfg)
            continue

        outcome, detail = await _execute_step(product, ru, enr, journey,
                                              step, state, cfg)
        await db.log_journey_step(pid, enr["id"],
                                  int(step.get("step_id") or idx),
                                  outcome, None, detail)
        if outcome == "deferred_frequency":
            await db.set_enrollment_next_step(
                pid, enr["id"],
                next_step_at=_dt.datetime.now(_dt.timezone.utc)
                + _FREQUENCY_RETRY)
            stats["deferred"] += 1
            continue
        if outcome == "blocked_terminal":
            await db.finish_enrollment(pid, enr["id"], "exited_terminal",
                                       reason=detail)
            stats["exited"] += 1
            continue
        stats["executed"] += 1
        await _advance(product, ru, enr, journey, idx, cfg)
    return stats


def _returned_since(journey: dict[str, Any], enr: dict[str, Any],
                    ru: dict[str, Any]) -> bool:
    """Return-exit: casino activity after enrollment. Applies to scheduled
    (recovery-style) journeys by default; event journeys opt in via
    metadata.exit_on_return."""
    trig_type = str((journey.get("trigger") or {}).get("type") or "event")
    meta = journey.get("metadata") or {}
    applies = (trig_type == "scheduled"
               if meta.get("exit_on_return") is None
               else bool(meta.get("exit_on_return")))
    if not applies:
        return False
    enrolled_at = db._as_ts(enr.get("enrolled_at"))
    if enrolled_at is None:
        return False
    for f in ("last_login_at", "last_played_at", "last_deposit_at"):
        ts = db._as_ts(ru.get(f))
        if ts is not None and ts > enrolled_at:
            return True
    return False


async def _advance(product: dict[str, Any], ru: dict[str, Any],
                   enr: dict[str, Any], journey: dict[str, Any],
                   idx: int, cfg: dict[str, Any]) -> None:
    pid = int(product["id"])
    steps = _steps_of(journey)
    nxt = idx + 1
    if nxt >= len(steps):
        await db.finish_enrollment(pid, enr["id"], "completed", reason=None)
        await db.log_admin_event(None, "journey_completed",
                                 {"journey": enr["journey_key"],
                                  "player_id": enr.get("player_id")},
                                 product_id=pid)
        return
    await db.advance_enrollment(pid, enr["id"], current_step=nxt)
    await _schedule_step(pid, ru, enr["id"], journey, nxt, cfg)


async def run_product_journeys(product: dict[str, Any],
                               cfg: dict[str, Any], *,
                               force: bool = False) -> dict[str, Any]:
    """The sweep entry (rides the worker tick, self-paced): scheduled
    matching + due-step drain."""
    if not journeys_enabled(cfg):
        return {"skipped": "journeys_disabled"}
    pid = int(product["id"])
    now = time.monotonic()
    interval = int(cfg.get("journey_step_sweep_interval_sec")
                   or config.RETENTION_JOURNEY_STEP_SWEEP_INTERVAL_SEC)
    last = _last_sweep.get(pid)
    if not force and last is not None and now - last < interval:
        return {"skipped": "paced"}
    _last_sweep[pid] = now
    try:
        enrolled = await match_scheduled_journeys(product, cfg)
        stats = await drain_due_steps(product, cfg)
        stats["enrolled"] = enrolled
        return stats
    except Exception:  # noqa: BLE001
        log.exception("journey_sweep_failed product=%s", pid)
        return {"error": "sweep_failed"}
