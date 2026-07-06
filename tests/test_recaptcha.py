"""antispam.verify_recaptcha — the fail-open/fail-closed contract.

Security-sensitive: the only place a token is skipped is dev mode (no secret).
Every other path with a secret set must fail CLOSED — missing token, verifier
outage, unsuccessful verification, and a below-threshold score.
"""
from __future__ import annotations

import antispam
import config
import settings


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; returns a scripted verify response or raises."""

    def __init__(self, data=None, exc=None):
        self._data = data
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        if self._exc:
            raise self._exc
        return _FakeResp(self._data)


def _patch_httpx(monkeypatch, *, data=None, exc=None):
    monkeypatch.setattr(antispam.httpx, "AsyncClient",
                        lambda *a, **k: _FakeAsyncClient(data=data, exc=exc))


async def test_dev_mode_skips_when_no_secret(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "")
    out = await antispam.verify_recaptcha(token=None)
    assert out["ok"] is True and out["skipped"] is True
    assert out["reason"] == "no_secret_dev_mode"


async def test_missing_token_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "secret")
    out = await antispam.verify_recaptcha(token=None)
    assert out["ok"] is False and out["skipped"] is False
    assert out["reason"] == "missing_token"


async def test_verifier_outage_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "secret")
    _patch_httpx(monkeypatch, exc=RuntimeError("network"))
    out = await antispam.verify_recaptcha(token="tok")
    assert out["ok"] is False
    assert out["reason"].startswith("verify_error:")


async def test_unsuccessful_verification_rejected(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "secret")
    _patch_httpx(monkeypatch, data={"success": False, "score": 0.9})
    out = await antispam.verify_recaptcha(token="tok")
    assert out["ok"] is False
    assert out["reason"] == "recaptcha_failed"


async def test_low_score_rejected(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "secret")
    min_score = settings.antispam()["recaptcha_min_score"]
    _patch_httpx(monkeypatch, data={"success": True, "score": min_score - 0.1})
    out = await antispam.verify_recaptcha(token="tok")
    assert out["ok"] is False
    assert out["reason"] == "low_score"


async def test_success_above_threshold(monkeypatch):
    monkeypatch.setattr(config, "RECAPTCHA_SECRET", "secret")
    min_score = settings.antispam()["recaptcha_min_score"]
    _patch_httpx(monkeypatch, data={"success": True, "score": min_score + 0.05})
    out = await antispam.verify_recaptcha(token="tok")
    assert out["ok"] is True and out["skipped"] is False
    assert out["reason"] == "ok"
