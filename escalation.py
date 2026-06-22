"""Escalation decision + contact-button payload (no form / live agent in Phase 1).

Escalate when ANY of:
  - the model signalled it cannot help (leading [[ESCALATE]] tag, stripped upstream)
  - the user explicitly asks for a human / operator / complaint
  - message_count >= MAX_MESSAGES_PER_SESSION
  - topic is 'other' and the model could not resolve after N turns
  - high-risk keywords (fraud / legal threats) -> immediate escalation
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config

# Topic 'other' escalates if unresolved after this many turns.
OTHER_MAX_TURNS = 3

# Explicit human-request keywords (multi-language).
_HUMAN_KEYWORDS = (
    "оператор", "человек", "поддержк", "жалоба", "претензия",
    "agent", "human", "operator", "complaint", "representative",
    "agente", "humano", "queja", "reclamo",
    "operatör", "şikayet", "temsilci",
    "atendente", "reclamação", "humano",
)

# High-risk keywords -> immediate escalation.
_HIGHRISK_KEYWORDS = (
    "fraud", "scam", "stolen", "lawsuit", "lawyer", "police", "chargeback",
    "мошенн", "украл", "суд", "адвокат", "полиц", "обман",
    "fraude", "estafa", "robaron", "demanda", "abogado",
    "dolandırıcı", "avukat", "mahkeme",
    "golpe", "advogado", "processo",
)


@dataclass
class EscalationDecision:
    active: bool
    reason: Optional[str] = None


def user_requests_human(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(k in lowered for k in _HUMAN_KEYWORDS)


def is_high_risk(text: str, keywords: Optional[tuple] = None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    kws = keywords if keywords is not None else _HIGHRISK_KEYWORDS
    return any(k in lowered for k in kws)


def decide(
    *,
    user_text: str,
    model_signalled: bool,
    message_count: int,
    topic_slug: Optional[str],
    already_escalated: bool = False,
) -> EscalationDecision:
    """Combine all escalation triggers into a single decision.

    Thresholds/keywords come from the resolved runtime settings (app_settings >
    env > default), so the owner can tune them live without a redeploy.
    """
    import settings  # lazy import to avoid a settings<->escalation cycle
    cfg = settings.escalation()
    max_messages = cfg["max_messages_per_session"]
    unresolved_turns = cfg["unresolved_turns_before_escalate"]
    high_risk_keywords = tuple(cfg["high_risk_keywords"])

    if already_escalated:
        return EscalationDecision(True, "already_escalated")
    if is_high_risk(user_text, high_risk_keywords):
        return EscalationDecision(True, "high_risk")
    if user_requests_human(user_text):
        return EscalationDecision(True, "user_requested_human")
    if message_count >= max_messages:
        return EscalationDecision(True, "message_cap")
    if model_signalled:
        if topic_slug == "other" and message_count < unresolved_turns:
            # 'other' gets a couple of turns before we hand off, unless the
            # model itself gave up — which it just did, so escalate.
            return EscalationDecision(True, "model_signalled_other")
        return EscalationDecision(True, "model_signalled")
    return EscalationDecision(False)


# Localized escalation copy. Falls back to English.
_MESSAGES = {
    "en": "I'll connect you with our support team — they can take it from here.",
    "ru": "Я передам ваш вопрос в службу поддержки — они помогут дальше.",
    "es": "Te conectaré con nuestro equipo de soporte — ellos continuarán desde aquí.",
    "tr": "Sizi destek ekibimize bağlayacağım — buradan itibaren onlar yardımcı olacak.",
    "pt": "Vou conectar você com nossa equipe de suporte — eles continuarão a partir daqui.",
}
_BUTTON_LABELS = {
    "en": "Contact support",
    "ru": "Связаться с поддержкой",
    "es": "Contactar soporte",
    "tr": "Desteğe ulaşın",
    "pt": "Falar com o suporte",
}


def build_payload(lang: str) -> dict:
    """Return the escalation block for the API response.

    The fallback language and the contact-button URL come from the resolved
    runtime settings (app_settings > env > default), so the owner tunes them in
    the admin panel without a redeploy.
    """
    import language  # lazy to keep this module import-light
    import settings
    default = language.default_code()
    code = lang if lang in _MESSAGES else default
    code = code if code in _MESSAGES else "en"
    return {
        "active": True,
        "message": _MESSAGES[code],
        "button": {
            "label": _BUTTON_LABELS[code],
            "url": settings.general()["contact_form_url"] or "",
        },
    }


def inactive_payload() -> dict:
    return {"active": False}


# ---------------------------------------------------------------------------
# Phase 2 — ticket snapshot + Telegram notification (button always retained)
# ---------------------------------------------------------------------------
def _transcript_snapshot(history: list[dict], limit: int = 20) -> list[dict]:
    convo = [m for m in history if m.get("role") in ("user", "assistant")]
    return [{"role": m["role"], "content": m.get("content", "")}
            for m in convo[-limit:]]


async def open_ticket(session: dict, reason: str, lang: str) -> dict:
    """Snapshot the conversation into an escalation_tickets row and notify the
    agent chat via Telegram (if configured). ALWAYS returns the contact-button
    payload so the user has a usable path even when delivery fails.

    Imports are local so escalation.decide() stays a pure, DB-free function.
    """
    import db
    from notifiers import telegram

    session_id = session["id"]
    topic_slug = None
    topic_id = session.get("topic_id")
    if topic_id is not None:
        topic = await db.get_topic_by_id(topic_id)
        topic_slug = topic["slug"] if topic else None

    history = await db.get_history(session_id, limit=20)
    payload = {
        "session_id": session_id,
        "reason": reason,
        "topic": topic_slug,
        "lang": lang,
        "player_id": session.get("player_id"),
        "user_context": session.get("user_context", {}),
        "transcript": _transcript_snapshot(history),
    }

    use_telegram = telegram.is_configured()
    channel = "telegram" if use_telegram else "button"
    ticket_id = await db.create_escalation_ticket(
        session_id=session_id, reason=reason, channel=channel,
        delivered=False, payload=payload,
    )

    if use_telegram:
        delivered = await telegram.send_escalation(payload)
        if delivered:
            await db.mark_ticket_delivered(ticket_id)
        else:
            await db.log_admin_event(
                session_id, "telegram_notify_failed", {"ticket_id": ticket_id}
            )

    return build_payload(lang)
