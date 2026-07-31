"""RG Guard Layer (DOC-3, MVP): the decision cascade, guard integration,
audit-always, and the dialogue suppression trigger."""
from __future__ import annotations

import datetime as _dt

from app.core import db
from app.retention import rg_guard
from app.retention import retention_v2


def _iso_in(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            + _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "ping_daily_cap": 5, "ping_min_gap_hours": 0,
        "quiet_hours_start": 0, "quiet_hours_end": 0,
        "quiet_hours_utc_offset": 0,
        "v2_enabled": True, "v2_dry_run": True,
        "v2_daily_budget_usd": 0.0, "v2_loss_comfort_hours": 0,
        "v2_loss_high_usd": 0.0, "v2_same_event_cooldown_hours": 0,
        "holdout_pct": 0,
        "rg_enabled": True, "rg_require_consent": False,
        "rg_unknown_status_policy": "warn", "rg_dialogue_suppression": True,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {
        "id": 10, "product_id": 1, "tg_user_id": 555, "player_id": "p1",
        "vip_level": "", "subscribed": True, "pings_muted": False,
        "unreachable": False, "last_ping_at": None, "pings_day": None,
        "pings_sent_today": 0, "rg_status": None, "rg_status_until": None,
        "marketing_consent": None, "rg_signals": None,
        "last_active_at": _iso_in(-1), "last_login_at": _iso_in(-1),
        "last_played_at": _iso_in(-1), "last_deposit_at": _iso_in(-3),
        "registration_date": _iso_in(-40), "session_id": None,
    }
    base.update(over)
    return base


def _evt(**over):
    base = {"id": 77, "event_id": "e", "event_name": "deposit_confirmed",
            "player_id": "p1", "ts": _iso_in(0), "payload": {}}
    base.update(over)
    return base


def _stub_audit(monkeypatch, sink=None):
    async def _audit(product_id, **kw):
        if sink is not None:
            sink.append(kw)
        return 1

    monkeypatch.setattr(db, "insert_rg_audit", _audit)


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------
async def test_self_exclude_is_permanent(monkeypatch):
    rg_guard.reset_signal_cache()
    v = await rg_guard.evaluate(1, _ru(rg_status="self_exclude"),
                                "event_reaction", _cfg())
    assert v["decision"] == rg_guard.BLOCK_PERMANENT
    assert v["reason"] == "permanent_self_exclude"
    assert v["allow_general"] is False


async def test_rg_hold_is_permanent(monkeypatch):
    rg_guard.reset_signal_cache()
    v = await rg_guard.evaluate(1, _ru(rg_status="rg_hold"),
                                "event_reaction", _cfg())
    assert v["decision"] == rg_guard.BLOCK_PERMANENT


async def test_cool_off_conditional_until_expiry(monkeypatch):
    rg_guard.reset_signal_cache()
    ru = _ru(rg_status="cool_off", rg_status_until=_iso_in(5))
    v = await rg_guard.evaluate(1, ru, "event_reaction", _cfg())
    assert v["decision"] == rg_guard.BLOCK_CONDITIONAL
    # Expired cool_off degrades to ok (the gate lazily persists the clear).
    ru = _ru(rg_status="cool_off", rg_status_until=_iso_in(-1))
    v = await rg_guard.evaluate(1, ru, "event_reaction", _cfg())
    assert v["decision"] == rg_guard.PASS


async def test_unknown_status_policy(monkeypatch):
    rg_guard.reset_signal_cache()
    v = await rg_guard.evaluate(1, _ru(), "event_reaction", _cfg())
    assert v["decision"] == rg_guard.PASS
    assert v["reason"] == "pass_status_unknown"
    v = await rg_guard.evaluate(
        1, _ru(), "event_reaction", _cfg(rg_unknown_status_policy="block"))
    assert v["decision"] == rg_guard.BLOCK_CONDITIONAL


async def test_consent_gate_off_by_default_on_mvp(monkeypatch):
    rg_guard.reset_signal_cache()
    # consent absent + gate off (MVP default) -> pass
    v = await rg_guard.evaluate(1, _ru(rg_status="ok"), "event_reaction",
                                _cfg())
    assert v["decision"] == rg_guard.PASS
    # gate on -> conditional
    v = await rg_guard.evaluate(1, _ru(rg_status="ok"), "event_reaction",
                                _cfg(rg_require_consent=True))
    assert v["decision"] == rg_guard.BLOCK_CONDITIONAL
    assert v["reason"] == "no_marketing_consent"


async def test_casino_flag_signal(monkeypatch):
    rg_guard.reset_signal_cache()

    async def _signals(product_id, only_enabled=False):
        return [{"signal_key": "escalating_bets", "enabled": True,
                 "block_class": "conditional", "params": {},
                 "source": "casino_flag"}]

    monkeypatch.setattr(db, "list_rg_signal_config", _signals)
    ru = _ru(rg_status="ok", rg_signals={"escalating_bets": True})
    v = await rg_guard.evaluate(1, ru, "event_reaction", _cfg())
    assert v["decision"] == rg_guard.BLOCK_CONDITIONAL
    assert "escalating_bets" in v["signals"]
    rg_guard.reset_signal_cache()


# ---------------------------------------------------------------------------
# The gate + guard integration
# ---------------------------------------------------------------------------
async def test_gate_audits_pass_too(monkeypatch):
    rg_guard.reset_signal_cache()
    audits = []
    _stub_audit(monkeypatch, audits)
    verdict = await rg_guard.gate(1, _ru(rg_status="ok"), "event_reaction",
                                  _cfg())
    assert verdict is None
    assert audits and audits[0]["decision"] == "pass"


async def test_gate_conditional_blocks_game_but_not_general(monkeypatch):
    rg_guard.reset_signal_cache()
    _stub_audit(monkeypatch)
    ru = _ru(rg_status="cool_off", rg_status_until=_iso_in(5))
    # Game trigger (an offer) -> deny.
    v = await rg_guard.gate(1, ru, "offer_grant", _cfg())
    assert v and v["deny"] is True
    # Idle ping is a come-back invite -> deny too.
    v = await rg_guard.gate(1, dict(ru), "idle_ping", _cfg())
    assert v and v["deny"] is True
    # General event reaction -> allowed with the no-play-talk constraint.
    v = await rg_guard.gate(1, dict(ru), "event_reaction", _cfg())
    assert v and v["deny"] is False and "no play" in v["constraint"].lower()


async def test_guard_check_rg_first_beats_holdout(monkeypatch):
    rg_guard.reset_signal_cache()
    _stub_audit(monkeypatch)

    async def _boom(*a, **kw):
        raise AssertionError("holdout must not be consulted for an RG block")

    monkeypatch.setattr(db, "record_retention_outcome", _boom)
    guard = await retention_v2.guard_check(
        1, _ru(rg_status="self_exclude"), _evt(), {},
        _cfg(holdout_pct=50))
    assert guard["allow"] is False
    assert guard["reasons"] == ["rg_permanent_self_exclude"]


async def test_guard_check_conditional_strips_photo_and_adds_constraint(
        monkeypatch):
    rg_guard.reset_signal_cache()
    _stub_audit(monkeypatch)

    async def _no_cost(product_id):
        return 0.0

    async def _no_recent(*a, **kw):
        return False

    async def _loss(product_id, player_id):
        return 0.0

    async def _set(*a):
        pass

    monkeypatch.setattr(db, "retention_v2_cost_today", _no_cost)
    monkeypatch.setattr(db, "recent_v2_decision_exists", _no_recent)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(db, "set_holdout_group", _set)
    ru = _ru(rg_status="cool_off", rg_status_until=_iso_in(5))
    guard = await retention_v2.guard_check(1, ru, _evt(), {}, _cfg())
    assert guard["allow"] is True
    assert "photo" not in guard["allowed_actions"]
    assert any("responsible-gaming" in c for c in guard["constraints"])


async def test_rg_disabled_no_audit(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("no audit when RG is disabled")

    monkeypatch.setattr(db, "insert_rg_audit", _boom)
    assert await rg_guard.gate(1, _ru(rg_status="self_exclude"),
                               "offer_grant", _cfg(rg_enabled=False)) is None


# ---------------------------------------------------------------------------
# Dialogue suppression
# ---------------------------------------------------------------------------
def test_dialogue_suppression_due():
    cfg = _cfg()
    assert rg_guard.dialogue_suppression_due(_ru(rg_status="self_exclude"), cfg)
    assert rg_guard.dialogue_suppression_due(_ru(rg_status="cool_off"), cfg)
    assert not rg_guard.dialogue_suppression_due(_ru(rg_status="ok"), cfg)
    assert not rg_guard.dialogue_suppression_due(_ru(), cfg)
    assert not rg_guard.dialogue_suppression_due(
        _ru(rg_status="self_exclude"), _cfg(rg_dialogue_suppression=False))


def test_rg_suppress_injects_directive_and_drops_nudge():
    from app.ai import prompts
    body = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hi",
        play_nudge=True, rg_suppress=True)
    assert "RG PROTECTION" in body
    assert "Never invite them to play" in body
