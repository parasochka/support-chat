"""Retention-bot logic tests: prompt assembly, sentinels, gating, transport parse.

Pure-logic coverage (no real DB). DB-touching helpers are monkeypatched where a
test needs to reach past them.
"""
from __future__ import annotations

import prompts
import retention
import settings
import telegram_transport as tt


# ---------------------------------------------------------------------------
# Prompt assembly + sentinels
# ---------------------------------------------------------------------------
def test_retention_core_byte_stable():
    a = prompts.get_retention_system_core()
    b = prompts.get_retention_system_core()
    assert a == b
    # persona + retention machinery present; support-only mechanics absent
    assert "PHOTO" in a and "HANDOFF" in a and "STAGE_UP" in a
    assert "TOPIC ROUTING" not in a
    assert "[[SUGGEST" not in a


def test_retention_core_differs_from_support_core():
    assert prompts.get_retention_system_core() != prompts.get_system_core()


def test_retention_core_uses_telegram_formatting():
    """Retention replies are rendered with a LIGHT Telegram-HTML subset
    (telegram_format converts **bold**/*italic* and sends parse_mode=HTML), so
    the retention Layer 1 carries the telegram formatting directive — allowing a
    touch of emphasis but no lists/tables, and forbidding em dashes / guillemets.
    It must NOT carry the widget's own Markdown directive."""
    core = prompts.get_retention_system_core()
    assert "FORMATTING (TELEGRAM)" in core
    assert "em dash" in core and "guillemet" in core.lower()
    assert "Always use light Markdown" not in core
    # the support core keeps asking for the widget's Markdown subset
    assert "Always use light Markdown" in prompts.get_system_core()


def test_retention_core_has_own_tone_variable():
    """Retention tone is tuned independently from the support tone."""
    assert "{retention_tone_of_voice}" in prompts.SYSTEM_CORE_RETENTION
    assert "{tone_of_voice}" not in prompts.SYSTEM_CORE_RETENTION
    assert "{tone_of_voice}" in prompts.SYSTEM_CORE


def test_retention_core_puts_connection_before_casino():
    """The retention mission is the personal connection — the player comes back
    to HER; the casino is a light occasional backdrop, never a per-message
    pitch (a constant nudge toward play reads as an advert and kills the
    mood)."""
    core = prompts.get_retention_system_core()
    assert "never in every message" in core
    assert "come back" in core
    # The old promotional mission line must stay gone.
    assert "keep the excitement of playing alive" not in core


async def test_create_deeplink_carries_lang(monkeypatch):
    """The site conversation's language rides in the nonce payload (supported
    codes only) so /start can adopt it as the bot's conversation language."""
    captured = {}

    async def _create_nonce(nonce, product_id, payload, escalation, ttl):
        captured.update(payload=payload, escalation=escalation)
    monkeypatch.setattr(retention.db, "create_retention_nonce", _create_nonce)

    async def _log_event(*a, **k):
        pass
    monkeypatch.setattr(retention.db, "log_admin_event", _log_event)

    product = {"id": 1, "telegram_bot_username": "nika_bot"}
    link = await retention.create_deeplink(product, {"full_name": "Andrey"},
                                           escalation=True, lang="ru")
    assert link["deep_link"].startswith("https://t.me/nika_bot?start=")
    assert captured["payload"]["lang"] == "ru"
    assert captured["payload"]["full_name"] == "Andrey"

    # an unsupported code is dropped, not stored
    await retention.create_deeplink(product, {}, escalation=False, lang="xx")
    assert "lang" not in captured["payload"]


def test_strip_photo_tag():
    clean, pid = prompts.strip_photo_tag("[[PHOTO:42]]\nlook at this")
    assert pid == 42 and clean == "look at this"
    clean, pid = prompts.strip_photo_tag("no tag here")
    assert pid is None and clean == "no tag here"
    # non-numeric ids never match
    clean, pid = prompts.strip_photo_tag("[[PHOTO:abc]]\nhi")
    assert pid is None


def test_strip_stage_and_handoff_tags():
    clean, up = prompts.strip_stage_up_tag("[[STAGE_UP]]\nhey")
    assert up is True and clean == "hey"
    clean, ho = prompts.strip_handoff_tag("[[HANDOFF]]\nI'll pass you along")
    assert ho is True and clean == "I'll pass you along"
    clean, ho = prompts.strip_handoff_tag("just chatting")
    assert ho is False


def test_photo_candidates_directive():
    block = prompts._photo_candidates_directive([
        {"id": 7, "stage": 2, "description": "beach, red swimsuit", "tags": ["beach", "summer"]},
    ])
    assert "7" in block and "beach" in block and "stage 2" in block
    empty = prompts._photo_candidates_directive([])
    assert "none" in empty.lower()


def test_build_retention_messages_shape():
    session = {"id": "s1", "user_context": {"full_name": "Andrey", "vip_level": "Gold"}}
    msgs = prompts.build_retention_messages(
        session=session, kb_block="Scenario: welcome back", history=[],
        user_text="hi there", resolved_lang="en",
        photo_candidates=[{"id": 3, "stage": 1, "description": "portrait", "tags": []}],
    )
    assert msgs[0]["role"] == "system"
    assert "RETENTION KNOWLEDGE BASE" in msgs[0]["content"]
    assert msgs[-1]["role"] == "user"
    assert "PHOTO CANDIDATES" in msgs[-1]["content"]
    assert "hi there" in msgs[-1]["content"]


# ---------------------------------------------------------------------------
# Settings group
# ---------------------------------------------------------------------------
def test_retention_settings_defaults():
    r = settings.retention()
    assert r["daily_photo_cap"] == 10
    assert r["candidate_list_size"] == 6
    assert r["stage_advance_msgs"] == [20, 40, 80, 160]
    assert "vip_tiers" in r and isinstance(r["max_stage_by_tier"], dict)


def test_retention_settings_validation_ok():
    v = settings.validate_setting("retention", {
        "daily_photo_cap": 5,
        "stage_advance_msgs": [10, 20],
        "max_stage_by_tier": {"gold": 3},
        "vip_tiers": ["none", "gold"],
    })
    assert v["daily_photo_cap"] == 5


def test_retention_settings_validation_rejects_bad():
    import pytest
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"daily_photo_cap": -1})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"stage_advance_msgs": ["x"]})
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"max_stage_by_tier": {"gold": 99}})


def test_retention_is_in_setting_keys():
    assert "retention" in settings.SETTING_KEYS
    assert "retention" in settings.resolved_all()


# ---------------------------------------------------------------------------
# Tier / stage / request helpers
# ---------------------------------------------------------------------------
def test_tier_ordinal_and_ceiling():
    cfg = settings.retention()
    assert retention.tier_ordinal("Gold", cfg) == cfg["vip_tiers"].index("gold")
    assert retention.tier_ordinal("unknown-tier", cfg) == 0
    assert retention.tier_stage_ceiling("gold", cfg) == cfg["max_stage_by_tier"]["gold"]
    # a tier not in the map falls back to the global max_stage
    assert retention.tier_stage_ceiling("mystery", cfg) == cfg["max_stage"]


def test_photo_request_detection():
    assert retention.is_photo_request("покажи фото")
    assert retention.is_photo_request("send me a pic")
    assert not retention.is_photo_request("какая погода")


def test_is_meaningful():
    assert retention.is_meaningful("привет как дела")
    assert not retention.is_meaningful("!")
    assert not retention.is_meaningful("а")


def test_resolve_user_lang():
    assert retention.resolve_user_lang({"conv_lang": "es"}) == "es"
    # falls back to tg client language, then default
    lang = retention.resolve_user_lang({}, "ru-RU")
    assert lang in ("ru", settings.language()["default"])


# ---------------------------------------------------------------------------
# Stage advance gate (monkeypatched DB write)
# ---------------------------------------------------------------------------
async def test_maybe_advance_stage_gated(monkeypatch):
    calls = []

    async def _fake_advance(rid, new_stage):
        calls.append((rid, new_stage))

    monkeypatch.setattr(retention.db, "advance_retention_stage", _fake_advance)

    # Below threshold -> no advance even with a hint.
    ru = {"id": 1, "unlocked_stage": 1, "vip_level": "gold",
          "meaningful_msgs": 5, "last_stage_advance_at": None}
    assert await retention.maybe_advance_stage(ru, True) is None
    assert calls == []

    # At threshold (20 for stage 2) -> advance.
    ru2 = {"id": 2, "unlocked_stage": 1, "vip_level": "gold",
           "meaningful_msgs": 25, "last_stage_advance_at": None}
    new_stage = await retention.maybe_advance_stage(ru2, True)
    assert new_stage == 2 and calls == [(2, 2)]


async def test_stage_advance_respects_tier_ceiling(monkeypatch):
    calls = []

    async def _fake_advance(rid, new_stage):
        calls.append((rid, new_stage))

    monkeypatch.setattr(retention.db, "advance_retention_stage", _fake_advance)
    # "none" tier ceiling is 3; already at the ceiling, can't advance to stage 4.
    ru = {"id": 3, "unlocked_stage": 3, "vip_level": "none",
          "meaningful_msgs": 200, "last_stage_advance_at": None}
    assert await retention.maybe_advance_stage(ru, True) is None
    assert calls == []


# ---------------------------------------------------------------------------
# Candidate gating early-returns (no DB reached on cap/cooldown)
# ---------------------------------------------------------------------------
async def test_candidates_daily_cap(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("db.candidate_photos should not be called at cap")

    monkeypatch.setattr(retention.db, "candidate_photos", _boom)
    ru = {"id": 1, "vip_level": "gold", "unlocked_stage": 2,
          "photos_sent_today": 10, "photos_day": None, "msgs_since_photo": 99}
    out = await retention.select_photo_candidates(1, ru, "покажи фото")
    assert out == []


async def test_candidates_cooldown(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("proactive cooldown should short-circuit")

    monkeypatch.setattr(retention.db, "candidate_photos", _boom)
    # not a photo request + msgs_since_photo below cooldown (6) -> empty
    ru = {"id": 1, "vip_level": "gold", "unlocked_stage": 2,
          "photos_sent_today": 0, "photos_day": None, "msgs_since_photo": 1}
    out = await retention.select_photo_candidates(1, ru, "как дела")
    assert out == []


async def test_candidates_reactive_bypasses_cooldown(monkeypatch):
    seen = {}

    async def _fake(product_id, rid, *, level_ordinal, max_stage, limit):
        seen.update(level_ordinal=level_ordinal, max_stage=max_stage, limit=limit)
        return [{"id": 5}]

    monkeypatch.setattr(retention.db, "candidate_photos", _fake)
    ru = {"id": 1, "vip_level": "gold", "unlocked_stage": 2,
          "photos_sent_today": 0, "photos_day": None, "msgs_since_photo": 0}
    out = await retention.select_photo_candidates(1, ru, "покажи фото пожалуйста")
    assert out == [{"id": 5}]
    # teaser: max_stage = min(unlocked+1, ceiling); unlocked 2 -> 3 (below the
    # gold ceiling of 5, so the teaser step is what caps it)
    assert seen["max_stage"] == 3


# ---------------------------------------------------------------------------
# Telegram transport parsing
# ---------------------------------------------------------------------------
def test_parse_start_update():
    pu = tt.parse_update({"message": {"from": {"id": 5, "username": "u"},
                                      "chat": {"id": 5}, "text": "/start r7Kx"}})
    assert pu.kind == "message" and pu.tg_user_id == 5 and pu.start_param == "r7Kx"


def test_parse_plain_message():
    pu = tt.parse_update({"message": {"from": {"id": 9}, "chat": {"id": 9},
                                      "text": "hi"}})
    assert pu.kind == "message" and pu.start_param is None and pu.text == "hi"


def test_parse_callback():
    pu = tt.parse_update({"callback_query": {"id": "cb1", "from": {"id": 3},
                          "data": "rtn:nika", "message": {"chat": {"id": 3},
                          "message_id": 12}}})
    assert pu.kind == "callback" and pu.callback_data == "rtn:nika"
    assert pu.callback_id == "cb1" and pu.chat_id == 3


def test_extract_photo_file_id():
    result = {"photo": [{"file_id": "small"}, {"file_id": "big"}]}
    assert tt.TelegramClient.extract_photo_file_id(result) == "big"
    assert tt.TelegramClient.extract_photo_file_id(None) is None
    assert tt.TelegramClient.extract_photo_file_id({"photo": []}) is None


def test_inline_keyboard():
    kb = tt.inline_keyboard([[{"text": "A", "callback_data": "a"}]])
    assert kb == {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}


# ---------------------------------------------------------------------------
# Lazy profile pull (§8 level 2)
# ---------------------------------------------------------------------------
def test_retention_settings_has_pull_ttl():
    assert "profile_pull_ttl_sec" in settings.retention()
    v = settings.validate_setting("retention", {"profile_pull_ttl_sec": 60})
    assert v["profile_pull_ttl_sec"] == 60


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    payload = {"vip_level": "Platinum", "balance": "5000 EUR"}
    status = 200

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        return _FakeResp(self.status, self.payload)


async def test_maybe_pull_profile_fresh_skips(monkeypatch):
    import datetime as dt
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    called = {"update": False}

    async def _update(*a, **k):
        called["update"] = True
        return 1
    monkeypatch.setattr(retention.db, "update_retention_profile", _update)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    product = {"id": 1, "player_api_url": "https://api"}
    ru = {"id": 1, "tg_user_id": 7, "player_id": "p1", "profile_updated_at": now}
    out = await retention.maybe_pull_profile(product, ru)
    assert out is ru and called["update"] is False   # fresh -> no pull


async def test_maybe_pull_profile_stale_pulls(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def _key(pid):
        return "apikey"
    monkeypatch.setattr(retention.db, "get_product_player_api_key", _key)

    captured = {}

    async def _update(product_id, player_id, profile, source):
        captured.update(profile=profile, source=source)
        return 1
    monkeypatch.setattr(retention.db, "update_retention_profile", _update)

    async def _get(pid, tg):
        return {"id": 1, "tg_user_id": tg, "vip_level": "Platinum"}
    monkeypatch.setattr(retention.db, "get_retention_user", _get)

    product = {"id": 1, "player_api_url": "https://api"}
    ru = {"id": 1, "tg_user_id": 7, "player_id": "p1",
          "profile_updated_at": "2000-01-01T00:00:00+00:00"}
    out = await retention.maybe_pull_profile(product, ru)
    assert captured["source"] == "pull"
    assert captured["profile"]["vip_level"] == "Platinum"
    assert out["vip_level"] == "Platinum"


async def test_maybe_pull_profile_no_url_noop(monkeypatch):
    product = {"id": 1, "player_api_url": ""}
    ru = {"id": 1, "tg_user_id": 7, "player_id": "p1"}
    out = await retention.maybe_pull_profile(product, ru)
    assert out is ru
