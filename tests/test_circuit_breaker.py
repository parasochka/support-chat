"""OpenAI circuit breaker: opens after N consecutive failures and fails fast,
then a half-open trial probes recovery. Prevents a sustained OpenAI outage from
making every request pay the full failover cost + pile up coroutines."""
from __future__ import annotations

import pytest

import config
import openai_client


@pytest.fixture(autouse=True)
def _clear_breakers():
    openai_client._breakers.clear()
    yield
    openai_client._breakers.clear()


def _client(source="test-src"):
    c = openai_client.OpenAIClient.__new__(openai_client.OpenAIClient)
    c.key_source = source
    return c


class _Boom(Exception):
    pass


async def test_breaker_opens_after_threshold_and_fails_fast(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BREAKER_FAIL_THRESHOLD", 3)
    monkeypatch.setattr(config, "OPENAI_BREAKER_COOLDOWN_SEC", 30)
    c = _client()
    calls = {"n": 0}

    async def boom(*_a, **_k):
        calls["n"] += 1
        raise _Boom("outage")

    c._complete_inner = boom
    # Threshold failures reach the real call and open the breaker.
    for _ in range(3):
        with pytest.raises(_Boom):
            await c.complete([{"role": "user", "content": "x"}])
    assert calls["n"] == 3
    # Breaker now OPEN: next call fails fast without touching _complete_inner.
    with pytest.raises(openai_client.CircuitOpenError):
        await c.complete([{"role": "user", "content": "x"}])
    assert calls["n"] == 3  # no extra underlying call was made


async def test_breaker_half_open_trial_recovers(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BREAKER_FAIL_THRESHOLD", 2)
    monkeypatch.setattr(config, "OPENAI_BREAKER_COOLDOWN_SEC", 30)
    c = _client()

    async def boom(*_a, **_k):
        raise _Boom("x")

    c._complete_inner = boom
    for _ in range(2):
        with pytest.raises(_Boom):
            await c.complete([{"role": "user", "content": "x"}])
    b = openai_client._breaker_for("test-src")
    assert b.opened_at  # open
    # Age the cooldown out so the next call is a half-open trial.
    b.opened_at -= 100

    async def ok(*_a, **_k):
        return "RESULT"

    c._complete_inner = ok
    res = await c.complete([{"role": "user", "content": "x"}])
    assert res == "RESULT"
    assert b.opened_at == 0.0 and b.fails == 0  # reset on success


async def test_breaker_half_open_trial_failure_rearms(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BREAKER_FAIL_THRESHOLD", 1)
    monkeypatch.setattr(config, "OPENAI_BREAKER_COOLDOWN_SEC", 30)
    c = _client()

    async def boom(*_a, **_k):
        raise _Boom("x")

    c._complete_inner = boom
    with pytest.raises(_Boom):
        await c.complete([{"role": "user", "content": "x"}])
    b = openai_client._breaker_for("test-src")
    opened_first = b.opened_at
    assert opened_first
    b.opened_at -= 100  # cooldown elapsed -> half-open trial allowed
    with pytest.raises(_Boom):  # trial fails
        await c.complete([{"role": "user", "content": "x"}])
    # Breaker re-armed to a fresh (recent) cooldown window, not left elapsed.
    assert b.opened_at > opened_first - 100


async def test_breaker_disabled_when_threshold_zero(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_BREAKER_FAIL_THRESHOLD", 0)
    c = _client()

    async def boom(*_a, **_k):
        raise _Boom("x")

    c._complete_inner = boom
    # With the breaker disabled the underlying error always surfaces (never
    # short-circuited to CircuitOpenError), no matter how many fail.
    for _ in range(6):
        with pytest.raises(_Boom):
            await c.complete([{"role": "user", "content": "x"}])
