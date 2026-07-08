"""The proactive ping engine (retention_pings) — the "retention matrix" worker.

Covers: quiet-hours math, rule matching (trigger kinds, VIP filter, per-rule
cooldown), the send flow (persist only what was delivered, ledger rows, blocked
bot -> unreachable), the photo->text fallback, the ping prompt shape, and the
new `retention` settings-knob validation.
"""
from __future__ import annotations

import datetime as _dt

import chat_service
import db
import prompts
import retention_pings
import settings


def _cfg(**over):
    base = {
        "pings_enabled": True, "ping_daily_cap": 1, "ping_min_gap_hours": 48,
        "quiet_hours_start": 22, "quiet_hours_end": 9,
        "quiet_hours_utc_offset": 0, "ping_batch_size": 30,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------
def test_quiet_hours_window(monkeypatch):
    class _FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 23, 0, tzinfo=_dt.timezone.utc)

    monkeypatch.setattr(retention_pings._dt, "datetime", _FakeDT)
    assert retention_pings._in_quiet_hours(_cfg())              # 23:00 in 22->9
    assert not retention_pings._in_quiet_hours(
        _cfg(quiet_hours_utc_offset=12))                        # local 11:00
    assert not retention_pings._in_quiet_hours(
        _cfg(quiet_hours_start=9, quiet_hours_end=9))           # zero-length


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------
def _iso_days_ago(days: float) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(days=days)).isoformat()


def _rule(**over):
    base = {"id": 1, "name": "idle-week", "enabled": True,
            "trigger_kind": "bot_inactivity", "inactivity_days": 7,
            "action": "message", "intent": "", "vip_tiers": [],
            "cooldown_days": 14, "priority": 0}
    base.update(over)
    return base


async def test_match_rule_by_bot_inactivity(monkeypatch):
    async def _not_fired(rid, rule_id, days):
        return False
    monkeypatch.setattr(db, "ping_rule_recently_fired", _not_fired)
    ru = {"id": 10, "vip_level": "gold", "last_active_at": _iso_days_ago(8)}

    matched = await retention_pings._match_rule(ru, [_rule()])
    assert matched is not None
    rule, idle = matched
    assert rule["id"] == 1 and idle == 8

    # Not idle enough -> no match.
    fresh = dict(ru, last_active_at=_iso_days_ago(2))
    assert await retention_pings._match_rule(fresh, [_rule()]) is None


async def test_match_rule_vip_filter_and_cooldown(monkeypatch):
    fired: dict = {"value": False}

    async def _fired(rid, rule_id, days):
        return fired["value"]
    monkeypatch.setattr(db, "ping_rule_recently_fired", _fired)
    ru = {"id": 10, "vip_level": "Bronze", "last_active_at": _iso_days_ago(10)}

    # VIP filter: rule limited to gold does not fire for bronze.
    assert await retention_pings._match_rule(
        ru, [_rule(vip_tiers=["gold"])]) is None
    # Matching tier fires…
    assert await retention_pings._match_rule(
        ru, [_rule(vip_tiers=["bronze"])]) is not None
    # …unless the rule already pinged them within its cooldown.
    fired["value"] = True
    assert await retention_pings._match_rule(
        ru, [_rule(vip_tiers=["bronze"])]) is None


async def test_casino_triggers_need_the_signal(monkeypatch):
    async def _not_fired(rid, rule_id, days):
        return False
    monkeypatch.setattr(db, "ping_rule_recently_fired", _not_fired)
    # No casino data at all -> casino rules never fire (no false pings).
    ru = {"id": 10, "vip_level": "", "last_active_at": _iso_days_ago(30)}
    assert await retention_pings._match_rule(
        ru, [_rule(trigger_kind="casino_inactivity")]) is None
    assert await retention_pings._match_rule(
        ru, [_rule(trigger_kind="no_deposit")]) is None

    # With the signal present the rule keys on it.
    ru2 = dict(ru, last_deposit_at=_iso_days_ago(40))
    matched = await retention_pings._match_rule(
        ru2, [_rule(trigger_kind="no_deposit", inactivity_days=30)])
    assert matched is not None and matched[1] == 40


# ---------------------------------------------------------------------------
# The send flow
# ---------------------------------------------------------------------------
class FakeTelegram:
    def __init__(self, *a, **k):
        self.sent: list = []
        self.fail_code = None

    async def send_message_verbose(self, chat_id, text, *, reply_markup=None,
                                   parse_mode=None):
        if self.fail_code:
            return None, self.fail_code, "bot was blocked by the user"
        self.sent.append((chat_id, text))
        return {"message_id": 1}, None, None


PRODUCT = {"id": 1, "active": True, "retention_enabled": True,
           "telegram_bot_username": "nika_bot"}

RU = {"id": 10, "tg_user_id": 7, "vip_level": "gold", "conv_lang": "en",
      "player_id": "p9", "session_id": "sess-1", "subscribed": True,
      "last_active_at": None, "full_name": "Andrey"}


def _wire_product_run(monkeypatch, tg, *, users, rules, capture):
    import retention as retention_mod
    monkeypatch.setattr(settings, "retention", lambda: dict(
        _cfg(), daily_photo_cap=10, proactive_photo_cooldown_msgs=6,
        candidate_list_size=6, stage_advance_msgs=[20, 45, 80],
        stage_advance_min_hours=24, max_stage=4,
        max_stage_by_tier={"gold": 4}, vip_tiers=["none", "gold"],
        nonce_ttl_sec=120, profile_pull_ttl_sec=0, session_idle_minutes=0,
        carry_context_turns=6))

    async def _token(pid):
        return "bot-token"
    monkeypatch.setattr(db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention_pings, "TelegramClient", lambda *a, **k: tg)

    async def _rules(pid, only_enabled=False):
        return rules
    monkeypatch.setattr(db, "list_retention_rules", _rules)

    async def _users(pid, **kw):
        return users
    monkeypatch.setattr(db, "eligible_ping_users", _users)

    async def _not_fired(rid, rule_id, days):
        return False
    monkeypatch.setattr(db, "ping_rule_recently_fired", _not_fired)

    async def _session(pid, ru, lang):
        return {"id": "sess-1", "product_id": pid, "user_context": {},
                "lang": lang, "conv_lang": lang, "message_count": 3,
                "status": "open"}
    monkeypatch.setattr(retention_mod, "_ensure_session", _session)

    async def _record(pid, rid, rule_id, action, status, detail=None,
                      cost_usd=None):
        capture.setdefault("ledger", []).append((status, action, detail))
    monkeypatch.setattr(db, "record_retention_ping", _record)

    async def _persist(sid, text, ai_meta=None, product_id=None):
        capture["persisted"] = text
        return 4
    monkeypatch.setattr(db, "persist_ping_turn", _persist)

    async def _log_event(*a, **k):
        capture.setdefault("events", []).append(a[1] if len(a) > 1 else k)
    monkeypatch.setattr(db, "log_admin_event", _log_event)

    async def _log_ai(*a, **k):
        capture["ai_logged_failure"] = True
    monkeypatch.setattr(db, "log_ai_interaction", _log_ai)

    async def _unreachable(rid, val=True):
        capture["unreachable"] = val
    monkeypatch.setattr(db, "set_retention_unreachable", _unreachable)


async def test_run_product_pings_sends_and_persists(monkeypatch):
    tg = FakeTelegram()
    capture: dict = {}
    ru = dict(RU, last_active_at=_iso_days_ago(9))
    _wire_product_run(monkeypatch, tg, users=[ru], rules=[_rule()],
                      capture=capture)

    async def _draft(session, **kw):
        assert kw["idle_days"] == 9
        return chat_service.PingDraft(
            text="hey, miss you", lang="en", photo_id=None,
            ai_meta={"model": "gpt-5-mini", "cost_usd": 0.001, "ok": True})
    monkeypatch.setattr(chat_service, "generate_retention_ping", _draft)

    stats = await retention_pings.run_product_pings(PRODUCT,
                                                    ignore_quiet_hours=True)

    assert stats == {"sent": 1, "failed": 0, "considered": 1}
    assert tg.sent == [(7, "hey, miss you")]
    assert capture["persisted"] == "hey, miss you"
    assert ("sent", "message", "idle-week") in capture["ledger"]
    assert capture["events"], "an admin retention_ping event is expected"


async def test_blocked_bot_marks_unreachable(monkeypatch):
    tg = FakeTelegram()
    tg.fail_code = 403
    capture: dict = {}
    ru = dict(RU, last_active_at=_iso_days_ago(9))
    _wire_product_run(monkeypatch, tg, users=[ru], rules=[_rule()],
                      capture=capture)

    async def _draft(session, **kw):
        return chat_service.PingDraft(
            text="hey", lang="en", photo_id=None,
            ai_meta={"model": "gpt-5-mini", "cost_usd": 0.001, "ok": True})
    monkeypatch.setattr(chat_service, "generate_retention_ping", _draft)

    stats = await retention_pings.run_product_pings(PRODUCT,
                                                    ignore_quiet_hours=True)

    assert stats["failed"] == 1 and stats["sent"] == 0
    assert capture["unreachable"] is True
    assert "persisted" not in capture, "an undelivered ping is never persisted"
    assert capture["ai_logged_failure"], "the model cost must still be logged"
    assert capture["ledger"][0][0] == "failed"


async def test_pings_disabled_skips(monkeypatch):
    monkeypatch.setattr(settings, "retention",
                        lambda: dict(_cfg(pings_enabled=False)))
    stats = await retention_pings.run_product_pings(PRODUCT)
    assert stats == {"skipped": "pings_disabled"}


async def test_model_failure_skips_player(monkeypatch):
    tg = FakeTelegram()
    capture: dict = {}
    ru = dict(RU, last_active_at=_iso_days_ago(9))
    _wire_product_run(monkeypatch, tg, users=[ru], rules=[_rule()],
                      capture=capture)

    async def _draft(session, **kw):
        return None  # transient model failure
    monkeypatch.setattr(chat_service, "generate_retention_ping", _draft)

    stats = await retention_pings.run_product_pings(PRODUCT,
                                                    ignore_quiet_hours=True)
    assert stats["failed"] == 1
    assert tg.sent == []
    assert capture["ledger"][0] == ("failed", "message", "model_error")


# ---------------------------------------------------------------------------
# Ping prompt shape
# ---------------------------------------------------------------------------
def test_ping_prompt_shape():
    session = {"user_context": {"full_name": "Andrey", "vip_level": "Gold"},
               "lang": "en"}
    messages = prompts.build_retention_ping_messages(
        session=session, kb_block="KB DOC", history=[],
        resolved_lang="ru", idle_days=7, reason="they have been away",
        intent="tease the weekend slots race",
        photo_candidates=[{"id": 5, "stage": 1, "description": "beach",
                           "tags": []}],
    )
    system = messages[0]["content"]
    user = messages[-1]["content"]
    # Layer 1 is the normal byte-stable retention core (+ the KB document).
    assert system.startswith(prompts.get_retention_system_core())
    assert "KB DOC" in system
    assert "PROACTIVE MESSAGE TASK" in user
    assert "7 days" in user
    assert "tease the weekend slots race" in user
    assert "[[LANG:ru]]" in user
    assert "Andrey" in user
    assert "- 5 | stage 1 | beach" in user


def test_retention_ping_settings_validation():
    ok = settings.validate_setting("retention", {
        "pings_enabled": True, "ping_daily_cap": 2, "ping_min_gap_hours": 24,
        "quiet_hours_start": 22, "quiet_hours_end": 9,
        "quiet_hours_utc_offset": 3, "ping_batch_size": 10})
    assert ok["pings_enabled"] is True

    import pytest
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"quiet_hours_start": 25})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"ping_daily_cap": 0})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"pings_enabled": "yes"})
