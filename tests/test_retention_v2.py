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

import db
import player_sync
import prompts
import retention_v2
import settings
import tenancy


def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _cfg(**over):
    base = {
        "ping_daily_cap": 1, "ping_min_gap_hours": 48,
        "quiet_hours_start": 22, "quiet_hours_end": 9,
        "quiet_hours_utc_offset": 0, "ping_batch_size": 30,
        "v2_enabled": True, "v2_dry_run": True, "v2_show_trigger": True,
        "v2_daily_budget_usd": 5.0, "v2_loss_comfort_hours": 24,
        "v2_loss_high_usd": 100.0, "v2_same_event_cooldown_hours": 20,
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

    async def _touch(product_id, player_id, field, ts):
        touches.append((player_id, field))
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
    assert res == {"stored": True, "duplicate": False, "id": 42}
    # deposit_confirmed bumps last_deposit_at; the vip_level payload field
    # rides into the profile snapshot with source 'event'.
    assert touches == [("p1", "last_deposit_at")]
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

    today = _dt.date.today().isoformat()
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
                      exclude_silence=False):
        seen["hours"] = hours
        return True

    _patch_guard_env(monkeypatch, recent=True)
    monkeypatch.setattr(db, "recent_v2_decision_exists", _recent)

    # 0 = off: the DB is never asked, no cooldown reason.
    g = await retention_v2.guard_check(
        1, _ru(), _evt(), st, _cfg(v2_same_event_cooldown_hours=0))
    assert "same_event_cooldown" not in g["reasons"] and "hours" not in seen

    # A custom window is passed through.
    g = await retention_v2.guard_check(
        1, _ru(), _evt(), st, _cfg(v2_same_event_cooldown_hours=4))
    assert "same_event_cooldown" in g["reasons"] and seen["hours"] == 4

    # A cfg without the key (older override sets) falls back to the default.
    cfg = _cfg()
    del cfg["v2_same_event_cooldown_hours"]
    g = await retention_v2.guard_check(1, _ru(), _evt(), st, cfg)
    assert seen["hours"] == retention_v2._SAME_EVENT_COOLDOWN_HOURS


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
    rows: list[dict] = []

    async def _insert(product_id, **kw):
        rows.append(dict(kw, product_id=product_id))
        return len(rows)

    async def _admin_event(*a, **kw):
        return None

    monkeypatch.setattr(db, "insert_retention_v2_decision", _insert)
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

    async def _send(product, ru, evt, decision, *, comfort, cfg):
        sent["comfort"] = comfort
        return True, 0.003, None

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
    import chat_service
    import retention

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
                       ping_context=None):
        persisted.update(session_id=session_id, text=text,
                         ping_context=ping_context)
        return 1

    async def _record(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention, "_ensure_session", _sess)
    monkeypatch.setattr(retention, "_user_context_from_ru", lambda ru: {})
    monkeypatch.setattr(chat_service, "generate_retention_ping", _gen)
    monkeypatch.setattr(retention_v2, "TelegramClient", _TG)
    monkeypatch.setattr(db, "persist_ping_turn", _persist)
    monkeypatch.setattr(db, "record_retention_ping", _record)


async def test_send_touch_shows_trigger_and_persists_context(monkeypatch):
    sent, persisted = {}, {}
    _patch_send_env(monkeypatch, sent, persisted)
    decision = {"action": "message", "tone": "warm", "intent": "hi",
                "reason": "r"}

    ok, cost, detail = await retention_v2._send_touch(
        {"id": 1}, _ru(), _evt(), decision, comfort=False, cfg=_cfg())
    assert ok is True and detail is None
    # The fired trigger is a visible chrome line in the sent message
    # (the `v2_show_trigger` knob, on by default)…
    assert "deposit_confirmed" in sent["text"]
    assert "Поздравляю с пополнением!" in sent["text"]
    # …and the trigger + occasion are persisted with the turn, so the prompt
    # history (and the admin transcript) can explain later WHY the bot wrote.
    assert persisted["ping_context"].startswith("deposit_confirmed:")
    assert "deposit" in persisted["ping_context"]


async def test_send_touch_trigger_line_can_be_disabled(monkeypatch):
    sent, persisted = {}, {}
    _patch_send_env(monkeypatch, sent, persisted)
    decision = {"action": "message", "tone": "warm", "intent": "hi",
                "reason": "r"}
    ok, *_ = await retention_v2._send_touch(
        {"id": 1}, _ru(), _evt(), decision, comfort=False,
        cfg=_cfg(v2_show_trigger=False))
    assert ok is True
    assert "deposit_confirmed" not in sent["text"]
    # The persisted context stays regardless — it feeds the prompt history.
    assert persisted["ping_context"].startswith("deposit_confirmed:")


# ---------------------------------------------------------------------------
# The enable switch + the atomic event claim
# ---------------------------------------------------------------------------
async def test_sweep_skips_disabled_products(monkeypatch):
    monkeypatch.setattr(settings, "retention", lambda: _cfg(v2_enabled=False))
    monkeypatch.setattr(tenancy, "set_current_product", lambda pid: None)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"skipped": "agent_disabled"}


async def test_sweep_claims_events_atomically(monkeypatch):
    """The drain must use db.claim_retention_events (atomic pick-up) — a plain
    SELECT let the worker sweep and the admin «Process queue now» button pick
    up the SAME event concurrently and each send a message (the duplicate
    deposit thank-you bug)."""
    claimed = []

    async def _claim(pid, limit):
        claimed.append((pid, limit))
        return []
    monkeypatch.setattr(settings, "retention", lambda: _cfg(v2_enabled=True))
    monkeypatch.setattr(tenancy, "set_current_product", lambda pid: None)
    monkeypatch.setattr(db, "claim_retention_events", _claim)
    stats = await retention_v2.run_product_events({"id": 1})
    assert stats == {"events": 0, "decided": 0, "sent": 0}
    assert claimed == [(1, 30)]  # ping_batch_size from _cfg


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
    ok = {"v2_enabled": True, "v2_dry_run": False, "v2_show_trigger": False,
          "v2_daily_budget_usd": 2.5, "v2_loss_comfort_hours": 12,
          "v2_loss_high_usd": 200.0, "v2_same_event_cooldown_hours": 0,
          "worker_interval_sec": 5}
    assert settings.validate_setting("retention", ok) == ok
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"worker_interval_sec": 2})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_enabled": "yes"})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_show_trigger": "yes"})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_daily_budget_usd": -1})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"v2_loss_comfort_hours": 100000})
    with pytest.raises(ValueError):
        settings.validate_setting("retention",
                                  {"v2_same_event_cooldown_hours": -1})


def test_retention_settings_resolve_v2_defaults():
    cfg = settings.retention()
    assert cfg["v2_enabled"] is True        # the agent is the one regime
    assert cfg["v2_dry_run"] is True        # shadow mode by default
    assert cfg["v2_show_trigger"] is True   # trigger chrome line on by default
    assert cfg["v2_daily_budget_usd"] > 0
    assert cfg["v2_same_event_cooldown_hours"] == 20
    assert cfg["worker_interval_sec"] == 5  # near-realtime by default
    assert "pings_enabled" not in cfg       # the v1 ping matrix is gone


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
