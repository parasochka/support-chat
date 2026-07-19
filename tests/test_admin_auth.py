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
    # Login throttling uses its OWN dedicated allowance, decoupled from the widget
    # per-IP knob (so widening the widget limit can't widen the login CPU budget).
    monkeypatch.setattr(config, "ADMIN_LOGIN_RATE_LIMIT", 3)
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


def test_refresh_admin_token_slides_past_halflife():
    # A token minted well into its second half must refresh (new full TTL);
    # a fresh one must not.
    now = int(__import__("time").time())
    old = {"iat": now - 6000, "exp": now + 100, "role": "admin"}  # ~98% elapsed
    fresh = {"iat": now - 10, "exp": now + 3600, "role": "admin"}  # just minted
    assert auth.refresh_admin_token(old, "admin", "a@example.com") is not None
    assert auth.refresh_admin_token(fresh, "admin", "a@example.com") is None
    # A refreshed token is valid and carries a strictly later expiry.
    new_tok = auth.refresh_admin_token(old, "admin", "a@example.com")
    new_payload = auth.verify_admin_token(new_tok)
    assert new_payload["exp"] > old["exp"]
    assert new_payload["email"] == "a@example.com"


def _stale_admin_token(email="a@example.com"):
    """A validly-signed admin token whose iat/exp put it past half-life (so
    refresh_admin_token fires) but not yet expired."""
    import time as _t
    now = int(_t.time())
    header = auth._b64url_encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = {"sub": email, "email": email, "role": "admin",
               "iat": now - 6000, "exp": now + 100}
    p64 = auth._b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = auth._b64url_encode(
        auth._sign_with(config.ADMIN_JWT_SECRET, f"{header}.{p64}".encode("ascii")))
    return f"{header}.{p64}.{sig}"


async def test_require_admin_sets_refresh_header_when_stale():
    # A near-expired token drives require_admin to emit X-Refresh-Token so the
    # SPA can slide the session forward.
    from fastapi import Response

    resp = Response()
    payload = await admin_auth.require_admin(
        authorization=f"Bearer {_stale_admin_token()}", response=resp)
    assert payload["role"] == "admin"
    assert resp.headers.get("X-Refresh-Token")
    # And a freshly-minted token does NOT trigger a refresh.
    resp2 = Response()
    fresh = auth.issue_admin_token(role="admin", email="a@example.com")
    await admin_auth.require_admin(authorization=f"Bearer {fresh}", response=resp2)
    assert resp2.headers.get("X-Refresh-Token") is None


async def test_login_rehashes_legacy_hash(monkeypatch, _setup):
    """Rehash-on-login: a legacy weaker hash is transparently upgraded to the
    current iteration factor when the plaintext is in hand (the only moment it is,
    since there is no reset flow)."""
    legacy = auth.hash_password("s3cret-pw", iterations=200_000)
    assert auth.password_needs_rehash(legacy)
    written = {}

    async def _get_user(email):
        if email != "a@example.com":
            return None
        return {"email": email, "password_hash": legacy,
                "role": "admin", "active": True}

    async def _update(email, *, password_hash=None, **kw):
        written["hash"] = password_hash
        return {"email": email}

    monkeypatch.setattr(db, "get_admin_user", _get_user)
    monkeypatch.setattr(db, "update_admin_user", _update)

    resp = await admin_auth.login(
        _req(), admin_auth.AdminLogin(email="a@example.com", password="s3cret-pw"))
    assert resp.status_code == 200
    assert written.get("hash") and not auth.password_needs_rehash(written["hash"])


async def test_token_revoked_after_password_change(monkeypatch, _setup):
    """The pwv binding revokes an outstanding token once the account's password
    hash changes (the natural response to a leak), without deactivating the
    account or rotating the deploy secret."""
    resp = await admin_auth.login(
        _req(), admin_auth.AdminLogin(email="a@example.com", password="s3cret-pw"))
    token = json.loads(resp.body)["token"]

    # Still valid against the unchanged hash.
    who = await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert who["email"] == "a@example.com"

    # Password rotated -> stored hash differs -> pwv mismatch -> token rejected.
    async def _rotated(email):
        return {"email": email,
                "password_hash": auth.hash_password("a-brand-new-password"),
                "role": "admin", "active": True}
    monkeypatch.setattr(db, "get_admin_user", _rotated)

    with pytest.raises(HTTPException) as ei:
        await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert ei.value.status_code == 401
