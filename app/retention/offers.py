"""Offer Engine — the agent's hands (DOC-2, bonus-CMS-ID model).

Owner decision (A5): the casino's Bonus Engine owns the bonus mechanics; its
CMS assigns every bonus an ID. Our catalog row references that ID
(`partner_bonus_id`) plus what the orchestrator itself needs — a cost
estimate for the budget guard, eligibility bounds, an enablement flag. A
grant is a call to the partner's bonus endpoint: "credit bonus <ID> to player
<X>", idempotent by `offer_grant_id` on BOTH sides.

Hard rails (triple-guarded against accidental real-money grants):
  - `offers_enabled` master switch (default OFF) + `offer_dry_run` (default
    ON) + `offer_daily_budget_usd` (default 0 = granting blocked).
  - `grant_offer` enters the model's allowed_actions ONLY after the
    deterministic eligibility/cooldown/budget resolve said yes; the model can
    never grant an offer that was not offered to it.
  - RG beats offers (trigger 'offer_grant' is a game trigger — any RG block
    suppresses it); VIP players never get an auto-offer on a high loss —
    they are routed to the VIP host queue instead (EPIC 8 boundary).
  - Order of operations: create the pending bonus -> partner confirms ->
    ONLY THEN the persona writes a message that mentions it. fraud_hold
    downgrades to a message without the bonus; a failed grant downgrades to
    silence. No promised-but-nonexistent bonuses, ever.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from app.core import config
from app.core import db
from app.retention import partner_out

log = logging.getLogger(__name__)

# Deny reasons (each lands in the decisions ledger).
R_NOT_ENABLED = "offer_not_enabled"
R_VIP_SUPPRESSED = "offer_vip_suppressed"
R_NOT_ELIGIBLE = "offer_not_eligible"
R_COOLDOWN = "offer_cooldown"
R_BUDGET = "offer_budget_reached"
R_LIFETIME_CAP = "offer_lifetime_cap"
R_RG_BLOCKED = "offer_rg_blocked"


def offers_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("offers_enabled")
    return config.RETENTION_OFFERS_ENABLED if v is None else bool(v)


def offer_dry_run(cfg: dict[str, Any]) -> bool:
    v = cfg.get("offer_dry_run")
    return config.RETENTION_OFFER_DRY_RUN if v is None else bool(v)


def classify_offer_trigger(evt: dict[str, Any], state: dict[str, Any],
                           cfg: dict[str, Any]) -> Optional[str]:
    """The deterministic trigger key for the DIRECT (event-path) offer flow.

    Only the loss tiers are event-detectable here; the idle/FTD triggers are
    journey territory (scheduled matching) and grant via journey steps."""
    if evt.get("event_name") != "bet_settled":
        return None
    loss = float(state.get("net_loss_24h_usd") or 0)
    high = float(cfg.get("v2_loss_high_usd") or 0)
    if high <= 0 or loss <= 0:
        return None
    if loss >= high:
        return "loss_high"
    if loss >= high / 2:
        return "loss_mid"
    return None


def grant_id_for(product_id: int, player_id: str, decision_id: Any) -> str:
    """Deterministic idempotency key: one decision -> one grant, retries
    collapse on both sides."""
    h = hashlib.sha1(
        f"{product_id}:{player_id}:{decision_id}".encode()).hexdigest()
    return f"og_{h[:12]}"


async def resolve_offer(product_id: int, ru: dict[str, Any], trigger_key: str,
                        cfg: dict[str, Any], state: dict[str, Any]
                        ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """The deterministic offer resolve: (offer_row | None, deny_reason).

    Every None carries its ledger reason. Runs BEFORE the model — grant_offer
    only enters allowed_actions when this said yes."""
    from app.retention import rg_guard
    from app.retention import scoring

    trigger = await db.get_offer_trigger(product_id, trigger_key)
    if trigger is None or not trigger.get("enabled"):
        return None, R_NOT_ENABLED

    # RG beats offers — always, and with its own audit trigger.
    rg_verdict = await rg_guard.gate(product_id, ru, "offer_grant", cfg)
    if rg_verdict is not None and rg_verdict.get("deny"):
        return None, R_RG_BLOCKED

    # VIP suppression (EPIC 8): a high-loss VIP gets a HUMAN, not an auto-bonus.
    if (trigger_key == "loss_high" and trigger.get("vip_suppress")
            and scoring.is_vip(ru)):
        try:
            await db.create_host_task(
                product_id, str(ru.get("player_id") or ""),
                reason="loss_high_vip",
                context={"net_loss_24h_usd": state.get("net_loss_24h_usd"),
                         "vip_level": ru.get("vip_level")})
            await db.log_admin_event(
                None, "retention_v2_offer_vip_suppressed",
                {"player_id": ru.get("player_id"), "trigger": trigger_key},
                product_id=product_id)
        except Exception:  # noqa: BLE001 - the suppression itself must hold
            log.exception("vip_host_task_failed product=%s", product_id)
        return None, R_VIP_SUPPRESSED

    offer = await db.get_offer_by_key(product_id, str(trigger["offer_key"]))
    if offer is None or not offer.get("enabled"):
        return None, R_NOT_ENABLED

    # Eligibility (deterministic).
    min_dep = offer.get("min_deposit_usd")
    if min_dep is not None:
        lifetime = float(ru.get("total_deposit_lifetime") or 0)
        if lifetime < float(min_dep):
            return None, R_NOT_ELIGIBLE
    countries = offer.get("allowed_countries") or []
    if countries:
        country = str(ru.get("country") or "").strip().upper()
        if country not in {str(c).strip().upper() for c in countries}:
            return None, R_NOT_ELIGIBLE

    # Offer cooldown + lifetime cap (separate from message cooldowns).
    cd = await db.get_offer_cooldown(product_id,
                                     str(ru.get("player_id") or ""))
    cooldown_h = int(cfg.get("offer_cooldown_hours")
                     or config.RETENTION_OFFER_COOLDOWN_HOURS)
    if cd and cd.get("last_offer_at") is not None and cooldown_h > 0:
        from app.retention import retention_v2
        days = retention_v2.days_since(cd["last_offer_at"])
        if days is not None and days * 24 < cooldown_h:
            return None, R_COOLDOWN
    lifetime_cap = int(cfg.get("offer_lifetime_cap")
                       or config.RETENTION_OFFER_LIFETIME_CAP)
    if (lifetime_cap > 0 and cd
            and int(cd.get("offers_granted_total") or 0) >= lifetime_cap):
        return None, R_LIFETIME_CAP

    # The stimulus budget (separate from the AI budget; 0 = granting blocked).
    budget = float(cfg.get("offer_daily_budget_usd")
                   if cfg.get("offer_daily_budget_usd") is not None
                   else config.RETENTION_OFFER_DAILY_BUDGET_USD)
    cost = float(offer.get("cost_estimate_usd") or 0)
    spent = await db.offers_cost_today(product_id)
    if spent + cost > budget:
        return None, R_BUDGET

    return offer, None


async def resolve_offer_by_key(product_id: int, ru: dict[str, Any],
                               offer_key: str, cfg: dict[str, Any]
                               ) -> tuple[Optional[dict[str, Any]],
                                          Optional[str]]:
    """The journey-step variant of the resolve: the offer is named explicitly
    by the step (no trigger mapping, no VIP fork — the journey's own entry
    conditions own the segmentation), but EVERY protective guard still runs:
    RG, catalog enablement, eligibility, cooldown, budget, lifetime cap."""
    from app.retention import rg_guard
    if not offers_enabled(cfg):
        return None, R_NOT_ENABLED
    rg_verdict = await rg_guard.gate(product_id, ru, "offer_grant", cfg)
    if rg_verdict is not None and rg_verdict.get("deny"):
        return None, R_RG_BLOCKED
    offer = await db.get_offer_by_key(product_id, offer_key)
    if offer is None or not offer.get("enabled"):
        return None, R_NOT_ENABLED
    min_dep = offer.get("min_deposit_usd")
    if min_dep is not None:
        if float(ru.get("total_deposit_lifetime") or 0) < float(min_dep):
            return None, R_NOT_ELIGIBLE
    countries = offer.get("allowed_countries") or []
    if countries:
        country = str(ru.get("country") or "").strip().upper()
        if country not in {str(c).strip().upper() for c in countries}:
            return None, R_NOT_ELIGIBLE
    cd = await db.get_offer_cooldown(product_id,
                                     str(ru.get("player_id") or ""))
    cooldown_h = int(cfg.get("offer_cooldown_hours")
                     or config.RETENTION_OFFER_COOLDOWN_HOURS)
    if cd and cd.get("last_offer_at") is not None and cooldown_h > 0:
        from app.retention import retention_v2
        days = retention_v2.days_since(cd["last_offer_at"])
        if days is not None and days * 24 < cooldown_h:
            return None, R_COOLDOWN
    lifetime_cap = int(cfg.get("offer_lifetime_cap")
                       or config.RETENTION_OFFER_LIFETIME_CAP)
    if (lifetime_cap > 0 and cd
            and int(cd.get("offers_granted_total") or 0) >= lifetime_cap):
        return None, R_LIFETIME_CAP
    budget = float(cfg.get("offer_daily_budget_usd")
                   if cfg.get("offer_daily_budget_usd") is not None
                   else config.RETENTION_OFFER_DAILY_BUDGET_USD)
    cost = float(offer.get("cost_estimate_usd") or 0)
    if await db.offers_cost_today(product_id) + cost > budget:
        return None, R_BUDGET
    return offer, None


async def do_offer_grant(product: dict[str, Any], ru: dict[str, Any],
                         offer: dict[str, Any], decision_ref: Any,
                         cfg: dict[str, Any], *,
                         decision_id: Optional[int] = None
                         ) -> dict[str, Any]:
    """Idempotent grant: ledger row first, then the partner call (unless
    dry-run), then the row updated with the partner's verdict."""
    pid = int(product["id"])
    player_id = str(ru.get("player_id") or "")
    grant_id = grant_id_for(pid, player_id, decision_ref)

    existing = await db.get_offer_grant(pid, grant_id)
    if existing and existing.get("status") == "granted":
        return existing  # idempotent: one decision never grants twice

    dry = offer_dry_run(cfg)
    cost = float(offer.get("cost_estimate_usd") or 0)
    await db.upsert_offer_grant(
        pid, grant_id, player_id=player_id, decision_id=decision_id,
        offer_key=str(offer["offer_key"]),
        offer_type=str(offer.get("offer_type") or "bonus"),
        partner_bonus_id=offer.get("partner_bonus_id"),
        params_snapshot=offer.get("params") or {},
        cost_usd=cost, status="dry_run" if dry else "pending")
    if dry:
        return await db.get_offer_grant(pid, grant_id)

    timeout = int(cfg.get("offer_grant_timeout_sec")
                  or config.RETENTION_OFFER_GRANT_TIMEOUT_SEC)
    payload = {
        "offer_grant_id": grant_id,
        "player_id": player_id,
        "bonus_id": offer.get("partner_bonus_id"),
        "offer_type": offer.get("offer_type") or "bonus",
        "params": offer.get("params") or {},
    }
    try:
        resp = await partner_out.post_json(
            product, str(product.get("offer_grant_url") or ""), payload,
            timeout_sec=timeout)
        status = str(resp.get("status") or "failed")
        if status == "duplicate":
            status = "granted"  # the partner already holds this grant
        detail = str(resp.get("reason") or "") or None
        partner_ref = resp.get("partner_ref")
        credited = resp.get("credited_usd")
    except partner_out.PartnerCallError as exc:
        status, detail, partner_ref, credited = "failed", str(exc), None, None
    if status not in ("granted", "fraud_hold", "failed"):
        status, detail = "failed", f"unknown partner status {status!r}"
    await db.update_offer_grant(
        pid, grant_id, status=status, partner_ref=partner_ref,
        detail=detail,
        cost_usd=float(credited) if credited is not None else cost)
    if status == "granted":
        await db.touch_offer_cooldown(pid, player_id)
        await db.log_admin_event(
            None, "retention_v2_offer_granted",
            {"grant_id": grant_id, "offer_key": offer["offer_key"],
             "partner_ref": partner_ref, "cost_usd": cost},
            product_id=pid)
    elif status == "fraud_hold":
        await db.log_admin_event(
            None, "retention_v2_offer_fraud_hold",
            {"grant_id": grant_id, "player_id": player_id, "detail": detail},
            product_id=pid)
    return await db.get_offer_grant(pid, grant_id)


def offer_constraint_line(offer: dict[str, Any]) -> str:
    """The decision-prompt line describing the grantable stimulus."""
    desc = str(offer.get("description") or offer.get("offer_key") or "a bonus")
    return (f"you MAY grant the player a real bonus this turn: {desc}. "
            "Choose action 'grant_offer' ONLY if a gift genuinely fits the "
            "moment; the bonus is credited before your message goes out, so "
            "you may then mention it warmly (never invent its terms).")
