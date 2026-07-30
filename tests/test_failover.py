"""Two-key failover: race after switch timeout, hard error switches immediately.

Also the per-purpose timeout profiles: a background purpose ships with the race
OFF, so it never pays for the same work twice — but it still fails over when the
primary key actually breaks.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.core import config
from app.ai import openai_client


def _make_resp(text: str, tin=10, tout=5, cached=0, finish=None):
    usage = type("U", (), {
        "prompt_tokens": tin,
        "completion_tokens": tout,
        "prompt_tokens_details": type("D", (), {"cached_tokens": cached})(),
    })()
    choice = type("C", (), {
        "message": type("M", (), {"content": text})(),
        "finish_reason": finish,
    })()
    return type("R", (), {"choices": [choice], "usage": usage})()


class _FakeKey:
    """Stand-in for _KeyClient: returns/raises per script after an optional delay."""

    def __init__(self, name, *, delay=0.0, exc=None, text="ok"):
        self.name = name
        self.delay = delay
        self.exc = exc
        self.text = text
        self.calls = 0
        self.purposes = []

    async def call(self, messages, purpose="chat"):
        self.calls += 1
        self.purposes.append(purpose)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return _make_resp(self.text)


def _client_with(primary, fallback):
    c = openai_client.OpenAIClient.__new__(openai_client.OpenAIClient)
    c.primary = primary
    c.fallback = fallback
    return c


@pytest.mark.asyncio
async def test_fast_primary_wins_no_failover(monkeypatch):
    primary = _FakeKey("primary", delay=0.0, text="primary-answer")
    fallback = _FakeKey("fallback", delay=0.0, text="fallback-answer")
    client = _client_with(primary, fallback)

    events = []
    async def on_fo(sid, reason): events.append(reason)

    res = await client.complete([{"role": "user", "content": "x"}],
                                session_id="s", on_failover=on_fo)
    assert res.text == "primary-answer"
    assert res.key_used == "primary"
    assert events == []  # no failover when primary answers promptly


@pytest.mark.asyncio
async def test_switch_timeout_races_fallback_and_logs(monkeypatch):
    # Make the switch timeout tiny so the test is fast.
    monkeypatch.setattr(config, "OPENAI_KEY_SWITCH_TIMEOUT_SEC", 0.05)
    # Primary is slow; fallback is fast -> fallback should win the race.
    primary = _FakeKey("primary", delay=5.0, text="primary-answer")
    fallback = _FakeKey("fallback", delay=0.0, text="fallback-answer")
    client = _client_with(primary, fallback)

    events = []
    async def on_fo(sid, reason): events.append(reason)

    res = await client.complete([{"role": "user", "content": "x"}],
                                session_id="s", on_failover=on_fo)
    assert res.text == "fallback-answer"
    assert res.key_used == "fallback"
    assert "switch_timeout" in events  # failover was logged


@pytest.mark.asyncio
async def test_hard_primary_error_switches_immediately(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_KEY_SWITCH_TIMEOUT_SEC", 5.0)
    monkeypatch.setattr(config, "OPENAI_MAX_ATTEMPTS", 1)

    import openai as openai_mod
    hard = openai_mod.AuthenticationError("bad key")

    primary = _FakeKey("primary", delay=0.0, exc=hard)
    fallback = _FakeKey("fallback", delay=0.0, text="fallback-answer")
    client = _client_with(primary, fallback)

    events = []
    async def on_fo(sid, reason): events.append(reason)

    res = await client.complete([{"role": "user", "content": "x"}],
                                session_id="s", on_failover=on_fo)
    assert res.text == "fallback-answer"
    assert res.key_used == "fallback"
    assert "primary_error" in events


@pytest.mark.asyncio
async def test_background_purpose_does_not_race_the_fallback(monkeypatch):
    """A slow BACKGROUND call must not spawn a second, billable request.

    The reviewer/agent/media passes routinely run past the interactive 15s
    switch timeout — with the race on, every one of them paid for the same work
    twice (and the loser's usage is unaccountable by construction).
    """
    monkeypatch.setattr(config, "OPENAI_KEY_SWITCH_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(config, "OPENAI_REVIEW_KEY_SWITCH_TIMEOUT_SEC", 0)
    primary = _FakeKey("primary", delay=0.2, text="primary-answer")
    fallback = _FakeKey("fallback", delay=0.0, text="fallback-answer")
    client = _client_with(primary, fallback)

    events = []
    async def on_fo(sid, reason): events.append(reason)

    res = await client.complete([{"role": "user", "content": "x"}],
                                on_failover=on_fo, purpose="review")
    assert res.text == "primary-answer"  # the slow primary is simply awaited
    assert fallback.calls == 0           # no speculative second call
    assert events == []                  # and no failover was recorded
    assert primary.purposes == ["review"]


@pytest.mark.asyncio
async def test_background_purpose_still_fails_over_on_error(monkeypatch):
    """Race off is not failover off — invariant §5 still holds."""
    monkeypatch.setattr(config, "OPENAI_REVIEW_KEY_SWITCH_TIMEOUT_SEC", 0)
    monkeypatch.setattr(config, "OPENAI_MAX_ATTEMPTS", 1)

    import openai as openai_mod
    primary = _FakeKey("primary", exc=openai_mod.AuthenticationError("bad key"))
    fallback = _FakeKey("fallback", text="fallback-answer")
    client = _client_with(primary, fallback)

    events = []
    async def on_fo(sid, reason): events.append(reason)

    res = await client.complete([{"role": "user", "content": "x"}],
                                on_failover=on_fo, purpose="review")
    assert res.text == "fallback-answer"
    assert res.key_used == "fallback"
    assert "primary_error" in events
    assert fallback.purposes == ["review"]


@pytest.mark.asyncio
async def test_interactive_purpose_keeps_racing(monkeypatch):
    """The chat profile is untouched: a silent primary is still raced."""
    monkeypatch.setattr(config, "OPENAI_KEY_SWITCH_TIMEOUT_SEC", 0.05)
    primary = _FakeKey("primary", delay=5.0, text="primary-answer")
    fallback = _FakeKey("fallback", delay=0.0, text="fallback-answer")
    client = _client_with(primary, fallback)

    res = await client.complete([{"role": "user", "content": "x"}],
                                purpose="chat")
    assert res.key_used == "fallback"
    assert fallback.purposes == ["chat"]


@pytest.mark.asyncio
async def test_no_fallback_configured_uses_primary_only():
    primary = _FakeKey("primary", delay=0.0, text="only-primary")
    client = _client_with(primary, None)
    res = await client.complete([{"role": "user", "content": "x"}])
    assert res.text == "only-primary"
    assert res.key_used == "primary"


def test_is_truncated_empty_detection():
    # Reasoning ate the whole budget: length + empty -> retry-worthy.
    assert openai_client._is_truncated_empty(_make_resp("", finish="length")) is True
    assert openai_client._is_truncated_empty(_make_resp("   ", finish="length")) is True
    # Normal completions are never flagged.
    assert openai_client._is_truncated_empty(_make_resp("hi", finish="stop")) is False
    assert openai_client._is_truncated_empty(_make_resp("hi", finish="length")) is False
    assert openai_client._is_truncated_empty(_make_resp("", finish="stop")) is False
    # Defensive against odd shapes (no finish_reason attr at all).
    assert openai_client._is_truncated_empty(_make_resp("")) is False


@pytest.mark.asyncio
async def test_empty_truncated_reply_retries_with_larger_budget(monkeypatch):
    budgets: list[int] = []

    async def _create(**kwargs):
        budgets.append(kwargs["max_completion_tokens"])
        if len(budgets) == 1:
            # First try: hidden reasoning consumed the whole budget -> empty.
            return _make_resp("", tout=700, finish="length")
        return _make_resp("real answer", tout=120, finish="stop")

    import types as _types
    fake_client = _types.SimpleNamespace(
        chat=_types.SimpleNamespace(
            completions=_types.SimpleNamespace(create=_create)
        )
    )
    monkeypatch.setattr(openai_client.settings, "model", lambda: {
        "model": "gpt-5-mini", "max_output_tokens": 700,
        "request_timeout_sec": 40, "reasoning_effort": "low",
        "verbosity": "low",
    })

    kc = openai_client._KeyClient.__new__(openai_client._KeyClient)
    kc.name = "primary"
    kc._sem = asyncio.Semaphore(1)
    kc._pending_extra_usage = {}
    kc.client = fake_client

    resp = await kc.call([{"role": "user", "content": "x"}])
    assert resp.choices[0].message.content == "real answer"
    # Retried exactly once, with a larger budget the second time.
    assert len(budgets) == 2
    assert budgets[0] == 700
    assert budgets[1] >= openai_client._MIN_RETRY_OUTPUT_TOKENS

    # The discarded first attempt still billed its tokens (700 of hidden
    # reasoning): _result must fold them into the returned usage so cost
    # accounting covers BOTH calls.
    client = openai_client.OpenAIClient.__new__(openai_client.OpenAIClient)
    res = client._result(resp, kc, started=0.0)
    assert res.tokens_out == 120 + 700
    assert res.tokens_in == 10 + 10
    assert kc._pending_extra_usage == {}  # consumed exactly once


@pytest.mark.asyncio
async def test_non_empty_reply_does_not_retry(monkeypatch):
    budgets: list[int] = []

    async def _create(**kwargs):
        budgets.append(kwargs["max_completion_tokens"])
        return _make_resp("answered", tout=120, finish="stop")

    import types as _types
    fake_client = _types.SimpleNamespace(
        chat=_types.SimpleNamespace(
            completions=_types.SimpleNamespace(create=_create)
        )
    )
    monkeypatch.setattr(openai_client.settings, "model", lambda: {
        "model": "gpt-5-mini", "max_output_tokens": 700,
        "request_timeout_sec": 40, "reasoning_effort": "", "verbosity": "",
    })

    kc = openai_client._KeyClient.__new__(openai_client._KeyClient)
    kc.name = "primary"
    kc._sem = asyncio.Semaphore(1)
    kc._pending_extra_usage = {}
    kc.client = fake_client

    resp = await kc.call([{"role": "user", "content": "x"}])
    assert resp.choices[0].message.content == "answered"
    assert len(budgets) == 1  # no retry


@contextlib.contextmanager
def _current_model(model_id):
    """Pin the hot `model` setting — the ONE thing that picks a price now."""
    orig = openai_client.settings.model
    openai_client.settings.model = lambda: {"model": model_id}
    try:
        yield
    finally:
        openai_client.settings.model = orig


def test_cost_priced_from_the_current_model():
    with _current_model("gpt-5.4-mini"):
        cost = openai_client.compute_cost(tokens_in=1_000_000, tokens_out=0,
                                          cached_in=0)
        assert cost == pytest.approx(0.75)
        # cached tokens priced lower
        cost2 = openai_client.compute_cost(tokens_in=1_000_000, tokens_out=0,
                                           cached_in=1_000_000)
        assert cost2 == pytest.approx(0.075)


def test_cost_follows_a_model_switch():
    """Same usage, different configured model ⇒ different cost, no arg passed.

    This is the whole point of dropping the per-call model: one price — the
    current one — prices everything the service reports.
    """
    usage = dict(tokens_in=1_000_000, tokens_out=1_000_000, cached_in=0)
    with _current_model("gpt-5.5"):
        assert openai_client.compute_cost(**usage) == pytest.approx(35.0)
    with _current_model("gpt-5.6-luna"):
        assert openai_client.compute_cost(**usage) == pytest.approx(1.40)


def test_cost_snapshot_model_falls_back_to_alias():
    with _current_model("gpt-5.5-2026-06-23"):
        assert openai_client.compute_cost(
            tokens_in=1_000_000, tokens_out=1_000_000, cached_in=0
        ) == pytest.approx(35.0)


def test_cost_unknown_model_zero():
    with _current_model("nonexistent"):
        assert openai_client.compute_cost(100, 100, 0) == 0.0
        assert openai_client.current_pricing() == (0.0, 0.0, 0.0)
