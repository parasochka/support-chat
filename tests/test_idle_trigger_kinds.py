"""Idle ladder: a rule may watch several inactivity dimensions at once.

The default for a new/seeded rule is ALL THREE kinds (chat / casino /
deposit — owner decision); a rule fires when ANY of its kinds crosses the
threshold, the most-idle kind wins and names the reason.
"""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import retention_idle


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _ru(**over):
    base = {
        "id": 10, "product_id": 1, "tg_user_id": 555, "player_id": "p1",
        "vip_level": "", "subscribed": True, "pings_muted": False,
        "unreachable": False, "last_ping_at": None, "pings_day": None,
        "pings_sent_today": 0,
        "last_active_at": _iso_days_ago(1),
        "last_login_at": _iso_days_ago(1), "last_played_at": _iso_days_ago(1),
        "last_deposit_at": _iso_days_ago(1),
    }
    base.update(over)
    return base


def _rule(**over):
    base = {"id": 1, "name": "r", "trigger_kind": "bot_inactivity",
            "trigger_kinds": ["bot_inactivity", "casino_inactivity",
                              "no_deposit"],
            "inactivity_days": 7, "action": "message", "intent": "",
            "vip_tiers": [], "cooldown_days": 14, "priority": 10}
    base.update(over)
    return base


def test_rule_kinds_defaults_to_all_three():
    assert retention_idle.rule_kinds({"trigger_kinds": None,
                                      "trigger_kind": None}) == list(
        db.RULE_DEFAULT_TRIGGER_KINDS)
    # Legacy single-kind row falls back to its trigger_kind.
    assert retention_idle.rule_kinds(
        {"trigger_kinds": None, "trigger_kind": "no_deposit"}) == ["no_deposit"]
    # Explicit list wins.
    assert retention_idle.rule_kinds(
        {"trigger_kinds": ["casino_inactivity"],
         "trigger_kind": "bot_inactivity"}) == ["casino_inactivity"]


async def test_multi_kind_rule_fires_on_any_dimension(monkeypatch):
    async def _no_fired(rid, since, trigger_kind=None):
        return {}

    async def _no_cooldown(rid, rule_id, days):
        return False

    monkeypatch.setattr(db, "idle_rule_thresholds_fired_since", _no_fired)
    monkeypatch.setattr(db, "ping_rule_recently_fired", _no_cooldown)

    # Only the deposit dimension is idle past the threshold.
    ru = _ru(last_deposit_at=_iso_days_ago(9))
    matched = await retention_idle._match_rule(ru, [_rule()])
    assert matched is not None
    rule, idle_days, kind = matched
    assert kind == "no_deposit" and idle_days == 9

    # The MOST idle matching dimension wins when several cross the threshold.
    ru = _ru(last_deposit_at=_iso_days_ago(9),
             last_login_at=_iso_days_ago(12), last_played_at=_iso_days_ago(12))
    _, idle_days, kind = await retention_idle._match_rule(ru, [_rule()])
    assert kind == "casino_inactivity" and idle_days == 12


async def test_narrowed_rule_ignores_other_dimensions(monkeypatch):
    async def _no_fired(rid, since, trigger_kind=None):
        return {}

    async def _no_cooldown(rid, rule_id, days):
        return False

    monkeypatch.setattr(db, "idle_rule_thresholds_fired_since", _no_fired)
    monkeypatch.setattr(db, "ping_rule_recently_fired", _no_cooldown)

    # Deposit is long idle but the rule only watches chat inactivity.
    ru = _ru(last_deposit_at=_iso_days_ago(30))
    rule = _rule(trigger_kinds=["bot_inactivity"])
    assert await retention_idle._match_rule(ru, [rule]) is None


async def test_anti_cascade_applies_per_kind(monkeypatch):
    fired_calls = []

    async def _fired(rid, since, trigger_kind=None):
        fired_calls.append(trigger_kind)
        # A 14-day rung already fired on the deposit dimension.
        if trigger_kind == "no_deposit":
            return {"no_deposit": 14}
        return {}

    async def _no_cooldown(rid, rule_id, days):
        return False

    monkeypatch.setattr(db, "idle_rule_thresholds_fired_since", _fired)
    monkeypatch.setattr(db, "ping_rule_recently_fired", _no_cooldown)

    # deposit idle 20d matches the 7d rung, but a HIGHER deposit rung already
    # fired this stretch — the deposit dimension is suppressed; chat/casino are
    # not idle enough, so nothing fires.
    ru = _ru(last_deposit_at=_iso_days_ago(20))
    assert await retention_idle._match_rule(ru, [_rule()]) is None
    assert set(fired_calls) == set(db.RULE_DEFAULT_TRIGGER_KINDS)


def test_starter_ladder_watches_all_three_kinds():
    assert len(retention_idle.STARTER_IDLE_RULES) == 9
    for rule in retention_idle.STARTER_IDLE_RULES:
        assert rule["trigger_kinds"] == list(db.RULE_DEFAULT_TRIGGER_KINDS)
