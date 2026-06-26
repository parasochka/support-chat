"""Role guard: managers are read-only (require_admin_write 403s them)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import auth
from api import admin_auth


async def _guard_for(role, email=None):
    token = auth.issue_admin_token(role=role, email=email)
    admin = await admin_auth.require_admin(authorization=f"Bearer {token}")
    return await admin_auth.require_admin_write(admin=admin)


async def test_named_admin_may_write():
    res = await _guard_for("admin", email="a@example.com")
    assert res["role"] == "admin"
    assert res["email"] == "a@example.com"


async def test_manager_blocked_from_writes():
    with pytest.raises(HTTPException) as exc:
        await _guard_for("manager", email="m@example.com")
    assert exc.value.status_code == 403
