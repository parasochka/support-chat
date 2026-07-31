"""Adaptive frequency + Smart Send Time (DOC-4)."""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import frequency


def _cfg(**over):
    base = {
        "adaptive_frequency_enabled": True,
        "smart_send_time_enabled": True,
        "sst_max_shift_hours": 2, "sst_min_sample": 20,
        "quiet_hours_start": 22, "quiet_hours_end": 9,
        "quiet_hours_utc_offset": 0,
        "ping_daily_cap": 3, "ping_min_gap_hours": 2,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {"id": 10, "player_id": "p1", "vip_segment": None, "tz": None}
    base.update(over)
    return base


def _counts(monkeypatch, hour=0, day=0, week=0):
    async def _tc(product_id, rid, channel):
        return {"hour": hour, "day": day, "week": week}

    monkeypatch.setattr(db, "touch_counts", _tc)


def _no_stored(monkeypatch):
    async def _caps(product_id, only_enabled=False):
        return []

    async def _prio(product_id):
        return []

    monkeypatch.setattr(db, "list_frequency_caps", _caps)
    monkeypatch.setattr(db, "list_touch_priorities", _prio)
    frequency.reset_caches()


# ---------------------------------------------------------------------------
# Adaptive frequency gate
# ---------------------------------------------------------------------------
async def test_gate_off_returns_none():
    assert await frequency.gate(
        1, _ru(), "event_reaction",
        _cfg(adaptive_frequency_enabled=False)) is None


async def test_p2_never_cut_by_cap(monkeypatch):
    _no_stored(monkeypatch)
    _counts(monkeypatch, hour=5, day=50, week=100)
    v = await frequency.gate(1, _ru(), "loss_rescue", _cfg())
    assert v["allow"] is True and v["priority"] == 2


async def test_p3_cut_by_daily_cap(monkeypatch):
    _no_stored(monkeypatch)
    _counts(monkeypatch, day=3)
    v = await frequency.gate(1, _ru(), "event_reaction", _cfg())
    assert v["allow"] is False and v["reason"] == "freq_cap_priority_skip"


async def test_burst_cap(monkeypatch):
    _no_stored(monkeypatch)
    _counts(monkeypatch, hour=1)
    v = await frequency.gate(1, _ru(), "idle_ping", _cfg())
    assert v["allow"] is False and v["reason"] == "burst_cap"


async def test_vip_cohort_relaxed(monkeypatch):
    _no_stored(monkeypatch)
    _counts(monkeypatch, day=4)  # over mass cap 3, under vip cap 5
    v = await frequency.gate(1, _ru(vip_segment="vip"), "event_reaction",
                             _cfg())
    assert v["allow"] is True and v["cohort"] == "vip"
    v = await frequency.gate(1, _ru(), "event_reaction", _cfg())
    assert v["allow"] is False


async def test_channel_switch_marker(monkeypatch):
    async def _caps(product_id, only_enabled=False):
        return []

    async def _prio(product_id):
        return [{"touch_type": "daily_reminder", "priority": 3,
                 "channel_switch_on_cap": True}]

    monkeypatch.setattr(db, "list_frequency_caps", _caps)
    monkeypatch.setattr(db, "list_touch_priorities", _prio)
    frequency.reset_caches()
    _counts(monkeypatch, day=3)
    v = await frequency.gate(1, _ru(), "daily_reminder", _cfg())
    assert v["allow"] is False and v["would_switch_channel"] is True
    frequency.reset_caches()


async def test_email_rides_its_own_cap_row(monkeypatch):
    _no_stored(monkeypatch)
    # 3 telegram touches today would cap telegram, but the email row (cap 1)
    # is counted separately — 0 email touches -> email passes.
    _counts(monkeypatch, day=0)
    v = await frequency.gate(1, _ru(), "event_reaction", _cfg(),
                             channel="email")
    assert v["allow"] is True
    caps = await frequency.resolve_caps(1, "email", "mass")
    assert caps["per_day"] == 1


# ---------------------------------------------------------------------------
# Smart Send Time
# ---------------------------------------------------------------------------
def test_resolve_send_hour_modes():
    cfg = _cfg()
    prof = {"median_active_hour": 20, "sample_size": 100}
    # intelligent: clamp to hint +- 2h
    assert frequency.resolve_send_hour(prof, 20, cfg) == 20
    prof14 = {"median_active_hour": 14, "sample_size": 100}
    assert frequency.resolve_send_hour(prof14, 20, cfg) == 18
    # optimal: the median wins outright
    assert frequency.resolve_send_hour(prof14, 20, cfg, mode="optimal") == 14
    # fixed: the hint wins
    assert frequency.resolve_send_hour(prof14, 20, cfg, mode="fixed") == 20
    # thin profile -> the hint (a newcomer is never blocked by no data)
    thin = {"median_active_hour": 14, "sample_size": 3}
    assert frequency.resolve_send_hour(thin, 20, cfg) == 20


def test_send_hour_respects_quiet_hours():
    cfg = _cfg()  # quiet 22..9
    assert frequency.clamp_outside_quiet_hours(23, cfg) == 9
    assert frequency.clamp_outside_quiet_hours(3, cfg) == 9
    assert frequency.clamp_outside_quiet_hours(15, cfg) == 15


def test_player_tz_resolution():
    cfg = _cfg(quiet_hours_utc_offset=3)
    # No player tz -> the product audience offset.
    assert frequency.player_tz_offset_hours(_ru(), cfg) == 3
    # Fixed-offset form.
    assert frequency.player_tz_offset_hours(_ru(tz="+05:30"), cfg) == 5.5
    assert frequency.player_tz_offset_hours(_ru(tz="-04:00"), cfg) == -4
    # Garbage tz falls back.
    assert frequency.player_tz_offset_hours(_ru(tz="Mars/Olympus"), cfg) == 3


async def test_next_send_time_timing_critical_untouched():
    assert await frequency.next_send_time(
        1, _ru(), _cfg(), touch_type="loss_rescue") is None


async def test_next_send_time_shifts_to_active_hour(monkeypatch):
    async def _prof(product_id, player_id):
        return {"median_active_hour": 20, "sample_size": 100}

    monkeypatch.setattr(db, "get_activity_profile", _prof)
    now = _dt.datetime.now(_dt.timezone.utc)
    target = await frequency.next_send_time(
        1, _ru(), _cfg(quiet_hours_start=0, quiet_hours_end=0),
        touch_type="journey_step", hint_hour=20)
    if now.hour == 20:
        assert target is None  # already the right hour
    else:
        assert target is not None and target.hour in (18, 19, 20, 21, 22)
