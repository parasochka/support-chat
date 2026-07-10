"""Retention v2 — the agentic, event-driven proactive loop.

The parallel regime next to the v1 ping matrix (retention_pings.py): canonical
casino events (player_sync.py -> retention_events) wake an AGENT that decides
whether Nika reacts — congratulate a deposit, sympathize after a rough losing
day, celebrate a level-up, or (very often, correctly) stay silent. The split of
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

Per-product switch: `retention.v2_enabled` (hot). Exactly one proactive regime
runs per product — the v1 sweep skips v2 products and vice versa. `v2_dry_run`
(ships ON) makes the loop decide and log WITHOUT sending, so an owner reviews
real decisions before giving the agent a voice. Anti-annoyance state
(last_ping_at, daily counters) is SHARED with v1 via db.record_retention_ping,
so switching regimes never resets the player's protection.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import html
import json
import logging
import re
from typing import Any, Optional

import chat_service
import config
import db
import openai_client
import prompts
import retention
import settings
import telegram_format
import tenancy
from retention_pings import _in_quiet_hours
from telegram_transport import TelegramClient, inline_keyboard

log = logging.getLogger(__name__)

# Arbitrary but stable: the advisory-lock key for the v2 sweep (distinct from
# the v1 ping sweep's, so the two regimes never serialize each other).
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
}

# One reaction per event type per window — a partner retrying webhooks or a
# player making five deposits in an evening gets ONE warm note, not five.
_SAME_EVENT_COOLDOWN_HOURS = 20

_TONES = ("warm", "celebrate", "comfort", "neutral")

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Scheduler loop (started from main.py lifespan, next to the v1 loop)
# ---------------------------------------------------------------------------
async def scheduler_loop() -> None:
    """Wake up every RETENTION_PING_INTERVAL_SEC and drain the event queues."""
    interval = max(int(config.RETENTION_PING_INTERVAL_SEC), 30)
    log.info("retention_v2_scheduler_started interval_sec=%s", interval)
    while True:
        await asyncio.sleep(interval)
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
    """Drain one product's unprocessed events through the decision pipeline."""
    pid = int(product["id"])
    tenancy.set_current_product(pid)
    cfg = settings.retention()
    if not cfg.get("v2_enabled"):
        return {"skipped": "v2_disabled"}
    batch = int(limit or cfg["ping_batch_size"])
    events = await db.unprocessed_retention_events(pid, limit=batch)
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
        finally:
            await db.mark_retention_event_processed(evt["id"])
    return {"events": len(events), "decided": decided, "sent": sent}


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
    # Shared anti-annoyance state (same fields the v1 matrix maintains).
    gap_h = int(cfg["ping_min_gap_hours"])
    last_ping_days = _days_since(ru.get("last_ping_at"))
    if last_ping_days is not None and last_ping_days * 24 < gap_h:
        reasons.append("min_gap_not_elapsed")
    pings_day = ru.get("pings_day")
    today = _dt.date.today().isoformat()
    if (pings_day is not None and str(pings_day)[:10] == today
            and int(ru.get("pings_sent_today") or 0) >= int(cfg["ping_daily_cap"])):
        reasons.append("daily_cap_reached")
    if _in_quiet_hours(cfg):
        reasons.append("quiet_hours")
    budget = float(cfg.get("v2_daily_budget_usd") or 0)
    if budget > 0:
        spent = await db.retention_v2_cost_today(product_id)
        if spent >= budget:
            reasons.append("daily_budget_reached")
    player_id = ru.get("player_id") or ""
    if await db.recent_v2_decision_exists(
            product_id, player_id, hours=_SAME_EVENT_COOLDOWN_HOURS,
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
    ru = await db.get_retention_user_by_player(pid, player_id)

    # Cheap pre-filters that need no model: unknown player / log-only event.
    if ru is None:
        if evt.get("event_name") in DECISION_EVENTS:
            await db.insert_retention_v2_decision(
                pid, retention_user_id=None, player_id=player_id,
                trigger_kind="event", event_pk=evt["id"],
                event_name=evt.get("event_name"), state={}, guard={},
                action="skipped", reason="player not linked to the Telegram bot",
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
    return "sent" if delivered else action


# ---------------------------------------------------------------------------
# Sending (the persona writes; mechanics mirror the v1 ping send)
# ---------------------------------------------------------------------------
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

    occasion = _OCCASIONS.get(evt.get("event_name") or "", "a notable moment")
    draft = await chat_service.generate_retention_ping(
        session, idle_days=0, reason="", intent=decision["intent"],
        photo_candidates=candidates, occasion=occasion, comfort=comfort)
    if draft is None:
        await db.record_retention_ping(pid, rid, None, decision["action"],
                                       "failed", detail="v2:model_error")
        return False, 0.0, "model_error"
    gen_cost = float(draft.ai_meta.get("cost_usd") or 0)

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
        delivered = await retention._send_photo(
            client, product, ru, chat_id, draft.photo_id, caption,
            session_id=session["id"], reply_markup=markup)
        if not delivered:
            detail = "photo_send_failed"
    else:
        header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
        text_html = telegram_format.to_html(draft.text)
        text_plain = draft.text
        if header:
            text_html = f"<i>{html.escape(header)}</i>\n\n{text_html}"
            text_plain = f"{header}\n\n{draft.text}"
        result, err_code, err_desc = await client.send_message_verbose(
            chat_id, text_html, parse_mode="HTML", reply_markup=markup)
        if result is None and text_html != text_plain and err_code != 403:
            result, err_code, err_desc = await client.send_message_verbose(
                chat_id, text_plain, reply_markup=markup)
        delivered = result is not None
        if not delivered:
            detail = f"{err_code}: {err_desc}" if err_desc else "send_failed"
            if err_code == 403:
                await db.set_retention_unreachable(rid, True)

    if delivered:
        await db.persist_ping_turn(session["id"], draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid)
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
