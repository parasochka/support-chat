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
import translations as translations_mod
from api.admin_auth import require_admin, require_admin_write, WRITE_ROLES

# Router-level dependency guards every route. Some handlers ALSO declare
# `admin=Depends(require_admin)` to read the resolved role from the token; FastAPI
# caches the dependency per request, so it is verified once despite the repeat.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------------------
# static metadata (supported languages, defaults) for the admin SPA
# ---------------------------------------------------------------------------
@router.get("/meta")
async def meta() -> JSONResponse:
    """Static config the admin UI needs to build pickers (e.g. language dropdowns).

    Keeps the SPA in sync with the env-configured `SUPPORTED_LANGUAGES` instead
    of hard-coding the list in JS.
    """
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
                   to: Optional[str] = None) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    raw = await db.overview_aggregates(dt_from, dt_to)
    return JSONResponse(content=metrics.overview(raw))


@router.get("/timeseries")
async def timeseries(metric: str = "sessions", bucket: str = "day",
                     from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    series = await db.timeseries(metric, dt_from, dt_to, bucket=bucket)
    return JSONResponse(content={"metric": metric, "bucket": bucket, "series": series})


@router.get("/by-topic")
async def by_topic(from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content={"topics": await db.by_topic(dt_from, dt_to)})


@router.get("/by-language")
async def by_language(from_: Optional[str] = Query(default=None, alias="from"),
                      to: Optional[str] = None) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content={"languages": await db.by_language(dt_from, dt_to)})


@router.get("/sessions")
async def sessions(from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None, topic: Optional[str] = None,
                   lang: Optional[str] = None, status: Optional[str] = None,
                   escalated: Optional[bool] = None, q: Optional[str] = None,
                   min_messages: Optional[int] = None,
                   page: int = 1) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    res = await db.list_sessions(dt_from, dt_to, topic=topic, lang=lang,
                                 status=status, escalated=escalated, q=q,
                                 min_messages=min_messages, page=page)
    return JSONResponse(content=res)


@router.get("/session/{session_id}")
async def session(session_id: str) -> JSONResponse:
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
    return JSONResponse(content=detail)


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
                     to: Optional[str] = None, format: str = "json") -> Any:
    dt_from, dt_to = _range(from_, to)
    groups = await db.unresolved_by_topic(dt_from, dt_to)
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
# KB management
# ---------------------------------------------------------------------------
class TopicUpsert(BaseModel):
    slug: str
    title: dict[str, str]
    order: int = 0
    active: bool = True


class KBContentWrite(BaseModel):
    topic_id: int
    content: str


@router.get("/kb/topics")
async def kb_topics() -> JSONResponse:
    return JSONResponse(content={"topics": await db.list_topics_with_counts()})


@router.post("/kb/topics")
async def kb_upsert_topic(body: TopicUpsert,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    tid = await db.upsert_topic(slug=body.slug, title=body.title,
                                display_order=body.order, active=body.active)
    await db.log_admin_event(None, "kb_topic_upserted", {"id": tid, "slug": body.slug})
    return JSONResponse(content=await db.get_topic_by_id(tid))


@router.get("/kb/content")
async def kb_content(topic_id: int) -> JSONResponse:
    """The topic's single KB text (one entry per topic), or null when empty."""
    entry = await db.get_kb_entry(topic_id)
    return JSONResponse(content={"content": entry["content"] if entry else None})


@router.put("/kb/content")
async def kb_set_content(body: KBContentWrite,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    eid = await db.set_kb_content(body.topic_id, body.content)
    await db.log_admin_event(None, "kb_content_updated", {"topic_id": body.topic_id})
    return JSONResponse(content={"id": eid})


@router.delete("/kb/content")
async def kb_clear_content(topic_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
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
async def kb_variables() -> JSONResponse:
    return JSONResponse(content={"variables": await db.list_kb_variables()})


@router.put("/kb/variables/{key}")
async def kb_set_variable(key: str, body: KBVariableWrite, admin=Depends(require_admin_write)) -> JSONResponse:
    if body.key != key:
        raise HTTPException(status_code=400, detail="Path key and body key must match.")
    item = await db.set_kb_variable(
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
# platform, tone of voice, …) that uniquify it per brand. Stored under their own
# app_settings key (like test_profile), hot-reloaded, edited from the Prompt tab.
# ---------------------------------------------------------------------------
class PromptVariablesWrite(BaseModel):
    value: Any = Field(...)


@router.get("/prompt-variables")
async def get_prompt_variables() -> JSONResponse:
    resolved = settings_mod.prompt_variables()
    return JSONResponse(content={"variables": [
        {"key": key, "description": desc, "default": default,
         "value": resolved.get(key, default)}
        for key, desc, default in prompts.PROMPT_VARIABLES
    ]})


@router.put("/prompt-variables")
async def put_prompt_variables(body: PromptVariablesWrite,
                               admin=Depends(require_admin_write)) -> JSONResponse:
    try:
        validated = settings_mod.validate_prompt_variables(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_setting("prompt_variables", validated,
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
async def get_translations() -> JSONResponse:
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
                           admin=Depends(require_admin_write)) -> JSONResponse:
    try:
        validated = settings_mod.validate_translations(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_setting("translations", validated,
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
async def get_settings() -> JSONResponse:
    # Resolved (effective) values plus the raw DB overrides currently stored.
    return JSONResponse(content={
        "resolved": settings_mod.resolved_all(),
        "overrides": await db.get_all_settings(),
        "keys": list(settings_mod.SETTING_KEYS),
    })


@router.put("/settings/{key}")
async def put_setting(key: str, body: SettingWrite, admin=Depends(require_admin_write)
                      ) -> JSONResponse:
    try:
        validated = settings_mod.validate_setting(key, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
async def get_test_profile() -> JSONResponse:
    return JSONResponse(content={
        "profile": settings_mod.test_profile(),
        "languages": [{"code": c, "name": language.LANG_NAMES.get(c, c.upper())}
                      for c in language.supported_codes()],
        # When a handshake secret is set the host site is authoritative and this
        # profile is ignored at session create — surface that so the UI can warn.
        "active": not bool(config.WIDGET_HANDSHAKE_SECRET),
    })


@router.put("/test-profile")
async def put_test_profile(body: TestProfileWrite,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    try:
        validated = settings_mod.validate_test_profile(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_setting("test_profile", validated,
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
    })


# ---------------------------------------------------------------------------
# User management — named admin/manager accounts (owner/admin only)
#
# Minimal by design: an owner/admin creates accounts with an email + password and
# a role (admin = full write, manager = read-only). No email delivery, no reset
# flows, no enumeration — all lifecycle is here. Passwords are stored only as a
# salted PBKDF2 hash (auth.hash_password); the hash never leaves db.py.
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USER_ROLES = ("admin", "manager")


class UserCreate(BaseModel):
    email: str
    password: str
    role: str = "manager"


class UserUpdate(BaseModel):
    password: Optional[str] = None
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


@router.get("/users")
async def list_users(admin=Depends(require_admin_write)) -> JSONResponse:
    return JSONResponse(content={"users": await db.list_admin_users()})


@router.post("/users")
async def create_user(body: UserCreate,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    email = _validate_email(body.email)
    _validate_password(body.password)
    role = _validate_role(body.role)
    if await db.get_admin_user(email):
        raise HTTPException(status_code=409, detail="A user with that email already exists.")
    # PBKDF2 hashing is CPU-bound — keep it off the event loop.
    pw_hash = await asyncio.to_thread(auth.hash_password, body.password)
    user = await db.create_admin_user(email, pw_hash, role)
    await db.log_admin_event(None, "admin_user_created",
                             {"email": email, "role": role, "by": admin.get("email")})
    return JSONResponse(content={"user": user})


@router.put("/users/{email}")
async def update_user(email: str, body: UserUpdate,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    existing = await db.get_admin_user(target)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")
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
    await db.log_admin_event(None, "admin_user_updated",
                             {"email": target, "by": admin.get("email")})
    return JSONResponse(content={"user": user})


@router.delete("/users/{email}")
async def delete_user(email: str,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    target = _validate_email(email)
    if admin.get("email") == target:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    ok = await db.delete_admin_user(target)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    await db.log_admin_event(None, "admin_user_deleted",
                             {"email": target, "by": admin.get("email")})
    return JSONResponse(content={"ok": True})


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


async def _build_effective_preview() -> dict[str, Any]:
    """Assemble the full prompt exactly as chat_service would, using the Test
    sandbox player.

    Returns the system message (Layer 1 SYSTEM_CORE + Layer 2 KB block) and the
    Layer-3 user message, plus a note of which example topic/language were used.
    Resilient by design: if topics/KB can't be loaded the preview still renders
    Layer 1 + the Layer-3 directives, so the page never breaks.
    """
    lang = language.default_code()
    current_topic: Optional[dict[str, Any]] = None
    kb_block: Optional[str] = None
    suggestable: list[dict[str, Any]] = []
    example_topic: Optional[str] = None
    try:
        topics = await db.list_topics(include_hidden=False)
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
async def get_effective_prompt() -> JSONResponse:
    """Read-only: the whole prompt as assembled from prompts.py (the source of truth)."""
    return JSONResponse(content={"effective_preview": await _build_effective_preview()})
