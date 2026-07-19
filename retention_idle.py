"""Idle re-engagement — the agent's INACTIVITY trigger (the admin-managed
"player quiet N days -> Nika writes first" ladder in `retention_rules`).

The event-driven agent (retention_v2) reacts to things that HAPPEN; a player
who simply went quiet produces no events, so without this sweep they are never
written to again. The rules ladder (7 / 14 / 30 days …) is edited on the Idle pings tab of the
admin Proactive agent page (/retention-agent?tab=idle); the sweep runs from the SAME worker
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
import logging
import time
from typing import Any, Optional

import chat_service
import config
import db
import delivery
import retention
import retention_v2

log = logging.getLogger(__name__)

# The idle ladder moves on a scale of DAYS, so sweeping it on the worker's
# seconds-scale cadence is pure waste. In-process pacing: one idle sweep per
# product at most every `retention.idle_sweep_interval_sec` (hot per-product
# knob; the event pipeline still runs every tick). The config default is the
# fallback when the knob is missing from an older stored override.
_last_sweep: dict[int, float] = {}

_TRIGGER_REASONS = {
    "bot_inactivity": "they have not written to you here",
    "casino_inactivity": "they have not been playing on the site",
    "no_deposit": "they have not made a deposit in a while",
}


def _idle_days_for(ru: dict[str, Any], trigger_kind: str,
                   now: _dt.datetime) -> Optional[float]:
    """Days of idleness for this trigger, or None when the signal is absent
    (a rule on casino data never fires for a player the casino hasn't fed)."""
    if trigger_kind == "bot_inactivity":
        return retention_v2.days_since(ru.get("last_active_at"), now)
    if trigger_kind == "casino_inactivity":
        candidates = [d for d in (retention_v2.days_since(ru.get("last_login_at"), now),
                                  retention_v2.days_since(ru.get("last_played_at"), now))
                      if d is not None]
        return min(candidates) if candidates else None
    if trigger_kind == "no_deposit":
        return retention_v2.days_since(ru.get("last_deposit_at"), now)
    return None


def _idle_anchor_for(ru: dict[str, Any], trigger_kind: str) -> Any:
    """The timestamp that marks the START of the current silence stretch for this
    trigger kind — the anti-cascade memory anchor. It MUST use the same clock as
    _idle_days_for (idle = now - anchor), so that a fired rung 'counts' only while
    it sits inside the ongoing silence.

    Must anchor PER trigger kind: `last_active_at` moves on any bot reply, so
    a casino/deposit silence stretch must key on its own clock or a player who
    replies to pings resets the fired memory mid-stretch."""
    if trigger_kind == "bot_inactivity":
        return ru.get("last_active_at")
    if trigger_kind == "casino_inactivity":
        # idle = min(days) => the MORE RECENT of login/played is the stretch start
        ts = [t for t in (ru.get("last_login_at"), ru.get("last_played_at"))
              if t is not None]
        return max(ts) if ts else None
    if trigger_kind == "no_deposit":
        return ru.get("last_deposit_at")
    return ru.get("last_active_at")


async def _match_rule(ru: dict[str, Any], rules: list[dict[str, Any]]
                      ) -> Optional[tuple[dict[str, Any], int]]:
    """The highest-priority rule that fires for this player right now.

    Anti-cascade: during ONE silence stretch only a rung ABOVE the highest
    already-fired one may fire (per trigger kind) — per-rule cooldowns alone
    would let a long-quiet player receive the whole ladder in reverse at
    min-gap pace. The fired-rung memory resets when the player writes again,
    so a returning-then-quiet-again player restarts the ladder from the
    bottom; the SAME rung may re-fire after its own `cooldown_days`."""
    now = _dt.datetime.now(_dt.timezone.utc)
    vip = (ru.get("vip_level") or "").strip().lower()
    # Fired-rung memory, anchored per trigger kind on that kind's own silence
    # clock (see _idle_anchor_for).
    fired: dict[str, int] = {}
    for kind in {str(r.get("trigger_kind") or "") for r in rules}:
        part = await db.idle_rule_thresholds_fired_since(
            int(ru["id"]), _idle_anchor_for(ru, kind), trigger_kind=kind)
        fired.update(part)
    for rule in rules:  # already ordered priority DESC
        idle = _idle_days_for(ru, rule.get("trigger_kind", ""), now)
        if idle is None or idle < int(rule.get("inactivity_days") or 0):
            continue
        max_fired = fired.get(str(rule.get("trigger_kind") or ""))
        if (max_fired is not None
                and int(rule.get("inactivity_days") or 0) < max_fired):
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


async def run_product_idle_pings_locked(product: dict[str, Any],
                                        cfg: dict[str, Any], *,
                                        force: bool = False,
                                        limit: Optional[int] = None
                                        ) -> dict[str, Any]:
    """run_product_idle_pings under the SAME advisory lock the worker holds.

    Required: without the lock a button-run and the worker's sweep can both
    read a player's guard counters before either writes — double send (the
    same guard-race class run_product_events_locked guards). Blocking lock (not try-lock): the button
    should run right after the worker finishes, not silently no-op."""
    pool = db.pool()
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)",
                           retention_v2._ADVISORY_LOCK_KEY)
        try:
            return await run_product_idle_pings(product, cfg, force=force,
                                                limit=limit)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               retention_v2._ADVISORY_LOCK_KEY)


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
                   or config.RETENTION_IDLE_SWEEP_INTERVAL_SEC)
    if not force and now - _last_sweep.get(pid, 0.0) < interval:
        return {"skipped": "paced"}
    _last_sweep[pid] = now
    token = await db.get_product_telegram_token(pid)
    if not token:
        return {"skipped": "no_bot_token"}
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

    # The send mechanics live in the delivery seam (delivery.py) — shared
    # with the event agent; future channels plug in there.
    channel = delivery.channel_for_product(
        product, token, silent=bool(cfg.get("silent_notifications")))
    sent = failed = 0
    for ru in users:
        if sent + failed >= batch:
            break
        matched = await _match_rule(ru, rules)
        if matched is None:
            continue
        rule, idle_days = matched
        ok = await _send_idle_ping(channel, product, ru, rule, idle_days, cfg)
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "considered": len(users)}


async def _send_idle_ping(channel: delivery.TelegramChannel,
                          product: dict[str, Any],
                          ru: dict[str, Any], rule: dict[str, Any],
                          idle_days: int, cfg: dict[str, Any]) -> bool:
    """One idle touch: generate -> (dry-run?) -> send -> persist/ledgers."""
    pid = int(product["id"])
    rid = int(ru["id"])
    rule_id = int(rule["id"])
    action = rule.get("action") or "message"
    dry_run = bool(cfg.get("v2_dry_run"))
    lang = retention.resolve_user_lang(ru)

    async def _ledger(**overrides: Any) -> None:
        """One decisions-ledger row per attempt; only the outcome fields vary."""
        await db.insert_retention_v2_decision(
            pid, retention_user_id=rid, player_id=ru.get("player_id"),
            trigger_kind="idle", event_pk=None,
            event_name=f"idle:{rule.get('trigger_kind')}",
            state={"idle_days": idle_days},
            guard={"allow": True, "reasons": []},
            action=action, intent=rule.get("intent") or "",
            reason=f"idle rule '{rule.get('name')}' matched ({idle_days}d)",
            **overrides)

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
        await _ledger(dry_run=True)
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
        await _ledger(dry_run=False, delivered=False, detail="model_error",
                      cost_usd=0.0)
        await db.record_retention_ping(pid, rid, rule_id, action, "failed",
                                       detail="model_error")
        return False

    header = retention._rtn_text("rtn_ping_header", draft.lang).strip()
    delivered, detail, link_attached = await delivery.deliver_draft(
        channel, ru, draft, header=header or None, session_id=session["id"],
        photo_fallback_caption=retention.fallback_photo_caption(draft.lang))

    cost = float(draft.ai_meta.get("cost_usd") or 0)
    ping_context = (f"idle_reengagement: the player has been away about "
                    f"{idle_days} days ({reason})")
    if delivered:
        await db.persist_ping_turn(session["id"], draft.text or "[photo]",
                                   ai_meta=draft.ai_meta, product_id=pid,
                                   ping_context=ping_context,
                                   link_url=draft.link_url if link_attached else None)
        await db.record_retention_ping(pid, rid, rule_id, action, "sent",
                                       detail=rule.get("name"), cost_usd=cost)
        await _ledger(dry_run=False, delivered=True, detail=rule.get("name"),
                      cost_usd=cost)
        await db.log_admin_event(
            session["id"], "retention_ping",
            {"rule_id": rule_id, "rule": rule.get("name"), "action": action,
             "tg_user_id": ru.get("tg_user_id"), "idle_days": idle_days,
             "cost_usd": cost},
            product_id=pid)
        return True

    # The model call happened but nothing reached the player: account the cost
    # (invariant §4 — every OpenAI call gets an ai_interaction_logs row) AND write
    # a decisions-ledger row. The per-product daily AI budget reads ONLY that
    # ledger, so without this the generation spend of a persistently-failing send
    # (revoked token, blocked players) would be invisible to the budget — it could
    # keep generating past the daily stop-switch — and the Decisions audit would
    # be missing every failed idle attempt.
    await delivery.account_undelivered_generation(
        session["id"], draft, detail, product_id=pid, label="ping_undelivered")
    await _ledger(dry_run=False, delivered=False, detail=detail, cost_usd=cost)
    await db.record_retention_ping(pid, rid, rule_id, action, "failed",
                                   detail=detail, cost_usd=cost)
    log.warning("retention_idle_ping_failed product=%s player=%s detail=%s",
                pid, ru.get("player_id"), detail)
    return False


# The starter ladder a NEW product is seeded with (db.create_product) — the
# operator tunes/translates from the Idle pings tab. English intents (they are
# model-facing prompt material), brand-neutral. This is the production-tuned
# 3/5/7/10/14/21/30/45/60-day ladder: frequent light check-ins early, photos
# as milestones, and increasingly heartfelt, pressure-free reaches as the
# silence grows.
STARTER_IDLE_RULES: tuple[dict[str, Any], ...] = (
    {"name": "Quiet 3 days - check in", "trigger_kind": "bot_inactivity",
     "inactivity_days": 3, "action": "message",
     "intent": "You miss him a little. Ask warmly how his week is going and "
               "what he has been up to. Do not mention the casino unless it "
               "flows naturally.",
     "cooldown_days": 7, "priority": 10},
    {"name": "Quiet 5 days - playful nudge", "trigger_kind": "bot_inactivity",
     "inactivity_days": 5, "action": "message",
     "intent": "A few quiet days. Be playful and a touch teasing that he has "
               "gone quiet on you; ask what has been keeping him busy and "
               "hint you would love to hear from him.",
     "cooldown_days": 10, "priority": 15},
    {"name": "Quiet 7 days - photo", "trigger_kind": "bot_inactivity",
     "inactivity_days": 7, "action": "photo",
     "intent": "He has been away a while. Welcome him back with zero guilt, "
               "attach a photo as a small personal gift, and tease that the "
               "games lobby has something new worth telling you about.",
     "cooldown_days": 14, "priority": 20},
    {"name": "Quiet 10 days - warm photo", "trigger_kind": "bot_inactivity",
     "inactivity_days": 10, "action": "photo",
     "intent": "Ten quiet days. Reach out softly and personally, send a photo "
               "as a little something just for him, and gently wonder aloud "
               "when he is coming back to keep you company.",
     "cooldown_days": 21, "priority": 25},
    {"name": "Quiet 14 days - win back", "trigger_kind": "bot_inactivity",
     "inactivity_days": 14, "action": "message",
     "intent": "A longer silence. Be soft and personal: you thought of him. "
               "Invite him to check the promotions page for what is live for "
               "his account and to come back and tell you if it was worth it.",
     "cooldown_days": 30, "priority": 30},
    {"name": "Quiet 21 days - personal pull", "trigger_kind": "bot_inactivity",
     "inactivity_days": 21, "action": "message",
     "intent": "Three weeks without a word. Be warm and a little vulnerable: "
               "you have been wondering how he is doing. No pressure at all - "
               "just let him know his spot next to you is still open and you "
               "would be happy to hear from him.",
     "cooldown_days": 30, "priority": 40},
    {"name": "Quiet 30 days - comeback gift", "trigger_kind": "bot_inactivity",
     "inactivity_days": 30, "action": "photo",
     "intent": "A whole month has passed. Welcome him back like an old friend "
               "with no guilt whatsoever, send a photo as a comeback gift, and "
               "softly mention the lobby and current promotions are worth a "
               "look whenever he feels like it.",
     "cooldown_days": 45, "priority": 50},
    {"name": "Quiet 45 days - heartfelt", "trigger_kind": "bot_inactivity",
     "inactivity_days": 45, "action": "message",
     "intent": "A long absence. Be genuine and heartfelt, not salesy: you "
               "still think about him now and then. Keep it human and light, "
               "invite him to say hi whenever he wants, and let him know "
               "nothing has changed on your side.",
     "cooldown_days": 60, "priority": 60},
    {"name": "Quiet 60 days - last warm reach", "trigger_kind": "bot_inactivity",
     "inactivity_days": 60, "action": "photo",
     "intent": "Two months of silence - likely a last gentle attempt. Reach "
               "out warmly and without any pressure, send a photo as a "
               "genuine keepsake, and leave the door wide open for him to "
               "come back whenever he likes, no strings attached.",
     "cooldown_days": 90, "priority": 70},
)


async def seed_starter_idle_rules(product_id: int) -> None:
    """Give a NEW product the default idle ladder (create_product path).

    Idempotent-safe the same way the starter KB is: only seeds when the
    product has no rules at all, so it can never duplicate or overwrite an
    operator's ladder.
    """
    if await db.list_retention_rules(product_id):
        return
    for rule in STARTER_IDLE_RULES:
        await db.create_retention_rule(product_id, dict(rule),
                                       updated_by="starter")


__all__ = ["run_product_idle_pings", "run_product_idle_pings_locked",
           "seed_starter_idle_rules",
           "STARTER_IDLE_RULES"]
