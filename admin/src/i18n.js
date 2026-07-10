/**
 * Tiny gettext-style i18n for the admin SPA: English source strings ARE the
 * keys, so components keep readable literals and just wrap them in t().
 * The language persists in localStorage; switching reloads the app (same
 * pattern as the Partner → Product scope switcher) so no context plumbing is
 * needed and every module-level string re-resolves.
 *
 * The admin CONTENT (prompts, KB, variable values) stays English-only — the
 * backend enforces it (settings.ensure_english). Only the admin UI chrome is
 * bilingual.
 */
const LANG_KEY = 'admin_lang';

export const getAdminLang = () => {
  try {
    return localStorage.getItem(LANG_KEY) === 'ru' ? 'ru' : 'en';
  } catch {
    return 'en';
  }
};

export const setAdminLang = (lang) => {
  try {
    localStorage.setItem(LANG_KEY, lang === 'ru' ? 'ru' : 'en');
  } catch {
    /* private mode — the switch just won't persist */
  }
  window.location.reload();
};

const RU = {
  // ----- menu / sections -----
  'Dashboard': 'Дашборд',
  'Support chat': 'Чат поддержки',
  'Telegram · Retention': 'Telegram · Ретеншен',
  'System': 'Система',
  'Conversations': 'Диалоги',
  'Escalations': 'Эскалации',
  'Knowledge base': 'База знаний',
  'Site map': 'Карта сайта',
  'Prompt': 'Промпт',
  'Translations': 'Переводы',
  'Analytics': 'Аналитика',
  'Telegram config': 'Настройка Telegram',
  'Retention KB': 'База знаний бота',
  'Media': 'Медиа',
  'Managers': 'Менеджеры',
  'Proactive agent': 'Проактивный агент',
  'Structure': 'Структура',
  'Settings': 'Настройки',
  'Users': 'Пользователи',
  'API keys': 'API-ключи',
  'Chat settings': 'Настройки чата',
  'Bot settings': 'Настройки бота',
  'Core settings': 'Настройки ядра',

  // ----- common chrome -----
  'Loading…': 'Загрузка…',
  'Save': 'Сохранить',
  'Saving…': 'Сохранение…',
  'Add': 'Добавить',
  'How it works': 'Как это работает',
  'Preview': 'Просмотр',
  'Prompt variables': 'Переменные промпта',
  'Prompt preview': 'Просмотр промпта',
  'Setup guide': 'Инструкция по запуску',

  // ----- language switcher -----
  'Admin language': 'Язык админки',

  // ----- settings: module titles / descriptions -----
  'Support chat settings': 'Настройки чата поддержки',
  'Retention bot settings': 'Настройки ретеншен-бота',
  'System settings (core)': 'Системные настройки (ядро)',
  'Anti-spam and chat limits for the support widget. These knobs only affect the on-site support chat.':
    'Антиспам и лимиты чата для виджета поддержки. Эти настройки влияют только на чат поддержки на сайте.',
  'Everything that paces the Telegram retention bot: photos, progression, the proactive agent and its per-player guards.':
    'Всё, что управляет Telegram-ретеншен-ботом: фото, прогрессия, проактивный агент и его защитные лимиты на игрока.',
  'Deploy-wide core: the AI model, supported languages and technical limits shared by both bots.':
    'Ядро сервиса: AI-модель, поддерживаемые языки и технические лимиты, общие для обоих ботов.',

  // ----- settings page chrome -----
  'Settings could not be loaded': 'Не удалось загрузить настройки',
  'Product settings': 'Настройки продукта',
  'Values you save here override the global defaults for this product only.':
    'Сохранённые здесь значения переопределяют глобальные значения только для этого продукта.',
  'Global defaults — the fallback for EVERY product': 'Глобальные значения — фолбэк для КАЖДОГО продукта',
  'No product is selected, so you are editing the deploy-wide fallback layer that applies to every product without its own override. To tune a single casino, pick it in the Partner → Product switcher at the top-right.':
    'Продукт не выбран, поэтому вы редактируете глобальный слой значений, который действует для каждого продукта без собственного переопределения. Чтобы настроить конкретное казино, выберите его в переключателе Партнёр → Продукт справа вверху.',
  'Hot-reloaded runtime settings — effective values shown (precedence product → global → env → default). The backend validates and rejects out-of-range values.':
    'Горячие настройки — показаны действующие значения (приоритет: продукт → глобально → env → по умолчанию). Бэкенд валидирует и отклоняет значения вне диапазона.',
  'for': 'для',
  'GLOBAL defaults': 'ГЛОБАЛЬНЫЕ значения',
  'settings saved': 'настройки сохранены',
  'Save failed': 'Не удалось сохранить',
  'Load failed': 'Не удалось загрузить',

  // ----- group labels / help -----
  'Anti-spam': 'Антиспам',
  'AI model': 'AI-модель',
  'General': 'Общие',
  'Chat limits': 'Лимиты чата',
  'Retention bot': 'Ретеншен-бот',
  'Languages': 'Языки',
  'Rate limiting, cooldowns and the injection / low-content guards that run before the model.':
    'Rate-лимиты, кулдауны и защита от инъекций/пустых сообщений — всё срабатывает до вызова модели.',
  'OpenAI request tuning. Edits are hot — the next turn uses them (the client is rebuilt on save).':
    'Тюнинг запросов к OpenAI. Изменения применяются сразу — следующий ответ уже использует их.',
  'Operational limits with no other home: session/token lifetimes, the message cap, prompt-history window and the request body cap.':
    'Технические лимиты: время жизни сессий и токенов, лимит сообщений, окно истории для модели и максимальный размер запроса.',
  'Telegram retention-bot pacing: photo caps, cooldowns, the photo-unlock progression and profile freshness.':
    'Темп ретеншен-бота в Telegram: лимиты фото, кулдауны, прогрессия «разблокировки» фото и свежесть профиля.',
  'Which languages the assistant supports and the default. Answers follow the player; the widget chrome follows the browser.':
    'Какие языки поддерживает ассистент и язык по умолчанию. Ответы следуют за игроком; интерфейс виджета — за браузером.',

  // ----- settings sections -----
  'Photo unlock progression (Stage × VIP Level)': 'Прогрессия разблокировки фото (Stage × VIP-уровень)',
  'Proactive agent (event-driven)': 'Проактивный агент (по событиям)',
  'Send-frequency guards (per-player protection)': 'Ограничители частоты (защита игрока от спама)',
  'Delivery': 'Доставка сообщений',
  'Subscription gate': 'Проверка подписки',

  // ----- field labels + helps (antispam) -----
  'Rate limit (max / IP)': 'Rate-лимит (макс. / IP)',
  'Maximum requests from one IP within the window (widget/API).':
    'Максимум запросов с одного IP за окно (виджет/API).',
  'Telegram rate limit (max / user)': 'Rate-лимит Telegram (макс. / игрок)',
  'Maximum Telegram messages from one player within the same window — a live chat needs more headroom than the widget.':
    'Максимум сообщений в Telegram от одного игрока за то же окно — живому диалогу нужен больший запас, чем виджету.',
  'Rate-limit window (sec)': 'Окно rate-лимита (сек)',
  'Length of the rate-limit window in seconds.': 'Длина окна rate-лимита в секундах.',
  'Message cooldown (sec)': 'Кулдаун между сообщениями (сек)',
  'Minimum seconds between two messages in one session.': 'Минимум секунд между двумя сообщениями в одной сессии.',
  'Max input characters': 'Макс. длина сообщения (символы)',
  'Longest single message the API accepts.': 'Самое длинное сообщение, которое принимает API.',
  'Hard-block injection attempts': 'Жёстко блокировать инъекции',
  'Reject prompt-injection with HTTP 400 (off = audit only, still answered).':
    'Отклонять prompt-инъекции с HTTP 400 (выкл = только аудит, ответ всё равно даётся).',
  'Block low-content messages': 'Блокировать пустые сообщения',
  'Nudge instead of calling the model on empty/one-character spam.':
    'На пустой/односимвольный спам отвечать подсказкой без вызова модели.',
  'Min meaningful characters': 'Мин. значимых символов',
  'Distinct letters/digits a message must carry to reach the model.':
    'Сколько разных букв/цифр должно быть в сообщении, чтобы дойти до модели.',

  // ----- field labels + helps (model) -----
  'Model id': 'ID модели',
  'OpenAI model, e.g. gpt-5-mini (the GPT-5 mini reasoning family).':
    'Модель OpenAI, напр. gpt-5-mini (семейство reasoning-моделей GPT-5 mini).',
  'Reasoning effort': 'Глубина рассуждений',
  'Hidden-reasoning depth. Empty = the model default (parameter omitted).':
    'Глубина скрытых рассуждений. Пусто = значение модели по умолчанию (параметр не передаётся).',
  'Verbosity': 'Развёрнутость ответов',
  'Answer length. Empty = the model default (parameter omitted).':
    'Длина ответов. Пусто = значение модели по умолчанию (параметр не передаётся).',
  'Max output tokens': 'Макс. токенов на ответ',
  'Output budget — INCLUDES hidden reasoning tokens, so keep it generous (≈2000).':
    'Бюджет ответа — ВКЛЮЧАЕТ скрытые reasoning-токены, держите с запасом (≈2000).',
  'Request timeout (sec)': 'Таймаут запроса (сек)',
  'Per-request timeout before a retry/failover.': 'Таймаут одного запроса до ретрая/failover.',
  'Key-switch timeout (sec)': 'Таймаут переключения ключа (сек)',
  'Silence on the primary key before the fallback key is raced.':
    'Сколько ждать тишины на основном ключе, прежде чем параллельно запустить резервный.',
  'Max attempts / key': 'Макс. попыток / ключ',
  'Retries per key on transient (429/timeout) errors.': 'Ретраи на ключ при временных ошибках (429/таймаут).',
  'Max concurrent / key': 'Макс. параллельных / ключ',
  'Concurrent in-flight requests allowed per API key.': 'Сколько одновременных запросов разрешено на один API-ключ.',

  // ----- field labels + helps (general) -----
  'Session TTL (hours)': 'Время жизни сессии (часы)',
  'How long a chat session stays valid.': 'Сколько времени сессия чата остаётся действительной.',
  'Admin token TTL (min)': 'Время жизни админ-токена (мин)',
  'Admin inactivity window (5 min … 1 week). The session slides: daily use auto-renews it; an account untouched for this long is logged out. Default 1 week (10080).':
    'Окно неактивности админа (5 мин … 1 неделя). Сессия скользящая: ежедневная работа продлевает её; аккаунт без активности разлогинивается. По умолчанию 1 неделя (10080).',
  'Max messages / session': 'Макс. сообщений / сессия',
  'Message cap before the session hands off to a human.': 'Лимит сообщений, после которого сессия передаётся человеку.',
  'History turns to model': 'Ходов истории для модели',
  'Recent turns fed into the prompt history (full transcript is always stored).':
    'Сколько последних ходов попадает в промпт (полная переписка хранится всегда).',
  'Max request body (bytes)': 'Макс. тело запроса (байты)',
  'Largest accepted request body (1 KiB … 100 MiB).': 'Максимальный принимаемый размер тела запроса (1 КиБ … 100 МиБ).',

  // ----- field labels + helps (retention) -----
  'Daily photo cap': 'Лимит фото в день',
  'Max photos sent to one player per day (hard, incl. requested).':
    'Максимум фото одному игроку в день (жёсткий, включая запрошенные).',
  'Proactive photo cooldown (msgs)': 'Кулдаун проактивных фото (сообщений)',
  'Messages between UNPROMPTED photos (a direct ask bypasses it).':
    'Сколько сообщений между фото БЕЗ запроса игрока (прямая просьба обходит кулдаун).',
  'Photo candidate list size': 'Размер списка фото-кандидатов',
  'How many photo candidates the model is offered to choose from.':
    'Из скольких фото-кандидатов модель выбирает.',
  'Stage advance min hours': 'Мин. часов между стадиями',
  'Minimum spacing between explicitness-stage advances.': 'Минимальный интервал между повышениями стадии откровенности.',
  'Deeplink nonce TTL (sec)': 'Время жизни deeplink-нонса (сек)',
  'Lifetime of a one-time deeplink nonce.': 'Время жизни одноразового deeplink-нонса.',
  'Profile pull TTL (sec)': 'Свежесть профиля (сек)',
  'How long a pulled player profile stays fresh before a re-pull.':
    'Сколько времени подтянутый профиль игрока считается свежим до повторного запроса.',
  'Session idle (min)': 'Неактивность чата (мин)',
  'Idle minutes before a Telegram chat closes; the next message starts a fresh chat (0 = never close).':
    'Минут неактивности до закрытия Telegram-чата; следующее сообщение начинает новый чат (0 = не закрывать).',
  'Carry-over context turns': 'Ходов контекста при возврате',
  'Trailing turns of the previous chat shown to the model when a returning player starts a fresh one (0 = off).':
    'Сколько последних ходов прошлого чата модель видит, когда вернувшийся игрок начинает новый (0 = выкл).',
  'Play reminder every N replies': 'Приглашение играть каждые N ответов',
  'Every N-th of Nika’s Telegram replies weaves in a light in-context invitation to play, with a one-tap site button picked from the Site map by intent (0 = off).':
    'Каждый N-й ответ Ники в Telegram содержит лёгкое приглашение поиграть с кнопкой сайта из Карты сайта (0 = выкл).',
  'Silent notifications (proactive)': 'Тихие уведомления (проактивные)',
  'Proactive messages arrive WITHOUT a sound/vibration on the player’s phone (Telegram silent delivery). Replies in a live dialogue always notify normally.':
    'Проактивные сообщения приходят БЕЗ звука/вибрации на телефоне игрока (тихая доставка Telegram). Ответы в живом диалоге всегда приходят со звуком.',
  'Subscription re-check cache (sec)': 'Кэш проверки подписки (сек)',
  'How long a positive channel-subscription check is cached before asking Telegram again (0 = re-check on every message).':
    'Сколько секунд кэшируется положительная проверка подписки на канал до нового запроса к Telegram (0 = проверять каждое сообщение).',
  'Agent enabled': 'Агент включён',
  'The proactive agent for this product: reacts to casino events (deposits, level-ups, losses) with a decision per event. Off = no proactive messages at all (the dialogue bot still answers).':
    'Проактивный агент этого продукта: реагирует на события казино (депозиты, повышения уровня, проигрыши) отдельным решением на событие. Выкл = никаких проактивных сообщений (диалоговый бот продолжает отвечать).',
  'Dry-run (shadow mode)': 'Dry-run (теневой режим)',
  'ON: the agent decides and logs to the Decisions ledger but sends nothing. Turn off only after reviewing decisions.':
    'ВКЛ: агент принимает решения и пишет их в журнал, но ничего не отправляет. Выключайте только после проверки решений.',
  'Show trigger in message': 'Показывать триггер в сообщении',
  'Adds an italic chrome line with the fired trigger ("⚡ Trigger: deposit_confirmed") to every proactive message. Great while testing; turn OFF for production players.':
    'Добавляет курсивную строку с триггером («⚡ Trigger: deposit_confirmed») в каждое проактивное сообщение. Удобно при тестировании; для реальных игроков выключите.',
  'Worker interval (seconds)': 'Интервал воркера (сек)',
  'How often the background worker drains the event queue. Applies live on the next tick (no redeploy). 5s = near-realtime reactions.':
    'Как часто фоновый воркер разбирает очередь событий. Применяется сразу, без редеплоя. 5с = реакции почти в реальном времени.',
  'Events per sweep': 'Событий за проход',
  'Max events one worker sweep processes per product — bounds the burst on Telegram and OpenAI.':
    'Максимум событий за один проход воркера на продукт — ограничивает нагрузку на Telegram и OpenAI.',
  'Daily AI budget (USD)': 'Дневной AI-бюджет (USD)',
  'Hard stop: once the day’s decisions cost this much, the agent goes quiet until tomorrow. 0 = no budget.':
    'Жёсткий стоп: когда решения за день стоят столько, агент замолкает до завтра. 0 = без бюджета.',
  'Max proactive messages per player per day': 'Макс. проактивных сообщений игроку в день',
  'Hard per-player cap: the agent never sends more than this many proactive messages to one player in a day, however many events fire.':
    'Жёсткий лимит на игрока: агент не отправит больше этого числа проактивных сообщений одному игроку в день, сколько бы событий ни произошло.',
  'Min gap between messages (hours)': 'Мин. интервал между сообщениями (часы)',
  'Minimum hours between two proactive messages to the same player (0 = off). Keep it short (1–2h) if you want the agent to react to several events per day.':
    'Минимум часов между двумя проактивными сообщениями одному игроку (0 = выкл). Держите коротким (1–2 ч), если агент должен реагировать на несколько событий в день.',
  'Same-event cooldown (hours)': 'Кулдаун одинаковых событий (часы)',
  'One reaction per event TYPE per player per window — five deposits in an evening get one warm note, not five. Set 0 to disable while testing, so a re-injected simulator event gets a fresh decision instead of a same_event_cooldown block.':
    'Одна реакция на ТИП события на игрока за окно — пять депозитов за вечер получают одно тёплое сообщение, а не пять. Поставьте 0 при тестировании, чтобы повторное событие из симулятора получало новое решение, а не блок same_event_cooldown.',
  'Quiet hours start (0–23)': 'Начало тихих часов (0–23)',
  'Hour when the no-contact window begins (players are not messaged at night).':
    'Час начала окна тишины (ночью игрокам не пишем).',
  'Quiet hours end (0–23)': 'Конец тихих часов (0–23)',
  'Hour when the no-contact window ends and proactive messages may resume.':
    'Час окончания окна тишины — проактивные сообщения снова разрешены.',
  'Quiet hours UTC offset': 'Смещение UTC для тихих часов',
  'Timezone offset the quiet hours (and the prompt’s current-time block) are evaluated in (e.g. 3 = UTC+3).':
    'Часовой пояс, в котором считаются тихие часы (и блок текущего времени в промпте), напр. 3 = UTC+3.',
  'Loss comfort window (hours)': 'Окно поддержки после проигрыша (часы)',
  'After a big-loss signal: no play invitations, no reward photos, empathetic tone only, for this many hours.':
    'После сигнала о крупном проигрыше: без приглашений играть, без фото-наград, только эмпатичный тон — столько часов.',
  'High-loss threshold (USD / 24h)': 'Порог крупного проигрыша (USD / 24ч)',
  'Net loss over 24 hours that marks the player critical and starts the comfort window.':
    'Чистый проигрыш за 24 часа, после которого игрок считается критичным и включается окно поддержки.',
  'Max stage (top explicitness)': 'Макс. стадия (верх откровенности)',
  'The hottest stage that exists. Photos and tier ceilings can never go above it — there is nothing beyond this number.':
    'Самая горячая существующая стадия. Фото и потолки VIP-уровней не могут её превысить — выше этого числа ничего нет.',
  'Messages to reach each stage': 'Сообщений до каждой стадии',
  'How many meaningful messages a player must send to unlock each stage. Stage 1 is free; the more they chat, the hotter the stage they reach (still capped by their VIP tier below).':
    'Сколько значимых сообщений игрок должен написать для разблокировки каждой стадии. Стадия 1 бесплатна; чем больше общения, тем горячее стадия (в пределах потолка VIP-уровня ниже).',
  'VIP tiers (lowest → highest)': 'VIP-уровни (от нижнего к верхнему)',
  'The VIP ladder, one tier per line, from lowest to highest. Order matters: a tier’s position is its Level number a photo can require.':
    'VIP-лестница, по одному уровню на строку, снизу вверх. Порядок важен: позиция уровня — это его номер Level, который может требовать фото.',
  'Stage ceiling per VIP tier (Level → highest Stage)': 'Потолок стадии по VIP-уровню (Level → макс. Stage)',
  'The highest stage each VIP tier is allowed to reach, no matter how much they chat. Higher VIP = hotter photos unlocked.':
    'Максимальная стадия для каждого VIP-уровня, сколько бы игрок ни общался. Выше VIP = горячее доступные фото.',

  // ----- language editor -----
  'Default answer language': 'Язык ответов по умолчанию',
  "Fallback when the player's language can't be detected.": 'Фолбэк, когда язык игрока не определяется.',
  'Supported languages': 'Поддерживаемые языки',
  'A language added here starts on English copy and becomes translatable in the Translations tab. Edit the display name (optional) to override the built-in name.':
    'Добавленный язык стартует с английскими текстами и переводится во вкладке «Переводы». Отображаемое имя (опционально) переопределяет встроенное.',
  'Add a language': 'Добавить язык',
  'ISO 639-1 language': 'Язык ISO 639-1',
  'Keep at least one supported language': 'Оставьте хотя бы один поддерживаемый язык',
  'Languages saved': 'Языки сохранены',
  'default': 'по умолчанию',

  // ----- how-it-works blocks -----
  'What lives here': 'Что здесь находится',
  'Support module — how it works': 'Модуль поддержки — как это работает',
  'Retention module — how it works': 'Модуль ретеншена — как это работает',
  'Core — how it works': 'Ядро — как это работает',
  'The support widget answers players on the site from the per-topic Knowledge base. Before the model sees a message it passes the anti-spam gates below; the chat limits bound one session. Content (KB texts, prompt variables, translations) is edited in the Support chat section of the menu.':
    'Виджет поддержки отвечает игрокам на сайте по Базе знаний (по темам). До модели сообщение проходит антиспам-фильтры ниже; лимиты чата ограничивают одну сессию. Контент (тексты БЗ, переменные промпта, переводы) редактируется в разделе меню «Чат поддержки».',
  'The Telegram bot re-engages players: it chats in persona, sends photos gated by Stage × VIP level, and the proactive agent reacts to casino events (deposits, level-ups, losses). The guards below are the dials for how often one player may be written to — the agent can never exceed them.':
    'Telegram-бот возвращает игроков: общается в персонаже, отправляет фото по стадиям и VIP-уровню, а проактивный агент реагирует на события казино (депозиты, повышения уровня, проигрыши). Ограничители ниже — регуляторы того, как часто можно писать одному игроку: агент не может их превысить.',
  'These settings are shared by BOTH bots: which OpenAI model answers (and its budgets/timeouts), which languages the assistant supports, and technical request limits. Change with care — they apply to every product without its own override.':
    'Эти настройки общие для ОБОИХ ботов: какая модель OpenAI отвечает (и её бюджеты/таймауты), какие языки поддерживает ассистент и технические лимиты запросов. Меняйте осторожно — они действуют для каждого продукта без собственного переопределения.',

  // ----- retention photo-gate explainer -----
  'How photos unlock — two gates, both must pass': 'Как открываются фото — два условия, нужны оба',
  'A photo carries a Stage (how explicit/hot it is, 1 = softest) and a Level (the minimum VIP tier that may see it). To receive a photo a player needs BOTH: enough chatting to reach that Stage (the thresholds below) AND a high enough VIP tier — the tier caps the top Stage they can ever reach and clears the photo’s Level. Set the same numbers on each photo in Media.':
    'У фото есть Stage (насколько оно откровенное, 1 = самое мягкое) и Level (минимальный VIP-уровень, которому оно доступно). Чтобы получить фото, игроку нужно ОБА условия: достаточно общения для этой стадии (пороги ниже) И достаточный VIP-уровень — уровень ограничивает максимальную стадию и открывает Level фото. Те же значения проставляются на каждом фото во вкладке «Медиа».',

  // ----- TextStats -----
  'characters': 'символов',
  'tokens (approx.)': 'токенов (примерно)',
  'uncached input': 'некэшированный ввод',
  'per prompt': 'за один ввод',

  // ----- prompt / KB pages -----
  'English only': 'Только английский',
  'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.':
    'Контент для модели должен быть на английском — бэкенд отклонит другие алфавиты. Тексты для игроков живут в «Переводах».',
};

const current = getAdminLang();

/** Translate an English source string; falls back to the source. */
export const t = (s) => (current === 'ru' && RU[s]) || s;

export default t;
