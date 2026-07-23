"""FastAPI app: lifespan(init_db), body-cap middleware, routers, static.

Serves the frontend widget + test page statically so the owner can open the test
page on Railway and tune the bot end-to-end.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
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

# Mirror runtime logs into the in-process buffer for the admin System-logs view
# (the flush loop below drains it into the bounded app_logs table) — see
# logcapture.py for the root-logger/denylist rationale.
import logcapture  # noqa: E402
logcapture.install()

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


_SETTINGS_REFRESH_SEC = 60


async def _settings_refresh_loop() -> None:
    """Re-pull the settings caches from the DB every minute (multi-instance)."""
    while True:
        await asyncio.sleep(_SETTINGS_REFRESH_SEC)
        try:
            await settings.reload()
            # Drop the per-process KB caches on the same cadence: they are only
            # invalidated by writes on THIS instance, so without this a KB/topic/
            # variable edit on another instance stayed invisible here until
            # restart. Cheap — the next request re-fetches the small KB rows.
            db.clear_kb_caches()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a transient DB error must not kill the loop
            log.exception("settings_refresh_failed")


_LOG_FLUSH_SEC = 3
_LOG_KEEP_ROWS = 5000
_LOG_PRUNE_EVERY = 20  # prune once every N flushes (~1 min)
# The append-only retention_events log has no size cap; reap processed rows older
# than this on a coarse cadence (~hourly) from the same loop.
_RETENTION_EVENTS_PRUNE_EVERY = 1200  # ~1h at _LOG_FLUSH_SEC
_RETENTION_EVENTS_KEEP_DAYS = 90


async def _log_flush_loop() -> None:
    """Drain captured log records into app_logs; periodically prune the table.

    Keeps the admin System-logs view fed without the logging hot path ever
    touching the DB (logcapture buffers in memory; this loop is the only writer).
    """
    ticks = 0
    while True:
        await asyncio.sleep(_LOG_FLUSH_SEC)
        try:
            items = logcapture.drain()
            if items:
                await db.insert_app_logs(items)
            ticks += 1
            if ticks % _LOG_PRUNE_EVERY == 0:
                await db.prune_app_logs(_LOG_KEEP_ROWS)
            if ticks % _RETENTION_EVENTS_PRUNE_EVERY == 0:
                removed = await db.prune_retention_events(_RETENTION_EVENTS_KEEP_DAYS)
                if removed:
                    log.info("retention_events_pruned rows=%s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let a DB hiccup kill the loop
            pass


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
    # The retention-agent worker (event-driven proactive loop). Deploy-level
    # switch; each product still opts in via the hot `retention.v2_enabled`
    # setting, the cadence is the hot `retention.worker_interval_sec` setting,
    # and event pickup is an atomic claim (plus a Postgres advisory lock per
    # sweep), so multiple instances never double-send.
    agent_task = None
    media_task = None
    if config.RETENTION_SCHEDULER_ENABLED:
        import media_normalizer
        import retention_v2
        agent_task = asyncio.create_task(retention_v2.scheduler_loop())
        # Media normalizer: the hourly sweep re-encoding uploaded retention
        # photos to WebP at Telegram-appropriate dimensions (heavy originals
        # deleted). Same deploy switch as the agent worker; normalization is
        # always-on and fully code-owned (no admin knob, no per-product switch).
        media_task = asyncio.create_task(media_normalizer.scheduler_loop())
    # Periodic settings-cache refresh: the in-process cache is reloaded on a
    # local admin write, but a write made by ANOTHER instance (or directly in
    # the DB) was invisible until restart — the "I changed a setting and
    # nothing happened" failure on multi-instance deployments. Two cheap
    # SELECTs a minute.
    refresh_task = asyncio.create_task(_settings_refresh_loop())
    # Flush captured runtime logs into app_logs for the admin System-logs view.
    log_task = asyncio.create_task(_log_flush_loop())
    log.info("Startup complete")
    try:
        yield
    finally:
        for task in (agent_task, media_task, refresh_task, log_task):
            if task is None:
                continue
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


# --- Admin audit middleware -------------------------------------------------
# Records one row per SUCCESSFUL mutating /admin/* request (who + what + which
# product scope + when) so the admin panel's Activity log can answer "who
# changed what?". The actor is stashed on request.state by require_admin; the
# product is read from the ?product_id= query param or a /products/{id} path.
# Best-effort: an audit-write failure never affects the response.
_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Auth/session churn and the log-read marker aren't content changes worth a row.
_AUDIT_SKIP_PATHS = {"/admin/login", "/admin/logout", "/admin/logs/read"}
_PRODUCT_PATH_RE = re.compile(r"/products/(\d+)")

# Friendly action labels by path fragment (first match wins), so the Activity
# log reads in plain language instead of raw method+path.
_AUDIT_LABELS = (
    ("/admin/settings", "Updated settings"),
    ("/admin/kb/variables", "Edited KB variables"),
    ("/admin/kb/content", "Edited knowledge base"),
    ("/admin/kb/topics", "Edited topics"),
    ("/admin/translations", "Edited translations"),
    ("/admin/prompt-variables", "Edited prompt variables"),
    ("/admin/test-profile", "Edited test profile"),
    ("/admin/site-map", "Edited site map"),
    ("/admin/retention/photos", "Media library change"),
    ("/admin/retention/kb", "Edited retention KB"),
    ("/admin/retention/prompt-variables", "Edited retention prompt"),
    ("/admin/retention/managers", "Managers change"),
    ("/admin/retention/idle", "Idle-ping rules change"),
    ("/admin/retention/v2", "Proactive-agent change"),
    ("/admin/retention", "Retention config change"),
    ("/admin/users", "User management"),
    ("/admin/api-keys", "API key management"),
    ("/admin/products", "Structure change"),
    ("/admin/partners", "Structure change"),
)


def _audit_action_label(method: str, path: str) -> str:
    for frag, label in _AUDIT_LABELS:
        if path.startswith(frag):
            verb = "Deleted" if method == "DELETE" else None
            return f"{label} (deleted)" if verb else label
    return f"{method} {path}"


@app.middleware("http")
async def audit_admin_actions(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if (request.method in _AUDIT_METHODS
                and path.startswith("/admin/")
                and path not in _AUDIT_SKIP_PATHS
                and 200 <= response.status_code < 400):
            actor = getattr(request.state, "audit_actor", None)
            if actor and actor.get("email"):
                pid = request.query_params.get("product_id")
                product_id = int(pid) if pid and pid.isdigit() else None
                if product_id is None:
                    m = _PRODUCT_PATH_RE.search(path)
                    if m:
                        product_id = int(m.group(1))
                await db.log_audit(
                    actor_email=actor["email"],
                    actor_role=actor.get("role"),
                    method=request.method,
                    path=path,
                    action=_audit_action_label(request.method, path),
                    product_id=product_id,
                    status=response.status_code,
                )
    except Exception:  # noqa: BLE001 - auditing must never break the request
        pass
    return response


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
    # Same no-cache header as /test.html — the two serve the same page and must
    # revalidate identically.
    return FileResponse(_TEST_PAGE, media_type="text/html",
                        headers=_WIDGET_CACHE)


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
def _register_doc_page(path: str, filename: str) -> None:
    async def _serve() -> FileResponse:
        return FileResponse(os.path.join(_FRONTEND_DIR, filename),
                            media_type="text/html", headers=_WIDGET_CACHE)
    app.get(path, response_class=HTMLResponse, name=f"docs_{filename}")(_serve)


for _doc_path in ("integration", "integration-widget", "integration-data",
                  "integration-chat-api", "integration-telegram",
                  "integration-admin"):
    _register_doc_page(f"/{_doc_path}", f"{_doc_path}.html")
