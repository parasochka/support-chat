"""The SEND stage — delivery as its own worker, shaped by a token bucket.

Deciding and sending used to be one loop: the agent picked an action and put
the message on the wire in the same breath. That coupling is what a broadcast
breaks. Telegram accepts about 25 messages a second per bot, so ten thousand
queued touches are seven minutes of pure send time — seven minutes during
which the decision pipeline, sharing the loop, would not react to a single
deposit. And a send that failed had nowhere to live: it was retried by nobody.

So a decision now ENQUEUES (`retention_deliveries`, one row per touch, keyed by
a deterministic `delivery_id` so a replayed decision cannot enqueue twice) and
this loop delivers:

  - claims a batch under a LEASE, best lane first (a transactional reaction
    overtakes a bulk re-engagement wave, always);
  - takes a token from the per-bot and per-chat buckets (`db.take_rate_token`,
    shared through Postgres so the limit holds however many workers run) and
    RESCHEDULES rather than sleeps when the bucket is empty — a held lease is a
    worker slot doing nothing;
  - on success opens the attribution row and marks the decision delivered;
  - on transient failure backs off [1m, 5m, 30m]; on a permanent one (the
    player blocked the bot) stops for good.

The whole stage is behind `retention.send_worker_enabled`, OFF by default: with
it off `_send_touch` sends inline exactly as before and this loop finds nothing
to do.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.core import config
from app.core import db
from app.core import settings
from app.core import tenancy
from app.retention import outcomes
from app.retention import retention_v2

log = logging.getLogger(__name__)

# Backoff ladder for a transient send failure — the same steps the delegated
# channels already use, so a retry cadence is one concept in this codebase.
_RETRY_STEPS = (60, 300, 1800)

# How long an empty token bucket parks a touch. Short: the bucket refills
# continuously, and this is the pacing loop of a burst.
_BUCKET_RETRY_SEC = 1.0


def send_worker_enabled() -> bool:
    return settings.global_retention_bool(
        "send_worker_enabled", config.RETENTION_SEND_WORKER_ENABLED)


async def send_loop(stop: Optional[asyncio.Event] = None) -> None:
    """Drain the send queues of every product on the worker cadence."""
    log.info("retention_send_loop_started")
    while True:
        interval = retention_v2.worker_interval_sec()
        if await retention_v2._sleep_or_stop(interval, stop):
            log.info("retention_send_loop_stopping")
            return
        if not send_worker_enabled():
            continue
        try:
            stats = await run_due_sends(stop=stop)
            if stats.get("sent") or stats.get("failed"):
                log.info("retention_send_sweep_done stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            log.exception("retention_send_sweep_failed")


async def run_due_sends(*, stop: Optional[asyncio.Event] = None
                        ) -> dict[str, Any]:
    """One send pass across all products, products in parallel."""
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
            return await run_product_sends(product, stop=stop)

    results = await asyncio.gather(*(_one(p) for p in products),
                                   return_exceptions=True)
    totals: dict[str, Any] = {"products": 0}
    for res in results:
        if isinstance(res, BaseException):
            log.exception("retention_send_product_failed", exc_info=res)
            continue
        totals["products"] += 1
        for k, v in (res or {}).items():
            if isinstance(v, int) and v:
                totals[k] = totals.get(k, 0) + v
    return totals


async def run_product_sends(product: dict[str, Any], *,
                            limit: Optional[int] = None,
                            stop: Optional[asyncio.Event] = None
                            ) -> dict[str, Any]:
    """Deliver one product's due touches."""
    pid = int(product["id"])
    with tenancy.scoped_product(pid):
        cfg = settings.retention()
        batch = int(limit or cfg.get("send_batch_size")
                    or config.RETENTION_SEND_BATCH_SIZE)
        lease = settings.global_retention_int(
            "send_lease_sec", config.RETENTION_SEND_LEASE_SEC, 10, 3600)
        rows = await db.claim_deliveries(
            pid, limit=batch, lease_sec=lease,
            worker_id=retention_v2.worker_id())
        if not rows:
            return {}
        owed = {int(r["id"]) for r in rows}
        counters = {"sent": 0, "failed": 0, "deferred": 0}
        width = int(cfg.get("send_concurrency")
                    or config.RETENTION_SEND_CONCURRENCY)
        sem = asyncio.Semaphore(max(width, 1))

        async def _one(row: dict[str, Any]) -> None:
            async with sem:
                if stop is not None and stop.is_set():
                    return
                try:
                    outcome = await _deliver_row(product, row, cfg)
                except Exception as exc:  # noqa: BLE001 - one bad row must not wedge the queue
                    log.exception("retention_send_row_failed product=%s id=%s",
                                  pid, row.get("id"))
                    await db.mark_delivery_failed(
                        int(row["id"]), repr(exc),
                        backoff_sec=_backoff_for(row))
                    outcome = "failed"
                owed.discard(int(row["id"]))
                if outcome in counters:
                    counters[outcome] += 1

        try:
            await asyncio.gather(*(_one(r) for r in rows),
                                 return_exceptions=True)
        finally:
            if owed:
                try:
                    await db.release_deliveries(sorted(owed))
                except Exception:  # noqa: BLE001 - the lease reclaimer is the backstop
                    log.exception("retention_send_release_failed product=%s",
                                  pid)
        return counters


def _backoff_for(row: dict[str, Any]) -> int:
    """Next retry delay from the attempt count already stamped by the claim."""
    attempt = max(int(row.get("attempts") or 1), 1)
    return _RETRY_STEPS[min(attempt - 1, len(_RETRY_STEPS) - 1)]


async def _take_tokens(pid: int, channel: str, chat_id: Optional[int],
                       cfg: dict[str, Any]) -> bool:
    """Both buckets a Telegram send must pass: per bot AND per chat.

    Per chat matters as much as per bot: Telegram throttles a single
    conversation at roughly one message a second, and a burst that respects
    only the global rate still trips it whenever two touches for one player
    land together.
    """
    if channel != "telegram":
        if channel == "email":
            return await db.take_rate_token(
                f"email:{pid}",
                rate_per_sec=float(cfg.get("email_rate_per_sec")
                                   or config.RETENTION_EMAIL_RATE_PER_SEC),
                burst=float(cfg.get("email_rate_per_sec")
                            or config.RETENTION_EMAIL_RATE_PER_SEC))
        return True  # delegated channels are paced by the partner
    rate = float(cfg.get("telegram_rate_per_sec")
                 or config.RETENTION_TELEGRAM_RATE_PER_SEC)
    burst = float(cfg.get("telegram_burst") or config.RETENTION_TELEGRAM_BURST)
    if not await db.take_rate_token(f"tg:{pid}", rate_per_sec=rate,
                                    burst=burst):
        return False
    if chat_id is None:
        return True
    chat_rate = float(cfg.get("telegram_chat_rate_per_sec")
                      or config.RETENTION_TELEGRAM_CHAT_RATE_PER_SEC)
    # The per-bot token is already spent if the per-chat bucket refuses. That
    # is the cheap direction of the trade: one token of a 25/s budget, versus
    # holding the chat bucket open while another worker takes the bot slot.
    return await db.take_rate_token(f"tg:chat:{chat_id}",
                                    rate_per_sec=chat_rate, burst=1.0)


async def _suppressed_now(pid: int, ru: dict[str, Any],
                          payload: dict[str, Any],
                          cfg: dict[str, Any]) -> Optional[str]:
    """Why this queued touch must NOT go out after all, or None.

    Deliberately only the checks whose answer can CHANGE between deciding and
    sending, and whose change makes the send wrong rather than merely
    late: consent (the player muted or blocked the bot) and responsible
    gaming (the casino flagged them). Frequency caps are not re-checked — the
    decision already consumed the budget, and re-running the whole guard here
    would just decide the same thing twice.
    """
    from app.retention import channels
    if not channels.opted_in(ru, "telegram"):
        return "opted_out_before_send"
    try:
        from app.retention import rg_guard
        verdict = await rg_guard.gate(pid, ru, str(payload.get("event_name")
                                                   or "proactive"), cfg)
    except Exception:  # noqa: BLE001 - never let the guard's own failure send
        log.exception("retention_send_rg_gate_failed product=%s", pid)
        return None
    if verdict is not None and verdict.get("deny"):
        return f"rg:{verdict.get('reason') or 'blocked'}"
    return None


async def _deliver_row(product: dict[str, Any], row: dict[str, Any],
                       cfg: dict[str, Any]) -> str:
    """Deliver one claimed row. Returns 'sent' | 'failed' | 'deferred'."""
    pid = int(product["id"])
    payload = row.get("payload") or {}
    rid = row.get("retention_user_id") or payload.get("retention_user_id")
    ru = await db.get_retention_user_by_id(pid, int(rid)) if rid else None
    if ru is None:
        await db.mark_delivery_failed(int(row["id"]), "player link missing",
                                      permanent=True)
        return "failed"

    # RE-CHECK CONSENT AT SEND TIME. The guards ran when the touch was
    # DECIDED, which is now minutes ago — long enough for the player to have
    # hit /stop, blocked the bot, or been flagged by the casino's responsible-
    # gaming feed. Sending anyway because "it was allowed when we wrote it"
    # is exactly the failure a queue introduces, and for the RG case it is a
    # compliance failure, not a nuisance.
    suppressed = await _suppressed_now(pid, ru, payload, cfg)
    if suppressed:
        await db.mark_delivery_failed(int(row["id"]), suppressed,
                                      permanent=True)
        log.info("retention_send_suppressed product=%s player=%s reason=%s",
                 pid, ru.get("player_id"), suppressed)
        return "failed"

    if not await _take_tokens(pid, str(row.get("channel") or "telegram"),
                              ru.get("tg_user_id"), cfg):
        await db.reschedule_delivery(int(row["id"]),
                                     delay_sec=_BUCKET_RETRY_SEC)
        return "deferred"

    delivered, detail, facts = await retention_v2.deliver_payload(
        product, ru, payload, cfg)
    decision_id = row.get("decision_id")
    if delivered:
        outcome_id = await outcomes.record(
            pid, ru, kind="proactive", session_id=facts.get("session_id"),
            decision_id=decision_id, event_name=payload.get("event_name"),
            action=payload.get("action"), tone=payload.get("tone"),
            photo_id=facts.get("photo_id"), media_type=facts.get("media_type"),
            link_url=facts.get("link_url"),
            cost_usd=float(payload.get("gen_cost") or 0))
        await db.mark_delivery_sent(int(row["id"]), outcome_id=outcome_id)
        if decision_id:
            await db.update_retention_v2_decision(int(decision_id),
                                                  delivered=True, detail="sent")
        return "sent"

    # A blocked bot / deleted account is final: retrying it forever would burn
    # the product's send budget on a player who cannot receive anything. The
    # delivery seam has already flagged the player unreachable.
    permanent = bool(ru.get("unreachable")) or str(detail or "").startswith("403")
    await db.mark_delivery_failed(int(row["id"]), detail or "send_failed",
                                  permanent=permanent,
                                  backoff_sec=_backoff_for(row))
    if decision_id:
        await db.update_retention_v2_decision(
            int(decision_id), detail=f"send_failed: {detail}")
    return "failed"
