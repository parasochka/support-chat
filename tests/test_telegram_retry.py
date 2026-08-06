"""TelegramClient._post: the single 429 rate-limit retry.

Telegram answers a rate-limited send with ok=false / error_code=429 /
parameters.retry_after. The transport honours retry_after ONCE (capped) and then
gives up to the caller's normal failure handling — it never drops the send
silently on the first 429, and never loops unboundedly.
"""
from __future__ import annotations

from app.core import http
from app.retention import telegram_transport
from app.retention.telegram_transport import TelegramClient


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for the shared pooled client; serves a scripted response list."""
    scripted: list = []
    calls: int = 0
    timeouts: list = []

    def __init__(self, *a, **k):
        pass

    async def post(self, url, **kwargs):
        payload = type(self).scripted[type(self).calls]
        type(self).calls += 1
        type(self).timeouts.append(kwargs.get("timeout"))
        return _FakeResp(payload)


def _install(monkeypatch, scripted):
    _FakeAsyncClient.scripted = scripted
    _FakeAsyncClient.calls = 0
    _FakeAsyncClient.timeouts = []
    # The transport now posts through the process-wide client
    # (app/core/http.py), so the seam is the accessor, not httpx.AsyncClient.
    fake = _FakeAsyncClient()
    monkeypatch.setattr(http, "client", lambda: fake)

    slept = []

    async def _sleep(sec):
        slept.append(sec)
    monkeypatch.setattr(telegram_transport.asyncio, "sleep", _sleep)
    return slept


async def test_429_then_success_retries_once(monkeypatch):
    slept = _install(monkeypatch, [
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}},
        {"ok": True, "result": {"message_id": 42}},
    ])
    client = TelegramClient("t")
    result, code, desc = await client.send_message_verbose(1, "hi")
    assert result == {"message_id": 42}
    assert code is None and desc is None
    assert _FakeAsyncClient.calls == 2      # retried exactly once
    assert slept == [3]                      # honoured retry_after


async def test_429_capped_wait(monkeypatch):
    slept = _install(monkeypatch, [
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 9999}},
        {"ok": True, "result": {"message_id": 1}},
    ])
    await TelegramClient("t").send_message_verbose(1, "hi")
    assert slept == [telegram_transport._MAX_RETRY_AFTER_SEC]


async def test_persistent_429_gives_up_after_one_retry(monkeypatch):
    _install(monkeypatch, [
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
    ])
    result, code, desc = await TelegramClient("t").send_message_verbose(1, "hi")
    assert result is None
    assert code == 429                       # surfaced, not looped
    assert _FakeAsyncClient.calls == 2       # one initial + one retry, no more


async def test_403_not_retried(monkeypatch):
    """A non-429 error (player blocked the bot) fails through immediately."""
    slept = _install(monkeypatch, [
        {"ok": False, "error_code": 403, "description": "bot was blocked"},
    ])
    result, code, desc = await TelegramClient("t").send_message_verbose(1, "hi")
    assert result is None and code == 403
    assert _FakeAsyncClient.calls == 1       # no retry
    assert slept == []
