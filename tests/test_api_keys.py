"""Service API keys (`sak_...` Bearer tokens) — the machine credential path.

require_admin accepts a service key alongside the human JWT: the key's single
scoped role becomes a synthetic membership, so every scope helper answers the
same way it would for a human account with that membership.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import db
from api import admin_auth


def _key(**over):
    base = {"id": 5, "name": "master-panel", "token_hint": "abcd",
            "role": "manager", "scope_type": "global", "partner_id": None,
            "product_id": None, "active": True}
    base.update(over)
    return base


def _wire(monkeypatch, key):
    async def _by_token(token):
        return dict(key) if key is not None else None
    monkeypatch.setattr(db, "get_admin_api_key_by_token", _by_token)


async def test_sak_token_resolves_to_synthetic_membership(monkeypatch):
    _wire(monkeypatch, _key(role="admin"))
    admin = await admin_auth.require_admin("Bearer sak_something")
    assert admin["email"] == "apikey:master-panel"
    assert admin["role"] == "admin"
    assert admin["memberships"] == [{"role": "admin", "scope_type": "global",
                                     "partner_id": None, "product_id": None}]
    assert admin_auth.global_role(admin) == "admin"


async def test_manager_key_is_read_only(monkeypatch):
    _wire(monkeypatch, _key(role="manager"))
    admin = await admin_auth.require_admin("Bearer sak_x")
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin_write(admin)
    assert exc.value.status_code == 403


async def test_unknown_or_inactive_key_is_401(monkeypatch):
    _wire(monkeypatch, None)  # get_admin_api_key_by_token filters on active
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_admin("Bearer sak_revoked")
    assert exc.value.status_code == 401


async def test_product_scoped_key_reaches_only_its_product(monkeypatch):
    _wire(monkeypatch, _key(role="admin", scope_type="product", product_id=7))

    async def _get_product(pid):
        return ({"id": pid, "partner_id": 1} if pid in (7, 8) else None)
    monkeypatch.setattr(db, "get_product", _get_product)

    admin = await admin_auth.require_admin("Bearer sak_x")
    assert await admin_auth.role_for_product(admin, 7) == "admin"
    assert await admin_auth.role_for_product(admin, 8) is None
    with pytest.raises(HTTPException):
        admin_auth.require_global_write(admin)


def test_token_hashing_is_stable():
    h1 = db._hash_api_token("sak_abc")
    h2 = db._hash_api_token("sak_abc")
    assert h1 == h2 and len(h1) == 64
    assert db._hash_api_token("sak_abd") != h1
