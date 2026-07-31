"""Player scoring (DOC-5): cohort classification, RFM determinism, value
tiers, the VIP mapping, the additive resolver extension and hot-path
recovery."""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import retention_v2
from app.retention import scoring


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "v2_loss_high_usd": 0.0,
        "scoring_enabled": True, "rfm_window_days": 30,
        "scoring_sweep_interval_sec": 3600,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {
        "id": 10, "product_id": 1, "player_id": "p1", "vip_level": "gold",
        "subscribed": True, "pings_muted": False,
        "last_active_at": _iso_days_ago(1),
        "last_login_at": _iso_days_ago(1), "last_played_at": _iso_days_ago(1),
        "last_deposit_at": _iso_days_ago(3),
        "registration_date": _iso_days_ago(40),
        "dormancy_cohort": None,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def test_cohort_boundaries_match_epic():
    for idle, expect in ((0, "active"), (6.9, "active"), (8, "dormant_d7"),
                         (13, "dormant_d10"), (20, "dormant_d14"),
                         (29, "dormant_d21"), (45, "dormant_d30"),
                         (70, "lost")):
        assert scoring.classify_cohort(idle) == expect, idle
    # No signal at all -> active (never guess a player dormant).
    assert scoring.classify_cohort(None) == "active"


def test_value_tiers():
    assert scoring.classify_value_tier(50) == "low"
    assert scoring.classify_value_tier(300) == "mid"
    assert scoring.classify_value_tier(700) == "high"
    assert scoring.classify_value_tier(8000) == "whale"
    # Config override moves a boundary without a redeploy.
    assert scoring.classify_value_tier(300, {"high": 250}) == "high"


def test_vip_mapping_epic7_classes():
    for cls in ("player", "bronze", "silver", "gold", "platinum"):
        assert scoring.map_vip_segment(cls) == "mass"
    assert scoring.map_vip_segment("VIP") == "vip"
    assert scoring.map_vip_segment("vip_plus") == "vip_plus"
    assert scoring.map_vip_segment("VIP+") == "vip_plus"
    # Unknown class is never guessed into VIP.
    assert scoring.map_vip_segment("diamond") == "mass"
    # Config-driven remap for a brand with its own naming.
    assert scoring.map_vip_segment("diamond",
                                   {"diamond": "vip"}) == "vip"


def test_is_vip_prefers_cached_segment():
    assert scoring.is_vip({"vip_segment": "vip", "vip_level": "bronze"})
    assert not scoring.is_vip({"vip_segment": "mass", "vip_level": "vip"})
    # No cache yet -> maps the raw class.
    assert scoring.is_vip({"vip_segment": None, "vip_level": "vip"})


def test_rfm_deterministic():
    s1 = scoring.rfm_score(2, 6, 800)
    assert s1 == scoring.rfm_score(2, 6, 800)
    assert scoring.rfm_score(1, 12, 2000) == 5
    assert scoring.rfm_score(None, 0, 0) == 1


# ---------------------------------------------------------------------------
# Resolver extension (additive; OFF = untouched)
# ---------------------------------------------------------------------------
async def test_resolver_snapshot_unchanged_when_disabled(monkeypatch):
    async def _loss(product_id, player_id):
        return 0.0

    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    state = await retention_v2.resolve_player_state(
        1, _ru(), _cfg(scoring_enabled=False))
    assert "dormancy_cohort" not in state and "rfm" not in state


async def test_resolver_snapshot_annotated_when_enabled(monkeypatch):
    async def _loss(product_id, player_id):
        return 0.0

    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    ru = _ru(dormancy_cohort="dormant_d10", value_tier="high",
             vip_segment="mass", rfm_score=3)
    state = await retention_v2.resolve_player_state(1, ru, _cfg())
    assert state["dormancy_cohort"] == "dormant_d10"
    assert state["value_tier"] == "high"
    assert state["sources"]["vip_segment"] == "from_casino"
    # The base fields kept their semantics.
    assert state["user_status"] == "active"


# ---------------------------------------------------------------------------
# Recompute + transitions
# ---------------------------------------------------------------------------
async def test_recompute_logs_transition_once(monkeypatch):
    transitions = []
    cached = []

    async def _cfg_db(product_id):
        return {}

    async def _inputs(product_id, player_id, window_days):
        return {"deposits_window": 4, "sum_window": 250.0,
                "sum_lifetime": 700.0}

    async def _cache(product_id, rid, fields, cohort_changed=False):
        cached.append((fields, cohort_changed))

    async def _log_tr(product_id, player_id, **kw):
        transitions.append(kw)
        return True

    async def _log_ev(*a, **kw):
        pass

    monkeypatch.setattr(db, "get_scoring_config", _cfg_db)
    monkeypatch.setattr(db, "rfm_inputs", _inputs)
    monkeypatch.setattr(db, "cache_player_scoring", _cache)
    monkeypatch.setattr(db, "log_cohort_transition", _log_tr)
    monkeypatch.setattr(db, "log_admin_event", _log_ev)

    ru = _ru(last_login_at=_iso_days_ago(11), last_played_at=_iso_days_ago(11),
             dormancy_cohort="active")
    fields = await scoring.recompute_player(1, ru, _cfg())
    assert fields["dormancy_cohort"] == "dormant_d10"
    assert fields["value_tier"] == "high"
    assert transitions and transitions[0]["to_cohort"] == "dormant_d10"
    # Same state on the next pass -> no new transition.
    transitions.clear()
    await scoring.recompute_player(1, ru, _cfg())
    assert not transitions


async def test_mark_recovered_hot_path(monkeypatch):
    events = []

    async def _cache(product_id, rid, fields, cohort_changed=False):
        pass

    async def _log_tr(product_id, player_id, **kw):
        events.append(("transition", kw))
        return True

    async def _log_ev(sid, etype, payload, product_id=None):
        events.append((etype, payload))

    monkeypatch.setattr(db, "cache_player_scoring", _cache)
    monkeypatch.setattr(db, "log_cohort_transition", _log_tr)
    monkeypatch.setattr(db, "log_admin_event", _log_ev)

    ru = _ru(dormancy_cohort="dormant_d14")
    await scoring.mark_recovered(1, ru)
    assert ru["dormancy_cohort"] == "active"
    assert any(e[0] == "dormancy_recovered" for e in events)
    # Already active -> nothing happens.
    events.clear()
    await scoring.mark_recovered(1, ru)
    assert not events
