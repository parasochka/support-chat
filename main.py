"""FastAPI app: lifespan(init_db + seed), body-cap middleware, routers, static.

Serves the frontend widget + test page statically so the owner can open the test
page on Railway and tune the bot end-to-end.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
import db
from api import chat as chat_api
from api import health as health_api
from seed import kb_seed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(config.SERVICE_NAME)

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s: init_db + seed", config.SERVICE_NAME)
    await db.init_db()
    await kb_seed.run()
    log.info("Startup complete")
    try:
        yield
    finally:
        await db.close()
        log.info("Shutdown complete")


app = FastAPI(title=config.SERVICE_NAME, lifespan=lifespan)

# --- CORS (env-driven) ------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Body-size cap middleware ----------------------------------------------
@app.middleware("http")
async def body_size_cap(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > config.BODY_MAX_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": "body_too_large",
                             "detail": f"Body exceeds {config.BODY_MAX_BYTES} bytes."},
                )
        except ValueError:
            pass
    return await call_next(request)


# --- routers ----------------------------------------------------------------
app.include_router(health_api.router)
app.include_router(chat_api.router)


# --- static frontend --------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "test.html"))


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")


@app.get("/widget.js")
async def widget_js() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.js"),
                        media_type="application/javascript")


@app.get("/widget.css")
async def widget_css() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.css"),
                        media_type="text/css")


@app.get("/test.html")
async def test_html() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "test.html"))
