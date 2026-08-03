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

from app.chat import chat_service
from app.core import config
from app.core import db
from app.retention import delivery
from app.ai import openai_client
from app.ai import prompts
from app.retention import outcomes
from app.retention import retention
from app.core import settings
from app.core import tenancy
from app.i18n import translations

log = logging.getLogger(__name__)

# Arbitrary but stable: the advisory-lock key for the agent sweep.
_ADVISORY_LOCK_KEY = 0x50494E56  # "PINV"

# How long a manual «Run now» / «Process queue now» waits behind the worker's
# advisory lock before giving up with WorkerBusy. Long enough to queue behind
# a normal sweep tail, short enough that a wedged sweep can't hang the admin's
# HTTP request forever (the dedicated lock connection has no command_timeout).
_MANUAL_LOCK_WAIT_SEC = 120


class WorkerBusy(RuntimeError):
    """A manual run could not take the worker lock within the wait ceiling."""

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


_model_sem: Optional[asyncio.Semaphore] = None
_model_sem_width = 0


def _model_slot() -> Any:
    """The fleet-wide ceiling on concurrent BACKGROUND model calls.

    Rebuilt when the knob changes (a Semaphore's size is fixed at
    construction), and deliberately per-process: it bounds this worker, and the
    number of workers is a deploy decision.
    """
    global _model_sem, _model_sem_width
    width = settings.global_retention_int(
        "agent_model_concurrency", config.RETENTION_AGENT_MODEL_CONCURRENCY,
        1, 256)
    if _model_sem is None or width != _model_sem_width:
        _model_sem = asyncio.Semaphore(width)
        _model_sem_width = width
    return _model_sem


async def _sleep_or_stop(seconds: float,
                         stop: Optional[asyncio.Event]) -> bool:
    """Sleep, waking early when the stop flag is raised. True = time to exit.

    A plain `asyncio.sleep` in a worker loop means a deploy waits out the full
    interval (or gets killed mid-batch). Every loop in this module sleeps
    through here so SIGTERM is honoured within milliseconds.
    """
    if stop is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def scheduler_loop(stop: Optional[asyncio.Event] = None) -> None:
    """Drain the event queues on the hot-reloaded worker cadence."""
    log.info("retention_agent_scheduler_started interval_sec=%s",
             worker_interval_sec())
    while True:
        if await _sleep_or_stop(worker_interval_sec(), stop):
            log.info("retention_agent_scheduler_stopping")
            return
        try:
            stats = await run_due_events(stop=stop)
            if stats.get("decided") or stats.get("sent") or stats.get("failed"):
                log.info("retention_v2_sweep_done stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            log.exception("retention_v2_sweep_failed")


def worker_id() -> str:
    """Stable-per-process id stamped on every lease (who holds this row).

    Makes "which replica wedged the queue" answerable from the rows themselves,
    which is the only way to tell once more than one worker exists.
    """
    global _WORKER_ID
    if _WORKER_ID is None:
        import os
        import socket
        replica = (os.environ.get("RAILWAY_REPLICA_ID")
                   or os.environ.get("HOSTNAME") or socket.gethostname())
        _WORKER_ID = f"{replica}-{os.getpid()}"[:64]
    return _WORKER_ID


_WORKER_ID: Optional[str] = None


async def run_due_events(*, stop: Optional[asyncio.Event] = None
                         ) -> dict[str, Any]:
    """One drain pass across all agent-enabled products, IN PARALLEL.

    There used to be ONE global advisory lock around this whole function, with
    the products walked in a `for` loop inside it. That made the tick duration
    the SUM over every product — and, since the lock was global, a second
    service instance did no background work at all. It also meant one product's
    90-second model call stalled every other casino's queue.

    Now each product drains on its own, concurrently, bounded by
    `worker_product_concurrency`. Nothing needs a product-level lock any more:
    the claim itself is what prevents double work (an atomic lease + a
    per-player exclusion, see `run_product_events`), so several workers may
    drain the same product at the same time and still never react twice to one
    event.
    """
    products = await db.list_retention_products()
    if not products:
        return {"products": 0}
    width = settings.global_retention_int(
        "worker_product_concurrency",
        config.RETENTION_WORKER_PRODUCT_CONCURRENCY, 1, 32)
    sem = asyncio.Semaphore(width)

    async def _one(product: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            if stop is not None and stop.is_set():
                return {}
            return await run_product_events(product, stop=stop)

    results = await asyncio.gather(*(_one(p) for p in products),
                                   return_exceptions=True)
    totals: dict[str, Any] = {"products": 0, "events": 0, "decided": 0,
                              "sent": 0}
    for product, res in zip(products, results):
        if isinstance(res, BaseException):
            log.exception("retention_v2_product_failed product=%s",
                          product.get("id"), exc_info=res)
            totals["errors"] = totals.get("errors", 0) + 1
            continue
        totals["products"] += 1
        for k, v in (res or {}).items():
            if isinstance(v, int) and v:
                totals[k] = totals.get(k, 0) + v
    return totals


async def run_product_events_locked(product: dict[str, Any], *,
                                    limit: Optional[int] = None
                                    ) -> dict[str, Any]:
    """The admin «Process queue now» path: drain one product, right now.

    Historically this took the worker's global advisory lock so a button press
    could not race the sweep into a double send. That lock is gone — the claim
    is the mutual exclusion now (an atomic lease plus a per-player in-flight
    exclusion), so the button simply drains whatever the worker has not taken.
    It keeps its own name and the WorkerBusy escape hatch so the API contract
    is unchanged; the only behavioural difference is that it can no longer 409
    just because the worker happens to be mid-sweep.
    """
    return await run_product_events(product, limit=limit,
                                    ignore_send_delay=True)


async def maintenance_loop(stop: Optional[asyncio.Event] = None) -> None:
    """Everything that is NOT event processing, on its own clock.

    These six sweeps used to run in the tail of `run_product_events`, once per
    product per tick, in sequence — which put attribution, scoring, activity
    profiles, journeys and the idle ladder squarely on the critical path of
    reacting to a deposit. Here they are independent: each is paced per product
    through `retention_worker_jobs` (so the interval survives a deploy and
    holds across worker instances), each swallows its own errors, and a slow
    one cannot delay a fast one.

    The queue's own housekeeping — reclaiming expired leases — rides the same
    loop and runs FIRST, because it is what turns a killed worker's in-flight
    batch back into work instead of a silent loss.
    """
    log.info("retention_maintenance_loop_started")
    # A crash-restart may leave rows leased by this worker's previous life;
    # picking them up immediately (rather than after their lease) is the whole
    # point of a fast restart.
    try:
        await _reclaim_leases()
    except Exception:  # noqa: BLE001
        log.exception("retention_lease_reclaim_failed")
    # One-time (converging) migration of pre-lifecycle rows. Batched and run
    # here rather than at boot: it can cover millions of rows and must never be
    # able to time out the init transaction.
    try:
        moved = await db.backfill_event_lifecycle()
        if moved:
            log.info("retention_event_lifecycle_backfilled rows=%s", moved)
    except Exception:  # noqa: BLE001 - the claim does not depend on it
        log.exception("retention_event_lifecycle_backfill_failed")
    while True:
        interval = settings.global_retention_int(
            "maintenance_interval_sec",
            config.RETENTION_MAINTENANCE_INTERVAL_SEC, 5, 3600)
        if await _sleep_or_stop(interval, stop):
            log.info("retention_maintenance_loop_stopping")
            return
        try:
            await _reclaim_leases()
            await run_maintenance_pass(stop=stop)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            log.exception("retention_maintenance_pass_failed")


async def _reclaim_leases() -> None:
    """Return abandoned event/delivery leases to their queues."""
    max_attempts = settings.global_retention_int(
        "event_max_attempts", config.RETENTION_EVENT_MAX_ATTEMPTS, 1, 50)
    events = await db.reclaim_expired_event_leases(max_attempts=max_attempts)
    deliveries = await db.reclaim_expired_delivery_leases()
    if events or deliveries:
        log.warning("retention_leases_reclaimed events=%s deliveries=%s",
                    events, deliveries)


# (job name, settings key for the interval, config default). The interval is
# read per product so a busy tenant can be swept harder than a quiet one.
_MAINTENANCE_JOBS: tuple[tuple[str, str, str], ...] = (
    ("attribution", "attribution_interval_sec",
     "RETENTION_ATTRIBUTION_INTERVAL_SEC"),
    ("scoring", "scoring_interval_sec", "RETENTION_SCORING_INTERVAL_SEC"),
    ("profiles", "profile_interval_sec", "RETENTION_PROFILE_INTERVAL_SEC"),
    ("journeys", "journey_interval_sec", "RETENTION_JOURNEY_INTERVAL_SEC"),
)


async def run_maintenance_pass(*, stop: Optional[asyncio.Event] = None
                               ) -> dict[str, Any]:
    """One maintenance pass over every product, products in parallel."""
    products = await db.list_retention_products()
    if not products:
        return {"products": 0}
    width = settings.global_retention_int(
        "worker_product_concurrency",
        config.RETENTION_WORKER_PRODUCT_CONCURRENCY, 1, 32)
    sem = asyncio.Semaphore(width)

    async def _one(product: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            if stop is not None and stop.is_set():
                return {}
            return await run_product_maintenance(product)

    results = await asyncio.gather(*(_one(p) for p in products),
                                   return_exceptions=True)
    totals: dict[str, Any] = {"products": 0}
    for res in results:
        if isinstance(res, BaseException):
            log.exception("retention_maintenance_product_failed", exc_info=res)
            continue
        totals["products"] += 1
        for k, v in (res or {}).items():
            if isinstance(v, int) and v:
                totals[k] = totals.get(k, 0) + v
    return totals


async def run_product_maintenance(product: dict[str, Any]) -> dict[str, Any]:
    """The per-product half of the maintenance pass.

    Each sweep is individually paced and individually error-guarded: the whole
    point of taking them off the event path is that one of them being slow or
    broken stops mattering to everything else.
    """
    pid = int(product["id"])
    stats: dict[str, Any] = {}
    with tenancy.scoped_product(pid):
        cfg = settings.retention()
        # The idle ladder paces itself (idle_sweep_interval_sec) inside
        # run_product_idle_pings, so it is called unconditionally — but not
        # while the event queue is so far behind that re-engaging quiet
        # players would be competing with reacting to live ones.
        try:
            idle_pause_at = int(cfg.get("queue_degrade_idle_sec")
                                or config.RETENTION_QUEUE_DEGRADE_IDLE_SEC)
            if await db.retention_queue_lag(pid) >= idle_pause_at:
                stats["idle_paused"] = 1
            else:
                from app.retention import retention_idle
                idle = await retention_idle.run_product_idle_pings(product, cfg)
                for k in ("sent", "failed"):
                    if idle.get(k):
                        stats[f"idle_{k}"] = idle[k]
        except Exception:  # noqa: BLE001
            log.exception("retention_idle_sweep_failed product=%s", pid)

        for job, _key, _default in _MAINTENANCE_JOBS:
            try:
                res = await _run_maintenance_job(job, product, pid, cfg)
                for k, v in (res or {}).items():
                    if isinstance(v, int) and v:
                        stats[f"{job}_{k}" if k != job else k] = v
            except Exception:  # noqa: BLE001 - one sweep must not stop the rest
                log.exception("retention_%s_sweep_failed product=%s", job, pid)
                await db.finish_worker_job(pid, job, status="error",
                                           error="see logs")
        # Delivery retries: only meaningful while the send worker is off (with
        # it on, the send loop's own claim picks failed rows back up).
        if not settings.global_retention_bool(
                "send_worker_enabled", config.RETENTION_SEND_WORKER_ENABLED):
            try:
                from app.retention import channels
                retried = await channels.drain_delivery_retries(product, cfg)
                if retried:
                    stats["delivery_retries"] = retried
            except Exception:  # noqa: BLE001
                log.exception("delivery_retry_sweep_failed product=%s", pid)
    return stats


async def _run_maintenance_job(job: str, product: dict[str, Any], pid: int,
                               cfg: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one paced sweep and record how it went."""
    started = _dt.datetime.now(_dt.timezone.utc)
    if job == "attribution":
        res = await outcomes.run_product_attribution(pid)
    elif job == "scoring":
        from app.retention import scoring
        res = await scoring.run_product_scoring(pid, cfg)
    elif job == "profiles":
        from app.retention import frequency
        res = await frequency.run_product_activity_profiles(pid, cfg)
    elif job == "journeys":
        from app.retention import journeys
        res = await journeys.run_product_journeys(product, cfg)
    else:  # pragma: no cover - the table above is the whole set
        return {}
    if (res or {}).get("skipped"):
        return {}
    took = int((_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()
               * 1000)
    await db.finish_worker_job(pid, job, status="ok", duration_ms=took)
    return res or {}


def _max_priority_for_lag(lag_sec: int, cfg: dict[str, Any]) -> int:
    """The backpressure ladder: which lanes may still be claimed at this lag.

    A backlog is not an excuse to fall behind on the events a player is
    actually waiting for. As the age of the oldest untouched event grows, the
    drain sheds work from the bottom: first the state-food lane, then the
    default lane, so deposits and KYC outcomes keep moving at full speed while
    the cheap stuff waits for the queue to recover.
    """
    p3_at = int(cfg.get("queue_degrade_p3_sec")
                or config.RETENTION_QUEUE_DEGRADE_P3_SEC)
    p2_at = int(cfg.get("queue_degrade_p2_sec")
                or config.RETENTION_QUEUE_DEGRADE_P2_SEC)
    if lag_sec >= max(p2_at, p3_at):
        return 2
    if lag_sec >= p3_at:
        return 3
    return 5


def _group_by_player(events: list[dict[str, Any]]
                     ) -> list[list[dict[str, Any]]]:
    """Split a claimed batch into per-player chains, oldest event first.

    Processing is parallel ACROSS players and strictly serial WITHIN a player:
    two decisions for the same player at the same time read the same guard
    counters before either writes, which is how one player collects two
    "messages" for two events that should have produced one.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for evt in events:
        groups.setdefault(str(evt.get("player_id") or ""), []).append(evt)
    return [sorted(g, key=lambda e: e["id"]) for g in groups.values()]


async def run_product_events(product: dict[str, Any], *,
                             limit: Optional[int] = None,
                             ignore_send_delay: bool = False,
                             stop: Optional[asyncio.Event] = None
                             ) -> dict[str, Any]:
    """Drain one product's queued events through the decision pipeline.

    Events are CLAIMED as a LEASE (db.claim_retention_events): the worker
    sweep, the admin «Process queue now» button and any other service instance
    can all run concurrently — each event still reaches the pipeline exactly
    once, and an event whose processing dies is retried instead of vanishing.
    Every claimed row MUST be closed here (complete / fail / release), which is
    why the batch is bracketed in a try/finally.

    The batch is then grouped BY PLAYER and the groups run concurrently
    (`worker_player_concurrency`), each group serially. Under a growing backlog
    the claim sheds low lanes (`_max_priority_for_lag`).

    The worker honours the humanizing SEND DELAY (an event becomes claimable a
    per-event random `v2_send_delay_min_sec`..`v2_send_delay_max_sec` after it
    arrived — an instant reaction to a deposit reads as surveillance, not
    warmth); `ignore_send_delay` is the admin «Process queue now» override.

    QUIET HOURS defer the pipeline instead of consuming events: the worker
    simply does not CLAIM during the window, so a night-time deposit gets its
    warm note in the morning (in a casino the night IS peak deposit time; the
    freshness cap in `_is_decision_worthy` bounds how stale a reaction may
    get). The admin «Process queue now» button claims regardless.

    The maintenance sweeps (idle ladder, attribution, scoring, activity
    profiles, journeys, delivery retries) used to run in this function's tail,
    which put every one of them on the critical path of event processing. They
    are their own loops now — see `maintenance_loop`.
    """
    pid = int(product["id"])
    with tenancy.scoped_product(pid):
        cfg = settings.retention()
        stats: dict[str, Any] = {"events": 0, "decided": 0, "sent": 0}
        if not cfg.get("v2_enabled"):
            return {"agent": "disabled"}
        if not ignore_send_delay and _in_quiet_hours(cfg):
            return {"agent": "quiet_hours_deferred"}
        # Budget brake: the idle ladder always honoured the daily AI budget,
        # the event path never did — so a runaway event storm could spend past
        # the cap all day. Checked before claiming, so a capped product costs
        # one cheap SELECT per tick.
        budget = float(cfg.get("v2_daily_budget_usd") or 0)
        if budget > 0 and await db.retention_v2_cost_today(pid) >= budget:
            await db.finish_worker_job(pid, "events", status="budget_reached")
            return {"agent": "daily_budget_reached"}

        lag = await db.retention_queue_lag(pid)
        max_priority = _max_priority_for_lag(lag, cfg)
        if max_priority < 5:
            log.warning("retention_queue_degraded product=%s lag_sec=%s "
                        "max_priority=%s", pid, lag, max_priority)
            await db.log_admin_event_sampled(
                None, "retention_queue_degraded",
                {"lag_sec": lag, "max_priority": max_priority},
                product_id=pid)

        batch = int(limit or cfg["ping_batch_size"])
        delay_min = (0 if ignore_send_delay
                     else int(cfg.get("v2_send_delay_min_sec") or 0))
        delay_max = (0 if ignore_send_delay
                     else int(cfg.get("v2_send_delay_max_sec") or 0))
        events = await db.claim_retention_events(
            pid, limit=batch, delay_min_sec=delay_min,
            delay_max_sec=max(delay_max, delay_min),
            lease_sec=settings.global_retention_int(
                "event_lease_sec", config.RETENTION_EVENT_LEASE_SEC, 60, 3600),
            worker_id=worker_id(), max_priority=max_priority)
        if not events:
            return {"events": 0, "lag_sec": lag}

        # Every claimed id starts OWED: whatever happens below, the row is
        # either closed or handed back. Losing this bookkeeping is what made
        # the old pipeline drop events on the floor.
        owed: set[int] = {int(e["id"]) for e in events}
        counters = {"decided": 0, "sent": 0, "failed": 0, "dead": 0}
        max_attempts = settings.global_retention_int(
            "event_max_attempts", config.RETENTION_EVENT_MAX_ATTEMPTS, 1, 50)
        backoff = settings.global_retention_int(
            "event_backoff_base_sec",
            config.RETENTION_EVENT_BACKOFF_BASE_SEC, 1, 3600)
        width = settings.global_retention_int(
            "worker_player_concurrency",
            config.RETENTION_WORKER_PLAYER_CONCURRENCY, 1, 64)
        sem = asyncio.Semaphore(width)

        async def _run_group(group: list[dict[str, Any]]) -> None:
            async with sem:
                for evt in group:
                    pk = int(evt["id"])
                    if stop is not None and stop.is_set():
                        return  # the finally below hands the rest back
                    try:
                        outcome = await _process_event(product, evt, cfg)
                        await db.complete_retention_event(pk)
                        owed.discard(pk)
                        if outcome:
                            counters["decided"] += 1
                            if outcome == "sent":
                                counters["sent"] += 1
                    except Exception as exc:  # noqa: BLE001 - one bad event must not wedge the queue
                        status = await db.fail_retention_event(
                            pk, repr(exc), max_attempts=max_attempts,
                            backoff_base_sec=backoff)
                        owed.discard(pk)
                        counters["failed"] += 1
                        if status == "dead":
                            counters["dead"] += 1
                        log.exception(
                            "retention_v2_event_failed product=%s event=%s "
                            "status=%s", pid, pk, status)
                        # A player's later events are not independent of the
                        # one that just failed (the reaction they'd produce
                        # assumes it landed), so the rest of the chain waits
                        # for the retry rather than running out of order.
                        return

        try:
            await asyncio.gather(
                *(_run_group(g) for g in _group_by_player(events)),
                return_exceptions=True)
        finally:
            # Anything still owed (a stop signal, a cancelled task, an error
            # outside the per-event guard) goes straight back to 'pending'
            # rather than waiting out its lease.
            if owed:
                try:
                    await db.release_retention_events(sorted(owed))
                except Exception:  # noqa: BLE001 - the lease reclaimer is the backstop
                    log.exception("retention_v2_release_failed product=%s", pid)

        stats.update(events=len(events), lag_sec=lag, **counters)
        if counters["dead"]:
            await db.log_admin_event(
                None, "retention_events_dead_lettered",
                {"count": counters["dead"]}, product_id=pid)
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

    state = {
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
    # ADDITIVE scoring dimensions (dormancy cohort / RFM / value tier / VIP
    # segment, DOC-5) — appended from the sweep-maintained cache only when
    # scoring is enabled; the base fields above never change semantics.
    from app.retention import scoring  # late: scoring imports this module
    return scoring.annotate_state(state, ru, cfg)


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
    # RG FIRST — responsible gaming beats everything (holdout, frequency, the
    # model). A permanent status denies outright; a conditional one lets a
    # non-game touch through WITH the no-play-talk constraint (collected below).
    rg_constraint: Optional[str] = None
    from app.retention import rg_guard  # late: rg_guard imports db only
    rg_verdict = await rg_guard.gate(product_id, ru, "event_reaction", cfg)
    if rg_verdict is not None:
        if rg_verdict.get("deny"):
            return {"allow": False, "reasons": [rg_verdict["reason"]],
                    "allowed_actions": [], "constraints": [],
                    "comfort": False,
                    "holdout_group": ru.get("holdout_group") or "treatment"}
        rg_constraint = rg_verdict.get("constraint")

    reasons: list[str] = []
    if not ru.get("subscribed"):
        reasons.append("not_subscribed")
    if ru.get("pings_muted"):
        reasons.append("player_opted_out")
    if ru.get("unreachable"):
        reasons.append("bot_blocked_by_player")
    # HOLDOUT (measurement): a deterministically held-out player never gets a
    # proactive touch — instead a VIRTUAL outcome row records the would-have
    # moment so uplift has a base rate. Checked only when the hard denies
    # passed (a blocked player would not have been touched anyway, so a
    # virtual row there would poison the base rate), and BEFORE any further
    # guard work or model call.
    if not reasons:
        from app.retention import measurement  # late: measurement imports db only
        grp = await measurement.resolve_holdout_group(product_id, ru, cfg)
        if grp == measurement.HOLDOUT:
            await measurement.open_virtual_outcome(product_id, ru,
                                                   evt.get("event_name"))
            return {"allow": False, "reasons": ["held_out"],
                    "allowed_actions": [], "constraints": [],
                    "comfort": False, "holdout_group": grp}
    # Frequency: the ADAPTIVE priority/cohort-aware gate when enabled (DOC-4;
    # P1/P2 touches are never cut, VIP cohorts may run relaxed rows), else the
    # legacy static guards bit-for-bit.
    from app.retention import frequency
    if evt.get("event_name") == "bet_settled":
        _touch_type = "loss_rescue"
    elif evt.get("event_name") in ("level_up", "class_up"):
        _touch_type = "level_up"
    else:
        _touch_type = "event_reaction"
    fq = await frequency.gate(product_id, ru, _touch_type, cfg)
    if fq is not None:
        if not fq["allow"] and fq["reason"]:
            reasons.append(fq["reason"])
    else:
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
    if rg_constraint:
        constraints.append(rg_constraint)
    if comfort:
        constraints.append(
            "comfort mode - the player recently lost money: empathetic tone "
            "only, never mention amounts, no play invitation, no photo, "
            "no link")
    elif evt.get("event_name") in _PHOTO_EVENTS and not rg_constraint:
        allowed.append("photo")
    return {
        "allow": not reasons,
        "reasons": reasons,
        "allowed_actions": allowed,
        "constraints": constraints,
        "comfort": comfort,
        "holdout_group": ru.get("holdout_group") or "treatment",
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
    recent, history, touches = await asyncio.gather(
        db.recent_retention_events_for_player(product_id, player_id, limit=8),
        _history_tail(ru),
        _touch_history(product_id, player_id),
    )
    messages = prompts.build_retention_v2_decision_messages(
        state=state, event=evt, recent_events=recent,
        history_tail=history,
        allowed_actions=guard["allowed_actions"],
        constraints=guard["constraints"],
        touch_history=touches)
    client = await openai_client.client_for_product(product_id)
    try:
        # `agent`: a proactive decision nobody is waiting on — its own timeout
        # profile, no fallback-key race (see openai_client.CALL_PURPOSES).
        # Held under the FLEET-wide background ceiling: the per-key semaphore
        # inside the client bounds one product's key, but with products drained
        # in parallel and players sharded inside each, a burst could otherwise
        # open hundreds of completions at once and turn a queue spike straight
        # into a bill.
        async with _model_slot():
            result = await client.complete(messages, purpose="agent")
    except Exception as exc:  # noqa: BLE001 - a model failure skips this event
        await db.log_ai_interaction(
            None, settings.model()["model"], "none", 0, 0, 0, 0.0, 0, False,
            f"v2_decision: {exc.__class__.__name__}", product_id=product_id,
            consumer="telegram", source="agent")
        log.warning("retention_v2_decision_model_failed product=%s error=%s",
                    product_id, exc)
        return None, 0.0
    cost = openai_client.compute_cost(result.model, result.tokens_in,
                                      result.tokens_out, result.cached_in)
    # The decision call spends on the PROACTIVE AGENT (no session to hang it
    # on) — labelled so it lands in the agent bucket, not the media one.
    await db.log_ai_interaction(
        None, result.model, result.key_used, result.tokens_in,
        result.tokens_out, result.cached_in, cost, result.latency_ms,
        True, None, product_id=product_id,
        consumer="telegram", source="agent")
    return parse_decision(result.text, guard["allowed_actions"]), float(cost or 0)


async def _touch_history(product_id: int, player_id: str
                         ) -> list[dict[str, Any]]:
    """This player's last proactive touches WITH their measured outcome — the
    feedback the decision prompt weighs ("the last three went unanswered").
    Best-effort: the agent still decides without it."""
    if not player_id:
        return []
    try:
        return await db.recent_touch_outcomes(product_id, player_id, limit=6)
    except Exception:  # noqa: BLE001 - feedback is best-effort context
        return []


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

    # Return detection (scoring): real activity flips a dormant cohort back to
    # 'active' on the HOT path — a returned player must never be treated as
    # dormant for up to a sweep interval (recovery journeys key on the cohort).
    from app.retention import scoring
    if (scoring.scoring_enabled(cfg)
            and evt.get("event_name") in ("session_started", "session_ended",
                                          "deposit_confirmed", "bet_settled")):
        await scoring.mark_recovered(pid, ru)

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

    # OFFER RESOLVE (deterministic, BEFORE the model): when the event matches
    # an enabled offer trigger and every offer guard (RG, VIP suppression,
    # eligibility, cooldown, budget) says yes, 'grant_offer' joins the model's
    # allowed actions with a constraint line describing the stimulus. The
    # model can only choose it — never conjure it.
    from app.retention import offers
    offer = None
    offer_deny: Optional[str] = None
    if offers.offers_enabled(cfg):
        trigger_key = offers.classify_offer_trigger(evt, state, cfg)
        if trigger_key:
            offer, offer_deny = await offers.resolve_offer(
                pid, ru, trigger_key, cfg, state)
            if offer is not None:
                guard = dict(guard)
                guard["allowed_actions"] = (list(guard["allowed_actions"])
                                            + ["grant_offer"])
                guard["constraints"] = (list(guard["constraints"])
                                        + [offers.offer_constraint_line(offer)])
            elif offer_deny:
                guard = dict(guard)
                guard["offer_denied"] = offer_deny

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
    facts: dict[str, Any] = {}
    total_cost = decision_cost
    grant: Optional[dict[str, Any]] = None
    # RESERVE the ledger row BEFORE anything the player can see happens.
    # Delivery of an event is at-least-once (an expired lease, a reclaim after
    # a crash), and the unique partial index on (product_id, event_pk) is what
    # stops that from becoming at-least-twice delivery of the MESSAGE: a
    # replay gets None here and returns, in front of the bonus grant and the
    # send. What actually happened is written back at the end. The insert used
    # to sit AFTER the send, which left nothing between a retry and a second
    # message to the player.
    decision_id = await db.insert_retention_v2_decision(
        pid, retention_user_id=int(ru["id"]), player_id=player_id,
        trigger_kind="event", event_pk=evt["id"],
        event_name=evt.get("event_name"), state=state, guard=guard,
        action=action, intent=decision["intent"], tone=decision["tone"],
        reason=decision["reason"], dry_run=dry_run, delivered=False,
        cost_usd=decision_cost)
    if decision_id is None:
        log.info("retention_v2_replay_skipped product=%s event=%s player=%s",
                 pid, evt.get("id"), player_id)
        return "duplicate"
    if action == "grant_offer" and offer is not None:
        # Order of operations (invariant): create the pending bonus -> the
        # partner confirms -> ONLY THEN the persona writes a message that
        # mentions it. fraud_hold -> message without the bonus; failed ->
        # silence. Idempotent by the event id, so a retried event can never
        # grant twice.
        grant = await offers.do_offer_grant(product, ru, offer, evt["id"],
                                            cfg)
        status = str(grant.get("status") or "failed")
        detail = f"offer:{status}"
        decision = dict(decision)
        if status == "granted":
            desc = str(offer.get("description") or offer.get("offer_key"))
            decision["intent"] = (
                f"{decision['intent']} | A real gift was JUST credited to "
                f"the player's account: {desc}. Mention it warmly and "
                "concretely; never invent terms beyond this description."
            )[:900]
            if not dry_run:
                delivered, send_cost, sdetail, facts = await _send_touch(
                    product, ru, evt, decision, comfort=guard["comfort"],
                    cfg=cfg)
                total_cost += send_cost
                if sdetail:
                    detail = f"offer:granted; {sdetail}"
        elif status == "dry_run":
            pass  # shadow grant: ledger only, nothing sent
        elif status == "fraud_hold":
            # The touch may still go out — without any mention of a bonus.
            if not dry_run:
                delivered, send_cost, sdetail, facts = await _send_touch(
                    product, ru, evt, decision, comfort=guard["comfort"],
                    cfg=cfg)
                total_cost += send_cost
        else:  # failed — never promise what was not credited
            action = "silence"
            decision["reason"] = (decision.get("reason") or "") + \
                " (offer grant failed -> silence)"
    elif action in ("message", "photo") and not dry_run:
        delivered, send_cost, detail, facts = await _send_touch(
            product, ru, evt, decision, comfort=guard["comfort"], cfg=cfg)
        total_cost += send_cost
    # Write back what the reserved row actually turned into (the action can
    # still change: a failed offer grant demotes the touch to silence).
    await db.update_retention_v2_decision(
        decision_id, action=action, intent=decision["intent"],
        reason=decision["reason"], delivered=delivered, detail=detail,
        cost_usd=total_cost)
    if grant is not None:
        # Back-link the grant to its ledger row (the row id exists only now).
        try:
            await db.link_offer_grant_decision(pid, grant["offer_grant_id"],
                                               decision_id)
        except Exception:  # noqa: BLE001 - the grant row itself is durable
            log.exception("offer_grant_link_failed product=%s", pid)
    # Attribution: a touch that actually went out opens an outcome row, so the
    # next decision for this player can read what he did with the last one.
    if delivered:
        await outcomes.record(
            pid, ru, kind="proactive", session_id=facts.get("session_id"),
            decision_id=decision_id, event_name=evt.get("event_name"),
            action=action, tone=decision["tone"],
            photo_id=facts.get("photo_id"), media_type=facts.get("media_type"),
            link_url=facts.get("link_url"), cost_usd=total_cost)
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
    # Event-triggered journey enrollment (DOC-6a): AFTER the single reaction —
    # enrollment only schedules future steps, it never touches the player now.
    try:
        from app.retention import journeys
        await journeys.match_event_journeys(product, evt, ru, state, cfg)
    except Exception:  # noqa: BLE001 - matching must never wedge the pipeline
        log.exception("journey_matching_failed product=%s", pid)
    return "sent" if delivered else action


# ---------------------------------------------------------------------------
# Sending (the persona writes; delivery via the delivery.py seam)
# ---------------------------------------------------------------------------
# Chrome detail per event for the header occasion phrase — unlike the
# model-facing occasion line, the CHROME may name the amount (the player just
# saw the exact number in their cashier; the phrase confirming "which deposit"
# is what makes the note read as personal, not cryptic).
def _media_type_of(candidates: list[dict[str, Any]],
                   photo_id: Optional[int]) -> Optional[str]:
    """The kind of media that was actually sent ('photo'/'video'), read off the
    candidate row the model picked — the attribution cut splits by it."""
    if photo_id is None:
        return None
    for c in candidates or []:
        if int(c.get("id", 0)) == int(photo_id):
            return c.get("media_type") or "photo"
    return None


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
                      ) -> tuple[bool, float, Optional[str], dict[str, Any]]:
    """Generate + deliver the agent-decided touch. Returns
    (delivered, generation_cost_usd, detail, facts).

    `facts` is what the touch actually carried (session, media, CTA link) — the
    caller opens the attribution row with it once the decision row exists.
    """
    pid = int(product["id"])
    rid = int(ru["id"])
    facts: dict[str, Any] = {}
    token = await db.get_product_telegram_token(pid)
    if not token:
        return False, 0.0, "no_bot_token", facts
    lang = retention.resolve_user_lang(ru)
    session = await retention._ensure_session(pid, ru, lang)
    if session is None:
        return False, 0.0, "no_session", facts
    session["user_context"] = retention._user_context_from_ru(ru)
    facts["session_id"] = session["id"]

    candidates: list[dict[str, Any]] = []
    if decision["action"] == "photo" and not comfort:
        candidates = await retention.select_photo_candidates(
            pid, ru, "", bypass_cooldown=True)

    occasion = occasion_for(evt)
    draft = await chat_service.generate_retention_ping(
        session, idle_days=0, reason="", intent=decision["intent"],
        photo_candidates=candidates, occasion=occasion, comfort=comfort,
        touch_history=await _touch_history(pid, ru.get("player_id") or ""))
    if draft is None:
        await db.record_retention_ping(pid, rid, None, decision["action"],
                                       "failed", detail="v2:model_error")
        return False, 0.0, "model_error", facts
    gen_cost = float(draft.ai_meta.get("cost_usd") or 0)

    # The trigger + occasion travel with the turn: persisted on the message row
    # (so the prompt history and the admin transcript can explain WHY the bot
    # wrote) and shown to the player as a human occasion phrase merged into the
    # header line ("✨ Привет, это Ника! Спасибо за депозит 10 USD") — never the
    # raw event name.
    event_name = str(evt.get("event_name") or "")
    ping_context = f"{event_name}: {occasion}" if event_name else occasion
    header_line = _proactive_header(draft.lang, evt, comfort=comfort)

    payload = {
        "text": draft.text,
        "lang": draft.lang,
        "photo_id": draft.photo_id,
        "link_url": draft.link_url,
        "link_label": draft.link_label,
        # The generation's accounting rides along so invariant §4 survives a
        # send that happens in another process (or never happens at all).
        "ai_meta": draft.ai_meta,
        "header": header_line,
        "ping_context": ping_context,
        "action": decision["action"],
        "event_name": event_name,
        "comfort": bool(comfort),
        "session_id": session["id"],
        "retention_user_id": rid,
        "fallback_caption": retention.fallback_media_caption(
            draft.lang, draft.photo_id, candidates),
        "media_type": _media_type_of(candidates, draft.photo_id),
        "gen_cost": gen_cost,
    }

    # SEND STAGE. With the send worker on, a decision does not send — it
    # ENQUEUES, and a separate loop delivers under a per-channel token bucket.
    # That is what keeps a 10k broadcast from competing with the decision
    # pipeline for the same worker, and what makes an unreachable Telegram a
    # retry instead of a lost touch. Off = the original inline send, so the
    # rollout is a flag flip either way.
    if settings.global_retention_bool("send_worker_enabled",
                                      config.RETENTION_SEND_WORKER_ENABLED):
        from app.retention import channels  # lazy: channels imports this module
        delivery_id = channels.delivery_id_for(pid, f"v2:{evt['id']}",
                                               "telegram")
        enqueued = await db.enqueue_delivery(
            pid, delivery_id, player_id=ru.get("player_id"),
            retention_user_id=rid, channel="telegram",
            priority=int(evt.get("priority") or 3),
            payload=payload, body=draft.text,
            cta_url=draft.link_url)
        if enqueued is None:
            # Same event, same touch, already queued or sent — the send-side
            # half of at-least-once safety.
            return False, gen_cost, "already_queued", facts
        return False, gen_cost, "queued", facts

    delivered, detail, facts2 = await deliver_payload(product, ru, payload, cfg)
    facts.update(facts2)
    return delivered, gen_cost, detail, facts


async def deliver_payload(product: dict[str, Any], ru: dict[str, Any],
                          payload: dict[str, Any], cfg: dict[str, Any]
                          ) -> tuple[bool, Optional[str], dict[str, Any]]:
    """Actually put a generated touch on the wire, with all its bookkeeping.

    The ONE place a proactive agent touch leaves the service — called inline by
    `_send_touch` when the send worker is off, and by the send worker when it
    is on, so the persistence, the anti-annoyance counters and the undelivered
    accounting cannot drift between the two paths.

    Returns (delivered, detail, facts); `facts` is what the touch actually
    carried (media, CTA link), which is what the attribution row is opened
    with.
    """
    pid = int(product["id"])
    rid = int(payload.get("retention_user_id") or ru["id"])
    session_id = payload.get("session_id")
    facts: dict[str, Any] = {"session_id": session_id}
    comfort = bool(payload.get("comfort"))
    action = str(payload.get("action") or "message")
    gen_cost = float(payload.get("gen_cost") or 0)

    token = await db.get_product_telegram_token(pid)
    if not token:
        return False, "no_bot_token", facts
    draft = chat_service.PingDraft(
        text=payload.get("text") or "",
        lang=payload.get("lang") or "en",
        photo_id=payload.get("photo_id"),
        ai_meta=payload.get("ai_meta") or {},
        link_url=payload.get("link_url"),
        link_label=payload.get("link_label"))

    # Proactive touches may be delivered silently (no sound on the player's
    # phone) — the hot per-product `retention.silent_notifications` knob. The
    # send mechanics live in the delivery seam (delivery.py) — the one place
    # a proactive message leaves the service, shared with the idle ladder.
    channel = delivery.channel_for_product(
        product, token, silent=bool(cfg.get("silent_notifications")))
    # A comfort touch never carries a photo or a play-CTA button.
    delivered, detail, link_attached = await delivery.deliver_draft(
        channel, ru, draft, header=payload.get("header") or None,
        session_id=session_id,
        photo_fallback_caption=payload.get("fallback_caption") or "",
        allow_photo=not comfort, allow_link=not comfort)
    if not delivered:
        log.warning("retention_v2_send_failed product=%s player=%s detail=%s",
                    pid, ru.get("player_id"), detail)

    if delivered:
        await db.persist_ping_turn(session_id, draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid,
                                   ping_context=payload.get("ping_context"),
                                   link_url=draft.link_url if link_attached else None)
        # Shared anti-annoyance state: the same ledger/counters the idle
        # ladder uses, so caps and min-gap hold across regimes.
        sent_detail = f"v2:{payload.get('event_name')}"
        if detail == "photo_fallback_text":
            sent_detail += " (photo fallback: text)"
        await db.record_retention_ping(
            pid, rid, None, action, "sent",
            detail=sent_detail, cost_usd=gen_cost)
        # A photo that fell back to text was NOT delivered as media, so it must
        # not be attributed as one (it would count as a failed photo).
        if draft.photo_id is not None and detail != "photo_fallback_text":
            facts["photo_id"] = draft.photo_id
            facts["media_type"] = payload.get("media_type")
        if link_attached:
            facts["link_url"] = draft.link_url
        return True, None, facts

    # Generated but undelivered: the cost still lands in ai_interaction_logs.
    await delivery.account_undelivered_generation(
        session_id, draft, detail, product_id=pid,
        label="v2_touch_undelivered")
    await db.record_retention_ping(pid, rid, None, action, "failed",
                                   detail=f"v2:{detail}", cost_usd=gen_cost)
    return False, detail, facts
