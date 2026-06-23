"""Two-key failover: race after switch timeout, hard error switches immediately."""
from __future__ import annotations

import asyncio

import pytest

import config
import openai_client


def _make_resp(text: str, tin=10, tout=5, cached=0):
    usage = type("U", (), {
        "prompt_tokens": tin,
        "completion_tokens": tout,
        "prompt_tokens_details": type("D", (), {"cached_tokens": cached})(),
    })()
    choice = type("C", (), {"message": type("M", (), {"content": text})()})()
    return type("R", (), {"choices": [choice], "usage": usage})()


class _FakeKey:
    """Stand-in for _KeyClient: returns/raises per script after an optional delay."""

    def __init__(self, name, *, delay=0.0, exc=None, text="ok"):
        self.name = name
        self.delay = delay
        self.exc = exc
        self.text = text
        self.calls = 0

    async def call(self, messages):
        self.calls += 1
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
async def test_no_fallback_configured_uses_primary_only():
    primary = _FakeKey("primary", delay=0.0, text="only-primary")
    client = _client_with(primary, None)
    res = await client.complete([{"role": "user", "content": "x"}])
    assert res.text == "only-primary"
    assert res.key_used == "primary"


def test_cost_computation_known_model():
    cost = openai_client.compute_cost("gpt-5.4-mini", tokens_in=1_000_000,
                                      tokens_out=0, cached_in=0)
    assert cost == pytest.approx(0.75)
    # cached tokens priced lower
    cost2 = openai_client.compute_cost("gpt-5.4-mini", tokens_in=1_000_000,
                                       tokens_out=0, cached_in=1_000_000)
    assert cost2 == pytest.approx(0.075)


def test_cost_computation_snapshot_model_falls_back_to_alias():
    cost = openai_client.compute_cost("gpt-5.5-2026-06-23", tokens_in=1_000_000,
                                      tokens_out=1_000_000, cached_in=0)
    assert cost == pytest.approx(35.0)


def test_cost_unknown_model_zero():
    assert openai_client.compute_cost("nonexistent", 100, 100, 0) == 0.0
