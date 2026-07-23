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

import asyncio
import hmac
import logging
import os
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
import openai_client
import player_sync
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


def _ct_eq(a: str, b: str) -> bool:
    """Constant-time string compare that tolerates non-ASCII input.

    hmac.compare_digest raises TypeError on a str containing a non-ASCII char,
    and Starlette decodes header bytes as latin-1, so an attacker-supplied
    Authorization / secret-token header with any byte > 0x7F would crash the
    compare -> HTTP 500. On the per-product partner path that 500-vs-401 split
    is a tenant-enumeration oracle (500 => the product exists AND has a secret).
    Comparing UTF-8 bytes makes any malformed token an ordinary auth failure."""
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except (AttributeError, TypeError):
        return False


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
    if not _ct_eq(x_telegram_bot_api_secret_token or "",
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
            # The model-visible whitelist — one source (prompts._CONTEXT_FIELDS),
            # so a new context field automatically reaches this branch too.
            context = {k: tp.get(k) for k in prompts._CONTEXT_FIELDS
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
    # Casino-side activity signals (ISO-8601 timestamps) — the agent's state
    # resolver keys on them (idle days, days since deposit).
    last_login_at: Optional[str] = None
    last_played_at: Optional[str] = None
    last_deposit_at: Optional[str] = None


async def _partner_auth(product_id: int,
                        authorization: Optional[str]
                        ) -> tuple[Optional[dict[str, Any]],
                                   Optional[JSONResponse]]:
    """Shared partner-secret Bearer check for the /partner/* webhooks
    (player-update and the canonical event feed). Returns (product, error).

    All pre-auth failure modes collapse into ONE opaque 401: product ids are
    sequential integers, so distinguishable "no such product" / "no secret
    configured" / "bad secret" responses let an unauthenticated caller walk
    the id space and map which tenants exist and which have partner
    integrations. The true reason still lands in the server log.
    """
    product = await db.get_product(product_id)
    secret = (await db.get_product_handshake_secret(product_id)
              if product is not None else None)
    token = None
    try:
        token = auth.extract_bearer(authorization)
    except auth.TokenError:
        pass
    if (product is None or not secret or token is None
            or not _ct_eq(token, secret)):
        if product is None:
            reason = "unknown_product"
        elif not secret:
            reason = "no_partner_secret"
        else:
            reason = "bad_or_missing_token"
        log.warning("partner_auth_failed product_id=%s reason=%s",
                    product_id, reason)
        return None, _err(401, "unauthorized", "Unauthorized.")
    return product, None


@public_router.post("/partner/{product_id}/player-update")
async def player_update(product_id: int, body: PlayerUpdateReq, req: Request,
                        authorization: Optional[str] = Header(default=None)
                        ) -> JSONResponse:
    """CRM pushes a (partial) profile change. Authorized with the product's
    handshake secret as a shared partner secret (Bearer). Fields left null are
    not touched (partial update)."""
    # Per-IP rate limit BEFORE the auth work: _partner_auth does a DB lookup + a
    # secretbox decrypt on every call, so an unauthenticated flood would otherwise
    # drive unbounded DB + crypto load (every other public POST is throttled).
    try:
        antispam.check_rate_limit(f"partner:{client_ip(req)}")
    except antispam.AntiSpamError as exc:
        return _err(exc.status, exc.code, exc.detail)
    _product, err = await _partner_auth(product_id, authorization)
    if err is not None:
        return err
    profile = {k: v for k, v in body.model_dump().items()
               if k != "player_id" and v is not None}
    updated = await player_sync.apply_profile_push(product_id, body.player_id,
                                                   profile)
    return JSONResponse(content={"ok": True, "updated": updated})


# ===========================================================================
# Partner canonical-event feed (the retention agent; also refreshes the
# player activity timestamps the state resolver reads)
# ===========================================================================
class PlayerEventsReq(BaseModel):
    """One canonical event (flat fields) OR a batch (`events` list)."""
    events: Optional[list[dict[str, Any]]] = None
    event_id: Optional[str] = None
    event_name: Optional[str] = None
    player_id: Optional[str] = None
    user_id: Optional[str] = None
    # Explicit Telegram recipient for a player_id linked to several TG accounts
    # (multi-tester setups). Declared so the FLAT single-event form doesn't drop
    # it — pydantic's extra='ignore' silently discarded a top-level tg_user_id
    # before player_sync._validate_event (which supports it) ever saw it, so the
    # documented flat shape misrouted the reaction to another account.
    tg_user_id: Optional[int] = None
    timestamp: Optional[str] = None
    event_version: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


_MAX_EVENT_BATCH = 500


@public_router.post("/partner/{product_id}/event")
async def player_event(product_id: int, body: PlayerEventsReq, req: Request,
                       authorization: Optional[str] = Header(default=None)
                       ) -> JSONResponse:
    """Canonical casino events (the EPIC-1 taxonomy), single or batch.

    Same partner-secret auth as player-update. Idempotent by event_id — resend
    freely (at-least-once delivery); duplicates are counted, not stored. Every
    stored event also bumps the matching v1 activity timestamps (the legacy
    bridge), so this ONE feed powers both retention regimes.
    """
    # Per-IP rate limit before the auth DB lookup + secretbox decrypt (see
    # player_update — the partner POSTs are the one public family that lacked it).
    try:
        antispam.check_rate_limit(f"partner:{client_ip(req)}")
    except antispam.AntiSpamError as exc:
        return _err(exc.status, exc.code, exc.detail)
    _product, err = await _partner_auth(product_id, authorization)
    if err is not None:
        return err
    if body.events is not None:
        if len(body.events) > _MAX_EVENT_BATCH:
            return _err(413, "batch_too_large",
                        f"At most {_MAX_EVENT_BATCH} events per request.")
        result = await player_sync.ingest_events(product_id, body.events)
        return JSONResponse(content={"ok": True, **result})
    evt = {k: v for k, v in body.model_dump().items()
           if k != "events" and v is not None}
    try:
        result = await player_sync.ingest_event(product_id, evt)
    except player_sync.EventError as exc:
        return _err(422, "invalid_event", str(exc))
    return JSONResponse(content={"ok": True, **result})


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
    # The persona-appearance block, grounded in the product's REAL photo
    # library (no player in a preview, so no "last sent" photo).
    appearance: Optional[dict] = None
    try:
        appearance = await db.retention_appearance_context(product_id, 0)
    except Exception:  # pragma: no cover - preview must never break the page
        appearance = None
    lang = language.default_code()
    messages = prompts.build_retention_messages(
        session={"user_context": _preview_context()},
        kb_block=kb_block,
        history=[],
        user_text=_RETENTION_PREVIEW_USER_TEXT,
        resolved_lang=lang,
        photo_candidates=_RETENTION_PREVIEW_CANDIDATES,
        appearance=appearance,
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
# under their own product-settings key; every key ships its OWN retention
# default and an empty override falls back to that default — the Telegram
# persona is a SEPARATE prompt, never inheriting from the support chat. The
# one editor is the Retention → Prompt variables tab.
# ===========================================================================
class RetentionPromptVariablesWrite(BaseModel):
    value: Any = Field(...)


def _retention_variables_payload() -> list[dict[str, Any]]:
    """The variables list for the admin editor (scope already bound).

    `value` is the raw stored override ('' = using the default), `default` the
    retention default that applies when the override is empty (the Telegram
    persona is independent of the support chat — no support inheritance),
    `resolved` what the prompt actually renders with.
    """
    overrides = settings_mod.retention_prompt_variable_overrides()
    resolved = settings_mod.retention_prompt_variables()
    out = []
    for key, desc, default, _renders in prompts.RETENTION_PROMPT_VARIABLES:
        default = default or ""
        out.append({
            "key": key, "description": desc,
            "default": default,
            "value": overrides.get(key, ""),
            "resolved": resolved.get(key, default),
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
    try:
        # The retention KB feeds the model-facing prompt (Layer 2) - English only.
        settings_mod.ensure_english(body.text, "retention KB")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        sort_order=body.sort_order, active=body.active,
        updated_by=admin.get("email"))
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


def _photo_gate_bounds(product_id: int) -> tuple[list[str], int]:
    """The product's real (vip_tiers, max_stage) — the gate a photo must fit.

    Binds the tenancy scope so the `retention` settings group resolves per
    product (the same resolution the delivery gate and metadata generation use).
    """
    tenancy.set_current_product(product_id)
    rt = settings_mod.retention()
    vip_tiers = [str(t) for t in rt.get("vip_tiers") or ["none"]]
    return vip_tiers, int(rt.get("max_stage") or 1)


def _clamp_photo_gate(*, stage: Optional[int], level_min: Optional[int],
                      vip_tiers: list[str], max_stage: int) -> dict[str, int]:
    """Clamp a hand-entered photo stage/level_min into the product's real ranges.

    stage: 1..max_stage (explicitness — 1 is the softest, there is nothing above
    max_stage). level_min: 0..top-tier ordinal (the minimum VIP tier to unlock).
    Mirrors _parse_photo_meta so a value typed in the media library (or posted by
    an API consumer) can never sit outside what the delivery gate can ever serve
    — a stage of 0 or 6, or a tier index past the last tier, is impossible.
    """
    out: dict[str, int] = {}
    top_tier = max(len(vip_tiers or ["none"]) - 1, 0)
    if stage is not None:
        out["stage"] = min(max(int(stage), 1), max(int(max_stage), 1))
    if level_min is not None:
        out["level_min"] = min(max(int(level_min), 0), top_tier)
    return out


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
    """Upload media binaries (photos AND videos) + create their catalogue rows.

    Bulk-friendly: `files` takes any number of files in one request (the shared
    form fields apply to every one — typically left blank and filled by the AI
    metadata generation afterwards). The single `file` field stays accepted for
    older API consumers. Validation runs BEFORE anything is written, so one bad
    file rejects the batch instead of half-uploading it. After the batch lands,
    the media normalizer runs for this product in the background (WebP for
    photos, Telegram MP4 + poster frame for videos) — no waiting for the
    hourly sweep.
    """
    import media_normalizer
    await admin_auth.require_product_write(admin, product_id)
    uploads = [u for u in ([file] if file else []) + list(files or []) if u]
    if not uploads:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    exts = []
    for up in uploads:
        ext = os.path.splitext(up.filename or "")[1].lower() or ".jpg"
        if ext not in _PHOTO_EXTS + media_normalizer.VIDEO_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported media type: {up.filename or ext}")
        exts.append(ext)
    os.makedirs(config.RETENTION_MEDIA_DIR, exist_ok=True)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    # Keep the gate values inside the product's real ranges (stage 1..max_stage,
    # level 0..top tier) so a bad hand-entered number can't create an unservable
    # photo — the same clamp the AI-metadata path applies.
    vip_tiers, max_stage = _photo_gate_bounds(product_id)
    gate = _clamp_photo_gate(stage=stage, level_min=level_min,
                             vip_tiers=vip_tiers, max_stage=max_stage)
    level_min, stage = gate["level_min"], gate["stage"]
    photos = []
    for up, ext in zip(uploads, exts):
        storage_ref = f"{product_id}_{uuid.uuid4().hex}{ext}"
        content = await up.read()

        # Off-thread: a multi-MB synchronous write onto the network Volume on
        # the event loop stalls every concurrent chat turn on the instance.
        def _write(path: str = os.path.join(config.RETENTION_MEDIA_DIR,
                                            storage_ref),
                   data: bytes = content) -> None:
            with open(path, "wb") as fh:
                fh.write(data)

        await asyncio.to_thread(_write)
        photo = await db.create_retention_photo(
            product_id, storage_ref=storage_ref, description=description,
            tags=tag_list, level_min=level_min, stage=stage,
            category=category.strip() or None, sort_order=sort_order,
            created_by=admin.get("email"),
            media_type="video" if ext in media_normalizer.VIDEO_EXTS
            else "photo")
        photos.append(photo)
    await db.log_admin_event(None, "retention_photo_created",
                             {"ids": [p["id"] for p in photos],
                              "count": len(photos), "by": admin.get("email")},
                             product_id=product_id)
    # Instant normalization: the batch is delivery-ready in moments (photos ->
    # WebP, videos -> Telegram MP4 + poster). Background + advisory-locked;
    # the hourly sweep remains the backstop if this run fails.
    media_normalizer.schedule_product_normalization(product_id)
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
_PHOTO_META_WAVE = 5  # concurrent vision calls per wave; the balancing counts
                      # are refreshed between waves (see generate_photo_metadata)
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
    clamped = _clamp_photo_gate(stage=stage, level_min=level_min,
                                vip_tiers=vip_tiers, max_stage=max_stage)
    return {"description": description[:1000], "tags": tags, **clamped}


async def _generate_photo_meta(client: Any, photo: dict[str, Any],
                               product_id: int, vip_tiers: list[str],
                               max_stage: int,
                               library_counts: Optional[dict[str, dict[int, int]]] = None,
                               ) -> dict[str, Any]:
    """One item: read the binary, one vision call, validate, update the row.

    A VIDEO is rated by its poster frame (extracted during normalization; a
    missing poster is extracted on demand here) — the model sees one
    representative frame with a "this is a frame from a short video" note.
    """
    import base64
    import media_normalizer
    ref = photo.get("storage_ref")
    path = os.path.join(config.RETENTION_MEDIA_DIR, os.path.basename(ref or ""))
    if not ref or not os.path.exists(path):
        return {"id": photo["id"], "ok": False, "error": "file missing on disk"}
    is_video = photo.get("media_type") == "video"
    if is_video:
        poster_ref = media_normalizer.poster_ref_for(os.path.basename(ref))
        if not poster_ref:
            return {"id": photo["id"], "ok": False,
                    "error": "no poster frame for this video"}
        poster_path = os.path.join(config.RETENTION_MEDIA_DIR, poster_ref)
        if not os.path.exists(poster_path):
            ok = await asyncio.to_thread(
                media_normalizer.extract_poster, path, poster_path,
                max_side=config.RETENTION_MEDIA_VIDEO_MAX_SIDE_PX)
            if not ok:
                return {"id": photo["id"], "ok": False,
                        "error": "could not extract a video frame"}
        path = poster_path
    ext = os.path.splitext(path)[1].lower()
    mime = _PHOTO_META_CONTENT_TYPES.get(ext, "image/jpeg")

    # Off-thread: the multi-MB read + base64 encode would otherwise run on the
    # event loop, five times back-to-back per generation wave.
    def _read_data_url() -> str:
        with open(path, "rb") as fh:
            return (f"data:{mime};base64,"
                    + base64.b64encode(fh.read()).decode("ascii"))

    data_url = await asyncio.to_thread(_read_data_url)
    messages = prompts.build_photo_meta_messages(data_url, vip_tiers, max_stage,
                                                 library_counts=library_counts,
                                                 is_video=is_video)
    try:
        result = await client.complete(messages)
    except Exception as exc:  # noqa: BLE001 - one bad photo must not kill the batch
        await db.log_ai_interaction(None, None, None, None, None, None, None,
                                    None, ok=False,
                                    error=f"photo_meta: {exc.__class__.__name__}",
                                    product_id=product_id)
        return {"id": photo["id"], "ok": False, "error": "model call failed"}
    cost = openai_client.compute_cost(result.model, result.tokens_in, result.tokens_out,
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
    vip_tiers, max_stage = _photo_gate_bounds(product_id)
    client = await openai_client.client_for_product(product_id)
    # Library distribution for the balancing block: the model rates each photo
    # independently, so without seeing the current spread everything clusters
    # on the same one-two values and the unlock ladder has nothing to serve.
    # Photos in this batch are about to be re-rated — their old numbers are
    # excluded. The batch runs in WAVES (not one big gather): between waves the
    # counts absorb the fresh ratings, so even a brand-new 20-photo library
    # spreads across the levels instead of all landing on the same guess.
    batch_ids = {p["id"] for p in photos}
    stage_counts = {s: 0 for s in range(1, max_stage + 1)}
    level_counts = {lv: 0 for lv in range(len(vip_tiers))}
    for row in await db.list_retention_photos(product_id, active_only=True):
        if row["id"] in batch_ids:
            continue
        if row.get("stage") in stage_counts:
            stage_counts[row["stage"]] += 1
        if row.get("level_min") in level_counts:
            level_counts[row["level_min"]] += 1
    results: list[dict[str, Any]] = []
    for i in range(0, len(photos), _PHOTO_META_WAVE):
        wave = photos[i:i + _PHOTO_META_WAVE]
        counts = {"stage": dict(stage_counts), "level": dict(level_counts)}
        wave_results = await asyncio.gather(*[
            _generate_photo_meta(client, p, product_id, vip_tiers, max_stage,
                                 library_counts=counts)
            for p in wave
        ])
        for r in wave_results:
            meta = r.get("photo") if r.get("ok") else None
            if meta:
                if meta.get("stage") in stage_counts:
                    stage_counts[meta["stage"]] += 1
                if meta.get("level_min") in level_counts:
                    level_counts[meta["level_min"]] += 1
        results.extend(wave_results)
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
    # Clamp any stage/level_min edit into the product's real ranges (see
    # _clamp_photo_gate) so the row can never gate outside the delivery gate.
    if "stage" in fields or "level_min" in fields:
        vip_tiers, max_stage = _photo_gate_bounds(photo["product_id"])
        fields.update(_clamp_photo_gate(
            stage=fields.get("stage"), level_min=fields.get("level_min"),
            vip_tiers=vip_tiers, max_stage=max_stage))
    updated = await db.update_retention_photo(photo_id, **fields)
    return JSONResponse(content={"photo": updated})


class NormalizePhotosReq(BaseModel):
    product_id: int


@admin_router.post("/photos/normalize")
async def normalize_photos(body: NormalizePhotosReq,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    """Run the media normalizer for one product NOW (the hourly sweep's admin
    button): re-encode heavy JPG/PNG uploads to Telegram-sized WebP and delete
    the originals. Bypasses the per-product enabled switch — pressing the
    button IS the opt-in for this run."""
    await admin_auth.require_product_write(admin, body.product_id)
    import media_normalizer
    stats = await media_normalizer.normalize_product_photos(
        body.product_id, force=True)
    return JSONResponse(content={"stats": stats})


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
async def get_photo_file(photo_id: int, poster: bool = False,
                         admin=Depends(require_admin)) -> Any:
    """Serve a media binary for the admin preview (guarded).

    `poster=1` on a VIDEO row serves its poster frame instead of the video
    binary — the grid preview shows a still, not a multi-MB download.
    """
    import media_normalizer
    photo = await db.get_retention_photo(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    await admin_auth.require_product_read(admin, photo["product_id"])
    ref = photo.get("storage_ref")
    if not ref:
        raise HTTPException(status_code=404, detail="No stored file.")
    ref = os.path.basename(ref)
    if poster and photo.get("media_type") == "video":
        poster_ref = media_normalizer.poster_ref_for(ref)
        if poster_ref and os.path.exists(
                os.path.join(config.RETENTION_MEDIA_DIR, poster_ref)):
            ref = poster_ref
        else:
            raise HTTPException(status_code=404,
                                detail="Poster not extracted yet.")
    path = os.path.join(config.RETENTION_MEDIA_DIR, os.path.basename(ref))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File missing on disk.")
    # The stored binary is immutable per photo id, so let the browser cache the
    # admin preview instead of re-downloading it on every grid render/pagination.
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


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
# Admin: the retention agent — event log, decision ledger, simulator.
# (Endpoint paths keep the historic /v2/ segment so stored bookmarks and API
# consumers survive; every user-visible label says "agent".)
# ===========================================================================
class SimulateEventReq(BaseModel):
    event_name: str
    player_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None
    # Optional explicit recipient: pins the exact Telegram account when the
    # player_id is linked to several (the one-test-player-many-testers setup).
    tg_user_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Idle re-engagement rules (the agent's inactivity ladder) — admin CRUD +
# a bounded manual test run. Rules live in retention_rules (retention_idle.py
# is the worker-side sweep).
# ---------------------------------------------------------------------------
_RULE_TRIGGERS = ("bot_inactivity", "casino_inactivity", "no_deposit")
# 'photo' offers the normal mixed media feed (photos + up to 1/3 videos, the
# model picks); 'video' restricts the candidates to videos only.
_RULE_ACTIONS = ("message", "photo", "video")


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
            try:
                # Model-facing prompt material — English by invariant §7.
                settings_mod.ensure_english(fields["intent"], "intent")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        return fields


@admin_router.get("/idle/rules")
async def list_idle_rules(product_id: int,
                          admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_retention_rules(product_id),
        "triggers": list(_RULE_TRIGGERS),
        "actions": list(_RULE_ACTIONS)})


@admin_router.post("/idle/rules")
async def create_idle_rule(product_id: int, body: RuleWrite,
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


@admin_router.put("/idle/rules/{rule_id}")
async def update_idle_rule(rule_id: int, product_id: int, body: RuleWrite,
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


@admin_router.delete("/idle/rules/{rule_id}")
async def delete_idle_rule(rule_id: int, product_id: int,
                           admin=Depends(require_admin_write)) -> JSONResponse:
    await admin_auth.require_product_write(admin, product_id)
    ok = await db.delete_retention_rule(rule_id, product_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Rule not found.")
    await db.log_admin_event(None, "retention_rule_deleted",
                             {"id": rule_id, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True})


@admin_router.get("/idle/ledger")
async def list_idle_ledger(product_id: int, page: int = 1, page_size: int = 50,
                           admin=Depends(require_admin)) -> JSONResponse:
    """The proactive-send ledger: who was nudged, by which rule, at what cost."""
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_pings(
        product_id, page=max(page, 1), page_size=max(1, min(page_size, 200))))


@admin_router.post("/idle/run")
async def run_idle_pings_now(product_id: int,
                             admin=Depends(require_admin_write)) -> JSONResponse:
    """Run one bounded idle sweep for this product immediately (test/QA button).

    Quiet hours and the in-process pacing are ignored (the operator is
    explicitly asking); every other guard — per-player caps, gaps, rule
    cooldowns, opt-outs, dry-run — still applies.
    """
    import retention_idle
    await admin_auth.require_product_write(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not product.get("retention_enabled"):
        raise HTTPException(status_code=409,
                            detail="Retention is not enabled for this product.")
    tenancy.set_current_product(product_id)
    # Locked variant: shares the worker's advisory lock so the button and the
    # sweep never evaluate the same player's send-frequency guards in parallel
    # (the same race the v2 «Process queue now» button is locked against).
    stats = await retention_idle.run_product_idle_pings_locked(
        product, settings_mod.retention(), force=True)
    await db.log_admin_event(None, "retention_ping_run_manual",
                             {"stats": stats, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"stats": stats})


@admin_router.get("/v2/status")
async def v2_status(product_id: int,
                    admin=Depends(require_admin)) -> JSONResponse:
    """The agent tab header: switches + today's spend vs budget + queue depth,
    the worker wiring (deploy scheduler switch + the hot sweep cadence), a
    DB-derived liveness snapshot (last event / last processed / last decision
    + today's decision mix), the event taxonomy split (decision-worthy /
    photo-eligible / state-food) and the EFFECTIVE guard knob values — so the
    tab's How-it-works guide always matches the code and the current tuning."""
    import retention_v2
    await admin_auth.require_product_read(admin, product_id)
    tenancy.set_current_product(product_id)
    cfg = settings_mod.retention()
    queued = await db.count_unprocessed_retention_events(product_id)
    cost_today = await db.retention_v2_cost_today(product_id)
    activity = await db.retention_v2_activity(product_id)
    return JSONResponse(content={
        "v2_enabled": bool(cfg.get("v2_enabled")),
        "v2_dry_run": bool(cfg.get("v2_dry_run")),
        "daily_budget_usd": float(cfg.get("v2_daily_budget_usd") or 0),
        "cost_today_usd": cost_today,
        "queued_events": queued,
        # Worker wiring: the deploy-level scheduler switch + the hot cadence
        # setting (retention.worker_interval_sec — Settings → Retention bot).
        "scheduler_enabled": bool(config.RETENTION_SCHEDULER_ENABLED),
        "sweep_interval_sec": retention_v2.worker_interval_sec(),
        "activity": activity,
        "canonical_events": sorted(player_sync.CANONICAL_EVENTS),
        # The EFFECTIVE decision set (retention.v2_decision_events, API-tunable;
        # the SPA uses it for the simulator chip + the agent guide).
        "decision_events": sorted(retention_v2.effective_decision_events(cfg)),
        "photo_events": sorted(retention_v2._PHOTO_EVENTS),
        "idle_pings_enabled": bool(cfg.get("idle_pings_enabled")),
        "send_delay_min_sec": int(cfg.get("v2_send_delay_min_sec") or 0),
        "send_delay_max_sec": int(cfg.get("v2_send_delay_max_sec") or 0),
        "same_event_cooldown_hours": int(
            cfg.get("v2_same_event_cooldown_hours") or 0),
        # The effective per-player send-frequency guards, so the admin page
        # can show (and link to) the knobs that decide how often the agent
        # may write to one player.
        "guards": {
            "ping_daily_cap": int(cfg.get("ping_daily_cap") or 0),
            "ping_min_gap_hours": int(cfg.get("ping_min_gap_hours") or 0),
            "quiet_hours_start": int(cfg.get("quiet_hours_start") or 0),
            "quiet_hours_end": int(cfg.get("quiet_hours_end") or 0),
            "quiet_hours_utc_offset": int(
                cfg.get("quiet_hours_utc_offset") or 0),
            "same_event_cooldown_hours": int(
                cfg.get("v2_same_event_cooldown_hours") or 0),
            "daily_budget_usd": float(cfg.get("v2_daily_budget_usd") or 0),
            "loss_comfort_hours": int(cfg.get("v2_loss_comfort_hours") or 0),
            "loss_high_usd": float(cfg.get("v2_loss_high_usd") or 0),
        },
    })


@admin_router.get("/v2/events")
async def v2_events(product_id: int, page: int = 1, page_size: int = 50,
                    admin=Depends(require_admin)) -> JSONResponse:
    """The canonical-event log (webhook + simulator), newest first."""
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_events(
        product_id, page=max(page, 1), page_size=max(1, min(page_size, 200))))


@admin_router.get("/v2/decisions")
async def v2_decisions(product_id: int, page: int = 1, page_size: int = 50,
                       admin=Depends(require_admin)) -> JSONResponse:
    """The agent decision ledger: state snapshot, guard verdict, decision,
    delivery and cost per row — the full audit trail."""
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_v2_decisions(
        product_id, page=max(page, 1), page_size=max(1, min(page_size, 200))))


@admin_router.get("/v2/logs")
async def v2_logs(product_id: int, page: int = 1, page_size: int = 50,
                  admin=Depends(require_admin)) -> JSONResponse:
    """The v2 system log: the durable `retention_v2_*` admin events
    (decisions, simulator injections, manual runs, deletes) — the admin-
    readable mirror of the Railway `retention_v2_*` log lines."""
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content=await db.list_retention_v2_logs(
        product_id, page=max(page, 1), page_size=max(1, min(page_size, 200))))


@admin_router.delete("/v2/events/{event_pk}")
async def v2_delete_event(product_id: int, event_pk: int,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    """Delete one canonical event (test cleanup — e.g. a duplicated simulator
    injection). Ledger rows that referenced it stay, minus the link."""
    await admin_auth.require_product_write(admin, product_id)
    if not await db.delete_retention_event(product_id, event_pk):
        raise HTTPException(status_code=404, detail="Event not found.")
    await db.log_admin_event(None, "retention_v2_event_deleted",
                             {"event_pk": event_pk, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True})


@admin_router.delete("/v2/events")
async def v2_clear_events(product_id: int,
                          admin=Depends(require_admin_write)) -> JSONResponse:
    """Wipe this product's whole canonical-event log (test cleanup). NB: the
    log also feeds the state resolver (loss window, recent activity), so this
    resets that derived state too."""
    await admin_auth.require_product_write(admin, product_id)
    deleted = await db.clear_retention_events(product_id)
    await db.log_admin_event(None, "retention_v2_events_cleared",
                             {"deleted": deleted, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True, "deleted": deleted})


@admin_router.delete("/v2/decisions/{decision_pk}")
async def v2_delete_decision(product_id: int, decision_pk: int,
                             admin=Depends(require_admin_write)) -> JSONResponse:
    """Delete one decision-ledger row (test cleanup). Deleting a row also
    'refunds' its cost from today's budget and re-arms the same-event
    cooldown for that event type."""
    await admin_auth.require_product_write(admin, product_id)
    if not await db.delete_retention_v2_decision(product_id, decision_pk):
        raise HTTPException(status_code=404, detail="Decision not found.")
    await db.log_admin_event(None, "retention_v2_decision_deleted",
                             {"decision_pk": decision_pk,
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True})


@admin_router.delete("/v2/decisions")
async def v2_clear_decisions(product_id: int,
                             admin=Depends(require_admin_write)
                             ) -> JSONResponse:
    """Wipe this product's whole decision ledger (test cleanup). Today's
    budget counter and all same-event cooldowns reset with it."""
    await admin_auth.require_product_write(admin, product_id)
    deleted = await db.clear_retention_v2_decisions(product_id)
    await db.log_admin_event(None, "retention_v2_decisions_cleared",
                             {"deleted": deleted, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True, "deleted": deleted})


@admin_router.post("/v2/simulate-event")
async def v2_simulate_event(product_id: int, body: SimulateEventReq,
                            admin=Depends(require_admin_write)) -> JSONResponse:
    """Inject one canonical event as if the casino had sent it (source is
    marked 'simulator'), so the whole pipeline can be exercised end-to-end
    before the partner integration exists."""
    await admin_auth.require_product_write(admin, product_id)
    evt = {
        "event_id": f"sim_{uuid.uuid4().hex}",
        "event_name": body.event_name,
        "player_id": body.player_id,
        "payload": body.payload,
        "timestamp": body.timestamp,
        "tg_user_id": body.tg_user_id,
    }
    try:
        result = await player_sync.ingest_event(product_id, evt,
                                                source="simulator")
    except player_sync.EventError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.log_admin_event(None, "retention_v2_simulated_event",
                             {"event": body.event_name,
                              "player_id": body.player_id,
                              "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"ok": True, **result})


@admin_router.post("/v2/run")
async def v2_run_now(product_id: int,
                     admin=Depends(require_admin_write)) -> JSONResponse:
    """Drain this product's event queue through the v2 pipeline immediately
    (the tab's test button; the worker loop does the same on its timer)."""
    import retention_v2
    await admin_auth.require_product_write(admin, product_id)
    product = await db.get_product(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    # Locked variant: shares the worker's advisory lock so the button and the
    # sweep never evaluate the same player's send-frequency guards in parallel.
    stats = await retention_v2.run_product_events_locked(product)
    await db.log_admin_event(None, "retention_v2_run_manual",
                             {"stats": stats, "by": admin.get("email")},
                             product_id=product_id)
    return JSONResponse(content={"stats": stats})


# ===========================================================================
# Admin: analytics
# ===========================================================================
@admin_router.get("/overview")
async def overview(product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None,
                   admin=Depends(require_admin)) -> JSONResponse:
    from api.admin import _range  # reuse the shared date-range parser
    # Support-dashboard scope convention (resolve_scope_filter): explicit
    # product read-checked; nothing selected = the caller's whole accessible
    # scope; None = all, [] = none.
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
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
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
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
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content={
        "series": await db.retention_timeseries(scope, dt_from, dt_to)})


@admin_router.get("/users")
async def users(product_id: int, limit: int = 100, offset: int = 0,
                admin=Depends(require_admin)) -> JSONResponse:
    await admin_auth.require_product_read(admin, product_id)
    return JSONResponse(content={
        "items": await db.list_retention_users(product_id,
                                               limit=max(1, min(limit, 500)),
                                               offset=max(offset, 0))})


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
        product_id, page=max(page, 1), page_size=max(1, min(page_size, 100))))
