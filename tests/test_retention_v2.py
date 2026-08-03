"""The retention agent (event-driven proactive loop) + player_sync.

Covers: canonical-event validation and the activity-timestamp bridge, the
deterministic state resolver, the guard rails (opt-out/caps/gap/quiet-hours/
budget/comfort window), the strict-JSON decision parser (guard verdict always
wins), the event pipeline (unknown player, log-only events, guard block,
dry-run never sends), the atomic event claim (no double-send), the enable
switch, the settings knobs, and the prompt builders.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from app.core import config
from app.core import db
from app.retention import player_sync
from app.ai import prompts
from app.retention import retention_v2
from app.core import settings
from app.core import tenancy
from app.i18n import translations


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "ping_daily_cap": 1, "ping_min_gap_hours": 48,
        "quiet_hours_start": 22, "quiet_hours_end": 9,
        "quiet_hours_utc_offset": 0, "ping_batch_size": 30,
        "v2_enabled": True, "v2_dry_run": True,
        "v2_daily_budget_usd": 5.0, "v2_loss_comfort_hours": 24,
        "v2_loss_high_usd": 100.0, "v2_same_event_cooldown_hours": 20,
        "v2_send_delay_min_sec": 300, "v2_send_delay_max_sec": 900,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {
        "id": 10, "product_id": 1, "tg_user_id": 555, "player_id": "p1",
        "vip_level": "gold", "subscribed": True, "pings_muted": False,
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


# ---------------------------------------------------------------------------
# player_sync: canonical-event validation
# ---------------------------------------------------------------------------
def test_validate_event_accepts_canonical_and_normalizes():
    v = player_sync._validate_event({
        "event_id": " e1 ", "event_name": "deposit_confirmed",
        "user_id": "p9", "timestamp": "2026-01-01T10:00:00Z",
        "payload": {"amount": 5}})
    assert v["event_id"] == "e1" and v["player_id"] == "p9"
    assert v["event_version"] == "1.0"


@pytest.mark.parametrize("bad", [
    {"event_name": "deposit_confirmed", "player_id": "p"},        # no event_id
    {"event_id": "e", "event_name": "made_up_event", "player_id": "p"},
    {"event_id": "e", "event_name": "deposit_confirmed"},         # no player
    {"event_id": "e", "event_name": "deposit_confirmed",
     "player_id": "p", "timestamp": "not-a-date"},
    {"event_id": "e", "event_name": "deposit_confirmed",
     "player_id": "p", "payload": ["not", "an", "object"]},
])
def test_validate_event_rejects(bad):
    with pytest.raises(player_sync.EventError):
        player_sync._validate_event(bad)


async def test_ingest_event_bridges_activity_and_profile(monkeypatch):
    stored, touches, profiles = {}, [], []

    async def _ingest(product_id, **kw):
        stored.update(kw, product_id=product_id)
        return 42

    async def _touch(product_id, player_id, field, ts, debounce_sec=0):
        touches.append((player_id, field, debounce_sec))
        return 1

    async def _profile(product_id, player_id, profile, profile_source=""):
        profiles.append((player_id, profile, profile_source))
        return 1

    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "touch_retention_activity", _touch)
    monkeypatch.setattr(db, "update_retention_profile", _profile)

    res = await player_sync.ingest_event(1, {
        "event_id": "e1", "event_name": "deposit_confirmed",
        "player_id": "p1", "payload": {"amount": 5, "vip_level": "Gold"}})
    assert res == {"stored": True, "duplicate": False, "id": 42,
                   "queued": True}
    # deposit_confirmed bumps last_deposit_at; the vip_level payload field
    # rides into the profile snapshot with source 'event'. The deposit mark is
    # NEVER debounced — the loss/recency maths keys on it directly.
    assert touches == [("p1", "last_deposit_at", 0)]
    assert profiles == [("p1", {"vip_level": "Gold"}, "event")]


async def test_ingest_event_duplicate_skips_bridge(monkeypatch):
    async def _ingest(product_id, **kw):
        return None  # ON CONFLICT DO NOTHING

    async def _boom(*a, **kw):
        raise AssertionError("bridge must not run for a duplicate")

    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "touch_retention_activity", _boom)
    monkeypatch.setattr(db, "update_retention_profile", _boom)
    res = await player_sync.ingest_event(1, {
        "event_id": "e1", "event_name": "session_started", "player_id": "p1"})
    assert res["duplicate"] is True and res["stored"] is False


async def test_ingest_events_batch_isolates_bad_rows(monkeypatch):
    async def _ingest(product_id, **kw):
        return 1

    async def _noop(*a, **kw):
        return 0

    monkeypatch.setattr(db, "ingest_retention_event", _ingest)
    monkeypatch.setattr(db, "touch_retention_activity", _noop)
    monkeypatch.setattr(db, "update_retention_profile", _noop)
    res = await player_sync.ingest_events(1, [
        {"event_id": "a", "event_name": "session_started", "player_id": "p"},
        {"event_id": "b", "event_name": "nonsense", "player_id": "p"},
        {"event_id": "c", "event_name": "bet_settled", "player_id": "p"},
    ])
    assert res["stored"] == 2
    assert len(res["errors"]) == 1 and res["errors"][0]["index"] == 1


# ---------------------------------------------------------------------------
# State resolver
# ---------------------------------------------------------------------------
async def test_resolve_player_state_dimensions(monkeypatch):
    async def _loss(product_id, player_id):
        return 0.0
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)

    st = await retention_v2.resolve_player_state(1, _ru(), _cfg())
    assert st["user_status"] == "active"
    assert st["risk_state"] == "low"
    assert st["lifecycle_stage"] == "d31_90_growth"

    st = await retention_v2.resolve_player_state(
        1, _ru(last_login_at=_iso_days_ago(9), last_played_at=None), _cfg())
    assert st["user_status"] == "at_risk" and st["risk_state"] == "high"

    st = await retention_v2.resolve_player_state(
        1, _ru(last_login_at=_iso_days_ago(20), last_played_at=None), _cfg())
    assert st["user_status"] == "dormant" and st["risk_state"] == "critical"

    st = await retention_v2.resolve_player_state(
        1, _ru(last_deposit_at=None), _cfg())
    assert st["user_status"] == "registered"


async def test_resolve_player_state_loss_marks_critical(monkeypatch):
    async def _loss(product_id, player_id):
        return 150.0
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    st = await retention_v2.resolve_player_state(1, _ru(), _cfg())
    assert st["risk_state"] == "critical"
    assert st["net_loss_24h_usd"] == 150.0


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
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
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: False)


async def test_guard_allows_clean_player_and_offers_photo(monkeypatch):
    _patch_guard_env(monkeypatch)
    st = {"net_loss_24h_usd": 0}
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, _cfg())
    assert g["allow"] is True and not g["comfort"]
    assert set(g["allowed_actions"]) == {"silence", "message", "photo"}


async def test_guard_denies_optouts_caps_and_gap(monkeypatch):
    _patch_guard_env(monkeypatch)
    st = {"net_loss_24h_usd": 0}
    cfg = _cfg()

    g = await retention_v2.guard_check(1, _ru(pings_muted=True), _evt(), st, cfg)
    assert not g["allow"] and "player_opted_out" in g["reasons"]

    g = await retention_v2.guard_check(1, _ru(subscribed=False), _evt(), st, cfg)
    assert "not_subscribed" in g["reasons"]

    g = await retention_v2.guard_check(
        1, _ru(last_ping_at=_iso_days_ago(0.5)), _evt(), st, cfg)
    assert "min_gap_not_elapsed" in g["reasons"]

    # UTC, not the machine's local date: guard_check compares against
    # datetime.now(timezone.utc).date(), so date.today() made this test pass or
    # fail depending on the runner's timezone (it FAILED outright under
    # TZ=Pacific/Kiritimati or Pacific/Midway).
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    g = await retention_v2.guard_check(
        1, _ru(pings_day=today, pings_sent_today=1), _evt(), st, cfg)
    assert "daily_cap_reached" in g["reasons"]


async def test_guard_budget_and_same_event_cooldown(monkeypatch):
    st = {"net_loss_24h_usd": 0}
    _patch_guard_env(monkeypatch, cost=5.0)
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, _cfg())
    assert "daily_budget_reached" in g["reasons"]

    _patch_guard_env(monkeypatch, recent=True)
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, _cfg())
    assert "same_event_cooldown" in g["reasons"]


async def test_guard_same_event_cooldown_knob(monkeypatch):
    """The cooldown window is the hot `v2_same_event_cooldown_hours` knob:
    0 disables it (repeat-testing mode) and a custom value reaches the DB
    check verbatim."""
    st = {"net_loss_24h_usd": 0}
    seen: dict = {}

    async def _recent(product_id, player_id, *, hours, event_name=None,
                      include_actions=None):
        seen["hours"] = hours
        seen["include"] = include_actions
        return True

    _patch_guard_env(monkeypatch, recent=True)
    monkeypatch.setattr(db, "recent_v2_decision_exists", _recent)

    # 0 = off: the DB is never asked, no cooldown reason.
    g = await retention_v2.guard_check(
        1, _ru(), _evt(), st, _cfg(v2_same_event_cooldown_hours=0))
    assert "same_event_cooldown" not in g["reasons"] and "hours" not in seen

    # A custom window is passed through; ordinary events count only real
    # reactions (a silence on one deposit must not mute the next deposit).
    g = await retention_v2.guard_check(
        1, _ru(), _evt(), st, _cfg(v2_same_event_cooldown_hours=4))
    assert "same_event_cooldown" in g["reasons"] and seen["hours"] == 4
    assert set(seen["include"]) == {"message", "photo"}

    # bet_settled counts SILENCE too: above the loss threshold every settled
    # bet is decision-worthy, so without the latch the agent re-ran a paid
    # decision call per bet of a losing streak.
    g = await retention_v2.guard_check(
        1, _ru(), _evt(event_name="bet_settled"), st,
        _cfg(v2_same_event_cooldown_hours=4))
    assert "same_event_cooldown" in g["reasons"]
    assert "silence" in seen["include"]

    # A cfg without the key (older override sets) falls back to the default.
    cfg = _cfg()
    del cfg["v2_same_event_cooldown_hours"]
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, cfg)
    assert seen["hours"] == config.RETENTION_V2_SAME_EVENT_COOLDOWN_HOURS


async def test_guard_has_no_quiet_hours_reason(monkeypatch):
    """Quiet hours are NOT a guard block anymore — the worker defers CLAIMING
    during the window (run_product_events), so a night-time deposit gets its
    reaction in the morning instead of being consumed as 'blocked' forever."""
    _patch_guard_env(monkeypatch)
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: True)
    st = {"net_loss_24h_usd": 0}
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, _cfg())
    assert g["allow"] is True
    assert "quiet_hours" not in g["reasons"]


async def test_guard_comfort_window_blocks_photo_and_constrains(monkeypatch):
    _patch_guard_env(monkeypatch)
    st = {"net_loss_24h_usd": 250.0}
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, _cfg())
    assert g["allow"] is True and g["comfort"] is True
    assert "photo" not in g["allowed_actions"]
    assert any("comfort" in c for c in g["constraints"])


# ---------------------------------------------------------------------------
# Decision parsing (the guard verdict always wins)
# ---------------------------------------------------------------------------
def test_parse_decision_valid_and_clamped():
    d = retention_v2.parse_decision(
        '{"action": "message", "tone": "celebrate", '
        '"intent": "congratulate the deposit", "reason": "positive moment"}',
        ["silence", "message", "photo"])
    assert d["action"] == "message" and d["tone"] == "celebrate"

    # Non-permitted action degrades to silence.
    d = retention_v2.parse_decision(
        '{"action": "photo", "tone": "warm"}', ["silence", "message"])
    assert d["action"] == "silence"

    # Unknown tone -> neutral; JSON inside prose still parses.
    d = retention_v2.parse_decision(
        'Sure! {"action": "message", "tone": "flirty", "intent": "x"}',
        ["silence", "message"])
    assert d["tone"] == "neutral" and d["action"] == "message"

    # Garbage -> silence.
    d = retention_v2.parse_decision("no json here", ["silence", "message"])
    assert d["action"] == "silence"


# ---------------------------------------------------------------------------
# The event pipeline
# ---------------------------------------------------------------------------
def _capture_ledger(monkeypatch):
    """Capture the decision ledger as ONE row per decision.

    The row is written in two steps now: reserved before anything the player
    can see (so the unique (product_id, event_pk) index can reject a replayed
    event in FRONT of the send) and completed afterwards with what actually
    happened. The fake folds the update back onto the reserved row, so a test
    still asserts on one final dict.
    """
    rows: list[dict] = []

    async def _insert(product_id, **kw):
        rows.append(dict(kw, product_id=product_id))
        return len(rows)

    async def _update(decision_id, **kw):
        row = rows[int(decision_id) - 1]
        row.update({k: v for k, v in kw.items() if v is not None})

    async def _admin_event(*a, **kw):
        return None

    monkeypatch.setattr(db, "insert_retention_v2_decision", _insert)
    monkeypatch.setattr(db, "update_retention_v2_decision", _update)
    monkeypatch.setattr(db, "log_admin_event", _admin_event)
    return rows


async def test_process_event_unlinked_player(monkeypatch):
    rows = _capture_ledger(monkeypatch)

    async def _none(product_id, player_id):
        return None
    monkeypatch.setattr(db, "get_retention_user_by_player", _none)

    out = await retention_v2._process_event({"id": 1}, _evt(), _cfg())
    assert out == "skipped"
    assert rows[0]["action"] == "skipped"
    assert "not linked" in rows[0]["reason"]


def test_validate_event_tg_target_normalizes_into_payload():
    # Top-level tg_user_id (the simulator / a partner pinning the recipient)
    # lands in the payload as an int — the append-only event row needs no
    # extra column and the pipeline reads it from there.
    v = player_sync._validate_event({
        "event_id": "e1", "event_name": "deposit_confirmed",
        "player_id": "p1", "tg_user_id": "555"})
    assert v["payload"]["tg_user_id"] == 555
    # payload-level works too, and no target means no payload key.
    v = player_sync._validate_event({
        "event_id": "e2", "event_name": "deposit_confirmed",
        "player_id": "p1", "payload": {"tg_user_id": 777, "amount": 5}})
    assert v["payload"]["tg_user_id"] == 777
    v = player_sync._validate_event({
        "event_id": "e3", "event_name": "deposit_confirmed",
        "player_id": "p1"})
    assert "tg_user_id" not in v["payload"]


@pytest.mark.parametrize("bad", ["abc", 0, -5, 1.5])
def test_validate_event_tg_target_rejects(bad):
    with pytest.raises(player_sync.EventError):
        player_sync._validate_event({
            "event_id": "e1", "event_name": "deposit_confirmed",
            "player_id": "p1", "tg_user_id": bad})


def test_validate_event_clamps_future_timestamp():
    """A future partner timestamp is clamped to now: the activity bridge is
    forward-only (GREATEST), so an un-clamped future ts would pin
    last_deposit_at ahead of reality FOREVER — the player would look
    permanently active and the idle ladder would never fire."""
    now = _dt.datetime.now(_dt.timezone.utc)
    v = player_sync._validate_event({
        "event_id": "e1", "event_name": "deposit_confirmed",
        "player_id": "p1",
        "timestamp": (now + _dt.timedelta(days=30)).isoformat()})
    assert v["ts"] <= _dt.datetime.now(_dt.timezone.utc)
    # A sane past timestamp passes through unchanged.
    past = now - _dt.timedelta(hours=3)
    v = player_sync._validate_event({
        "event_id": "e2", "event_name": "deposit_confirmed",
        "player_id": "p1", "timestamp": past.isoformat()})
    assert v["ts"] == past


async def test_process_event_explicit_tg_target_pins_recipient(monkeypatch):
    _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)
    looked = {}

    async def _by_tg(product_id, tg_user_id):
        looked["tg"] = tg_user_id
        return _ru(tg_user_id=tg_user_id)

    async def _by_player_boom(product_id, player_id):
        raise AssertionError("explicit target must not resolve by player_id")

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "silence", "tone": "neutral", "intent": "",
                 "reason": "quiet"}, 0.001)

    monkeypatch.setattr(db, "get_retention_user", _by_tg)
    monkeypatch.setattr(db, "get_retention_user_by_player", _by_player_boom)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(retention_v2, "_decide", _decide)

    out = await retention_v2._process_event(
        {"id": 1}, _evt(payload={"amount": 50, "tg_user_id": 777}), _cfg())
    assert looked == {"tg": 777}
    assert out == "silence"


async def test_process_event_unknown_tg_target_never_falls_back(monkeypatch):
    # An explicit target that is not linked must SKIP, not silently deliver
    # to another Telegram account of the same player (the exact confusion
    # explicit targeting exists to remove).
    rows = _capture_ledger(monkeypatch)

    async def _by_tg(product_id, tg_user_id):
        return None

    async def _by_player_boom(product_id, player_id):
        raise AssertionError("unknown explicit target must not fall back")

    monkeypatch.setattr(db, "get_retention_user", _by_tg)
    monkeypatch.setattr(db, "get_retention_user_by_player", _by_player_boom)

    out = await retention_v2._process_event(
        {"id": 1}, _evt(payload={"tg_user_id": 999}), _cfg())
    assert out == "skipped"
    assert "tg_user_id 999" in rows[0]["reason"]


async def test_process_event_log_only_is_silent(monkeypatch):
    rows = _capture_ledger(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0
    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)

    out = await retention_v2._process_event(
        {"id": 1}, _evt(event_name="session_started"), _cfg())
    assert out is None and rows == []
    # bet_settled below the loss threshold is log-only too.
    out = await retention_v2._process_event(
        {"id": 1}, _evt(event_name="bet_settled"), _cfg())
    assert out is None and rows == []


async def test_process_event_stale_event_is_state_food(monkeypatch):
    """A reaction only makes sense while the occasion is fresh: an event older
    than the freshness cap (a days-old backlog after the agent was off) is
    processed silently — no decision call, no ledger row — instead of a
    retroactive congratulation. The linked/unlinked branches both honour it."""
    rows = _capture_ledger(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0
    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)

    stale = _evt(ts=_iso_days_ago(2))  # deposit_confirmed, 2 days old
    out = await retention_v2._process_event({"id": 1}, stale, _cfg())
    assert out is None and rows == []

    async def _none(product_id, player_id):
        return None
    monkeypatch.setattr(db, "get_retention_user_by_player", _none)
    out = await retention_v2._process_event({"id": 1}, stale, _cfg())
    assert out is None and rows == []

    # A fresh event still reacts (the freshness cap boundary).
    assert retention_v2._fresh_enough(_evt())
    assert not retention_v2._fresh_enough(stale)


async def test_process_event_guard_block_is_ledgered(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru(pings_muted=True)

    async def _loss(product_id, player_id):
        return 0.0
    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)

    out = await retention_v2._process_event({"id": 1}, _evt(), _cfg())
    assert out == "blocked"
    assert rows[0]["action"] == "blocked"
    assert "player_opted_out" in rows[0]["reason"]


async def test_process_event_dry_run_decides_but_never_sends(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "message", "tone": "celebrate",
                 "intent": "thank them warmly", "reason": "fresh deposit"},
                0.002)

    async def _send_boom(*a, **kw):
        raise AssertionError("dry-run must never send")

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _send_boom)

    out = await retention_v2._process_event(
        {"id": 1}, _evt(), _cfg(v2_dry_run=True))
    assert out == "message"
    row = rows[0]
    assert row["action"] == "message" and row["dry_run"] is True
    assert row["delivered"] is False and row["intent"] == "thank them warmly"
    assert row["cost_usd"] == 0.002


async def test_process_event_live_sends_and_ledgers(monkeypatch):
    rows = _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)
    sent = {}

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "message", "tone": "warm", "intent": "hi",
                 "reason": "r"}, 0.001)

    async def _send(product, ru, evt, decision, *, comfort, cfg,
                    decision_id=None):
        sent["comfort"] = comfort
        return True, 0.003, None, {"session_id": "s-1"}

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _send)

    out = await retention_v2._process_event(
        {"id": 1}, _evt(), _cfg(v2_dry_run=False))
    assert out == "sent"
    assert sent == {"comfort": False}
    assert rows[0]["delivered"] is True
    assert rows[0]["cost_usd"] == pytest.approx(0.004)  # decision + generation


async def test_a_held_grant_strips_the_bonus_from_the_event_brief(monkeypatch):
    """The decision model wrote its intent UNDER the offer constraint ("the
    bonus is credited before your message goes out, so you may mention it
    warmly"). On `fraud_hold` nothing was credited — shipping that intent
    verbatim promises a gift that does not exist, and nothing else in the stack
    knows the grant fell through."""
    from app.retention import offers
    _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)
    sent = {}

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "grant_offer", "tone": "warm",
                 "intent": "tell them about the free spins just credited",
                 "reason": "r"}, 0.001)

    async def _send(product, ru, evt, decision, *, comfort, cfg,
                    decision_id=None):
        sent["intent"] = decision["intent"]
        return True, 0.003, None, {"session_id": "s-1"}

    async def _resolve(pid, ru, trigger_key, cfg, state):
        return {"offer_key": "fs50", "description": "50 free spins"}, None

    async def _grant(product, ru, offer, ref, cfg):
        return {"status": "fraud_hold", "offer_grant_id": "og_x"}

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(db, "link_offer_grant_decision", _noop)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _send)
    monkeypatch.setattr(offers, "classify_offer_trigger",
                        lambda evt, state, cfg: "reactivation")
    monkeypatch.setattr(offers, "offers_enabled", lambda cfg: True)
    monkeypatch.setattr(offers, "offer_constraint_line", lambda o: "gift line")
    monkeypatch.setattr(offers, "resolve_offer", _resolve)
    monkeypatch.setattr(offers, "do_offer_grant", _grant)

    await retention_v2._process_event({"id": 1}, _evt(),
                                      _cfg(v2_dry_run=False,
                                           offers_enabled=True))

    intent = sent["intent"].lower()
    assert "no bonus, gift, free spins or promotion was credited" in intent
    assert "do not mention" in intent


def test_occasion_for_safe_details_only():
    # Base occasion: never leaks payload amounts.
    o = retention_v2.occasion_for(_evt())  # payload carries amount=50
    assert "deposit" in o and "50" not in o
    # Whitelisted non-money detail is folded in so the persona can name the
    # concrete thing that happened.
    o = retention_v2.occasion_for(
        _evt(event_name="level_up", payload={"level": 7, "previous": 6}))
    assert "loyalty level" in o and "(level: 7)" in o
    o = retention_v2.occasion_for(
        _evt(event_name="deposit_failed",
             payload={"amount": 100, "reason": "card_declined"}))
    assert "card_declined" in o and "100" not in o
    # Unknown event degrades to the generic line.
    assert retention_v2.occasion_for(
        _evt(event_name="made_up")) == "a notable moment"


def _patch_send_env(monkeypatch, sent, persisted):
    from app.chat import chat_service
    from app.retention import delivery
    from app.retention import retention

    async def _token(pid):
        return "tok"

    async def _sess(pid, ru, lang):
        return {"id": "s1", "product_id": pid, "message_count": 0}

    async def _gen(session, **kw):
        return chat_service.PingDraft(
            text="Поздравляю с пополнением!", lang="ru", photo_id=None,
            ai_meta={"cost_usd": 0.01})

    class _TG:
        def __init__(self, token):
            pass

        async def send_message_verbose(self, chat_id, text, parse_mode=None,
                                       reply_markup=None, **kwargs):
            sent["text"] = text
            return {"message_id": 1}, None, None

    async def _persist(session_id, text, ai_meta=None, product_id=None,
                       ping_context=None, link_url=None):
        persisted.update(session_id=session_id, text=text,
                         ping_context=ping_context, link_url=link_url)
        return 1

    async def _record(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention, "_ensure_session", _sess)
    monkeypatch.setattr(retention, "_user_context_from_ru", lambda ru: {})
    monkeypatch.setattr(chat_service, "generate_retention_ping", _gen)
    # The send mechanics live in the delivery seam now — patch its transport.
    monkeypatch.setattr(delivery, "TelegramClient", _TG)
    monkeypatch.setattr(db, "persist_ping_turn", _persist)
    monkeypatch.setattr(db, "record_retention_ping", _record)


async def test_send_touch_header_carries_occasion_phrase(monkeypatch):
    sent, persisted = {}, {}
    _patch_send_env(monkeypatch, sent, persisted)
    decision = {"action": "message", "tone": "warm", "intent": "hi",
                "reason": "r"}

    ok, cost, detail, _facts = await retention_v2._send_touch(
        {"id": 1}, _ru(), _evt(), decision, comfort=False, cfg=_cfg())
    assert ok is True and detail is None
    # The occasion rides in the HEADER line as a human phrase (rtn_trig_*,
    # with the safe payload detail) — never the raw event name.
    assert "deposit_confirmed" not in sent["text"]
    assert "Спасибо за депозит 50" in sent["text"]  # ru copy + amount detail
    assert "Поздравляю с пополнением!" in sent["text"]
    # …and the trigger + occasion are persisted with the turn, so the prompt
    # history (and the admin transcript) can explain later WHY the bot wrote.
    assert persisted["ping_context"].startswith("deposit_confirmed:")
    assert "deposit" in persisted["ping_context"]


async def test_send_touch_comfort_has_no_occasion_phrase(monkeypatch):
    # A comfort touch (the player just lost money) must not carry a
    # congratulation-style occasion phrase — the persona header only.
    sent, persisted = {}, {}
    _patch_send_env(monkeypatch, sent, persisted)
    decision = {"action": "message", "tone": "comfort", "intent": "hi",
                "reason": "r"}
    ok, *_ = await retention_v2._send_touch(
        {"id": 1}, _ru(), _evt(event_name="bet_settled", payload={}),
        decision, comfort=True, cfg=_cfg())
    assert ok is True
    assert "bet_settled" not in sent["text"]
    assert "Спасибо" not in sent["text"]
    # The persisted context stays regardless — it feeds the prompt history.
    assert persisted["ping_context"].startswith("bet_settled:")


def test_trigger_phrase_details_and_punctuation():
    # Amount + currency fold into the phrase (chrome may name the amount)…
    p = retention_v2._trigger_phrase(
        _evt(payload={"amount": 50, "currency": "USD"}), "en")
    assert p == "Thank you for the deposit 50 USD!"
    # …and an empty detail leaves no dangling space before punctuation.
    p = retention_v2._trigger_phrase(_evt(payload={}), "en")
    assert p == "Thank you for the deposit!"
    # Unregistered events carry no phrase (header only) — bet_settled is the
    # one canonical event without a rtn_trig_* key by design (its touch is
    # the comfort path, which shows the bare header).
    assert retention_v2._trigger_phrase(
        _evt(event_name="bet_settled"), "en") == ""


def test_every_decision_event_has_phrase_and_occasion():
    # Only the decision events actually wake the agent and send a message, so
    # each must ship an admin-editable header phrase (rtn_trig_*) and a
    # model-facing occasion line — otherwise a reaction sends a bare header.
    # bet_settled is the one decision-worthy event without a phrase by design
    # (its touch is the comfort path, which shows the bare header).
    registered = {k for k, _scope, _d in translations.KEYS}
    for name in sorted(retention_v2.DECISION_EVENTS):
        assert f"rtn_trig_{name}" in registered, name
        assert retention_v2._trigger_phrase(
            _evt(event_name=name, payload={}), "en") != "", name
        assert name in retention_v2._OCCASIONS, name


def test_state_food_events_have_no_trigger_phrase():
    # The non-decision (state-food) canonical events never stimulate a message,
    # so their rtn_trig_* keys were removed. Each degrades to a bare header.
    registered = {k for k, _scope, _d in translations.KEYS}
    for name in sorted(player_sync.CANONICAL_EVENTS
                       - retention_v2.DECISION_EVENTS - {"bet_settled"}):
        assert f"rtn_trig_{name}" not in registered, name
        assert retention_v2._trigger_phrase(
            _evt(event_name=name, payload={}), "en") == "", name


def test_proactive_header_merges_on_one_line():
    header = retention_v2._proactive_header("ru", _evt(payload={"amount": 10}))
    assert "\n" not in header
    assert header.startswith("✨")
    assert "Спасибо за депозит 10" in header
    # Comfort: header only, no occasion phrase.
    comfort = retention_v2._proactive_header("ru", _evt(), comfort=True)
    assert "Спасибо" not in comfort and "\n" not in comfort


# ---------------------------------------------------------------------------
# The enable switch + the atomic event claim
# ---------------------------------------------------------------------------
async def test_sweep_skips_disabled_products(monkeypatch):
    monkeypatch.setattr(settings, "retention", lambda: _cfg(v2_enabled=False))
    monkeypatch.setattr(tenancy, "set_current_product", lambda pid: None)

    async def _claim_boom(*a, **kw):
        raise AssertionError("a disabled agent must not claim events")
    monkeypatch.setattr(db, "claim_retention_events", _claim_boom)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"agent": "disabled"}


async def test_idle_sweep_runs_even_with_agent_disabled(monkeypatch):
    """`idle_pings_enabled` is its OWN switch: turning the event agent off
    (v2_enabled=False) must not silently kill the idle ladder — the admin
    sees two independent toggles.

    The ladder moved OFF the event path (it is a maintenance sweep now, so a
    slow re-engagement pass cannot delay a deposit reaction), which is why this
    exercises run_product_maintenance rather than run_product_events."""
    from app.retention import retention_idle
    called = {}
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(v2_enabled=False, idle_pings_enabled=True))
    monkeypatch.setattr(tenancy, "set_current_product", lambda pid: None)

    async def _idle(product, cfg, **kw):
        called["ran"] = True
        return {"sent": 2, "failed": 1}

    async def _lag(pid, **_kw):
        return {2: 0, 3: 0, 5: 0}

    async def _skip(*a, **kw):
        return {"skipped": "paced"}

    async def _finish(*a, **kw):
        return None

    monkeypatch.setattr(retention_idle, "run_product_idle_pings", _idle)
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lag)
    # The ladder now closes its own retention_worker_jobs row (it claims the
    # slot itself, so nothing else can).
    monkeypatch.setattr(db, "finish_worker_job", _finish)
    monkeypatch.setattr(retention_v2, "_run_maintenance_job", _skip)
    # send_worker_enabled=True: the legacy inline delivery-retry drain is the
    # send worker's job then, so this pass does not reach for channels.
    monkeypatch.setattr(retention_v2.settings, "global_retention_bool",
                        lambda key, default: True)
    stats = await retention_v2.run_product_maintenance({"id": 1})
    assert called == {"ran": True}
    assert stats["idle_sent"] == 2 and stats["idle_failed"] == 1


async def test_maintenance_pauses_idle_when_the_queue_is_far_behind(monkeypatch):
    """A backlog means live players are waiting; re-engaging quiet ones can
    wait its turn. Past `queue_degrade_idle_sec` the ladder stands down."""
    from app.retention import retention_idle
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(idle_pings_enabled=True,
                                     queue_degrade_idle_sec=600))
    monkeypatch.setattr(tenancy, "set_current_product", lambda pid: None)

    async def _idle_boom(*a, **kw):
        raise AssertionError("the ladder must stand down behind a backlog")

    async def _lag(pid, **_kw):
        # Lane 3 is what the idle pause reads: the lanes the drain still
        # serves. A lane-5 backlog the ladder has deliberately shed must not
        # pause re-engagement (that is the same latching bug, one level up).
        return {2: 900, 3: 900, 5: 9_000}

    async def _skip(*a, **kw):
        return {"skipped": "paced"}

    monkeypatch.setattr(retention_idle, "run_product_idle_pings", _idle_boom)
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lag)
    monkeypatch.setattr(retention_v2, "_run_maintenance_job", _skip)
    # send_worker_enabled=True: the legacy inline delivery-retry drain is the
    # send worker's job then, so this pass does not reach for channels.
    monkeypatch.setattr(retention_v2.settings, "global_retention_bool",
                        lambda key, default: True)
    stats = await retention_v2.run_product_maintenance({"id": 1})
    assert stats["idle_paused"] == 1


async def test_sweep_defers_during_quiet_hours(monkeypatch):
    """Quiet hours defer the event pipeline (events stay queued, reacted to in
    the morning) instead of processing + guard-blocking them (which consumed
    the event forever — and in a casino the night is peak deposit time). The
    admin «Process queue now» button claims regardless."""
    monkeypatch.setattr(settings, "retention", lambda: _cfg(v2_enabled=True))
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: True)

    async def _claim_boom(*a, **kw):
        raise AssertionError("must not claim during quiet hours")

    async def _cost(pid):
        return 0.0

    async def _lag(pid, **_kw):
        return {2: 0, 3: 0, 5: 0}
    monkeypatch.setattr(db, "claim_retention_events", _claim_boom)
    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lag)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"agent": "quiet_hours_deferred"}

    claimed = []

    async def _claim(pid, **kw):
        claimed.append((pid, kw))
        return []
    monkeypatch.setattr(db, "claim_retention_events", _claim)
    await retention_v2.run_product_events({"id": 1}, ignore_send_delay=True)
    assert [c[0] for c in claimed] == [1]


async def test_sweep_claims_events_atomically(monkeypatch):
    """The drain must use db.claim_retention_events (an atomic LEASE) — a
    plain SELECT let the worker sweep and the admin «Process queue now» button
    pick up the SAME event concurrently and each send a message (the duplicate
    deposit thank-you bug). The lease also carries the worker id and the lane
    ceiling the backpressure ladder computed."""
    claimed = []

    async def _claim(pid, **kw):
        claimed.append((pid, kw))
        return []

    async def _cost(pid):
        return 0.0

    async def _lag(pid, **_kw):
        return {2: 0, 3: 0, 5: 0}
    monkeypatch.setattr(settings, "retention", lambda: _cfg(v2_enabled=True))
    # Neutralize the wall clock: _cfg's 22–9 window would make this test
    # night-flaky now that the sweep defers during quiet hours.
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: False)
    monkeypatch.setattr(db, "claim_retention_events", _claim)
    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lag)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"events": 0, "lag_sec": 0}
    pid, kw = claimed[0]
    # ping_batch_size + the humanizing send delay from _cfg (5–15 min).
    assert pid == 1 and kw["limit"] == 30
    assert (kw["delay_min_sec"], kw["delay_max_sec"]) == (300, 900)
    # No backlog -> every lane is claimable.
    assert kw["max_priority"] == 5 and kw["worker_id"]
    # The admin «Process queue now» path bypasses the delay.
    claimed.clear()
    await retention_v2.run_product_events({"id": 1}, ignore_send_delay=True)
    assert (claimed[0][1]["delay_min_sec"],
            claimed[0][1]["delay_max_sec"]) == (0, 0)


async def test_backlog_sheds_the_low_lanes(monkeypatch):
    """Backpressure: as the queue lag grows the drain stops claiming state
    food, then the default lane, so transactional events keep moving."""
    claimed = []

    async def _claim(pid, **kw):
        claimed.append(kw["max_priority"])
        return []

    async def _cost(pid):
        return 0.0

    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(v2_enabled=True,
                                     queue_degrade_p3_sec=300,
                                     queue_degrade_p2_sec=900))
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: False)
    monkeypatch.setattr(db, "claim_retention_events", _claim)
    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(db, "log_admin_event_sampled", _noop)

    # A uniform lag = every lane equally behind, which is the classic ladder.
    for lag, expected in ((0, 5), (400, 3), (1200, 2)):
        async def _lag(pid, _l=lag, **_kw):
            return {2: _l, 3: _l, 5: _l}
        monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lag)
        await retention_v2.run_product_events({"id": 1})
    assert claimed == [5, 3, 2]


async def test_a_shed_lane_cannot_argue_for_its_own_shedding(monkeypatch):
    """The rung that sheds a lane must NOT read that lane's own lag.

    Keyed on the whole queue, shedding was a one-way door: the lanes the drain
    stopped claiming stayed pending, so they stayed the oldest rows and held
    the ladder down until the pruner deleted them — the loss-comfort path and
    every lane-3 event silently dead, forever.
    """
    claimed = []

    async def _claim(pid, **kw):
        claimed.append(kw["max_priority"])
        return []

    async def _cost(pid):
        return 0.0

    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(v2_enabled=True,
                                     queue_degrade_p3_sec=300,
                                     queue_degrade_p2_sec=900))
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: False)
    monkeypatch.setattr(db, "claim_retention_events", _claim)
    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)
    monkeypatch.setattr(db, "log_admin_event_sampled", _noop)

    # A mountain of state food, everything else current: keep serving it.
    async def _food(pid, **_kw):
        return {2: 0, 3: 0, 5: 99_999}
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _food)
    await retention_v2.run_product_events({"id": 1})

    # Lane 3 behind (lanes 1-2 current): shed lane 5, keep lane 3.
    async def _lane3(pid, **_kw):
        return {2: 0, 3: 400, 5: 99_999}
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lane3)
    await retention_v2.run_product_events({"id": 1})

    # The transactional lanes themselves behind: shed everything below them.
    async def _lane2(pid, **_kw):
        return {2: 1_200, 3: 1_200, 5: 99_999}
    monkeypatch.setattr(db, "retention_queue_lag_by_lane", _lane2)
    await retention_v2.run_product_events({"id": 1})

    assert claimed == [5, 3, 2]


async def test_daily_budget_stops_the_event_path(monkeypatch):
    """The budget used to gate only the idle ladder, so an event storm could
    spend past the cap all day."""
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(v2_enabled=True, v2_daily_budget_usd=1.0))
    monkeypatch.setattr(retention_v2, "_in_quiet_hours", lambda cfg: False)

    async def _claim_boom(*a, **kw):
        raise AssertionError("must not claim past the daily budget")

    async def _cost(pid):
        return 1.5

    async def _finish(*a, **kw):
        return None
    monkeypatch.setattr(db, "claim_retention_events", _claim_boom)
    monkeypatch.setattr(db, "retention_v2_cost_today", _cost)
    monkeypatch.setattr(db, "finish_worker_job", _finish)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"agent": "daily_budget_reached"}


def test_worker_interval_is_hot_and_clamped(monkeypatch):
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(worker_interval_sec=1))
    assert retention_v2.worker_interval_sec() == 5   # clamped low
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(worker_interval_sec=42))
    assert retention_v2.worker_interval_sec() == 42
    monkeypatch.setattr(settings, "retention",
                        lambda: _cfg(worker_interval_sec=999999))
    assert retention_v2.worker_interval_sec() == 3600  # clamped high


# ---------------------------------------------------------------------------
# Settings knobs
# ---------------------------------------------------------------------------
def test_retention_v2_settings_validation():
    ok = {"v2_enabled": True, "v2_dry_run": False,
          "v2_daily_budget_usd": 2.5, "v2_loss_comfort_hours": 12,
          "v2_loss_high_usd": 200.0, "v2_same_event_cooldown_hours": 0,
          "worker_interval_sec": 5, "idle_pings_enabled": True,
          "v2_send_delay_min_sec": 300, "v2_send_delay_max_sec": 900,
          "v2_decision_events": ["deposit_confirmed", "level_up"],
          "max_reply_parts": 2, "idle_sweep_interval_sec": 300}
    assert settings.validate_setting("retention", ok) == ok
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"max_reply_parts": 0})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"max_reply_parts": 6})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"idle_sweep_interval_sec": 10})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"worker_interval_sec": 2})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_enabled": "yes"})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_daily_budget_usd": -1})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_loss_comfort_hours": 100000})
    with pytest.raises(ValueError):
        settings.validate_setting("retention",
                                  {"v2_same_event_cooldown_hours": -1})
    # Send delay: max must not undercut min; both bounded.
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_send_delay_min_sec": 600,
                                                "v2_send_delay_max_sec": 300})
    # Decision events: only canonical names, bet_settled stays special-cased.
    with pytest.raises(ValueError):
        settings.validate_setting("retention",
                                  {"v2_decision_events": ["made_up_event"]})
    with pytest.raises(ValueError):
        settings.validate_setting("retention",
                                  {"v2_decision_events": ["bet_settled"]})


def test_retention_settings_resolve_v2_defaults():
    cfg = settings.retention()
    assert cfg["v2_enabled"] is True        # the agent is the one regime
    assert cfg["v2_dry_run"] is True        # shadow mode by default
    assert "v2_show_trigger" not in cfg     # replaced by rtn_trig_* phrases
    assert cfg["v2_daily_budget_usd"] > 0
    assert cfg["v2_same_event_cooldown_hours"] == 5
    assert cfg["worker_interval_sec"] == 5  # near-realtime by default
    assert cfg["idle_pings_enabled"] is True
    assert cfg["v2_send_delay_min_sec"] == 300   # humanizing send delay 5–15m
    assert cfg["v2_send_delay_max_sec"] == 900
    assert cfg["v2_decision_events"] is None     # built-in set by default
    assert "pings_enabled" not in cfg       # the v1 ping matrix is gone


def test_effective_decision_events_override():
    assert retention_v2.effective_decision_events({}) \
        == retention_v2.DECISION_EVENTS
    eff = retention_v2.effective_decision_events(
        {"v2_decision_events": ["deposit_confirmed"]})
    assert eff == frozenset({"deposit_confirmed"})
    # An event toggled OFF no longer wakes the agent…
    assert not retention_v2._is_decision_worthy(
        _evt(event_name="level_up"), {}, {"v2_decision_events": []})
    # …while the bet_settled loss special case survives any toggle set.
    assert retention_v2._is_decision_worthy(
        _evt(event_name="bet_settled"), {"net_loss_24h_usd": 500},
        {"v2_decision_events": [], "v2_loss_high_usd": 100.0})


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------
def test_decision_messages_shape():
    msgs = prompts.build_retention_v2_decision_messages(
        state={"user_status": "active"},
        event=_evt(),
        recent_events=[_evt(event_name="session_started")],
        history_tail=[{"role": "user", "content": "привет"}],
        allowed_actions=["silence", "message"],
        constraints=["comfort mode"])
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert "silence, message" in user
    assert "deposit_confirmed" in user
    assert "Hard constraint: comfort mode" in user
    assert "STRICT JSON" in msgs[0]["content"]


def test_ping_prompt_occasion_and_comfort_switch():
    base = dict(user_context={}, resolved_lang="ru", idle_days=5,
                reason="inactivity", intent="")
    time_based = prompts.build_retention_ping_prompt(**base)
    assert "has not been around" in time_based

    touch = prompts.build_retention_ping_prompt(
        **base, occasion="the player just made a deposit")
    assert "something just happened: the player just made a deposit" in touch
    # The message must NAME the occasion so the player never has to ask what
    # the bot means (the vague-congratulation bug).
    assert "unmistakably clear WHAT you are reacting to" in touch
    assert "COMFORT MODE" not in touch

    comfort = prompts.build_retention_ping_prompt(
        **base, occasion="a rough day", comfort=True)
    assert "COMFORT MODE" in comfort
    assert "do NOT invite them to play" in comfort


def test_retention_history_marks_proactive_turns():
    """A persisted proactive turn carries its trigger into the prompt history,
    so a follow-up like "what do you mean?" is answerable — without it the
    persona had no idea why it wrote first (the live bug)."""
    ctx = "deposit_confirmed: the player just made a deposit"
    history = [
        {"role": "assistant", "content": "Congrats!", "ping_context": ctx},
        {"role": "user", "content": "what do you mean?", "ping_context": None},
    ]
    msgs = prompts.build_retention_messages(
        session={"user_context": {}}, kb_block=None, history=history,
        user_text="это ты о чем?", resolved_lang="ru")
    assistant = msgs[1]["content"]
    assert assistant.startswith("[You sent this message PROACTIVELY")
    assert "deposit_confirmed" in assistant and "Congrats!" in assistant
    # Ordinary turns are untouched; a user row never gets the note.
    assert msgs[2] == {"role": "user", "content": "what do you mean?"}
    # The ping builder shares the same history rendering.
    ping_msgs = prompts.build_retention_ping_messages(
        session={"user_context": {}}, kb_block=None, history=history,
        resolved_lang="ru", idle_days=1, reason="", intent="")
    assert "deposit_confirmed" in ping_msgs[1]["content"]
    # And the returning-player continuity block labels the proactive turn too.
    prev = prompts._previous_context_directive(history)
    assert "proactive message, trigger: deposit_confirmed" in prev


def test_ping_messages_carry_previous_session_tail():
    """A ping into a fresh session (the idle rollover just closed the old
    chat) carries the previous conversation's tail — the ping task asks for
    concrete call-backs to what the player said, which needs something to
    call back to. Without the tail the prompt is unchanged."""
    prev = [{"role": "user", "content": "завтра сдаю экзамен по вождению",
             "created_at": None}]
    base = dict(session={"user_context": {}}, kb_block=None, history=[],
                resolved_lang="ru", idle_days=3, reason="quiet", intent="")
    msgs = prompts.build_retention_ping_messages(**base,
                                                 previous_history=prev)
    user = msgs[-1]["content"]
    assert "RETURNING PLAYER" in user and "экзамен" in user
    plain = prompts.build_retention_ping_messages(**base)
    assert "RETURNING PLAYER" not in plain[-1]["content"]


def test_current_time_directive_rides_layer3():
    # Without an offset the prompts are unchanged (None => no block).
    assert prompts._current_time_directive(None) == ""
    base = dict(user_context={}, resolved_lang="ru", idle_days=5,
                reason="inactivity", intent="")
    assert "CURRENT TIME" not in prompts.build_retention_ping_prompt(**base)

    # With the audience clock the block names the local hour and the part of
    # day, so "enjoy your evening" can never go out at 10:00 again.
    block = prompts._current_time_directive(0)
    assert "CURRENT TIME" in block
    assert any(p in block for p in ("morning", "afternoon", "evening", "night"))

    ping = prompts.build_retention_ping_prompt(**base, tz_offset_hours=0)
    assert "CURRENT TIME" in ping
    dialog = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="ru", user_text="привет",
        tz_offset_hours=3)
    assert "CURRENT TIME" in dialog


async def test_dry_run_idle_ping_is_remembered_by_the_pacing_guards(monkeypatch):
    """A shadow-fired rung must pace like a real one.

    A dry-run ping was recorded as status='skipped', but both pacing memories —
    the per-rule cooldown (`ping_rule_recently_fired`) and the anti-cascade rung
    guard (`idle_rule_thresholds_fired_since`) — only counted 'sent'. Since
    `v2_dry_run` ships ON, the ladder paced as if nothing had happened: the same
    rule re-matched the same player every `ping_min_gap_hours`, filling the
    decision ledger with duplicates of a ping it had already "made". It is now
    its own status, which both memories count.
    """
    from app.retention import retention, retention_idle

    recorded: list[tuple] = []

    async def _record(product_id, rid, rule_id, action, status, detail=None,
                      cost_usd=None):
        recorded.append((status, detail))

    async def _decision(*a, **kw):
        return 1

    async def _sess(pid, ru, lang):
        return {"id": "s-1", "product_id": pid}

    monkeypatch.setattr(db, "record_retention_ping", _record)
    monkeypatch.setattr(db, "insert_retention_v2_decision", _decision)
    monkeypatch.setattr(retention, "_ensure_session", _sess)
    monkeypatch.setattr(retention, "_user_context_from_ru", lambda ru: {})

    ok = await retention_idle._send_idle_ping(
        None, {"id": 1}, _ru(), {"id": 9, "action": "message",
                                 "trigger_kind": "bot_inactivity",
                                 "intent": "come back"},
        idle_days=7, cfg=_cfg(v2_dry_run=True))

    assert ok is True
    assert recorded == [("dry_run", "dry_run")]


def test_ping_pacing_queries_count_dry_runs():
    """The two pacing memories share one predicate, so they cannot drift apart
    again — and neither may be narrowed back to 'sent' alone."""
    assert "'dry_run'" in db._PING_FIRED
    assert "'sent'" in db._PING_FIRED
