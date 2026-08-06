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

from app.core import config
from app.core import db
from app.core import http
from app.core import loops
from app.core import meminfo
from app.core import settings
from app.api import admin as admin_api
from app.api import admin_auth as admin_auth_api
from app.api import chat as chat_api
from app.api import health as health_api
from app.api import orchestrator as orchestrator_api
from app.api import quality as quality_api
from app.api import retention as retention_api

# Sets the log format and mirrors runtime logs into the in-process buffer for
# the admin System-logs view (the flush loop drains it into the bounded app_logs
# table). Shared with the worker entry point — see loops.py.
loops.install_logging()
log = logging.getLogger(config.SERVICE_NAME)

# The two infra loops both roles run. Re-exported under their original private
# names so the lifespan below (and anything importing them from here) is
# unchanged; they live in loops.py so `python -m app.worker` can have them
# without importing this module and building the whole ASGI app.
_settings_refresh_loop = loops.settings_refresh_loop
_log_flush_loop = loops.log_flush_loop

# Static assets live at the REPO root (frontend/, admin/dist), one level above
# the app/ package this module now lives in.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIR = os.path.join(_REPO_ROOT, "frontend")
_TEST_PAGE = os.path.join(_FRONTEND_DIR, "test.html")

_KNOWN_ROLES = ("web", "worker", "all")


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
    if config.SERVICE_ROLE not in _KNOWN_ROLES:
        log.warning(
            "SERVICE_ROLE=%r is not one of %s; treating this process as 'all' "
            "(HTTP + the background pipeline).",
            config.SERVICE_ROLE, "/".join(_KNOWN_ROLES),
        )


def _background_plan(role: str, scheduler_enabled: bool) -> tuple[bool, bool]:
    """Which background halves this process owns: (pipeline, media normalizer).

    On the 'web' role the pipeline belongs to the separate worker service
    (`python -m app.worker`, same image), whatever RETENTION_SCHEDULER_ENABLED
    says — that switch is the worker's master kill switch now, and the web
    service sets it to 0 precisely to hand the pipeline over. 'all' is the
    single-process mode and still runs everything here, gated by the switch as
    before. 'worker' means this uvicorn is misconfigured: the loops are the
    other entrypoint's, and starting them here would run every sweep twice.

    The media normalizer is NOT pipeline work: it owns the FILES on the local
    media dir — on Railway a Volume mounted to the service that serves the admin
    uploads — so it follows the volume and stays on the web process.

    A typo must not silently double every sweep against a real worker either, so
    an unknown role falls back to the pre-split behaviour (warned about above).
    """
    resolved = role if role in _KNOWN_ROLES else "all"
    runs_pipeline = resolved == "all" and scheduler_enabled
    return runs_pipeline, runs_pipeline or resolved == "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s: init_db", config.SERVICE_NAME)
    # Before anything else allocates — a tracemalloc snapshot only sees what was
    # allocated after tracing started. No-op unless MEMORY_TRACEMALLOC=1.
    meminfo.start_tracemalloc()
    _warn_insecure_config()
    await db.init_db()
    await settings.reload()       # populate the hot settings cache from app_settings
    try:
        os.makedirs(config.RETENTION_MEDIA_DIR, exist_ok=True)
    except OSError as exc:
        log.warning("could not create RETENTION_MEDIA_DIR: %s", exc)
    runs_pipeline, runs_media = _background_plan(
        config.SERVICE_ROLE, config.RETENTION_SCHEDULER_ENABLED)
    log.info("service role=%s pipeline=%s media=%s",
             config.SERVICE_ROLE, runs_pipeline, runs_media)
    agent_task = None
    maintenance_task = None
    send_task = None
    media_task = None
    review_task = None
    if runs_pipeline:
        from app.ai import reviewer
        from app.retention import retention_v2
        from app.retention import send_worker
        # The retention-agent worker (event-driven proactive loop). Each product
        # still opts in via the hot `retention.v2_enabled` setting, the cadence
        # is the hot `retention.worker_interval_sec` setting, and event pickup is
        # an atomic lease claim, so multiple instances never double-send.
        agent_task = asyncio.create_task(retention_v2.scheduler_loop())
        # Everything that is NOT event processing (lease reclaim, attribution,
        # scoring, activity profiles, journeys, the idle ladder) on its own
        # clock, so a slow sweep never sits on the critical path of reacting to
        # a deposit.
        maintenance_task = asyncio.create_task(retention_v2.maintenance_loop())
        # The send stage — a no-op until `retention.send_worker_enabled` is on.
        send_task = asyncio.create_task(send_worker.send_loop())
        # Quality review: the LLM-as-judge pass over finished conversations
        # (both facades), with the per-product on/off + daily cap in the
        # `general` settings group.
        review_task = asyncio.create_task(reviewer.scheduler_loop())
    if runs_media:
        # Media normalizer: the hourly sweep re-encoding uploaded retention
        # photos to WebP at Telegram-appropriate dimensions (heavy originals
        # deleted). Normalization is always-on and fully code-owned (no admin
        # knob, no per-product switch).
        from app.retention import media_normalizer
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
        for task in (agent_task, maintenance_task, send_task, media_task,
                     review_task, refresh_task, log_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # After the tasks that use it, before the DB: the loops send over the
        # shared HTTP client, so tearing its transport out from under a live
        # request would be the one ordering that matters. `aclose()` never
        # raises, so it cannot mask db.close() below.
        await http.aclose()
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


# --- Security response headers ----------------------------------------------
# The admin SPA is a same-origin React app whose ordinary buttons perform
# destructive, state-changing actions (delete a product, rotate a widget key,
# mint a service API key). Nothing stopped a third-party page from FRAMING it and
# clickjacking a logged-in operator into those clicks, so /admin/* refuses to be
# framed at all: X-Frame-Options for old browsers plus the CSP directive that
# supersedes it.
#
# The frame deny is deliberately scoped to /admin — the widget, the test page and
# the integration docs are MEANT to be embedded/opened by partner sites, and a
# blanket frame-ancestors would break exactly the integration this service
# exists for. `nosniff` is safe everywhere (every response here carries an
# explicit Content-Type) and stops a content-type confusion turning an
# admin-uploaded media binary into script. `setdefault`, not assignment, so a
# handler that set its own value keeps it.
_FRAME_DENY_PREFIX = "/admin"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.url.path.startswith(_FRAME_DENY_PREFIX):
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy",
                                    "frame-ancestors 'none'")
        # Keep admin URLs (product ids, filter state) out of third-party
        # referrers when an operator follows a link out of the panel.
        response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


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
app.include_router(orchestrator_api.router)       # /admin/retention/* orchestrator (guarded)
app.include_router(orchestrator_api.system_router)  # /admin/integration-checklist
app.include_router(quality_api.router)            # /admin/quality/* (guarded)


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
_ADMIN_DIST = os.path.join(_REPO_ROOT, "admin", "dist")

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


# `integration-reference` and `integration-variables` are the machine-generated
# pair: the contract surface that crosses a system boundary (endpoints, wire
# fields, events) and the product-internal text surface (KB/prompt variables,
# the copy registry, control sentinels). Both are built from the same source as
# the downloadable workbook (frontend/api-reference.xlsx, served by the /static
# mount) so the pages and the spreadsheet can never drift apart — see
# scripts/build_api_reference.py.
for _doc_path in ("integration", "integration-widget", "integration-data",
                  "integration-chat-api", "integration-telegram",
                  "integration-admin", "integration-reference",
                  "integration-variables"):
    _register_doc_page(f"/{_doc_path}", f"{_doc_path}.html")
