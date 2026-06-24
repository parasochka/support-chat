"""Escalation decision + contact-button payload (no form / live agent in Phase 1).

Escalate when ANY of:
  - high-risk keywords (fraud / legal threats) -> immediate escalation
  - the user explicitly asks for a human / operator / complaint
  - message_count would reach MAX_MESSAGES_PER_SESSION
  - the model signalled it cannot help (leading [[ESCALATE]] tag, stripped upstream)

Keyword scans run on a normalized copy of the message (mirroring
`antispam.scan_injection`) so trivial zero-width / Unicode-confusable obfuscation
can't slip a trigger past the substring match. The keyword lists are stems
(e.g. "поддержк", "мошенн") on purpose, so they keep matching inflected forms.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config

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


def _normalized(text: str) -> str:
    """Fold the message to the same canonical form `antispam.scan_injection` uses
    (NFKC + lower-case + zero-width strip + de-spacing) so obfuscated keywords are
    matched too. Lazy import keeps escalation free of an antispam->settings->
    escalation import cycle."""
    import antispam  # lazy: avoids the settings/escalation import cycle
    return antispam._normalize_for_scan(text)


def user_requests_human(text: str) -> bool:
    if not text:
        return False
    norm = _normalized(text)
    return any(k in norm for k in _HUMAN_KEYWORDS)


def is_high_risk(text: str, keywords: Optional[tuple] = None) -> bool:
    if not text:
        return False
    norm = _normalized(text)
    kws = keywords if keywords is not None else _HIGHRISK_KEYWORDS
    return any(k in norm for k in kws)


def decide(
    *,
    user_text: str,
    model_signalled: bool,
    message_count: int,
    already_escalated: bool = False,
) -> EscalationDecision:
    """Combine all escalation triggers into a single decision.

    Thresholds/keywords come from the resolved runtime settings (app_settings >
    env > default), so the owner can tune them live without a redeploy.

    `message_count` is the PROSPECTIVE count for this turn (current + 1), so the
    cap fires on the turn that reaches the limit. The model-free fast path in
    api/chat.py is the cheap belt-and-suspenders for a session already AT/over the
    cap (e.g. after the owner lowers it mid-session); the two are complementary,
    not a duplicate check.
    """
    import settings  # lazy import to avoid a settings<->escalation cycle
    cfg = settings.escalation()
    max_messages = cfg["max_messages_per_session"]
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
