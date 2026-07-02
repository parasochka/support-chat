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
    # Signed handshake blob (HMAC) from the host backend. When the service is
    # configured with WIDGET_HANDSHAKE_SECRET this is the ONLY trusted source of
    # user_context; unsigned context is ignored.
    signed_context: Optional[str] = None
    # Browser language (navigator.language); the single source for the session's
    # answer + chrome language.
    locale: Optional[str] = None
    recaptcha_token: Optional[str] = None


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
def _client_ip(request: Request) -> str:
    return client_ip(request)


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
    """Verify bearer token bound to session_id, then load the session row."""
    try:
        token = auth.extract_bearer(authorization)
        auth.verify_session_token(token, session_id)
    except auth.TokenError as exc:
        return None, _err(401, "unauthorized", str(exc))
    session = await db.get_session(session_id)
    if session is None:
        return None, _err(404, "not_found", "Session not found.")
    return session, None


# ---------------------------------------------------------------------------
# POST /api/chat/session
# ---------------------------------------------------------------------------
@router.post("/session")
async def create_session(req: Request, body: SessionCreate) -> JSONResponse:
    ip = _client_ip(req)

    # IP rate-limit session creation too (separate budget from /message) so a
    # bot can't mint unlimited sessions/tokens/DB rows when reCaptcha is unset.
    try:
        antispam.check_rate_limit(f"session:{ip}")
    except antispam.AntiSpamError as exc:
        await db.log_admin_event_sampled(None, "rate_limited",
                                         {"ip": ip, "scope": "session"})
        return _err(exc.status, exc.code, exc.detail)

    # reCaptcha (skips gracefully in dev; logs the skip, sampled — it fires on
    # EVERY dev session create otherwise)
    captcha = await antispam.verify_recaptcha(body.recaptcha_token, ip)
    if captcha.get("skipped"):
        await db.log_admin_event_sampled(None, "recaptcha_skipped",
                                         {"reason": captcha.get("reason")})
    elif not captcha.get("ok"):
        await db.log_admin_event(None, "recaptcha_blocked",
                                 {"reason": captcha.get("reason"), "score": captcha.get("score")})
        return _err(403, "recaptcha_failed", "reCaptcha verification failed.")

    # --- resolve trusted user_context (signed handshake §9) -----------------
    # Precedence of trust:
    #   1. signed_context present -> verify HMAC + expiry; trust that payload only.
    #   2. WIDGET_HANDSHAKE_SECRET configured but no signature -> production mode:
    #      do NOT trust browser-supplied context; zero it (anonymous session OK).
    #   3. No secret configured -> dev/test: the admin-configured test profile
    #      stands in for the host site (or the raw widget context if disabled).
    # The injection sanitizer (prompts.sanitize_user_context) runs regardless.
    user_context: dict[str, Any] = {}
    if body.signed_context:
        try:
            payload = auth.verify_handshake(body.signed_context)
        except auth.TokenError as exc:
            await db.log_admin_event(None, "handshake_rejected", {"reason": str(exc)})
            return _err(401, "bad_handshake", str(exc))
        user_context = {k: v for k, v in payload.items() if k not in ("iat", "exp")}
    elif config.WIDGET_HANDSHAKE_SECRET:
        if body.user_context:
            await db.log_admin_event(None, "unsigned_context_ignored",
                                     {"ip": ip})
        user_context = {}
    else:
        # Dev/test: no host site to sign a handshake. The admin "Test sandbox"
        # profile (app_settings) stands in for it so the owner can drive the
        # Layer-3 player data and pin the language for end-to-end testing.
        tp = settings.test_profile()
        if tp.get("enabled"):
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

    # The session's one language is the browser language (navigator.language),
    # mapped to a supported code; unknown locales fall back to the service
    # default. It drives both the answer language and the widget chrome.
    resolved = language.resolve(locale=body.locale)
    session_lang = None if resolved == language.AUTO else resolved

    new_id = str(uuid.uuid4())
    session_id = await db.create_session(
        consumer=body.consumer or "web",
        player_id=body.player_id or (user_context.get("id") if user_context else None),
        lang=session_lang,
        user_context=user_context,
        session_id=new_id,
    )
    token = auth.issue_session_token(session_id)
    topics = await kb.catalogue(lang=session_lang or language.default_code())

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
@router.get("/topics")
async def list_catalogue(lang: Optional[str] = None,
                         locale: Optional[str] = None) -> JSONResponse:
    """Return the topic picker without a session, token, reCaptcha, or DB write.

    The category buttons are static, language-derivable data, yet in POST
    /session they were trapped behind the (slow) reCaptcha script load + session
    create, so the widget showed an empty panel for seconds on open. Splitting
    the catalogue into its own cacheable GET lets the widget paint the buttons
    immediately while reCaptcha + session creation run in the background. The
    language is the browser language — the widget passes the code it already
    resolved as `lang` (or the raw `locale`); both map to the same base code as
    create_session, so the titles match the session from the first paint.
    """
    resolved = language.resolve(locale=lang or locale)
    answer_lang = language.default_code() if resolved == language.AUTO else resolved
    topics = await kb.catalogue(lang=answer_lang)
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
async def widget_i18n() -> JSONResponse:
    """Resolved widget-scope copy for every supported language.

    The widget paints instantly from its baked-in defaults, then fetches this and
    merges the resolved strings over them — so copy edited in the admin
    Translations tab (and languages added beyond the built-ins) reaches the
    chrome without a widget redeploy. Session-free and cacheable like /topics.
    """
    codes = language.supported_codes()
    resp = JSONResponse(
        status_code=200,
        content={
            "languages": codes,
            "strings": translations.widget_strings(codes),
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
    ip = _client_ip(req)
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
        ans_lang = (session.get("conv_lang") or session.get("lang")
                    or language.default_code())
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
        log.warning(
            "chat_message_injection_detected session_id=%s hard_block=%s",
            body.session_id, hard_block,
        )
        await db.log_admin_event_sampled(body.session_id, "injection_blocked",
                                         {"sample": body.text[:120],
                                          "blocked": hard_block})
        if hard_block:
            return _err(400, "rejected",
                        "Your message looks like an attempt to manipulate the "
                        "assistant. Please ask a product-support question.")

    # 5. message cap reached -> force escalation response (no model call)
    if session.get("message_count", 0) >= settings.escalation()["max_messages_per_session"]:
        log.info(
            "chat_message_cap_reached session_id=%s count=%s",
            body.session_id, session.get("message_count", 0),
        )
        ans_lang = (session.get("conv_lang") or session.get("lang")
                    or language.default_code())
        esc_payload = escalation.build_payload(ans_lang)
        new_count = await db.persist_turn(
            session_id=body.session_id,
            user_text=body.text,
            user_lang=None,
            assistant_text=esc_payload["message"],
            assistant_lang=ans_lang,
            ai_meta=None,
        )
        # Always close (idempotent): a session soft-escalated earlier already
        # has escalated=TRUE but must still be CLOSED when the cap fires.
        await db.mark_escalated(body.session_id)
        if session.get("status") != "escalated":
            await db.log_admin_event(body.session_id, "escalation",
                                     {"reason": "message_cap"})
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

    ans_lang = (session.get("conv_lang") or session.get("lang")
                or language.default_code())
    # Always close (idempotent) — an earlier SOFT keyword escalation left the
    # session open with escalated=TRUE; the explicit tap is a HARD hand-off.
    await db.mark_escalated(body.session_id)
    if session.get("status") != "escalated":
        await db.log_admin_event(body.session_id, "escalation", {"reason": "explicit"})
    esc_payload = escalation.build_payload(ans_lang)

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
