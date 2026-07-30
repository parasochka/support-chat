"""Stage progression surfacing: the Layer-3 PROGRESSION block, the stage-up
celebration note, and the settings knob that gates it.

The player-facing contract: Nika can explain HOW photos unlock (chat more ->
closer -> more daring photos; VIP raises the ceiling) using the player's REAL
numbers, and a real stage advance is followed by a celebratory persona note
that is persisted with its trigger — so a later "а что это было?" gets an
accurate answer from the history.
"""
import pytest

from app.chat import chat_service
from app.ai import prompts
from app.retention import retention
from app.core import settings


# ---------------------------------------------------------------------------
# progression_context — mirrors the maybe_advance_stage gate maths
# ---------------------------------------------------------------------------
def test_progression_context_mid_ladder():
    ru = {"unlocked_stage": 1, "vip_level": "gold", "meaningful_msgs": 12}
    p = retention.progression_context(ru)
    assert p["stage"] == 1
    assert p["ceiling"] == settings.retention()["max_stage_by_tier"]["gold"]
    assert p["vip_level"] == "gold"
    assert p["meaningful_msgs"] == 12
    # Next stage (2) needs stage_advance_msgs[0] (default 20).
    assert p["next_threshold"] == 20
    assert p["at_ceiling"] is False


def test_progression_context_at_tier_ceiling():
    # "none" tier ceiling is 3 by default: at stage 3 nothing further unlocks
    # by chatting alone.
    ru = {"unlocked_stage": 3, "vip_level": "none", "meaningful_msgs": 500}
    p = retention.progression_context(ru)
    assert p["at_ceiling"] is True
    assert p["next_threshold"] is None


def test_progression_context_no_threshold_is_ceiling(monkeypatch):
    cfg = settings.retention()
    monkeypatch.setattr(retention.settings, "retention",
                        lambda: {**cfg, "stage_advance_msgs": []})
    ru = {"unlocked_stage": 1, "vip_level": "gold", "meaningful_msgs": 999}
    assert retention.progression_context(ru)["at_ceiling"] is True


# ---------------------------------------------------------------------------
# The Layer-3 PROGRESSION block
# ---------------------------------------------------------------------------
def test_progression_block_in_dynamic_prompt():
    p = prompts.build_retention_dynamic_prompt(
        user_context={"full_name": "Andrey Smith", "vip_level": "Gold"},
        resolved_lang="ru", user_text="а когда фото поинтереснее?",
        progression={"stage": 2, "ceiling": 5, "vip_level": "Gold",
                     "meaningful_msgs": 25, "next_threshold": 40,
                     "at_ceiling": False})
    assert "=== PROGRESSION" in p
    assert "level unlocked: 2" in p
    assert "Gold" in p
    # ~15 more meaningful messages to the next stage.
    assert "roughly 15 more" in p


def test_progression_block_at_ceiling_wording():
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="more?",
        progression={"stage": 3, "ceiling": 3, "vip_level": "none",
                     "meaningful_msgs": 100, "next_threshold": None,
                     "at_ceiling": True})
    assert "top level currently available" in p
    assert "higher VIP standing would" in p


def test_no_progression_no_block():
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hi")
    assert "=== PROGRESSION" not in p


def test_progression_rides_through_build_retention_messages():
    session = {"user_context": {"full_name": "Andrey"}, "conv_lang": "ru"}
    msgs = prompts.build_retention_messages(
        session=session, kb_block=None, history=[], user_text="привет",
        resolved_lang="ru",
        progression={"stage": 1, "ceiling": 4, "vip_level": "bronze",
                     "meaningful_msgs": 3, "next_threshold": 20,
                     "at_ceiling": False})
    assert "=== PROGRESSION" in msgs[-1]["content"]
    # Dynamic data must never enter the byte-stable Layer-1 system message.
    assert "=== PROGRESSION" not in msgs[0]["content"]


# ---------------------------------------------------------------------------
# The Layer-3 OPENNESS LEVEL block (verbal register ladder)
# ---------------------------------------------------------------------------
_PROGRESSION_S5 = {"stage": 5, "ceiling": 5, "vip_level": "Diamond",
                   "meaningful_msgs": 200, "next_threshold": None,
                   "at_ceiling": True}


def test_openness_level_follows_boldest_sent_photo():
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hey",
        progression=_PROGRESSION_S5,
        appearance={"base": [], "last_sent": None, "max_sent_stage": 5,
                    "sent": [{"description": "d", "media_type": "photo",
                              "stage": 5, "viewed_at": None}]})
    assert "=== OPENNESS LEVEL" in p
    assert "Current level: 5 of 5" in p


def test_openness_level_all_time_max_beats_recent_window():
    # The `sent` list is capped to the last 8 views; the all-time aggregate
    # must win, so a run of recent tame photos never drops the register.
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hey",
        progression=_PROGRESSION_S5,
        appearance={"base": [], "last_sent": None, "max_sent_stage": 5,
                    "sent": [{"description": "d", "media_type": "photo",
                              "stage": 1, "viewed_at": None}]})
    assert "Current level: 5 of 5" in p


def test_openness_level_fallback_without_aggregate():
    # Callers without max_sent_stage (older context shape) still ground the
    # level in the boldest row of the sent list.
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hey",
        progression=_PROGRESSION_S5,
        appearance={"base": [], "last_sent": None,
                    "sent": [{"description": "d", "media_type": "photo",
                              "stage": 3, "viewed_at": None}]})
    assert "Current level: 3 of 5" in p


def test_openness_level_nothing_sent_caps_at_two():
    # Words never run ahead of the photos he has seen: with no photo received
    # yet, even an unlocked stage 4 keeps the verbal register at 2.
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hey",
        progression={"stage": 4, "ceiling": 5, "vip_level": "Diamond",
                     "meaningful_msgs": 90, "next_threshold": 120,
                     "at_ceiling": False})
    assert "Current level: 2 of 5" in p


def test_no_progression_no_openness_block():
    p = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="en", user_text="hi",
        appearance={"base": ["desc"], "last_sent": None, "max_sent_stage": 5,
                    "sent": []})
    assert "=== OPENNESS LEVEL" not in p


def test_openness_block_stays_out_of_layer_one():
    session = {"user_context": {}, "conv_lang": "en"}
    msgs = prompts.build_retention_messages(
        session=session, kb_block=None, history=[], user_text="hi",
        resolved_lang="en", progression=_PROGRESSION_S5,
        appearance={"base": [], "last_sent": None, "max_sent_stage": 2,
                    "sent": []})
    assert "=== OPENNESS LEVEL" in msgs[-1]["content"]
    # Per-request data must never enter the byte-stable Layer-1 system message
    # (the tone-of-voice may NAME the block, so key on the block header).
    assert "=== OPENNESS LEVEL" not in msgs[0]["content"]


def test_ping_path_carries_no_openness_block():
    # Documented: the ping stack passes no progression, so the ladder never
    # renders on a turn that cannot ground it.
    msgs = prompts.build_retention_ping_messages(
        session={"user_context": {}, "conv_lang": "en"}, kb_block=None,
        history=[], resolved_lang="en", idle_days=3, reason="idle", intent="")
    assert "=== OPENNESS LEVEL" not in msgs[-1]["content"]


# ---------------------------------------------------------------------------
# The stage-up celebration task (proactive ping stack variant)
# ---------------------------------------------------------------------------
def _stage_up_messages(at_ceiling: bool):
    session = {"user_context": {"full_name": "Andrey Smith"}, "conv_lang": "ru"}
    return prompts.build_retention_ping_messages(
        session=session, kb_block=None, history=[], resolved_lang="ru",
        idle_days=0, reason="stage_up", intent="",
        stage_up={"at_ceiling": at_ceiling})


def test_stage_up_task_more_to_come():
    task = _stage_up_messages(at_ceiling=False)[-1]["content"]
    assert "LEVEL-UP CELEBRATION TASK" in task
    assert "reached a NEW LEVEL" in task
    assert "more daring" in task
    # The keep-chatting hint is present, the ceiling wording is not.
    assert "keeps chatting with you" in task
    assert "top level currently available" not in task
    # It must not fall through to the idle-reengagement task.
    assert "has not been around" not in task


def test_stage_up_task_at_ceiling():
    task = _stage_up_messages(at_ceiling=True)[-1]["content"]
    assert "top level currently available" in task
    assert "keeps chatting with you" not in task


def test_stage_up_takes_precedence_over_occasion():
    session = {"user_context": {}, "conv_lang": "en"}
    msgs = prompts.build_retention_ping_messages(
        session=session, kb_block=None, history=[], resolved_lang="en",
        idle_days=0, reason="", intent="", occasion="a deposit arrived",
        stage_up={"at_ceiling": False})
    task = msgs[-1]["content"]
    assert "LEVEL-UP CELEBRATION TASK" in task
    assert "a deposit arrived" not in task


# ---------------------------------------------------------------------------
# Settings knob + carry-over default
# ---------------------------------------------------------------------------
def test_stage_up_notify_default_on():
    assert settings.retention()["stage_up_notify"] is True


def test_stage_up_notify_validation():
    v = settings.validate_setting("retention", {"stage_up_notify": False})
    assert v["stage_up_notify"] is False
    with pytest.raises(ValueError):
        settings.validate_setting("retention", {"stage_up_notify": "yes"})


def test_carry_context_default_is_ten():
    # The short-term cross-session memory window: ~10 messages of the previous
    # chat carried into a returning player's first turn.
    assert settings.retention()["carry_context_turns"] == 10


# ---------------------------------------------------------------------------
# _send_stage_up_note — generate -> send -> persist with ping_context
# ---------------------------------------------------------------------------
async def test_send_stage_up_note_persists_with_context(monkeypatch):
    sent, persisted, events = [], [], []

    async def _fake_generate(session, **kwargs):
        assert kwargs["stage_up"] == {"at_ceiling": False}
        # The note follows the language of the turn it trails.
        assert session["conv_lang"] == "ru"
        return chat_service.PingDraft(
            text="мы стали ближе!", lang="ru", photo_id=None,
            ai_meta={"model": "gpt-test", "cost_usd": 0.001, "ok": True})

    async def _fake_send(client, chat_id, text, **kwargs):
        sent.append((chat_id, text))
        return True

    async def _fake_persist(session_id, text, ai_meta=None, product_id=None,
                            ping_context=None):
        persisted.append((session_id, text, ping_context))
        return 5

    async def _fake_event(session_id, kind, payload, product_id=None):
        events.append((kind, payload))

    monkeypatch.setattr(retention.chat_service, "generate_retention_ping",
                        _fake_generate)
    monkeypatch.setattr(retention, "_send_ai_text", _fake_send)
    monkeypatch.setattr(retention.db, "persist_ping_turn", _fake_persist)
    monkeypatch.setattr(retention.db, "log_admin_event", _fake_event)

    ru = {"id": 7, "tg_user_id": 111, "vip_level": "gold"}
    session = {"id": "sess-1", "conv_lang": "en"}
    await retention._send_stage_up_note(
        client=None, product={"id": 1}, ru=ru, session=session,
        chat_id=111, lang="ru", new_stage=2)

    assert sent == [(111, "мы стали ближе!")]
    assert persisted and persisted[0][2].startswith("stage_up:")
    assert events and events[0][0] == "retention_stage_up"
    assert events[0][1]["new_stage"] == 2
    # The caller's session dict is not mutated (a copy carries the drift).
    assert session["conv_lang"] == "en"


async def test_send_stage_up_note_ceiling_flag(monkeypatch):
    """Advancing to the tier ceiling ⇒ the note must not promise more."""
    seen = {}

    async def _fake_generate(session, **kwargs):
        seen.update(kwargs["stage_up"])
        return None  # model failure -> note skipped gracefully

    monkeypatch.setattr(retention.chat_service, "generate_retention_ping",
                        _fake_generate)
    # "none" tier ceiling is 3: advancing to 3 leaves nothing further.
    ru = {"id": 8, "tg_user_id": 222, "vip_level": "none"}
    await retention._send_stage_up_note(
        client=None, product={"id": 1}, ru=ru, session={"id": "s2"},
        chat_id=222, lang="en", new_stage=3)
    assert seen == {"at_ceiling": True}


async def test_send_stage_up_note_send_failure_skips_persist(monkeypatch):
    persisted = []

    async def _fake_generate(session, **kwargs):
        return chat_service.PingDraft(text="hi", lang="en", photo_id=None,
                                      ai_meta={})

    async def _fake_send(client, chat_id, text, **kwargs):
        return False

    async def _fake_persist(*a, **k):
        persisted.append(a)
        return 1

    monkeypatch.setattr(retention.chat_service, "generate_retention_ping",
                        _fake_generate)
    monkeypatch.setattr(retention, "_send_ai_text", _fake_send)
    monkeypatch.setattr(retention.db, "persist_ping_turn", _fake_persist)
    await retention._send_stage_up_note(
        client=None, product={"id": 1}, ru={"id": 9, "vip_level": "gold"},
        session={"id": "s3"}, chat_id=1, lang="en", new_stage=2)
    # Nothing reached the player -> nothing may enter the transcript.
    assert persisted == []
