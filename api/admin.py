"""Admin dashboard data + management API (Phase 2). All routes behind require_admin.

Aggregation is done in SQL (db.py); derived rates live in metrics.py. Every
destructive action writes an `admin_events` audit row (invariant §15.5).
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import auth
import config
import db
import kb
import language
import metrics
import openai_client
import prompts
import settings as settings_mod
import tenancy
import translations as translations_mod
from api import admin_auth
from api.admin_auth import require_admin, require_admin_write, WRITE_ROLES

# Router-level dependency guards every route. Some handlers ALSO declare
# `admin=Depends(require_admin)` to read the resolved role from the token; FastAPI
# caches the dependency per request, so it is verified once despite the repeat.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _resolve_admin_product(admin: dict, product_id: Optional[int],
                                 *, write: bool) -> int:
    """Resolve + authorize the product an admin route acts on.

    No explicit product -> the boot-seeded default product (single-product
    back-compat: the pre-tenancy SPA and API clients keep working). The scope
    check (read or write) runs against the resolved product, and the request's
    tenancy scope is bound so every settings/KB/translations read below
    resolves for that product.
    """
    if product_id is None:
        product = await db.get_default_product()
        if product is None:
            raise HTTPException(status_code=404,
                                detail="No default product configured.")
        product_id = product["id"]
    if write:
        await admin_auth.require_product_write(admin, product_id)
    else:
        await admin_auth.require_product_read(admin, product_id)
    tenancy.set_current_product(product_id)
    return product_id


# ---------------------------------------------------------------------------
# static metadata (supported languages, defaults) for the admin SPA
# ---------------------------------------------------------------------------
@router.get("/meta")
async def meta(product_id: Optional[int] = None,
               admin=Depends(require_admin)) -> JSONResponse:
    """Static config the admin UI needs to build pickers (e.g. language dropdowns).

    Keeps the SPA in sync with the configured languages instead of hard-coding
    the list in JS. With a product selected the language set/default resolve
    for that product (its `language` settings override).
    """
    if product_id is not None:
        await _resolve_admin_product(admin, product_id, write=False)
    # `languages` is the SELECTABLE catalogue (built-ins + admin-added + currently
    # supported) so the language tab can render a checkbox per known language, not
    # only the ones already enabled. `iso_catalog` is the full ISO 639-1 list that
    # drives the "add a language" picker. `supported` flags which are enabled.
    return JSONResponse(content={
        "languages": language.selectable_languages(),
        "supported": language.supported_codes(),
        "default_language": language.default_code(),
        "iso_catalog": [{"code": c, "name": n}
                        for c, n in sorted(language.ISO_639_1.items(),
                                           key=lambda kv: kv[1])],
    })


# ---------------------------------------------------------------------------
# date-range helper
# ---------------------------------------------------------------------------
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_dt(value: Optional[str], default: datetime) -> datetime:
    if not value:
        return default
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        # bare date (YYYY-MM-DD) or junk -> try date, else default
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _range(from_: Optional[str], to: Optional[str]) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    dt_to = _parse_dt(to, now)
    # Admin date inputs send YYYY-MM-DD. The SQL range is half-open, so treating
    # the `to` date as midnight excluded the whole selected day (including
    # today's sessions on the default dashboard). Make date-only upper bounds
    # inclusive in the UI by querying up to the next midnight.
    if to and _DATE_ONLY_RE.match(to):
        dt_to = dt_to + timedelta(days=1)
    dt_from = _parse_dt(from_, now - timedelta(days=30))
    return dt_from, dt_to


# ---------------------------------------------------------------------------
# dashboard data
# ---------------------------------------------------------------------------
@router.get("/overview")
async def overview(from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None,
                   product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   admin=Depends(require_admin)) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    raw = await db.overview_aggregates(dt_from, dt_to, product_ids=scope)
    return JSONResponse(content=metrics.overview(raw))


@router.get("/timeseries")
async def timeseries(metric: str = "sessions", bucket: str = "day",
                     from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None,
                     product_id: Optional[int] = None,
                     partner_id: Optional[int] = None,
                     admin=Depends(require_admin)) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    series = await db.timeseries(metric, dt_from, dt_to, bucket=bucket,
                                 product_ids=scope)
    return JSONResponse(content={"metric": metric, "bucket": bucket, "series": series})


@router.get("/by-topic")
async def by_topic(from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None,
                   product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   admin=Depends(require_admin)) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    return JSONResponse(content={"topics": await db.by_topic(dt_from, dt_to,
                                                             product_ids=scope)})


@router.get("/by-language")
async def by_language(from_: Optional[str] = Query(default=None, alias="from"),
                      to: Optional[str] = None,
                      product_id: Optional[int] = None,
                      partner_id: Optional[int] = None,
                      admin=Depends(require_admin)) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    return JSONResponse(content={"languages": await db.by_language(
        dt_from, dt_to, product_ids=scope)})


@router.get("/sessions")
async def sessions(from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None, topic: Optional[str] = None,
                   lang: Optional[str] = None, status: Optional[str] = None,
                   escalated: Optional[bool] = None, q: Optional[str] = None,
                   min_messages: Optional[int] = None,
                   product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   page: int = 1,
                   admin=Depends(require_admin)) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    res = await db.list_sessions(dt_from, dt_to, topic=topic, lang=lang,
                                 status=status, escalated=escalated, q=q,
                                 min_messages=min_messages, product_ids=scope,
                                 page=page)
    return JSONResponse(content=res)


@router.get("/session/{session_id}")
async def session(session_id: str, admin=Depends(require_admin)) -> JSONResponse:
    # Validate up front: a non-UUID path segment would otherwise hit the UUID
    # column and bubble out of asyncpg as a 500 (the chat API never gets here
    # because its session tokens only ever carry real UUIDs).
    try:
        _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found.")
    detail = await db.session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    # Scope check: a session belongs to a product; only accounts with reach
    # over that product may open its transcript.
    sess_product = (detail.get("session") or {}).get("product_id")
    if sess_product is not None:
        await admin_auth.require_product_read(admin, sess_product)
    elif admin_auth.global_role(admin) is None:
        raise HTTPException(status_code=403, detail="No access to this session.")
    return JSONResponse(content=detail)


@router.delete("/session/{session_id}")
async def delete_session(session_id: str,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    """Hard-delete a session (support or Telegram) and all its rows.

    Admin-only (require_admin_write) and additionally write-scoped to the
    session's owning product — a manager, or an admin without reach over the
    product, is refused. Used by the Conversations / Unresolved / Telegram-chats
    delete controls.
    """
    try:
        _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found.")
    detail = await db.session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    sess_product = (detail.get("session") or {}).get("product_id")
    if sess_product is not None:
        await admin_auth.require_product_write(admin, sess_product)
    else:
        admin_auth.require_global_write(admin)
    await db.delete_session(session_id)
    await db.log_admin_event(None, "session_deleted", {"session_id": session_id})
    return JSONResponse(content={"ok": True, "id": session_id})


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection in player-controlled CSV cells.

    A message starting with '=', '+', '-', '@' (or a tab/CR) becomes a live
    formula when the export is opened in Excel/Sheets — classic CSV injection.
    Prefixing a single quote makes the cell render as literal text.
    """
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/unresolved")
async def unresolved(from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None, format: str = "json",
                     product_id: Optional[int] = None,
                     partner_id: Optional[int] = None,
                     admin=Depends(require_admin)) -> Any:
    dt_from, dt_to = _range(from_, to)
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    groups = await db.unresolved_by_topic(dt_from, dt_to, product_ids=scope)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["topic", "session_id", "lang", "status", "escalated",
                    "message_count", "cost_usd_total", "first_message", "created_at"])
        for g in groups:
            for s in g["sessions"]:
                w.writerow([g["topic"], s["session_id"], s.get("lang"), s.get("status"),
                            s.get("escalated"), s["message_count"],
                            s.get("cost_usd_total"),
                            _csv_safe((s["first_message"] or "").replace("\n", " ")),
                            s["created_at"]])
        return PlainTextResponse(content=buf.getvalue(), media_type="text/csv")
    return JSONResponse(content={"groups": groups})


# ---------------------------------------------------------------------------
# KB management (product-scoped)
# ---------------------------------------------------------------------------
class TopicUpsert(BaseModel):
    slug: str
    title: dict[str, str]
    order: int = 0
    active: bool = True
    product_id: Optional[int] = None


class KBContentWrite(BaseModel):
    topic_id: int
    content: str


async def _topic_for_write(admin: dict, topic_id: int) -> dict:
    """Load a topic and authorize a WRITE on its owning product."""
    topic = await db.get_topic_by_id(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    await _resolve_admin_product(admin, topic.get("product_id"), write=True)
    return topic


@router.get("/kb/topics")
async def kb_topics(product_id: Optional[int] = None,
                    admin=Depends(require_admin)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=False)
    return JSONResponse(content={"topics": await db.list_topics_with_counts(pid)})


@router.post("/kb/topics")
async def kb_upsert_topic(body: TopicUpsert,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, body.product_id, write=True)
    tid = await db.upsert_topic(product_id=pid, slug=body.slug, title=body.title,
                                display_order=body.order, active=body.active)
    await db.log_admin_event(None, "kb_topic_upserted", {"id": tid, "slug": body.slug})
    return JSONResponse(content=await db.get_topic_by_id(tid))


@router.get("/kb/content")
async def kb_content(topic_id: int, admin=Depends(require_admin)) -> JSONResponse:
    """The topic's single KB text (one entry per topic), or null when empty."""
    topic = await db.get_topic_by_id(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    await _resolve_admin_product(admin, topic.get("product_id"), write=False)
    entry = await db.get_kb_entry(topic_id)
    return JSONResponse(content={"content": entry["content"] if entry else None})


@router.put("/kb/content")
async def kb_set_content(body: KBContentWrite,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    await _topic_for_write(admin, body.topic_id)
    eid = await db.set_kb_content(body.topic_id, body.content)
    await db.log_admin_event(None, "kb_content_updated", {"topic_id": body.topic_id})
    return JSONResponse(content={"id": eid})


@router.delete("/kb/content")
async def kb_clear_content(topic_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    await _topic_for_write(admin, topic_id)
    ok = await db.clear_kb_content(topic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Topic has no KB to clear.")
    await db.log_admin_event(None, "kb_content_cleared", {"topic_id": topic_id})
    return JSONResponse(content={"ok": True})




# ---------------------------------------------------------------------------
# KB variables
# ---------------------------------------------------------------------------
class KBVariableWrite(BaseModel):
    key: str = Field(..., pattern=r"^[A-Za-z0-9_]+$")
    description: str = ""
    value: str = ""


@router.get("/kb/variables")
async def kb_variables(product_id: Optional[int] = None,
                       admin=Depends(require_admin)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=False)
    return JSONResponse(content={"variables": await db.list_kb_variables(pid)})


@router.put("/kb/variables/{key}")
async def kb_set_variable(key: str, body: KBVariableWrite,
                          product_id: Optional[int] = None,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    if body.key != key:
        raise HTTPException(status_code=400, detail="Path key and body key must match.")
    pid = await _resolve_admin_product(admin, product_id, write=True)
    item = await db.set_kb_variable(
        product_id=pid,
        key=key,
        description=body.description.strip(),
        value=body.value.strip(),
        # The audit trail records WHO acted — the named account, not its role.
        updated_by=admin.get("email") or admin.get("role"),
    )
    await db.log_admin_event(None, "kb_variable_updated", {"key": key})
    return JSONResponse(content={"variable": item})


# ---------------------------------------------------------------------------
# prompt variables — the brand-uniquification values for the prompt template
#
# The prompt WORDING stays in prompts.py (the single source of truth, read-only
# from the admin); these are the {placeholder} values (persona name, brand,
# products, tone of voice, …) that uniquify it per brand. Stored under their own
# app_settings key (like test_profile), hot-reloaded, edited from the Prompt tab.
# ---------------------------------------------------------------------------
class PromptVariablesWrite(BaseModel):
    value: Any = Field(...)


class SiteMapWrite(BaseModel):
    value: Any = Field(...)


@router.get("/prompt-variables")
async def get_prompt_variables(product_id: Optional[int] = None,
                               admin=Depends(require_admin)) -> JSONResponse:
    await _resolve_admin_product(admin, product_id, write=False)
    resolved = settings_mod.prompt_variables()
    return JSONResponse(content={"variables": [
        {"key": key, "description": desc, "default": default,
         "value": resolved.get(key, default)}
        for key, desc, default in prompts.PROMPT_VARIABLES
    ]})


@router.put("/prompt-variables")
async def put_prompt_variables(body: PromptVariablesWrite,
                               product_id: Optional[int] = None,
                               admin=Depends(require_admin_write)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=True)
    try:
        validated = settings_mod.validate_prompt_variables(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Multi-tenancy: the persona/brand values are the per-casino uniquification
    # seam, so they are stored on the PRODUCT (product_settings), never in the
    # global app_settings — each casino renders the shared prompt template with
    # its own brand.
    await db.set_product_setting(pid, "prompt_variables", validated,
                                 updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: the next prompt build renders new values
    await db.log_admin_event(None, "prompt_variables_updated",
                             {"keys": sorted(validated)})
    return JSONResponse(content={"variables": [
        {"key": key, "description": desc, "default": default,
         "value": settings_mod.prompt_variables().get(key, default)}
        for key, desc, default in prompts.PROMPT_VARIABLES
    ]})


# ---------------------------------------------------------------------------
# site map — the product's official pages the model may link to (Layer 1).
#
# A single per-product setting (list of {title, url, purpose}) injected into the
# byte-stable Layer-1 core of BOTH the support and the retention bot, and named
# in each core's links policy. Stored on the product (like prompt_variables),
# outside the generic Settings editor. See prompts.render_site_map_block /
# settings.site_map.
# ---------------------------------------------------------------------------
@router.get("/site-map")
async def get_site_map(product_id: Optional[int] = None,
                       admin=Depends(require_admin)) -> JSONResponse:
    await _resolve_admin_product(admin, product_id, write=False)
    return JSONResponse(content={"pages": settings_mod.site_map()})


@router.put("/site-map")
async def put_site_map(body: SiteMapWrite,
                       product_id: Optional[int] = None,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=True)
    try:
        validated = settings_mod.validate_site_map(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_product_setting(pid, "site_map", validated,
                                 updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: the next prompt build renders new pages
    await db.log_admin_event(None, "site_map_updated", {"count": len(validated)})
    return JSONResponse(content={"pages": settings_mod.site_map()})


# ---------------------------------------------------------------------------
# translations — the user-facing copy registry (widget chrome + server turns)
#
# Defaults live in translations.py; the admin stores per-language overrides
# ({lang: {key: text}}) under their own app_settings key. The widget picks the
# widget-scope strings up via GET /api/chat/i18n; the server-side copy
# (escalation card, closing bubble, nudges) resolves through the same registry.
# ---------------------------------------------------------------------------
class TranslationsWrite(BaseModel):
    value: Any = Field(...)


@router.get("/translations")
async def get_translations(product_id: Optional[int] = None,
                           admin=Depends(require_admin)) -> JSONResponse:
    await _resolve_admin_product(admin, product_id, write=False)
    codes = language.supported_codes()
    names = language.all_language_names()
    return JSONResponse(content={
        "keys": [{"key": key, "scope": scope, "description": desc}
                 for key, scope, desc in translations_mod.KEYS],
        "languages": [{"code": c, "name": names.get(c, c.upper())} for c in codes],
        # resolved = what the player currently sees; defaults = what an empty
        # override falls back to (the SPA stores only values that differ).
        "resolved": translations_mod.resolved(codes),
        "defaults": translations_mod.defaults_for(codes),
        "overrides": settings_mod.translations(),
    })


@router.put("/translations")
async def put_translations(body: TranslationsWrite,
                           product_id: Optional[int] = None,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=True)
    try:
        validated = settings_mod.validate_translations(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Player-facing copy is brand material -> stored on the PRODUCT, like the
    # prompt variables. Legacy global overrides (app_settings) keep resolving
    # underneath until a product override shadows them.
    await db.set_product_setting(pid, "translations", validated,
                                 updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: applies to new turns / the next i18n fetch
    await db.log_admin_event(None, "translations_updated",
                             {"languages": sorted(validated)})
    return JSONResponse(content={
        "resolved": translations_mod.resolved(language.supported_codes()),
        "overrides": settings_mod.translations(),
    })


# ---------------------------------------------------------------------------
# runtime settings
# ---------------------------------------------------------------------------
class SettingWrite(BaseModel):
    value: Any = Field(...)


@router.get("/settings")
async def get_settings(product_id: Optional[int] = None,
                       admin=Depends(require_admin)) -> JSONResponse:
    """Resolved (effective) values plus the raw overrides of the EDITED layer.

    With a product selected: resolved = product > global > env > default, and
    `overrides` is the product's own stored layer (what the editor round-trips).
    Without one: the global layer, as before (global scope required — the
    deploy-wide defaults are the hub owner's knobs).
    """
    if product_id is not None:
        pid = await _resolve_admin_product(admin, product_id, write=False)
        overrides = await db.get_product_settings(pid)
    else:
        if admin_auth.global_role(admin) is None:
            raise HTTPException(status_code=403,
                                detail="Global settings need a global account.")
        overrides = await db.get_all_settings()
    return JSONResponse(content={
        "resolved": settings_mod.resolved_all(),
        "overrides": overrides,
        "keys": list(settings_mod.SETTING_KEYS),
    })


@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingWrite,
                      product_id: Optional[int] = None,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    try:
        validated = settings_mod.validate_setting(key, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if product_id is not None:
        # Per-product operational knobs (the casino owner's layer).
        pid = await _resolve_admin_product(admin, product_id, write=True)
        await db.set_product_setting(pid, key, validated,
                                     updated_by=admin.get("email") or admin.get("role"))
    else:
        # Deploy-wide defaults every product inherits — global admins only.
        admin_auth.require_global_write(admin)
        await db.set_setting(key, validated,
                             updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: invalidate + repopulate cache
    if key == "model":
        # request_timeout + per-key concurrency are bound at client build time;
        # rebuild so the new values take effect (the rest is read live).
        openai_client.reset()
    await db.log_admin_event(None, "setting_updated", {"key": key})
    return JSONResponse(content={"key": key, "value": validated,
                                 "resolved": settings_mod.resolved_all()})


# ---------------------------------------------------------------------------
# test/dev sandbox profile
#
# In production the host site supplies the player's user_context over a signed
# handshake; in test/dev (no WIDGET_HANDSHAKE_SECRET) there is no host, so this
# stored profile stands in for it. It drives the Layer-3 player data the model
# sees and (optionally) pins the session language. Applied in api/chat.create_session.
# ---------------------------------------------------------------------------
class TestProfileWrite(BaseModel):
    value: Any = Field(...)


@router.get("/test-profile")
async def get_test_profile(product_id: Optional[int] = None,
                           admin=Depends(require_admin)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=False)
    product_handshake = await db.get_product_handshake_secret(pid)
    return JSONResponse(content={
        "profile": settings_mod.test_profile(),
        "languages": [{"code": c, "name": language.LANG_NAMES.get(c, c.upper())}
                      for c in language.supported_codes()],
        # When a handshake secret is set (product or deploy) the host site is
        # authoritative and this profile is ignored at session create — surface
        # that so the UI can warn.
        "active": not bool(product_handshake or config.WIDGET_HANDSHAKE_SECRET),
    })


@router.put("/test-profile")
async def put_test_profile(body: TestProfileWrite,
                           product_id: Optional[int] = None,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    pid = await _resolve_admin_product(admin, product_id, write=True)
    try:
        validated = settings_mod.validate_test_profile(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_product_setting(pid, "test_profile", validated,
                                 updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: applies to the next session created
    await db.log_admin_event(None, "test_profile_updated", {})
    return JSONResponse(content={"profile": settings_mod.test_profile()})


# ---------------------------------------------------------------------------
# Current admin identity (so the SPA can role-gate its UI after a reload)
# ---------------------------------------------------------------------------
@router.get("/me")
async def me(admin=Depends(require_admin)) -> JSONResponse:
    return JSONResponse(content={
        "role": admin.get("role"),
        "email": admin.get("email"),
        "can_write": admin.get("role") in WRITE_ROLES,
        # Scope info for the SPA: the header switcher + per-tab gating derive
        # from these (the server stays authoritative on every request).
        "memberships": admin.get("memberships", []),
        "global_role": admin_auth.global_role(admin),
    })


# ---------------------------------------------------------------------------
# User management — named accounts + scope memberships (multi-tenancy)
#
# An account is an email + password; WHAT it may touch is its memberships
# (global / partner / product, each with role admin|manager). An admin manages
# users strictly within its own reach: it may grant/revoke memberships only for
# scopes it holds an admin role over, and may edit/delete only accounts whose
# ENTIRE membership set lies inside that reach (so a product admin can never
# touch a global account). No email delivery, no reset flows, no enumeration.
# Passwords are stored only as a salted PBKDF2 hash; it never leaves db.py.
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER_ROLES = ("admin", "manager")


class MembershipSpec(BaseModel):
    scope_type: str = "global"
    partner_id: Optional[int] = None
    product_id: Optional[int] = None
    role: str = "manager"


class UserCreate(BaseModel):
    email: str
    password: str
    # Initial membership. The flat `role` alone (old API shape) still works and
    # means "global <role>", so pre-tenancy clients keep functioning.
    role: str = "manager"
    scope_type: str = "global"
    partner_id: Optional[int] = None
    product_id: Optional[int] = None


class UserUpdate(BaseModel):
    password: Optional[str] = None
    # Legacy shape: a bare role update means the GLOBAL membership's role.
    role: Optional[str] = None
    active: Optional[bool] = None


def _validate_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return e


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise HTTPException(status_code=400,
                            detail="Password must be at least 8 characters.")


def _validate_role(role: str) -> str:
    if role not in _USER_ROLES:
        raise HTTPException(status_code=400,
                            detail=f"Role must be one of: {', '.join(_USER_ROLES)}.")
    return role


async def _validate_scope(spec: MembershipSpec) -> tuple[str, Optional[int], Optional[int]]:
    """Normalize + verify a membership scope (type/ids consistent, targets exist)."""
    scope = (spec.scope_type or "global").strip().lower()
    if scope not in admin_auth.SCOPE_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"scope_type must be one of: "
                                   f"{', '.join(admin_auth.SCOPE_TYPES)}.")
    partner_id = spec.partner_id if scope == "partner" else None
    product_id = spec.product_id if scope == "product" else None
    if scope == "partner":
        if not partner_id or await db.get_partner(partner_id) is None:
            raise HTTPException(status_code=400, detail="Unknown partner.")
    if scope == "product":
        if not product_id or await db.get_product(product_id) is None:
            raise HTTPException(status_code=400, detail="Unknown product.")
    return scope, partner_id, product_id


async def _require_scope_admin(admin: dict, scope: str,
                               partner_id: Optional[int],
                               product_id: Optional[int]) -> None:
    """The caller must hold an ADMIN role over the scope being granted/revoked."""
    if scope == "global":
        admin_auth.require_global_write(admin)
    elif scope == "partner":
        if admin_auth.role_for_partner(admin, partner_id) not in WRITE_ROLES:
            raise HTTPException(status_code=403,
                                detail="This action requires an administrator "
                                       "role over this partner.")
    else:
        await admin_auth.require_product_write(admin, product_id)


async def _can_manage_user(admin: dict, target_memberships: list[dict]) -> bool:
    """True when every scope the target holds is inside the caller's admin reach.

    An account with NO memberships is manageable only globally (otherwise any
    product admin could take over orphan accounts).
    """
    if admin_auth.global_role(admin) in WRITE_ROLES:
        return True
    if not target_memberships:
        return False
    for m in target_memberships:
        if m["scope_type"] == "global":
            return False
        if m["scope_type"] == "partner":
            if admin_auth.role_for_partner(admin, m["partner_id"]) not in WRITE_ROLES:
                return False
        elif (await admin_auth.role_for_product(admin, m["product_id"])) not in WRITE_ROLES:
            return False
    return True


@router.get("/users")
async def list_users(admin=Depends(require_admin_write)) -> JSONResponse:
    """Accounts within the caller's reach, each with its memberships attached."""
    users = await db.list_admin_users()
    all_memberships = await db.list_all_memberships()
    by_email: dict[str, list] = {}
    for m in all_memberships:
        by_email.setdefault(m["email"], []).append(m)
    out = []
    for u in users:
        ms = by_email.get(u["email"], [])
        if u["email"] == admin.get("email") or await _can_manage_user(admin, ms):
            out.append({**u, "memberships": ms})
    return JSONResponse(content={"users": out})


@router.post("/users")
async def create_user(body: UserCreate,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    email = _validate_email(body.email)
    _validate_password(body.password)
    role = _validate_role(body.role)
    scope, partner_id, product_id = await _validate_scope(MembershipSpec(
        scope_type=body.scope_type, partner_id=body.partner_id,
        product_id=body.product_id, role=role))
    await _require_scope_admin(admin, scope, partner_id, product_id)
    if await db.get_admin_user(email):
        raise HTTPException(status_code=409, detail="A user with that email already exists.")
    # PBKDF2 hashing is CPU-bound — keep it off the event loop.
    pw_hash = await asyncio.to_thread(auth.hash_password, body.password)
    user = await db.create_admin_user(email, pw_hash, role)
    membership = await db.add_membership(email, scope, partner_id, product_id, role)
    await db.log_admin_event(None, "admin_user_created",
                             {"email": email, "role": role, "scope": scope,
                              "by": admin.get("email")})
    return JSONResponse(content={"user": {**user, "memberships": [membership]}})


@router.post("/users/{email}/memberships")
async def grant_membership(email: str, body: MembershipSpec,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    if not await db.get_admin_user(target):
        raise HTTPException(status_code=404, detail="User not found.")
    if admin.get("email") == target:
        raise HTTPException(status_code=400,
                            detail="You cannot change your own memberships.")
    role = _validate_role(body.role)
    scope, partner_id, product_id = await _validate_scope(body)
    await _require_scope_admin(admin, scope, partner_id, product_id)
    membership = await db.add_membership(target, scope, partner_id, product_id, role)
    await db.log_admin_event(None, "admin_membership_granted",
                             {"email": target, "scope": scope, "role": role,
                              "by": admin.get("email")})
    return JSONResponse(content={"membership": membership,
                                 "memberships": await db.memberships_for(target)})


@router.delete("/users/{email}/memberships/{membership_id}")
async def revoke_membership(email: str, membership_id: int,
                            admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    if admin.get("email") == target:
        raise HTTPException(status_code=400,
                            detail="You cannot change your own memberships.")
    membership = await db.get_membership(membership_id)
    if membership is None or membership.get("email") != target:
        raise HTTPException(status_code=404, detail="Membership not found.")
    await _require_scope_admin(admin, membership["scope_type"],
                               membership.get("partner_id"),
                               membership.get("product_id"))
    await db.delete_membership(membership_id)
    await db.log_admin_event(None, "admin_membership_revoked",
                             {"email": target, "scope": membership["scope_type"],
                              "by": admin.get("email")})
    return JSONResponse(content={"ok": True,
                                 "memberships": await db.memberships_for(target)})


@router.put("/users/{email}")
async def update_user(email: str, body: UserUpdate,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    existing = await db.get_admin_user(target)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
    target_memberships = await db.memberships_for(target)
    if admin.get("email") != target and \
            not await _can_manage_user(admin, target_memberships):
        raise HTTPException(status_code=403,
                            detail="No administrator reach over this account.")
    role = _validate_role(body.role) if body.role is not None else None
    pw_hash = None
    if body.password is not None:
        _validate_password(body.password)
        pw_hash = await asyncio.to_thread(auth.hash_password, body.password)
    # Guard against self-lockout: an account cannot demote or deactivate itself
    # in the same session. With no owner password recovery path, keep at least a
    # second admin account so a forgotten password never locks everyone out.
    if admin.get("email") == target:
        if role is not None and role not in WRITE_ROLES:
            raise HTTPException(status_code=400, detail="You cannot remove your own admin role.")
        if body.active is False:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    user = await db.update_admin_user(target, role=role, active=body.active,
                                      password_hash=pw_hash)
    if role is not None:
        # Legacy shape: a flat role update targets the GLOBAL membership. Writing
        # a global membership is a global-scope change, so it ALWAYS requires
        # global write — including when the caller edits their OWN account.
        # (Without this, a product/partner-scoped admin — coarse role "admin", so
        # it clears require_admin_write — could PUT its own account with
        # role="admin" and self-grant a global membership, escalating to full hub
        # control. The self-branch must never bypass the scope check.)
        admin_auth.require_global_write(admin)
        await db.add_membership(target, "global", None, None, role)
    await db.log_admin_event(None, "admin_user_updated",
                             {"email": target, "by": admin.get("email")})
    return JSONResponse(content={"user": {**(user or {}),
                                          "memberships": await db.memberships_for(target)}})


@router.delete("/users/{email}")
async def delete_user(email: str,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    if admin.get("email") == target:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if not await _can_manage_user(admin, await db.memberships_for(target)):
        raise HTTPException(status_code=403,
                            detail="No administrator reach over this account.")
    ok = await db.delete_admin_user(target)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    await db.log_admin_event(None, "admin_user_deleted",
                             {"email": target, "by": admin.get("email")})
    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# Service API keys — machine credentials for the /admin API (external master
# admin panels, partner backends). Bearer `sak_...` tokens; the plaintext is
# returned exactly ONCE at creation, only the SHA-256 hash is stored. Each key
# carries one role at one scope (like a membership); require_admin translates
# it into a synthetic membership so every scope check applies unchanged.
# Managing keys requires a HUMAN admin account — a leaked key must not be able
# to mint further keys.
# ---------------------------------------------------------------------------
class ApiKeyCreate(BaseModel):
    name: str
    role: str = "manager"
    scope_type: str = "global"
    partner_id: Optional[int] = None
    product_id: Optional[int] = None


class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None


def _require_human_admin(admin: dict) -> None:
    if admin.get("api_key_id") is not None:
        raise HTTPException(status_code=403,
                            detail="API keys are managed by named admin "
                                   "accounts only.")


async def _api_key_visible(admin: dict, key: dict) -> bool:
    """A key is visible/manageable when its scope lies inside the caller's
    ADMIN reach (managers never see keys — they carry credentials)."""
    scope = key.get("scope_type")
    if scope == "global":
        return admin_auth.global_role(admin) in WRITE_ROLES
    if scope == "partner":
        return admin_auth.role_for_partner(admin, key.get("partner_id")) in WRITE_ROLES
    return (await admin_auth.role_for_product(admin, key.get("product_id"))) in WRITE_ROLES


@router.get("/api-keys")
async def list_api_keys(admin=Depends(require_admin_write)) -> JSONResponse:
    _require_human_admin(admin)
    keys = [k for k in await db.list_admin_api_keys()
            if await _api_key_visible(admin, k)]
    return JSONResponse(content={"keys": keys})


@router.post("/api-keys")
async def create_api_key(body: ApiKeyCreate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    _require_human_admin(admin)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Key name is required.")
    role = _validate_role(body.role)
    scope, partner_id, product_id = await _validate_scope(MembershipSpec(
        scope_type=body.scope_type, partner_id=body.partner_id,
        product_id=body.product_id, role=role))
    await _require_scope_admin(admin, scope, partner_id, product_id)
    key, token = await db.create_admin_api_key(
        name=name, role=role, scope_type=scope, partner_id=partner_id,
        product_id=product_id, created_by=admin.get("email"))
    await db.log_admin_event(None, "admin_api_key_created",
                             {"id": key["id"], "name": name, "scope": scope,
                              "role": role, "by": admin.get("email")})
    # The ONE response that ever carries the plaintext token.
    return JSONResponse(content={"key": key, "token": token})


@router.put("/api-keys/{key_id}")
async def update_api_key(key_id: int, body: ApiKeyUpdate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    _require_human_admin(admin)
    key = await db.get_admin_api_key(key_id)
    if key is None or not await _api_key_visible(admin, key):
        raise HTTPException(status_code=404, detail="Key not found.")
    updated = await db.update_admin_api_key(
        key_id, active=body.active, name=(body.name or "").strip() or None)
    await db.log_admin_event(None, "admin_api_key_updated",
                             {"id": key_id, "by": admin.get("email")})
    return JSONResponse(content={"key": updated})


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: int,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    _require_human_admin(admin)
    key = await db.get_admin_api_key(key_id)
    if key is None or not await _api_key_visible(admin, key):
        raise HTTPException(status_code=404, detail="Key not found.")
    await db.delete_admin_api_key(key_id)
    await db.log_admin_event(None, "admin_api_key_deleted",
                             {"id": key_id, "name": key.get("name"),
                              "by": admin.get("email")})
    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# Structure — partners & products (the multi-tenancy tree + the header switcher)
#
# GET /structure feeds the Partner -> Product switcher in the admin header: it
# returns only what the caller can see. Partner lifecycle is global-admin-only;
# products are managed by global admins and the owning partner's admins.
# Per-product secrets (OpenAI keys, handshake secret) are WRITE-ONLY: the API
# accepts them, stores them encrypted, and ever after reports only has_* flags.
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")


class PartnerCreate(BaseModel):
    slug: str
    name: str


class PartnerUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None


class ProductCreate(BaseModel):
    partner_id: int
    slug: str
    name: str


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    # Per-product reCAPTCHA site key (PUBLIC config, pairs with the encrypted
    # recaptcha_secret below). Empty string clears it (env fallback applies).
    recaptcha_site_key: Optional[str] = None


class ProductSecretsWrite(BaseModel):
    # Absent field = leave unchanged; empty string = clear (fall back to env).
    openai_key_primary: Optional[str] = None
    openai_key_fallback: Optional[str] = None
    handshake_secret: Optional[str] = None
    # Retention / Telegram secrets (encrypted at rest, like the keys above).
    telegram_bot_token: Optional[str] = None
    player_api_key: Optional[str] = None
    # Per-product (per-domain) reCAPTCHA secret — each client site runs its own
    # reCAPTCHA property, so the pair lives on the product, not in deploy env.
    recaptcha_secret: Optional[str] = None


def _validate_slug(slug: str) -> str:
    s = (slug or "").strip().lower()
    if not _SLUG_RE.match(s):
        raise HTTPException(status_code=400,
                            detail="Slug must be 2-40 chars: a-z, 0-9, '-'.")
    return s


@router.get("/structure")
async def structure(admin=Depends(require_admin)) -> JSONResponse:
    """The partner -> products tree the caller may see (for the header switcher)."""
    accessible = await admin_auth.accessible_product_ids(admin)  # None = all
    partners = await db.list_partners()
    products = await db.list_products(product_ids=accessible)
    by_partner: dict[int, list] = {}
    for p in products:
        by_partner.setdefault(p["partner_id"], []).append(p)
    out = []
    for pa in partners:
        visible_products = by_partner.get(pa["id"], [])
        partner_role = admin_auth.role_for_partner(admin, pa["id"])
        # A partner appears when the caller has partner/global scope over it or
        # can see at least one of its products.
        if partner_role is None and not visible_products:
            continue
        out.append({**pa, "products": visible_products, "role": partner_role})
    return JSONResponse(content={
        "partners": out,
        "global_role": admin_auth.global_role(admin),
    })


@router.post("/partners")
async def create_partner(body: PartnerCreate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    admin_auth.require_global_write(admin)
    slug = _validate_slug(body.slug)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Partner name is required.")
    partner = await db.create_partner(slug, name)
    if partner is None:
        raise HTTPException(status_code=409, detail="That slug is already taken.")
    await db.log_admin_event(None, "partner_created",
                             {"id": partner["id"], "slug": slug,
                              "by": admin.get("email")})
    return JSONResponse(content={"partner": partner})


@router.put("/partners/{partner_id}")
async def update_partner(partner_id: int, body: PartnerUpdate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    admin_auth.require_global_write(admin)
    partner = await db.update_partner(partner_id, name=(body.name or "").strip() or None,
                                      active=body.active)
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner not found.")
    await db.log_admin_event(None, "partner_updated",
                             {"id": partner_id, "by": admin.get("email")})
    return JSONResponse(content={"partner": partner})


@router.post("/products")
async def create_product(body: ProductCreate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    if await db.get_partner(body.partner_id) is None:
        raise HTTPException(status_code=404, detail="Partner not found.")
    # Global admins and the owning partner's admins may add casinos to it.
    if admin_auth.role_for_partner(admin, body.partner_id) not in WRITE_ROLES:
        raise HTTPException(status_code=403,
                            detail="This action requires an administrator "
                                   "role over this partner.")
    slug = _validate_slug(body.slug)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Product name is required.")
    product = await db.create_product(body.partner_id, slug, name)
    if product is None:
        raise HTTPException(status_code=409, detail="That slug is already taken.")
    # create_product seeded the product's baseline prompt_variables into
    # product_settings — pull them into the in-process cache so the new
    # casino's very first prompt renders its own brand, not a stale scope.
    await settings_mod.reload()
    await db.log_admin_event(None, "product_created",
                             {"id": product["id"], "slug": slug,
                              "partner_id": body.partner_id,
                              "by": admin.get("email")},
                             product_id=product["id"])
    return JSONResponse(content={"product": product})


@router.put("/products/{product_id}")
async def update_product(product_id: int, body: ProductUpdate,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    product = await db.update_product(product_id,
                                      name=(body.name or "").strip() or None,
                                      active=body.active)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if body.recaptcha_site_key is not None:
        product = await db.set_product_recaptcha_site_key(
            product_id, body.recaptcha_site_key)
    await db.log_admin_event(None, "product_updated",
                             {"id": product_id, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"product": product})


@router.put("/products/{product_id}/secrets")
async def put_product_secrets(product_id: int, body: ProductSecretsWrite,
                              admin=Depends(require_admin_write)) -> JSONResponse:
    """Set/clear the product's OpenAI keys and handshake secret (encrypted at rest).

    Write-only by design: the plaintext is never echoed back, logged, or listed —
    the response (and every later read) carries only has_* presence flags.
    """
    await admin_auth.require_product_write(admin, product_id)
    fields: dict[str, Any] = {}
    if body.openai_key_primary is not None:
        fields["openai_key_primary"] = body.openai_key_primary
    if body.openai_key_fallback is not None:
        fields["openai_key_fallback"] = body.openai_key_fallback
    if body.handshake_secret is not None:
        fields["handshake_secret"] = body.handshake_secret
    if body.telegram_bot_token is not None:
        fields["telegram_bot_token"] = body.telegram_bot_token
    if body.player_api_key is not None:
        fields["player_api_key"] = body.player_api_key
    if body.recaptcha_secret is not None:
        fields["recaptcha_secret"] = body.recaptcha_secret
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    ok = await db.set_product_secrets(product_id, **fields)
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found.")
    # Product clients bind keys at construction — rebuild so a rotated key
    # takes effect on the next turn.
    openai_client.reset()
    await db.log_admin_event(None, "product_secrets_updated",
                             {"id": product_id, "fields": sorted(fields),
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"product": await db.get_product(product_id)})


@router.post("/products/{product_id}/widget-key")
async def rotate_widget_key(product_id: int,
                            admin=Depends(require_admin_write)) -> JSONResponse:
    """Rotate the public widget key. Old embeds stop resolving immediately."""
    await admin_auth.require_product_write(admin, product_id)
    new_key = await db.rotate_widget_key(product_id)
    if new_key is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    await db.log_admin_event(None, "widget_key_rotated",
                             {"id": product_id, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"widget_key": new_key})


# ---------------------------------------------------------------------------
# effective prompt — READ-ONLY view of the whole assembled prompt
#
# The prompt is sourced solely from `prompts.py` (the file is the single source
# of truth) and is NOT editable from the admin. This endpoint lets the owner SEE
# and verify the complete prompt the model receives — Layer 1 (byte-stable
# SYSTEM_CORE) + Layer 2 (the selected topic's KB) in the system message, and all
# Layer-3 directives (greeting, formatting, KB-grounding, escalation restraint,
# suggestions, resolved, topic routing, language, personalization, guardrails,
# forbidden topics) + the player context in the user message. We assemble the
# exact messages chat_service would send for the SAME example player the chat
# would actually use: the admin "Test sandbox" profile (the single source of the
# test player). There is NO separate hard-coded preview player — that diverged
# from the sandbox and showed one user in the prompt while the sandbox defined
# another. When the sandbox is disabled (or its fields are blank) the preview
# renders an anonymous session, so no invented player data appears anywhere.
# Nothing here is sent to the model — it's a faithful rendering of the live
# assembly so "how is the prompt formed?" has one answer in one place.
# ---------------------------------------------------------------------------
_PREVIEW_USER_TEXT = "\"...the player's current message will appear here...\""


def _preview_context() -> dict[str, Any]:
    """The player context for the preview: the admin Test-sandbox profile.

    Mirrors api/chat.create_session's dev/test path so the preview shows exactly
    the player the chat would use. When the sandbox is disabled the context is
    empty (anonymous session) — `prompts.build_dynamic_prompt` then renders no
    player-data lines and omits the personalization directive, so nothing
    invented is written.
    """
    tp = settings_mod.test_profile()
    if not tp.get("enabled"):
        return {}
    return {field: tp.get(field) or None
            for field in prompts._CONTEXT_FIELDS}


async def _build_effective_preview(product_id: Optional[int] = None
                                   ) -> dict[str, Any]:
    """Assemble the full prompt exactly as chat_service would, using the Test
    sandbox player.

    Returns the system message (Layer 1 SYSTEM_CORE + Layer 2 KB block) and the
    Layer-3 user message, plus a note of which example topic/language were used.
    Resilient by design: if topics/KB can't be loaded the preview still renders
    Layer 1 + the Layer-3 directives, so the page never breaks. The prompt
    variables / KB / language resolve for the request's product scope (set by
    the caller), so each casino previews ITS assembled prompt.
    """
    if product_id is None:
        product_id = tenancy.current_product_id()
    lang = language.default_code()
    current_topic: Optional[dict[str, Any]] = None
    kb_block: Optional[str] = None
    suggestable: list[dict[str, Any]] = []
    example_topic: Optional[str] = None
    try:
        topics = await db.list_topics(product_id)
        # Prefer a specialized topic (the common case) so the KB-grounding +
        # anchored routing directives are the ones shown.
        chosen = next((t for t in topics if t["slug"] != kb.OTHER_SLUG), None)
        if chosen is not None:
            current_topic = {
                "slug": chosen["slug"],
                "title": kb.localize_title(chosen.get("title"), lang),
            }
            example_topic = current_topic["title"]
            kb_block = await kb.kb_block_for_topic(chosen["id"])
            suggestable = await kb.suggestable_topics(
                exclude_topic_id=chosen["id"], lang=lang)
    except Exception:  # pragma: no cover - preview must never break the page
        current_topic, kb_block, suggestable = None, None, []

    messages = prompts.build_messages(
        session={"user_context": _preview_context()},
        kb_block=kb_block,
        history=[],
        user_text=_PREVIEW_USER_TEXT,
        resolved_lang=lang,
        available_topics=suggestable,
        current_topic=current_topic,
    )
    return {
        "system": messages[0]["content"],
        "user": messages[-1]["content"],
        "example": {
            "topic": example_topic,
            "lang": lang,
            "user_text": _PREVIEW_USER_TEXT,
        },
    }


@router.get("/effective-prompt")
async def get_effective_prompt(product_id: Optional[int] = None,
                               admin=Depends(require_admin)) -> JSONResponse:
    """Read-only: the whole prompt as assembled from prompts.py (the source of truth)."""
    pid = await _resolve_admin_product(admin, product_id, write=False)
    return JSONResponse(content={
        "effective_preview": await _build_effective_preview(pid)})
