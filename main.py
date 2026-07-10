"""FastAPI app: lifespan(init_db), body-cap middleware, routers, static.

Serves the frontend widget + test page statically so the owner can open the test
page on Railway and tune the bot end-to-end.
"""
from __future__ import annotations

import asyncio
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
from api import retention as retention_api

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
    if config.TELEGRAM_WEBHOOK_SECRET_IS_FALLBACK:
        log.warning(
            "TELEGRAM_WEBHOOK_SECRET is not set; the retention bot webhook falls "
            "back to SESSION_JWT_SECRET. Set a DISTINCT TELEGRAM_WEBHOOK_SECRET "
            "in production."
        )
    if not config.PUBLIC_BASE_URL:
        log.warning(
            "PUBLIC_BASE_URL is not set; the retention bot webhook cannot be "
            "auto-registered from the admin panel until it is."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s: init_db", config.SERVICE_NAME)
    _warn_insecure_config()
    await db.init_db()
    await settings.reload()       # populate the hot settings cache from app_settings
    try:
        os.makedirs(config.RETENTION_MEDIA_DIR, exist_ok=True)
    except OSError as exc:
        log.warning("could not create RETENTION_MEDIA_DIR: %s", exc)
    # Proactive ping worker (the retention "ping matrix"). Deploy-level switch;
    # each product still opts in via the `retention.pings_enabled` setting, and
    # the sweep takes a Postgres advisory lock so multiple instances don't race.
    ping_task = None
    v2_task = None
    if config.RETENTION_SCHEDULER_ENABLED:
        import retention_pings
        import retention_v2
        ping_task = asyncio.create_task(retention_pings.scheduler_loop())
        # Retention v2 (agentic, event-driven) runs NEXT TO the v1 sweep under
        # the same deploy switch; per product exactly one regime acts (the
        # hot `retention.v2_enabled` setting — each sweep skips the other's
        # products), so both loops idle cheaply when unused.
        v2_task = asyncio.create_task(retention_v2.scheduler_loop())
    log.info("Startup complete")
    try:
        yield
    finally:
        for task in (ping_task, v2_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        await db.close()
        log.info("Shutdown complete")


# The OpenAPI schema + Swagger/ReDoc pages describe the WHOLE surface (the
# /admin API included), so they are not served by default in a deployment.
# Set EXPOSE_API_DOCS=1 to publish /docs, /redoc and /openapi.json (dev/stage).
app = FastAPI(
    title=config.SERVICE_NAME,
    lifespan=lifespan,
    openapi_url="/openapi.json" if config.EXPOSE_API_DOCS else None,
    docs_url="/docs" if config.EXPOSE_API_DOCS else None,
    redoc_url="/redoc" if config.EXPOSE_API_DOCS else None,
)

# --- CORS (env-driven) ------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # The admin SPA reads the sliding-session refresh token off this response
    # header; a cross-origin dev deploy needs it explicitly exposed (production
    # serves the SPA same-origin, where it's readable anyway).
    expose_headers=["X-Refresh-Token"],
)


# Endpoints that accept a binary upload (retention media) need a much larger body
# than the JSON API cap — a photo is megabytes, not the 64 KiB JSON default. The
# media-upload path gets its own cap so a normal JSON body stays tightly bounded
# while a legitimate image upload is allowed through.
_UPLOAD_PATH_PREFIX = "/admin/retention/photos"


def _body_cap_for(request: Request) -> int:
    if (request.method == "POST"
            and request.url.path.startswith(_UPLOAD_PATH_PREFIX)):
        return config.RETENTION_MAX_UPLOAD_BYTES
    return settings.general()["body_max_bytes"]


# --- Body-size cap middleware ----------------------------------------------
@app.middleware("http")
async def body_size_cap(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            body_max = _body_cap_for(request)
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
app.include_router(retention_api.public_router)  # telegram webhook + deeplink + CRM
app.include_router(retention_api.admin_router)    # /admin/retention/* (guarded)


# --- static frontend --------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(_TEST_PAGE, media_type="text/html")


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

# --- admin SPA (React Admin) --------------------------------------------------
# The admin UI is the React Admin app in admin/ (repo root). The Docker build
# compiles it (node stage -> admin/dist) and this service serves it at /admin,
# same-origin with the /admin/* JSON API — no CORS needed for the admin. The
# SPA uses a hash router, so the single index.html covers every admin route;
# its hashed assets live under /admin/assets (vite base '/admin/'). In local
# dev without a build the mount is simply skipped (use `npm run dev` instead).
_ADMIN_DIST = os.path.join(os.path.dirname(__file__), "admin", "dist")

if os.path.isdir(_ADMIN_DIST):
    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/", include_in_schema=False)
    async def admin_spa() -> FileResponse:
        # no-cache: the HTML references hashed asset URLs, so revalidating the
        # tiny entry page is what makes a redeploy take effect immediately.
        return FileResponse(os.path.join(_ADMIN_DIST, "index.html"),
                            media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/admin/assets",
              StaticFiles(directory=os.path.join(_ADMIN_DIST, "assets")),
              name="admin-assets")

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
# Public, self-contained integration guides for partner dev teams. Static HTML,
# no auth (they document the public contract and contain no secrets); shareable
# as <host>/integration. Split by task into a family of same-style pages that
# cross-link each other: /integration is the hub (overview, architecture, env
# vars, docs index); the siblings cover the widget embed, player data transfer
# & sync, the public Chat API / custom UI contract, the Telegram retention bot,
# and the external "master" admin panel integration.
@app.get("/integration", response_class=HTMLResponse)
async def integration_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)


@app.get("/integration-widget", response_class=HTMLResponse)
async def integration_widget_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration-widget.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)


@app.get("/integration-data", response_class=HTMLResponse)
async def integration_data_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration-data.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)


@app.get("/integration-chat-api", response_class=HTMLResponse)
async def integration_chat_api_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration-chat-api.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)


@app.get("/integration-telegram", response_class=HTMLResponse)
async def integration_telegram_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration-telegram.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)


@app.get("/integration-admin", response_class=HTMLResponse)
async def integration_admin_docs() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "integration-admin.html"),
                        media_type="text/html", headers=_WIDGET_CACHE)
