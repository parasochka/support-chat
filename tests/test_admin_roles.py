"""Role guard: managers are read-only (require_admin_write 403s them)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import auth
import db
from api import admin_auth


def _stub_user(monkeypatch, role, active=True):
    async def _get_user(email):
        return {"email": email, "role": role, "active": active,
                "password_hash": "x"}
    monkeypatch.setattr(db, "get_admin_user", _get_user)


async def _guard_for(role, email=None):
    token = auth.issue_admin_token(role=role, email=email)
    admin = await admin_auth.require_admin(authorization=f"Bearer {token}")
    return await admin_auth.require_admin_write(admin=admin)


async def test_named_admin_may_write(monkeypatch):
    _stub_user(monkeypatch, "admin")
    res = await _guard_for("admin", email="a@example.com")
    assert res["role"] == "admin"
    assert res["email"] == "a@example.com"


async def test_manager_blocked_from_writes(monkeypatch):
    _stub_user(monkeypatch, "manager")
    with pytest.raises(HTTPException) as exc:
        await _guard_for("manager", email="m@example.com")
    assert exc.value.status_code == 403


async def test_deactivated_account_rejected_even_with_valid_token(monkeypatch):
    """A JWT has no revocation — require_admin re-checks admin_users.active, so
    deactivating an account cuts its access immediately, not at token expiry."""
    _stub_user(monkeypatch, "admin", active=False)
    token = auth.issue_admin_token(role="admin", email="a@example.com")
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert exc.value.status_code == 401


async def test_db_role_overrides_stale_token_role(monkeypatch):
    """A demotion applies immediately: the DB role is authoritative over the
    role claim still riding in an older token."""
    _stub_user(monkeypatch, "manager")
    token = auth.issue_admin_token(role="admin", email="a@example.com")
    admin = await admin_auth.require_admin(authorization=f"Bearer {token}")
    assert admin["role"] == "manager"
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin_write(admin=admin)
    assert exc.value.status_code == 403
