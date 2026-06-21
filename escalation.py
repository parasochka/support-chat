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


def is_high_risk(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(k in lowered for k in _HIGHRISK_KEYWORDS)


def decide(
    *,
    user_text: str,
    model_signalled: bool,
    message_count: int,
    topic_slug: Optional[str],
    already_escalated: bool = False,
) -> EscalationDecision:
    """Combine all escalation triggers into a single decision."""
    if already_escalated:
        return EscalationDecision(True, "already_escalated")
    if is_high_risk(user_text):
        return EscalationDecision(True, "high_risk")
    if user_requests_human(user_text):
        return EscalationDecision(True, "user_requested_human")
    if message_count >= config.MAX_MESSAGES_PER_SESSION:
        return EscalationDecision(True, "message_cap")
    if model_signalled:
        if topic_slug == "other" and message_count < OTHER_MAX_TURNS:
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
    """Return the escalation block for the API response."""
    code = lang if lang in _MESSAGES else config.DEFAULT_LANGUAGE
    code = code if code in _MESSAGES else "en"
    return {
        "active": True,
        "message": _MESSAGES[code],
        "button": {
            "label": _BUTTON_LABELS[code],
            "url": config.CONTACT_FORM_URL or "",
        },
    }


def inactive_payload() -> dict:
    return {"active": False}
