"""GET /admin/diagnostics/memory — authorization and payload shape.

The route reports DEPLOY-WIDE process internals: there is no product_id to scope
it by, so it follows the System-logs rule (global scope only). The router-level
`require_admin` alone is never enough authority — a product-scoped manager at
any tenant would otherwise read the hub's allocator internals.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api import admin as admin_api


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


async def test_global_admin_gets_a_snapshot():
    resp = await admin_api.memory_diagnostics(
        top=5, admin=_admin(("global", None, "admin")))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["blocks"] > 0
    assert "rss_mb" in body and "caches" in body
    # Whether tracing is armed is a boot-time decision; the block must always be
    # present so "off" and "nothing allocating" are never confused.
    assert "enabled" in body["tracemalloc"]


async def test_global_manager_may_read():
    """A read-only hub-wide manager is deliberately allowed — same as the logs."""
    resp = await admin_api.memory_diagnostics(
        top=5, admin=_admin(("global", None, "manager")))
    assert resp.status_code == 200


@pytest.mark.parametrize("membership", [
    ("product", 11, "admin"),
    ("partner", 1, "admin"),
    ("product", 11, "manager"),
])
async def test_a_scoped_admin_is_refused(membership):
    with pytest.raises(HTTPException) as exc:
        await admin_api.memory_diagnostics(top=5, admin=_admin(membership))
    assert exc.value.status_code == 403


async def test_payload_is_json_serializable():
    """Every handler here returns JSONResponse, which cannot serialize a raw
    datetime — the bug that shipped with the kb_variables tab."""
    resp = await admin_api.memory_diagnostics(
        top=50, admin=_admin(("global", None, "admin")))
    json.loads(resp.body)


def test_the_route_is_registered():
    """It is reachable only if it is actually mounted — the handler passing its
    own unit tests says nothing about the app wiring."""
    from app import main
    assert "/admin/diagnostics/memory" in main.app.openapi()["paths"]
