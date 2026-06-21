"""Admin auth: login issues a token, bad password is logged + rate-limited,
require_admin blocks unauthenticated calls."""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

import antispam
import auth
import config
import db
from api import admin_auth


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "s3cret")
    antispam.reset_state()
    logged = []

    async def _log(sid, type_, payload=None):
        logged.append((type_, payload))

    monkeypatch.setattr(db, "log_admin_event", _log)
    return logged


def _req(ip="9.9.9.9"):
    return types.SimpleNamespace(
        headers={"x-forwarded-for": ip},
        client=types.SimpleNamespace(host=ip),
    )


async def test_login_success_issues_admin_token():
    resp = await admin_auth.login(_req(), admin_auth.AdminLogin(password="s3cret"))
    assert resp.status_code == 200
    import json
    token = json.loads(resp.body)["token"]
    payload = auth.verify_admin_token(token)
    assert payload["role"] == "owner"


async def test_bad_password_logged_and_rejected(_setup):
    resp = await admin_auth.login(_req(), admin_auth.AdminLogin(password="nope"))
    assert resp.status_code == 401
    assert any(t == "admin_login_failed" for t, _ in _setup)


async def test_login_rate_limited(monkeypatch, _setup):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_PER_IP", 3)
    for _ in range(3):
        await admin_auth.login(_req("5.5.5.5"), admin_auth.AdminLogin(password="x"))
    resp = await admin_auth.login(_req("5.5.5.5"), admin_auth.AdminLogin(password="x"))
    assert resp.status_code == 429


async def test_require_admin_blocks_unauthenticated():
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin(authorization=None)
    assert exc.value.status_code == 401


async def test_require_admin_disabled_when_no_password(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", None)
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin(authorization="Bearer whatever")
    assert exc.value.status_code == 503


async def test_require_admin_accepts_valid_token():
    token = auth.issue_admin_token(role="owner")
    payload = await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert payload["role"] == "owner"
