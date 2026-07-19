"""Universal, consumer-agnostic chat API. All JSON; session = UUID.

Endpoints:
  POST /api/chat/session       create session, issue token, return topic catalogue
  GET  /api/chat/topics        session-free topic catalogue (instant first paint)
  POST /api/chat/topic         select a topic (loads KB into session context)
  POST /api/chat/message       one chat turn (gated, persisted atomically)
  GET  /api/chat/session/{id}  resume: history + state (token required)
  POST /api/chat/escalate      explicit escalation; returns button payload
  POST /api/chat/resolve       player ended the chat (finish-chat nudge); closes it
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import antispam
import auth
import chat_service
import config
import db
import escalation
import kb
import language
import settings
import tenancy
import translations
from api.client_ip import client_ip

router = APIRouter(prefix="/api/chat", tags=["chat"])
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------
class SessionCreate(BaseModel):
    consumer: Optional[str] = "web"
    player_id: Optional[str] = None
    user_context: dict[str, Any] = Field(default_factory=dict)
    # Signed handshake blob (HMAC) from the host backend. When the product (or
    # the deploy) is configured with a handshake secret this is the ONLY trusted
    # source of user_context; unsigned context is ignored.
    signed_context: Optional[str] = None
    # Browser language (navigator.language); the single source for the session's
    # answer + chrome language.
    locale: Optional[str] = None
    turnstile_token: Optional[str] = None
    # Public product identifier (multi-tenancy): tells the service WHICH casino
    # this widget belongs to. Absent -> the boot-seeded default product, so a
    # single-product deployment keeps working without an embed change.
    widget_key: Optional[str] = None


class TopicSelect(BaseModel):
    session_id: str
    topic_slug: str


class MessageSend(BaseModel):
    session_id: str
    text: str
    # Set by the widget when the player tapped the declarative "Issue solved."
    # closing bubble: the turn is a farewell, so the model is asked for a pure
    # goodbye with no follow-up that would reopen the conversation.
    closing: bool = False


class EscalateReq(BaseModel):
    session_id: str


class ResolveReq(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


def _ensure_open_session(session: dict) -> Optional[JSONResponse]:
    """Reject stale mutating requests for a session that is no longer open."""
    if session.get("status") == "open":
        return None
    return _err(
        409,
        "session_closed",
        "This chat session is already closed. Please start a new chat.",
    )


async def _auth_session(authorization: Optional[str], session_id: str
                        ) -> tuple[Optional[dict], Optional[JSONResponse]]:
    """Verify bearer token bound to session_id, then load the session row.

    Also binds the request to the session's PRODUCT (tenancy scope), so every
    downstream settings/KB/translations read resolves for the right casino.
    """
    try:
        token = auth.extract_bearer(authorization)
        auth.verify_session_token(token, session_id)
    except auth.TokenError as exc:
        return None, _err(401, "unauthorized", str(exc))
    session = await db.get_session(session_id)
    if session is None:
        return None, _err(404, "not_found", "Session not found.")
    tenancy.set_current_product(session.get("product_id"))
    return session, None


async def _resolve_product(widget_key: Optional[str]
                           ) -> tuple[Optional[dict], Optional[JSONResponse]]:
    """Map a widget key to its product (multi-tenancy entry point).

    No key -> the boot-seeded default product (single-product back-compat).
    An unknown or inactive key is rejected: it either means a stale embed
    (rotated key) or a third party probing another tenant's chat.
    """
    key = (widget_key or "").strip()
    if key:
        product = await db.get_product_by_widget_key(key)
        if product is None or not product.get("active"):
            return None, _err(403, "bad_widget_key",
                              "Unknown or inactive widget key.")
        return product, None
    product = await db.get_default_product()
    if product is None or not product.get("active"):
        return None, _err(403, "no_product",
                          "No active product is configured for this widget.")
    return product, None


# ---------------------------------------------------------------------------
# POST /api/chat/session
# ---------------------------------------------------------------------------
@router.post("/session")
async def create_session(req: Request, body: SessionCreate) -> JSONResponse:
    ip = client_ip(req)

    # IP rate-limit session creation too (separate budget from /message) so a
    # bot can't mint unlimited sessions/tokens/DB rows when Turnstile is unset.
    try:
        antispam.check_rate_limit(f"session:{ip}")
    except antispam.AntiSpamError as exc:
        await db.log_admin_event_sampled(None, "rate_limited",
                                         {"ip": ip, "scope": "session"})
        return _err(exc.status, exc.code, exc.detail)

    # --- resolve the product (multi-tenancy) ---------------------------------
    # The widget key names the casino; everything below (Turnstile secret,
    # handshake secret, test profile, language set, topics) resolves for it.
    product, err = await _resolve_product(body.widget_key)
    if err:
        await db.log_admin_event_sampled(None, "widget_key_rejected", {"ip": ip})
        return err
    tenancy.set_current_product(product["id"])

    # Cloudflare Turnstile — ADVISORY, fail-open: a missing token (the Turnstile
    # script is blocked in some regions) and a verifier outage SKIP the check
    # (logged, sampled — the skip fires on EVERY dev session create otherwise);
    # only an explicit "invalid token" verdict from Cloudflare blocks. Verified
    # against the PRODUCT's own Turnstile secret when it has one (each client
    # domain runs its own Turnstile widget); the deploy env secret is only the
    # fallback.
    product_turnstile = await db.get_product_turnstile_secret(product["id"])
    captcha = await antispam.verify_turnstile(body.turnstile_token, ip,
                                              secret=product_turnstile)
    if captcha.get("skipped"):
        await db.log_admin_event_sampled(None, "turnstile_skipped",
                                         {"reason": captcha.get("reason")})
    elif not captcha.get("ok"):
        await db.log_admin_event_sampled(None, "turnstile_blocked",
                                         {"reason": captcha.get("reason")},
                                         product_id=product["id"])
        return _err(403, "turnstile_failed", "Turnstile verification failed.")

    # --- resolve trusted user_context (signed handshake §9) -----------------
    # Precedence of trust:
    #   1. signed_context present -> verify HMAC + expiry; trust that payload
    #      only. The signature is checked against the PRODUCT's own handshake
    #      secret when it has one, else the deploy-level env secret.
    #   2. A handshake secret configured (product or env) but no signature ->
    #      production mode: do NOT trust browser-supplied context; zero it
    #      (anonymous session OK).
    #   3. No secret anywhere -> dev/test: the admin-configured test profile
    #      stands in for the host site (or the raw widget context if disabled).
    # The injection sanitizer (prompts.sanitize_user_context) runs regardless.
    product_handshake = await db.get_product_handshake_secret(product["id"])
    user_context: dict[str, Any] = {}
    context_source = "anonymous"
    if body.signed_context:
        try:
            payload = auth.verify_handshake(body.signed_context,
                                            secret=product_handshake)
        except auth.TokenError as exc:
            await db.log_admin_event_sampled(None, "handshake_rejected",
                                             {"reason": str(exc)},
                                             product_id=product["id"])
            return _err(401, "bad_handshake", str(exc))
        user_context = {k: v for k, v in payload.items() if k not in ("iat", "exp")}
        context_source = "signed_handshake"
    elif product_handshake or config.WIDGET_HANDSHAKE_SECRET:
        if body.user_context:
            await db.log_admin_event_sampled(None, "unsigned_context_ignored",
                                             {"ip": ip}, product_id=product["id"])
        user_context = {}
        context_source = "zeroed_handshake_required"
    else:
        # Dev/test: no host site to sign a handshake. The admin "Test sandbox"
        # profile (app_settings) stands in for it so the owner can drive the
        # Layer-3 player data and pin the language for end-to-end testing.
        tp = settings.test_profile()
        if tp.get("enabled"):
            context_source = "test_profile"
            user_context = {
                "id": tp.get("id") or None,
                "full_name": tp.get("full_name") or None,
                "email": tp.get("email") or None,
                "activation_status": tp.get("activation_status") or None,
                "country": tp.get("country") or None,
                "balance": tp.get("balance") or None,
                "vip_level": tp.get("vip_level") or None,
                "registration_date": tp.get("registration_date") or None,
            }
        else:
            user_context = body.user_context or {}
            context_source = "widget_context" if user_context else "anonymous"

    # Which source fed the player context and whether the by-name greeting has a
    # name to work with — the first thing to check when personalization looks off.
    log.info(
        "chat_session_context source=%s has_name=%s",
        context_source, bool((user_context or {}).get("full_name")),
    )

    # The session's one language is the browser language (navigator.language),
    # mapped to a supported code; unknown locales fall back to the service
    # default. It drives both the answer language and the widget chrome.
    resolved = language.resolve(locale=body.locale)
    session_lang = None if resolved == language.AUTO else resolved

    # The player id is persisted on the session and surfaced in admin dashboards
    # (attribution, escalations). When a handshake secret is configured the ONLY
    # trusted identity source is the signed payload (user_context) — a raw
    # client-supplied body.player_id must NOT override it, or an attacker could
    # attribute their session (and any escalation/fraud signal it raises) to a
    # victim's player id. Fall back to body.player_id only in dev mode, where no
    # authoritative source exists.
    ctx_player_id = user_context.get("id") if user_context else None
    handshake_enforced = bool(product_handshake or config.WIDGET_HANDSHAKE_SECRET)
    resolved_player_id = ctx_player_id if handshake_enforced else (
        body.player_id or ctx_player_id)

    new_id = str(uuid.uuid4())
    session_id = await db.create_session(
        consumer=body.consumer or "web",
        player_id=resolved_player_id,
        lang=session_lang,
        user_context=user_context,
        session_id=new_id,
        product_id=product["id"],
    )
    token = auth.issue_session_token(session_id)
    topics = await kb.catalogue(lang=session_lang or language.default_code(),
                                product_id=product["id"])

    return JSONResponse(
        status_code=200,
        content={
            "session_id": session_id,
            "token": token,
            "topics": topics,
            "lang": session_lang or language.default_code(),
            "languages": language.supported_codes(),
        },
    )


# ---------------------------------------------------------------------------
# GET /api/chat/topics  -- session-free catalogue for an instant first paint
# ---------------------------------------------------------------------------
# Session-free catalogue/i18n GETs do per-request DB work (product lookup, topics,
# translations). The 60s Cache-Control only helps cooperating browsers; a direct
# client can loop them at line rate (also probing widget keys). A GENEROUS per-IP
# cap bounds that abuse without hurting real traffic — even a busy NAT'd office
# opens the widget far below this. Deliberately high; it only catches egregious
# line-rate abuse, not normal re-opens.
_CATALOGUE_RATE_LIMIT = 600


@router.get("/topics")
async def list_catalogue(req: Request,
                         lang: Optional[str] = None,
                         locale: Optional[str] = None,
                         widget_key: Optional[str] = None) -> JSONResponse:
    """Return the topic picker without a session, token, Turnstile, or DB write.

    The category buttons are static, language-derivable data, yet in POST
    /session they were trapped behind the (slow) Turnstile script load + session
    create, so the widget showed an empty panel for seconds on open. Splitting
    the catalogue into its own cacheable GET lets the widget paint the buttons
    immediately while Turnstile + session creation run in the background. The
    language is the browser language — the widget passes the code it already
    resolved as `lang` (or the raw `locale`); both map to the same base code as
    create_session, so the titles match the session from the first paint.
    `widget_key` picks the product whose catalogue (and language set) to serve;
    it is part of the URL, so the browser cache stays per product.
    """
    ip = client_ip(req)
    try:
        antispam.check_rate_limit(f"catalogue:{ip}", _CATALOGUE_RATE_LIMIT)
    except antispam.AntiSpamError as exc:
        await db.log_admin_event_sampled(None, "rate_limited",
                                         {"ip": ip, "scope": "catalogue"})
        return _err(exc.status, exc.code, exc.detail)
    product, err = await _resolve_product(widget_key)
    if err:
        return err
    tenancy.set_current_product(product["id"])
    resolved = language.resolve(locale=lang or locale)
    answer_lang = language.default_code() if resolved == language.AUTO else resolved
    topics = await kb.catalogue(lang=answer_lang, product_id=product["id"])
    resp = JSONResponse(
        status_code=200,
        content={
            "topics": topics,
            "lang": answer_lang,
            "languages": language.supported_codes(),
        },
    )
    # The catalogue changes only on a KB edit; a short browser cache makes
    # re-opens (and quick navigations) instant without going stale for long.
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# ---------------------------------------------------------------------------
# GET /api/chat/i18n  -- widget chrome strings (admin Translations > defaults)
# ---------------------------------------------------------------------------
@router.get("/i18n")
async def widget_i18n(req: Request,
                      widget_key: Optional[str] = None) -> JSONResponse:
    """Resolved widget-scope copy for every supported language.

    The widget paints instantly from its baked-in defaults, then fetches this and
    merges the resolved strings over them — so copy edited in the admin
    Translations tab (and languages added beyond the built-ins) reaches the
    chrome without a widget redeploy. Session-free and cacheable like /topics.
    `widget_key` scopes the copy (and the language set) to the product.
    """
    ip = client_ip(req)
    try:
        antispam.check_rate_limit(f"catalogue:{ip}", _CATALOGUE_RATE_LIMIT)
    except antispam.AntiSpamError as exc:
        await db.log_admin_event_sampled(None, "rate_limited",
                                         {"ip": ip, "scope": "catalogue"})
        return _err(exc.status, exc.code, exc.detail)
    product, err = await _resolve_product(widget_key)
    if err:
        return err
    tenancy.set_current_product(product["id"])
    codes = language.supported_codes()
    resp = JSONResponse(
        status_code=200,
        content={
            "languages": codes,
            "strings": translations.widget_strings(codes),
            # The product's PUBLIC Turnstile site key (env pair as fallback):
            # the widget adopts it unless the host page pinned its own, so a
            # per-domain key needs no embed change.
            "turnstile_site_key": (product.get("turnstile_site_key")
                                   or config.TURNSTILE_SITE_KEY or None),
        },
    )
    # Same short cache as /topics: changes only on an admin edit.
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# ---------------------------------------------------------------------------
# POST /api/chat/topic
# ---------------------------------------------------------------------------
@router.post("/topic")
async def select_topic(body: TopicSelect,
                       authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    session, err = await _auth_session(authorization, body.session_id)
    if err:
        return err
    closed = _ensure_open_session(session)
    if closed:
        return closed

    topic = await kb.topic_by_slug(body.topic_slug)
    if topic is None:
        return _err(400, "bad_topic", f"Unknown topic slug: {body.topic_slug}")

    await db.set_session_topic(body.session_id, topic["id"])
    return JSONResponse(status_code=200, content={"ok": True})


# ---------------------------------------------------------------------------
# POST /api/chat/message
# ---------------------------------------------------------------------------
@router.post("/message")
async def send_message(req: Request, body: MessageSend,
                       authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    ip = client_ip(req)
    log.info(
        "chat_message_received session_id=%s ip=%s chars=%s",
        body.session_id, ip, len(body.text or ""),
    )

    # 1. verify session token -> 401
    session, err = await _auth_session(authorization, body.session_id)
    if err:
        log.warning("chat_message_auth_failed session_id=%s", body.session_id)
        return err
    log.info(
        "chat_message_session_loaded session_id=%s topic_id=%s message_count=%s status=%s",
        body.session_id, session.get("topic_id"),
        session.get("message_count", 0), session.get("status"),
    )
    closed = _ensure_open_session(session)
    if closed:
        log.info(
            "chat_message_rejected_closed_session session_id=%s status=%s",
            body.session_id, session.get("status"),
        )
        return closed

    # 2. rate-limit (IP) -> 429 + log
    try:
        antispam.check_rate_limit(ip)
    except antispam.AntiSpamError as exc:
        log.warning(
            "chat_message_rate_limited session_id=%s ip=%s code=%s",
            body.session_id, ip, exc.code,
        )
        await db.log_admin_event_sampled(body.session_id, "rate_limited", {"ip": ip})
        return _err(exc.status, exc.code, exc.detail)

    # 3. cooldown -> 429
    try:
        antispam.check_cooldown(body.session_id)
    except antispam.AntiSpamError as exc:
        log.warning(
            "chat_message_cooldown_blocked session_id=%s code=%s",
            body.session_id, exc.code,
        )
        return _err(exc.status, exc.code, exc.detail)

    # 4. body/input caps -> 413/400  (body cap handled by middleware; input here)
    try:
        antispam.check_input_length(body.text)
    except antispam.AntiSpamError as exc:
        log.warning(
            "chat_message_input_too_long session_id=%s chars=%s code=%s",
            body.session_id, len(body.text or ""), exc.code,
        )
        return _err(exc.status, exc.code, exc.detail)

    # 4b. low-content guard: lone characters, symbol/emoji-only spam, or one
    # character mashed repeatedly carry nothing to answer, so we never call the
    # model (no tokens burned). Return a localized nudge as a normal 200 turn
    # rather than a hard error; don't persist or count it toward the cap.
    try:
        antispam.check_low_content(body.text)
    except antispam.AntiSpamError:
        ans_lang = language.session_base_lang(session)
        log.info(
            "chat_message_low_content_blocked session_id=%s chars=%s",
            body.session_id, len(body.text or ""),
        )
        await db.log_admin_event_sampled(body.session_id, "low_content_blocked",
                                         {"sample": body.text[:120]})
        return JSONResponse(
            status_code=200,
            content={
                "reply": antispam.low_content_reply(ans_lang),
                "lang": ans_lang,
                "escalation": {"active": False},
                "message_count": session.get("message_count", 0),
            },
        )

    # 8. injection scan: always audit; optionally hard-block (settings-gated).
    if antispam.scan_injection(body.text):
        hard_block = settings.antispam()["injection_hard_block"]
        # A genuine complaint / fraud report / ask-for-a-human can share wording
        # with a jailbreak ("so you are now refusing my withdrawal, this is
        # fraud, I want a human"). Never 400 such a message: the injection scan
        # runs BEFORE the (soft) keyword-escalation gate in chat_service, so a
        # hard block here would swallow the hand-off. Let it through to be
        # escalated instead — the audit row still records the injection signal.
        escalation_intent = bool(escalation.keyword_trigger(body.text))
        blocked = hard_block and not escalation_intent
        log.warning(
            "chat_message_injection_detected session_id=%s hard_block=%s escalation_intent=%s blocked=%s",
            body.session_id, hard_block, escalation_intent, blocked,
        )
        await db.log_admin_event_sampled(body.session_id, "injection_blocked",
                                         {"sample": body.text[:120],
                                          "blocked": blocked,
                                          "escalation_intent": escalation_intent})
        if blocked:
            return _err(400, "rejected",
                        "Your message looks like an attempt to manipulate the "
                        "assistant. Please ask a product-support question.")

    # All reject-gates passed: this message is going to be answered — arm the
    # cooldown clock now (a rejected message must not throttle its own fix-up).
    antispam.arm_cooldown(body.session_id)

    # 5. message cap reached -> force escalation response (no model call)
    if session.get("message_count", 0) >= settings.general()["max_messages_per_session"]:
        log.info(
            "chat_message_cap_reached session_id=%s count=%s",
            body.session_id, session.get("message_count", 0),
        )
        ans_lang = language.session_base_lang(session)
        esc_payload = await escalation.build_payload_for_session(session, ans_lang)
        new_count = await db.persist_turn(
            session_id=body.session_id,
            user_text=body.text,
            user_lang=None,
            assistant_text=esc_payload["message"],
            assistant_lang=ans_lang,
            ai_meta=None,
            product_id=session.get("product_id"),
        )
        await escalation.apply_hard_escalation(session, "message_cap")
        return JSONResponse(
            status_code=200,
            content={
                "reply": esc_payload["message"],
                "lang": ans_lang,
                "escalation": esc_payload,
                "message_count": new_count,
            },
        )

    # -> build prompt, call model, persist turn atomically, return
    log.info("chat_message_dispatching_generation session_id=%s", body.session_id)
    result = await chat_service.handle_message(session, body.text, closing=body.closing)
    # A cross-topic routing-only turn (suggested_topic set, empty reply) triggers
    # an IMMEDIATE automatic re-ask from the widget against the switched topic.
    # That re-ask would otherwise land inside this session's message cooldown
    # window and 429 — so the original question is never answered. Release the
    # cooldown for the automated follow-up (the routing turn still burned a model
    # call; this is not a spam bypass).
    # Same release on a transient model failure: the turn was NOT persisted and the
    # nudge explicitly asks the player to resend — the resend must not be throttled
    # by the cooldown armed above (which contradicts the "please resend" reply).
    if result.suggested_topic or result.model_error:
        antispam.clear_cooldown(body.session_id)
    log.info(
        "chat_message_response_ready session_id=%s reply_chars=%s lang=%s escalation_active=%s message_count=%s",
        body.session_id, len(result.reply or ""), result.lang,
        bool((result.escalation or {}).get("active")), result.message_count,
    )
    return JSONResponse(
        status_code=200,
        content={
            "reply": result.reply,
            "lang": result.lang,
            "escalation": result.escalation,
            "message_count": result.message_count,
            # {slug, title} when the model routed the question to another topic.
            "suggested_topic": result.suggested_topic,
            # Up to 2 guide-to-KB follow-up questions rendered as one-tap bubbles.
            "suggestions": result.suggestions or [],
            # The declarative closing option ("Issue solved.") rendered as a
            # distinct finish-the-chat bubble; tapping it resolves the session.
            "closing_suggestion": result.closing_suggestion,
            # True when the question looks resolved -> widget offers "finish chat".
            "resolved": result.resolved,
        },
    )


# ---------------------------------------------------------------------------
# GET /api/chat/session/{id}  -- resume
# ---------------------------------------------------------------------------
@router.get("/session/{session_id}")
async def resume_session(session_id: str,
                         authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    session, err = await _auth_session(authorization, session_id)
    if err:
        return err

    history = await db.get_history(session_id, limit=50)
    visible = [
        {"role": m["role"], "content": m["content"], "lang": m.get("lang")}
        for m in history
        if m["role"] in ("user", "assistant")
    ]
    return JSONResponse(
        status_code=200,
        content={
            "session_id": session_id,
            "status": session.get("status"),
            "escalated": session.get("escalated"),
            "lang": session.get("lang"),
            "message_count": session.get("message_count"),
            "history": visible,
        },
    )


# ---------------------------------------------------------------------------
# POST /api/chat/escalate  -- explicit escalation
# ---------------------------------------------------------------------------
@router.post("/escalate")
async def escalate(body: EscalateReq,
                   authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    session, err = await _auth_session(authorization, body.session_id)
    if err:
        return err
    closed = _ensure_open_session(session)
    if closed:
        return closed

    ans_lang = language.session_base_lang(session)
    # The explicit tap is a HARD hand-off (an earlier SOFT keyword escalation
    # left the session open).
    await escalation.apply_hard_escalation(session, "explicit")
    esc_payload = await escalation.build_payload_for_session(session, ans_lang)

    return JSONResponse(
        status_code=200,
        content={"escalation": esc_payload},
    )


# ---------------------------------------------------------------------------
# POST /api/chat/resolve  -- player ended the chat via the "finish chat" nudge
# ---------------------------------------------------------------------------
@router.post("/resolve")
async def resolve(body: ResolveReq,
                  authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    """Close a session the player finished after a [[RESOLVED]] turn.

    Marks status='resolved' (unless the session is escalated — a pending hand-off
    is never closed by the player) and logs the close as an admin event. Idempotent
    and best-effort: the widget collapses the panel regardless of the outcome.
    """
    session, err = await _auth_session(authorization, body.session_id)
    if err:
        return err

    already = session.get("status") == "resolved"
    if not already and session.get("status") != "escalated":
        await db.mark_resolved(body.session_id)
        await db.log_admin_event(body.session_id, "session_resolved",
                                 {"message_count": session.get("message_count", 0)})

    return JSONResponse(status_code=200, content={"ok": True, "status": "resolved"})
