"""Idle re-engagement — the agent's INACTIVITY trigger (the admin-managed
"player quiet N days -> Nika writes first" ladder in `retention_rules`).

The event-driven agent (retention_v2) reacts to things that HAPPEN; a player
who simply went quiet produces no events, so without this sweep they are never
written to again. The rules ladder (7 / 14 / 30 days …) is edited in the admin
Retention · Telegram -> Idle pings tab; the sweep runs from the SAME worker
loop as the event pipeline, under the same advisory lock, and is bounded by
the SAME per-player guards and ledgers:

  - `db.eligible_ping_users` prefilters (subscribed, not /stop-muted, not
    unreachable, past `ping_min_gap_hours`, under `ping_daily_cap`);
  - quiet hours, the per-product daily AI budget and `v2_dry_run` are honoured
    exactly like an event touch (dry-run logs the decision, sends nothing);
  - every fired rule lands in the `retention_v2_decisions` ledger
    (trigger_kind='idle') AND the `retention_pings` ledger (rule_id feeds the
    per-rule `cooldown_days`), so "why did/didn't the bot write?" stays
    answerable from one place.

The message itself comes from the normal persona ping stack
(chat_service.generate_retention_ping, the idle wording of
prompts._RETENTION_PING_TASK), delivered with the localized `rtn_ping_header`
chrome line — same voice, same language stickiness, same photo machinery.
"""
from __future__ import annotations

import datetime as _dt
import html
import logging
import time
from typing import Any, Optional

import chat_service
import db
import retention
import telegram_format
from telegram_transport import TelegramClient, inline_keyboard

log = logging.getLogger(__name__)

# The idle ladder moves on a scale of DAYS, so sweeping it on the worker's
# seconds-scale cadence is pure waste. In-process pacing: one idle sweep per
# product at most every `retention.idle_sweep_interval_sec` (hot per-product
# knob; the event pipeline still runs every tick). 600s is the fallback when
# the knob is missing from an older stored override.
_IDLE_SWEEP_INTERVAL_SEC = 600
_last_sweep: dict[int, float] = {}

_TRIGGER_REASONS = {
    "bot_inactivity": "they have not written to you here",
    "casino_inactivity": "they have not been playing on the site",
    "no_deposit": "they have not made a deposit in a while",
}


def _days_since(value: Any, now: _dt.datetime) -> Optional[float]:
    dt = db._as_ts(value)  # one shared "maybe ISO string" parser (Z-suffix safe)
    if dt is None:
        return None
    return max((now - dt).total_seconds() / 86400.0, 0.0)


def _idle_days_for(ru: dict[str, Any], trigger_kind: str,
                   now: _dt.datetime) -> Optional[float]:
    """Days of idleness for this trigger, or None when the signal is absent
    (a rule on casino data never fires for a player the casino hasn't fed)."""
    if trigger_kind == "bot_inactivity":
        return _days_since(ru.get("last_active_at"), now)
    if trigger_kind == "casino_inactivity":
        candidates = [d for d in (_days_since(ru.get("last_login_at"), now),
                                  _days_since(ru.get("last_played_at"), now))
                      if d is not None]
        return min(candidates) if candidates else None
    if trigger_kind == "no_deposit":
        return _days_since(ru.get("last_deposit_at"), now)
    return None


async def _match_rule(ru: dict[str, Any], rules: list[dict[str, Any]]
                      ) -> Optional[tuple[dict[str, Any], int]]:
    """The highest-priority rule that fires for this player right now."""
    now = _dt.datetime.now(_dt.timezone.utc)
    vip = (ru.get("vip_level") or "").strip().lower()
    for rule in rules:  # already ordered priority DESC
        idle = _idle_days_for(ru, rule.get("trigger_kind", ""), now)
        if idle is None or idle < int(rule.get("inactivity_days") or 0):
            continue
        tiers = [str(t).strip().lower() for t in (rule.get("vip_tiers") or [])]
        if tiers and vip not in tiers:
            continue
        if await db.ping_rule_recently_fired(
                int(ru["id"]), int(rule["id"]),
                int(rule.get("cooldown_days") or 0)):
            continue
        return rule, int(idle)
    return None


async def run_product_idle_pings(product: dict[str, Any],
                                 cfg: dict[str, Any], *,
                                 force: bool = False,
                                 limit: Optional[int] = None
                                 ) -> dict[str, Any]:
    """Evaluate this product's idle rules and write to the matched players.

    Called from the worker sweep (same advisory lock as the event pipeline);
    `force` (the admin "run now" path) skips the in-process pacing and the
    quiet-hours skip so a test run answers immediately.
    """
    pid = int(product["id"])
    if not cfg.get("idle_pings_enabled"):
        return {"skipped": "idle_pings_disabled"}
    now = time.monotonic()
    interval = int(cfg.get("idle_sweep_interval_sec")
                   or _IDLE_SWEEP_INTERVAL_SEC)
    if not force and now - _last_sweep.get(pid, 0.0) < interval:
        return {"skipped": "paced"}
    _last_sweep[pid] = now
    token = await db.get_product_telegram_token(pid)
    if not token:
        return {"skipped": "no_bot_token"}
    import retention_v2  # late: retention_v2 imports this module
    if not force and retention_v2._in_quiet_hours(cfg):
        return {"skipped": "quiet_hours"}
    rules = await db.list_retention_rules(pid, only_enabled=True)
    if not rules:
        return {"skipped": "no_rules"}
    budget = float(cfg.get("v2_daily_budget_usd") or 0)
    if budget > 0 and await db.retention_v2_cost_today(pid) >= budget:
        return {"skipped": "daily_budget_reached"}
    batch = int(limit or cfg["ping_batch_size"])
    # Over-fetch: the most-idle players often sit inside a per-rule cooldown
    # right after a successful sweep; with an exact-LIMIT fetch they would
    # occupy the whole batch and starve everyone behind them.
    users = await db.eligible_ping_users(
        pid,
        min_gap_hours=int(cfg["ping_min_gap_hours"]),
        daily_cap=int(cfg["ping_daily_cap"]),
        limit=batch * 3,
    )
    if not users:
        return {"sent": 0, "failed": 0, "considered": 0}

    client = TelegramClient(token)
    sent = failed = 0
    for ru in users:
        if sent + failed >= batch:
            break
        matched = await _match_rule(ru, rules)
        if matched is None:
            continue
        rule, idle_days = matched
        ok = await _send_idle_ping(client, product, ru, rule, idle_days, cfg)
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "considered": len(users)}


async def _send_idle_ping(client: TelegramClient, product: dict[str, Any],
                          ru: dict[str, Any], rule: dict[str, Any],
                          idle_days: int, cfg: dict[str, Any]) -> bool:
    """One idle touch: generate -> (dry-run?) -> send -> persist/ledgers."""
    pid = int(product["id"])
    rid = int(ru["id"])
    rule_id = int(rule["id"])
    action = rule.get("action") or "message"
    dry_run = bool(cfg.get("v2_dry_run"))
    lang = retention.resolve_user_lang(ru)
    chat_id = int(ru["tg_user_id"])  # a private chat's id IS the user id

    session = await retention._ensure_session(pid, ru, lang)
    if session is None:
        await db.record_retention_ping(pid, rid, rule_id, action, "skipped",
                                       detail="no_session")
        return False
    session["user_context"] = retention._user_context_from_ru(ru)

    candidates: list[dict[str, Any]] = []
    if action == "photo" and not dry_run:
        candidates = await retention.select_photo_candidates(
            pid, ru, "", bypass_cooldown=True)
        # No sendable photo (daily photo cap, tier gate, nothing unseen) —
        # gracefully fall back to a text-only ping rather than skipping.

    reason = _TRIGGER_REASONS.get(rule.get("trigger_kind", ""), "inactivity")
    if dry_run:
        # Same shadow mode as the event agent: the ledger shows exactly what
        # WOULD have gone out, nothing is generated or sent (a dry idle rule
        # must not burn model calls on every sweep).
        await db.insert_retention_v2_decision(
            pid, retention_user_id=rid, player_id=ru.get("player_id"),
            trigger_kind="idle", event_pk=None,
            event_name=f"idle:{rule.get('trigger_kind')}",
            state={"idle_days": idle_days},
            guard={"allow": True, "reasons": []},
            action=action, intent=rule.get("intent") or "",
            reason=f"idle rule '{rule.get('name')}' matched ({idle_days}d)",
            dry_run=True)
        await db.record_retention_ping(pid, rid, rule_id, action, "skipped",
                                       detail="dry_run")
        return True

    draft = await chat_service.generate_retention_ping(
        session,
        idle_days=idle_days,
        reason=reason,
        intent=rule.get("intent") or "",
        photo_candidates=candidates,
    )
    if draft is None:
        await db.record_retention_ping(pid, rid, rule_id, action, "failed",
                                       detail="model_error")
        return False

    markup = None
    if draft.link_url:
        markup = inline_keyboard([[{"text": draft.link_label or draft.link_url,
                                    "url": draft.link_url}]])

    silent = bool(cfg.get("silent_notifications"))
    delivered = False
    detail: Optional[str] = None
    if draft.photo_id is not None:
        caption = draft.text or retention.fallback_photo_caption(draft.lang)
        header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
        if header:
            caption = f"{header}\n\n{caption}"
        photo_status = await retention._send_photo(
            client, product, ru, chat_id, draft.photo_id, caption,
            session_id=session["id"], reply_markup=markup, silent=silent)
        delivered = photo_status is not None
        if not delivered:
            detail = "photo_send_failed"
    else:
        header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
        ping_html = telegram_format.to_html(draft.text)
        ping_plain = draft.text
        if header:
            ping_html = f"<i>{html.escape(header)}</i>\n\n{ping_html}"
            ping_plain = f"{header}\n\n{draft.text}"
        result, err_code, err_desc = await client.send_message_verbose(
            chat_id, ping_html, parse_mode="HTML", reply_markup=markup,
            disable_notification=silent)
        if result is None and ping_html != ping_plain and err_code != 403:
            # Bad HTML (never a block) — retry once as plain text.
            result, err_code, err_desc = await client.send_message_verbose(
                chat_id, ping_plain, reply_markup=markup,
                disable_notification=silent)
        delivered = result is not None
        if not delivered:
            detail = f"{err_code}: {err_desc}" if err_desc else "send_failed"
            if err_code == 403:
                # The player blocked the bot — stop trying until they write.
                await db.set_retention_unreachable(rid, True)

    cost = float(draft.ai_meta.get("cost_usd") or 0)
    ping_context = (f"idle_reengagement: the player has been away about "
                    f"{idle_days} days ({reason})")
    if delivered:
        await db.persist_ping_turn(session["id"], draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid,
                                   ping_context=ping_context,
                                   link_url=draft.link_url if markup else None)
        await db.record_retention_ping(pid, rid, rule_id, action, "sent",
                                       detail=rule.get("name"), cost_usd=cost)
        await db.insert_retention_v2_decision(
            pid, retention_user_id=rid, player_id=ru.get("player_id"),
            trigger_kind="idle", event_pk=None,
            event_name=f"idle:{rule.get('trigger_kind')}",
            state={"idle_days": idle_days},
            guard={"allow": True, "reasons": []},
            action=action, intent=rule.get("intent") or "",
            reason=f"idle rule '{rule.get('name')}' matched ({idle_days}d)",
            dry_run=False, delivered=True, detail=rule.get("name"),
            cost_usd=cost)
        await db.log_admin_event(
            session["id"], "retention_ping",
            {"rule_id": rule_id, "rule": rule.get("name"), "action": action,
             "tg_user_id": ru.get("tg_user_id"), "idle_days": idle_days,
             "cost_usd": cost},
            product_id=pid)
        return True

    # The model call happened but nothing reached the player: account the cost
    # (invariant §4 — every OpenAI call gets an ai_interaction_logs row).
    meta = draft.ai_meta
    await db.log_ai_interaction(
        session["id"], meta.get("model"), meta.get("key_used"),
        meta.get("tokens_in"), meta.get("tokens_out"), meta.get("cached_in"),
        cost, meta.get("latency_ms"), False, f"ping_undelivered {detail}",
        product_id=pid)
    await db.record_retention_ping(pid, rid, rule_id, action, "failed",
                                   detail=detail, cost_usd=cost)
    log.warning("retention_idle_ping_failed product=%s player=%s detail=%s",
                pid, ru.get("player_id"), detail)
    return False


# The starter ladder a NEW product is seeded with (db.create_product) — the
# operator tunes/translates from the Idle pings tab. English intents (they are
# model-facing prompt material), brand-neutral.
STARTER_IDLE_RULES: tuple[dict[str, Any], ...] = (
    {"name": "Quiet for a week", "trigger_kind": "bot_inactivity",
     "inactivity_days": 7, "action": "message",
     "intent": "You miss him a little; ask warmly how he is doing.",
     "cooldown_days": 14, "priority": 30},
    {"name": "Away two weeks", "trigger_kind": "bot_inactivity",
     "inactivity_days": 14, "action": "photo",
     "intent": "Warmly remind him of you; a photo makes it personal.",
     "cooldown_days": 21, "priority": 20},
    {"name": "Gone a month", "trigger_kind": "bot_inactivity",
     "inactivity_days": 30, "action": "message",
     "intent": "A gentle 'thinking of you' note with no pressure at all.",
     "cooldown_days": 45, "priority": 10},
)


async def seed_starter_idle_rules(product_id: int) -> None:
    """Give a NEW product the default 7/14/30 idle ladder (create_product path).

    Idempotent-safe the same way the starter KB is: only seeds when the
    product has no rules at all, so it can never duplicate or overwrite an
    operator's ladder.
    """
    if await db.list_retention_rules(product_id):
        return
    for rule in STARTER_IDLE_RULES:
        await db.create_retention_rule(product_id, dict(rule),
                                       updated_by="starter")


__all__ = ["run_product_idle_pings", "seed_starter_idle_rules",
           "STARTER_IDLE_RULES"]
