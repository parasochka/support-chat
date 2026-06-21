"""FastAPI app: lifespan(init_db + seed), body-cap middleware, routers, static.

Serves the frontend widget + test page statically so the owner can open the test
page on Railway and tune the bot end-to-end.
"""
from __future__ import annotations

import html
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
from seed import kb_seed
from seed import prompt_seed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(config.SERVICE_NAME)

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
_PROJECT_DIR = os.path.dirname(__file__)
_TEST_PAGE = os.path.join(_FRONTEND_DIR, "test.html")
_CLAUDE_MD = os.path.join(_PROJECT_DIR, "CLAUDE.md")
_CLAUDE_MD_MARKER = "<!--CLAUDE_MD_CONTENT-->"


def _render_test_page() -> str:
    """Inject the live CLAUDE.md contents into the test page template.

    CLAUDE.md is the single source of truth and is kept identical to README.md;
    it is read on every request so the root page always reflects the current
    file. The contents are HTML-escaped before landing inside the <pre> block.
    """
    with open(_TEST_PAGE, encoding="utf-8") as f:
        template = f.read()
    try:
        with open(_CLAUDE_MD, encoding="utf-8") as f:
            doc = f.read()
    except OSError:
        doc = ""
    return template.replace(_CLAUDE_MD_MARKER, html.escape(doc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s: init_db + seed", config.SERVICE_NAME)
    await db.init_db()
    await kb_seed.run()
    await prompt_seed.run()       # migrate Phase 1 core into prompt_versions (once)
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
app.include_router(admin_auth_api.router)   # /admin/login + require_admin
app.include_router(admin_api.router)        # /admin/* data + management (guarded)


# --- static frontend --------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_render_test_page())


if os.path.isdir(_FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=_FRONTEND_DIR), name="static")

# --- admin dashboard SPA ----------------------------------------------------
# The data API lives under /admin/* (guarded). The SPA HTML is served at /admin
# and /admin/, and its ES-module assets under /admin-static (a distinct prefix
# so it never shadows the /admin/* JSON routes).
_ADMIN_DIR = os.path.join(_FRONTEND_DIR, "admin")


@app.get("/admin")
@app.get("/admin/")
async def admin_index() -> FileResponse:
    return FileResponse(os.path.join(_ADMIN_DIR, "index.html"))


if os.path.isdir(_ADMIN_DIR):
    app.mount("/admin-static", StaticFiles(directory=_ADMIN_DIR), name="admin-static")


@app.get("/widget.js")
async def widget_js() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.js"),
                        media_type="application/javascript")


@app.get("/widget.css")
async def widget_css() -> FileResponse:
    return FileResponse(os.path.join(_FRONTEND_DIR, "widget.css"),
                        media_type="text/css")


@app.get("/test.html", response_class=HTMLResponse)
async def test_html() -> HTMLResponse:
    return HTMLResponse(_render_test_page())
