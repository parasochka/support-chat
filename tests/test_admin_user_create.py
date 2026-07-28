"""Users tab create/update path: the handler must hash the password itself.

Regression: `app/api/admin.py` called `auth.hash_password(...)` without importing
`auth`, so every "Create user" (and password update) raised NameError → an
unhandled 500 in the Users tab. These tests exercise the handlers with the DB
helpers stubbed, so a missing import (or any pre-DB crash) fails the suite
instead of only surfacing as a 500 in production.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core import auth
from app.core import db
from app.api import admin as admin_api
from app.api.admin import UserCreate, UserUpdate


# A caller with GLOBAL admin reach (the membership shape require_admin builds).
_GLOBAL_ADMIN = {
    "role": "admin", "email": "boss@nowplix.com",
    "memberships": [{"id": 1, "email": "boss@nowplix.com", "scope_type": "global",
                     "partner_id": None, "product_id": None, "role": "admin"}],
}


def _stub_db(monkeypatch):
    created: dict = {}
    memberships: dict[str, list] = {}

    async def get_admin_user(email):
        return created.get(email)

    async def create_admin_user(email, password_hash, role):
        # Store the hash so the test can assert the handler hashed (never stored
        # plaintext) the password before it reached the DB layer.
        row = {"email": email, "password_hash": password_hash, "role": role,
               "active": True, "created_at": None, "updated_at": None}
        created[email] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def update_admin_user(email, *, role=None, active=None, password_hash=None):
        row = created[email]
        if password_hash is not None:
            row["password_hash"] = password_hash
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def add_membership(email, scope_type, partner_id, product_id, role):
        m = {"id": len(memberships.get(email, [])) + 1, "email": email,
             "scope_type": scope_type, "partner_id": partner_id,
             "product_id": product_id, "role": role}
        memberships.setdefault(email, []).append(m)
        return m

    async def memberships_for(email):
        return memberships.get(email, [])

    async def log_admin_event(*a, **k):
        return None

    monkeypatch.setattr(db, "get_admin_user", get_admin_user)
    monkeypatch.setattr(db, "create_admin_user", create_admin_user)
    monkeypatch.setattr(db, "update_admin_user", update_admin_user)
    monkeypatch.setattr(db, "add_membership", add_membership)
    monkeypatch.setattr(db, "memberships_for", memberships_for)
    monkeypatch.setattr(db, "log_admin_event", log_admin_event)
    return created


async def test_create_user_hashes_password(monkeypatch):
    created = _stub_db(monkeypatch)
    admin = _GLOBAL_ADMIN
    res = await admin_api.create_user(
        UserCreate(email="artem@nowplix.com", password="G#EwiXCcvgo4", role="admin"),
        admin=admin)
    assert res.status_code == 200
    stored = created["artem@nowplix.com"]["password_hash"]
    # The handler must hash, never store the plaintext.
    assert stored != "G#EwiXCcvgo4"
    assert auth.verify_password("G#EwiXCcvgo4", stored)


async def test_update_user_password_hashes(monkeypatch):
    created = _stub_db(monkeypatch)
    admin = _GLOBAL_ADMIN
    await admin_api.create_user(
        UserCreate(email="a@b.com", password="initialpw1", role="manager"),
        admin=admin)
    await admin_api.update_user(
        "a@b.com", UserUpdate(password="rotatedpw9"), admin=admin)
    stored = created["a@b.com"]["password_hash"]
    assert auth.verify_password("rotatedpw9", stored)


# A caller scoped to a SINGLE product: coarse role "admin" (so it clears
# require_admin_write) but NO global membership.
_PRODUCT_ADMIN = {
    "role": "admin", "email": "p@nowplix.com",
    "memberships": [{"id": 9, "email": "p@nowplix.com", "scope_type": "product",
                     "partner_id": None, "product_id": 11, "role": "admin"}],
}


async def test_self_flat_role_update_requires_global_write(monkeypatch):
    """C1 regression: a product-scoped admin must NOT be able to self-grant a
    GLOBAL admin membership by PUT-ing its own account with role='admin'. The
    flat-role update writes a global membership, so it always needs global write
    — the self-edit branch must not bypass that check."""
    created = _stub_db(monkeypatch)
    created["p@nowplix.com"] = {
        "email": "p@nowplix.com", "password_hash": "x", "role": "admin",
        "active": True, "created_at": None, "updated_at": None,
    }
    with pytest.raises(HTTPException) as exc:
        await admin_api.update_user(
            "p@nowplix.com", UserUpdate(role="admin"), admin=_PRODUCT_ADMIN)
    assert exc.value.status_code == 403


async def test_self_password_only_update_still_allowed(monkeypatch):
    """A product-scoped admin can still change ITS OWN password (no role change,
    so no global-membership write) — the C1 fix must not lock self-service out."""
    created = _stub_db(monkeypatch)
    created["p@nowplix.com"] = {
        "email": "p@nowplix.com", "password_hash": "x", "role": "admin",
        "active": True, "created_at": None, "updated_at": None,
    }
    await admin_api.update_user(
        "p@nowplix.com", UserUpdate(password="newpassw0rd"), admin=_PRODUCT_ADMIN)
    assert auth.verify_password("newpassw0rd", created["p@nowplix.com"]["password_hash"])


async def test_revoke_membership_checks_reach_over_the_target_account(monkeypatch):
    """Revoking must apply the same TARGET-account reach check as granting.

    It only authorized the revoked membership's own scope, so a product-scoped
    admin could strip that product's role off an account otherwise outside its
    reach — a global admin who also holds a product membership, say. No
    privilege escalation, but it mutates the rights of an account the caller may
    not administer, which is exactly what `_can_manage_user` exists to prevent.
    """
    _stub_db(monkeypatch)
    victim = "global.boss@nowplix.com"
    # The victim is a GLOBAL admin who also carries a membership on product 7.
    victim_memberships = [
        {"id": 10, "email": victim, "scope_type": "global",
         "partner_id": None, "product_id": None, "role": "admin"},
        {"id": 11, "email": victim, "scope_type": "product",
         "partner_id": 1, "product_id": 7, "role": "admin"},
    ]

    async def memberships_for(email):
        return victim_memberships if email == victim else []

    async def get_membership(mid):
        return next((m for m in victim_memberships if m["id"] == mid), None)

    deleted: list[int] = []

    async def delete_membership(mid):
        deleted.append(mid)
        return True

    async def get_admin_user(email):
        return {"email": email, "active": True}

    async def get_product(pid):
        return {"id": pid, "partner_id": 1, "active": True}

    monkeypatch.setattr(db, "memberships_for", memberships_for)
    monkeypatch.setattr(db, "get_membership", get_membership)
    monkeypatch.setattr(db, "get_product", get_product)
    monkeypatch.setattr(db, "delete_membership", delete_membership)
    monkeypatch.setattr(db, "get_admin_user", get_admin_user)

    # The caller administers product 7 only — the membership's own scope, so the
    # scope check alone would have let this through.
    product_admin = {
        "role": "admin", "email": "p7@nowplix.com",
        "memberships": [{"id": 2, "email": "p7@nowplix.com",
                         "scope_type": "product", "partner_id": 1,
                         "product_id": 7, "role": "admin"}],
    }
    with pytest.raises(HTTPException) as exc:
        await admin_api.revoke_membership(victim, 11, admin=product_admin)
    assert exc.value.status_code == 403
    assert deleted == []

    # A global admin has reach over the whole account and may revoke.
    result = await admin_api.revoke_membership(victim, 11, admin=_GLOBAL_ADMIN)
    assert result.status_code == 200
    assert deleted == [11]
