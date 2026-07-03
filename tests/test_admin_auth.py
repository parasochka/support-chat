"""Admin auth: named-user login issues a token, bad credentials are logged +
rate-limited, require_admin blocks unauthenticated calls."""
from __future__ import annotations

import json
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
    antispam.reset_state()
    logged = []

    async def _log(sid, type_, payload=None):
        logged.append((type_, payload))

    # A single named admin account ("a@example.com" / "s3cret-pw") stands in for
    # the DB; everyone else is unknown.
    users = {
        "a@example.com": {
            "email": "a@example.com",
            "password_hash": auth.hash_password("s3cret-pw"),
            "role": "admin",
            "active": True,
        }
    }

    async def _get_user(email):
        return users.get(email)

    async def _memberships(email):
        # Mirror the boot migration: a legacy account carries one GLOBAL
        # membership with its old role.
        user = users.get(email)
        if not user:
            return []
        return [{"id": 1, "email": email, "scope_type": "global",
                 "partner_id": None, "product_id": None, "role": user["role"]}]

    monkeypatch.setattr(db, "log_admin_event", _log)
    monkeypatch.setattr(db, "get_admin_user", _get_user)
    monkeypatch.setattr(db, "memberships_for", _memberships)
    return logged


def _req(ip="9.9.9.9"):
    return types.SimpleNamespace(
        headers={"x-forwarded-for": ip},
        client=types.SimpleNamespace(host=ip),
    )


async def test_login_success_issues_admin_token():
    resp = await admin_auth.login(
        _req(), admin_auth.AdminLogin(email="a@example.com", password="s3cret-pw"))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    payload = auth.verify_admin_token(body["token"])
    assert payload["role"] == "admin"
    assert payload["email"] == "a@example.com"


async def test_missing_email_rejected():
    resp = await admin_auth.login(_req(), admin_auth.AdminLogin(password="s3cret-pw"))
    assert resp.status_code == 400


async def test_bad_password_logged_and_rejected(_setup):
    resp = await admin_auth.login(
        _req(), admin_auth.AdminLogin(email="a@example.com", password="nope"))
    assert resp.status_code == 401
    assert any(t == "admin_login_failed" for t, _ in _setup)


async def test_unknown_user_rejected():
    resp = await admin_auth.login(
        _req(), admin_auth.AdminLogin(email="ghost@example.com", password="whatever"))
    assert resp.status_code == 401


async def test_login_rate_limited(monkeypatch, _setup):
    monkeypatch.setattr(config, "RATE_LIMIT_MAX_PER_IP", 3)
    for _ in range(3):
        await admin_auth.login(
            _req("5.5.5.5"), admin_auth.AdminLogin(email="a@example.com", password="x"))
    resp = await admin_auth.login(
        _req("5.5.5.5"), admin_auth.AdminLogin(email="a@example.com", password="x"))
    assert resp.status_code == 429


async def test_require_admin_blocks_unauthenticated():
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin(authorization=None)
    assert exc.value.status_code == 401


async def test_require_admin_accepts_valid_token():
    token = auth.issue_admin_token(role="admin", email="a@example.com")
    payload = await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert payload["role"] == "admin"
