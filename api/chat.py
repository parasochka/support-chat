"""Universal, consumer-agnostic chat API. All JSON; session = UUID.

Endpoints:
  POST /api/chat/session       create session, issue token, return topic catalogue
  GET  /api/chat/topics        session-free topic catalogue (instant first paint)
  POST /api/chat/topic         select a topic (loads KB into session context)
  POST /api/chat/message       one chat turn (gated, persisted atomically)
  GET  /api/chat/session/{id}  resume: history + state (token required)
  POST /api/chat/escalate      explicit escalation; returns button payload
"""
from __future__ import annotations

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
import prompt_store
import settings

router = APIRouter(prefix="/api/chat", tags=["chat"])


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


class EscalateReq(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _client_ip(request: Request) -> str:
    """Resolve the real client IP without trusting attacker-controlled input.

    `X-Forwarded-For` is appended left-to-right (client, proxy1, proxy2, …), so
    the left-most entry is fully client-supplied and trivially spoofable. We take
    the value `TRUSTED_PROXY_COUNT` hops from the RIGHT — the address our own
    edge actually observed — so a forged left-hand IP cannot rotate around the
    rate limiter.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            idx = min(max(config.TRUSTED_PROXY_COUNT, 1), len(parts))
            return parts[-idx]
    return request.client.host if request.client else "unknown"


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


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
        await db.log_admin_event(None, "rate_limited", {"ip": ip, "scope": "session"})
        return _err(exc.status, exc.code, exc.detail)

    # reCaptcha (skips gracefully in dev; logs the skip)
    captcha = await antispam.verify_recaptcha(body.recaptcha_token, ip)
    if captcha.get("skipped"):
        await db.log_admin_event(None, "recaptcha_skipped", {"reason": captcha.get("reason")})
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

    # Assign the prompt version for this session (A/B split or live default).
    new_id = str(uuid.uuid4())
    prompt_version_id = await prompt_store.resolve_for_new_session(new_id)

    session_id = await db.create_session(
        consumer=body.consumer or "web",
        player_id=body.player_id or (user_context.get("id") if user_context else None),
        lang=session_lang,
        user_context=user_context,
        prompt_version_id=prompt_version_id,
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
    answer_lang = config.DEFAULT_LANGUAGE if resolved == language.AUTO else resolved
    topics = await kb.catalogue(lang=answer_lang)
    resp = JSONResponse(
        status_code=200,
        content={
            "topics": topics,
            "lang": answer_lang,
            "languages": config.SUPPORTED_LANGUAGES,
        },
    )
    # The catalogue changes only on a KB edit; a short browser cache makes
    # re-opens (and quick navigations) instant without going stale for long.
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

    # 1. verify session token -> 401
    session, err = await _auth_session(authorization, body.session_id)
    if err:
        return err

    # 2. rate-limit (IP) -> 429 + log
    try:
        antispam.check_rate_limit(ip)
    except antispam.AntiSpamError as exc:
        await db.log_admin_event(body.session_id, "rate_limited", {"ip": ip})
        return _err(exc.status, exc.code, exc.detail)

    # 3. cooldown -> 429
    try:
        antispam.check_cooldown(body.session_id)
    except antispam.AntiSpamError as exc:
        return _err(exc.status, exc.code, exc.detail)

    # 4. body/input caps -> 413/400  (body cap handled by middleware; input here)
    try:
        antispam.check_input_length(body.text)
    except antispam.AntiSpamError as exc:
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
        await db.log_admin_event(body.session_id, "low_content_blocked",
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
        await db.log_admin_event(body.session_id, "injection_blocked",
                                 {"sample": body.text[:120],
                                  "blocked": hard_block})
        if hard_block:
            return _err(400, "rejected",
                        "Your message looks like an attempt to manipulate the "
                        "assistant. Please ask a product-support question.")

    # 5. message cap reached -> force escalation response (no model call)
    if session.get("message_count", 0) >= settings.escalation()["max_messages_per_session"]:
        ans_lang = (session.get("conv_lang") or session.get("lang")
                    or language.default_code())
        if not session.get("escalated", False):
            await db.mark_escalated(body.session_id)
            await db.log_admin_event(body.session_id, "escalation",
                                     {"reason": "message_cap"})
            esc_payload = await escalation.open_ticket(session, "cap_reached", ans_lang)
        else:
            esc_payload = escalation.build_payload(ans_lang)
        return JSONResponse(
            status_code=200,
            content={
                "reply": esc_payload["message"],
                "lang": ans_lang,
                "escalation": esc_payload,
                "message_count": session.get("message_count", 0),
            },
        )

    # -> build prompt, call model, persist turn atomically, return
    result = await chat_service.handle_message(session, body.text)
    return JSONResponse(
        status_code=200,
        content={
            "reply": result.reply,
            "lang": result.lang,
            "escalation": result.escalation,
            "message_count": result.message_count,
            # {slug, title} when the model routed the question to another topic.
            "suggested_topic": result.suggested_topic,
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

    ans_lang = (session.get("conv_lang") or session.get("lang")
                or language.default_code())
    if not session.get("escalated", False):
        await db.mark_escalated(body.session_id)
        await db.log_admin_event(body.session_id, "escalation", {"reason": "explicit"})
        esc_payload = await escalation.open_ticket(session, "user_request", ans_lang)
    else:
        esc_payload = escalation.build_payload(ans_lang)

    return JSONResponse(
        status_code=200,
        content={"escalation": esc_payload},
    )
