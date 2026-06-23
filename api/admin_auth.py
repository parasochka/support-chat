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


class AdminLogin(BaseModel):
    password: str


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
