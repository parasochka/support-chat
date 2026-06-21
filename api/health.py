"""Liveness endpoint — checks DB connectivity."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import db

router = APIRouter()


@router.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        db_ok = await db.ping()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "error", "service": config.SERVICE_NAME,
                     "db": False, "error": exc.__class__.__name__},
        )
    status = "ok" if db_ok else "degraded"
    code = 200 if db_ok else 503
    return JSONResponse(
        status_code=code,
        content={"status": status, "service": config.SERVICE_NAME, "db": db_ok},
    )
