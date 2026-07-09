"""Admin authentication + scope authorization (multi-tenancy).

Every admin signs in as a named `admin_users` account (email + password). What
an account may SEE and TOUCH is driven by its `admin_memberships` rows — one
role per scope, where a scope is 'global' (the whole hub), a 'partner' (all of
that partner's products) or a single 'product'. Role semantics per scope:
'admin' may write, 'manager' is read-only. `require_admin` loads the
memberships on every request (single choke point, same reason the account
itself is re-checked: a JWT has no revocation), and the helpers below answer
"which products can this account read?" / "may it write this product?" for the
data routes in api/admin.py.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import antispam
import auth
import config
import db
from api.client_ip import client_ip

router = APIRouter(prefix="/admin", tags=["admin"])

# A throwaway PBKDF2 hash used to equalize login timing: when the account is
# missing or disabled we still run one verify against this hash, so a valid
# active email (which costs ~100ms of PBKDF2) is timing-indistinguishable from a
# non-existent/disabled one. Otherwise the CPU-cost delta enumerates accounts.
_DUMMY_PW_HASH = auth.hash_password("timing-equalizer-not-a-real-password")

# Roles allowed to MUTATE state within their scope. "admin" is a named user
# with full write rights over the scope; "manager" is read-only (support staff
# who triage sessions but touch nothing technical).
WRITE_ROLES = ("admin",)
SCOPE_TYPES = ("global", "partner", "product")


class AdminLogin(BaseModel):
    password: str
    email: Optional[str] = None


def _client_ip(request: Request) -> str:
    return client_ip(request)


async def require_admin(authorization: Optional[str] = Header(default=None),
                        response: Response = None) -> dict:
    """FastAPI dependency: verify the admin JWT (or a service API key) or raise
    401. Guards /admin/* data.

    Two credential kinds share the Bearer header:
      - `sak_...` — a service API key (admin_api_keys row, for machine callers
        like an external master admin panel). Resolved by hash on every request
        (deactivating a key applies immediately); its single scoped role is
        translated into a synthetic membership so every scope helper below
        works unchanged.
      - anything else — a human admin JWT from /admin/login.

    Beyond the signature/expiry check, the named account is re-checked against
    `admin_users` on every request: a JWT has no revocation, so without this a
    deactivated (or deleted) admin would keep full access until the token
    expired (up to ADMIN_TOKEN_TTL_MIN). The account's memberships are loaded
    here too (the DB is authoritative — a demotion applies immediately) and
    ride in the payload for the scope helpers below.
    """
    try:
        token = auth.extract_bearer(authorization)
    except auth.TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    if token.startswith("sak_"):
        key = await db.get_admin_api_key_by_token(token)
        if key is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        membership = {"role": key["role"], "scope_type": key["scope_type"],
                      "partner_id": key.get("partner_id"),
                      "product_id": key.get("product_id")}
        return {
            "email": f"apikey:{key['name']}",
            "role": key["role"] if key["role"] in WRITE_ROLES else "manager",
            "memberships": [membership],
            "api_key_id": key["id"],
        }
    try:
        payload = auth.verify_admin_token(token)
    except auth.TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    email = payload.get("email")
    if not email:
        # Every token minted by /admin/login carries the account email; a token
        # without one cannot be re-validated against admin_users — reject it.
        raise HTTPException(status_code=401, detail="token missing account email")
    user = await db.get_admin_user(email)
    if not user or not user.get("active", False):
        raise HTTPException(status_code=401, detail="account disabled")
    payload["memberships"] = await db.memberships_for(email)
    # Effective coarse role: 'admin' when the account may write ANYWHERE (the
    # fine-grained scope checks still apply per route), else 'manager'.
    payload["role"] = ("admin" if any(m.get("role") == "admin"
                                      for m in payload["memberships"])
                       else "manager")
    # Sliding session: once this token is past half its life, hand the client a
    # fresh one (new full TTL) via a response header. Active operators keep
    # sliding forward and are never logged out mid-work; an untouched account
    # still expires after a full TTL window. Applies to every human account
    # (admin + manager) — service API keys (the sak_ path above) are not
    # sessions and returned earlier without refresh.
    if response is not None:
        new_token = auth.refresh_admin_token(payload, payload["role"], email)
        if new_token:
            response.headers["X-Refresh-Token"] = new_token
    return payload


async def require_admin_write(admin: dict = Depends(require_admin)) -> dict:
    """FastAPI dependency: the account holds an admin role in AT LEAST ONE scope.

    A coarse pre-filter for mutating routes — pure managers 403 here without
    touching the handler. Every mutating handler still enforces the
    fine-grained scope check (require_product_write / require_global_write) on
    the specific target; this dependency alone is never sufficient authority.
    """
    if admin.get("role") not in WRITE_ROLES:
        raise HTTPException(status_code=403,
                            detail="This action requires an administrator account.")
    return admin


# ---------------------------------------------------------------------------
# Scope helpers (the authorization vocabulary for api/admin.py)
# ---------------------------------------------------------------------------
def _best(roles: set) -> Optional[str]:
    if "admin" in roles:
        return "admin"
    if "manager" in roles:
        return "manager"
    return None


def global_role(admin: dict) -> Optional[str]:
    """The account's global-scope role, or None when it has no global membership."""
    return _best({m["role"] for m in admin.get("memberships", [])
                  if m.get("scope_type") == "global"})


def role_for_partner(admin: dict, partner_id: int) -> Optional[str]:
    """Effective role over a partner: global membership or partner membership."""
    roles = {m["role"] for m in admin.get("memberships", [])
             if m.get("scope_type") == "partner" and m.get("partner_id") == partner_id}
    g = global_role(admin)
    if g:
        roles.add(g)
    return _best(roles)


async def role_for_product(admin: dict, product_id: int) -> Optional[str]:
    """Effective role over a product: global > owning partner > the product itself.

    None when the product does not exist or the account has no scope over it.
    """
    product = await db.get_product(product_id)
    if product is None:
        return None
    roles = {m["role"] for m in admin.get("memberships", [])
             if (m.get("scope_type") == "product" and m.get("product_id") == product_id)
             or (m.get("scope_type") == "partner"
                 and m.get("partner_id") == product["partner_id"])}
    g = global_role(admin)
    if g:
        roles.add(g)
    return _best(roles)


async def accessible_product_ids(admin: dict) -> Optional[list[int]]:
    """The products this account may read. None = ALL (global scope).

    An empty list is a real answer (an account with no product reach) and must
    filter every per-product query down to nothing.
    """
    if global_role(admin):
        return None
    memberships = admin.get("memberships", [])
    partner_ids = [m["partner_id"] for m in memberships
                   if m.get("scope_type") == "partner" and m.get("partner_id")]
    product_ids = {m["product_id"] for m in memberships
                   if m.get("scope_type") == "product" and m.get("product_id")}
    product_ids.update(await db.product_ids_for_partners(partner_ids))
    return sorted(product_ids)


async def require_product_read(admin: dict, product_id: int) -> str:
    role = await role_for_product(admin, product_id)
    if role is None:
        raise HTTPException(status_code=403,
                            detail="No access to this product.")
    return role


async def require_product_write(admin: dict, product_id: int) -> None:
    role = await role_for_product(admin, product_id)
    if role not in WRITE_ROLES:
        raise HTTPException(status_code=403,
                            detail="This action requires an administrator "
                                   "role over this product.")


def require_global_write(admin: dict) -> None:
    if global_role(admin) not in WRITE_ROLES:
        raise HTTPException(status_code=403,
                            detail="This action requires a global administrator "
                                   "account.")


async def resolve_scope_filter(admin: dict, product_id: Optional[int] = None,
                               partner_id: Optional[int] = None
                               ) -> Optional[list[int]]:
    """Turn the dashboard's selected scope into a SQL product filter.

    Explicit product -> [that product] (after a read check); explicit partner ->
    that partner's products (after a read check); nothing selected -> everything
    the account may read (None = all, for global scope). db._product_clause
    treats an empty list as "match nothing".
    """
    if product_id is not None:
        await require_product_read(admin, product_id)
        return [product_id]
    if partner_id is not None:
        if role_for_partner(admin, partner_id) is None:
            raise HTTPException(status_code=403,
                                detail="No access to this partner.")
        return await db.product_ids_for_partners([partner_id])
    return await accessible_product_ids(admin)


@router.post("/login")
async def login(req: Request, body: AdminLogin) -> JSONResponse:
    ip = _client_ip(req)
    # Reuse the Phase 1 sliding-window to throttle brute force on a dedicated key.
    try:
        antispam.check_rate_limit(f"admin-login:{ip}")
    except antispam.AntiSpamError as exc:
        await db.log_admin_event(None, "admin_login_failed",
                                 {"ip": ip, "reason": "rate_limited"})
        return JSONResponse(status_code=exc.status,
                            content={"error": exc.code, "detail": exc.detail})

    email = (body.email or "").strip().lower()
    if not email:
        return JSONResponse(status_code=400,
                            content={"error": "bad_request",
                                     "detail": "Email and password are required."})

    # Named user login: email + password verified against the salted PBKDF2
    # hash. A missing/disabled user and a bad password are indistinguishable to
    # the client (no account enumeration).
    user = await db.get_admin_user(email)
    ok = False
    # PBKDF2 (200k iterations) is CPU-bound and would block the event loop for
    # ~100ms per attempt — run it in a worker thread. Always run exactly one
    # verify: against the real hash when the account exists and is active, else
    # against a dummy hash of equal cost, so timing cannot distinguish a valid
    # active email from a missing/disabled one (no account enumeration).
    if user and user.get("active", False):
        ok = await asyncio.to_thread(
            auth.verify_password, body.password, user["password_hash"]
        )
    else:
        await asyncio.to_thread(
            auth.verify_password, body.password, _DUMMY_PW_HASH
        )
    if not ok:
        await db.log_admin_event(None, "admin_login_failed",
                                 {"ip": ip, "reason": "bad_user_credentials",
                                  "email": email})
        return JSONResponse(status_code=401,
                            content={"error": "unauthorized",
                                     "detail": "Invalid email or password."})
    # The token's role claim is a coarse "may write anywhere?" hint — the
    # authoritative fine-grained answer is recomputed from admin_memberships on
    # every request in require_admin.
    memberships = await db.memberships_for(email)
    role = ("admin" if any(m.get("role") == "admin" for m in memberships)
            else "manager")
    token = auth.issue_admin_token(role=role, email=email)
    return JSONResponse(status_code=200,
                        content={"token": token,
                                 "ttl_min": config.ADMIN_TOKEN_TTL_MIN,
                                 "role": role, "email": email,
                                 "memberships": memberships})
