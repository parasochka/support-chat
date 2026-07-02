"""User-facing copy registry — every string the player sees, per language.

One place for ALL localized user-facing copy: the widget chrome (header title,
topic-picker heading, the canned greeting, buttons, error notes) AND the
server-generated turns (the escalation card, the closing "Issue solved." bubble,
the low-content nudge, the model-error nudge). The built-in defaults below are
the shipped copy; the admin Translations tab stores per-language overrides in
app_settings (settings.translations()), resolved here with the chain
override[lang] > default[lang] > override[default language] > default[default
language] > English. That also makes a language ADDED from the admin panel
fully translatable: it starts on English copy and the owner fills in overrides.

The widget keeps its own baked-in copy of the "widget"-scope strings (widget.js
I18N) so the first paint never waits on the network; it then fetches
GET /api/chat/i18n and merges these resolved strings over it.

House style (matches Nika's formatting rules): straight quotes only, no
guillemets, no em dashes.
"""
from __future__ import annotations

from typing import Any, Optional

# Registry of every localizable copy key: (key, scope, admin-facing description).
# scope "widget" = rendered client-side (served to the widget via /api/chat/i18n);
# scope "server" = used server-side when building a turn/payload. Keys are the
# widget's own names (camelCase where it already used them) so the client merge
# is a plain per-language object spread.
KEYS: tuple[tuple[str, str, str], ...] = (
    ("support", "widget", "Widget header title"),
    ("topics", "widget", "Topic picker heading"),
    ("other", "widget", "The always-available \"Other\" topic button"),
    ("back", "widget", "Back-to-topics button label (accessibility)"),
    ("greeting", "widget", "Canned first bubble shown when a topic is picked"),
    ("placeholder", "widget", "Message input placeholder"),
    ("send", "widget", "Send button"),
    ("launcher", "widget", "Launcher button label (accessibility)"),
    ("startError", "widget", "Error shown when the chat could not start"),
    ("sendError", "widget", "Error shown when sending a message failed"),
    ("switching", "widget", "Topic auto-switch notice; keep the {topic} placeholder"),
    ("switchStuck", "widget", "Fallback when the topic auto-switch loops"),
    ("finish", "widget", "The green \"finish chat\" button"),
    ("finished", "widget", "Note shown after the chat is finished"),
    ("escalation_message", "server", "Escalation card message (hand-off to a human)"),
    ("escalation_button", "server", "Escalation card contact-button label"),
    ("closing_suggestion", "server", "The declarative closing bubble (\"Issue solved.\")"),
    ("low_content_reply", "server", "Nudge for a message with nothing to answer"),
    ("model_error_reply", "server", "Nudge shown on a transient model failure"),
)

# Built-in default copy per language. This is the single shipped source for the
# server-side strings (the modules that used to hold their own dicts now read
# from here); the widget's I18N block mirrors the "widget" scope for an instant,
# network-free first paint.
DEFAULTS: dict[str, dict[str, str]] = {
    "en": {
        "support": "Support",
        "topics": "What can we help you with?",
        "other": "Other",
        "back": "Back to topics",
        "greeting": "Hi, I'm Nika! How can I help you?",
        "placeholder": "Type your message…",
        "send": "Send",
        "launcher": "Open support chat",
        "startError": "Could not start chat. Please try again later.",
        "sendError": "Something went wrong. Please try again.",
        "switching": 'Looks like your question is about "{topic}", switching you there…',
        "switchStuck": "I couldn't settle on the right topic for this question. Please rephrase it in a bit more detail.",
        "finish": "End chat",
        "finished": "Chat ended. Thanks for reaching out!",
        "escalation_message": "I'll connect you with our support team. They can take it from here.",
        "escalation_button": "Contact support",
        "closing_suggestion": "Issue solved.",
        "low_content_reply": "Could you describe your question in a sentence or two? I didn't catch a question I can help with.",
        "model_error_reply": "Sorry, I'm having a brief technical hiccup. Please send your message again in a moment.",
    },
    "ru": {
        "support": "Поддержка",
        "topics": "Чем мы можем помочь?",
        "other": "Другое",
        "back": "К выбору темы",
        "greeting": "Привет, я Ника, чем могу тебе помочь?",
        "placeholder": "Введите сообщение…",
        "send": "Отправить",
        "launcher": "Открыть чат поддержки",
        "startError": "Не удалось начать чат. Попробуйте позже.",
        "sendError": "Что-то пошло не так. Попробуйте ещё раз.",
        "switching": 'Похоже, твой вопрос про "{topic}", переключаю тему…',
        "switchStuck": "Мне не удалось подобрать подходящую тему для этого вопроса. Пожалуйста, переформулируй его чуть подробнее.",
        "finish": "Завершить чат",
        "finished": "Чат завершён. Спасибо за обращение!",
        "escalation_message": "Я передам ваш вопрос в службу поддержки. Они помогут дальше.",
        "escalation_button": "Связаться с поддержкой",
        "closing_suggestion": "Проблема решена.",
        "low_content_reply": "Опиши, пожалуйста, свой вопрос в одном-двух предложениях: я не увидел вопроса, с которым могу помочь.",
        "model_error_reply": "Извини, у меня небольшие технические неполадки. Пожалуйста, отправь сообщение ещё раз через минуту.",
    },
    "es": {
        "support": "Soporte",
        "topics": "¿En qué podemos ayudarte?",
        "other": "Otro",
        "back": "Volver a los temas",
        "greeting": "¡Hola, soy Nika! ¿En qué puedo ayudarte?",
        "placeholder": "Escribe tu mensaje…",
        "send": "Enviar",
        "launcher": "Abrir chat de soporte",
        "startError": "No se pudo iniciar el chat. Inténtalo más tarde.",
        "sendError": "Algo salió mal. Inténtalo de nuevo.",
        "switching": 'Parece que tu pregunta es sobre "{topic}", cambiando de tema…',
        "switchStuck": "No pude encontrar el tema adecuado para esta pregunta. Por favor, reformúlala con un poco más de detalle.",
        "finish": "Finalizar chat",
        "finished": "Chat finalizado. ¡Gracias por contactarnos!",
        "escalation_message": "Te conectaré con nuestro equipo de soporte. Ellos continuarán desde aquí.",
        "escalation_button": "Contactar soporte",
        "closing_suggestion": "Problema resuelto.",
        "low_content_reply": "¿Podrías describir tu pregunta en una o dos frases? No detecté una consulta con la que pueda ayudarte.",
        "model_error_reply": "Perdona, tengo un problema técnico temporal. Por favor, envía tu mensaje de nuevo en un momento.",
    },
    "tr": {
        "support": "Destek",
        "topics": "Size nasıl yardımcı olabiliriz?",
        "other": "Diğer",
        "back": "Konulara dön",
        "greeting": "Merhaba, ben Nika! Sana nasıl yardımcı olabilirim?",
        "placeholder": "Mesajınızı yazın…",
        "send": "Gönder",
        "launcher": "Destek sohbetini aç",
        "startError": "Sohbet başlatılamadı. Lütfen daha sonra tekrar deneyin.",
        "sendError": "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
        "switching": 'Görünüşe göre sorunuz "{topic}" ile ilgili, konuyu değiştiriyorum…',
        "switchStuck": "Bu soru için uygun konuyu bulamadım. Lütfen biraz daha ayrıntılı şekilde yeniden yazar mısın?",
        "finish": "Sohbeti bitir",
        "finished": "Sohbet sona erdi. Bize ulaştığınız için teşekkürler!",
        "escalation_message": "Sizi destek ekibimize bağlayacağım. Buradan itibaren onlar yardımcı olacak.",
        "escalation_button": "Desteğe ulaşın",
        "closing_suggestion": "Sorun çözüldü.",
        "low_content_reply": "Sorununuzu bir iki cümleyle yazar mısınız? Yardımcı olabileceğim bir soru göremedim.",
        "model_error_reply": "Kusura bakma, geçici bir teknik sorun yaşıyorum. Lütfen mesajını birazdan tekrar gönder.",
    },
    "pt": {
        "support": "Suporte",
        "topics": "Como podemos ajudar?",
        "other": "Outro",
        "back": "Voltar aos tópicos",
        "greeting": "Oi, eu sou a Nika! Como posso te ajudar?",
        "placeholder": "Digite sua mensagem…",
        "send": "Enviar",
        "launcher": "Abrir chat de suporte",
        "startError": "Não foi possível iniciar o chat. Tente novamente mais tarde.",
        "sendError": "Algo deu errado. Tente novamente.",
        "switching": 'Parece que sua pergunta é sobre "{topic}", mudando de tópico…',
        "switchStuck": "Não consegui encontrar o tópico certo para essa pergunta. Por favor, reformule com um pouco mais de detalhes.",
        "finish": "Encerrar chat",
        "finished": "Chat encerrado. Obrigado pelo contato!",
        "escalation_message": "Vou conectar você com nossa equipe de suporte. Eles continuarão a partir daqui.",
        "escalation_button": "Falar com o suporte",
        "closing_suggestion": "Problema resolvido.",
        "low_content_reply": "Você poderia descrever sua dúvida em uma ou duas frases? Não identifiquei uma pergunta com a qual eu possa ajudar.",
        "model_error_reply": "Desculpe, estou com um problema técnico temporário. Por favor, envie sua mensagem novamente em instantes.",
    },
}


def _overrides() -> dict[str, Any]:
    import settings  # lazy: keep this module importable standalone
    return settings.translations()


def _lookup(key: str, code: Optional[str], overrides: dict[str, Any]) -> Optional[str]:
    if not code:
        return None
    ov = overrides.get(code)
    if isinstance(ov, dict):
        v = ov.get(key)
        if isinstance(v, str) and v.strip():
            return v
    d = DEFAULTS.get(code) or {}
    v = d.get(key)
    return v if v else None


def text(key: str, lang: Optional[str]) -> str:
    """Resolve one copy string for a language.

    Chain: override[lang] > default[lang] > override[default language] >
    default[default language] > English default. Mirrors the old per-module
    fallbacks (unknown language -> service default -> English) so behaviour is
    unchanged when no overrides are stored.
    """
    import language  # lazy: avoid an import cycle at module load

    overrides = _overrides()
    code = (lang or "").strip().lower() or None
    for candidate in (code, language.default_code(), "en"):
        v = _lookup(key, candidate, overrides)
        if v is not None:
            return v
    return DEFAULTS["en"].get(key, key)


def resolved(codes: list[str], scope: Optional[str] = None) -> dict[str, dict[str, str]]:
    """The fully-resolved copy map for the given languages (optionally one scope)."""
    keys = [k for k, s, _d in KEYS if scope is None or s == scope]
    return {code: {k: text(k, code) for k in keys} for code in codes}


def defaults_for(codes: list[str], scope: Optional[str] = None) -> dict[str, dict[str, str]]:
    """The built-in (no-override) copy for the given languages — what an empty
    field falls back to. Used by the admin editor to show/diff the defaults."""
    keys = [k for k, s, _d in KEYS if scope is None or s == scope]
    no_overrides: dict[str, Any] = {}
    out: dict[str, dict[str, str]] = {}
    for code in codes:
        row: dict[str, str] = {}
        for k in keys:
            v = _lookup(k, code, no_overrides)
            row[k] = v if v is not None else DEFAULTS["en"].get(k, k)
        out[code] = row
    return out


def widget_strings(codes: list[str]) -> dict[str, dict[str, str]]:
    """The widget-scope strings served to the client (GET /api/chat/i18n)."""
    return resolved(codes, scope="widget")
