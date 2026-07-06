"""openai_client backoff + error classification (invariant §5).

test_failover.py covers the two-key race; this covers the per-key retry pacing:
which SDK errors are hard (fail over now) vs. transient (retry with backoff),
Retry-After parsing, and _call_with_backoff's retry / immediate-raise /
exhaustion behaviour. Sleeps are stubbed so the tests are instant.
"""
from __future__ import annotations

import asyncio
import types

import openai_client

_MOD = openai_client._openai_mod  # the (stubbed in tests) openai module


def _make_resp(text="ok"):
    usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1,
                           "prompt_tokens_details": type("D", (), {"cached_tokens": 0})()})()
    choice = type("C", (), {"message": type("M", (), {"content": text})(),
                            "finish_reason": "stop"})()
    return type("R", (), {"choices": [choice], "usage": usage})()


class _ScriptedKey:
    """Raises the queued exceptions in order, then returns a response."""

    def __init__(self, name, script):
        self.name = name
        self._script = list(script)
        self.calls = 0

    async def call(self, messages):
        self.calls += 1
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
        return _make_resp()


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------
def test_hard_errors_classified():
    assert openai_client._is_hard_error(_MOD.AuthenticationError("x")) is True
    assert openai_client._is_hard_error(_MOD.NotFoundError("x")) is True
    assert openai_client._is_hard_error(_MOD.RateLimitError("x")) is False


def test_transient_errors_classified():
    assert openai_client._is_transient(_MOD.RateLimitError("x")) is True
    assert openai_client._is_transient(_MOD.APITimeoutError("x")) is True
    assert openai_client._is_transient(asyncio.TimeoutError()) is True
    assert openai_client._is_transient(_MOD.AuthenticationError("x")) is False


def test_retry_after_seconds_parsing():
    def _exc_with_header(value):
        return types.SimpleNamespace(
            response=types.SimpleNamespace(headers={"retry-after": value}))
    assert openai_client._retry_after_seconds(_exc_with_header("2.5")) == 2.5
    assert openai_client._retry_after_seconds(_exc_with_header("nope")) is None
    # no response / no headers -> None
    assert openai_client._retry_after_seconds(RuntimeError("plain")) is None


# ---------------------------------------------------------------------------
# _call_with_backoff
# ---------------------------------------------------------------------------
async def test_transient_then_success_retries(monkeypatch):
    slept: list = []

    async def _sleep(d):
        slept.append(d)
    monkeypatch.setattr(openai_client.asyncio, "sleep", _sleep)

    kc = _ScriptedKey("primary", [_MOD.RateLimitError("429")])
    resp = await openai_client._call_with_backoff(kc, [{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "ok"
    assert kc.calls == 2       # one failure, one success
    assert len(slept) == 1     # backed off once between attempts


async def test_hard_error_raises_immediately(monkeypatch):
    async def _sleep(d):  # pragma: no cover - must not be reached
        raise AssertionError("hard error should not back off")
    monkeypatch.setattr(openai_client.asyncio, "sleep", _sleep)

    kc = _ScriptedKey("primary", [_MOD.AuthenticationError("bad key")])
    try:
        await openai_client._call_with_backoff(kc, [{"role": "user", "content": "hi"}])
        raised = False
    except Exception as exc:  # noqa: BLE001
        raised = isinstance(exc, _MOD.AuthenticationError)
    assert raised is True
    assert kc.calls == 1       # no retry on a hard error


async def test_exhaustion_raises_last_error(monkeypatch):
    async def _sleep(d):
        return None
    monkeypatch.setattr(openai_client.asyncio, "sleep", _sleep)
    monkeypatch.setattr(openai_client.settings, "model",
                        lambda: {"max_attempts": 3})

    kc = _ScriptedKey("primary", [_MOD.RateLimitError("1"),
                                  _MOD.RateLimitError("2"),
                                  _MOD.RateLimitError("3")])
    try:
        await openai_client._call_with_backoff(kc, [{"role": "user", "content": "hi"}])
        raised = False
    except _MOD.RateLimitError:
        raised = True
    assert raised is True
    assert kc.calls == 3       # exactly max_attempts tries
