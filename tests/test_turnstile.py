"""antispam.verify_turnstile — the advisory fail-open contract.

Turnstile is deliberately ADVISORY: the challenges.cloudflare.com script can be
blocked in some regions/networks, and a player must never lose the chat over
that. A missing token and a verifier outage SKIP the check (ok=True,
skipped=True); the ONLY blocking path is an explicit "invalid token" verdict
from Cloudflare (a definitive bot signal, not a loading problem).
"""
from __future__ import annotations

import antispam
import config


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
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "")
    out = await antispam.verify_turnstile(token=None)
    assert out["ok"] is True and out["skipped"] is True
    assert out["reason"] == "no_secret_dev_mode"


async def test_missing_token_skips_fail_open(monkeypatch):
    """The client couldn't obtain a token (Turnstile blocked/slow/unreachable in
    the player's region) — the check is SKIPPED, never a block."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    out = await antispam.verify_turnstile(token=None)
    assert out["ok"] is True and out["skipped"] is True
    assert out["reason"] == "no_token_client_side"


async def test_verifier_outage_skips_fail_open(monkeypatch):
    """A siteverify outage must not kill session creation (advisory check)."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    _patch_httpx(monkeypatch, exc=RuntimeError("network"))
    out = await antispam.verify_turnstile(token="tok")
    assert out["ok"] is True and out["skipped"] is True
    assert out["reason"].startswith("verify_error:")


async def test_invalid_token_rejected(monkeypatch):
    """An explicit failure verdict from Cloudflare is the ONE blocking path."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    _patch_httpx(monkeypatch, data={"success": False,
                                    "error-codes": ["invalid-input-response"]})
    out = await antispam.verify_turnstile(token="tok")
    assert out["ok"] is False and out["skipped"] is False
    assert out["reason"] == "turnstile_failed:invalid-input-response"


async def test_success(monkeypatch):
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    _patch_httpx(monkeypatch, data={"success": True})
    out = await antispam.verify_turnstile(token="tok")
    assert out["ok"] is True and out["skipped"] is False
    assert out["reason"] == "ok"


# ---------------------------------------------------------------------------
# Per-product secret (each client domain runs its own Turnstile widget)
# ---------------------------------------------------------------------------
async def test_product_secret_activates_verification(monkeypatch):
    """With no env secret at all, a PRODUCT secret still turns verification on:
    an invalid-TOKEN verdict is rejected instead of dev-mode-skipping."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "")
    _patch_httpx(monkeypatch, data={"success": False,
                                    "error-codes": ["invalid-input-response"]})
    out = await antispam.verify_turnstile(token="tok", secret="product-secret")
    assert out["ok"] is False and out["reason"].startswith("turnstile_failed")


async def test_config_error_codes_fail_open(monkeypatch):
    """A mistyped/absent secret or a Cloudflare internal-error yields
    success:false WITHOUT an invalid-token verdict — that is a config/outage
    problem, not a bot, so the advisory check must SKIP (fail-open), never 403
    the whole product's chat. Only invalid-input-response / timeout-or-duplicate
    are definitive bad-token blocks."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    for codes in (["invalid-input-secret"], ["internal-error"],
                  ["missing-input-secret"], []):
        _patch_httpx(monkeypatch, data={"success": False, "error-codes": codes})
        out = await antispam.verify_turnstile(token="tok")
        assert out["ok"] is True and out["skipped"] is True, codes
        assert out["reason"].startswith("turnstile_nonblocking")


async def test_timeout_or_duplicate_token_blocks(monkeypatch):
    """A replayed/expired token (timeout-or-duplicate) is a definitive bad-token
    signal and still blocks."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "secret")
    _patch_httpx(monkeypatch, data={"success": False,
                                    "error-codes": ["timeout-or-duplicate"]})
    out = await antispam.verify_turnstile(token="tok")
    assert out["ok"] is False and out["skipped"] is False


async def test_product_secret_wins_over_env(monkeypatch):
    """The product's own secret is what gets POSTed to Cloudflare, not the env one."""
    monkeypatch.setattr(config, "TURNSTILE_SECRET", "env-secret")
    seen = {}

    class _CapturingClient(_FakeAsyncClient):
        async def post(self, url, data=None):
            seen.update(data or {})
            seen["url"] = url
            return _FakeResp({"success": True})

    monkeypatch.setattr(antispam.httpx, "AsyncClient",
                        lambda *a, **k: _CapturingClient())
    out = await antispam.verify_turnstile(token="tok", secret="product-secret")
    assert out["ok"] is True
    assert seen["secret"] == "product-secret"
    assert seen["url"] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
