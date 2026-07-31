"""Offer engine (DOC-2, bonus-CMS-ID model): the deterministic resolve chain,
idempotent granting, dry-run, VIP suppression, fraud_hold/failed downgrades."""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import offers
from app.retention import partner_out
from app.retention import rg_guard


def _iso_hours_ago(hours: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=hours)).isoformat()


def _cfg(**over):
    base = {
        "offers_enabled": True, "offer_dry_run": False,
        "offer_daily_budget_usd": 100.0, "offer_cooldown_hours": 72,
        "offer_lifetime_cap": 0, "offer_grant_timeout_sec": 10,
        "v2_loss_high_usd": 100.0,
        "rg_enabled": True, "rg_require_consent": False,
        "rg_unknown_status_policy": "warn",
    }
    base.update(over)
    return base


def _ru(**over):
    base = {"id": 10, "product_id": 1, "player_id": "p1", "vip_level": "gold",
            "vip_segment": None, "country": "DE", "rg_status": None,
            "total_deposit_lifetime": 500.0}
    base.update(over)
    return base


def _offer(**over):
    base = {"id": 1, "offer_key": "loss_cashback", "offer_type": "cashback",
            "partner_bonus_id": "BONUS-42", "params": {},
            "cost_estimate_usd": 10.0, "enabled": True,
            "min_deposit_usd": None, "allowed_countries": [],
            "description": "10% cashback up to $50"}
    base.update(over)
    return base


def _trigger(**over):
    base = {"trigger_key": "loss_high", "offer_key": "loss_cashback",
            "vip_suppress": True, "enabled": True}
    base.update(over)
    return base


def _wire(monkeypatch, *, trigger=None, offer=None, cooldown=None,
          spent=0.0):
    async def _get_trigger(pid, key):
        return trigger

    async def _get_offer(pid, key):
        return offer

    async def _get_cd(pid, player):
        return cooldown

    async def _spent(pid):
        return spent

    async def _audit(pid, **kw):
        return 1

    monkeypatch.setattr(db, "get_offer_trigger", _get_trigger)
    monkeypatch.setattr(db, "get_offer_by_key", _get_offer)
    monkeypatch.setattr(db, "get_offer_cooldown", _get_cd)
    monkeypatch.setattr(db, "offers_cost_today", _spent)
    monkeypatch.setattr(db, "insert_rg_audit", _audit)
    rg_guard.reset_signal_cache()


# ---------------------------------------------------------------------------
# Trigger classification
# ---------------------------------------------------------------------------
def test_classify_offer_trigger_loss_tiers():
    cfg = _cfg()
    evt = {"event_name": "bet_settled"}
    assert offers.classify_offer_trigger(
        evt, {"net_loss_24h_usd": 150}, cfg) == "loss_high"
    assert offers.classify_offer_trigger(
        evt, {"net_loss_24h_usd": 60}, cfg) == "loss_mid"
    assert offers.classify_offer_trigger(
        evt, {"net_loss_24h_usd": 10}, cfg) is None
    assert offers.classify_offer_trigger(
        {"event_name": "deposit_confirmed"}, {"net_loss_24h_usd": 500},
        cfg) is None


# ---------------------------------------------------------------------------
# The resolve chain
# ---------------------------------------------------------------------------
async def test_resolve_disabled_trigger(monkeypatch):
    _wire(monkeypatch, trigger=None)
    offer, reason = await offers.resolve_offer(1, _ru(), "loss_high", _cfg(),
                                               {})
    assert offer is None and reason == offers.R_NOT_ENABLED


async def test_resolve_vip_suppressed_routes_to_host(monkeypatch):
    tasks = []

    async def _host(pid, player, reason, context):
        tasks.append(reason)
        return 1

    async def _log(*a, **kw):
        pass

    _wire(monkeypatch, trigger=_trigger(), offer=_offer())
    monkeypatch.setattr(db, "create_host_task", _host)
    monkeypatch.setattr(db, "log_admin_event", _log)
    ru = _ru(vip_segment="vip")
    offer, reason = await offers.resolve_offer(
        1, ru, "loss_high", _cfg(), {"net_loss_24h_usd": 200})
    assert offer is None and reason == offers.R_VIP_SUPPRESSED
    assert tasks == ["loss_high_vip"]


async def test_resolve_rg_blocks_offer(monkeypatch):
    _wire(monkeypatch, trigger=_trigger(), offer=_offer())
    ru = _ru(rg_status="cool_off",
             rg_status_until=(_dt.datetime.now(_dt.timezone.utc)
                              + _dt.timedelta(days=2)).isoformat())
    offer, reason = await offers.resolve_offer(1, ru, "loss_high", _cfg(), {})
    assert offer is None and reason == offers.R_RG_BLOCKED


async def test_resolve_eligibility_and_budget(monkeypatch):
    # min deposit not met
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False),
          offer=_offer(min_deposit_usd=1000.0))
    offer, reason = await offers.resolve_offer(1, _ru(), "loss_high", _cfg(),
                                               {})
    assert reason == offers.R_NOT_ELIGIBLE
    # country filter
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False),
          offer=_offer(allowed_countries=["TR"]))
    offer, reason = await offers.resolve_offer(1, _ru(country="DE"),
                                               "loss_high", _cfg(), {})
    assert reason == offers.R_NOT_ELIGIBLE
    # budget exhausted
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False), offer=_offer(),
          spent=95.0)
    offer, reason = await offers.resolve_offer(1, _ru(), "loss_high", _cfg(),
                                               {})
    assert reason == offers.R_BUDGET
    # zero budget (the safe default) blocks everything
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False), offer=_offer())
    offer, reason = await offers.resolve_offer(
        1, _ru(), "loss_high", _cfg(offer_daily_budget_usd=0.0), {})
    assert reason == offers.R_BUDGET


async def test_resolve_cooldown_and_lifetime_cap(monkeypatch):
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False), offer=_offer(),
          cooldown={"last_offer_at": _iso_hours_ago(10),
                    "offers_granted_total": 1})
    offer, reason = await offers.resolve_offer(1, _ru(), "loss_high", _cfg(),
                                               {})
    assert reason == offers.R_COOLDOWN
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False), offer=_offer(),
          cooldown={"last_offer_at": _iso_hours_ago(100),
                    "offers_granted_total": 5})
    offer, reason = await offers.resolve_offer(
        1, _ru(), "loss_high", _cfg(offer_lifetime_cap=5), {})
    assert reason == offers.R_LIFETIME_CAP


async def test_resolve_happy_path(monkeypatch):
    _wire(monkeypatch, trigger=_trigger(vip_suppress=False), offer=_offer())
    offer, reason = await offers.resolve_offer(1, _ru(), "loss_high", _cfg(),
                                               {})
    assert offer is not None and reason is None


# ---------------------------------------------------------------------------
# Granting
# ---------------------------------------------------------------------------
def _grant_db(monkeypatch, store):
    async def _get(pid, gid):
        return store.get(gid)

    async def _upsert(pid, gid, **kw):
        store[gid] = {"offer_grant_id": gid, **kw}

    async def _update(pid, gid, **kw):
        store[gid].update({k: v for k, v in kw.items() if v is not None})

    async def _touch(pid, player):
        store.setdefault("_cooldowns", []).append(player)

    async def _log(*a, **kw):
        pass

    monkeypatch.setattr(db, "get_offer_grant", _get)
    monkeypatch.setattr(db, "upsert_offer_grant", _upsert)
    monkeypatch.setattr(db, "update_offer_grant", _update)
    monkeypatch.setattr(db, "touch_offer_cooldown", _touch)
    monkeypatch.setattr(db, "log_admin_event", _log)


async def test_grant_dry_run_no_partner_call(monkeypatch):
    store: dict = {}
    _grant_db(monkeypatch, store)

    async def _boom(*a, **kw):
        raise AssertionError("no partner call in dry run")

    monkeypatch.setattr(partner_out, "post_json", _boom)
    product = {"id": 1, "offer_grant_url": "https://casino.example/grant"}
    grant = await offers.do_offer_grant(product, _ru(), _offer(), 77,
                                        _cfg(offer_dry_run=True))
    assert grant["status"] == "dry_run"


async def test_grant_idempotent_and_granted(monkeypatch):
    store: dict = {}
    _grant_db(monkeypatch, store)
    calls = []

    async def _post(product, url, payload, timeout_sec=10):
        calls.append(payload)
        return {"status": "granted", "partner_ref": "bonus_889912",
                "credited_usd": 7.0}

    monkeypatch.setattr(partner_out, "post_json", _post)
    product = {"id": 1, "offer_grant_url": "https://casino.example/grant"}
    g1 = await offers.do_offer_grant(product, _ru(), _offer(), 77, _cfg())
    assert g1["status"] == "granted" and g1["partner_ref"] == "bonus_889912"
    assert store["_cooldowns"] == ["p1"]
    assert calls[0]["bonus_id"] == "BONUS-42"
    # Second call with the SAME decision ref: no second partner call.
    g2 = await offers.do_offer_grant(product, _ru(), _offer(), 77, _cfg())
    assert len(calls) == 1 and g2["status"] == "granted"


async def test_grant_fraud_hold_and_failed(monkeypatch):
    store: dict = {}
    _grant_db(monkeypatch, store)

    async def _hold(product, url, payload, timeout_sec=10):
        return {"status": "fraud_hold", "reason": "risk_score_high"}

    monkeypatch.setattr(partner_out, "post_json", _hold)
    product = {"id": 1, "offer_grant_url": "https://casino.example/grant"}
    g = await offers.do_offer_grant(product, _ru(), _offer(), 78, _cfg())
    assert g["status"] == "fraud_hold"
    assert "_cooldowns" not in store  # a held grant burns no cooldown

    async def _fail(product, url, payload, timeout_sec=10):
        raise partner_out.PartnerCallError("partner HTTP 500")

    monkeypatch.setattr(partner_out, "post_json", _fail)
    g = await offers.do_offer_grant(product, _ru(), _offer(), 79, _cfg())
    assert g["status"] == "failed"


def test_grant_id_deterministic():
    a = offers.grant_id_for(1, "p1", 77)
    assert a == offers.grant_id_for(1, "p1", 77)
    assert a != offers.grant_id_for(1, "p1", 78)
    assert a.startswith("og_")
