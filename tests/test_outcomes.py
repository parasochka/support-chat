"""Outcome attribution — the feedback loop under the proactive stack.

Covers: the recording seam (what opens an attribution row and what must not),
the event/idle/dialogue call sites, the sweep's pacing, and the two prompt
blocks the measured data feeds — the decision call's evidence block and the
writer's "change the approach" hint.
"""
from __future__ import annotations

import pytest

from app.ai import prompts
from app.core import config
from app.core import db
from app.retention import outcomes
from app.retention import retention_v2


def _touch(**over):
    base = {"sent_at": "2026-07-20T10:00:00+00:00",
            "event_name": "deposit_confirmed", "action": "message",
            "tone": "celebrate", "with_photo": False, "with_link": False,
            "replied": False, "reply_latency_sec": None, "player_msgs": 0,
            "deposited": False, "returned": False, "settled": True}
    base.update(over)
    return base


def _capture_outcomes(monkeypatch):
    rows: list[dict] = []

    async def _record(product_id, **kw):
        rows.append(dict(kw, product_id=product_id))
        return len(rows)

    monkeypatch.setattr(db, "record_retention_outcome", _record)
    return rows


# --- recording seam --------------------------------------------------------
async def test_record_never_raises(monkeypatch):
    """Analytics must never break a send that already reached the player."""
    async def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "record_retention_outcome", _boom)
    assert await outcomes.record(1, {"id": 5, "player_id": "p1"},
                                 kind="proactive") is None


async def test_dialogue_turn_recorded_only_when_measurable(monkeypatch):
    rows = _capture_outcomes(monkeypatch)
    ru = {"id": 5, "player_id": "p1"}
    # A plain text answer inside a live conversation has no outcome to measure.
    assert await outcomes.record_dialogue_turn(1, ru, "s-1") is None
    assert rows == []
    # A photo does.
    await outcomes.record_dialogue_turn(1, ru, "s-1", photo_id=9,
                                        media_type="video")
    # …and so does a CTA button.
    await outcomes.record_dialogue_turn(1, ru, "s-1",
                                        link_url="https://x.test/slots")
    assert [r["action"] for r in rows] == ["photo", "message"]
    assert rows[0]["photo_id"] == 9 and rows[0]["media_type"] == "video"
    assert rows[1]["link_url"] == "https://x.test/slots"
    assert all(r["kind"] == "dialogue" for r in rows)
    # A dialogue row carries no cost: the turn's cost belongs to the
    # conversation, not to the button it happened to carry.
    assert all(r.get("cost_usd") is None for r in rows)


async def test_sweep_is_paced(monkeypatch):
    """A never-swept product runs immediately; a just-swept one waits.

    The "immediately" half is the regression guard: pacing used to default the
    last-sweep stamp to 0.0, and time.monotonic() counts from boot — so on a
    freshly started machine every product's FIRST sweep was skipped for the
    whole interval (which is exactly how CI, on a fresh runner, caught it).
    """
    calls = {"n": 0}

    async def _attribute(product_id, **kw):
        calls["n"] += 1
        return 3

    monkeypatch.setattr(db, "attribute_retention_outcomes", _attribute)
    monkeypatch.setattr(outcomes.time, "monotonic", lambda: 12.0)  # just booted
    outcomes._last_sweep.pop(42, None)
    first = await outcomes.run_product_attribution(42)
    second = await outcomes.run_product_attribution(42)
    assert first == {"attributed": 3}
    assert second == {"skipped": "paced"}
    assert calls["n"] == 1
    # `force` (the admin path) ignores the pacing entirely.
    forced = await outcomes.run_product_attribution(42, force=True)
    assert forced == {"attributed": 3} and calls["n"] == 2
    # …and once the interval has elapsed it is due again.
    monkeypatch.setattr(
        outcomes.time, "monotonic",
        lambda: 12.0 + config.RETENTION_OUTCOME_SWEEP_INTERVAL_SEC + 1)
    assert await outcomes.run_product_attribution(42) == {"attributed": 3}


async def test_sweep_swallows_errors(monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(db, "attribute_retention_outcomes", _boom)
    outcomes._last_sweep.pop(43, None)
    assert await outcomes.run_product_attribution(43, force=True) == {
        "error": "sweep_failed"}


def test_windows_are_deploy_constants():
    assert config.RETENTION_OUTCOME_REPLY_WINDOW_HOURS > 0
    assert config.RETENTION_OUTCOME_CONVERSION_WINDOW_HOURS > 0


# --- the event pipeline records what it sent -------------------------------
async def test_delivered_touch_opens_an_outcome_row(monkeypatch):
    from tests.test_retention_v2 import (_capture_ledger, _cfg, _evt,
                                         _patch_guard_env, _ru)
    _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)
    rows = _capture_outcomes(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _decide(product_id, ru, evt, state, guard):
        return ({"action": "photo", "tone": "celebrate", "intent": "i",
                 "reason": "r"}, 0.001)

    async def _send(product, ru, evt, decision, *, comfort, cfg):
        return True, 0.002, None, {"session_id": "s-9", "photo_id": 4,
                                   "media_type": "photo",
                                   "link_url": "https://x.test/casino"}

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(retention_v2, "_decide", _decide)
    monkeypatch.setattr(retention_v2, "_send_touch", _send)

    assert await retention_v2._process_event(
        {"id": 1}, _evt(), _cfg(v2_dry_run=False)) == "sent"
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "proactive" and row["action"] == "photo"
    assert row["event_name"] == "deposit_confirmed" and row["tone"] == "celebrate"
    assert row["photo_id"] == 4 and row["link_url"] == "https://x.test/casino"
    # The whole touch cost (decision + generation) rides on the row — that is
    # what makes cost-per-outcome meaningful.
    assert row["cost_usd"] == pytest.approx(0.003)
    assert row["decision_id"] is not None


async def test_dry_run_and_silence_record_nothing(monkeypatch):
    from tests.test_retention_v2 import (_capture_ledger, _cfg, _evt,
                                         _patch_guard_env, _ru)
    _capture_ledger(monkeypatch)
    _patch_guard_env(monkeypatch)
    rows = _capture_outcomes(monkeypatch)

    async def _ru_get(product_id, player_id):
        return _ru()

    async def _loss(product_id, player_id):
        return 0.0

    async def _silence(product_id, ru, evt, state, guard):
        return ({"action": "silence", "tone": "neutral", "intent": "",
                 "reason": "not now"}, 0.001)

    monkeypatch.setattr(db, "get_retention_user_by_player", _ru_get)
    monkeypatch.setattr(db, "player_net_loss_24h", _loss)
    monkeypatch.setattr(retention_v2, "_decide", _silence)
    await retention_v2._process_event({"id": 1}, _evt(), _cfg(v2_dry_run=False))
    assert rows == []  # nothing went out, nothing to attribute


async def test_media_type_of_reads_the_candidate_row():
    candidates = [{"id": 1, "media_type": "photo"},
                  {"id": 2, "media_type": "video"}]
    assert retention_v2._media_type_of(candidates, 2) == "video"
    assert retention_v2._media_type_of(candidates, 1) == "photo"
    assert retention_v2._media_type_of(candidates, None) is None
    assert retention_v2._media_type_of(candidates, 99) is None


# --- the prompt blocks the measurements feed -------------------------------
def test_touch_history_block_summarizes_reactions():
    block = prompts._touch_history_block([
        _touch(replied=True, reply_latency_sec=240, player_msgs=3,
               deposited=True),
        _touch(event_name="idle:bot_inactivity", action="photo",
               with_photo=True, tone=None),
    ])
    assert "PREVIOUS PROACTIVE TOUCHES" in block
    assert "HE ANSWERED in 4 min, 3 messages" in block
    assert "he deposited afterwards" in block
    assert "NO ANSWER" in block
    assert "Of his last 2 settled proactive touches, 1 got an answer." in block
    assert prompts._touch_history_block([]) == ""


def test_decision_prompt_carries_the_touch_history():
    messages = prompts.build_retention_v2_decision_messages(
        state={"user_status": "active"}, event={"event_name": "level_up"},
        recent_events=[], history_tail=[], allowed_actions=["silence", "message"],
        touch_history=[_touch(), _touch()])
    user = messages[1]["content"]
    assert "PREVIOUS PROACTIVE TOUCHES" in user
    # Without history the block simply does not appear (nothing to weigh).
    plain = prompts.build_retention_v2_decision_messages(
        state={}, event={}, recent_events=[], history_tail=[],
        allowed_actions=["silence"])
    assert "PREVIOUS PROACTIVE TOUCHES" not in plain[1]["content"]


def test_writer_hint_only_fires_on_a_streak_of_silence():
    # One unanswered touch is not evidence.
    assert prompts._touch_feedback_hint([_touch()]) == ""
    # A recent answer means contact is welcome — no hint.
    assert prompts._touch_feedback_hint(
        [_touch(replied=True), _touch(), _touch()]) == ""
    # Two settled, both ignored -> tell the writer to change the approach.
    hint = prompts._touch_feedback_hint([_touch(), _touch()])
    assert "no reply" in hint and "different subject" in hint
    # Unsettled (too recent) touches are not counted as failures.
    assert prompts._touch_feedback_hint(
        [_touch(settled=False), _touch(settled=False)]) == ""


def test_ping_prompt_carries_the_writer_hint():
    body = prompts.build_retention_ping_prompt(
        user_context={}, resolved_lang="ru", idle_days=3, reason="silence",
        intent="", touch_history=[_touch(), _touch()])
    assert "FEEDBACK:" in body
    assert "FEEDBACK:" not in prompts.build_retention_ping_prompt(
        user_context={}, resolved_lang="ru", idle_days=3, reason="silence",
        intent="")


def test_proven_links_line_is_a_hint_not_an_order():
    line = prompts._proven_links_line(["https://x.test/slots",
                                       "https://x.test/tournaments"])
    assert "https://x.test/slots" in line
    assert "not an instruction" in line and "rotation rule" in line
    assert prompts._proven_links_line([]) == ""
    assert prompts._proven_links_line(None) == ""


def test_nudge_links_ride_only_on_a_nudge_turn():
    with_nudge = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="ru", user_text="hi", play_nudge=True,
        nudge_links=["https://x.test/slots"])
    assert "https://x.test/slots" in with_nudge
    without = prompts.build_retention_dynamic_prompt(
        user_context={}, resolved_lang="ru", user_text="hi", play_nudge=False,
        nudge_links=["https://x.test/slots"])
    assert "https://x.test/slots" not in without


def test_humanize_seconds():
    assert prompts._humanize_seconds(45) == "45s"
    assert prompts._humanize_seconds(240) == "4 min"
    assert prompts._humanize_seconds(7200) == "2 h"
    assert prompts._humanize_seconds(None) == "?"
