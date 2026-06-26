"""Admin authentication — single-owner password login + JWT guard (Phase 2).

Single owner for now, but the token carries a `role` claim so multi-admin can be
added later without reshaping it (§16 decision: single owner, future-proofed).
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import antispam
import auth
import config
import db
from api.client_ip import client_ip

router = APIRouter(prefix="/admin", tags=["admin"])

# Roles allowed to MUTATE state. Everyone authenticated may read; only these may
# write (KB, settings, variables, test profile, user management). "owner" is the
# password-only super-admin; "admin" is a named user with the same write rights;
# "manager" is read-only (support staff who triage sessions but touch nothing
# technical).
WRITE_ROLES = ("owner", "admin")


class AdminLogin(BaseModel):
    password: str
    email: Optional[str] = None


def _client_ip(request: Request) -> str:
    return client_ip(request)


async def require_admin(authorization: Optional[str] = Header(default=None)) -> dict:
    """FastAPI dependency: verify the admin JWT or raise 401. Guards /admin/* data."""
    if not config.ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin dashboard is disabled.")
    try:
        token = auth.extract_bearer(authorization)
        return auth.verify_admin_token(token)
    except auth.TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


async def require_admin_write(admin: dict = Depends(require_admin)) -> dict:
    """FastAPI dependency: verify the token AND that the role may write (403 if not).

    Managers are read-only, so every mutating route depends on this instead of the
    bare `require_admin`. The check is server-side authority — the SPA also hides
    the controls, but that is only cosmetic.
    """
    if admin.get("role") not in WRITE_ROLES:
        raise HTTPException(status_code=403,
                            detail="This action requires an administrator account.")
    return admin


@router.post("/login")
async def login(req: Request, body: AdminLogin) -> JSONResponse:
    if not config.ADMIN_PASSWORD:
        return JSONResponse(status_code=503,
                            content={"error": "disabled",
                                     "detail": "Admin dashboard is disabled."})
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
    if email:
        # Named user login: email + password verified against the salted PBKDF2
        # hash. A missing/disabled user and a bad password are indistinguishable
        # to the client (no account enumeration).
        user = await db.get_admin_user(email)
        ok = bool(user) and user.get("active", False) \
            and auth.verify_password(body.password, user["password_hash"])
        if not ok:
            await db.log_admin_event(None, "admin_login_failed",
                                     {"ip": ip, "reason": "bad_user_credentials",
                                      "email": email})
            return JSONResponse(status_code=401,
                                content={"error": "unauthorized",
                                         "detail": "Invalid email or password."})
        role = user["role"]
        token = auth.issue_admin_token(role=role, email=email)
        return JSONResponse(status_code=200,
                            content={"token": token,
                                     "ttl_min": config.ADMIN_TOKEN_TTL_MIN,
                                     "role": role, "email": email})

    if not hmac.compare_digest(body.password, config.ADMIN_PASSWORD):
        await db.log_admin_event(None, "admin_login_failed",
                                 {"ip": ip, "reason": "bad_password"})
        return JSONResponse(status_code=401,
                            content={"error": "unauthorized",
                                     "detail": "Invalid password."})

    token = auth.issue_admin_token(role="owner")
    return JSONResponse(status_code=200,
                        content={"token": token,
                                 "ttl_min": config.ADMIN_TOKEN_TTL_MIN,
                                 "role": "owner"})
