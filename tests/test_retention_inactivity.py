"""The inactivity ladder (dormancy contour) + the Stage-1/2/3 additions.

Covers: the rg_hold guard (absolute, terminal), the ladder scan (step
selection, latest-step-wins supersede, once-per-cycle event ids, VIP-at-risk),
the lifecycle in the pipeline (cancelled_by_return at claim time, transient
deferral vs terminal consumption, follow-up scheduling), the loss-reaction
delay, the reply-adaptive backoff, per-player timezone resolution, the
rg_hold/self_excluded/timezone profile normalization, and the offers feed
(prompt block + [[LINK]] validation against offer deeplinks).
"""
from __future__ import annotations

import datetime as _dt

import db
import player_sync
import prompts
import retention
import retention_v2


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "ping_daily_cap": 5, "ping_min_gap_hours": 0,
        "quiet_hours_start": 0, "quiet_hours_end": 0,
        "quiet_hours_utc_offset": 0, "ping_batch_size": 30,
        "v2_enabled": True, "v2_dry_run": True,
        "v2_daily_budget_usd": 0.0, "v2_loss_comfort_hours": 24,
        "v2_loss_high_usd": 100.0, "v2_same_event_cooldown_hours": 0,
        "inactivity_enabled": True, "inactivity_steps": [7, 10, 14, 21, 30],
        "v2_loss_delay_min": 0, "v2_backoff_after_ignored": 0,
        "v2_follow_up_hours": 0, "v2_vip_at_risk_days": 45,
        "vip_tiers": ["none", "bronze", "silver", "gold", "platinum",
                      "diamond"],
    }
    base.update(over)
    return base


def _ru(**over):
    base = {
        "id": 10, "product_id": 1, "tg_user_id": 555, "player_id": "p1",
        "vip_level": "gold", "subscribed": True, "pings_muted": False,
        "unreachable": False, "rg_hold": False, "tz_name": None,
        "unanswered_touches": 0, "inact_cycle": 0, "inact_step_done": 0,
        "last_ping_at": None, "pings_day": None, "pings_sent_today": 0,
        "last_active_at": _iso_days_ago(8), "last_login_at": _iso_days_ago(8),
        "last_played_at": _iso_days_ago(8),
        "last_deposit_at": _iso_days_ago(9),
        "registration_date": _iso_days_ago(40), "session_id": None,
        "assigned_manager_id": None,
    }
    base.update(over)
    return base


def _patch_guard_env(monkeypatch, *, cost=0.0, recent=False, loss=None):
    async def _cost(product_id):
        return cost

    async def _recent(*a, **kw):
        return recent

    async def _last_loss(product_id, player_id):
        return loss

    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)
    monkeypatch.setattr(db, "recent_v2_decision_exists", _recent)
    monkeypatch.setattr(db, "last_loss_signal_at", _last_loss)


# ---------------------------------------------------------------------------
# rg_hold — the absolute guard
# ---------------------------------------------------------------------------
async def test_guard_rg_hold_blocks_everything(monkeypatch):
    _patch_guard_env(monkeypatch)
    monkeypatch.setattr(retention_v2, "_in_quiet_hours",
                        lambda cfg, ru=None: False)
    g = await retention_v2.guard_check(
        1, _ru(rg_hold=True), {"event_name": "deposit_confirmed"},
        {"net_loss_24h_usd": 0}, _cfg())
    assert not g["allow"] and "rg_hold" in g["reasons"]
    # rg_hold is TERMINAL, never a transient deferral reason.
    assert "rg_hold" not in retention_v2._TRANSIENT_GUARD_REASONS


def test_normalize_profile_rg_and_timezone():
    out = player_sync.normalize_profile(
        {"full_name": "X", "timezone": "Europe/Moscow", "self_excluded": True})
    assert out["tz_name"] == "Europe/Moscow"
    assert out["rg_hold"] is True and "self_excluded" not in out
    # rg_hold true wins over self_excluded false; both false clears the hold.
    assert player_sync.normalize_profile(
        {"rg_hold": "true", "self_excluded": False})["rg_hold"] is True
    assert player_sync.normalize_profile(
        {"rg_hold": False, "self_excluded": False})["rg_hold"] is False
    # No flags supplied -> no rg_hold key at all (partial update semantics).
    assert "rg_hold" not in player_sync.normalize_profile({"full_name": "X"})


# ---------------------------------------------------------------------------
# Per-player timezone
# ---------------------------------------------------------------------------
def test_player_tz_offset_resolution():
    cfg = {"quiet_hours_utc_offset": 3}
    # No profile timezone -> the product offset.
    assert retention.player_tz_offset(_ru(), cfg) == 3.0
    # Offset strings parse; junk falls back.
    assert retention.player_tz_offset(_ru(tz_name="UTC+5"), cfg) == 5.0
    assert retention.player_tz_offset(_ru(tz_name="-4"), cfg) == -4.0
    assert retention.player_tz_offset(_ru(tz_name="garbage!"), cfg) == 3.0
    # IANA names resolve through zoneinfo (UTC has offset 0).
    assert retention.player_tz_offset(_ru(tz_name="UTC"), cfg) == 0.0


# ---------------------------------------------------------------------------
# Ladder scan
# ---------------------------------------------------------------------------
def test_parse_inactivity_steps():
    assert retention_v2.parse_inactivity_steps(
        {"inactivity_steps": [10, 7, 7, 400, "x"]}) == [7, 10]
    assert retention_v2.parse_inactivity_steps(
        {"inactivity_steps": "7, 10; 14"}) == [7, 10, 14]
    assert retention_v2.parse_inactivity_steps({"inactivity_steps": []}) == []


async def _scan_env(monkeypatch, candidates, live=None):
    ingested, ledger, closed = [], [], []

    async def _cands(product_id, min_days, limit=500):
        return candidates

    async def _open_evt(product_id, player_id, name):
        return live if name == "inactivity_check" else None

    async def _close(pk):
        closed.append(pk)

    async def _ingest(product_id, **kw):
        ingested.append(kw)
        return 100 + len(ingested)

    async def _ledger(product_id, **kw):
        ledger.append(kw)

    async def _admin_evt(*a, **kw):
        pass

    monkeypatch.setattr(db, "inactivity_candidates", _cands)
    monkeypatch.setattr(db, "get_open_retention_event", _open_evt)
    monkeypatch.setattr(db, "close_retention_event", _close)
    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "insert_retention_v2_decision", _ledger)
    monkeypatch.setattr(db, "log_admin_event", _admin_evt)
    return ingested, ledger, closed


async def test_scan_creates_highest_crossed_step(monkeypatch):
    ru = _ru(last_seen=_iso_days_ago(12))  # crossed D7 and D10
    ingested, ledger, closed = await _scan_env(monkeypatch, [ru])
    res = await retention_v2.scan_inactivity({"id": 1}, _cfg(), force=True)
    assert res["created"] == 1 and not closed
    evt = ingested[0]
    assert evt["event_name"] == "inactivity_check"
    assert evt["payload"]["step"] == 10          # highest crossed, not both
    assert evt["event_id"] == "inact:p1:10:0"    # once per cycle by id


async def test_scan_skips_consumed_step_and_supersedes_lower(monkeypatch):
    # Step 10 already consumed -> idle 12 days creates nothing.
    ru = _ru(inact_step_done=10, last_seen=_iso_days_ago(12))
    ingested, ledger, closed = await _scan_env(monkeypatch, [ru])
    res = await retention_v2.scan_inactivity({"id": 1}, _cfg(), force=True)
    assert res["created"] == 0

    # A live D7 event + the player crossed D14 -> supersede + create D14.
    live = {"id": 55, "payload": {"step": 7}}
    ru = _ru(last_seen=_iso_days_ago(15))
    ingested, ledger, closed = await _scan_env(monkeypatch, [ru], live=live)
    res = await retention_v2.scan_inactivity({"id": 1}, _cfg(), force=True)
    assert closed == [55]
    assert ledger and ledger[0]["action"] == "superseded"
    assert ingested[0]["payload"]["step"] == 14


async def test_scan_vip_at_risk_for_top_tiers_only(monkeypatch):
    cfg = _cfg(inactivity_steps=[])  # isolate the VIP contour
    vip = _ru(vip_level="diamond", last_seen=_iso_days_ago(50))
    pleb = _ru(id=11, player_id="p2", vip_level="silver",
               last_seen=_iso_days_ago(50))
    ingested, _, _ = await _scan_env(monkeypatch, [vip, pleb])
    res = await retention_v2.scan_inactivity({"id": 1}, cfg, force=True)
    assert res["created"] == 1
    assert ingested[0]["event_name"] == "vip_at_risk"
    assert ingested[0]["event_id"] == "vipar:p1:0"


async def test_scan_disabled_or_throttled(monkeypatch):
    ingested, _, _ = await _scan_env(monkeypatch, [_ru()])
    res = await retention_v2.scan_inactivity(
        {"id": 1}, _cfg(inactivity_enabled=False), force=True)
    assert res == {"skipped": "inactivity_disabled"} and not ingested


# ---------------------------------------------------------------------------
# Pipeline lifecycle
# ---------------------------------------------------------------------------
def _inact_evt(step=7, **over):
    base = {"id": 77, "event_id": f"inact:p1:{step}:0",
            "event_name": "inactivity_check", "player_id": "p1",
            "ts": _iso_days_ago(0), "due_at": None,
            "payload": {"step": step, "cycle": 0}}
    base.update(over)
    return base


def _pipeline_env(monkeypatch, ru, *, decision=None):
    ledger, deferred, consumed, cycles, ingested = [], [], [], [], []

    async def _get_ru_by_player(product_id, player_id):
        return ru

    async def _loss(product_id, player_id):
        return 0.0

    async def _ledger_ins(product_id, **kw):
        ledger.append(kw)

    async def _defer(pk, due):
        deferred.append((pk, due))

    async def _consume(rid, step):
        consumed.append((rid, step))

    async def _cycle(product_id, player_id, force=False):
        cycles.append((player_id, force))

    async def _ingest(product_id, **kw):
        ingested.append(kw)
        return 200

    async def _admin_evt(*a, **kw):
        pass

    async def _decide(pid, ru_, evt, state, guard):
        return (decision or {"action": "silence", "tone": "neutral",
                             "intent": "", "reason": "test"}), 0.001

    monkeypatch.setattr(db, "get_retention_user_by_player", _get_ru_by_player)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(db, "insert_retention_v2_decision", _ledger_ins)
    monkeypatch.setattr(db, "defer_retention_event", _defer)
    monkeypatch.setattr(db, "consume_inactivity_step", _consume)
    monkeypatch.setattr(db, "bump_inactivity_cycle", _cycle)
    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "log_admin_event", _admin_evt)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_in_quiet_hours",
                        lambda cfg, ru=None: False)
    return ledger, deferred, consumed, cycles, ingested


async def test_inactivity_cancelled_by_return_at_claim(monkeypatch):
    # The player was active 1 day ago (< step 7) -> the claimed event dies.
    ru = _ru(last_login_at=_iso_days_ago(1), last_played_at=_iso_days_ago(1),
             last_active_at=_iso_days_ago(1))
    _patch_guard_env(monkeypatch)
    ledger, deferred, consumed, cycles, _ = _pipeline_env(monkeypatch, ru)
    out = await retention_v2._process_event({"id": 1}, _inact_evt(), _cfg())
    assert out == "cancelled"
    assert ledger[0]["action"] == "cancelled"
    assert "cancelled_by_return" in ledger[0]["reason"]
    assert cycles == [("p1", True)] and not consumed and not deferred


async def test_inactivity_transient_guard_defers_without_consuming(monkeypatch):
    ru = _ru(last_ping_at=_iso_days_ago(0.01))  # min-gap not elapsed
    _patch_guard_env(monkeypatch)
    ledger, deferred, consumed, _, _ = _pipeline_env(monkeypatch, ru)
    out = await retention_v2._process_event(
        {"id": 1}, _inact_evt(), _cfg(ping_min_gap_hours=2))
    assert out == "deferred"
    assert deferred and deferred[0][0] == 77
    assert ledger[0]["action"] == "deferred"
    assert not consumed  # the step survives for the retry


async def test_inactivity_terminal_guard_consumes_step(monkeypatch):
    ru = _ru(pings_muted=True)
    _patch_guard_env(monkeypatch)
    ledger, deferred, consumed, _, _ = _pipeline_env(monkeypatch, ru)
    out = await retention_v2._process_event({"id": 1}, _inact_evt(), _cfg())
    assert out == "blocked"
    assert consumed == [(10, 7)] and not deferred


async def test_inactivity_decision_consumes_step_and_schedules_followup(
        monkeypatch):
    ru = _ru()
    _patch_guard_env(monkeypatch)
    ledger, _, consumed, _, ingested = _pipeline_env(monkeypatch, ru)
    # Dry-run decision (silence) still consumes the step; no follow-up (not
    # delivered).
    out = await retention_v2._process_event(
        {"id": 1}, _inact_evt(), _cfg(v2_follow_up_hours=48))
    assert out == "silence"
    assert consumed == [(10, 7)] and not ingested

    # A LIVE delivered message schedules exactly one follow-up with due_at.
    consumed.clear()
    ledger.clear()

    async def _send(product, ru_, evt, decision, *, comfort, cfg, state=None):
        return True, 0.01, None
    monkeypatch.setattr(retention_v2, "_send_touch", _send)
    _pipeline_env_decision = {"action": "message", "tone": "warm",
                              "intent": "warm note", "reason": "test"}

    async def _decide(pid, ru_, evt, state, guard):
        return _pipeline_env_decision, 0.001
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    out = await retention_v2._process_event(
        {"id": 1}, _inact_evt(), _cfg(v2_dry_run=False, v2_follow_up_hours=48))
    assert out == "sent"
    assert ingested and ingested[0]["event_name"] == "touch_follow_up"
    assert ingested[0]["event_id"] == "fup:77"
    assert ingested[0]["due_at"] is not None


async def test_follow_up_cancelled_when_player_replied(monkeypatch):
    ru = _ru(unanswered_touches=0)  # a reply reset the counter
    _patch_guard_env(monkeypatch)
    ledger, _, _, cycles, _ = _pipeline_env(monkeypatch, ru)
    evt = {"id": 88, "event_id": "fup:77", "event_name": "touch_follow_up",
           "player_id": "p1", "ts": _iso_days_ago(0), "due_at": None,
           "payload": {"origin": "inactivity_check", "step": 7}}
    out = await retention_v2._process_event({"id": 1}, evt, _cfg())
    assert out == "cancelled"
    assert not cycles  # a follow-up cancel never bumps the ladder cycle


async def test_loss_delay_defers_first_pass(monkeypatch):
    ru = _ru()
    _patch_guard_env(monkeypatch)

    async def _loss(product_id, player_id):
        return 500.0  # over the high threshold -> decision-worthy
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    ledger, deferred, _, _, _ = _pipeline_env(monkeypatch, ru)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)  # re-patch over env

    evt = {"id": 99, "event_id": "e9", "event_name": "bet_settled",
           "player_id": "p1", "ts": _iso_days_ago(0), "due_at": None,
           "payload": {"amount": 500, "win_amount": 0}}
    out = await retention_v2._process_event(
        {"id": 1}, evt, _cfg(v2_loss_delay_min=45))
    assert out == "deferred"
    assert deferred and deferred[0][0] == 99
    # Second pass (due_at set) goes through to a decision.
    evt2 = dict(evt, due_at=_iso_days_ago(0))
    out2 = await retention_v2._process_event(
        {"id": 1}, evt2, _cfg(v2_loss_delay_min=45))
    assert out2 in ("silence", "blocked")  # reached guards/decision


# ---------------------------------------------------------------------------
# Reply-adaptive backoff
# ---------------------------------------------------------------------------
async def test_backoff_stretches_min_gap(monkeypatch):
    _patch_guard_env(monkeypatch)
    monkeypatch.setattr(retention_v2, "_in_quiet_hours",
                        lambda cfg, ru=None: False)
    cfg = _cfg(ping_min_gap_hours=2, v2_backoff_after_ignored=3)
    st = {"net_loss_24h_usd": 0}
    # 5 hours since the last touch: the base gap (2h) has passed…
    ru = _ru(last_ping_at=_iso_days_ago(5 / 24), unanswered_touches=3)
    g = await retention_v2.guard_check(1, ru, {"event_name": "level_up"},
                                       st, cfg)
    assert not g["allow"] and "ignored_backoff" in g["reasons"]
    # …a replying player (counter 0) sails through.
    ru = _ru(last_ping_at=_iso_days_ago(5 / 24), unanswered_touches=0)
    g = await retention_v2.guard_check(1, ru, {"event_name": "level_up"},
                                       st, cfg)
    assert g["allow"]


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------
def test_offers_directive_and_no_bonus_line():
    offers = [{"id": "o1", "title": "Weekend Reload",
               "description": "50% up to 200", "deeplink": "https://x.y/promo",
               "expires_at": "2026-07-12", "type": "reload"}]
    p = prompts.build_retention_ping_prompt(
        {"full_name": "T"}, "en", 7, "inactivity ladder step D7", "",
        offers=offers)
    assert "AVAILABLE OFFERS" in p and "Weekend Reload" in p
    assert "https://x.y/promo" in p
    # No offers + no_bonus_talk -> the hard bonus-free line, no offers block.
    p2 = prompts.build_retention_ping_prompt(
        {"full_name": "T"}, "en", 7, "inactivity ladder step D7", "",
        no_bonus_talk=True)
    assert "HARD RULE" in p2 and "AVAILABLE OFFERS" not in p2
    # Neither -> neither block.
    p3 = prompts.build_retention_ping_prompt(
        {"full_name": "T"}, "en", 0, "", "", occasion="a deposit")
    assert "HARD RULE" not in p3 and "AVAILABLE OFFERS" not in p3


def test_normalize_offer_rejects_junk():
    assert player_sync._normalize_offer({"title": ""}) is None
    o = player_sync._normalize_offer(
        {"title": "X", "deeplink": "javascript:alert(1)"})
    assert o["deeplink"] == ""  # non-http deeplink dropped, offer kept
