"""Escalation decision + contact-button payload (no form / live agent in Phase 1).

Escalation comes in two strengths:
  - HARD (final=True): the model signalled it cannot help ([[ESCALATE]]), the
    session hit the message cap, or the player tapped the explicit escalate
    action. The session is closed (status='escalated') and the widget ends the
    conversation — only a new chat is possible after that.
  - SOFT (final=False): a keyword trigger (high-risk fraud/legal stems or an
    explicit ask for a human) detected BEFORE the model call in chat_service.
    The contact card is shown and the session is flagged `escalated` for the
    metrics/queue, but it stays OPEN and the player can keep chatting — so a
    false-positive keyword hit (stems are fuzzy by nature) never kills a live
    conversation, and no model tokens are burned on the hand-off turn.

`decide()` covers the post-model HARD triggers (cap + model sentinel); the
keyword scans are exposed as `is_high_risk` / `user_requests_human` and run in
chat_service ahead of the model call.

Keyword scans run on a normalized copy of the message (mirroring
`antispam.scan_injection`) so trivial zero-width / Unicode-confusable obfuscation
can't slip a trigger past the match. The keyword lists are stems (e.g.
"поддержк", "мошенн") on purpose, so they keep matching inflected forms — but a
stem only matches at the START of a word (and a very short stem must match the
whole word), so "рассудите"/"судя по всему" no longer trip the "суд" stem the
way plain substring matching did. Both lists (`high_risk_keywords` and
`human_request_keywords`) are tunable live from the admin `escalation` settings
group; the constants below are the built-in defaults used until the owner
overrides them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import config

# Explicit human-request keywords (multi-language). Built-in DEFAULT only — the
# owner can override the list live from the admin `escalation` settings group
# (`human_request_keywords`), same as `high_risk_keywords` below.
_HUMAN_KEYWORDS = (
    "оператор", "человек", "поддержк", "жалоба", "претензия",
    "agent", "human", "operator", "complaint", "representative",
    "agente", "humano", "queja", "reclamo",
    "operatör", "şikayet", "temsilci",
    "atendente", "reclamação", "humano",
)

# High-risk keywords -> immediate escalation. Built-in DEFAULT only; overridable
# live from the admin `escalation` settings group (`high_risk_keywords`).
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


_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Stems this short (or shorter) must match a WHOLE word: "суд" matches "в суд"
# but no longer fires inside "судя"/"судьба"/"рассудите" — with plain substring
# (or even prefix) matching an innocent "судя по всему" escalated the chat.
_SHORT_STEM_EXACT_LEN = 3


def _matches_keywords(norm: str, keywords: tuple) -> bool:
    """True when a keyword stem/phrase hits the normalized message.

    Rules (per keyword, lower-cased):
      - a PHRASE (contains a space) matches as a substring, as before;
      - a longer stem matches only at the START of a word ("поддержк" ->
        "поддержка/поддержку", but never buried mid-word);
      - a short stem (<= _SHORT_STEM_EXACT_LEN chars) must equal a whole word,
        so 3-letter stems can't fire inside unrelated longer words.
    """
    words: Optional[list[str]] = None
    for raw in keywords:
        k = (raw or "").strip().lower()
        if not k:
            continue
        if " " in k:
            if k in norm:
                return True
            continue
        if words is None:
            words = _WORD_RE.findall(norm)
        if len(k) <= _SHORT_STEM_EXACT_LEN:
            if any(w == k for w in words):
                return True
        elif any(w.startswith(k) for w in words):
            return True
    return False


def user_requests_human(text: str, keywords: Optional[tuple] = None) -> bool:
    if not text:
        return False
    norm = _normalized(text)
    kws = keywords if keywords is not None else _HUMAN_KEYWORDS
    return _matches_keywords(norm, tuple(kws))


def is_high_risk(text: str, keywords: Optional[tuple] = None) -> bool:
    if not text:
        return False
    norm = _normalized(text)
    kws = keywords if keywords is not None else _HIGHRISK_KEYWORDS
    return _matches_keywords(norm, tuple(kws))


def keyword_trigger(user_text: str) -> Optional[str]:
    """Pre-model keyword check: the SOFT-escalation trigger, or None.

    Runs in chat_service BEFORE the OpenAI call (the keywords don't depend on
    the model, so triggering here saves the whole request). Keyword lists come
    from the resolved runtime settings so the owner tunes them live.
    """
    import settings  # lazy import to avoid a settings<->escalation cycle
    cfg = settings.escalation()
    if is_high_risk(user_text, tuple(cfg["high_risk_keywords"])):
        return "high_risk"
    if user_requests_human(user_text, tuple(cfg["human_request_keywords"])):
        return "user_requested_human"
    return None


def decide(*, model_signalled: bool, message_count: int) -> EscalationDecision:
    """Combine the post-model HARD escalation triggers into a single decision.

    Keyword triggers are handled separately (SOFT, pre-model) via
    `keyword_trigger`. A session that was soft-escalated earlier keeps chatting
    normally — there is no "already escalated" auto-trigger any more; the hard
    close comes only from the cap or the model's own [[ESCALATE]].

    `message_count` is the PROSPECTIVE count for this turn (current + 1), so the
    cap fires on the turn that reaches the limit. The model-free fast path in
    api/chat.py is the cheap belt-and-suspenders for a session already AT/over the
    cap (e.g. after the owner lowers it mid-session); the two are complementary,
    not a duplicate check.
    """
    import settings  # lazy import to avoid a settings<->escalation cycle
    cfg = settings.escalation()

    if message_count >= cfg["max_messages_per_session"]:
        return EscalationDecision(True, "message_cap")
    if model_signalled:
        return EscalationDecision(True, "model_signalled")
    return EscalationDecision(False)


# Localized escalation copy. Falls back to English. House style matches the
# model's formatting rules: no em dashes, no guillemet quotes.
_MESSAGES = {
    "en": "I'll connect you with our support team. They can take it from here.",
    "ru": "Я передам ваш вопрос в службу поддержки. Они помогут дальше.",
    "es": "Te conectaré con nuestro equipo de soporte. Ellos continuarán desde aquí.",
    "tr": "Sizi destek ekibimize bağlayacağım. Buradan itibaren onlar yardımcı olacak.",
    "pt": "Vou conectar você com nossa equipe de suporte. Eles continuarão a partir daqui.",
}
_BUTTON_LABELS = {
    "en": "Contact support",
    "ru": "Связаться с поддержкой",
    "es": "Contactar soporte",
    "tr": "Desteğe ulaşın",
    "pt": "Falar com o suporte",
}


def build_payload(lang: str, final: bool = True) -> dict:
    """Return the escalation block for the API response.

    `final` mirrors the hard/soft split (module docstring): True closes the
    conversation in the widget (hard hand-off), False shows the contact card but
    keeps the chat usable (soft keyword trigger — a false positive must not kill
    the conversation). The fallback language and the contact-button URL come from
    the resolved runtime settings (app_settings > env > default), so the owner
    tunes them in the admin panel without a redeploy.
    """
    import language  # lazy to keep this module import-light
    import settings
    default = language.default_code()
    code = lang if lang in _MESSAGES else default
    code = code if code in _MESSAGES else "en"
    return {
        "active": True,
        "final": final,
        "message": _MESSAGES[code],
        "button": {
            "label": _BUTTON_LABELS[code],
            "url": settings.general()["contact_form_url"] or "",
        },
    }


def inactive_payload() -> dict:
    return {"active": False}
