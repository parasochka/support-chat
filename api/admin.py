"""Admin dashboard data + management API (Phase 2). All routes behind require_admin.

Aggregation is done in SQL (db.py); derived rates live in metrics.py. Every
destructive action writes an `admin_events` audit row (invariant §15.5).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import config
import db
import kb_import
import language
import metrics
import openai_client
import prompt_store
import prompts
import settings as settings_mod
from api.admin_auth import require_admin

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
             for c in config.SUPPORTED_LANGUAGES]
    return JSONResponse(content={
        "languages": langs,
        "default_language": config.DEFAULT_LANGUAGE,
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
        w.writerow(["topic", "session_id", "message_count", "first_message",
                    "created_at"])
        for g in groups:
            for s in g["sessions"]:
                w.writerow([g["topic"], s["session_id"], s["message_count"],
                            (s["first_message"] or "").replace("\n", " "),
                            s["created_at"]])
        return PlainTextResponse(content=buf.getvalue(), media_type="text/csv")
    return JSONResponse(content={"groups": groups})


@router.get("/ab/results")
async def ab_results(from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None) -> JSONResponse:
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content={"results": await db.ab_results(dt_from, dt_to)})


# ---------------------------------------------------------------------------
# prompt versioning + A/B
# ---------------------------------------------------------------------------
class PromptCreate(BaseModel):
    name: str
    body: str


class PromptUpdate(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None


class ABWeight(BaseModel):
    id: int
    weight: int


class ABWeights(BaseModel):
    weights: list[ABWeight]


@router.get("/prompts")
async def list_prompts() -> JSONResponse:
    return JSONResponse(content={"versions": await db.list_prompt_versions()})


@router.post("/prompts")
async def create_prompt(body: PromptCreate, admin=Depends(require_admin)) -> JSONResponse:
    vid = await db.create_prompt_version(name=body.name, body=body.body, status="draft")
    await db.log_admin_event(None, "prompt_version_created", {"id": vid})
    return JSONResponse(content=await db.get_prompt_version(vid))


@router.put("/prompts/{version_id}")
async def update_prompt(version_id: int, body: PromptUpdate) -> JSONResponse:
    try:
        row = await db.update_prompt_version(version_id, body.name, body.body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return JSONResponse(content=row)


@router.post("/prompts/{version_id}/publish")
async def publish_prompt(version_id: int) -> JSONResponse:
    row = await db.publish_prompt_version(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    prompt_store.invalidate()  # deliberate, one-time cache reset
    await db.log_admin_event(None, "prompt_version_published", {"id": version_id})
    return JSONResponse(content=row)


@router.post("/prompts/ab")
async def set_ab(body: ABWeights) -> JSONResponse:
    await db.set_ab_weights([{"id": w.id, "weight": w.weight} for w in body.weights])
    await db.log_admin_event(None, "prompt_ab_weights_set",
                             {"weights": [w.model_dump() for w in body.weights]})
    return JSONResponse(content={"versions": await db.list_prompt_versions()})


@router.post("/prompts/{version_id}/archive")
async def archive_prompt(version_id: int) -> JSONResponse:
    try:
        row = await db.archive_prompt_version(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    await db.log_admin_event(None, "prompt_version_archived", {"id": version_id})
    return JSONResponse(content=row)


# ---------------------------------------------------------------------------
# KB management + import
# ---------------------------------------------------------------------------
class TopicUpsert(BaseModel):
    slug: str
    title: dict[str, str]
    order: int = 0
    active: bool = True


class EntryCreate(BaseModel):
    topic_id: int
    lang: str = "ru"
    content: str


class EntryUpdate(BaseModel):
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


@router.get("/kb/entries")
async def kb_entries(topic_id: int, include_inactive: bool = False) -> JSONResponse:
    return JSONResponse(content={
        "entries": await db.list_kb_entries(topic_id, include_inactive=include_inactive)
    })


@router.post("/kb/entries")
async def kb_create_entry(body: EntryCreate) -> JSONResponse:
    eid = await db.create_kb_entry(body.topic_id, body.lang.lower(), body.content)
    await db.log_admin_event(None, "kb_entry_created",
                             {"id": eid, "topic_id": body.topic_id, "lang": body.lang})
    return JSONResponse(content={"id": eid})


@router.put("/kb/entries/{entry_id}")
async def kb_update_entry(entry_id: int, body: EntryUpdate) -> JSONResponse:
    new_id = await db.update_kb_entry(entry_id, body.content)
    if new_id is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    await db.log_admin_event(None, "kb_entry_updated",
                             {"from": entry_id, "to": new_id})
    return JSONResponse(content={"id": new_id})


@router.delete("/kb/entries/{entry_id}")
async def kb_delete_entry(entry_id: int) -> JSONResponse:
    ok = await db.soft_delete_kb_entry(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found or already inactive.")
    await db.log_admin_event(None, "kb_entry_deleted", {"id": entry_id})
    return JSONResponse(content={"ok": True})


@router.post("/kb/import")
async def kb_import_endpoint(file: UploadFile = File(...),
                            format: str = Form(...),
                            lang: str = Form("ru")) -> JSONResponse:
    raw = (await file.read()).decode("utf-8", errors="replace")
    fallback_slug = (file.filename or "").rsplit(".", 1)[0]
    try:
        rows = kb_import.parse(raw, format, fallback_slug=fallback_slug, lang=lang)
    except kb_import.ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    inserted, skipped = 0, []
    for r in rows:
        topic = await db.get_topic_by_slug(r["topic_slug"])
        if topic is None:
            skipped.append(r["topic_slug"])
            continue
        await db.create_kb_entry(topic["id"], r["lang"], r["content"])
        inserted += 1
    await db.log_admin_event(None, "kb_imported",
                             {"inserted": inserted, "skipped": skipped,
                              "format": format})
    return JSONResponse(content={"inserted": inserted, "skipped_unknown_topics": skipped})


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
                      for c in config.SUPPORTED_LANGUAGES],
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
# system prompt (Layer-1 core) — structured, edit-and-apply-live
#
# The core is broken into named sections (tone of voice + each rule block) so the
# owner can retune the prompt from Settings. Saving composes the sections into
# the core and publishes it live as the new default prompt version — reusing the
# version machinery keeps attribution, the audit trail, and the byte-stable cache
# boundary intact (a publish is one deliberate, warned-about cache reset).
# ---------------------------------------------------------------------------
class SystemPromptWrite(BaseModel):
    sections: dict[str, str]


@router.get("/system-prompt")
async def get_system_prompt() -> JSONResponse:
    sections = settings_mod.system_prompt()
    live = await db.get_default_prompt_version()
    return JSONResponse(content={
        "sections": sections,
        "meta": prompts.section_meta(),
        "composed": prompts.compose_core(sections),
        "live_version": ({"id": live["id"], "name": live["name"]}
                         if live else None),
    })


@router.put("/system-prompt")
async def put_system_prompt(body: SystemPromptWrite,
                            admin=Depends(require_admin)) -> JSONResponse:
    keys = set(prompts.SECTION_KEYS)
    cleaned: dict[str, str] = {}
    for key, val in (body.sections or {}).items():
        if key not in keys:
            raise HTTPException(status_code=400, detail=f"unknown section: {key!r}")
        if not isinstance(val, str) or not val.strip():
            raise HTTPException(status_code=400,
                                detail=f"section {key!r} must be a non-empty string")
        cleaned[key] = val.strip()

    # Persist the structured sections (the editor's source of truth)…
    await db.set_setting("system_prompt", {"sections": cleaned},
                         updated_by=admin.get("role"))
    await settings_mod.reload()
    # …then publish the composed core live as the new default version.
    core = prompts.compose_core(settings_mod.system_prompt())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    vid = await db.create_prompt_version(name=f"settings-{stamp}", body=core,
                                         status="draft")
    await db.publish_prompt_version(vid)
    prompt_store.invalidate()  # deliberate, one-time cache reset
    await db.log_admin_event(None, "system_prompt_updated",
                             {"version_id": vid, "sections": list(cleaned)})
    return JSONResponse(content={"ok": True, "version_id": vid,
                                 "sections": settings_mod.system_prompt(),
                                 "composed": core})
