"""Liveness endpoint.

Deliberately a LIVENESS probe, decoupled from DB readiness: it returns 200 as
long as the process is up and its event loop responds, even when the DB is
momentarily down. The platform healthcheck restarts the container on a non-200,
so tying this to the DB turned a transient DB blip into a restart/crash loop
(the app can't re-init without the DB either). The DB state still rides in the
body (`db`, `status`) for observability, and `db.ping()` is timeout-bounded so
this never hangs. Use `?deep=1` for a strict readiness check (503 when the DB is
down) where a caller genuinely wants it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import config
import db

router = APIRouter()
log = logging.getLogger(config.SERVICE_NAME)


@router.get("/healthz")
async def healthz(deep: bool = False) -> JSONResponse:
    db_ok = await db.ping()  # timeout-bounded, returns False (never raises) on error
    status = "ok" if db_ok else "degraded"
    if not db_ok:
        log.warning("healthz_db_unavailable service=%s", config.SERVICE_NAME)
    # Liveness: 200 unless the caller explicitly asked for a strict readiness
    # probe (?deep=1), which 503s when the DB is down.
    code = 200 if (db_ok or not deep) else 503
    return JSONResponse(
        status_code=code,
        content={"status": status, "service": config.SERVICE_NAME, "db": db_ok},
    )
