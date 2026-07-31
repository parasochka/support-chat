"""Channel abstraction — router + adapters + delivery tracking (DOC-7).

Channel classes:
  - OWN transport: `telegram` (the existing delivery.py seam, bit-for-bit)
    and `email` (Customer.io App API transactional send; the App API key is
    an encrypted product secret, region/from ride in the channel config).
  - DELEGATED delivery: `push` / `in_app` — the casino delivers on-device
    with its own infrastructure; we POST a delivery ORDER to the product's
    `delivery_endpoint_url` (partner_out) and accept a status callback.
  - HUMAN route: `vip_host` — not a send at all; a task lands in the host
    queue and the persona never writes to the player on this route.

Hard rules:
  - STRICT OPT-IN: the router never selects a channel the player has not
    consented to — not on fallback, not for a critical touch. No consented
    channel at all => `undeliverable` (logged for marketing-ops review).
  - The router is deterministic code; the model never picks a channel.
  - Every send goes through the same guard chain regardless of channel.
  - `multichannel_enabled` OFF (the default): the router always answers
    `telegram` and no other adapter is active — behaviour exactly as before.
  - Delivery lifecycle rides `retention_deliveries` (idempotent by
    delivery_id); transient failures retry with backoff [1m, 5m, 30m],
    permanent failures (bounce, no token) never retry.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import time
from typing import Any, Optional

from app.core import config
from app.core import db
from app.retention import partner_out

log = logging.getLogger(__name__)

CHANNELS = ("telegram", "email", "push", "in_app", "vip_host")

_RETRY_STEPS = (_dt.timedelta(minutes=1), _dt.timedelta(minutes=5),
                _dt.timedelta(minutes=30))

# In-process TTL cache of per-product channel config rows.
_cfg_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_CFG_TTL = 60.0


def reset_caches() -> None:
    _cfg_cache.clear()


def multichannel_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("multichannel_enabled")
    return (config.RETENTION_MULTICHANNEL_ENABLED if v is None else bool(v))


async def _channel_rows(product_id: int) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _cfg_cache.get(product_id)
    if cached and now - cached[0] < _CFG_TTL:
        return cached[1]
    try:
        rows = await db.list_channel_config(product_id)
    except Exception:  # noqa: BLE001
        rows = []
    _cfg_cache[product_id] = (now, rows)
    return rows


async def executable_channels(product_id: int, cfg: dict[str, Any]
                              ) -> set[str]:
    """Channels a journey step may execute on for this product right now."""
    if not multichannel_enabled(cfg):
        return {"telegram"}
    enabled = {str(r["channel"]) for r in await _channel_rows(product_id)
               if r.get("enabled")}
    enabled.add("telegram")  # the native channel is always executable
    enabled.add("vip_host")  # a route, not a send
    return enabled


def opted_in(ru: dict[str, Any], channel: str) -> bool:
    """STRICT opt-in per channel. Telegram consent = the subscription the bot
    already tracks; vip_host is a human route (always allowed)."""
    if channel == "telegram":
        return bool(ru.get("subscribed")) and not ru.get("pings_muted") \
            and not ru.get("unreachable")
    if channel == "email":
        return ru.get("email_opt_in") is True
    if channel == "push":
        return ru.get("push_opt_in") is True
    if channel == "in_app":
        return ru.get("in_app_available") is True
    if channel == "sms":
        return ru.get("sms_opt_in") is True
    if channel == "vip_host":
        return True
    return False


def available(ru: dict[str, Any], channel: str) -> bool:
    if channel == "email":
        return bool(ru.get("email")) and ru.get("email_verified") is not False
    if channel == "push":
        return ru.get("push_available") is True
    if channel == "in_app":
        return ru.get("in_app_available") is True
    return True


async def route_channel(product_id: int, ru: dict[str, Any],
                        cfg: dict[str, Any], *,
                        wanted: str = "auto",
                        fallback: Optional[str] = None,
                        exclude: Optional[set[str]] = None) -> Optional[str]:
    """The deterministic router. Explicit channel -> it (or its fallback) if
    consented+available; 'auto' -> the priority order over consented
    channels. None = undeliverable (NEVER a non-consented channel)."""
    if not multichannel_enabled(cfg):
        ch = "telegram"
        return ch if opted_in(ru, ch) else None
    executable = await executable_channels(product_id, cfg)
    exclude = exclude or set()

    def _usable(ch: str) -> bool:
        return (ch in executable and ch not in exclude
                and opted_in(ru, ch) and available(ru, ch))

    if wanted != "auto":
        if _usable(wanted):
            return wanted
        if fallback and _usable(fallback):
            return fallback
        return None
    order = [c.strip() for c in str(
        cfg.get("channel_auto_priority")
        or config.RETENTION_CHANNEL_AUTO_PRIORITY).split(",") if c.strip()]
    for ch in order + ["telegram"]:
        if _usable(ch):
            return ch
    return None


def delivery_id_for(product_id: int, ref: Any, channel: str) -> str:
    h = hashlib.sha1(f"{product_id}:{ref}:{channel}".encode()).hexdigest()
    return f"dl_{h[:12]}"


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
async def _send_email_customerio(product: dict[str, Any],
                                 ru: dict[str, Any], *, subject: str,
                                 body: str) -> tuple[bool, Optional[str],
                                                     bool, Optional[str]]:
    """Customer.io App API transactional send.
    Returns (sent, fail_reason, permanent, provider_ref)."""
    import httpx
    pid = int(product["id"])
    key = await db.get_product_email_api_key(pid)
    if not key:
        return False, "no_email_api_key", True, None
    email = str(ru.get("email") or "").strip()
    if not email:
        return False, "no_email_address", True, None
    row = next((r for r in await _channel_rows(pid)
                if str(r["channel"]) == "email"), None)
    ch_cfg = (row or {}).get("config") or {}
    host = ("api-eu.customer.io"
            if str(ch_cfg.get("region") or "us").lower() == "eu"
            else "api.customer.io")
    payload: dict[str, Any] = {
        "to": email,
        "identifiers": {"id": str(ru.get("player_id") or email)},
        "subject": subject,
        "body": body,
    }
    if ch_cfg.get("from"):
        payload["from"] = str(ch_cfg["from"])
    if ch_cfg.get("transactional_message_id"):
        payload["transactional_message_id"] = ch_cfg[
            "transactional_message_id"]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://{host}/v1/send/email", json=payload,
                headers={"Authorization": f"Bearer {key}"})
    except Exception as exc:  # noqa: BLE001 - transient
        return False, f"email_send_failed: {exc.__class__.__name__}", False, \
            None
    if resp.status_code >= 400:
        permanent = resp.status_code not in (429, 500, 502, 503, 504)
        return False, f"customerio HTTP {resp.status_code}", permanent, None
    try:
        ref = str((resp.json() or {}).get("delivery_id") or "")
    except ValueError:
        ref = ""
    return True, None, False, ref or None


async def _send_delegated(product: dict[str, Any], ru: dict[str, Any], *,
                          channel: str, delivery_id: str, title: str,
                          body: str, cta_url: Optional[str],
                          cfg: dict[str, Any]
                          ) -> tuple[bool, Optional[str], bool,
                                     Optional[str]]:
    """push / in_app: the delivery ORDER to the casino's endpoint."""
    timeout = int(cfg.get("push_delivery_timeout_sec")
                  or config.RETENTION_PUSH_DELIVERY_TIMEOUT_SEC)
    payload = {
        "delivery_id": delivery_id,
        "player_id": str(ru.get("player_id") or ""),
        "channel": channel,
        "title": title,
        "body": body,
        "cta_url": cta_url,
        "ttl_sec": 86400,
    }
    try:
        resp = await partner_out.post_json(
            product, str(product.get("delivery_endpoint_url") or ""),
            payload, timeout_sec=timeout)
    except partner_out.PartnerCallError as exc:
        return False, str(exc), exc.permanent, None
    status = str(resp.get("status") or "failed")
    if status in ("sent", "delivered", "queued"):
        return True, None, False, resp.get("provider_ref")
    return False, str(resp.get("reason") or status), \
        bool(resp.get("permanent")), resp.get("provider_ref")


# ---------------------------------------------------------------------------
# The journey-touch sender (shared by journey steps; telegram path mirrors
# the idle ping mechanics)
# ---------------------------------------------------------------------------
async def send_journey_touch(product: dict[str, Any], ru: dict[str, Any],
                             cfg: dict[str, Any], *, channel: str,
                             intent: str, journey_key: str, step_id: int,
                             priority: int = 3
                             ) -> tuple[bool, Optional[str], Optional[int]]:
    """Generate + deliver one journey-step touch on a channel.
    Returns (sent, detail, decision_ledger_id)."""
    from app.chat import chat_service
    from app.retention import delivery as delivery_seam
    from app.retention import outcomes
    from app.retention import retention
    from app.retention import retention_v2

    pid = int(product["id"])
    rid = int(ru["id"])
    event_name = f"journey:{journey_key}#{step_id}"

    async def _ledger(action: str, delivered: bool, detail: Optional[str],
                      cost: float) -> int:
        return await db.insert_retention_v2_decision(
            pid, retention_user_id=rid, player_id=ru.get("player_id"),
            trigger_kind="journey", event_pk=None, event_name=event_name,
            state={"journey": journey_key, "step": step_id,
                   "channel": channel},
            guard={"allow": True, "reasons": []},
            action=action, intent=intent,
            reason=f"journey '{journey_key}' step {step_id}",
            dry_run=False, delivered=delivered, detail=detail, cost_usd=cost)

    if channel == "vip_host":
        await db.create_host_task(pid, str(ru.get("player_id") or ""),
                                  reason=f"journey:{journey_key}",
                                  context={"step": step_id})
        decision_id = await _ledger("routed_host", False, "vip_host", 0.0)
        return True, "routed_host", decision_id

    lang = retention.resolve_user_lang(ru)
    session = await retention._ensure_session(pid, ru, lang)
    if session is None:
        return False, "no_session", None
    session["user_context"] = retention._user_context_from_ru(ru)
    draft = await chat_service.generate_retention_ping(
        session, idle_days=0, reason="", intent=intent,
        photo_candidates=[],
        touch_history=await retention_v2._touch_history(
            pid, ru.get("player_id") or ""))
    if draft is None:
        return False, "model_error", None
    cost = float(draft.ai_meta.get("cost_usd") or 0)

    if channel == "telegram":
        token = await db.get_product_telegram_token(pid)
        tg = delivery_seam.channel_for_product(
            product, token, silent=bool(cfg.get("silent_notifications")))
        if tg is None:
            return False, "no_bot_token", None
        header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
        delivered, detail, link_attached = await delivery_seam.deliver_draft(
            tg, ru, draft, header=header or None, session_id=session["id"],
            photo_fallback_caption="")
        if delivered:
            await db.persist_ping_turn(
                session["id"], draft.text or "[photo]", ai_meta=draft.ai_meta,
                product_id=pid,
                ping_context=f"journey: {journey_key} step {step_id}",
                link_url=draft.link_url if link_attached else None)
            await db.record_retention_ping(pid, rid, None, "message", "sent",
                                           detail=event_name, cost_usd=cost)
            decision_id = await _ledger("message", True, None, cost)
            await outcomes.record(
                pid, ru, kind="proactive", session_id=session["id"],
                decision_id=decision_id, event_name=event_name,
                action="message",
                link_url=draft.link_url if link_attached else None,
                cost_usd=cost)
            return True, None, decision_id
        await delivery_seam.account_undelivered_generation(
            session["id"], draft, detail, product_id=pid,
            label="journey_undelivered")
        await db.record_retention_ping(pid, rid, None, "message", "failed",
                                       detail=detail, cost_usd=cost)
        return False, detail, None

    # Non-telegram: the delivery ledger owns the lifecycle.
    delivery_id = delivery_id_for(pid, f"j:{journey_key}:{step_id}:{rid}",
                                  channel)
    existing = await db.get_delivery(pid, delivery_id)
    if existing and existing.get("status") in ("sent", "delivered", "opened",
                                               "clicked"):
        return True, "already_delivered", None
    title = str((product.get("name") or "")).strip()
    body = draft.text or ""
    await db.upsert_delivery(
        pid, delivery_id, player_id=str(ru.get("player_id") or ""),
        retention_user_id=rid, channel=channel, intended_channel=channel,
        status="sending", title=title, body=body, cta_url=draft.link_url)
    if channel == "email":
        sent, fail, permanent, ref = await _send_email_customerio(
            product, ru, subject=title or "A note from us", body=body)
    else:
        sent, fail, permanent, ref = await _send_delegated(
            product, ru, channel=channel, delivery_id=delivery_id,
            title=title, body=body, cta_url=draft.link_url, cfg=cfg)
    if sent:
        await db.update_delivery(pid, delivery_id, status="sent",
                                 provider_ref=ref)
        await db.persist_ping_turn(
            session["id"], body or "[touch]", ai_meta=draft.ai_meta,
            product_id=pid,
            ping_context=f"journey: {journey_key} step {step_id} "
                         f"(channel {channel})")
        decision_id = await _ledger("message", True, f"channel:{channel}",
                                    cost)
        await outcomes.record(
            pid, ru, kind="proactive", session_id=session["id"],
            decision_id=decision_id, event_name=event_name, action="message",
            link_url=draft.link_url, cost_usd=cost)
        return True, None, decision_id
    next_attempt = (None if permanent
                    else _dt.datetime.now(_dt.timezone.utc) + _RETRY_STEPS[0])
    await db.update_delivery(pid, delivery_id, status="failed",
                             fail_reason=fail, permanent_fail=permanent,
                             next_attempt_at=next_attempt)
    from app.retention import delivery as delivery_seam2  # noqa: F401
    await delivery_seam.account_undelivered_generation(
        session["id"], draft, fail, product_id=pid,
        label=f"{channel}_undelivered")
    return False, fail, None


async def drain_delivery_retries(product: dict[str, Any],
                                 cfg: dict[str, Any], *,
                                 limit: int = 20) -> int:
    """Retry transiently-failed non-telegram deliveries with backoff."""
    v = cfg.get("delivery_retry_enabled")
    if not (config.RETENTION_DELIVERY_RETRY_ENABLED if v is None else v):
        return 0
    if not multichannel_enabled(cfg):
        return 0
    pid = int(product["id"])
    retried = 0
    for d in await db.due_delivery_retries(pid, limit=limit):
        attempts = int(d.get("attempts") or 0)
        if attempts >= len(_RETRY_STEPS):
            await db.update_delivery(pid, d["delivery_id"], status="failed",
                                     next_attempt_at=None)
            continue
        ru = None
        if d.get("retention_user_id"):
            ru = await db.get_retention_user_by_id(
                pid, int(d["retention_user_id"]))
        if ru is None:
            await db.update_delivery(pid, d["delivery_id"], status="failed",
                                     permanent_fail=True,
                                     next_attempt_at=None)
            continue
        channel = str(d["channel"])
        if channel == "email":
            sent, fail, permanent, ref = await _send_email_customerio(
                product, ru, subject=d.get("title") or "",
                body=d.get("body") or "")
        else:
            sent, fail, permanent, ref = await _send_delegated(
                product, ru, channel=channel,
                delivery_id=str(d["delivery_id"]),
                title=d.get("title") or "", body=d.get("body") or "",
                cta_url=d.get("cta_url"), cfg=cfg)
        retried += 1
        if sent:
            await db.update_delivery(pid, d["delivery_id"], status="sent",
                                     provider_ref=ref, next_attempt_at=None,
                                     attempts=attempts + 1)
        else:
            nxt = (None if permanent or attempts + 1 >= len(_RETRY_STEPS)
                   else _dt.datetime.now(_dt.timezone.utc)
                   + _RETRY_STEPS[min(attempts + 1, len(_RETRY_STEPS) - 1)])
            await db.update_delivery(
                pid, d["delivery_id"],
                status="failed", fail_reason=fail,
                permanent_fail=permanent, next_attempt_at=nxt,
                attempts=attempts + 1)
    return retried


_STATUS_ORDER = ("queued", "sending", "sent", "delivered", "opened",
                 "clicked")


async def apply_delivery_status(product_id: int, delivery_id: str,
                                status: str) -> bool:
    """The partner/provider status callback (sent -> delivered -> opened ->
    clicked, or bounced). Never moves a delivery backwards."""
    if status == "bounced":
        await db.update_delivery(product_id, delivery_id, status="bounced",
                                 permanent_fail=True, next_attempt_at=None)
        return True
    if status not in _STATUS_ORDER:
        return False
    existing = await db.get_delivery(product_id, delivery_id)
    if existing is None:
        return False
    cur = str(existing.get("status") or "queued")
    if (cur in _STATUS_ORDER
            and _STATUS_ORDER.index(status) <= _STATUS_ORDER.index(cur)):
        return True  # idempotent / out-of-order callback
    await db.update_delivery(product_id, delivery_id, status=status)
    return True
