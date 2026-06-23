"""Admin dashboard data + management API (Phase 2). All routes behind require_admin.

Aggregation is done in SQL (db.py); derived rates live in metrics.py. Every
destructive action writes an `admin_events` audit row (invariant §15.5).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import config
import db
import kb
import language
import metrics
import openai_client
import prompts
import settings as settings_mod
from api.admin_auth import require_admin

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
    langs = [{"code": c, "name": language.LANG_NAMES.get(c, c.upper())}
             for c in language.supported_codes()]
    return JSONResponse(content={
        "languages": langs,
        "default_language": language.default_code(),
    })


# ---------------------------------------------------------------------------
# date-range helper
# ---------------------------------------------------------------------------
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
                   page: int = 1) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    res = await db.list_sessions(dt_from, dt_to, topic=topic, lang=lang,
                                 status=status, escalated=escalated, q=q, page=page)
    return JSONResponse(content=res)


@router.get("/session/{session_id}")
async def session(session_id: str) -> JSONResponse:
    detail = await db.session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return JSONResponse(content=detail)


@router.get("/unresolved")
async def unresolved(from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None, format: str = "json") -> Any:
    dt_from, dt_to = _range(from_, to)
    groups = await db.unresolved_by_topic(dt_from, dt_to)
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["topic", "session_id", "status", "escalated",
                    "message_count", "first_message", "created_at"])
        for g in groups:
            for s in g["sessions"]:
                w.writerow([g["topic"], s["session_id"], s.get("status"),
                            s.get("escalated"), s["message_count"],
                            (s["first_message"] or "").replace("\n", " "),
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
async def kb_upsert_topic(body: TopicUpsert) -> JSONResponse:
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
async def kb_set_content(body: KBContentWrite) -> JSONResponse:
    eid = await db.set_kb_content(body.topic_id, body.content)
    await db.log_admin_event(None, "kb_content_updated", {"topic_id": body.topic_id})
    return JSONResponse(content={"id": eid})


@router.delete("/kb/content")
async def kb_clear_content(topic_id: int) -> JSONResponse:
    ok = await db.clear_kb_content(topic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Topic has no KB to clear.")
    await db.log_admin_event(None, "kb_content_cleared", {"topic_id": topic_id})
    return JSONResponse(content={"ok": True})


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
async def put_setting(key: str, body: SettingWrite, admin=Depends(require_admin)
                      ) -> JSONResponse:
    try:
        validated = settings_mod.validate_setting(key, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_setting(key, validated, updated_by=admin.get("role"))
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
                           admin=Depends(require_admin)) -> JSONResponse:
    try:
        validated = settings_mod.validate_test_profile(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.set_setting("test_profile", validated, updated_by=admin.get("role"))
    await settings_mod.reload()  # hot: applies to the next session created
    await db.log_admin_event(None, "test_profile_updated", {})
    return JSONResponse(content={"profile": settings_mod.test_profile()})


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
# exact messages chat_service would send for a sample player + sample topic.
# Nothing here is sent to the model — it's a faithful rendering of the live
# assembly so "how is the prompt formed?" has one answer in one place.
# ---------------------------------------------------------------------------
_PREVIEW_CONTEXT = {
    "id": "10042",
    "full_name": "John Smith",
    "email": "john@example.com",
    "activation_status": "active",
    "country": "KZ",
    "balance": "1500",
    "vip_level": "Silver",
    "registration_date": "2024-01-15",
}
_PREVIEW_USER_TEXT = "«…the player's current message will appear here…»"


async def _build_effective_preview() -> dict[str, Any]:
    """Assemble the full prompt exactly as chat_service would, with sample data.

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
        session={"user_context": _PREVIEW_CONTEXT},
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
