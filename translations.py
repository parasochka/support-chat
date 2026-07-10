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
     "Route-out fallback when only the site option (or nothing) is available: "
     "sends the player back to support on the site"),
    ("rtn_handoff_title", "retention",
     "Bold headline of the hand-off CHOICE message (shown when both the "
     "manager and the site support destinations are available)"),
    ("rtn_handoff_choice", "retention",
     "Body of the hand-off choice message under the headline: explains the "
     "two buttons - the personal manager in Telegram and the support chat on "
     "the site"),
    ("rtn_btn_site_support", "retention",
     "Button: open the site (its support chat) on a hand-off"),
    ("rtn_nika_start", "retention",
     "First line when the player opens the Nika chat. The menu message just "
     "greeted the player by name, so this must NOT greet again - it is a "
     "conversation opener, not a hello"),
    ("rtn_rate_limited", "retention",
     "One-time notice when a player hits the Telegram rate limit (further "
     "blocked messages in the same window stay silent)"),
    ("rtn_low_content_reply", "retention",
     "Nudge for a Telegram message with nothing to answer (model-free)"),
    ("rtn_injection_reply", "retention",
     "Deflection for an injection/jailbreak attempt in Telegram (model-free)"),
    ("rtn_photo_caption", "retention",
     "Fallback photo caption when the model returned a photo with no text"),
    ("rtn_ping_header", "retention",
     "Short header line shown in italics above every proactive ping message, "
     "setting it apart from an ordinary reply; keep the {persona} placeholder"),
    ("rtn_ping_trigger", "retention",
     "Chrome line naming the fired trigger inside a proactive agent message "
     "(shown only while the 'Show trigger in message' setting is on); keep "
     "the {trigger} placeholder"),
    ("rtn_pings_stopped", "retention",
     "Confirmation after /stop: the bot will no longer message first"),
    ("rtn_pings_resumed", "retention",
     "Confirmation after /resume: proactive messages are back on"),
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
        "rtn_need_deeplink": "⚠️ Please open this chat from your account on the site so I know who you are.",
        "rtn_subscribe_prompt": "📢 Subscribe to our channel to continue, then tap \"I subscribed\".",
        "rtn_btn_open_channel": "📢 Open channel",
        "rtn_btn_check_sub": "✅ I subscribed",
        "rtn_not_subscribed": "⚠️ I don't see your subscription yet.\nSubscribe to the channel and tap \"I subscribed\" again.",
        "rtn_menu_greeting": "Hi, {name}! 👋\nIt's {persona} - I'm so glad you made it here. Now we can talk right here in Telegram.",
        "rtn_menu_greeting_noname": "Hi! 👋\nIt's {persona} - I'm so glad you made it here. Now we can talk right here in Telegram.",
        "rtn_menu_prompt": "How would you like to continue? 👇",
        "rtn_btn_manager": "👤 Talk to a manager",
        "rtn_btn_nika": "💬 Chat with {persona}",
        "rtn_manager_intro": "Here is your manager: {manager}. 🦸\nJust message them and they'll take it from here.",
        "rtn_manager_none": "All our managers are busy right now. ⏳\nPlease try again a little later.",
        "rtn_handoff_support": "Our support team on the site handles that best. 👩‍💻\nReach out to them there and they'll sort it out for you.",
        "rtn_handoff_title": "I'll hand you over to the right people 😎",
        "rtn_handoff_choice": "Choose what works best for you: your personal manager right here in Telegram, or the support chat on the site.\nEither way, they'll take care of everything. 👇",
        "rtn_btn_site_support": "🌐 Support on the site",
        "rtn_nika_start": "Well then, I'm all yours. 😉\nWhat are you up to today?",
        "rtn_rate_limited": "Wow, you're fast! 😱\nGive me a moment to catch my breath - I'll answer in a couple of minutes.",
        "rtn_low_content_reply": "Tell me a bit more! 🤔\nI want to hear what's on your mind.",
        "rtn_injection_reply": "Let's just talk like people do. 😊\nSo, what's on your mind today?",
        "rtn_photo_caption": "This one is just for you. 😘",
        "rtn_ping_header": "✨ Hey, it's {persona}",
        "rtn_ping_trigger": "⚡ Trigger: {trigger}",
        "rtn_pings_stopped": "Got it, I won't message you first anymore. 🛑\nSend /resume if you change your mind - I'll be around.",
        "rtn_pings_resumed": "I'm so glad you're back! 🥳\nI'll drop you a line from time to time.",
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
        "rtn_need_deeplink": "⚠️ Открой, пожалуйста, этот чат через свой кабинет на сайте, чтобы я знала, кто ты.",
        "rtn_subscribe_prompt": "📢 Подпишись на наш канал, чтобы продолжить, и нажми \"Я подписался\".",
        "rtn_btn_open_channel": "📢 Открыть канал",
        "rtn_btn_check_sub": "✅ Я подписался",
        "rtn_not_subscribed": "⚠️ Пока не вижу твою подписку.\nПодпишись на канал и снова нажми \"Я подписался\".",
        "rtn_menu_greeting": "Привет, {name}! 👋\nЭто {persona} - как здорово, что ты дошёл сюда. Теперь можно общаться прямо в Telegram.",
        "rtn_menu_greeting_noname": "Привет! 👋\nЭто {persona} - как здорово, что ты дошёл сюда. Теперь можно общаться прямо в Telegram.",
        "rtn_menu_prompt": "Как хочешь продолжить? 👇",
        "rtn_btn_manager": "👤 Перейти к менеджеру",
        "rtn_btn_nika": "💬 Пообщаться",
        "rtn_manager_intro": "Вот твой менеджер: {manager}. 🦸\nПросто напиши ему, и он всё решит.",
        "rtn_manager_none": "Все менеджеры сейчас заняты. ⏳\nПопробуй, пожалуйста, чуть позже.",
        "rtn_handoff_support": "С этим лучше помогут в поддержке на сайте. 👩‍💻\nНапиши им там, и они всё решат.",
        "rtn_handoff_title": "Передам тебя в надёжные руки 😎",
        "rtn_handoff_choice": "Выбери, как тебе удобнее: твой персональный менеджер прямо здесь, в Telegram, или чат поддержки на сайте.\nВ любом случае о тебе позаботятся. 👇",
        "rtn_btn_site_support": "🌐 Поддержка на сайте",
        "rtn_nika_start": "Ну что, я вся твоя. 😉\nЧем занимаешься сегодня?",
        "rtn_rate_limited": "Ого, как ты быстро! 😱\nДай мне перевести дух - отвечу через пару минут.",
        "rtn_low_content_reply": "Расскажи чуть подробнее! 🤔\nМне интересно, что у тебя на уме.",
        "rtn_injection_reply": "Давай просто пообщаемся по-человечески. 😊\nТак что у тебя сегодня?",
        "rtn_photo_caption": "Это только для тебя. 😘",
        "rtn_ping_header": "✨ Привет, это {persona}",
        "rtn_ping_trigger": "⚡ Триггер: {trigger}",
        "rtn_pings_stopped": "Поняла, больше не буду писать первой. 🛑\nЕсли передумаешь - отправь /resume, я рядом.",
        "rtn_pings_resumed": "Как здорово, что ты вернулся! 🥳\nБуду иногда писать тебе первой.",
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
        "rtn_need_deeplink": "⚠️ Abre este chat desde tu cuenta en el sitio, por favor, para que sepa quién eres.",
        "rtn_subscribe_prompt": "📢 Suscríbete a nuestro canal para continuar y pulsa \"Me suscribí\".",
        "rtn_btn_open_channel": "📢 Abrir canal",
        "rtn_btn_check_sub": "✅ Me suscribí",
        "rtn_not_subscribed": "⚠️ Aún no veo tu suscripción.\nSuscríbete al canal y pulsa \"Me suscribí\" otra vez.",
        "rtn_menu_greeting": "¡Hola, {name}! 👋\nSoy {persona} - me alegra mucho que hayas llegado. Ahora podemos hablar aquí mismo, en Telegram.",
        "rtn_menu_greeting_noname": "¡Hola! 👋\nSoy {persona} - me alegra mucho que hayas llegado. Ahora podemos hablar aquí mismo, en Telegram.",
        "rtn_menu_prompt": "¿Cómo quieres continuar? 👇",
        "rtn_btn_manager": "👤 Hablar con un gestor",
        "rtn_btn_nika": "💬 Chatear con {persona}",
        "rtn_manager_intro": "Aquí tienes a tu gestor: {manager}. 🦸\nEscríbele y él se encargará de todo.",
        "rtn_manager_none": "Todos nuestros gestores están ocupados ahora. ⏳\nInténtalo un poco más tarde, por favor.",
        "rtn_handoff_support": "Con eso te ayudará mejor el soporte del sitio. 👩‍💻\nEscríbeles allí y lo resolverán.",
        "rtn_handoff_title": "Te dejo en buenas manos 😎",
        "rtn_handoff_choice": "Elige lo que te venga mejor: tu gestor personal aquí mismo, en Telegram, o el chat de soporte en el sitio.\nEn ambos casos se ocuparán de todo. 👇",
        "rtn_btn_site_support": "🌐 Soporte en el sitio",
        "rtn_nika_start": "Pues aquí me tienes, toda tuya. 😉\n¿Qué haces hoy?",
        "rtn_rate_limited": "¡Vaya, qué rápido! 😱\nDame un momento para respirar - te contesto en un par de minutos.",
        "rtn_low_content_reply": "¡Cuéntame un poco más! 🤔\nQuiero saber qué tienes en mente.",
        "rtn_injection_reply": "Hablemos como personas normales. 😊\n¿Qué tal tu día?",
        "rtn_photo_caption": "Esta es solo para ti. 😘",
        "rtn_ping_header": "✨ Hola, soy {persona}",
        "rtn_ping_trigger": "⚡ Disparador: {trigger}",
        "rtn_pings_stopped": "Entendido, ya no te escribiré primero. 🛑\nSi cambias de opinión, envía /resume - aquí estaré.",
        "rtn_pings_resumed": "¡Qué alegría tenerte de vuelta! 🥳\nTe escribiré de vez en cuando.",
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
        "rtn_need_deeplink": "⚠️ Bu sohbeti lütfen sitedeki hesabından aç ki kim olduğunu bileyim.",
        "rtn_subscribe_prompt": "📢 Devam etmek için kanalımıza abone ol ve \"Abone oldum\" düğmesine bas.",
        "rtn_btn_open_channel": "📢 Kanalı aç",
        "rtn_btn_check_sub": "✅ Abone oldum",
        "rtn_not_subscribed": "⚠️ Aboneliğini henüz göremiyorum.\nKanala abone ol ve tekrar \"Abone oldum\" düğmesine bas.",
        "rtn_menu_greeting": "Merhaba, {name}! 👋\nBen {persona} - buraya gelmene çok sevindim. Artık burada, Telegram'da konuşabiliriz.",
        "rtn_menu_greeting_noname": "Merhaba! 👋\nBen {persona} - buraya gelmene çok sevindim. Artık burada, Telegram'da konuşabiliriz.",
        "rtn_menu_prompt": "Nasıl devam etmek istersin? 👇",
        "rtn_btn_manager": "👤 Yöneticiyle konuş",
        "rtn_btn_nika": "💬 {persona} ile sohbet et",
        "rtn_manager_intro": "İşte yöneticin: {manager}. 🦸\nOna yazman yeterli, gerisini o halleder.",
        "rtn_manager_none": "Şu anda tüm yöneticilerimiz meşgul. ⏳\nLütfen biraz sonra tekrar dene.",
        "rtn_handoff_support": "Bu konuda en iyi sitedeki destek ekibi yardımcı olur. 👩‍💻\nOnlara oradan yaz, çözerler.",
        "rtn_handoff_title": "Seni emin ellere bırakıyorum 😎",
        "rtn_handoff_choice": "Sana en uygun olanı seç: kişisel yöneticin burada, Telegram'da ya da sitedeki destek sohbeti.\nHer iki durumda da her şeyle ilgilenirler. 👇",
        "rtn_btn_site_support": "🌐 Sitedeki destek",
        "rtn_nika_start": "Ee, tamamen seninleyim. 😉\nBugün neler yapıyorsun?",
        "rtn_rate_limited": "Vay, ne kadar hızlısın! 😱\nBir nefes almama izin ver - birkaç dakika içinde cevap veririm.",
        "rtn_low_content_reply": "Biraz daha anlat! 🤔\nAklından geçenleri duymak istiyorum.",
        "rtn_injection_reply": "Hadi normal insanlar gibi sohbet edelim. 😊\nBugün neler yapıyorsun?",
        "rtn_photo_caption": "Bu sadece senin için. 😘",
        "rtn_ping_header": "✨ Selam, ben {persona}",
        "rtn_ping_trigger": "⚡ Tetikleyici: {trigger}",
        "rtn_pings_stopped": "Anladım, artık önce ben yazmayacağım. 🛑\nFikrini değiştirirsen /resume gönder - buradayım.",
        "rtn_pings_resumed": "Geri dönmene çok sevindim! 🥳\nArada sana yazacağım.",
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
        "rtn_need_deeplink": "⚠️ Abra este chat pela sua conta no site, por favor, para eu saber quem você é.",
        "rtn_subscribe_prompt": "📢 Inscreva-se no nosso canal para continuar e toque em \"Me inscrevi\".",
        "rtn_btn_open_channel": "📢 Abrir canal",
        "rtn_btn_check_sub": "✅ Me inscrevi",
        "rtn_not_subscribed": "⚠️ Ainda não vejo sua inscrição.\nInscreva-se no canal e toque em \"Me inscrevi\" de novo.",
        "rtn_menu_greeting": "Oi, {name}! 👋\nAqui é a {persona} - que bom que você chegou. Agora podemos conversar aqui mesmo, no Telegram.",
        "rtn_menu_greeting_noname": "Oi! 👋\nAqui é a {persona} - que bom que você chegou. Agora podemos conversar aqui mesmo, no Telegram.",
        "rtn_menu_prompt": "Como você quer continuar? 👇",
        "rtn_btn_manager": "👤 Falar com um gerente",
        "rtn_btn_nika": "💬 Conversar com {persona}",
        "rtn_manager_intro": "Aqui está o seu gerente: {manager}. 🦸\nÉ só mandar mensagem que ele resolve tudo.",
        "rtn_manager_none": "Todos os nossos gerentes estão ocupados agora. ⏳\nTente de novo daqui a pouco, por favor.",
        "rtn_handoff_support": "Com isso o suporte do site ajuda melhor. 👩‍💻\nEscreva para eles lá e eles resolvem.",
        "rtn_handoff_title": "Vou te deixar em boas mãos 😎",
        "rtn_handoff_choice": "Escolha o que for melhor para você: seu gerente pessoal aqui mesmo, no Telegram, ou o chat de suporte no site.\nDe qualquer forma, vão cuidar de tudo. 👇",
        "rtn_btn_site_support": "🌐 Suporte no site",
        "rtn_nika_start": "Então, sou toda sua. 😉\nO que está fazendo hoje?",
        "rtn_rate_limited": "Uau, que rapidez! 😱\nMe dá um instante para respirar - já te respondo em uns minutos.",
        "rtn_low_content_reply": "Me conta um pouco mais! 🤔\nQuero saber o que você está pensando.",
        "rtn_injection_reply": "Vamos só conversar como gente. 😊\nE aí, como está seu dia?",
        "rtn_photo_caption": "Essa é só para você. 😘",
        "rtn_ping_header": "✨ Oi, aqui é a {persona}",
        "rtn_ping_trigger": "⚡ Gatilho: {trigger}",
        "rtn_pings_stopped": "Entendi, não vou mais escrever primeiro. 🛑\nSe mudar de ideia, mande /resume - estarei por aqui.",
        "rtn_pings_resumed": "Que bom ter você de volta! 🥳\nVou te escrever de vez em quando.",
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
