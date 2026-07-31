"""Admin API of the retention orchestrator (measurement / RG / frequency /
scoring / offers / journeys / templates / channels) + the global integration
checklist.

Every route rides the same authorization choke points as the rest of
/admin/*: `require_admin` on the router, `require_product_read` /
`require_product_write` per product, `require_global_write` for the few
deploy-level surfaces (the RG audit — the MVP compliance role is the owner's
global admin — and the integration checklist).
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import db
from app.core import settings as settings_mod
from app.core import tenancy
from app.api import admin_auth
from app.api.admin_auth import require_admin, require_admin_write
from app.retention import channels as channels_mod
from app.retention import frequency as frequency_mod
from app.retention import measurement
from app.retention import rg_guard
from app.retention import scenario_library
from app.retention import scoring as scoring_mod

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/retention", tags=["retention-orchestrator"],
                   dependencies=[Depends(require_admin)])
system_router = APIRouter(prefix="/admin", tags=["integration-checklist"],
                          dependencies=[Depends(require_admin)])


def _parse_dt(value: Optional[str], default: _dt.datetime) -> _dt.datetime:
    if not value:
        return default
    parsed = db._as_ts(value)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Bad date value.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


# ===========================================================================
# Measurement: holdout + uplift
# ===========================================================================
class HoldoutWrite(BaseModel):
    holdout_pct: int = Field(ge=0, le=50)
    holdout_salt: str = Field(min_length=1, max_length=64)
    note: Optional[str] = None


@router.get("/holdout/config")
async def get_holdout_config(product_id: int,
                             admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    tenancy.set_current_product(product_id)
    cfg = settings_mod.retention()
    stored = await db.get_holdout_config(product_id)
    return JSONResponse(content={
        "holdout_pct": cfg.get("holdout_pct"),
        "holdout_salt": cfg.get("holdout_salt"),
        "history": stored,
    })


@router.put("/holdout/config")
async def put_holdout_config(product_id: int, body: HoldoutWrite,
                             admin=Depends(require_admin_write)
                             ) -> JSONResponse:
    """Write the experiment config: an audit row + the hot settings keys the
    guard reads (the product layer of the retention group), in one call."""
    await admin_auth.require_product_write(admin, product_id)
    await db.insert_holdout_config(
        product_id, holdout_pct=body.holdout_pct,
        holdout_salt=body.holdout_salt.strip(), note=body.note,
        created_by=admin.get("email"))
    current = (await db.get_product_settings(product_id)).get("retention") or {}
    current = dict(current)
    current["holdout_pct"] = body.holdout_pct
    current["holdout_salt"] = body.holdout_salt.strip()
    await db.set_product_setting(product_id, "retention", current,
                                 updated_by=admin.get("email"))
    await settings_mod.reload()
    await db.log_admin_event(
        None, "retention_v2_holdout_config_changed",
        {"pct": body.holdout_pct, "salt": body.holdout_salt,
         "by": admin.get("email")}, product_id=product_id)
    return JSONResponse(content={"ok": True, "holdout_pct": body.holdout_pct,
                                 "holdout_salt": body.holdout_salt})


@router.get("/holdout/status")
async def get_holdout_status(product_id: int,
                             admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.holdout_status(product_id))


@router.get("/uplift")
async def get_uplift(product_id: int, dt_from: Optional[str] = None,
                     dt_to: Optional[str] = None,
                     admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    now = _dt.datetime.now(_dt.timezone.utc)
    frm = _parse_dt(dt_from, now - _dt.timedelta(days=28))
    to = _parse_dt(dt_to, now)
    rows = await measurement.compute_uplift(product_id, frm, to)
    return JSONResponse(content={"windows": rows,
                                 "from": frm.isoformat(),
                                 "to": to.isoformat()})


@router.post("/attribution/run")
async def run_attribution(product_id: int,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    from app.retention import outcomes
    await admin_auth.require_product_write(admin, product_id)
    res = await outcomes.run_product_attribution(product_id, force=True)
    return JSONResponse(content=res)


# ===========================================================================
# RG guard (MVP compliance = the owner's global admin)
# ===========================================================================
class RgSignalWrite(BaseModel):
    signal_key: str
    enabled: bool = False
    block_class: str = "conditional"
    params: dict[str, Any] = Field(default_factory=dict)
    source: str = "computed"


class RgStatusWrite(BaseModel):
    player_id: str
    rg_status: str
    rg_status_until: Optional[str] = None


@router.get("/rg/signals")
async def get_rg_signals(product_id: int,
                         admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_rg_signal_config(product_id)})


@router.put("/rg/signals")
async def put_rg_signal(product_id: int, body: RgSignalWrite,
                        admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.block_class not in ("permanent", "conditional"):
        raise HTTPException(status_code=400,
                            detail="block_class must be permanent|conditional")
    if body.source not in ("computed", "casino_flag"):
        raise HTTPException(status_code=400,
                            detail="source must be computed|casino_flag")
    row = await db.upsert_rg_signal_config(
        product_id, signal_key=body.signal_key.strip(), enabled=body.enabled,
        block_class=body.block_class, params=body.params, source=body.source,
        updated_by=admin.get("email"))
    rg_guard.reset_signal_cache()
    await db.log_admin_event(None, "rg_signal_config_changed",
                             {"signal": body.signal_key,
                              "enabled": body.enabled,
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True, "signal": row})


@router.get("/rg/audit")
async def get_rg_audit(product_id: int, page: int = 1, page_size: int = 50,
                       decision: Optional[str] = None,
                       admin=Depends(require_admin)) -> JSONResponse:
    """Compliance audit read: GLOBAL admin only at MVP (the owner acts as the
    compliance officer; the roles model is not widened for now)."""
    admin_auth.require_global_write(admin)
    data = await db.list_rg_audit(product_id, page=max(page, 1),
                                  page_size=max(1, min(page_size, 200)),
                                  decision=decision)
    summary = await db.rg_audit_summary(product_id)
    return JSONResponse(content={**data, "summary": summary})


@router.get("/rg/status")
async def get_rg_status(product_id: int, player_id: str,
                        admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    ru = await db.get_retention_user_by_player(product_id, player_id)
    if ru is None:
        raise HTTPException(status_code=404, detail="Player not linked.")
    return JSONResponse(content={
        "player_id": player_id,
        "rg_status": ru.get("rg_status"),
        "rg_status_source": ru.get("rg_status_source"),
        "rg_status_until": ru.get("rg_status_until"),
        "marketing_consent": ru.get("marketing_consent"),
        "rg_signals": ru.get("rg_signals"),
    })


@router.post("/rg/set-status")
async def set_rg_status(product_id: int, body: RgStatusWrite,
                        admin=Depends(require_admin_write)) -> JSONResponse:
    """Manual RG marking (the bridge while the casino feed is not wired):
    global-admin only — a product admin must not be able to lift a block."""
    admin_auth.require_global_write(admin)
    if body.rg_status not in rg_guard.RG_STATUSES:
        raise HTTPException(status_code=400,
                            detail="rg_status must be one of: "
                                   + ", ".join(rg_guard.RG_STATUSES))
    ru = await db.get_retention_user_by_player(product_id, body.player_id)
    if ru is None:
        raise HTTPException(status_code=404, detail="Player not linked.")
    await db.set_rg_status(product_id, int(ru["id"]), body.rg_status,
                           source="manual", until=body.rg_status_until)
    await db.log_admin_event(None, "rg_status_changed",
                             {"player_id": body.player_id,
                              "rg_status": body.rg_status,
                              "source": "manual", "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Frequency + send time
# ===========================================================================
class CapWrite(BaseModel):
    channel: str
    cohort: str = "mass"
    per_day: int = Field(ge=0, le=100)
    per_week: int = Field(ge=0, le=500)
    burst_per_hour: int = Field(ge=0, le=20)
    enabled: bool = True


class PriorityWrite(BaseModel):
    touch_type: str
    priority: int = Field(ge=1, le=5)
    channel_switch_on_cap: bool = False


@router.get("/frequency/caps")
async def get_caps(product_id: int,
                   admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_frequency_caps(product_id),
        "defaults": [{"channel": ch, "cohort": co, **caps}
                     for (ch, co), caps in
                     frequency_mod.DEFAULT_CAPS.items()]})


@router.put("/frequency/caps")
async def put_cap(product_id: int, body: CapWrite,
                  admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    row = await db.upsert_frequency_cap(
        product_id, channel=body.channel.strip(), cohort=body.cohort.strip(),
        per_day=body.per_day, per_week=body.per_week,
        burst_per_hour=body.burst_per_hour, enabled=body.enabled,
        updated_by=admin.get("email"))
    frequency_mod.reset_caches()
    return JSONResponse(content={"ok": True, "cap": row})


@router.get("/frequency/priorities")
async def get_priorities(product_id: int,
                         admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_touch_priorities(product_id),
        "defaults": frequency_mod.DEFAULT_TOUCH_PRIORITIES})


@router.put("/frequency/priorities")
async def put_priority(product_id: int, body: PriorityWrite,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    row = await db.upsert_touch_priority(
        product_id, touch_type=body.touch_type.strip(),
        priority=body.priority,
        channel_switch_on_cap=body.channel_switch_on_cap,
        updated_by=admin.get("email"))
    frequency_mod.reset_caches()
    return JSONResponse(content={"ok": True, "priority": row})


@router.get("/send-time/profile")
async def get_send_time_profile(product_id: int, player_id: str,
                                admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    profile = await db.get_activity_profile(product_id, player_id)
    return JSONResponse(content={"profile": profile})


# ===========================================================================
# Scoring
# ===========================================================================
class ScoringConfigWrite(BaseModel):
    config_key: str
    params: dict[str, Any]


@router.get("/scoring/config")
async def get_scoring_config(product_id: int,
                             admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    stored = await db.get_scoring_config(product_id)
    return JSONResponse(content={
        "stored": stored,
        "defaults": {
            "dormancy_boundaries": scoring_mod.DEFAULT_DORMANCY_BOUNDARIES,
            "value_tiers": scoring_mod.DEFAULT_VALUE_TIERS,
            "vip_mapping": scoring_mod.DEFAULT_VIP_MAPPING,
        }})


@router.put("/scoring/config")
async def put_scoring_config(product_id: int, body: ScoringConfigWrite,
                             admin=Depends(require_admin_write)
                             ) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.config_key not in ("dormancy_boundaries", "value_tiers",
                               "vip_mapping"):
        raise HTTPException(status_code=400, detail="Unknown config_key.")
    await db.upsert_scoring_config(product_id, body.config_key, body.params,
                                   admin.get("email"))
    await db.log_admin_event(None, "retention_v2_scoring_config_changed",
                             {"key": body.config_key,
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True})


@router.get("/scoring/player")
async def get_scoring_player(product_id: int, player_id: str,
                             admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    ru = await db.get_retention_user_by_player(product_id, player_id)
    if ru is None:
        raise HTTPException(status_code=404, detail="Player not linked.")
    return JSONResponse(content={
        "dormancy_cohort": ru.get("dormancy_cohort"),
        "dormancy_since": ru.get("dormancy_since"),
        "rfm": {"recency": ru.get("rfm_recency"),
                "frequency": ru.get("rfm_frequency"),
                "monetary": ru.get("rfm_monetary"),
                "score": ru.get("rfm_score")},
        "value_tier": ru.get("value_tier"),
        "vip_segment": ru.get("vip_segment"),
        "total_deposit_lifetime": ru.get("total_deposit_lifetime"),
        "score_computed_at": ru.get("score_computed_at"),
        "sources": {"dormancy_cohort": "computed", "rfm": "computed",
                    "value_tier": "computed",
                    "vip_segment": "from_casino"},
    })


@router.get("/scoring/distribution")
async def get_scoring_distribution(product_id: int,
                                   admin=Depends(require_admin)
                                   ) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.scoring_distribution(product_id))


@router.get("/scoring/transitions")
async def get_scoring_transitions(product_id: int, page: int = 1,
                                  page_size: int = 50,
                                  admin=Depends(require_admin)
                                  ) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_cohort_transitions(
        product_id, page=max(page, 1),
        page_size=max(1, min(page_size, 200))))


# ===========================================================================
# Offers
# ===========================================================================
class OfferWrite(BaseModel):
    offer_key: str
    offer_type: str = "bonus"
    partner_bonus_id: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    cost_estimate_usd: float = Field(ge=0, default=0)
    enabled: bool = False
    min_deposit_usd: Optional[float] = Field(ge=0, default=None)
    allowed_countries: list[str] = Field(default_factory=list)
    description: str = ""


class OfferTriggerWrite(BaseModel):
    trigger_key: str
    offer_key: str
    vip_suppress: bool = True
    enabled: bool = False


@router.get("/offers/catalog")
async def get_offer_catalog(product_id: int,
                            admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_offer_catalog(product_id)})


@router.put("/offers/catalog")
async def put_offer(product_id: int, body: OfferWrite,
                    admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    try:
        settings_mod.ensure_english(body.description, "description")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = await db.upsert_offer_catalog(
        product_id, offer_key=body.offer_key.strip(),
        offer_type=body.offer_type.strip(),
        partner_bonus_id=(body.partner_bonus_id or "").strip() or None,
        params=body.params, cost_estimate_usd=body.cost_estimate_usd,
        enabled=body.enabled, min_deposit_usd=body.min_deposit_usd,
        allowed_countries=body.allowed_countries,
        description=body.description.strip(),
        updated_by=admin.get("email"))
    return JSONResponse(content={"ok": True, "offer": row})


@router.delete("/offers/catalog/{offer_key}")
async def delete_offer(product_id: int, offer_key: str,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if not await db.delete_offer_catalog(product_id, offer_key):
        raise HTTPException(status_code=404, detail="Offer not found.")
    return JSONResponse(content={"ok": True})


@router.get("/offers/triggers")
async def get_offer_triggers(product_id: int,
                             admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_offer_triggers(product_id)})


@router.put("/offers/triggers")
async def put_offer_trigger(product_id: int, body: OfferTriggerWrite,
                            admin=Depends(require_admin_write)
                            ) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    row = await db.upsert_offer_trigger(
        product_id, trigger_key=body.trigger_key.strip(),
        offer_key=body.offer_key.strip(), vip_suppress=body.vip_suppress,
        enabled=body.enabled, updated_by=admin.get("email"))
    return JSONResponse(content={"ok": True, "trigger": row})


@router.get("/offers/grants")
async def get_offer_grants(product_id: int, page: int = 1,
                           page_size: int = 50,
                           admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    data = await db.list_offer_grants(product_id, page=max(page, 1),
                                      page_size=max(1, min(page_size, 200)))
    tenancy.set_current_product(product_id)
    cfg = settings_mod.retention()
    return JSONResponse(content={
        **data,
        "budget": {"spent_today": await db.offers_cost_today(product_id),
                   "daily_budget_usd": cfg.get("offer_daily_budget_usd")}})


@router.get("/host-tasks")
async def get_host_tasks(product_id: int, status: Optional[str] = None,
                         admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_host_tasks(product_id, status=status)})


class HostTaskWrite(BaseModel):
    status: str


@router.put("/host-tasks/{task_id}")
async def put_host_task(product_id: int, task_id: int, body: HostTaskWrite,
                        admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.status not in ("open", "claimed", "done"):
        raise HTTPException(status_code=400, detail="Bad status.")
    if not await db.update_host_task(product_id, task_id, body.status):
        raise HTTPException(status_code=404, detail="Task not found.")
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Journeys + templates + the scenario library
# ===========================================================================
class JourneyWrite(BaseModel):
    journey_key: str
    name: str
    version: int = 1
    status: str = "draft"
    trigger: dict[str, Any] = Field(default_factory=dict)
    entry_conditions: list[dict[str, Any]] = Field(default_factory=list)
    exit_conditions: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = True
    priority: int = Field(ge=1, le=5, default=3)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JourneyStatusWrite(BaseModel):
    status: str
    dry_run: Optional[bool] = None


@router.get("/journeys")
async def get_journeys(product_id: int,
                       admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    items = await db.list_journeys(product_id)
    for j in items:
        j["enrollments"] = await db.journey_enrollment_stats(
            product_id, j["journey_key"])
    return JSONResponse(content={"items": items})


@router.put("/journeys")
async def put_journey(product_id: int, body: JourneyWrite,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.status not in ("draft", "active", "paused"):
        raise HTTPException(status_code=400, detail="Bad status.")
    trig_type = str((body.trigger or {}).get("type") or "")
    if trig_type not in ("event", "scheduled"):
        raise HTTPException(status_code=400,
                            detail="trigger.type must be event|scheduled")
    for step in body.steps:
        if not step.get("channel"):
            raise HTTPException(status_code=400,
                                detail="every step must name a channel")
    row = await db.upsert_journey(product_id, body.model_dump(),
                                  updated_by=admin.get("email"),
                                  is_starter=False)
    return JSONResponse(content={"ok": True, "journey": row})


@router.put("/journeys/{journey_key}/status")
async def put_journey_status(product_id: int, journey_key: str,
                             body: JourneyStatusWrite,
                             admin=Depends(require_admin_write)
                             ) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.status not in ("draft", "active", "paused"):
        raise HTTPException(status_code=400, detail="Bad status.")
    if not await db.set_journey_status(product_id, journey_key, body.status,
                                       dry_run=body.dry_run):
        raise HTTPException(status_code=404, detail="Journey not found.")
    warnings: list[dict[str, Any]] = []
    if body.status == "active":
        # A7: a recovery journey overlaps the idle ladder on the same quiet
        # days — surface the rungs the operator should switch off.
        rules = await db.list_retention_rules(product_id, only_enabled=True)
        warnings = scenario_library.ladder_overlap_warning(journey_key, rules)
        await db.log_admin_event(None, "scenario_activated",
                                 {"journey": journey_key,
                                  "by": admin.get("email")},
                                 product_id=product_id)
    return JSONResponse(content={"ok": True,
                                 "ladder_overlap_warning": warnings})


@router.delete("/journeys/{journey_key}")
async def delete_journey(product_id: int, journey_key: str,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if not await db.delete_journey(product_id, journey_key):
        raise HTTPException(status_code=404, detail="Journey not found.")
    return JSONResponse(content={"ok": True})


@router.get("/journeys/{journey_key}/enrollments")
async def get_enrollments(product_id: int, journey_key: str,
                          status: Optional[str] = None,
                          admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "stats": await db.journey_enrollment_stats(product_id, journey_key),
        "items": await db.list_enrollments(product_id, journey_key,
                                           status=status)})


@router.post("/journeys/run")
async def run_journeys_now(product_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    from app.retention import journeys as journeys_mod
    await admin_auth.require_product_write(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    tenancy.set_current_product(product_id)
    stats = await journeys_mod.run_product_journeys(
        product, settings_mod.retention(), force=True)
    return JSONResponse(content={"stats": stats})


class SeedReq(BaseModel):
    packs: Optional[list[str]] = None


@router.post("/scenarios/seed")
async def seed_scenarios(product_id: int, body: SeedReq,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    known = set(scenario_library.STARTER_JOURNEYS) | set(
        scenario_library.STARTER_TEMPLATES)
    packs = body.packs or sorted(known)
    bad = [p for p in packs if p not in known]
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"Unknown packs: {', '.join(bad)}")
    res = await scenario_library.seed_library(product_id, packs,
                                              updated_by=admin.get("email"))
    return JSONResponse(content={**res, "status": "all draft/dry_run"})


class TemplateWrite(BaseModel):
    template_key: str
    type: str = "telegram"
    mode: str = "persona_brief"
    intent: str = ""
    localizations: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"


@router.get("/templates")
async def get_templates(product_id: int,
                        admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={"items": await db.list_templates(product_id)})


@router.put("/templates")
async def put_template(product_id: int, body: TemplateWrite,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.mode not in ("persona_brief", "verbatim"):
        raise HTTPException(status_code=400,
                            detail="mode must be persona_brief|verbatim")
    if body.mode == "persona_brief":
        try:
            settings_mod.ensure_english(body.intent, "intent")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    row = await db.upsert_template(
        product_id, template_key=body.template_key.strip(), type_=body.type,
        mode=body.mode, intent=body.intent.strip(),
        localizations=body.localizations, variables=body.variables,
        status=body.status, is_starter=False, updated_by=admin.get("email"))
    return JSONResponse(content={"ok": True, "template": row})


@router.delete("/templates/{template_key}")
async def delete_template(product_id: int, template_key: str,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if not await db.delete_template(product_id, template_key):
        raise HTTPException(status_code=404, detail="Template not found.")
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Channels + deliveries + partner endpoints
# ===========================================================================
class ChannelWrite(BaseModel):
    channel: str
    enabled: bool = False
    priority: int = Field(ge=0, le=1000, default=100)
    config: dict[str, Any] = Field(default_factory=dict)


class PartnerEndpointsWrite(BaseModel):
    offer_grant_url: Optional[str] = None
    delivery_endpoint_url: Optional[str] = None


@router.get("/channels")
async def get_channels(product_id: int,
                       admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    product = await db.get_product(product_id)
    return JSONResponse(content={
        "items": await db.list_channel_config(product_id),
        "endpoints": {
            "offer_grant_url": (product or {}).get("offer_grant_url"),
            "delivery_endpoint_url": (product or {}).get(
                "delivery_endpoint_url"),
        }})


@router.put("/channels")
async def put_channel(product_id: int, body: ChannelWrite,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if body.channel not in channels_mod.CHANNELS:
        raise HTTPException(status_code=400,
                            detail="channel must be one of: "
                                   + ", ".join(channels_mod.CHANNELS))
    row = await db.upsert_channel_config(
        product_id, channel=body.channel, enabled=body.enabled,
        priority=body.priority, channel_config=body.config,
        updated_by=admin.get("email"))
    channels_mod.reset_caches()
    return JSONResponse(content={"ok": True, "channel": row})


@router.put("/partner-endpoints")
async def put_partner_endpoints(product_id: int, body: PartnerEndpointsWrite,
                                admin=Depends(require_admin_write)
                                ) -> JSONResponse:
    """The outbound casino endpoints (offer-grant / delegated deliver). The
    Bearer secret for them (partner_out_key) is written through the normal
    product-secrets endpoint."""
    await admin_auth.require_product_write(admin, product_id)
    from app.retention import player_sync
    for url in (body.offer_grant_url, body.delivery_endpoint_url):
        if url and not await player_sync.is_safe_outbound_url(url):
            raise HTTPException(status_code=400,
                                detail=f"Unsafe or unresolvable URL: {url}")
    ok = await db.set_product_partner_endpoints(
        product_id, offer_grant_url=body.offer_grant_url,
        delivery_endpoint_url=body.delivery_endpoint_url)
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found.")
    return JSONResponse(content={"ok": True})


@router.get("/deliveries")
async def get_deliveries(product_id: int, status: Optional[str] = None,
                         page: int = 1, page_size: int = 50,
                         admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_deliveries(
        product_id, status=status, page=max(page, 1),
        page_size=max(1, min(page_size, 200))))


# ===========================================================================
# Integration checklist (global, System section)
# ===========================================================================
class ChecklistWrite(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    blocking: Optional[str] = None


class ChecklistCreate(BaseModel):
    item_key: str
    title: str
    description: str = ""
    owner: str = ""
    blocking: str = ""


_CHECKLIST_STATUSES = ("waiting", "in_progress", "received", "wired",
                       "not_needed")


@system_router.get("/integration-checklist")
async def get_checklist(admin=Depends(require_admin)) -> JSONResponse:
    return JSONResponse(content={
        "items": await db.list_integration_checklist(),
        "statuses": list(_CHECKLIST_STATUSES)})


@system_router.put("/integration-checklist/{item_key}")
async def put_checklist(item_key: str, body: ChecklistWrite,
                        admin=Depends(require_admin_write)) -> JSONResponse:
    admin_auth.require_global_write(admin)
    if body.status is not None and body.status not in _CHECKLIST_STATUSES:
        raise HTTPException(status_code=400,
                            detail="status must be one of: "
                                   + ", ".join(_CHECKLIST_STATUSES))
    row = await db.update_integration_checklist(
        item_key, status=body.status, notes=body.notes, title=body.title,
        description=body.description, owner=body.owner,
        blocking=body.blocking, updated_by=admin.get("email"))
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return JSONResponse(content={"ok": True, "item": row})


@system_router.post("/integration-checklist")
async def post_checklist(body: ChecklistCreate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    admin_auth.require_global_write(admin)
    row = await db.create_integration_checklist_item(
        body.item_key.strip(), title=body.title.strip(),
        description=body.description, owner=body.owner,
        blocking=body.blocking, updated_by=admin.get("email"))
    if row is None:
        raise HTTPException(status_code=409, detail="item_key already exists.")
    return JSONResponse(content={"ok": True, "item": row})
