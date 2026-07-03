"""Membership-based admin authorization: global / partner / product scopes.

The helpers in api/admin_auth.py answer "what may this account read/write?"
from its admin_memberships rows; these tests pin the reach rules — a product
manager can't read a sibling product, a partner admin covers all the partner's
products, a global account covers everything.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import db
from api import admin_auth


def _admin(*memberships, email="u@example.com"):
    ms = []
    for i, (scope, ref, role) in enumerate(memberships, start=1):
        ms.append({
            "id": i, "email": email, "scope_type": scope,
            "partner_id": ref if scope == "partner" else None,
            "product_id": ref if scope == "product" else None,
            "role": role,
        })
    return {"email": email, "memberships": ms,
            "role": "admin" if any(m["role"] == "admin" for m in ms) else "manager"}


# Two partners: partner 1 owns products 11, 12; partner 2 owns product 21.
_PRODUCTS = {
    11: {"id": 11, "partner_id": 1, "slug": "p11", "active": True},
    12: {"id": 12, "partner_id": 1, "slug": "p12", "active": True},
    21: {"id": 21, "partner_id": 2, "slug": "p21", "active": True},
}


@pytest.fixture(autouse=True)
def _stub_products(monkeypatch):
    async def get_product(product_id):
        return _PRODUCTS.get(product_id)

    async def product_ids_for_partners(partner_ids):
        return [p["id"] for p in _PRODUCTS.values()
                if p["partner_id"] in partner_ids]

    monkeypatch.setattr(db, "get_product", get_product)
    monkeypatch.setattr(db, "product_ids_for_partners", product_ids_for_partners)


async def test_global_role_covers_everything():
    admin = _admin(("global", None, "admin"))
    assert admin_auth.global_role(admin) == "admin"
    assert await admin_auth.role_for_product(admin, 21) == "admin"
    assert await admin_auth.accessible_product_ids(admin) is None  # None = all


async def test_partner_admin_covers_its_products_only():
    admin = _admin(("partner", 1, "admin"))
    assert await admin_auth.role_for_product(admin, 11) == "admin"
    assert await admin_auth.role_for_product(admin, 12) == "admin"
    assert await admin_auth.role_for_product(admin, 21) is None
    assert await admin_auth.accessible_product_ids(admin) == [11, 12]


async def test_product_manager_reads_but_never_writes():
    admin = _admin(("product", 11, "manager"))
    assert await admin_auth.require_product_read(admin, 11) == "manager"
    with pytest.raises(HTTPException) as exc:
        await admin_auth.require_product_write(admin, 11)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        await admin_auth.require_product_read(admin, 12)  # sibling product


async def test_admin_role_wins_over_manager_role():
    # manager globally but admin on one product -> writes only that product.
    admin = _admin(("global", None, "manager"), ("product", 12, "admin"))
    assert await admin_auth.role_for_product(admin, 12) == "admin"
    assert await admin_auth.role_for_product(admin, 11) == "manager"  # via global
    await admin_auth.require_product_write(admin, 12)  # no raise
    with pytest.raises(HTTPException):
        await admin_auth.require_product_write(admin, 11)


async def test_resolve_scope_filter():
    partner_admin = _admin(("partner", 1, "admin"))
    # No selection -> everything the account reaches.
    assert await admin_auth.resolve_scope_filter(partner_admin) == [11, 12]
    # Explicit product inside the reach -> just that product.
    assert await admin_auth.resolve_scope_filter(partner_admin, product_id=12) == [12]
    # Explicit product outside the reach -> 403.
    with pytest.raises(HTTPException):
        await admin_auth.resolve_scope_filter(partner_admin, product_id=21)
    # Explicit partner -> its products.
    assert await admin_auth.resolve_scope_filter(partner_admin, partner_id=1) == [11, 12]
    with pytest.raises(HTTPException):
        await admin_auth.resolve_scope_filter(partner_admin, partner_id=2)
    # Global account, nothing selected -> None (no SQL filter at all).
    global_admin = _admin(("global", None, "admin"))
    assert await admin_auth.resolve_scope_filter(global_admin) is None


async def test_unknown_product_is_no_access():
    admin = _admin(("global", None, "admin"))
    assert await admin_auth.role_for_product(admin, 999) is None


async def test_product_admin_cannot_manage_global_account(monkeypatch):
    """User management reach: every membership of the target must lie inside
    the caller's admin reach — so a product admin can never touch a global
    account (or an orphan account with no memberships)."""
    from api import admin as admin_api
    product_admin = _admin(("product", 11, "admin"))
    global_user_ms = [{"scope_type": "global", "partner_id": None,
                       "product_id": None, "role": "manager"}]
    own_user_ms = [{"scope_type": "product", "partner_id": None,
                    "product_id": 11, "role": "manager"}]
    assert not await admin_api._can_manage_user(product_admin, global_user_ms)
    assert not await admin_api._can_manage_user(product_admin, [])
    assert await admin_api._can_manage_user(product_admin, own_user_ms)
    global_admin = _admin(("global", None, "admin"))
    assert await admin_api._can_manage_user(global_admin, global_user_ms)
