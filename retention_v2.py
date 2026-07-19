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
import json
import logging
import re
from typing import Any, Optional

import chat_service
import config
import db
import delivery
import openai_client
import prompts
import retention
import settings
import tenancy
import translations

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
    "bonus_completed", "bonus_expired",
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
    "bonus_completed": "the player just completed a bonus's wagering and "
                       "received the payout",
    "bonus_expired": "one of the player's bonuses just expired unused",
    "bet_settled": "the player has had a rough, losing day",
    # Non-default triggers (enabled only via the `retention.v2_decision_events`
    # setting) — every enableable canonical event ships its own occasion wording.
    "session_started": "the player just came online and started a session",
    "session_ended": "the player just wrapped up a play session",
    "deposit_initiated": "the player just started making a deposit (it is "
                         "NOT confirmed yet - no thanks for money, keep it "
                         "light and unpushy)",
    "bonus_granted": "the player was just granted a new bonus",
    "bonus_claimed": "the player just claimed and activated a bonus",
    "kyc_started": "the player just started account verification",
    "kyc_rejected": "the player's account verification was rejected (be "
                    "gentle and supportive, no blame, no pressure)",
    "xp_granted": "the player just earned experience points",
    "downgrade": "the player's loyalty level just went down (be tactful "
                 "and encouraging, never scold or dwell on the loss)",
    "highlights_pack_opened": "the player just opened a highlights pack",
    "highlights_pack_completed": "the player just completed a highlights "
                                 "pack",
    "check_in_done": "the player just completed a daily check-in",
    "mission_completed": "the player just completed a mission",
}

# Safe, non-money payload details folded into the occasion line so the persona
# can react to the CONCRETE thing that happened ("new level: 7"), not a vague
# congratulation. Amounts are deliberately absent — the prompt bans naming them.
_OCCASION_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "level_up": ("level",),
    "class_up": ("class",),
    "bonus_completed": ("type", "bonus_id"),
    "bonus_expired": ("type", "bonus_id"),
    "deposit_failed": ("reason",),
    "bonus_granted": ("type", "bonus_id"),
    "bonus_claimed": ("type", "bonus_id"),
    "kyc_rejected": ("reason",),
    "xp_granted": ("xp",),
    "downgrade": ("level", "class"),
    "mission_completed": ("mission", "name", "title"),
}


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
            details.append(f"{key}: {value}")
            break  # one detail is enough; the keys are fallbacks
    if details:
        occasion = f"{occasion} ({details[0]})"
    return occasion


# One reaction per event type per window — a partner retrying webhooks or a
# player making five deposits in an evening gets ONE warm note, not five.
# Fallback for the hot `retention.v2_same_event_cooldown_hours` knob
# (0 = off, useful while testing with repeated simulator events) — the config
# default, so a hand-built cfg dict resolves the same value as the settings
# layer.

# A reaction only makes sense while the occasion is FRESH: "thanks for the
# deposit" a day later reads as surveillance, not warmth. Events older than
# this (a long quiet-hours backlog, the agent re-enabled after days off) are
# demoted to state food — processed silently, no ledger row, no model call.
_MAX_REACTION_AGE_HOURS = 24

_TONES = ("warm", "celebrate", "comfort", "neutral")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Scheduler loop (started from main.py lifespan)
# ---------------------------------------------------------------------------
def worker_interval_sec() -> int:
    """The hot worker cadence (`retention.worker_interval_sec`, global layer).

    Read live on every loop iteration so a Settings save applies on the next
    tick without a redeploy. Clamped to 5s..1h — a sweep is a couple of cheap
    SELECTs when the queues are empty, so a short cadence is fine.
    """
    # Global-layer read (settings.GLOBAL_ONLY_FIELDS keeps the product layer
    # out of this key); also called from admin requests, whose product scope
    # must survive — settings.global_retention_int handles both.
    return settings.global_retention_int(
        "worker_interval_sec", config.RETENTION_WORKER_INTERVAL_SEC, 5, 3600)


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
                totals["products"] += 1
                for k in ("events", "decided", "sent", "idle_sent",
                          "idle_failed"):
                    if stats.get(k):
                        totals[k] = totals.get(k, 0) + stats[k]
            return totals
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)


async def run_product_events_locked(product: dict[str, Any], *,
                                    limit: Optional[int] = None
                                    ) -> dict[str, Any]:
    """run_product_events under the SAME advisory lock the worker sweep holds.

    Required: without the lock a button-run and the worker can both read the
    per-player guard counters before either writes — double send. Blocking
    lock (not try-lock): the button should run right after the worker
    finishes, not silently no-op. The manual run also bypasses the humanizing
    send delay — the operator pressing the button wants answers now."""
    pool = db.pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _ADVISORY_LOCK_KEY)
        try:
            return await run_product_events(product, limit=limit,
                                            ignore_send_delay=True)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)


async def run_product_events(product: dict[str, Any], *,
                             limit: Optional[int] = None,
                             ignore_send_delay: bool = False) -> dict[str, Any]:
    """Drain one product's unprocessed events through the decision pipeline.

    Events are CLAIMED atomically (db.claim_retention_events): the worker
    sweep, the admin «Process queue now» button and any second service
    instance can all run concurrently — each event still reaches the pipeline
    exactly once, so one deposit can never produce two thank-you messages.

    The worker honours the humanizing SEND DELAY (an event becomes claimable a
    per-event random `v2_send_delay_min_sec`..`v2_send_delay_max_sec` after it
    arrived — an instant reaction to a deposit reads as surveillance, not
    warmth); `ignore_send_delay` is the admin «Process queue now» override.

    QUIET HOURS defer the pipeline instead of consuming events: the worker
    simply does not CLAIM during the window, so a night-time deposit gets its
    warm note in the morning (in a casino the night IS peak deposit time; the
    freshness cap in `_is_decision_worthy` bounds how stale a reaction may
    get). The admin «Process queue now» button (`ignore_send_delay`) claims
    regardless: the operator explicitly asked for answers now.

    The idle sweep at the tail runs on its OWN switch (`idle_pings_enabled`),
    independent of `v2_enabled` — turning the event agent off must not
    silently kill the idle ladder the admin sees as a separate toggle.
    """
    pid = int(product["id"])
    tenancy.set_current_product(pid)
    cfg = settings.retention()
    stats: dict[str, Any] = {"events": 0, "decided": 0, "sent": 0}
    if not cfg.get("v2_enabled"):
        stats["agent"] = "disabled"
    elif not ignore_send_delay and _in_quiet_hours(cfg):
        stats["agent"] = "quiet_hours_deferred"
    else:
        batch = int(limit or cfg["ping_batch_size"])
        delay_min = (0 if ignore_send_delay
                     else int(cfg.get("v2_send_delay_min_sec") or 0))
        delay_max = (0 if ignore_send_delay
                     else int(cfg.get("v2_send_delay_max_sec") or 0))
        events = await db.claim_retention_events(
            pid, limit=batch, delay_min_sec=delay_min,
            delay_max_sec=max(delay_max, delay_min))
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
        stats.update(events=len(events), decided=decided, sent=sent)
    # The agent's INACTIVITY trigger: a quiet player produces no events, so the
    # idle rules ladder (retention_idle) runs from the same sweep — same lock,
    # same guards, same dry-run — gated by its OWN `idle_pings_enabled` switch.
    # Self-paced (once per ~10 min per product), so a seconds-scale worker
    # interval doesn't hammer it.
    try:
        import retention_idle  # late: retention_idle imports this module
        idle = await retention_idle.run_product_idle_pings(product, cfg)
        if idle.get("sent") or idle.get("failed"):
            stats["idle_sent"] = idle.get("sent", 0)
            stats["idle_failed"] = idle.get("failed", 0)
    except Exception:  # noqa: BLE001 - the idle sweep must not wedge the events
        log.exception("retention_idle_sweep_failed product=%s", pid)
    return stats


# ---------------------------------------------------------------------------
# State resolver (deterministic, from the event log + the profile snapshot)
# ---------------------------------------------------------------------------
def days_since(value: Any, now: Optional[_dt.datetime] = None
               ) -> Optional[float]:
    """Days since a timestamp-ish value (db._as_ts parses; None = unknown).
    Shared with retention_idle (its sweep passes one `now` per batch)."""
    dt = db._as_ts(value)
    if dt is None:
        return None
    now = now or _dt.datetime.now(dt.tzinfo or _dt.timezone.utc)
    return max((now - dt).total_seconds() / 86400.0, 0.0)


async def resolve_player_state(product_id: int, ru: dict[str, Any],
                               cfg: dict[str, Any]) -> dict[str, Any]:
    """The canonical player-state snapshot the guards and the agent read.

    A lite State Resolver: user_status / risk_state / lifecycle_stage per the
    spec's thresholds, plus the 24h loss window — computed from the profile
    snapshot and the event log, no extra infrastructure.
    """
    login_days = days_since(ru.get("last_login_at"))
    played_days = days_since(ru.get("last_played_at"))
    deposit_days = days_since(ru.get("last_deposit_at"))
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

    reg_days = days_since(ru.get("registration_date"))
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
def _in_quiet_hours(cfg: dict[str, Any]) -> bool:
    """True when the product's local time sits inside the no-contact window."""
    start = int(cfg["quiet_hours_start"])
    end = int(cfg["quiet_hours_end"])
    if start == end:
        return False  # zero-length window = no quiet hours
    offset = int(cfg["quiet_hours_utc_offset"])
    hour = (_dt.datetime.now(_dt.timezone.utc).hour + offset) % 24
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
    if not ru.get("subscribed"):
        reasons.append("not_subscribed")
    if ru.get("pings_muted"):
        reasons.append("player_opted_out")
    if ru.get("unreachable"):
        reasons.append("bot_blocked_by_player")
    # Shared anti-annoyance state (the per-player ping counters/gaps).
    gap_h = int(cfg["ping_min_gap_hours"])
    last_ping_days = days_since(ru.get("last_ping_at"))
    if last_ping_days is not None and last_ping_days * 24 < gap_h:
        reasons.append("min_gap_not_elapsed")
    pings_day = ru.get("pings_day")
    # UTC: same clock as the DB-side day rollover (see db.record_retention_ping).
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    if (pings_day is not None and str(pings_day)[:10] == today
            and int(ru.get("pings_sent_today") or 0) >= int(cfg["ping_daily_cap"])):
        reasons.append("daily_cap_reached")
    # NB: quiet hours are NOT a guard reason — the worker defers CLAIMING
    # during the window (run_product_events), so a night event is reacted to
    # in the morning instead of being consumed as 'blocked'.
    budget = float(cfg.get("v2_daily_budget_usd") or 0)
    if budget > 0:
        spent = await db.retention_v2_cost_today(product_id)
        if spent >= budget:
            reasons.append("daily_budget_reached")
    player_id = ru.get("player_id") or ""
    cooldown_h = cfg.get("v2_same_event_cooldown_hours")
    cooldown_h = (config.RETENTION_V2_SAME_EVENT_COOLDOWN_HOURS
                  if cooldown_h is None else int(cooldown_h))
    if cooldown_h > 0:
        # The cooldown counts real reactions only — a silence decision on a
        # deposit must not suppress the reaction to the NEXT deposit. The one
        # exception is bet_settled: above the loss threshold EVERY settled bet
        # is decision-worthy, so without counting silence the agent re-ran a
        # paid decision call per bet of a losing streak until the daily budget
        # burned out on "stay quiet" verdicts. One look per window, whatever
        # the agent chose.
        include: tuple[str, ...] = ("message", "photo")
        if evt.get("event_name") == "bet_settled":
            include = ("message", "photo", "silence")
        if await db.recent_v2_decision_exists(
                product_id, player_id, hours=cooldown_h,
                event_name=evt.get("event_name"), include_actions=include):
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
            loss_days = days_since(last_loss) if last_loss else None
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
def effective_decision_events(cfg: dict[str, Any]) -> frozenset[str]:
    """The event names allowed to wake the agent for this product.

    Tunable only via the `retention.v2_decision_events` setting (deliberately
    not panel-editable); absent/None resolves to the built-in DECISION_EVENTS. bet_settled is never
    in the set — it stays special-cased on the loss threshold below.
    """
    v = cfg.get("v2_decision_events")
    if v is None:
        return DECISION_EVENTS
    return frozenset(str(x) for x in v)


def _fresh_enough(evt: dict[str, Any]) -> bool:
    """Is the event recent enough to still deserve a reaction?

    Keyed on the event's own `ts` (when the occasion actually happened; falls
    back to arrival time). A quiet-hours backlog stays reactable in the
    morning; a days-old queue (the agent re-enabled after time off) is not
    congratulated retroactively."""
    ts = db._as_ts(evt.get("ts") or evt.get("created_at"))
    if ts is None:
        return True
    now = _dt.datetime.now(ts.tzinfo or _dt.timezone.utc)
    return (now - ts).total_seconds() <= _MAX_REACTION_AGE_HOURS * 3600


def _is_decision_worthy(evt: dict[str, Any], state: dict[str, Any],
                        cfg: dict[str, Any]) -> bool:
    if not _fresh_enough(evt):
        return False
    name = evt.get("event_name") or ""
    if name in effective_decision_events(cfg):
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
        if (evt.get("event_name") in effective_decision_events(cfg)
                and _fresh_enough(evt)):
            await db.insert_retention_v2_decision(
                pid, retention_user_id=None, player_id=player_id,
                trigger_kind="event", event_pk=evt["id"],
                event_name=evt.get("event_name"), state={}, guard={},
                action="skipped", reason=skip_reason,
                dry_run=bool(cfg.get("v2_dry_run")))
            return "skipped"
        return None

    state = await resolve_player_state(pid, ru, cfg)
    if not _is_decision_worthy(evt, state, cfg):
        return None

    guard = await guard_check(pid, ru, evt, state, cfg)
    dry_run = bool(cfg.get("v2_dry_run"))
    if not guard["allow"]:
        await db.insert_retention_v2_decision(
            pid, retention_user_id=int(ru["id"]), player_id=player_id,
            trigger_kind="event", event_pk=evt["id"],
            event_name=evt.get("event_name"), state=state, guard=guard,
            action="blocked", reason="; ".join(guard["reasons"]),
            dry_run=dry_run)
        log.info("retention_v2_guard_blocked product=%s player=%s event=%s "
                 "reasons=%s", pid, player_id, evt.get("event_name"),
                 ",".join(guard["reasons"]))
        return "blocked"

    decision, decision_cost = await _decide(pid, ru, evt, state, guard)
    if decision is None:
        await db.insert_retention_v2_decision(
            pid, retention_user_id=int(ru["id"]), player_id=player_id,
            trigger_kind="event", event_pk=evt["id"],
            event_name=evt.get("event_name"), state=state, guard=guard,
            action="skipped", reason="decision model call failed",
            dry_run=dry_run, cost_usd=decision_cost)
        return "skipped"

    action = decision["action"]
    delivered = False
    detail: Optional[str] = None
    total_cost = decision_cost
    if action in ("message", "photo") and not dry_run:
        delivered, send_cost, detail = await _send_touch(
            product, ru, evt, decision, comfort=guard["comfort"], cfg=cfg)
        total_cost += send_cost
    await db.insert_retention_v2_decision(
        pid, retention_user_id=int(ru["id"]), player_id=player_id,
        trigger_kind="event", event_pk=evt["id"],
        event_name=evt.get("event_name"), state=state, guard=guard,
        action=action, intent=decision["intent"], tone=decision["tone"],
        reason=decision["reason"], dry_run=dry_run, delivered=delivered,
        detail=detail, cost_usd=total_cost)
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
# Sending (the persona writes; delivery via the delivery.py seam)
# ---------------------------------------------------------------------------
# Chrome detail per event for the header occasion phrase — unlike the
# model-facing occasion line, the CHROME may name the amount (the player just
# saw the exact number in their cashier; the phrase confirming "which deposit"
# is what makes the note read as personal, not cryptic).
def _trigger_detail(evt: dict[str, Any]) -> str:
    name = str(evt.get("event_name") or "")
    p = evt.get("payload") or {}
    if name in ("deposit_confirmed", "withdrawal_settled"):
        amount = p.get("amount")
        if amount in (None, ""):
            return ""
        currency = str(p.get("currency") or "").strip()
        return f"{amount} {currency}".strip()
    if name == "level_up":
        return str(p.get("level") or "").strip()
    if name == "class_up":
        return str(p.get("class") or "").strip()
    if name in ("bonus_completed", "bonus_expired", "bonus_granted",
                "bonus_claimed"):
        return str(p.get("type") or "").strip()
    if name == "xp_granted":
        return str(p.get("xp") or "").strip()
    if name == "mission_completed":
        for key in ("mission", "name", "title"):
            value = str(p.get(key) or "").strip()
            if value:
                return value
    # downgrade / kyc_rejected deliberately carry no chrome detail — naming
    # the lost level or the rejection reason in the header would sting.
    return ""


def _trigger_phrase(evt: dict[str, Any], lang: str) -> str:
    """The localized human occasion phrase for the header line, or ''.

    Resolved from the translations registry (rtn_trig_<event>, admin-editable
    per language); the optional {detail} placeholder carries the safe payload
    detail (amount / level / class / bonus type)."""
    name = str(evt.get("event_name") or "")
    key = f"rtn_trig_{name}"
    if not any(k == key for k, _scope, _d in translations.KEYS):
        return ""
    tpl = retention._rtn_text(key, lang)
    detail = _trigger_detail(evt)
    text = tpl.replace("{detail}", f" {detail}" if detail else "")
    # An empty detail may leave a dangling space before punctuation.
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([!?.,])", r"\1", text)
    return text.strip()


def _proactive_header(lang: str, evt: dict[str, Any], *,
                      comfort: bool = False) -> str:
    """One header line: '✨ Привет, это Ника! Спасибо за депозит 10 USD'.

    The occasion phrase rides ON THE SAME line as the persona header (a second
    chrome line read as a system stamp). A comfort touch (the player just lost
    money) carries no occasion phrase — congratulation chrome would be tone-deaf.
    """
    header = retention._rtn_text("rtn_ping_header", lang).strip()
    phrase = "" if comfort else _trigger_phrase(evt, lang)
    if not phrase:
        return header
    if not header:
        return phrase
    if header[-1] not in "!?.…":
        header += "!"
    return f"{header} {phrase}"



async def _send_touch(product: dict[str, Any], ru: dict[str, Any],
                      evt: dict[str, Any], decision: dict[str, Any], *,
                      comfort: bool, cfg: dict[str, Any]
                      ) -> tuple[bool, float, Optional[str]]:
    """Generate + deliver the agent-decided touch. Returns
    (delivered, generation_cost_usd, detail)."""
    pid = int(product["id"])
    rid = int(ru["id"])
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

    occasion = occasion_for(evt)
    draft = await chat_service.generate_retention_ping(
        session, idle_days=0, reason="", intent=decision["intent"],
        photo_candidates=candidates, occasion=occasion, comfort=comfort)
    if draft is None:
        await db.record_retention_ping(pid, rid, None, decision["action"],
                                       "failed", detail="v2:model_error")
        return False, 0.0, "model_error"
    gen_cost = float(draft.ai_meta.get("cost_usd") or 0)

    # The trigger + occasion travel with the turn: persisted on the message row
    # (so the prompt history and the admin transcript can explain WHY the bot
    # wrote) and shown to the player as a human occasion phrase merged into the
    # header line ("✨ Привет, это Ника! Спасибо за депозит 10 USD") — never the
    # raw event name.
    event_name = str(evt.get("event_name") or "")
    ping_context = f"{event_name}: {occasion}" if event_name else occasion
    header_line = _proactive_header(draft.lang, evt, comfort=comfort)

    # Proactive touches may be delivered silently (no sound on the player's
    # phone) — the hot per-product `retention.silent_notifications` knob. The
    # send mechanics live in the delivery seam (delivery.py) — the one place
    # a proactive message leaves the service, shared with the idle ladder.
    channel = delivery.channel_for_product(
        product, token, silent=bool(cfg.get("silent_notifications")))
    # A comfort touch never carries a photo or a play-CTA button.
    delivered, detail, link_attached = await delivery.deliver_draft(
        channel, ru, draft, header=header_line, session_id=session["id"],
        photo_fallback_caption=retention.fallback_photo_caption(draft.lang),
        allow_photo=not comfort, allow_link=not comfort)
    if not delivered:
        log.warning("retention_v2_send_failed product=%s player=%s detail=%s",
                    pid, ru.get("player_id"), detail)

    if delivered:
        await db.persist_ping_turn(session["id"], draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid,
                                   ping_context=ping_context,
                                   link_url=draft.link_url if link_attached else None)
        # Shared anti-annoyance state: the same ledger/counters the idle
        # ladder uses, so caps and min-gap hold across regimes.
        sent_detail = f"v2:{evt.get('event_name')}"
        if detail == "photo_fallback_text":
            sent_detail += " (photo fallback: text)"
        await db.record_retention_ping(
            pid, rid, None, decision["action"], "sent",
            detail=sent_detail, cost_usd=gen_cost)
        return True, gen_cost, None

    # Generated but undelivered: the cost still lands in ai_interaction_logs.
    await delivery.account_undelivered_generation(
        session["id"], draft, detail, product_id=pid,
        label="v2_touch_undelivered")
    await db.record_retention_ping(pid, rid, None, decision["action"],
                                   "failed", detail=f"v2:{detail}",
                                   cost_usd=gen_cost)
    return False, gen_cost, detail
