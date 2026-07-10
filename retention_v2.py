"""The retention agent — the event-driven proactive loop.

The ONE proactive regime: canonical casino events (player_sync.py ->
retention_events) wake an AGENT that decides whether Nika reacts —
congratulate a deposit, sympathize after a rough losing day, celebrate a
level-up, or (very often, correctly) stay silent. The split of
responsibilities is the whole design:

  - DETERMINISTIC GUARDS decide whether contact is ALLOWED at all and which
    actions are permitted (caps, min-gap, quiet hours, /stop, unreachable,
    subscription, the daily AI budget, the loss comfort window, per-event
    cooldowns). The model never overrides them.
  - THE AGENT (one cheap JSON decision call, prompts.build_retention_v2_
    decision_messages) picks among the permitted options and writes the brief.
  - THE PERSONA STACK (chat_service.generate_retention_ping with `occasion`)
    writes the actual message — same Layer 1/2, language stickiness and photo
    machinery as every other retention turn.
  - THE LEDGER (retention_v2_decisions) gets ONE row per decision, whatever
    the outcome — state snapshot, guard verdict, agent decision, cost,
    delivery — so "why did/didn't the bot write?" is always answerable.

Per-product switch: `retention.v2_enabled` (hot; the historic `v2_` key prefix
survives in settings keys, endpoint paths, table names and admin-event types
for stored-data compatibility — every user-visible surface says "agent").
`v2_dry_run` (ships ON) makes the loop decide and log WITHOUT sending, so an
owner reviews real decisions before giving the agent a voice. Anti-annoyance
state (last_ping_at, daily counters) lives on retention_users via
db.record_retention_ping.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import html
import json
import logging
import re
import time
from typing import Any, Optional

import chat_service
import config
import db
import openai_client
import prompts
import player_sync
import retention
import settings
import telegram_format
import tenancy
from telegram_transport import TelegramClient, inline_keyboard

log = logging.getLogger(__name__)

# Arbitrary but stable: the advisory-lock key for the agent sweep.
_ADVISORY_LOCK_KEY = 0x50494E56  # "PINV"

# Events that may wake the agent at all. Everything else is state food: it
# feeds the resolver/bridge and is marked processed silently — no model call,
# no ledger row (bet_settled is special-cased: only a high-loss window crossing
# makes it decision-worthy).
DECISION_EVENTS: frozenset[str] = frozenset({
    "deposit_confirmed", "deposit_failed", "withdrawal_settled",
    "level_up", "class_up", "kyc_approved",
    "bonus_granted", "bonus_completed", "bonus_expired",
    # Synthetic (service-generated) triggers: the inactivity ladder, the
    # one-shot follow-up after an unanswered touch, the VIP-at-risk signal.
    "inactivity_check", "touch_follow_up", "vip_at_risk",
})

# Positive moments where a photo is a permissible gesture (never after losses,
# never for technical/KYC events — the guard enforces it, the agent chooses).
_PHOTO_EVENTS = frozenset({
    "deposit_confirmed", "level_up", "class_up", "bonus_completed",
    "withdrawal_settled",
})

# Plain-English occasion lines for the persona prompt, per event.
_OCCASIONS: dict[str, str] = {
    "deposit_confirmed": "the player just made a deposit",
    "deposit_failed": "the player just tried to deposit and the payment "
                      "failed (be helpful and reassuring, no pressure)",
    "withdrawal_settled": "the player just received a withdrawal payout",
    "level_up": "the player just reached a new loyalty level",
    "class_up": "the player just reached a whole new loyalty class",
    "kyc_approved": "the player just passed account verification",
    "bonus_granted": "the player was just granted a bonus (name it only if "
                     "the event names it; never invent its terms)",
    "bonus_completed": "the player just completed a bonus's wagering and "
                       "received the payout",
    "bonus_expired": "one of the player's bonuses just expired unused",
    "bet_settled": "the player has had a rough, losing day",
    "touch_follow_up": "your earlier note went unanswered - ONE gentle, "
                       "unpushy follow-up from a fresh angle, then let it "
                       "rest (never mention the unanswered message)",
    "vip_at_risk": "a valued VIP player has been away for a long while - "
                   "a warm, personal 'thinking of you' with zero pressure",
}

# Guard reasons that are TRANSIENT (a bad moment, not a bad idea): a synthetic
# ladder event is DEFERRED to the next allowed window instead of consumed.
# Everything else (rg_hold, opt-out, unsubscribed, blocked bot) is terminal.
_TRANSIENT_GUARD_REASONS = frozenset({
    "min_gap_not_elapsed", "daily_cap_reached", "quiet_hours",
    "daily_budget_reached", "same_event_cooldown", "ignored_backoff",
})

# Reply-adaptive backoff: after this many consecutive unanswered proactive
# touches the min-gap requirement stretches by this factor (reset on any
# inbound player message). The threshold knob is v2_backoff_after_ignored.
_BACKOFF_GAP_MULT = 4

# Safe, non-money payload details folded into the occasion line so the persona
# can react to the CONCRETE thing that happened ("new level: 7"), not a vague
# congratulation. Amounts are deliberately absent — the prompt bans naming them.
# Bonus events get a richer whitelist (name + type + expiry — a bonus is only
# mentionable by its real facts).
_OCCASION_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "level_up": ("level",),
    "class_up": ("class",),
    "bonus_granted": ("bonus_name", "name", "title", "type", "bonus_id"),
    "bonus_completed": ("bonus_name", "name", "title", "type", "bonus_id"),
    "bonus_expired": ("bonus_name", "name", "title", "type", "bonus_id"),
    "deposit_failed": ("reason",),
}

# Bonus events also surface the expiry alongside the name-ish detail.
_OCCASION_EXPIRY_KEYS = ("expiry_at", "expires_at")


def occasion_for(evt: dict[str, Any]) -> str:
    """The plain-English occasion line for the persona prompt: the per-event
    wording plus whitelisted, non-money payload details."""
    name = evt.get("event_name") or ""
    occasion = _OCCASIONS.get(name, "a notable moment")
    payload = evt.get("payload") or {}
    details = []
    for key in _OCCASION_DETAIL_KEYS.get(name, ()):
        value = payload.get(key)
        if value not in (None, ""):
            details.append(f"{key}: {str(value)[:80]}")
            break  # one name-ish detail is enough; the keys are fallbacks
    if name.startswith("bonus_"):
        expiry = next((payload[k] for k in _OCCASION_EXPIRY_KEYS
                       if payload.get(k)), None)
        if expiry:
            details.append(f"expires {str(expiry)[:40]}")
    if details:
        occasion = f"{occasion} ({'; '.join(details)})"
    return occasion

# One reaction per event type per window — a partner retrying webhooks or a
# player making five deposits in an evening gets ONE warm note, not five.
# The default for the hot `retention.v2_same_event_cooldown_hours` knob
# (0 = off, useful while testing the pipeline with repeated simulator events).
_SAME_EVENT_COOLDOWN_HOURS = 20

_TONES = ("warm", "celebrate", "comfort", "neutral")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Scheduler loop (started from main.py lifespan, next to the v1 loop)
# ---------------------------------------------------------------------------
def worker_interval_sec() -> int:
    """The hot worker cadence (`retention.worker_interval_sec`, global layer).

    Read live on every loop iteration so a Settings save applies on the next
    tick without a redeploy. Clamped to 5s..1h — a sweep is a couple of cheap
    SELECTs when the queues are empty, so a short cadence is fine.
    """
    tenancy.set_current_product(None)  # the loop is global — never read a
    # per-product override left on the ContextVar by the previous sweep
    try:
        v = int(settings.retention().get("worker_interval_sec") or 0)
    except Exception:  # noqa: BLE001 - a bad stored value must not kill the loop
        v = 0
    return min(max(v or config.RETENTION_WORKER_INTERVAL_SEC, 5), 3600)


async def scheduler_loop() -> None:
    """Drain the event queues on the hot-reloaded worker cadence."""
    log.info("retention_agent_scheduler_started interval_sec=%s",
             worker_interval_sec())
    while True:
        await asyncio.sleep(worker_interval_sec())
        try:
            stats = await run_due_events()
            if stats.get("decided") or stats.get("sent"):
                log.info("retention_v2_sweep_done stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            log.exception("retention_v2_sweep_failed")


async def run_due_events() -> dict[str, Any]:
    """One sweep across all v2-enabled products (advisory-locked)."""
    pool = db.pool()
    async with pool.acquire() as conn:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                  _ADVISORY_LOCK_KEY)
        if not got:
            return {"skipped": "another instance holds the lock"}
        try:
            totals: dict[str, Any] = {"products": 0, "events": 0,
                                      "decided": 0, "sent": 0}
            for product in await db.list_retention_products():
                stats = await run_product_events(product)
                if stats.get("skipped"):
                    continue
                totals["products"] += 1
                for k in ("events", "decided", "sent"):
                    totals[k] += stats.get(k, 0)
            return totals
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)


async def run_product_events(product: dict[str, Any], *,
                             limit: Optional[int] = None) -> dict[str, Any]:
    """Drain one product's unprocessed events through the decision pipeline.

    Events are CLAIMED atomically (db.claim_retention_events): the worker
    sweep, the admin «Process queue now» button and any second service
    instance can all run concurrently — each event still reaches the pipeline
    exactly once, so one deposit can never produce two thank-you messages.
    """
    pid = int(product["id"])
    tenancy.set_current_product(pid)
    cfg = settings.retention()
    if not cfg.get("v2_enabled"):
        return {"skipped": "agent_disabled"}
    # The inactivity ladder feeds the same queue with synthetic events before
    # each drain (self-throttled — a scan is one indexed SELECT, but there is
    # no point re-walking the user base every 5-second sweep).
    try:
        await scan_inactivity(product, cfg)
    except Exception:  # noqa: BLE001 - a scan failure must not stop the drain
        log.exception("retention_inactivity_scan_failed product=%s", pid)
    batch = int(limit or cfg["ping_batch_size"])
    events = await db.claim_retention_events(pid, limit=batch)
    if not events:
        return {"events": 0, "decided": 0, "sent": 0}
    decided = sent = 0
    for evt in events:
        try:
            outcome = await _process_event(product, evt, cfg)
            if outcome:
                decided += 1
                if outcome == "sent":
                    sent += 1
        except Exception:  # noqa: BLE001 - one bad event must not wedge the queue
            log.exception("retention_v2_event_failed product=%s event=%s",
                          pid, evt.get("id"))
    return {"events": len(events), "decided": decided, "sent": sent}


# ---------------------------------------------------------------------------
# Inactivity ladder — the dormancy contour (EPIC-5 lite, agent-decided)
# ---------------------------------------------------------------------------
# The scan turns "the player has been gone N days" into synthetic
# `inactivity_check` events on the SAME queue the casino events ride, so the
# whole existing pipeline (state -> guards -> agent decision -> persona send ->
# ledger) applies unchanged. Ladder invariants (per the approved design):
#   - steps come from the `inactivity_steps` knob (spec ladder 7/10/14/21/30);
#   - at most ONE live inactivity event per player; a later-crossed step
#     SUPERSEDES a still-live earlier one (latest-step-wins — this also covers
#     worker downtime: the player gets the highest crossed step, once);
#   - a step is CONSUMED only by a terminal outcome (delivery, agent silence,
#     opt-out/unreachable/rg_hold); transient guard verdicts DEFER the event
#     (due_at) instead — see _process_event;
#   - any real player activity cancels the live event and restarts the cycle
#     (player_sync.cancel_pending_touches).
_INACT_SCAN_INTERVAL_SEC = 900  # per product; the ladder moves in days
_last_inact_scan: dict[int, float] = {}


def parse_inactivity_steps(cfg: dict[str, Any]) -> list[int]:
    """The ladder's day-steps, ascending. Accepts a list or a '7,10,14' string
    (the Settings editor stores a string); junk entries are dropped."""
    raw = cfg.get("inactivity_steps")
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    steps = set()
    for item in raw or []:
        try:
            v = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 365:
            steps.add(v)
    return sorted(steps)


async def scan_inactivity(product: dict[str, Any], cfg: dict[str, Any],
                          force: bool = False) -> dict[str, Any]:
    """Queue synthetic inactivity/vip_at_risk events for idle players."""
    pid = int(product["id"])
    if not cfg.get("inactivity_enabled"):
        return {"skipped": "inactivity_disabled"}
    steps = parse_inactivity_steps(cfg)
    vip_days = int(cfg.get("v2_vip_at_risk_days") or 0)
    if not steps and vip_days <= 0:
        return {"skipped": "no_steps"}
    now_mono = time.monotonic()
    if not force and now_mono - _last_inact_scan.get(pid, 0.0) \
            < _INACT_SCAN_INTERVAL_SEC:
        return {"skipped": "throttled"}
    _last_inact_scan[pid] = now_mono

    min_days = min([s for s in steps] + ([vip_days] if vip_days > 0 else []))
    candidates = await db.inactivity_candidates(pid, min_days=min_days)
    created = superseded = 0
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    for ru in candidates:
        player_id = str(ru.get("player_id") or "")
        idle = _days_since(ru.get("last_seen"))
        if not player_id or idle is None:
            continue
        cycle = int(ru.get("inact_cycle") or 0)
        step = max((s for s in steps if idle >= s), default=0)
        if step and step > int(ru.get("inact_step_done") or 0):
            live = await db.get_open_retention_event(pid, player_id,
                                                     "inactivity_check")
            live_step = int(((live or {}).get("payload") or {}).get("step") or 0)
            if live is None or live_step < step:
                if live is not None:
                    # latest-step-wins: the deferred/queued earlier step dies,
                    # the higher one replaces it. Ledger row per transition.
                    await db.close_retention_event(live["id"])
                    await db.insert_retention_v2_decision(
                        pid, retention_user_id=int(ru["id"]),
                        player_id=player_id, trigger_kind="system",
                        event_pk=live["id"], event_name="inactivity_check",
                        state={}, guard={}, action="superseded",
                        reason=f"latest-step-wins: D{live_step} -> D{step}",
                        dry_run=False)
                    superseded += 1
                pk = await db.ingest_retention_event(
                    pid, event_id=f"inact:{player_id}:{step}:{cycle}",
                    event_name="inactivity_check", player_id=player_id,
                    ts=now_utc,
                    payload={"step": step, "cycle": cycle,
                             "idle_days": round(idle, 1)},
                    source="system")
                if pk:
                    created += 1
        # VIP-at-risk: a long-idle top-tier player gets its own signal (over
        # and above the ladder) + a durable operator-visible admin event with
        # the assigned manager, so a human can also reach out. Once per cycle
        # (the event_id carries the cycle; idempotent by design).
        if (vip_days > 0 and idle >= vip_days
                and retention.is_vip_tier(ru.get("vip_level"), cfg)):
            pk = await db.ingest_retention_event(
                pid, event_id=f"vipar:{player_id}:{cycle}",
                event_name="vip_at_risk", player_id=player_id, ts=now_utc,
                payload={"idle_days": round(idle, 1), "cycle": cycle},
                source="system")
            if pk:
                created += 1
                await db.log_admin_event(
                    None, "retention_vip_at_risk",
                    {"player_id": player_id,
                     "vip_level": ru.get("vip_level"),
                     "idle_days": round(idle, 1),
                     "manager_id": ru.get("assigned_manager_id")},
                    product_id=pid)
    if created or superseded:
        log.info("retention_inactivity_scan product=%s created=%s "
                 "superseded=%s candidates=%s", pid, created, superseded,
                 len(candidates))
    return {"created": created, "superseded": superseded,
            "candidates": len(candidates)}


# ---------------------------------------------------------------------------
# State resolver (deterministic, from the event log + the profile snapshot)
# ---------------------------------------------------------------------------
def _days_since(value: Any) -> Optional[float]:
    dt = db._as_ts(value)
    if dt is None:
        return None
    now = _dt.datetime.now(dt.tzinfo or _dt.timezone.utc)
    return max((now - dt).total_seconds() / 86400.0, 0.0)


async def resolve_player_state(product_id: int, ru: dict[str, Any],
                               cfg: dict[str, Any]) -> dict[str, Any]:
    """The canonical player-state snapshot the guards and the agent read.

    A lite State Resolver: user_status / risk_state / lifecycle_stage per the
    spec's thresholds, plus the 24h loss window — computed from the profile
    snapshot and the event log, no extra infrastructure.
    """
    login_days = _days_since(ru.get("last_login_at"))
    played_days = _days_since(ru.get("last_played_at"))
    deposit_days = _days_since(ru.get("last_deposit_at"))
    idle_candidates = [d for d in (login_days, played_days) if d is not None]
    idle_days = min(idle_candidates) if idle_candidates else None

    if ru.get("last_deposit_at") is None:
        user_status = "registered"
    elif idle_days is None:
        user_status = "depositor"
    elif idle_days < 7:
        user_status = "active"
    elif idle_days < 14:
        user_status = "at_risk"
    else:
        user_status = "dormant"

    net_loss = await db.player_net_loss_24h(product_id, ru.get("player_id") or "")
    loss_high = float(cfg.get("v2_loss_high_usd") or 0)
    risk_state = "low"
    if idle_days is not None and 7 <= idle_days < 14:
        risk_state = "high"
    if (idle_days is not None and idle_days >= 14) or (
            loss_high > 0 and net_loss >= loss_high):
        risk_state = "critical"

    reg_days = _days_since(ru.get("registration_date"))
    if reg_days is None:
        lifecycle = "unknown"
    elif reg_days <= 1:
        lifecycle = "d0_onboarding"
    elif reg_days <= 7:
        lifecycle = "d1_7_activation"
    elif reg_days <= 30:
        lifecycle = "d8_30_habit"
    elif reg_days <= 90:
        lifecycle = "d31_90_growth"
    elif reg_days <= 180:
        lifecycle = "d91_180_maturity"
    else:
        lifecycle = "d181_plus_veteran"

    return {
        "user_status": user_status,
        "risk_state": risk_state,
        "lifecycle_stage": lifecycle,
        "idle_days": round(idle_days, 1) if idle_days is not None else None,
        "days_since_deposit": (round(deposit_days, 1)
                               if deposit_days is not None else None),
        "net_loss_24h_usd": round(net_loss, 2),
        "vip_level": ru.get("vip_level"),
        "pings_muted": bool(ru.get("pings_muted")),
        "subscribed": bool(ru.get("subscribed")),
    }


# ---------------------------------------------------------------------------
# Guards (deterministic — the agent picks only among what these permit)
# ---------------------------------------------------------------------------
def _in_quiet_hours(cfg: dict[str, Any],
                    ru: Optional[dict[str, Any]] = None) -> bool:
    """True when the player's local time sits inside the no-contact window.

    The clock is the PLAYER's (their profile timezone when the casino feed
    supplies one, else the product offset) — retention.player_tz_offset, the
    same offset the CURRENT TIME prompt block runs on, so the two always agree.
    """
    start = int(cfg["quiet_hours_start"])
    end = int(cfg["quiet_hours_end"])
    if start == end:
        return False  # zero-length window = no quiet hours
    hour = (_dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(hours=retention.player_tz_offset(ru, cfg))).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps midnight


async def guard_check(product_id: int, ru: dict[str, Any],
                      evt: dict[str, Any], state: dict[str, Any],
                      cfg: dict[str, Any]) -> dict[str, Any]:
    """The hard rails. Returns {"allow", "reasons", "allowed_actions",
    "constraints", "comfort"} — every deny carries its reason so the ledger
    explains itself."""
    reasons: list[str] = []
    if ru.get("rg_hold"):
        # Responsible-gaming hold from the casino feed: absolute, no bot-side
        # override — the ONE rule that even a VIP flow must never bend.
        reasons.append("rg_hold")
    if not ru.get("subscribed"):
        reasons.append("not_subscribed")
    if ru.get("pings_muted"):
        reasons.append("player_opted_out")
    if ru.get("unreachable"):
        reasons.append("bot_blocked_by_player")
    # Shared anti-annoyance state (same fields the v1 matrix maintains).
    # Reply-adaptive backoff: a player who ignored the last K touches earns a
    # stretched min-gap — the channel quiets down instead of insisting.
    gap_h = int(cfg["ping_min_gap_hours"])
    backoff_after = int(cfg.get("v2_backoff_after_ignored") or 0)
    ignored = int(ru.get("unanswered_touches") or 0)
    backing_off = backoff_after > 0 and ignored >= backoff_after
    eff_gap_h = gap_h * (_BACKOFF_GAP_MULT if backing_off else 1)
    last_ping_days = _days_since(ru.get("last_ping_at"))
    if last_ping_days is not None and last_ping_days * 24 < eff_gap_h:
        reasons.append("ignored_backoff" if backing_off
                       and last_ping_days * 24 >= gap_h
                       else "min_gap_not_elapsed")
    pings_day = ru.get("pings_day")
    today = _dt.date.today().isoformat()
    if (pings_day is not None and str(pings_day)[:10] == today
            and int(ru.get("pings_sent_today") or 0) >= int(cfg["ping_daily_cap"])):
        reasons.append("daily_cap_reached")
    if _in_quiet_hours(cfg, ru):
        reasons.append("quiet_hours")
    budget = float(cfg.get("v2_daily_budget_usd") or 0)
    if budget > 0:
        spent = await db.retention_v2_cost_today(product_id)
        if spent >= budget:
            reasons.append("daily_budget_reached")
            # The one guard worth an operator alert: the agent has gone quiet
            # for the rest of the day because the budget is spent (sampled,
            # best-effort — an alert failure must never block the verdict).
            try:
                await db.log_admin_event_sampled(
                    None, "retention_budget_exhausted",
                    {"budget_usd": budget, "spent_usd": round(spent, 4)},
                    product_id=product_id)
            except Exception:  # noqa: BLE001
                log.warning("retention_budget_alert_failed product=%s",
                            product_id)
    player_id = ru.get("player_id") or ""
    cooldown_h = cfg.get("v2_same_event_cooldown_hours")
    cooldown_h = (_SAME_EVENT_COOLDOWN_HOURS if cooldown_h is None
                  else int(cooldown_h))
    if cooldown_h > 0 and await db.recent_v2_decision_exists(
            product_id, player_id, hours=cooldown_h,
            event_name=evt.get("event_name"), exclude_silence=True):
        reasons.append("same_event_cooldown")

    # Loss comfort window: active high loss OR a recent loss signal.
    comfort = False
    comfort_h = int(cfg.get("v2_loss_comfort_hours") or 0)
    loss_high = float(cfg.get("v2_loss_high_usd") or 0)
    if comfort_h > 0 and loss_high > 0:
        if state.get("net_loss_24h_usd", 0) >= loss_high:
            comfort = True
        else:
            last_loss = await db.last_loss_signal_at(product_id, player_id)
            loss_days = _days_since(last_loss) if last_loss else None
            if (loss_days is not None and loss_days * 24 < comfort_h
                    and state.get("net_loss_24h_usd", 0) > 0):
                comfort = True

    allowed = ["silence", "message"]
    constraints: list[str] = []
    if comfort:
        constraints.append(
            "comfort mode - the player recently lost money: empathetic tone "
            "only, never mention amounts, no play invitation, no photo, "
            "no link")
    elif evt.get("event_name") in _PHOTO_EVENTS:
        allowed.append("photo")
    return {
        "allow": not reasons,
        "reasons": reasons,
        "allowed_actions": allowed,
        "constraints": constraints,
        "comfort": comfort,
    }


def _defer_due_at(reasons: list[str], ru: dict[str, Any],
                  cfg: dict[str, Any]) -> _dt.datetime:
    """The nearest moment every transient guard reason has cleared.

    Approximate on purpose (the guards re-run at claim time anyway — a wrong
    guess just means one extra defer): quiet hours -> the window's local end;
    min-gap/backoff -> last_ping_at + the effective gap; daily cap / budget ->
    the next local midnight. Floor 15 minutes, ceiling 7 days.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    reasons_set = set(reasons)
    candidates = [now + _dt.timedelta(minutes=15)]
    offset = retention.player_tz_offset(ru, cfg)
    local_now = now + _dt.timedelta(hours=offset)
    if "quiet_hours" in reasons_set:
        end = int(cfg["quiet_hours_end"])
        local_end = local_now.replace(hour=end, minute=5, second=0,
                                      microsecond=0)
        if local_end <= local_now:
            local_end += _dt.timedelta(days=1)
        candidates.append(local_end - _dt.timedelta(hours=offset))
    if reasons_set & {"min_gap_not_elapsed", "ignored_backoff"}:
        last_ping = db._as_ts(ru.get("last_ping_at"))
        gap_h = int(cfg["ping_min_gap_hours"])
        if "ignored_backoff" in reasons_set:
            gap_h *= _BACKOFF_GAP_MULT
        if last_ping is not None:
            candidates.append(last_ping + _dt.timedelta(hours=gap_h,
                                                        minutes=5))
    if reasons_set & {"daily_cap_reached", "daily_budget_reached",
                      "same_event_cooldown"}:
        local_midnight = (local_now + _dt.timedelta(days=1)).replace(
            hour=0, minute=15, second=0, microsecond=0)
        candidates.append(local_midnight - _dt.timedelta(hours=offset))
    due = max(candidates)
    return min(due, now + _dt.timedelta(days=7))


# ---------------------------------------------------------------------------
# The agent decision call (cheap, session-less, strict JSON)
# ---------------------------------------------------------------------------
def parse_decision(text: str, allowed_actions: list[str]) -> dict[str, Any]:
    """Parse + clamp the agent's JSON. Anything malformed or not permitted
    degrades to silence — the guard's verdict always wins."""
    m = _JSON_RE.search(text or "")
    if not m:
        return {"action": "silence", "tone": "neutral", "intent": "",
                "reason": "unparseable decision"}
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return {"action": "silence", "tone": "neutral", "intent": "",
                "reason": "unparseable decision"}
    action = str(raw.get("action") or "silence").strip().lower()
    if action not in allowed_actions:
        return {"action": "silence", "tone": "neutral", "intent": "",
                "reason": f"model chose non-permitted action {action!r}"}
    tone = str(raw.get("tone") or "neutral").strip().lower()
    if tone not in _TONES:
        tone = "neutral"
    return {
        "action": action,
        "tone": tone,
        "intent": str(raw.get("intent") or "").strip()[:500],
        "reason": str(raw.get("reason") or "").strip()[:500],
    }


async def _decide(product_id: int, ru: dict[str, Any], evt: dict[str, Any],
                  state: dict[str, Any], guard: dict[str, Any]
                  ) -> tuple[Optional[dict[str, Any]], float]:
    """One decision call. Returns (decision|None, cost_usd). Every call —
    success or failure — lands in ai_interaction_logs (invariant §4,
    session_id=NULL like the photo-metadata calls)."""
    player_id = ru.get("player_id") or ""
    recent, history = await asyncio.gather(
        db.recent_retention_events_for_player(product_id, player_id, limit=8),
        _history_tail(ru),
    )
    messages = prompts.build_retention_v2_decision_messages(
        state=state, event=evt, recent_events=recent,
        history_tail=history,
        allowed_actions=guard["allowed_actions"],
        constraints=guard["constraints"])
    client = await openai_client.client_for_product(product_id)
    try:
        result = await client.complete(messages)
    except Exception as exc:  # noqa: BLE001 - a model failure skips this event
        await db.log_ai_interaction(
            None, settings.model()["model"], "none", 0, 0, 0, 0.0, 0, False,
            f"v2_decision: {exc.__class__.__name__}", product_id=product_id)
        log.warning("retention_v2_decision_model_failed product=%s error=%s",
                    product_id, exc)
        return None, 0.0
    cost = openai_client.compute_cost(result.model, result.tokens_in,
                                      result.tokens_out, result.cached_in)
    await db.log_ai_interaction(
        None, result.model, result.key_used, result.tokens_in,
        result.tokens_out, result.cached_in, cost, result.latency_ms,
        True, None, product_id=product_id)
    return parse_decision(result.text, guard["allowed_actions"]), float(cost or 0)


async def _history_tail(ru: dict[str, Any]) -> list[dict[str, Any]]:
    session_id = ru.get("session_id")
    if not session_id:
        return []
    try:
        return await db.get_history(str(session_id), limit=6)
    except Exception:  # noqa: BLE001 - context is best-effort
        return []


# ---------------------------------------------------------------------------
# The pipeline for one event
# ---------------------------------------------------------------------------
def _is_decision_worthy(evt: dict[str, Any], state: dict[str, Any],
                        cfg: dict[str, Any]) -> bool:
    name = evt.get("event_name") or ""
    if name in DECISION_EVENTS:
        return True
    if name == "bet_settled":
        loss_high = float(cfg.get("v2_loss_high_usd") or 0)
        return loss_high > 0 and state.get("net_loss_24h_usd", 0) >= loss_high
    return False


async def _process_event(product: dict[str, Any], evt: dict[str, Any],
                         cfg: dict[str, Any]) -> Optional[str]:
    """Run one event through guards -> agent -> (maybe) send + ledger.

    Returns None for a log-only event, else the ledger action recorded
    ('sent' when a message actually went out).
    """
    pid = int(product["id"])
    player_id = evt.get("player_id") or ""
    # Recipient resolution. An explicit payload `tg_user_id` (validated at
    # ingest) pins the exact Telegram account; without it the most recently
    # active link for the player_id wins — ambiguous when one player is linked
    # to several Telegram accounts (the multi-tester setup), which is exactly
    # what the explicit target exists for. An explicit target that is not
    # linked NEVER falls back to another account silently — that would resend
    # the confusion the field was added to remove.
    target_tg = (evt.get("payload") or {}).get("tg_user_id")
    if target_tg:
        ru = await db.get_retention_user(pid, int(target_tg))
        skip_reason = (f"tg_user_id {target_tg} is not linked to the "
                       "Telegram bot for this product")
    else:
        ru = await db.get_retention_user_by_player(pid, player_id)
        skip_reason = "player not linked to the Telegram bot"

    # Cheap pre-filters that need no model: unknown recipient / log-only event.
    if ru is None:
        if evt.get("event_name") in DECISION_EVENTS:
            await db.insert_retention_v2_decision(
                pid, retention_user_id=None, player_id=player_id,
                trigger_kind="event", event_pk=evt["id"],
                event_name=evt.get("event_name"), state={}, guard={},
                action="skipped", reason=skip_reason,
                dry_run=bool(cfg.get("v2_dry_run")))
            return "skipped"
        return None

    name = evt.get("event_name") or ""
    payload = evt.get("payload") or {}
    step = int(payload.get("step") or 0)
    synthetic = name in ("inactivity_check", "touch_follow_up", "vip_at_risk")
    state = await resolve_player_state(pid, ru, cfg)

    # cancelled_by_return, claim-time re-check: the world may have moved while
    # the synthetic event sat queued/deferred (the sweep-time cancellation in
    # player_sync covers the event feed; this covers everything else).
    if synthetic:
        idle = state.get("idle_days")
        came_back = (
            (name == "inactivity_check" and (idle is None or idle < step))
            or (name == "touch_follow_up"
                and int(ru.get("unanswered_touches") or 0) == 0)
            or (name == "vip_at_risk"
                and (idle is None
                     or idle < int(cfg.get("v2_vip_at_risk_days") or 0))))
        if came_back:
            await db.insert_retention_v2_decision(
                pid, retention_user_id=int(ru["id"]), player_id=player_id,
                trigger_kind="system", event_pk=evt["id"], event_name=name,
                state=state, guard={}, action="cancelled",
                reason="cancelled_by_return: player active again",
                dry_run=bool(cfg.get("v2_dry_run")))
            if name in ("inactivity_check", "vip_at_risk"):
                await db.bump_inactivity_cycle(pid, player_id, force=True)
            return "cancelled"

    if not _is_decision_worthy(evt, state, cfg):
        return None

    # Loss reaction delay (EPIC-5: 30-60 min after the last bet, not mid-
    # session): the first time a high-loss bet_settled comes through, defer it;
    # when it comes due the loss window is re-resolved fresh.
    loss_delay_min = int(cfg.get("v2_loss_delay_min") or 0)
    if (name == "bet_settled" and loss_delay_min > 0
            and evt.get("due_at") is None):
        due = (_dt.datetime.now(_dt.timezone.utc)
               + _dt.timedelta(minutes=loss_delay_min))
        await db.defer_retention_event(evt["id"], due)
        await db.insert_retention_v2_decision(
            pid, retention_user_id=int(ru["id"]), player_id=player_id,
            trigger_kind="event", event_pk=evt["id"], event_name=name,
            state=state, guard={}, action="deferred",
            reason=f"loss reaction delayed {loss_delay_min}min "
                   f"(due {due.isoformat(timespec='minutes')})",
            dry_run=bool(cfg.get("v2_dry_run")))
        return "deferred"

    guard = await guard_check(pid, ru, evt, state, cfg)
    dry_run = bool(cfg.get("v2_dry_run"))
    if not guard["allow"]:
        transient = set(guard["reasons"]) <= _TRANSIENT_GUARD_REASONS
        if synthetic and transient:
            # A bad MOMENT, not a bad idea: the ladder step is NOT consumed —
            # the event is re-queued for the nearest allowed window.
            due = _defer_due_at(guard["reasons"], ru, cfg)
            await db.defer_retention_event(evt["id"], due)
            await db.insert_retention_v2_decision(
                pid, retention_user_id=int(ru["id"]), player_id=player_id,
                trigger_kind="system", event_pk=evt["id"], event_name=name,
                state=state, guard=guard, action="deferred",
                reason="; ".join(guard["reasons"])
                       + f" (due {due.isoformat(timespec='minutes')})",
                dry_run=dry_run)
            return "deferred"
        await db.insert_retention_v2_decision(
            pid, retention_user_id=int(ru["id"]), player_id=player_id,
            trigger_kind="system" if synthetic else "event",
            event_pk=evt["id"], event_name=name, state=state, guard=guard,
            action="blocked", reason="; ".join(guard["reasons"]),
            dry_run=dry_run)
        if name == "inactivity_check" and step:
            # Terminal verdict (rg_hold / opt-out / unreachable / not
            # subscribed): the step is consumed — no retry loop.
            await db.consume_inactivity_step(int(ru["id"]), step)
        log.info("retention_v2_guard_blocked product=%s player=%s event=%s "
                 "reasons=%s", pid, player_id, name,
                 ",".join(guard["reasons"]))
        return "blocked"

    decision, decision_cost = await _decide(pid, ru, evt, state, guard)
    if decision is None:
        await db.insert_retention_v2_decision(
            pid, retention_user_id=int(ru["id"]), player_id=player_id,
            trigger_kind="system" if synthetic else "event",
            event_pk=evt["id"], event_name=name, state=state, guard=guard,
            action="skipped", reason="decision model call failed",
            dry_run=dry_run, cost_usd=decision_cost)
        return "skipped"

    action = decision["action"]
    delivered = False
    detail: Optional[str] = None
    total_cost = decision_cost
    if action in ("message", "photo") and not dry_run:
        delivered, send_cost, detail = await _send_touch(
            product, ru, evt, decision, comfort=guard["comfort"], cfg=cfg,
            state=state)
        total_cost += send_cost
    await db.insert_retention_v2_decision(
        pid, retention_user_id=int(ru["id"]), player_id=player_id,
        trigger_kind="system" if synthetic else "event",
        event_pk=evt["id"], event_name=name, state=state, guard=guard,
        action=action, intent=decision["intent"], tone=decision["tone"],
        reason=decision["reason"], dry_run=dry_run, delivered=delivered,
        detail=detail, cost_usd=total_cost)
    # The agent has SPOKEN on this step (or chosen silence): terminal either
    # way — the ladder never re-runs a decided step within a cycle.
    if name == "inactivity_check" and step:
        await db.consume_inactivity_step(int(ru["id"]), step)
    # Journey-lite: one gentle follow-up when a delivered re-engagement touch
    # stays unanswered (knob v2_follow_up_hours; 0 = off). Reactive event
    # touches never chain — only the ladder/VIP contour does.
    fup_h = int(cfg.get("v2_follow_up_hours") or 0)
    if (delivered and fup_h > 0
            and name in ("inactivity_check", "vip_at_risk")):
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        await db.ingest_retention_event(
            pid, event_id=f"fup:{evt['id']}",
            event_name="touch_follow_up", player_id=player_id, ts=now_utc,
            payload={"origin": name, "step": step},
            source="system", due_at=now_utc + _dt.timedelta(hours=fup_h))
    await db.log_admin_event(
        None, "retention_v2_decision",
        {"event": evt.get("event_name"), "action": action,
         "tone": decision["tone"], "dry_run": dry_run,
         "delivered": delivered, "cost_usd": total_cost},
        product_id=pid)
    # One Railway line per agent decision — the log mirror of the ledger row,
    # so "what is the agent doing right now?" is answerable from the deploy
    # logs alone.
    log.info(
        "retention_v2_decision product=%s player=%s event=%s action=%s "
        "tone=%s dry_run=%s delivered=%s detail=%s cost_usd=%.6f",
        pid, player_id, evt.get("event_name"), action, decision["tone"],
        dry_run, delivered, detail, total_cost)
    return "sent" if delivered else action


# ---------------------------------------------------------------------------
# Sending (the persona writes; mechanics mirror the v1 ping send)
# ---------------------------------------------------------------------------
async def _send_touch(product: dict[str, Any], ru: dict[str, Any],
                      evt: dict[str, Any], decision: dict[str, Any], *,
                      comfort: bool, cfg: dict[str, Any],
                      state: Optional[dict[str, Any]] = None
                      ) -> tuple[bool, float, Optional[str]]:
    """Generate + deliver the agent-decided touch. Returns
    (delivered, generation_cost_usd, detail)."""
    pid = int(product["id"])
    rid = int(ru["id"])
    name = evt.get("event_name") or ""
    token = await db.get_product_telegram_token(pid)
    if not token:
        return False, 0.0, "no_bot_token"
    lang = retention.resolve_user_lang(ru)
    session = await retention._ensure_session(pid, ru, lang)
    if session is None:
        return False, 0.0, "no_session"
    session["user_context"] = retention._user_context_from_ru(ru)

    candidates: list[dict[str, Any]] = []
    if decision["action"] == "photo" and not comfort:
        candidates = await retention.select_photo_candidates(
            pid, ru, "", bypass_cooldown=True)

    # Real offers (the platform's read-only Offers API, Stage-4 contract):
    # fetched per touch when configured; never in comfort mode. With none, the
    # re-engagement wording is hard-locked to bonus-free warmth.
    offers: list[dict[str, Any]] = []
    if not comfort and (product.get("offers_api_url") or "").strip():
        offers = await player_sync.fetch_player_offers(
            product, str(ru.get("player_id") or ""))

    # An inactivity/VIP touch is the classic idle re-engagement (the PING task
    # wording); reactive event touches carry the occasion wording instead.
    if name in ("inactivity_check", "vip_at_risk"):
        occasion = None
        idle_days = int(float((state or {}).get("idle_days") or 0)) or 1
        step = int((evt.get("payload") or {}).get("step") or 0)
        reason = (f"inactivity ladder step D{step}" if step
                  else "long-idle VIP player")
    else:
        occasion = occasion_for(evt)
        idle_days, reason = 0, ""
    draft = await chat_service.generate_retention_ping(
        session, idle_days=idle_days, reason=reason,
        intent=decision["intent"],
        photo_candidates=candidates, occasion=occasion, comfort=comfort,
        offers=offers,
        no_bonus_talk=(name in ("inactivity_check", "touch_follow_up",
                                "vip_at_risk") and not offers),
        tz_offset_hours=retention.player_tz_offset(ru, cfg))
    if draft is None:
        await db.record_retention_ping(pid, rid, None, decision["action"],
                                       "failed", detail="v2:model_error")
        return False, 0.0, "model_error"
    gen_cost = float(draft.ai_meta.get("cost_usd") or 0)

    # The trigger + occasion travel with the turn: persisted on the message row
    # (so the prompt history and the admin transcript can explain WHY the bot
    # wrote) and — with `v2_show_trigger` on — shown as a chrome line in the
    # sent message itself.
    event_name = str(evt.get("event_name") or "")
    # Inactivity/VIP touches carry no `occasion` (they use the idle-days ping
    # wording) — their `reason` line is the context instead.
    context_desc = occasion or reason or "proactive touch"
    ping_context = (f"{event_name}: {context_desc}" if event_name
                    else context_desc)
    trigger_line = ""
    if cfg.get("v2_show_trigger"):
        trigger_line = retention._rtn_text(
            "rtn_ping_trigger", draft.lang
        ).replace("{trigger}", event_name or "event").strip()

    markup = None
    if draft.link_url and not comfort:
        markup = inline_keyboard([[{"text": draft.link_label or draft.link_url,
                                    "url": draft.link_url}]])
    client = TelegramClient(token)
    chat_id = int(ru["tg_user_id"])
    delivered = False
    detail: Optional[str] = None
    if draft.photo_id is not None and not comfort:
        caption = draft.text or retention._rtn_text("rtn_photo_caption",
                                                    draft.lang)
        if trigger_line:
            caption = f"{trigger_line}\n\n{caption}"
        delivered = await retention._send_photo(
            client, product, ru, chat_id, draft.photo_id, caption,
            session_id=session["id"], reply_markup=markup)
        if not delivered:
            detail = "photo_send_failed"
    else:
        header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
        text_html = telegram_format.to_html(draft.text)
        text_plain = draft.text
        chrome = [ln for ln in (header, trigger_line) if ln]
        if chrome:
            chrome_html = "\n".join(f"<i>{html.escape(ln)}</i>"
                                    for ln in chrome)
            chrome_plain = "\n".join(chrome)
            text_html = f"{chrome_html}\n\n{text_html}"
            text_plain = f"{chrome_plain}\n\n{draft.text}"
        result, err_code, err_desc = await client.send_message_verbose(
            chat_id, text_html, parse_mode="HTML", reply_markup=markup)
        if result is None and text_html != text_plain and err_code != 403:
            result, err_code, err_desc = await client.send_message_verbose(
                chat_id, text_plain, reply_markup=markup)
        delivered = result is not None
        if not delivered:
            detail = f"{err_code}: {err_desc}" if err_desc else "send_failed"
            log.warning("retention_v2_send_failed product=%s player=%s "
                        "detail=%s", pid, ru.get("player_id"), detail)
            if err_code == 403:
                await db.set_retention_unreachable(rid, True)

    if delivered:
        await db.persist_ping_turn(session["id"], draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid,
                                   ping_context=ping_context)
        # Shared anti-annoyance state: the SAME ledger/counters the v1 matrix
        # uses, so caps and min-gap hold across regimes.
        await db.record_retention_ping(
            pid, rid, None, decision["action"], "sent",
            detail=f"v2:{evt.get('event_name')}", cost_usd=gen_cost)
        return True, gen_cost, None

    # The generation call happened but nothing reached the player: account the
    # cost (invariant §4 — every OpenAI call gets an ai_interaction_logs row).
    meta = draft.ai_meta
    await db.log_ai_interaction(
        session["id"], meta.get("model"), meta.get("key_used"),
        meta.get("tokens_in"), meta.get("tokens_out"), meta.get("cached_in"),
        gen_cost, meta.get("latency_ms"), False,
        f"v2_touch_undelivered {detail}", product_id=pid)
    await db.record_retention_ping(pid, rid, None, decision["action"],
                                   "failed", detail=f"v2:{detail}",
                                   cost_usd=gen_cost)
    return False, gen_cost, detail
