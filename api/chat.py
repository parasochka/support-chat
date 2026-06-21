"""Universal, consumer-agnostic chat API. All JSON; session = UUID.

Endpoints:
  POST /api/chat/session       create session, issue token, return topic catalogue
  POST /api/chat/topic         select a topic (loads KB into session context)
  POST /api/chat/message       one chat turn (gated, persisted atomically)
  GET  /api/chat/session/{id}  resume: history + state (token required)
  POST /api/chat/escalate      explicit escalation; returns button payload
"""
from __future__ import annotations

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

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# request models
# ---------------------------------------------------------------------------
class SessionCreate(BaseModel):
    consumer: Optional[str] = "web"
    player_id: Optional[str] = None
    user_context: dict[str, Any] = Field(default_factory=dict)
    lang: Optional[str] = None
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
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
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

    # reCaptcha (skips gracefully in dev; logs the skip)
    captcha = await antispam.verify_recaptcha(body.recaptcha_token, ip)
    if captcha.get("skipped"):
        await db.log_admin_event(None, "recaptcha_skipped", {"reason": captcha.get("reason")})
    elif not captcha.get("ok"):
        await db.log_admin_event(None, "recaptcha_blocked",
                                 {"reason": captcha.get("reason"), "score": captcha.get("score")})
        return _err(403, "recaptcha_failed", "reCaptcha verification failed.")

    resolved = language.resolve(lang=body.lang, locale=body.locale)
    session_lang = None if resolved == language.AUTO else resolved

    session_id = await db.create_session(
        consumer=body.consumer or "web",
        player_id=body.player_id,
        lang=session_lang,
        user_context=body.user_context or {},
    )
    token = auth.issue_session_token(session_id)
    topics = await kb.catalogue(lang=session_lang or config.DEFAULT_LANGUAGE)

    return JSONResponse(
        status_code=200,
        content={
            "session_id": session_id,
            "token": token,
            "topics": topics,
            "lang": session_lang or config.DEFAULT_LANGUAGE,
        },
    )


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

    # 8. injection scan (log, don't reject)
    if antispam.scan_injection(body.text):
        await db.log_admin_event(body.session_id, "injection_blocked",
                                 {"sample": body.text[:120]})

    # 5. message cap reached -> force escalation response (no model call)
    if session.get("message_count", 0) >= config.MAX_MESSAGES_PER_SESSION:
        ans_lang = session.get("lang") or config.DEFAULT_LANGUAGE
        if not session.get("escalated", False):
            await db.mark_escalated(body.session_id)
            await db.log_admin_event(body.session_id, "escalation",
                                     {"reason": "message_cap"})
        return JSONResponse(
            status_code=200,
            content={
                "reply": escalation.build_payload(ans_lang)["message"],
                "lang": ans_lang,
                "escalation": escalation.build_payload(ans_lang),
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

    ans_lang = session.get("lang") or config.DEFAULT_LANGUAGE
    if not session.get("escalated", False):
        await db.mark_escalated(body.session_id)
        await db.log_admin_event(body.session_id, "escalation", {"reason": "explicit"})

    return JSONResponse(
        status_code=200,
        content={"escalation": escalation.build_payload(ans_lang)},
    )
