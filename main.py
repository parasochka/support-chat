"""FastAPI app: lifespan(init_db), body-cap middleware, routers, static.

Serves the frontend widget + test page statically so the owner can open the test
page on Railway and tune the bot end-to-end.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import config
import db
import settings
from api import admin as admin_api
from api import admin_auth as admin_auth_api
from api import chat as chat_api
from api import health as health_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(config.SERVICE_NAME)

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
_TEST_PAGE = os.path.join(_FRONTEND_DIR, "test.html")


def _warn_insecure_config() -> None:
    """Flag deployment foot-guns at startup (logged, never fatal).

    These are safe in local/dev but risky in production, so we surface them
    loudly instead of failing — the operator decides.
    """
    if config.ADMIN_JWT_SECRET_IS_FALLBACK:
        log.warning(
            "ADMIN_JWT_SECRET is not set; admin tokens are signed with "
            "SESSION_JWT_SECRET. Set a DISTINCT ADMIN_JWT_SECRET in production."
        )
    if "*" in config.CORS_ALLOW_ORIGINS:
        log.warning(
            "CORS_ALLOW_ORIGINS is '*' (any origin). Restrict it to your "
            "host site origins in production."
        )
    if config.SECRETS_MASTER_KEY_IS_FALLBACK:
        log.warning(
            "SECRETS_MASTER_KEY is not set; per-product secrets are encrypted "
            "with SESSION_JWT_SECRET. Set a DISTINCT SECRETS_MASTER_KEY in "
            "production (rotating it later invalidates stored product secrets)."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s: init_db", config.SERVICE_NAME)
    _warn_insecure_config()
    await db.init_db()
    await settings.reload()       # populate the hot settings cache from app_settings
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
            body_max = settings.general()["body_max_bytes"]
            if int(cl) > body_max:
                return JSONResponse(
                    status_code=413,
                    content={"error": "body_too_large",
                             "detail": f"Body exceeds {body_max} bytes."},
                )
        except ValueError:
            pass
    elif "transfer-encoding" in request.headers:
        # A chunked request carries no Content-Length, so it would sail past the
        # cap above and the whole body would still be buffered by the JSON
        # parser. No legitimate client of this JSON API streams chunked bodies —
        # require a declared length instead of buffering blind.
        return JSONResponse(
            status_code=411,
            content={"error": "length_required",
                     "detail": "Chunked request bodies are not accepted; "
                               "send a Content-Length header."},
        )
    return await call_next(request)


# --- routers ----------------------------------------------------------------
app.include_router(health_api.router)
app.include_router(chat_api.router)
app.include_router(admin_auth_api.router)   # /admin/login + require_admin
app.include_router(admin_api.router)        # /admin/* data + management (guarded)


# --- static frontend --------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(_TEST_PAGE, media_type="text/html")


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

# --- admin dashboard SPA ----------------------------------------------------
# The data API lives under /admin/* (guarded). The SPA HTML is served at /admin
# and /admin/, and its ES-module assets under /admin-static (a distinct prefix
# so it never shadows the /admin/* JSON routes).
_ADMIN_DIR = os.path.join(_FRONTEND_DIR, "admin")


def _asset_version() -> str:
    """Cache-busting token from the admin assets' mtimes.

    The SPA has no build step and its assets are served by StaticFiles, which
    sets no explicit Cache-Control — so browsers apply *heuristic* caching and
    can serve a stale admin.js for a while after a redeploy (the bug behind
    "the new option isn't showing up"). We stamp the asset URLs with a token
    derived from the files' modification times so every redeploy yields a fresh
    URL and the browser is forced to refetch.
    """
    latest = 0.0
    for name in ("admin.js", "admin.css"):
        try:
            latest = max(latest, os.path.getmtime(os.path.join(_ADMIN_DIR, name)))
        except OSError:
            pass
    return str(int(latest))


@app.get("/admin")
@app.get("/admin/")
async def admin_index() -> HTMLResponse:
    with open(os.path.join(_ADMIN_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    v = _asset_version()
    html = html.replace("/admin-static/admin.css", f"/admin-static/admin.css?v={v}")
    html = html.replace("/admin-static/admin.js", f"/admin-static/admin.js?v={v}")
    # Always revalidate the HTML itself so the freshly-stamped URLs take effect
    # on the next load instead of being served from a stale cached page.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if os.path.isdir(_ADMIN_DIR):
    app.mount("/admin-static", StaticFiles(directory=_ADMIN_DIR), name="admin-static")


# The widget files are served at fixed URLs with no build step, so without an
# explicit Cache-Control browsers apply heuristic caching and can keep serving
# a stale widget.js for days after a redeploy (the admin SPA already solves
# this with mtime-stamped URLs). `no-cache` = revalidate each load; the files
# are small and the 304 path keeps repeat loads cheap.
_WIDGET_CACHE = {"Cache-Control": "no-cache"}


@app.get("/widget.js")
async def widget_js() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.js"),
                        media_type="application/javascript",
                        headers=_WIDGET_CACHE)


@app.get("/widget.css")
async def widget_css() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.css"),
                        media_type="text/css", headers=_WIDGET_CACHE)


@app.get("/test.html", response_class=HTMLResponse)
async def test_html() -> FileResponse:
    return FileResponse(_TEST_PAGE, media_type="text/html",
                        headers=_WIDGET_CACHE)


# --- integration docs ---------------------------------------------------------
# Public, self-contained API/integration guide for partner dev teams: how to
# embed the widget (widget key), sign the player handshake, and talk to the
# chat/admin APIs. Static HTML, no auth (it documents the public contract and
# contains no secrets); shareable as <host>/integration.
@app.get("/integration", response_class=HTMLResponse)
async def integration_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)
