"""Retention bot HTTP surface: Telegram webhook, deeplink exchange, partner
profile push, and the admin CRUD for the retention KB / media / managers / config.

Public routes (no admin auth):
  POST /telegram/webhook/{secret}         Telegram updates (secret-token verified)
  POST /api/retention/deeplink            site -> {nonce, deep_link}
  POST /partner/{product_id}/player-update CRM profile push (partner-secret auth)

Admin routes (behind require_admin, prefix /admin/retention): retention KB,
media library (+ upload), managers, per-product Telegram config, users analytics,
webhook registration. The `retention` settings GROUP is edited through the generic
/admin/settings/retention endpoint (it is in settings.SETTING_KEYS).
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets as _secrets
import uuid
from typing import Any, Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Query, Request, UploadFile)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import antispam
import auth
import config
import db
import kb
import language
import prompts
import retention as retention_mod
import settings as settings_mod
import tenancy
import telegram_transport
from api import admin_auth
from api.admin_auth import require_admin, require_admin_write
from api.client_ip import client_ip

log = logging.getLogger(__name__)

# Public router (Telegram + site + CRM). No admin auth; each route authenticates
# in its own way (webhook secret token / handshake / partner secret).
public_router = APIRouter(tags=["retention"])
# Admin router (management UI), guarded like the rest of /admin/*.
admin_router = APIRouter(prefix="/admin/retention", tags=["retention-admin"],
                         dependencies=[Depends(require_admin)])


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


# ===========================================================================
# Telegram webhook
# ===========================================================================
@public_router.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request,
                           background: BackgroundTasks,
                           x_telegram_bot_api_secret_token: Optional[str] =
                           Header(default=None)) -> JSONResponse:
    """Receive a Telegram update, resolve the product, process in the background.

    Auth is two-layer: the path `{secret}` is the product's non-secret webhook
    routing token (maps update -> product), and the X-Telegram-Bot-Api-Secret-Token
    header must equal the deploy-wide TELEGRAM_WEBHOOK_SECRET we set when
    registering (NOT in the URL), so a caller who learns the URL still cannot
    forge updates. We always return 200 fast (Telegram retries on non-200); the
    AI turn runs after the response.
    """
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "",
                               config.TELEGRAM_WEBHOOK_SECRET):
        return _err(403, "bad_secret_token", "Invalid webhook secret token.")
    product = await db.get_product_by_telegram_webhook_secret(secret)
    if product is None or not product.get("active") or not product.get("retention_enabled"):
        # Unknown token or retention disabled — acknowledge so Telegram stops
        # retrying, but do nothing.
        return JSONResponse(content={"ok": True})
    try:
        update = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(content={"ok": True})
    background.add_task(retention_mod.handle_update, product, update)
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Deeplink exchange (site -> nonce + deep_link)
# ===========================================================================
class DeeplinkReq(BaseModel):
    widget_key: Optional[str] = None
    signed_context: Optional[str] = None
    user_context: dict[str, Any] = Field(default_factory=dict)
    escalation: bool = False
    # The language the player's conversation/UI was already running in (a code
    # or a locale like "ru-RU"). Rides in the nonce so the bot opens in it.
    lang: Optional[str] = None


async def _resolve_product(widget_key: Optional[str]) -> Optional[dict[str, Any]]:
    key = (widget_key or "").strip()
    if key:
        return await db.get_product_by_widget_key(key)
    return await db.get_default_product()


@public_router.post("/api/retention/deeplink")
async def create_deeplink(req: Request, body: DeeplinkReq) -> JSONResponse:
    """Site/widget hands us the player handshake; we return a one-time deep link.

    Trust of the profile mirrors the chat handshake: a signed blob is verified
    against the product's handshake secret; without a secret configured (dev) the
    admin test profile stands in; the injection sanitizer runs regardless when the
    context reaches the model.
    """
    # IP rate-limit (dedicated budget) so the nonce table can't be flooded.
    ip = client_ip(req)
    try:
        antispam.check_rate_limit(f"deeplink:{ip}")
    except antispam.AntiSpamError as exc:
        return _err(exc.status, exc.code, exc.detail)
    product = await _resolve_product(body.widget_key)
    if product is None or not product.get("active"):
        return _err(403, "bad_widget_key", "Unknown or inactive widget key.")
    if not product.get("retention_enabled"):
        return _err(403, "retention_disabled",
                    "Retention is not enabled for this product.")
    if not product.get("telegram_bot_username"):
        return _err(409, "no_bot", "This product has no Telegram bot configured.")
    tenancy.set_current_product(product["id"])

    product_handshake = await db.get_product_handshake_secret(product["id"])
    context: dict[str, Any] = {}
    if body.signed_context:
        try:
            payload = auth.verify_handshake(body.signed_context,
                                            secret=product_handshake)
        except auth.TokenError as exc:
            return _err(401, "bad_handshake", str(exc))
        context = {k: v for k, v in payload.items() if k not in ("iat", "exp")}
    elif product_handshake or config.WIDGET_HANDSHAKE_SECRET:
        # Production: never trust unsigned browser context.
        context = {}
    else:
        tp = settings_mod.test_profile()
        if tp.get("enabled"):
            context = {k: tp.get(k) for k in (
                "id", "full_name", "email", "activation_status",
                "country", "balance", "vip_level", "registration_date")
                if tp.get(k)}
        else:
            context = body.user_context or {}

    link_lang = language.locale_to_lang(body.lang) if body.lang else None
    link = await retention_mod.create_deeplink(product, context, body.escalation,
                                               lang=link_lang)
    if not link["deep_link"]:
        return _err(409, "no_bot", "This product has no Telegram bot username set.")
    return JSONResponse(content=link)


# ===========================================================================
# Partner profile push (CRM -> us)
# ===========================================================================
class PlayerUpdateReq(BaseModel):
    player_id: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    activation_status: Optional[str] = None
    country: Optional[str] = None
    balance: Optional[str] = None
    vip_level: Optional[str] = None
    registration_date: Optional[str] = None
    # Casino-side activity signals (ISO-8601 timestamps) — the ping matrix keys
    # on them ("hasn't logged in for a week", "no deposit in 30 days", ...).
    last_login_at: Optional[str] = None
    last_played_at: Optional[str] = None
    last_deposit_at: Optional[str] = None


@public_router.post("/partner/{product_id}/player-update")
async def player_update(product_id: int, body: PlayerUpdateReq,
                        authorization: Optional[str] = Header(default=None)
                        ) -> JSONResponse:
    """CRM pushes a (partial) profile change. Authorized with the product's
    handshake secret as a shared partner secret (Bearer). Fields left null are
    not touched (partial update)."""
    product = await db.get_product(product_id)
    if product is None:
        return _err(404, "not_found", "Product not found.")
    secret = await db.get_product_handshake_secret(product_id)
    if not secret:
        return _err(403, "no_partner_secret",
                    "No partner secret configured for this product.")
    try:
        token = auth.extract_bearer(authorization)
    except auth.TokenError as exc:
        return _err(401, "unauthorized", str(exc))
    if not hmac.compare_digest(token, secret):
        return _err(401, "unauthorized", "Bad partner secret.")
    profile = {k: v for k, v in body.model_dump().items()
               if k != "player_id" and v is not None}
    updated = await db.update_retention_profile(product_id, body.player_id,
                                                profile, profile_source="push")
    return JSONResponse(content={"ok": True, "updated": updated})


# ===========================================================================
# Admin: per-product Telegram config + secrets + webhook registration
# ===========================================================================
class TelegramConfigWrite(BaseModel):
    telegram_bot_username: Optional[str] = None
    telegram_channel_id: Optional[str] = None
    telegram_channel_url: Optional[str] = None
    player_api_url: Optional[str] = None
    retention_enabled: Optional[bool] = None


@admin_router.get("/telegram/{product_id}")
async def get_telegram_config(product_id: int,
                              admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    webhook_url = None
    if product.get("telegram_webhook_secret") and config.PUBLIC_BASE_URL:
        webhook_url = (config.PUBLIC_BASE_URL.rstrip("/")
                       + f"/telegram/webhook/{product['telegram_webhook_secret']}")
    return JSONResponse(content={"product": product, "webhook_url": webhook_url})


@admin_router.put("/telegram/{product_id}")
async def put_telegram_config(product_id: int, body: TelegramConfigWrite,
                              admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    fields: dict[str, Any] = {}
    for f in ("telegram_bot_username", "telegram_channel_id",
              "telegram_channel_url", "player_api_url", "retention_enabled"):
        v = getattr(body, f)
        if v is not None:
            fields[f] = v
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    product = await db.update_product_telegram_config(product_id, **fields)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    await db.log_admin_event(None, "retention_telegram_config_updated",
                             {"id": product_id, "fields": sorted(fields),
                              "by": admin.get("email")}, product_id=product_id)
    return JSONResponse(content={"product": product})


@admin_router.post("/webhook/{product_id}")
async def register_webhook(product_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    """Register (or refresh) the bot's Telegram webhook to point at this service."""
    await admin_auth.require_product_write(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not config.PUBLIC_BASE_URL:
        raise HTTPException(status_code=409,
                            detail="PUBLIC_BASE_URL env is not set; cannot build "
                                   "the webhook URL.")
    token = await db.get_product_telegram_token(product_id)
    if not token:
        raise HTTPException(status_code=409,
                            detail="Set the Telegram bot token first.")
    secret = product.get("telegram_webhook_secret")
    if not secret:
        raise HTTPException(status_code=409,
                            detail="No webhook routing token; re-save the bot token.")
    url = config.PUBLIC_BASE_URL.rstrip("/") + f"/telegram/webhook/{secret}"
    client = telegram_transport.TelegramClient(token)
    result = await client.set_webhook(url, config.TELEGRAM_WEBHOOK_SECRET)
    me = await client.get_me()
    if me and me.get("username") and me.get("username") != product.get("telegram_bot_username"):
        # Keep the stored username in sync so deeplinks are correct.
        await db.update_product_telegram_config(
            product_id, telegram_bot_username=me["username"])
    ok = result is not None
    await db.log_admin_event(None, "retention_webhook_registered",
                             {"id": product_id, "ok": ok, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": ok, "webhook_url": url,
                                 "bot": me.get("username") if me else None})


# ===========================================================================
# Admin: retention effective prompt — READ-ONLY preview (mirrors the support
# GET /admin/effective-prompt). The retention prompt wording lives in
# prompts.py (single source of truth); this endpoint shows the whole assembled
# retention prompt for the product — Layer 1 (byte-stable retention core) +
# Layer 2 (the retention-KB document) in the system message, and the Layer-3
# user message (profile, personalization, language, photo candidates,
# guardrails) — plus the prompt variables the RETENTION templates actually use
# (their one editor stays the support Prompt → Prompt variables sub-tab).
# ===========================================================================
_RETENTION_PREVIEW_USER_TEXT = (
    "\"...the player's current message will appear here...\"")

# Illustrative photo candidate so the operator sees the block's shape; the real
# list is selected per turn from the Media tab by retention.select_photo_candidates.
_RETENTION_PREVIEW_CANDIDATES = [{
    "id": 1, "stage": 1,
    "description": "(example) a photo from the Media tab - the real candidate "
                   "list is selected per turn",
    "tags": ["example"],
}]


@admin_router.get("/effective-prompt")
async def retention_effective_prompt(product_id: int,
                                     admin=Depends(require_admin)) -> JSONResponse:
    """Read-only: the whole retention prompt as assembled from prompts.py."""
    from api.admin import _preview_context  # reuse the Test-sandbox preview player
    await admin_auth.require_product_read(admin, product_id)
    # Bind the tenancy scope so prompt variables / KB variables / language
    # resolve for THIS product — each casino previews ITS retention prompt.
    tenancy.set_current_product(product_id)
    kb_block: Optional[str] = None
    try:
        kb_text = await db.retention_kb_block(product_id)
        if kb_text:
            kb_block = await kb.render_variables(kb_text, product_id=product_id)
    except Exception:  # pragma: no cover - preview must never break the page
        kb_block = None
    lang = language.default_code()
    messages = prompts.build_retention_messages(
        session={"user_context": _preview_context()},
        kb_block=kb_block,
        history=[],
        user_text=_RETENTION_PREVIEW_USER_TEXT,
        resolved_lang=lang,
        photo_candidates=_RETENTION_PREVIEW_CANDIDATES,
    )
    return JSONResponse(content={
        "effective_preview": {
            "system": messages[0]["content"],
            "user": messages[-1]["content"],
            "example": {"lang": lang,
                        "user_text": _RETENTION_PREVIEW_USER_TEXT},
        },
        "variables": _retention_variables_payload(),
    })


# ===========================================================================
# Admin: retention prompt variables — the Telegram-persona uniquification
# values (retention_persona_name / _role / _brand / _products / tone). Stored
# under their own product-settings key; every non-tone key INHERITS the
# resolved support prompt variable when left empty (the Telegram persona
# mirrors the support chat by default and is overridden only where it should
# differ). The one editor is the Retention → Prompt variables tab.
# ===========================================================================
class RetentionPromptVariablesWrite(BaseModel):
    value: Any = Field(...)


def _retention_variables_payload() -> list[dict[str, Any]]:
    """The variables list for the admin editor (scope already bound).

    `value` is the raw stored override ('' = inherited), `inherited` the value
    that applies when the override is empty (the support counterpart or the
    registry default), `resolved` what the prompt actually renders with.
    """
    overrides = settings_mod.retention_prompt_variable_overrides()
    resolved = settings_mod.retention_prompt_variables()
    support = settings_mod.prompt_variables()
    out = []
    for key, desc, default, inherits in prompts.RETENTION_PROMPT_VARIABLES:
        inherited = default if default is not None else support.get(inherits or "", "")
        out.append({
            "key": key, "description": desc,
            "inherits_from": inherits,
            "inherited": inherited,
            "value": overrides.get(key, ""),
            "resolved": resolved.get(key, inherited),
        })
    return out


@admin_router.get("/prompt-variables")
async def get_retention_prompt_variables(product_id: int,
                                         admin=Depends(require_admin)
                                         ) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    tenancy.set_current_product(product_id)
    return JSONResponse(content={"variables": _retention_variables_payload()})


@admin_router.put("/prompt-variables")
async def put_retention_prompt_variables(product_id: int,
                                         body: RetentionPromptVariablesWrite,
                                         admin=Depends(require_admin_write)
                                         ) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    tenancy.set_current_product(product_id)
    try:
        validated = settings_mod.validate_retention_prompt_variables(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Product store only (like the support prompt variables): the Telegram
    # persona is a per-casino uniquification seam, never a global one.
    await db.set_product_setting(product_id, "retention_prompt_variables",
                                 validated,
                                 updated_by=admin.get("email") or admin.get("role"))
    await settings_mod.reload()  # hot: the next retention prompt renders new values
    await db.log_admin_event(None, "retention_prompt_variables_updated",
                             {"keys": sorted(validated),
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"variables": _retention_variables_payload()})


# ===========================================================================
# Admin: retention KB — ONE free-text document per product (edited like a
# support topic's KB text). The legacy per-entry CRUD below stays for API
# consumers; the admin SPA uses only these two document endpoints.
# ===========================================================================
class RetentionKBTextWrite(BaseModel):
    text: str = ""


@admin_router.get("/kb/text")
async def get_kb_text(product_id: int, admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={"text": await db.get_retention_kb_text(product_id)})


@admin_router.put("/kb/text")
async def put_kb_text(product_id: int, body: RetentionKBTextWrite,
                      admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    await db.set_retention_kb_text(product_id, body.text,
                                   updated_by=admin.get("email"))
    await db.log_admin_event(None, "retention_kb_updated",
                             {"chars": len(body.text.strip()),
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"text": await db.get_retention_kb_text(product_id)})


class RetentionKBWrite(BaseModel):
    title: str
    trigger_when: Optional[str] = None
    body: str
    links: list[str] = Field(default_factory=list)
    sort_order: int = 0
    active: bool = True


@admin_router.get("/kb")
async def list_kb(product_id: int, admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={"items": await db.list_retention_kb(product_id)})


@admin_router.post("/kb")
async def create_kb(product_id: int, body: RetentionKBWrite,
                    admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if not body.title.strip() or not body.body.strip():
        raise HTTPException(status_code=400, detail="Title and body are required.")
    entry = await db.create_retention_kb(
        product_id, title=body.title.strip(),
        trigger_when=(body.trigger_when or "").strip() or None,
        body=body.body.strip(), links=[l.strip() for l in body.links if l.strip()],
        sort_order=body.sort_order, updated_by=admin.get("email"))
    await db.log_admin_event(None, "retention_kb_created", {"id": entry["id"]},
                             product_id=product_id)
    return JSONResponse(content={"entry": entry})


@admin_router.put("/kb/{entry_id}")
async def update_kb(entry_id: int, body: RetentionKBWrite,
                    admin=Depends(require_admin_write)) -> JSONResponse:
    entry = await db.get_retention_kb_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    await admin_auth.require_product_write(admin, entry["product_id"])
    updated = await db.update_retention_kb(
        entry_id, title=body.title.strip(),
        trigger_when=(body.trigger_when or "").strip() or None,
        body=body.body.strip(), links=[l.strip() for l in body.links if l.strip()],
        sort_order=body.sort_order, active=body.active,
        updated_by=admin.get("email"))
    return JSONResponse(content={"entry": updated})


@admin_router.delete("/kb/{entry_id}")
async def delete_kb(entry_id: int, admin=Depends(require_admin_write)) -> JSONResponse:
    entry = await db.get_retention_kb_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found.")
    await admin_auth.require_product_write(admin, entry["product_id"])
    await db.delete_retention_kb(entry_id)
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Admin: media library
# ===========================================================================
class PhotoWrite(BaseModel):
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    level_min: Optional[int] = None
    stage: Optional[int] = None
    category: Optional[str] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


@admin_router.get("/photos")
async def list_photos(product_id: int, admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={"items": await db.list_retention_photos(product_id)})


_PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


@admin_router.post("/photos")
async def create_photo(product_id: int = Form(...),
                       description: str = Form(""),
                       tags: str = Form(""),
                       level_min: int = Form(0),
                       stage: int = Form(1),
                       category: str = Form(""),
                       sort_order: int = Form(0),
                       file: Optional[UploadFile] = File(default=None),
                       files: Optional[list[UploadFile]] = File(default=None),
                       admin=Depends(require_admin_write)) -> JSONResponse:
    """Upload photo binaries to the media Volume + create their catalogue rows.

    Bulk-friendly: `files` takes any number of images in one request (the shared
    form fields apply to every one — typically left blank and filled by the AI
    metadata generation afterwards). The single `file` field stays accepted for
    older API consumers. Validation runs BEFORE anything is written, so one bad
    file rejects the batch instead of half-uploading it.
    """
    await admin_auth.require_product_write(admin, product_id)
    uploads = [u for u in ([file] if file else []) + list(files or []) if u]
    if not uploads:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    exts = []
    for up in uploads:
        ext = os.path.splitext(up.filename or "")[1].lower() or ".jpg"
        if ext not in _PHOTO_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image type: {up.filename or ext}")
        exts.append(ext)
    os.makedirs(config.RETENTION_MEDIA_DIR, exist_ok=True)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    photos = []
    for up, ext in zip(uploads, exts):
        storage_ref = f"{product_id}_{uuid.uuid4().hex}{ext}"
        content = await up.read()
        with open(os.path.join(config.RETENTION_MEDIA_DIR, storage_ref), "wb") as fh:
            fh.write(content)
        photo = await db.create_retention_photo(
            product_id, storage_ref=storage_ref, description=description,
            tags=tag_list, level_min=level_min, stage=stage,
            category=category.strip() or None, sort_order=sort_order,
            created_by=admin.get("email"))
        photos.append(photo)
    await db.log_admin_event(None, "retention_photo_created",
                             {"ids": [p["id"] for p in photos],
                              "count": len(photos), "by": admin.get("email")},
                             product_id=product_id)
    # `photo` (the first row) is kept for pre-bulk API consumers.
    return JSONResponse(content={"photos": photos, "photo": photos[0]})


# ===========================================================================
# Admin: AI metadata generation for media photos
#
# Cataloguing hundreds of photos by hand (description + tags + explicitness
# stage + VIP tier) is the slowest part of setting a product up, so the admin
# can select photos and have the product's OWN model fill the metadata: one
# vision call per photo (the product's encrypted OpenAI keys via
# client_for_product, the product-resolved `model` settings group — the same
# stack every chat turn uses), returning strict JSON that is validated/clamped
# against the product's actual tier list and stage ladder before it lands on
# the row. Every call is logged to ai_interaction_logs (invariant §4).
# ===========================================================================
_PHOTO_META_BATCH_CAP = 20  # per request; the SPA chunks larger selections
_PHOTO_META_CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}


class GeneratePhotoMetaReq(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=_PHOTO_META_BATCH_CAP)


def _parse_photo_meta(text: str, *, vip_tiers: list[str],
                      max_stage: int) -> dict[str, Any]:
    """Parse + clamp the model's JSON metadata. Raises ValueError on garbage.

    Tolerates a fenced/prefixed reply (extracts the outermost {...} block);
    clamps stage/level into the product's real ranges so a hallucinated number
    can never unlock a photo beyond what the delivery gate allows.
    """
    import json as _json
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the model reply")
    data = _json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    description = str(data.get("description") or "").strip()
    if not description:
        raise ValueError("empty description")
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")
    tags = [str(t).strip().lower() for t in tags if str(t).strip()][:10]
    try:
        stage = int(data.get("stage"))
        level_min = int(data.get("level_min"))
    except (TypeError, ValueError):
        raise ValueError("stage/level_min must be integers")
    stage = min(max(stage, 1), max(int(max_stage), 1))
    level_min = min(max(level_min, 0), max(len(vip_tiers or ["none"]) - 1, 0))
    return {"description": description[:1000], "tags": tags,
            "stage": stage, "level_min": level_min}


async def _generate_photo_meta(client: Any, photo: dict[str, Any],
                               product_id: int, vip_tiers: list[str],
                               max_stage: int) -> dict[str, Any]:
    """One photo: read the binary, one vision call, validate, update the row."""
    import base64
    ref = photo.get("storage_ref")
    path = os.path.join(config.RETENTION_MEDIA_DIR, os.path.basename(ref or ""))
    if not ref or not os.path.exists(path):
        return {"id": photo["id"], "ok": False, "error": "file missing on disk"}
    ext = os.path.splitext(path)[1].lower()
    mime = _PHOTO_META_CONTENT_TYPES.get(ext, "image/jpeg")
    with open(path, "rb") as fh:
        data_url = (f"data:{mime};base64,"
                    + base64.b64encode(fh.read()).decode("ascii"))
    messages = prompts.build_photo_meta_messages(data_url, vip_tiers, max_stage)
    try:
        result = await client.complete(messages)
    except Exception as exc:  # noqa: BLE001 - one bad photo must not kill the batch
        await db.log_ai_interaction(None, None, None, None, None, None, None,
                                    None, ok=False,
                                    error=f"photo_meta: {exc.__class__.__name__}",
                                    product_id=product_id)
        return {"id": photo["id"], "ok": False, "error": "model call failed"}
    import openai_client as oc
    cost = oc.compute_cost(result.model, result.tokens_in, result.tokens_out,
                           result.cached_in)
    await db.log_ai_interaction(None, result.model, result.key_used,
                                result.tokens_in, result.tokens_out,
                                result.cached_in, cost, result.latency_ms,
                                ok=True, error=None, product_id=product_id)
    try:
        meta = _parse_photo_meta(result.text, vip_tiers=vip_tiers,
                                 max_stage=max_stage)
    except Exception as exc:  # noqa: BLE001 - malformed JSON must not kill the batch
        log.warning("photo_meta_parse_failed photo_id=%s error=%s",
                    photo["id"], exc)
        return {"id": photo["id"], "ok": False,
                "error": "could not parse the model reply"}
    updated = await db.update_retention_photo(photo["id"], **meta)
    return {"id": photo["id"], "ok": True, "photo": updated}


@admin_router.post("/photos/generate-metadata")
async def generate_photo_metadata(product_id: int, body: GeneratePhotoMetaReq,
                                  admin=Depends(require_admin_write)
                                  ) -> JSONResponse:
    """Fill description/tags/stage/level_min for the selected photos with AI."""
    import asyncio

    import openai_client
    await admin_auth.require_product_write(admin, product_id)
    # Bind the scope so the `model` and `retention` groups resolve per product.
    tenancy.set_current_product(product_id)
    ids = list(dict.fromkeys(body.ids))  # dedupe, keep order
    photos = []
    for pid_ in ids:
        photo = await db.get_retention_photo(pid_)
        if photo is None or photo["product_id"] != product_id:
            raise HTTPException(status_code=404,
                                detail=f"Photo {pid_} not found in this product.")
        photos.append(photo)
    rt = settings_mod.retention()
    vip_tiers = [str(t) for t in rt.get("vip_tiers") or ["none"]]
    max_stage = int(rt.get("max_stage") or 1)
    client = await openai_client.client_for_product(product_id)
    results = await asyncio.gather(*[
        _generate_photo_meta(client, p, product_id, vip_tiers, max_stage)
        for p in photos
    ])
    ok = sum(1 for r in results if r["ok"])
    await db.log_admin_event(None, "retention_photo_meta_generated",
                             {"ids": ids, "ok": ok,
                              "failed": len(results) - ok,
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"results": list(results)})


@admin_router.put("/photos/{photo_id}")
async def update_photo(photo_id: int, body: PhotoWrite,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    photo = await db.get_retention_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    await admin_auth.require_product_write(admin, photo["product_id"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await db.update_retention_photo(photo_id, **fields)
    return JSONResponse(content={"photo": updated})


@admin_router.delete("/photos/{photo_id}")
async def delete_photo(photo_id: int,
                       admin=Depends(require_admin_write)) -> JSONResponse:
    photo = await db.get_retention_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    await admin_auth.require_product_write(admin, photo["product_id"])
    await db.delete_retention_photo(photo_id)
    return JSONResponse(content={"ok": True})


@admin_router.get("/photos/{photo_id}/file")
async def get_photo_file(photo_id: int, admin=Depends(require_admin)) -> Any:
    """Serve a photo binary for the admin preview (guarded)."""
    photo = await db.get_retention_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    await admin_auth.require_product_read(admin, photo["product_id"])
    ref = photo.get("storage_ref")
    if not ref:
        raise HTTPException(status_code=404, detail="No stored file.")
    path = os.path.join(config.RETENTION_MEDIA_DIR, os.path.basename(ref))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing on disk.")
    return FileResponse(path)


# ===========================================================================
# Admin: managers pool
# ===========================================================================
class ManagerWrite(BaseModel):
    display_name: Optional[str] = None
    username: Optional[str] = None
    active: Optional[bool] = None


@admin_router.get("/managers")
async def list_managers(product_id: int, admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={"items": await db.list_retention_managers(product_id)})


@admin_router.post("/managers")
async def create_manager(product_id: int, body: ManagerWrite,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    if not (body.display_name or "").strip() or not (body.username or "").strip():
        raise HTTPException(status_code=400,
                            detail="Display name and username are required.")
    mgr = await db.create_retention_manager(
        product_id, display_name=body.display_name.strip(),
        username=body.username.strip())
    return JSONResponse(content={"manager": mgr})


@admin_router.put("/managers/{manager_id}")
async def update_manager(manager_id: int, body: ManagerWrite,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    mgr = await db.get_retention_manager(manager_id)
    if mgr is None:
        raise HTTPException(status_code=404, detail="Manager not found.")
    await admin_auth.require_product_write(admin, mgr["product_id"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await db.update_retention_manager(manager_id, **fields)
    return JSONResponse(content={"manager": updated})


@admin_router.delete("/managers/{manager_id}")
async def delete_manager(manager_id: int,
                         admin=Depends(require_admin_write)) -> JSONResponse:
    mgr = await db.get_retention_manager(manager_id)
    if mgr is None:
        raise HTTPException(status_code=404, detail="Manager not found.")
    await admin_auth.require_product_write(admin, mgr["product_id"])
    await db.delete_retention_manager(manager_id)
    return JSONResponse(content={"ok": True})


# ===========================================================================
# Admin: ping matrix (proactive re-engagement rules + ledger + run-now)
# ===========================================================================
_RULE_TRIGGERS = ("bot_inactivity", "casino_inactivity", "no_deposit")
_RULE_ACTIONS = ("message", "photo")


class RuleWrite(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    trigger_kind: Optional[str] = None
    inactivity_days: Optional[int] = Field(default=None, ge=1, le=365)
    action: Optional[str] = None
    intent: Optional[str] = None
    vip_tiers: Optional[list[str]] = None
    cooldown_days: Optional[int] = Field(default=None, ge=0, le=365)
    priority: Optional[int] = Field(default=None, ge=-1000, le=1000)

    def clean_fields(self) -> dict[str, Any]:
        fields = {k: v for k, v in self.model_dump().items() if v is not None}
        if "trigger_kind" in fields and fields["trigger_kind"] not in _RULE_TRIGGERS:
            raise HTTPException(status_code=400,
                                detail=f"trigger_kind must be one of "
                                       f"{', '.join(_RULE_TRIGGERS)}.")
        if "action" in fields and fields["action"] not in _RULE_ACTIONS:
            raise HTTPException(status_code=400,
                                detail=f"action must be one of "
                                       f"{', '.join(_RULE_ACTIONS)}.")
        if "vip_tiers" in fields:
            fields["vip_tiers"] = [str(t).strip().lower()
                                   for t in fields["vip_tiers"] if str(t).strip()]
        if "intent" in fields:
            fields["intent"] = fields["intent"].strip()
        return fields


@admin_router.get("/pings/rules")
async def list_ping_rules(product_id: int,
                          admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_retention_rules(product_id)})


@admin_router.post("/pings/rules")
async def create_ping_rule(product_id: int, body: RuleWrite,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    fields = body.clean_fields()
    if not (fields.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Rule name is required.")
    fields["name"] = fields["name"].strip()
    rule = await db.create_retention_rule(product_id, fields,
                                          updated_by=admin.get("email"))
    await db.log_admin_event(None, "retention_rule_created",
                             {"id": rule["id"], "name": rule["name"],
                              "by": admin.get("email")}, product_id=product_id)
    return JSONResponse(content={"rule": rule})


@admin_router.put("/pings/rules/{rule_id}")
async def update_ping_rule(rule_id: int, product_id: int, body: RuleWrite,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    fields = body.clean_fields()
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update.")
    rule = await db.update_retention_rule(rule_id, product_id, fields,
                                          updated_by=admin.get("email"))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return JSONResponse(content={"rule": rule})


@admin_router.delete("/pings/rules/{rule_id}")
async def delete_ping_rule(rule_id: int, product_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    ok = await db.delete_retention_rule(rule_id, product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return JSONResponse(content={"ok": True})


@admin_router.get("/pings")
async def list_pings(product_id: int, page: int = 1, page_size: int = 50,
                     admin=Depends(require_admin)) -> JSONResponse:
    """The ping ledger: what was sent to whom, by which rule, at what cost."""
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_pings(
        product_id, page=page, page_size=min(page_size, 200)))


@admin_router.post("/pings/run")
async def run_pings_now(product_id: int,
                        admin=Depends(require_admin_write)) -> JSONResponse:
    """Run one bounded sweep for this product immediately (test/QA button).

    Quiet hours are ignored (the operator is explicitly asking); every other
    guard — per-player caps, gaps, rule cooldowns, opt-outs — still applies.
    """
    import retention_pings
    await admin_auth.require_product_write(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not product.get("retention_enabled"):
        raise HTTPException(status_code=409,
                            detail="Retention is not enabled for this product.")
    stats = await retention_pings.run_product_pings(
        product, ignore_quiet_hours=True)
    await db.log_admin_event(None, "retention_ping_run_manual",
                             {"stats": stats, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"stats": stats})


# ===========================================================================
# Admin: analytics
# ===========================================================================
async def _analytics_scope(admin: dict, product_id: Optional[int],
                           partner_id: Optional[int]) -> Optional[list[int]]:
    """Product scope for a retention analytics read — the support dashboard's
    resolve_scope_filter convention verbatim (explicit product read-checked;
    nothing selected = the caller's whole accessible scope; None = all,
    [] = none). One authorization choke point, no forked copy."""
    return await admin_auth.resolve_scope_filter(admin, product_id, partner_id)


@admin_router.get("/overview")
async def overview(product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None,
                   admin=Depends(require_admin)) -> JSONResponse:
    from api.admin import _range  # reuse the shared date-range parser
    scope = await _analytics_scope(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content=await db.retention_overview(scope, dt_from, dt_to))


@admin_router.get("/funnel")
async def funnel(product_id: Optional[int] = None,
                 partner_id: Optional[int] = None,
                 from_: Optional[str] = Query(default=None, alias="from"),
                 to: Optional[str] = None,
                 admin=Depends(require_admin)) -> JSONResponse:
    """Entry funnel: deeplinks -> starts -> linked -> subscribed -> engaged ->
    photos -> handoffs, for the range."""
    from api.admin import _range
    scope = await _analytics_scope(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content=await db.retention_funnel(scope, dt_from, dt_to))


@admin_router.get("/timeseries")
async def timeseries(product_id: Optional[int] = None,
                     partner_id: Optional[int] = None,
                     from_: Optional[str] = Query(default=None, alias="from"),
                     to: Optional[str] = None,
                     admin=Depends(require_admin)) -> JSONResponse:
    """Daily retention activity (messages, active players, photos, pings, cost)."""
    from api.admin import _range
    scope = await _analytics_scope(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content={
        "series": await db.retention_timeseries(scope, dt_from, dt_to)})


@admin_router.get("/users")
async def users(product_id: int, limit: int = 100, offset: int = 0,
                admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_retention_users(product_id, limit=min(limit, 500),
                                               offset=offset)})


@admin_router.get("/sessions")
async def sessions(product_id: int, page: int = 1, page_size: int = 25,
                   admin=Depends(require_admin)) -> JSONResponse:
    """Telegram chat sessions for a product (the Retention → Conversations tab).

    Support chats never appear here, and Telegram chats never appear in the
    support Conversations / Unresolved lists — the two channels are logged
    apart. The transcript of a row is read via the shared
    GET /admin/session/{id} (same scope check).
    """
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_sessions(
        product_id, page=page, page_size=min(page_size, 100)))
