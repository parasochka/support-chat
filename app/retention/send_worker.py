"""The SEND stage — delivery as its own worker, shaped by a token bucket.

Deciding and sending used to be one loop: the agent picked an action and put
the message on the wire in the same breath. That coupling is what a broadcast
breaks. Telegram accepts about 30 messages a second per bot, so ten thousand
queued touches are ~6 minutes of pure send time — six minutes during
which the decision pipeline, sharing the loop, would not react to a single
deposit. And a send that failed had nowhere to live: it was retried by nobody.

So a decision now ENQUEUES (`retention_deliveries`, one row per touch, keyed by
a deterministic `delivery_id` so a replayed decision cannot enqueue twice) and
this loop delivers:

  - claims a batch under a LEASE, best lane first (a transactional reaction
    overtakes a bulk re-engagement wave, always);
  - keeps claiming while there is work, so the CHANNEL's rate limit is what
    paces the burst rather than the worker's tick;
  - takes a token from that channel's buckets (`db.take_rate_token`, shared
    through Postgres so the limit holds however many workers run), waiting a
    beat for one rather than giving up instantly — at 30/s a token is ~33ms
    away, and rescheduling a row over that would throw away the rate;
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
import time
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

# How long a row waits for a token before being parked. The bucket refills
# continuously (30/s = a token every ~33ms), so a short wait is what turns the
# channel's rate limit into the actual send rate; giving up instantly instead
# meant a burst delivered at the tick rate, not the channel rate.
_TOKEN_WAIT_SEC = 2.0
_TOKEN_POLL_SEC = 0.05
# Parked when even that wait found nothing — the channel is genuinely saturated.
_BUCKET_RETRY_SEC = 1.0
# Pause before re-claiming when a whole batch was parked, so a dry bucket does
# not turn into a spin against the database.
_DRY_BUCKET_PAUSE_SEC = 1.0


def send_worker_enabled() -> bool:
    return settings.global_retention_bool(
        "send_worker_enabled", config.RETENTION_SEND_WORKER_ENABLED)


async def send_loop(stop: Optional[asyncio.Event] = None) -> None:
    """Drain the send queues of every product on the worker cadence."""
    log.info("retention_send_loop_started")
    busy = False
    while True:
        # A pass that moved something goes straight back in: it stopped on the
        # pass budget, not because the queue ran dry, and sleeping a full tick
        # in the middle of a burst is exactly the stall this stage exists to
        # avoid. Only an idle pass waits for the next tick.
        interval = 0 if busy else retention_v2.worker_interval_sec()
        if interval and await retention_v2._sleep_or_stop(interval, stop):
            log.info("retention_send_loop_stopping")
            return
        if stop is not None and stop.is_set():
            log.info("retention_send_loop_stopping")
            return
        if not send_worker_enabled():
            busy = False
            continue
        try:
            stats = await run_due_sends(stop=stop)
            busy = bool(stats.get("sent") or stats.get("deferred"))
            if stats.get("sent") or stats.get("failed"):
                log.info("retention_send_sweep_done stats=%s", stats)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive any sweep error
            busy = False
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
    """Deliver one product's due touches, draining until the queue is empty.

    The loop used to claim ONE batch per worker tick, which quietly made the
    send rate `send_batch_size / worker_interval_sec` — 50 rows every 30s, or
    1.7/s, while the token bucket sat there allowing 30/s. A 10k broadcast took
    an hour and a half for no reason. The bucket, not the tick, has to be what
    paces sending, so the pass keeps claiming while there is work.

    Two bounds keep that from becoming a monopoly: `send_pass_max_sec` ends the
    pass so the worker's product slot is handed back (the next tick continues
    where this one stopped — the queue is durable), and the stop flag ends it
    immediately on shutdown.
    """
    pid = int(product["id"])
    with tenancy.scoped_product(pid):
        cfg = settings.retention()
        batch = int(limit or cfg.get("send_batch_size")
                    or config.RETENTION_SEND_BATCH_SIZE)
        lease = settings.global_retention_int(
            "send_lease_sec", config.RETENTION_SEND_LEASE_SEC, 10, 3600)
        pass_budget = settings.global_retention_int(
            "send_pass_max_sec", config.RETENTION_SEND_PASS_MAX_SEC, 1, 600)
        width = int(cfg.get("send_concurrency")
                    or config.RETENTION_SEND_CONCURRENCY)
        sem = asyncio.Semaphore(max(width, 1))
        counters = {"sent": 0, "failed": 0, "deferred": 0, "batches": 0}
        deadline = time.monotonic() + pass_budget

        while True:
            if stop is not None and stop.is_set():
                break
            rows = await db.claim_deliveries(
                pid, limit=batch, lease_sec=lease,
                worker_id=retention_v2.worker_id())
            if not rows:
                break
            counters["batches"] += 1
            owed = {int(r["id"]) for r in rows}

            async def _one(row: dict[str, Any]) -> None:
                async with sem:
                    if stop is not None and stop.is_set():
                        return
                    try:
                        outcome = await _deliver_row(product, row, cfg)
                    except Exception as exc:  # noqa: BLE001 - one bad row must not wedge the queue
                        log.exception("retention_send_row_failed product=%s "
                                      "id=%s", pid, row.get("id"))
                        await db.mark_delivery_failed(
                            int(row["id"]), repr(exc),
                            backoff_sec=_backoff_for(row))
                        outcome = "failed"
                    owed.discard(int(row["id"]))
                    if outcome in counters:
                        counters[outcome] += 1

            before = counters["sent"]
            try:
                await asyncio.gather(*(_one(r) for r in rows),
                                     return_exceptions=True)
            finally:
                if owed:
                    try:
                        await db.release_deliveries(sorted(owed))
                    except Exception:  # noqa: BLE001 - the reclaimer is the backstop
                        log.exception("retention_send_release_failed "
                                      "product=%s", pid)
            if time.monotonic() >= deadline:
                break
            if counters["sent"] == before:
                # Nothing got through: the channel's bucket is dry for longer
                # than a row is willing to wait.
                if len(rows) < batch:
                    # ...and the queue is nearly empty anyway. Hand the product
                    # slot back rather than holding it for a trickle; the
                    # parked rows are durable and the next tick takes them.
                    break
                # A full batch means there IS more work, so wait for the bucket
                # instead of spinning claims against the database.
                if await retention_v2._sleep_or_stop(_DRY_BUCKET_PAUSE_SEC,
                                                     stop):
                    break
        # Nothing was even claimed: say so plainly rather than reporting a row
        # of zeros the aggregate log would have to filter out.
        return counters if counters["batches"] else {}


def _backoff_for(row: dict[str, Any]) -> int:
    """Next retry delay from the attempt count already stamped by the claim."""
    attempt = max(int(row.get("attempts") or 1), 1)
    return _RETRY_STEPS[min(attempt - 1, len(_RETRY_STEPS) - 1)]


async def _wait_for_token(scope: str, *, rate: float, burst: float) -> bool:
    """Take a token, waiting a beat for one. False = the channel is saturated.

    The wait is what makes the CHANNEL's limit the send rate. Without it a row
    that arrived a few milliseconds early was parked for a second and
    re-claimed later, so a burst delivered at the worker's tick rate instead of
    the channel's — 1.7/s against a bucket allowing 30/s.
    """
    if rate <= 0:
        return True
    deadline = time.monotonic() + _TOKEN_WAIT_SEC
    while True:
        if await db.take_rate_token(scope, rate_per_sec=rate, burst=burst):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_TOKEN_POLL_SEC)


def _channel_rate(channel: str, cfg: dict[str, Any]) -> tuple[float, float]:
    """(rate/sec, burst) for a channel's per-product bucket.

    EVERY outbound channel is shaped, not just Telegram. Email hits a provider
    with its own account-wide limit, and push/in_app are POSTs at the CASINO's
    delivery endpoint — a 10k broadcast unshaped is a denial of service against
    our own partner, who then rate-limits or drops us.
    """
    if channel == "telegram":
        rate = float(cfg.get("telegram_rate_per_sec")
                     or config.RETENTION_TELEGRAM_RATE_PER_SEC)
        return rate, float(cfg.get("telegram_burst")
                           or config.RETENTION_TELEGRAM_BURST)
    if channel == "email":
        rate = float(cfg.get("email_rate_per_sec")
                     or config.RETENTION_EMAIL_RATE_PER_SEC)
        return rate, rate
    if channel in ("push", "in_app"):
        rate = float(cfg.get("partner_rate_per_sec")
                     or config.RETENTION_PARTNER_RATE_PER_SEC)
        return rate, rate
    # vip_host is a task in a queue, never a message on a wire.
    return 0.0, 0.0


async def _take_tokens(pid: int, channel: str, chat_id: Optional[int],
                       cfg: dict[str, Any]) -> bool:
    """Every bucket this send must pass: the channel's, and per chat.

    Per chat matters as much as per bot: Telegram throttles a single
    conversation at roughly one message a second, and a burst that respects
    only the global rate still trips it whenever two touches for one player
    land together.
    """
    rate, burst = _channel_rate(channel, cfg)
    scope = (f"tg:{pid}" if channel == "telegram"
             else f"{channel}:{pid}")
    if not await _wait_for_token(scope, rate=rate, burst=burst):
        return False
    if channel != "telegram" or chat_id is None:
        return True
    chat_rate = float(cfg.get("telegram_chat_rate_per_sec")
                      or config.RETENTION_TELEGRAM_CHAT_RATE_PER_SEC)
    # The per-bot token is already spent if the per-chat bucket refuses. That
    # is the cheap direction of the trade: one token of a 30/s budget, versus
    # holding the chat bucket open while another worker takes the bot slot.
    # No WAIT here — a per-chat refusal means this player just got a message,
    # and the right answer is to park his next one, not to block a send slot.
    return await db.take_rate_token(f"tg:chat:{chat_id}",
                                    rate_per_sec=chat_rate, burst=1.0)


async def _account_unsent(pid: int, payload: dict[str, Any],
                          detail: str) -> None:
    """Log a generation that was paid for but never left (invariant §4).

    Best-effort by contract: the touch is already suppressed, and failing to
    write its accounting must not turn that into an exception the send loop
    has to handle.
    """
    meta = payload.get("ai_meta") or {}
    if not meta:
        return
    try:
        await db.log_ai_interaction(
            payload.get("session_id"), meta.get("model"), meta.get("key_used"),
            meta.get("tokens_in"), meta.get("tokens_out"),
            meta.get("cached_in"), float(meta.get("cost_usd") or 0),
            meta.get("latency_ms"), False, f"v2_touch_suppressed {detail}",
            product_id=pid, consumer="telegram", source="agent")
    except Exception:  # noqa: BLE001 - accounting must not break the loop
        log.exception("retention_send_accounting_failed product=%s", pid)


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
    decision_id = row.get("decision_id")
    suppressed = await _suppressed_now(pid, ru, payload, cfg)
    if suppressed:
        await db.mark_delivery_failed(int(row["id"]), suppressed,
                                      permanent=True)
        # The message was already GENERATED and billed when the decision was
        # made — the model call happened, so invariant §4 says it must land in
        # ai_interaction_logs whether or not the text ever reached the player.
        # Every other terminal branch writes it through deliver_payload; this
        # one returns before that, so it accounts for itself.
        await _account_unsent(pid, payload, suppressed)
        if decision_id:
            await db.update_retention_v2_decision(
                int(decision_id), delivered=False,
                detail=f"suppressed: {suppressed}")
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
