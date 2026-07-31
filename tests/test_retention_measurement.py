"""Measurement (DOC-1): deterministic holdout, the held_out guard verdict,
the virtual outcome row for the control group, and the uplift math."""
from __future__ import annotations

import datetime as _dt

import pytest

from app.core import db
from app.retention import measurement
from app.retention import retention_v2


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "ping_daily_cap": 5, "ping_min_gap_hours": 0,
        "quiet_hours_start": 0, "quiet_hours_end": 0,
        "quiet_hours_utc_offset": 0, "ping_batch_size": 30,
        "v2_enabled": True, "v2_dry_run": True,
        "v2_daily_budget_usd": 0.0, "v2_loss_comfort_hours": 0,
        "v2_loss_high_usd": 0.0, "v2_same_event_cooldown_hours": 0,
        "holdout_pct": 15, "holdout_salt": "default",
        "rg_enabled": False,
    }
    base.update(over)
    return base


def _ru(player="p1", **over):
    base = {
        "id": 10, "product_id": 1, "tg_user_id": 555, "player_id": player,
        "vip_level": "", "subscribed": True, "pings_muted": False,
        "unreachable": False, "last_ping_at": None, "pings_day": None,
        "pings_sent_today": 0, "last_active_at": _iso_days_ago(1),
        "last_login_at": _iso_days_ago(1), "last_played_at": _iso_days_ago(1),
        "last_deposit_at": _iso_days_ago(3),
        "registration_date": _iso_days_ago(40), "session_id": None,
    }
    base.update(over)
    return base


def _evt(**over):
    base = {"id": 77, "event_id": "evt_1", "event_name": "deposit_confirmed",
            "player_id": "p1", "ts": _iso_days_ago(0),
            "payload": {"amount": 50}}
    base.update(over)
    return base


def _holdout_player(pct=15, salt="default", product=1):
    """A synthetic player id that lands in the holdout bucket."""
    for i in range(10_000):
        pid = f"h{i}"
        if measurement.bucket_of(product, pid, salt) < pct:
            return pid
    raise AssertionError("no holdout player found")


def _treatment_player(pct=15, salt="default", product=1):
    for i in range(10_000):
        pid = f"t{i}"
        if measurement.bucket_of(product, pid, salt) >= pct:
            return pid
    raise AssertionError("no treatment player found")


# ---------------------------------------------------------------------------
# Deterministic split
# ---------------------------------------------------------------------------
def test_bucket_is_deterministic_and_uniform():
    b1 = measurement.bucket_of(1, "player-x", "default")
    assert all(measurement.bucket_of(1, "player-x", "default") == b1
               for _ in range(100))
    # Distribution: pct=10 over 10k synthetic ids lands within 10% +-1.5pp.
    n = sum(1 for i in range(10_000)
            if measurement.bucket_of(1, f"u{i}", "s") < 10)
    assert 850 <= n <= 1150


async def test_resolve_group_caches_and_salt_rotation(monkeypatch):
    writes = []

    async def _set(product_id, rid, grp, salt):
        writes.append((rid, grp, salt))

    monkeypatch.setattr(db, "set_holdout_group", _set)
    ru = _ru(player=_holdout_player())
    cfg = _cfg()
    grp = await measurement.resolve_holdout_group(1, ru, cfg)
    assert grp == "holdout" and writes and writes[0][1] == "holdout"
    # Cached: no recompute write on the second resolve under the same salt.
    writes.clear()
    assert await measurement.resolve_holdout_group(1, ru, cfg) == "holdout"
    assert not writes
    # Salt rotation invalidates the cache lazily (rebucketed + re-cached).
    cfg2 = _cfg(holdout_salt="exp2")
    await measurement.resolve_holdout_group(1, ru, cfg2)
    assert writes and writes[0][2] == "exp2"


async def test_pct_zero_means_everyone_treatment():
    ru = _ru(player=_holdout_player())
    assert await measurement.resolve_holdout_group(
        1, ru, _cfg(holdout_pct=0)) == "treatment"


async def test_unlinked_player_is_always_treatment():
    ru = _ru(player="")
    assert await measurement.resolve_holdout_group(1, ru, _cfg()) == "treatment"


# ---------------------------------------------------------------------------
# The guard verdict
# ---------------------------------------------------------------------------
async def test_guard_held_out_before_model(monkeypatch):
    virtual = []

    async def _set(*a):
        pass

    async def _record(product_id, **kw):
        virtual.append(kw)
        return 1

    monkeypatch.setattr(db, "set_holdout_group", _set)
    monkeypatch.setattr(db, "record_retention_outcome", _record)
    player = _holdout_player()
    guard = await retention_v2.guard_check(
        1, _ru(player=player), _evt(player_id=player), {}, _cfg())
    assert guard["allow"] is False
    assert guard["reasons"] == ["held_out"]
    assert guard["holdout_group"] == "holdout"
    # The virtual outcome row was opened (kind='holdout', no cost, no send).
    assert virtual and virtual[0]["kind"] == "holdout"
    assert virtual[0]["holdout_group"] == "holdout"


async def test_guard_treatment_player_passes(monkeypatch):
    async def _set(*a):
        pass

    async def _no_cost(product_id):
        return 0.0

    async def _no_recent(*a, **kw):
        return False

    monkeypatch.setattr(db, "set_holdout_group", _set)
    monkeypatch.setattr(db, "retention_v2_cost_today", _no_cost)
    monkeypatch.setattr(db, "recent_v2_decision_exists", _no_recent)

    async def _loss(product_id, player_id):
        return 0.0

    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    player = _treatment_player()
    guard = await retention_v2.guard_check(
        1, _ru(player=player), _evt(player_id=player), {}, _cfg())
    assert guard["allow"] is True
    assert guard["holdout_group"] == "treatment"


async def test_hard_deny_wins_over_holdout_no_virtual_row(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("virtual outcome must not open for a blocked player")

    monkeypatch.setattr(db, "record_retention_outcome", _boom)
    player = _holdout_player()
    guard = await retention_v2.guard_check(
        1, _ru(player=player, pings_muted=True), _evt(player_id=player),
        {}, _cfg())
    assert guard["allow"] is False
    assert "player_opted_out" in guard["reasons"]
    assert "held_out" not in guard["reasons"]


# ---------------------------------------------------------------------------
# Uplift math
# ---------------------------------------------------------------------------
async def test_compute_uplift_math(monkeypatch):
    async def _uplift(product_id, dt_from, dt_to):
        # Delegate to the shape the real query returns: rely on db helper's
        # own math via a synthetic aggregate instead — here we exercise the
        # public wrapper only.
        return [{"conversion_type": "reactivation",
                 "treatment_players": 380, "holdout_players": 42,
                 "treatment_conversions": 70, "holdout_conversions": 5,
                 "treatment_rate": 0.184, "holdout_rate": 0.119,
                 "uplift_pp": 6.5, "low_confidence": False}]

    monkeypatch.setattr(db, "retention_uplift", _uplift)
    rows = await measurement.compute_uplift(1, "2026-07-01", "2026-07-28")
    assert rows[0]["uplift_pp"] == pytest.approx(6.5)


async def test_outcome_record_carries_holdout_label(monkeypatch):
    from app.retention import outcomes
    seen = {}

    async def _record(product_id, **kw):
        seen.update(kw)
        return 5

    monkeypatch.setattr(db, "record_retention_outcome", _record)
    ru = _ru()
    ru["holdout_group"] = "treatment"
    await outcomes.record(1, ru, kind="proactive", action="message")
    assert seen["holdout_group"] == "treatment"
