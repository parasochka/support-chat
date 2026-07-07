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
    ("contact_url", "server",
     "Escalation contact-button URL for this language (http(s) link). Empty = "
     "no button link for this product; only the boot-seeded default product "
     "then falls back to the deploy-level CONTACT_FORM_URL env default"),
    ("closing_suggestion", "server", "The declarative closing bubble (\"Issue solved.\")"),
    ("low_content_reply", "server", "Nudge for a message with nothing to answer"),
    ("model_error_reply", "server", "Nudge shown on a transient model failure"),
    # --- Retention / Telegram bot copy (scope 'retention') ------------------
    ("rtn_need_deeplink", "retention",
     "Shown when someone opens the bot without a valid site deeplink"),
    ("rtn_subscribe_prompt", "retention",
     "Channel-subscription gate message shown before any menu"),
    ("rtn_btn_open_channel", "retention", "Button: open the channel"),
    ("rtn_btn_check_sub", "retention", "Button: I subscribed / re-check"),
    ("rtn_not_subscribed", "retention", "Shown when the re-check still finds no subscription"),
    ("rtn_menu_greeting", "retention",
     "Persona greeting above the entry menu when the player's name is known; "
     "keep the {persona} and {name} placeholders"),
    ("rtn_menu_greeting_noname", "retention",
     "Persona greeting above the entry menu when no name is known; keep the "
     "{persona} placeholder"),
    ("rtn_menu_prompt", "retention", "Menu heading shown after the subscription gate"),
    ("rtn_btn_manager", "retention", "Button: go to a manager (escalation entry only)"),
    ("rtn_btn_nika", "retention", "Button: chat with Nika"),
    ("rtn_manager_intro", "retention",
     "Message before the manager link; keep the {manager} placeholder"),
    ("rtn_manager_none", "retention", "Shown when no manager is available in the pool"),
    ("rtn_handoff_support", "retention",
     "Route-out on a retention-entry chat (send the player back to site support)"),
    ("rtn_nika_start", "retention", "First line when the player opens the Nika chat"),
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
        # No built-in URL: an empty resolution makes escalation.build_payload
        # fall back to the deploy-level default (CONTACT_FORM_URL / the legacy
        # general.contact_form_url override) — for the DEFAULT product only;
        # other products must set their own contact_url in the admin. Only "en"
        # carries the key — other languages resolve through the
        # default-language/English chain.
        "contact_url": "",
        "closing_suggestion": "Issue solved.",
        "low_content_reply": "Could you describe your question in a sentence or two? I didn't catch a question I can help with.",
        "model_error_reply": "Sorry, I'm having a brief technical hiccup. Please send your message again in a moment.",
        "rtn_need_deeplink": "Please open this chat from your account on the site so I know who you are.",
        "rtn_subscribe_prompt": "Subscribe to our channel to continue, then tap \"I subscribed\".",
        "rtn_btn_open_channel": "📢 Open channel",
        "rtn_btn_check_sub": "✅ I subscribed",
        "rtn_not_subscribed": "I don't see your subscription yet. Subscribe to the channel and tap \"I subscribed\" again.",
        "rtn_menu_greeting": "Hi, {name}! It's {persona} - I'm so glad you made it here. Now we can talk right in Telegram.",
        "rtn_menu_greeting_noname": "Hi! It's {persona} - I'm so glad you made it here. Now we can talk right in Telegram.",
        "rtn_menu_prompt": "How would you like to continue?",
        "rtn_btn_manager": "👤 Talk to a manager",
        "rtn_btn_nika": "💬 Chat with {persona}",
        "rtn_manager_intro": "Here is your manager: {manager}. Just message them and they'll take it from here.",
        "rtn_manager_none": "All our managers are busy right now. Please try again a little later.",
        "rtn_handoff_support": "That's something our support team on the site handles best - please reach out to them there and they'll sort it out for you.",
        "rtn_nika_start": "Hey! I'm so glad you're here. What are you up to today?",
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
        "rtn_need_deeplink": "Открой, пожалуйста, этот чат через свой кабинет на сайте, чтобы я знала, кто ты.",
        "rtn_subscribe_prompt": "Подпишись на наш канал, чтобы продолжить, и нажми \"Я подписался\".",
        "rtn_btn_open_channel": "📢 Открыть канал",
        "rtn_btn_check_sub": "✅ Я подписался",
        "rtn_not_subscribed": "Пока не вижу твою подписку. Подпишись на канал и снова нажми \"Я подписался\".",
        "rtn_menu_greeting": "Привет, {name}! Это {persona} - как здорово, что ты дошёл сюда. Теперь можно общаться прямо в Telegram.",
        "rtn_menu_greeting_noname": "Привет! Это {persona} - как здорово, что ты дошёл сюда. Теперь можно общаться прямо в Telegram.",
        "rtn_menu_prompt": "Как хочешь продолжить?",
        "rtn_btn_manager": "👤 Перейти к менеджеру",
        "rtn_btn_nika": "💬 Пообщаться с {persona}",
        "rtn_manager_intro": "Вот твой менеджер: {manager}. Просто напиши ему, и он всё решит.",
        "rtn_manager_none": "Все менеджеры сейчас заняты. Попробуй, пожалуйста, чуть позже.",
        "rtn_handoff_support": "С этим лучше помогут в поддержке на сайте - напиши им там, и они всё решат.",
        "rtn_nika_start": "Привет! Как здорово, что ты заглянул. Чем занимаешься сегодня?",
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
        "rtn_need_deeplink": "Abre este chat desde tu cuenta en el sitio, por favor, para que sepa quién eres.",
        "rtn_subscribe_prompt": "Suscríbete a nuestro canal para continuar y pulsa \"Me suscribí\".",
        "rtn_btn_open_channel": "📢 Abrir canal",
        "rtn_btn_check_sub": "✅ Me suscribí",
        "rtn_not_subscribed": "Aún no veo tu suscripción. Suscríbete al canal y pulsa \"Me suscribí\" otra vez.",
        "rtn_menu_greeting": "¡Hola, {name}! Soy {persona} - me alegra mucho que hayas llegado. Ahora podemos hablar aquí, en Telegram.",
        "rtn_menu_greeting_noname": "¡Hola! Soy {persona} - me alegra mucho que hayas llegado. Ahora podemos hablar aquí, en Telegram.",
        "rtn_menu_prompt": "¿Cómo quieres continuar?",
        "rtn_btn_manager": "👤 Hablar con un gestor",
        "rtn_btn_nika": "💬 Chatear con {persona}",
        "rtn_manager_intro": "Aquí tienes a tu gestor: {manager}. Escríbele y él se encargará de todo.",
        "rtn_manager_none": "Todos nuestros gestores están ocupados ahora. Inténtalo un poco más tarde, por favor.",
        "rtn_handoff_support": "Con eso te ayudará mejor el soporte del sitio - escríbeles allí y lo resolverán.",
        "rtn_nika_start": "¡Hola! Qué bien que estés aquí. ¿Qué haces hoy?",
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
        "rtn_need_deeplink": "Bu sohbeti lütfen sitedeki hesabından aç ki kim olduğunu bileyim.",
        "rtn_subscribe_prompt": "Devam etmek için kanalımıza abone ol ve \"Abone oldum\" düğmesine bas.",
        "rtn_btn_open_channel": "📢 Kanalı aç",
        "rtn_btn_check_sub": "✅ Abone oldum",
        "rtn_not_subscribed": "Aboneliğini henüz göremiyorum. Kanala abone ol ve tekrar \"Abone oldum\" düğmesine bas.",
        "rtn_menu_greeting": "Merhaba, {name}! Ben {persona} - buraya gelmene çok sevindim. Artık Telegram'da konuşabiliriz.",
        "rtn_menu_greeting_noname": "Merhaba! Ben {persona} - buraya gelmene çok sevindim. Artık Telegram'da konuşabiliriz.",
        "rtn_menu_prompt": "Nasıl devam etmek istersin?",
        "rtn_btn_manager": "👤 Yöneticiyle konuş",
        "rtn_btn_nika": "💬 {persona} ile sohbet et",
        "rtn_manager_intro": "İşte yöneticin: {manager}. Ona yazman yeterli, gerisini o halleder.",
        "rtn_manager_none": "Şu anda tüm yöneticilerimiz meşgul. Lütfen biraz sonra tekrar dene.",
        "rtn_handoff_support": "Bu konuda en iyi sitedeki destek ekibi yardımcı olur - onlara oradan yaz, çözerler.",
        "rtn_nika_start": "Merhaba! Burada olmana çok sevindim. Bugün neler yapıyorsun?",
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
        "rtn_need_deeplink": "Abra este chat pela sua conta no site, por favor, para eu saber quem você é.",
        "rtn_subscribe_prompt": "Inscreva-se no nosso canal para continuar e toque em \"Me inscrevi\".",
        "rtn_btn_open_channel": "📢 Abrir canal",
        "rtn_btn_check_sub": "✅ Me inscrevi",
        "rtn_not_subscribed": "Ainda não vejo sua inscrição. Inscreva-se no canal e toque em \"Me inscrevi\" de novo.",
        "rtn_menu_greeting": "Oi, {name}! Aqui é a {persona} - que bom que você chegou. Agora podemos conversar direto no Telegram.",
        "rtn_menu_greeting_noname": "Oi! Aqui é a {persona} - que bom que você chegou. Agora podemos conversar direto no Telegram.",
        "rtn_menu_prompt": "Como você quer continuar?",
        "rtn_btn_manager": "👤 Falar com um gerente",
        "rtn_btn_nika": "💬 Conversar com {persona}",
        "rtn_manager_intro": "Aqui está o seu gerente: {manager}. É só mandar mensagem que ele resolve tudo.",
        "rtn_manager_none": "Todos os nossos gerentes estão ocupados agora. Tente de novo daqui a pouco, por favor.",
        "rtn_handoff_support": "Com isso o suporte do site ajuda melhor - escreva para eles lá e eles resolvem.",
        "rtn_nika_start": "Oi! Que bom ter você aqui. O que está fazendo hoje?",
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
