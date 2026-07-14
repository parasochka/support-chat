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
  // ----- idle pings tab + agent triggers tab -----
  'Idle pings': 'Пинги неактивности',
  'Rules': 'Правила',
  'Add rule': 'Добавить правило',
  'Run now': 'Запустить сейчас',
  'Rule saved': 'Правило сохранено',
  'Rule created': 'Правило создано',
  'Sweep skipped:': 'Проход пропущен:',
  'Sweep done': 'Проход выполнен',
  'considered': 'рассмотрено',
  'sent': 'отправлено',
  'On': 'Вкл',
  'Trigger': 'Триггер',
  'Days': 'Дней',
  'Action': 'Действие',
  'VIP tiers': 'VIP-уровни',
  'Cooldown': 'Кулдаун',
  'Priority': 'Приоритет',
  'all': 'все',
  'Ledger': 'Журнал отправок',
  'When': 'Когда',
  'Player': 'Игрок',
  'Rule': 'Правило',
  'Status': 'Статус',
  'Detail': 'Детали',
  'Cost $': 'Стоимость $',
  'Prev': 'Назад',
  'Next': 'Вперёд',
  'Edit rule': 'Изменить правило',
  'New rule': 'Новое правило',
  'Inactivity days': 'Дней неактивности',
  'Cooldown days': 'Кулдаун (дней)',
  'Enabled': 'Включено',
  'message': 'сообщение',
  'photo': 'фото',
  'Quiet in the bot': 'Молчит в боте',
  'Not playing on the site': 'Не играет на сайте',
  'No deposit': 'Нет депозита',
  'Intent (English hint for the AI)': 'Интент (подсказка для ИИ, на английском)',
  'VIP tiers (comma-separated, empty = all)': 'VIP-уровни (через запятую, пусто = все)',
  'Delete this ping rule? The ledger history stays.': 'Удалить это правило? История в журнале сохранится.',
  'No rules yet — quiet players are not re-engaged until a rule exists.': 'Правил пока нет — без правил молчащих игроков никто не будит.',
  'No proactive sends yet.': 'Проактивных отправок пока нет.',
  'Shown in the rules table and the ledger.': 'Отображается в таблице правил и журнале.',
  'How many quiet days before the rule fires.': 'Сколько дней тишины до срабатывания правила.',
  'Days before the SAME rule may hit the same player again.': 'Через сколько дней это же правило может снова сработать по тому же игроку.',
  'Higher wins when several rules match one player.': 'При нескольких совпадениях побеждает правило с большим приоритетом.',
  "Casino triggers need the partner's Player API / event feed to see logins and deposits.": 'Казино-триггерам нужен Player API / поток событий партнёра, чтобы видеть логины и депозиты.',
  "Photo pings pick from the player's unlocked media (tier × stage gates apply).": 'Фото-пинги берут снимки из доступной игроку медиатеки (гейты уровень × стадия действуют).',
  'Lowercase tier names from Settings → Retention bot → VIP tiers, e.g. gold, platinum.': 'Названия уровней строчными из Настройки → Ретеншен-бот → VIP-уровни, напр. gold, platinum.',
  'What the ping should achieve, e.g. “miss them warmly, tease what’s new, invite them back — no pressure”. English only (it feeds the model prompt).': 'Чего должен добиться пинг, напр. «тепло соскучиться, заинтриговать новинками, позвать обратно — без давления». Только на английском (уходит в промпт модели).',
  'Run now sweeps this product once, ignoring quiet hours (you are explicitly asking); every other guard — caps, gaps, cooldowns, opt-outs, dry-run — still applies.': 'Запуск сейчас делает один проход по продукту, игнорируя тихие часы (вы явно просите); остальные ограничители — капы, интервалы, кулдауны, отписки, dry-run — действуют.',
  'The proactive agent is DISABLED for this product, so idle pings do not fire either (they are part of the agent). Enable it in Settings → Retention bot → «Agent enabled».': 'Проактивный агент для этого продукта ВЫКЛЮЧЕН, поэтому пинги неактивности тоже не срабатывают (они часть агента). Включите его в Настройки → Ретеншен-бот → «Агент включён».',
  'Dry-run (shadow mode) is ON: matched idle rules are logged to the agent’s Decisions ledger but nothing is sent. Turn it off in Settings → Retention bot when ready.': 'Включён dry-run (теневой режим): сработавшие правила пишутся в журнал решений агента, но ничего не отправляется. Отключите его в Настройки → Ретеншен-бот, когда будете готовы.',
  'Idle pings re-engage QUIET players — the inactivity side of the proactive agent (casino events are handled by the [Proactive agent](#/retention-agent) page). Each rule picks WHO (a trigger + inactivity window, optionally narrowed to VIP tiers) and WHAT (a message or a photo, with an English intent hint that grounds what Nika writes). Per-player caps, the minimum gap, quiet hours and the daily AI budget from Settings → Retention bot apply to every send; players opt out with `/stop`.': 'Пинги неактивности будят ЗАМОЛЧАВШИХ игроков — это «сторона тишины» проактивного агента (события казино обрабатывает страница [Проактивный агент](#/retention-agent)). Каждое правило задаёт КОГО (триггер + окно неактивности, опционально сужение по VIP-уровням) и ЧТО (сообщение или фото, с англоязычным интентом, направляющим текст Ники). Персональные капы, минимальный интервал, тихие часы и дневной ИИ-бюджет из Настройки → Ретеншен-бот действуют на каждую отправку; игрок отписывается командой `/stop`.',
  'Every proactive-send attempt (idle rules AND event reactions): who was nudged, by which rule, and what it cost. Skipped rows explain why a candidate was passed over.': 'Каждая попытка проактивной отправки (правила неактивности И реакции на события): кого тронули, каким правилом и сколько это стоило. Пропущенные строки объясняют, почему кандидата обошли.',
  'Triggers': 'Триггеры',
  'Triggers saved': 'Триггеры сохранены',
  'Save triggers': 'Сохранить триггеры',
  'Reset to defaults': 'Сбросить к дефолтным',
  'off by default': 'по умолчанию выкл',
  'Events with the switch ON wake the agent: each one goes through the guards and gets a row in the Decisions ledger (message / photo / silence). Events with the switch OFF are "state food": they still update the player state (activity timestamps, loss window) but never reach a decision — that is why they do not appear in Decisions.': 'События с включённым тумблером будят агента: каждое проходит через ограничители и получает строку в журнале решений (сообщение / фото / молчание). События с выключенным тумблером — «пища для состояния»: они обновляют состояние игрока (метки активности, окно проигрыша), но до решения не доходят — поэтому их и нет в разделе «Решения».',
  'bet_settled is special and has no switch: it wakes the agent ONLY when the 24h net loss crosses the high-loss threshold (Settings → Retention bot → Send-frequency guards) — the comfort reaction.': 'bet_settled — особый случай без тумблера: он будит агента ТОЛЬКО когда чистый проигрыш за 24 часа превышает порог крупного проигрыша (Настройки → Ретеншен-бот → Ограничители частоты) — это реакция-поддержка.',
  'State food by default — switch on to react to it.': 'По умолчанию «пища для состояния» — включите, чтобы реагировать.',
  'A deposit landed — a warm thank-you moment.': 'Пришёл депозит — момент тёплой благодарности.',
  'A payment attempt failed — a reassuring note.': 'Платёж не прошёл — подбодрить.',
  'A payout arrived — congratulate.': 'Пришла выплата — поздравить.',
  'New loyalty level — celebrate.': 'Новый уровень лояльности — отпраздновать.',
  'New loyalty class — celebrate.': 'Новый класс лояльности — отпраздновать.',
  'Verification passed — a nice milestone.': 'Пройдена верификация — приятная веха.',
  'Bonus wagered through — congratulate.': 'Бонус отыгран — поздравить.',
  'A bonus expired unused — a gentle heads-up.': 'Бонус сгорел неиспользованным — мягко напомнить.',
  'Deploy-wide setting: switch the header to “All products” to edit it.': 'Общесистемная настройка: чтобы изменить её, переключите шапку на «Все продукты».',
  'global': 'глобально',
  'on': 'вкл',
  'off': 'выкл',
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

  // ----- common gate / login -----
  'Please select a product': 'Выберите продукт',
  'This screen shows data for a single product. Pick a product in the Partner → Product switcher at the top-right to continue.':
    'Этот экран показывает данные одного продукта. Выберите продукт в переключателе Партнёр → Продукт справа вверху, чтобы продолжить.',
  'Invalid email or password': 'Неверный email или пароль',
  'Password': 'Пароль',
  'Remember me': 'Запомнить меня',
  'Sign in': 'Войти',
  'No results.': 'Ничего не найдено.',

  // ----- lists / conversations -----
  'Open': 'Открыта',
  'Escalated': 'Эскалирована',
  'Resolved': 'Решена',
  'Search in messages': 'Поиск по сообщениям',
  'Min messages': 'Мин. сообщений',
  'Topic slug': 'Слаг темы',
  'Language': 'Язык',
  'From': 'С',
  'To': 'По',
  'Session': 'Сессия',
  'Lang': 'Язык',
  'Msgs': 'Сообщ.',
  'Message thread': 'Переписка',
  'No messages.': 'Сообщений нет.',
  'Topic': 'Тема',
  'yes': 'да',
  'no': 'нет',
  'Messages': 'Сообщения',
  'Total cost $': 'Итого $',
  'Created': 'Создана',
  'Conversation': 'Диалог',
  'Sessions that still need attention: escalated hand-offs and abandoned open chats. Rows open the full conversation.':
    'Сессии, требующие внимания: эскалации и брошенные открытые чаты. Клик по строке открывает полную переписку.',
  'Escalations / unresolved': 'Эскалации / нерешённые',
  'First message': 'Первое сообщение',

  // ----- users -----
  'admin (read + write)': 'admin (чтение + запись)',
  'manager (read-only)': 'manager (только чтение)',
  'Generate': 'Сгенерировать',
  'Admin users': 'Администраторы',
  'Edit admin user': 'Редактировать администратора',
  'New admin user': 'Новый администратор',
  'New password (leave empty to keep)': 'Новый пароль (пусто = оставить прежний)',
  'Minimum 8 characters. Set directly — there is no email reset flow.':
    'Минимум 8 символов. Задаётся напрямую — сброса по email нет.',

  // ----- knowledge base -----
  'Knowledge base · variables': 'База знаний · переменные',
  'Edit KB variable': 'Редактировать переменную БЗ',
  'Topics & KB texts': 'Темы и тексты БЗ',
  'Variables': 'Переменные',
  'Stable topic identifier (e.g. deposits). Cannot change after create.':
    'Постоянный идентификатор темы (напр. deposits). После создания не меняется.',
  'Topic title (English)': 'Название темы (английский)',
  'The canonical title and the fallback for every language.':
    'Каноническое название и фолбэк для всех языков.',
  'Translate the title into other languages in': 'Переведите название на другие языки в',
  'Topic names': 'Названия тем',
  'The prompt itself is English-only, so only this English title feeds the model.':
    'Промпт — только на английском, поэтому в модель попадает только это английское название.',
  'Display order': 'Порядок отображения',
  'KB content': 'Текст базы знаний',
  "The topic's knowledge base text (Layer 2). {placeholders} are substituted from KB variables. Clearing the field removes the entry.":
    'Текст базы знаний темы (слой 2). {плейсхолдеры} подставляются из переменных БЗ. Очистка поля удаляет запись.',
  'Edit topic + KB': 'Редактировать тему и БЗ',
  'New topic': 'Новая тема',

  // ----- site map -----
  "Official pages of this product's website. They are added to the system prompt of BOTH the support chat and the Telegram retention bot, and to their links policy, so the assistant links players to real pages instead of inventing URLs. One entry per page — the URL is required and must start with http:// or https://.":
    'Официальные страницы сайта продукта. Они добавляются в системный промпт ОБОИХ ботов (чат поддержки и Telegram-ретеншен) и в их политику ссылок, чтобы ассистент вёл игроков на реальные страницы, а не придумывал URL. Одна запись на страницу — URL обязателен и должен начинаться с http:// или https://.',
  'Title': 'Название',
  'Purpose (when to link here)': 'Назначение (когда сюда ссылаться)',
  'Add page': 'Добавить страницу',
  'Save site map': 'Сохранить карту сайта',
  'Site map saved': 'Карта сайта сохранена',

  // ----- translations page -----
  'General — widget interface': 'Общее — интерфейс виджета',
  'Chrome strings rendered by the widget itself: header, topic picker, buttons, input placeholder.':
    'Строки интерфейса самого виджета: шапка, выбор темы, кнопки, плейсхолдер ввода.',
  'Support bot — messages to the player': 'Бот поддержки — сообщения игроку',
  'What the support bot itself says to the player: the escalation card and its button (incl. the per-language contact_url link) and the closing option.':
    'Что бот поддержки говорит игроку: карточка эскалации и её кнопка (включая ссылку contact_url для каждого языка) и завершающая опция.',
  'Retention bot (Telegram) — messages to the player': 'Ретеншен-бот (Telegram) — сообщения игроку',
  'What the Telegram retention bot says: the entry menu and its buttons, the subscription gate, the manager hand-off, the proactive-ping header and the /stop-/resume confirmations.':
    'Что говорит Telegram-бот: входное меню и его кнопки, проверка подписки, передача менеджеру, заголовок проактивных сообщений и подтверждения /stop и /resume.',
  'Service and error notices': 'Служебные и ошибочные уведомления',
  'Technical fallbacks shown on failures and guards (errors, rate limit, low-content and injection nudges) — rarely need brand tuning.':
    'Технические фолбэки при сбоях и срабатывании защит (ошибки, rate-лимит, пустые сообщения, инъекции) — редко требуют брендовой настройки.',
  "Everything the player sees, editable per language and split into blocks: the general widget interface, the support bot's messages, the Telegram retention bot's messages, and the service / error notices — plus the topic names. Clearing a field falls back to the shipped default (shown as placeholder).":
    'Всё, что видит игрок, — редактируется по языкам и разбито на блоки: интерфейс виджета, сообщения бота поддержки, сообщения Telegram-бота и служебные уведомления, плюс названия тем. Очистка поля возвращает встроенное значение (показано как плейсхолдер).',
  'The topic picker buttons, per language. Stored on the topic itself; a missing translation falls back to English.':
    'Кнопки выбора темы, по языкам. Хранится на самой теме; отсутствующий перевод падает на английский.',
  'Translations saved — live': 'Переводы сохранены — применены',
  'Save translations': 'Сохранить переводы',

  // ----- dashboard -----
  'Sessions over time': 'Сессии по дням',
  'Cost over time': 'Стоимость по дням',
  'Avg cost / session': 'Средняя стоимость / сессия',
  'Escalation rate': 'Доля эскалаций',
  'metrics could not be loaded': 'метрики не загрузились',
  'Sessions (30d)': 'Сессии (30 дн)',
  'Engaged': 'С сообщениями',
  '≥ 1 message': '≥ 1 сообщение',
  'Open sessions': 'Открытые сессии',
  'engaged, still open': 'с сообщениями, ещё открыты',
  'rate': 'доля',
  'Resolution rate': 'Доля решённых',
  'proxy: not escalated': 'прокси: не эскалированы',
  'Avg msgs / session': 'Средн. сообщений / сессия',
  'Cost (USD)': 'Стоимость (USD)',
  'session': 'сессия',
  'Avg response time': 'Среднее время ответа',
  'AI generation, successful calls': 'генерация AI, успешные вызовы',
  'AI calls': 'Вызовы AI',
  'failed': 'с ошибкой',
  'OpenAI requests': 'запросы к OpenAI',
  'Cache hit ratio': 'Попадание в кэш',
  'prefix-cache economics': 'экономика префикс-кэша',
  'Key failovers': 'Переключения ключей',
  'fallback key engaged': 'включался резервный ключ',
  'Blocks': 'Блокировки',
  'rate-limit + injection': 'rate-лимит + инъекции',
  'By topic': 'По темам',
  'By language': 'По языкам',
  'Sessions': 'Сессии',
  'Retention': 'Ретеншен',
  'Retention · Telegram': 'Ретеншен · Telegram',
  'Linked players': 'Привязанные игроки',
  'Active (30d)': 'Активные (30 дн)',
  'wrote in the bot': 'писали боту',
  'Pings sent': 'Отправлено пингов',
  'Photos sent': 'Отправлено фото',
  'Hand-offs': 'Передачи менеджеру',
  'to manager / support': 'менеджеру / в поддержку',

  // ----- proactive agent page -----
  'Agent ENABLED': 'Агент ВКЛЮЧЁН',
  'Agent DISABLED (no proactive messages)': 'Агент ВЫКЛЮЧЕН (проактивных сообщений нет)',
  'DRY-RUN (decides, never sends)': 'DRY-RUN (решает, но не отправляет)',
  'LIVE sending': 'БОЕВАЯ отправка',
  'today': 'сегодня',
  'budget': 'бюджет',
  'none': 'нет',
  'queued events': 'событий в очереди',
  'Refresh': 'Обновить',
  'Drain the event queue through the pipeline now (the worker does the same on its timer).':
    'Прогнать очередь событий через пайплайн сейчас (воркер делает то же по таймеру).',
  'Running…': 'Выполняется…',
  'Process queue now': 'Обработать очередь',
  'worker: running every': 'воркер: запускается каждые',
  'worker: OFF (RETENTION_SCHEDULER_ENABLED=0 in deploy env — only «Process queue now» works)':
    'воркер: ВЫКЛ (RETENTION_SCHEDULER_ENABLED=0 в env деплоя — работает только «Обработать очередь»)',
  'last event': 'последнее событие',
  'Event': 'Событие',
  'Telegram recipient': 'Получатель в Telegram',
  'Player id': 'ID игрока',
  'Payload (JSON)': 'Данные (JSON)',
  'Sending…': 'Отправка…',
  'Inject event': 'Отправить событие',
  'Events': 'События',
  'Decisions': 'Решения',
  'System log': 'Системный журнал',
  'How it works & testing': 'Как это работает и тестирование',
  'What the proactive agent is': 'Что такое проактивный агент',
  'Turning it on and off': 'Включение и выключение',
  'Where the voice, persona and content come from': 'Откуда берутся голос, персонаж и контент',
  'Which events wake the agent': 'Какие события будят агента',
  'Guards — how often the agent may write to one player': 'Ограничители — как часто агент может писать одному игроку',
  'How to test, step by step': 'Как тестировать, по шагам',
  'Costs': 'Стоимость',

  // ----- retention page -----
  'Telegram config saved': 'Настройки Telegram сохранены',
  'Secrets saved': 'Секреты сохранены',
  'Retention bot enabled': 'Ретеншен-бот включён',
  'Save config': 'Сохранить настройки',
  'Register Telegram webhook': 'Зарегистрировать вебхук Telegram',
  'Save secrets': 'Сохранить секреты',
  'Secrets': 'Секреты',
  'Bot username (without @)': 'Username бота (без @)',
  'Channel id (@channel or -100…)': 'ID канала (@channel или -100…)',
  'Channel URL (subscription gate)': 'URL канала (проверка подписки)',
  'Player API URL (profile pull)': 'URL Player API (подтяжка профиля)',
  'Retention KB saved': 'База знаний бота сохранена',
  'Retention prompt variables saved': 'Переменные промпта бота сохранены',
  'Save variables': 'Сохранить переменные',
  'Upload photos': 'Загрузка фото',
  'Description (grounds the caption the model writes)': 'Описание (на нём модель строит подпись)',
  'Tags (comma-separated)': 'Теги (через запятую)',
  'Level (min VIP tier)': 'Level (мин. VIP-уровень)',
  'Stage (explicitness)': 'Stage (откровенность)',
  'Category': 'Категория',
  'Uploading…': 'Загрузка…',
  'Upload': 'Загрузить',
  'Search (description, tags, category)': 'Поиск (описание, теги, категория)',
  'Stage': 'Stage',
  // Kept as-is in RU on purpose — established technical terms in the panel copy.
  'dry-run': 'dry-run',
  'URL': 'URL',
  'Level min': 'Мин. Level',
  'Generate metadata': 'Сгенерировать метаданные',
  'Select all shown': 'Выбрать все показанные',
  'Clear selection': 'Снять выбор',
  'Account & appearance': 'Аккаунт и оформление',
  'Account': 'Аккаунт',
  'Appearance': 'Оформление',
  'Active': 'Активен',
  'Inactive': 'Неактивен',
  'Role': 'Роль',
  'Administrator': 'Администратор',
  'Manager (read-only)': 'Менеджер (только чтение)',
  'Registered': 'Зарегистрирован',
  'Access (groups)': 'Доступ (группы)',
  'No memberships': 'Нет доступов',
  'Global (whole hub)': 'Глобально (весь хаб)',
  'Partner': 'Партнёр',
  'Product': 'Продукт',
  'Theme': 'Тема',
  'Light': 'Светлая',
  'Dark': 'Тёмная',
  'Switching the language reloads the panel.': 'Переключение языка перезагрузит панель.',
  'Content': 'Контент',
  'Bot setup': 'Настройка бота',
  'Bot content': 'Контент бота',
  'Setup': 'Настройка',
  'Logs': 'Логи',
  'System logs': 'Системные логи',
  'Activity (who changed what)': 'Активность (кто что менял)',
  'All levels': 'Все уровни',
  'Info': 'Инфо',
  'Warnings & errors': 'Предупреждения и ошибки',
  'Errors only': 'Только ошибки',
  'Search text': 'Поиск по тексту',
  'Time': 'Время',
  'Message': 'Сообщение',
  'No logs match the filter.': 'Нет логов по фильтру.',
  'Load more': 'Показать ещё',
  'Who': 'Кто',
  'Search (actor, action)': 'Поиск (кто, действие)',
  'No actions recorded yet.': 'Пока нет записей о действиях.',
  'Who changed what in the admin panel. You see actions within your access scope; administrators see every action in reach, managers see only manager-made changes.':
    'Кто что менял в админке. Вы видите действия в пределах своего доступа: администраторы — все действия в зоне доступа, менеджеры — только правки менеджеров.',
  'Select all': 'Выбрать все',
  'Generating…': 'Генерация…',
  'Generating metadata…': 'Генерация метаданных…',
  'Optimize': 'Оптимизировать',
  'Optimizing…': 'Оптимизация…',
  'Optimizing media… this can take a while, keep this tab open.':
    'Оптимизация медиа… это может занять время, не закрывайте вкладку.',
  "AI (the product's own model + API key) fills the description, tags, stage and minimum VIP level for every selected photo. Current values are overwritten.":
    'ИИ (модель и API-ключ самого продукта) заполняет описание, теги, stage и минимальный VIP-уровень для каждого выбранного фото. Текущие значения перезаписываются.',
  'Re-encodes heavy uploads (multi-MB JPG/PNG) to Telegram-sized WebP and deletes the originals. Runs automatically on a schedule — this is the immediate run.':
    'Пережимает тяжёлые загрузки (JPG/PNG в несколько МБ) в WebP размера Telegram и удаляет оригиналы. Запускается автоматически по расписанию — эта кнопка запускает сейчас.',
  'Delete this manager?': 'Удалить этого менеджера?',
  'Display name': 'Отображаемое имя',
  'Telegram username (without @)': 'Username в Telegram (без @)',
  'Add manager': 'Добавить менеджера',
  'Player base': 'База игроков',
  'In range': 'За период',
  'lifetime deeplink entries': 'входы по deeplink за всё время',
  'Subscribed': 'Подписаны',
  'passed the channel gate': 'прошли проверку канала',
  'Pings muted': 'Отключили пинги',
  'opted out via /stop': 'отписались через /stop',
  'Unreachable': 'Недоступны',
  'blocked the bot / sends fail': 'заблокировали бота / отправка падает',
  'Active players': 'Активные игроки',
  'wrote in the range': 'писали за период',
  'New players': 'Новые игроки',
  'first deeplink entry': 'первый вход по deeplink',
  'Player messages': 'Сообщения игроков',
  'proactive nudges': 'проактивные сообщения',
  'Ping replies': 'Ответы на пинги',
  'to manager / site support': 'менеджеру / в поддержку сайта',
  'TG dialog + photo metadata': 'TG-диалог + метаданные фото',
  'Daily activity': 'Активность по дням',
  'Both days inclusive. “Player base” below is lifetime; everything else counts this range.':
    'Обе даты включительно. «База игроков» ниже — за всё время; остальное — за период.',

  // ----- structure / api keys -----
  'Widget key': 'Ключ виджета',
  'Embed snippet': 'Код для вставки',
  'Turnstile site key': 'Turnstile site key',
  'Site URL (home page)': 'URL сайта (главная)',
  'New product slug': 'Слаг нового продукта',
  'Name': 'Имя',
  'New partner slug': 'Слаг нового партнёра',
  'Admins only': 'Только для администраторов',
  'Name (what uses it)': 'Имя (кто использует)',
  'Scope': 'Область',

  // ----- prompt / KB pages -----
  'English only': 'Только английский',
  'Model-facing content must be in English — the backend rejects other scripts. Player-facing copy belongs in Translations.':
    'Контент для модели должен быть на английском — бэкенд отклонит другие алфавиты. Тексты для игроков живут в «Переводах».',
  'KB variables': 'Переменные БЗ',

  // ----- settings editor bits -----
  'free / baseline': 'бесплатно / база',
  'msgs': 'сообщ.',
  '+ Add stage': '+ Добавить стадию',
  '− Remove last': '− Убрать последнюю',
  '(model default)': '(значение модели)',

  // ----- settings: how-it-works bullet points -----
  'Gate order for every message: rate limit → cooldown → length cap → low-content guard → injection scan. A message rejected by a gate never reaches the model — attacks and spam cost no tokens.':
    'Порядок фильтров для каждого сообщения: rate-лимит → кулдаун → лимит длины → защита от пустых сообщений → скан на инъекции. Отклонённое фильтром сообщение никогда не доходит до модели — атаки и спам не тратят токены.',
  '**Anti-spam** ships with sensible values; raise the rate limit only if real players actually hit it. The injection hard-block and low-content guard are safe to keep on.':
    '**Антиспам** поставляется с разумными значениями; повышайте rate-лимит, только если в него реально упираются живые игроки. Жёсткую блокировку инъекций и защиту от пустых сообщений можно спокойно держать включёнными.',
  '**Chat limits** bound one session: how long it stays valid, how many messages before a forced hand-off to a human, and how many recent turns the model sees (the full transcript is always stored).':
    '**Лимиты чата** ограничивают одну сессию: сколько она действует, сколько сообщений до принудительной передачи человеку и сколько последних ходов видит модель (полная переписка хранится всегда).',
  'The whole support pipeline, the content map ("where do I fix this text?") and a step-by-step testing checklist live on the [How it works](#/support-guide) page.':
    'Весь конвейер поддержки, карта контента («где исправить этот текст?») и пошаговый чек-лист тестирования — на странице [Как это работает](#/support-guide).',
  'Two regimes: the **dialogue bot** answers when the player writes; the **proactive agent** writes first in reaction to casino events. The «Send-frequency guards» section is the hard rail for the agent — daily cap, min gap, cooldowns, quiet hours, budget.':
    'Два режима: **диалоговый бот** отвечает, когда игрок пишет; **проактивный агент** пишет первым в ответ на события казино. Раздел «Ограничители частоты» — жёсткие рамки для агента: дневной лимит, мин. интервал, кулдауны, тихие часы, бюджет.',
  'Photos unlock through two gates at once: chat progression (**Stage**) × the player’s VIP tier (**Level**) — the «Photo unlock progression» section below sets both ladders; the same numbers are stamped on each photo in Media.':
    'Фото открываются через два условия сразу: прогресс общения (**Stage**) × VIP-уровень игрока (**Level**) — раздел «Прогрессия разблокировки фото» ниже задаёт обе лестницы; те же значения проставляются на каждом фото в «Медиа».',
  'The agent’s own switches (enabled, dry-run, worker interval, budget) are in the «Proactive agent» section. The full pipeline, guard reference with current values and a testing checklist are on the agent’s [How it works & testing](#/retention-agent?tab=guide) tab.':
    'Переключатели самого агента (включение, dry-run, интервал воркера, бюджет) — в разделе «Проактивный агент». Полный конвейер, справочник ограничителей с текущими значениями и чек-лист тестирования — на вкладке агента [Как это работает и тестирование](#/retention-agent?tab=guide).',
  '**AI model** — the model id plus its budgets and timeouts. «Max output tokens» INCLUDES the model’s hidden reasoning: keep it generous (≈2000), too low and answers can come back empty.':
    '**AI-модель** — ID модели плюс её бюджеты и таймауты. «Макс. токенов на ответ» ВКЛЮЧАЕТ скрытые рассуждения модели: держите с запасом (≈2000), при слишком низком значении ответы могут приходить пустыми.',
  '**Languages** — the supported set and the default. A newly added language starts on English copy and becomes translatable in Translations; answers always follow the player’s language.':
    '**Языки** — поддерживаемый набор и язык по умолчанию. Новый язык стартует с английскими текстами и переводится в «Переводах»; ответы всегда следуют за языком игрока.',
  '**General** — technical lifetimes and caps (sessions, admin tokens, request bodies). These rarely need changing.':
    '**Общие** — технические сроки жизни и лимиты (сессии, админ-токены, тела запросов). Их редко нужно менять.',
  'Every setting resolves per product: product override → global default → deploy env → built-in default. The banner above the form shows which layer you are editing right now.':
    'Каждая настройка резолвится по продукту: переопределение продукта → глобальное значение → env деплоя → встроенное значение. Баннер над формой показывает, какой слой вы сейчас редактируете.',

  // ----- support guide page -----
  'Support chat — how it works': 'Чат поддержки — как это работает',
  'The operator’s guide to the on-site support chat: what happens to a player’s message, where every piece of content is edited, and how to test the whole flow before going live.':
    'Руководство оператора по чату поддержки на сайте: что происходит с сообщением игрока, где редактируется каждый элемент контента и как протестировать весь поток перед запуском.',
  'What the support chat is': 'Что такое чат поддержки',
  'A chat widget on the casino site where the AI persona answers player questions strictly from this product’s **Knowledge base**. The player picks a topic, chats in their own language, and either gets the answer or is handed off to a human via the escalation card. The widget is embedded with one script tag — the snippet (with this product’s widget key) is in **Structure**.':
    'Чат-виджет на сайте казино, где AI-персонаж отвечает на вопросы игроков строго по **Базе знаний** этого продукта. Игрок выбирает тему, общается на своём языке и либо получает ответ, либо передаётся человеку через карточку эскалации. Виджет встраивается одним script-тегом — код вставки (с ключом виджета продукта) находится в «**Структуре**».',
  'The path of one message': 'Путь одного сообщения',
  'Every player message goes through the same pipeline, in order:':
    'Каждое сообщение игрока проходит один и тот же конвейер, по порядку:',
  '**Anti-spam gates** — rate limit per IP, a cooldown between messages, a length cap, the low-content guard (one-character spam gets a nudge without a model call) and the prompt-injection scan. All tunable in **Chat settings**; a rejected message never reaches the model, so attacks don’t burn tokens.':
    '**Антиспам-фильтры** — rate-лимит по IP, кулдаун между сообщениями, лимит длины, защита от пустых сообщений (односимвольный спам получает подсказку без вызова модели) и скан на prompt-инъекции. Всё настраивается в «**Настройках чата**»; отклонённое сообщение не доходит до модели, поэтому атаки не сжигают токены.',
  '**Keyword escalation check** — if the message hits a high-risk stem (fraud, legal threats) or an explicit "call a human", the escalation card is shown immediately, before any model call. The lists are edited in **Prompt → Prompt variables**.':
    '**Проверка стоп-слов** — если сообщение содержит высокорисковый триггер (мошенничество, юридические угрозы) или явную просьбу позвать человека, карточка эскалации показывается сразу, до вызова модели. Списки редактируются в «**Промпт → Переменные промпта**».',
  '**Prompt assembly** — three layers: the fixed persona + rules (rendered with your prompt variables), the selected topic’s KB text, and the per-message data (player profile, conversation history, language). Only the selected topic’s KB is loaded — that’s why topic routing matters.':
    '**Сборка промпта** — три слоя: фиксированный персонаж + правила (с вашими переменными промпта), текст БЗ выбранной темы и данные конкретного сообщения (профиль игрока, история диалога, язык). Загружается только БЗ выбранной темы — поэтому маршрутизация тем так важна.',
  '**Model answer** — the AI answers in the player’s language and may attach service signals: a topic switch, follow-up suggestions, a "question resolved" flag, or an escalation. The signals are stripped from the text and become widget behaviour.':
    '**Ответ модели** — AI отвечает на языке игрока и может приложить служебные сигналы: смену темы, подсказки-продолжения, флаг «вопрос решён» или эскалацию. Сигналы вырезаются из текста и превращаются в поведение виджета.',
  '**Persistence** — the turn, its token cost and every state change are stored; you see them in **Conversations** (full transcripts with per-turn cost) and on the **Dashboard**.':
    '**Сохранение** — ход диалога, его стоимость в токенах и каждая смена состояния записываются; вы видите их в «**Диалогах**» (полные переписки со стоимостью каждого хода) и на «**Дашборде**».',
  'Where every piece of content is edited': 'Где редактируется каждый элемент контента',
  'One home per thing — if a text or number is wrong in the chat, this table says where to fix it:':
    'У каждой вещи один дом — если в чате неверный текст или число, эта таблица подскажет, где исправлять:',
  'What': 'Что',
  'Where to edit': 'Где редактировать',
  'Notes': 'Примечания',
  'Answers to player questions': 'Ответы на вопросы игроков',
  'One KB text per topic. The assistant answers STRICTLY from it — facts missing here are the #1 reason for vague answers or escalations.':
    'Один текст БЗ на тему. Ассистент отвечает СТРОГО по нему — отсутствующие здесь факты — причина №1 расплывчатых ответов и эскалаций.',
  'Numbers, amounts, timeframes': 'Числа, суммы, сроки',
  'Knowledge base → Variables': 'База знаний → Переменные',
  'Reusable `{placeholder}` values substituted into every KB text — change a limit once, it updates everywhere.':
    'Переиспользуемые значения `{placeholder}`, подставляемые в каждый текст БЗ, — измените лимит один раз, и он обновится везде.',
  'Persona: name, brand, tone of voice': 'Персонаж: имя, бренд, тон общения',
  'Prompt → Prompt variables': 'Промпт → Переменные промпта',
  'The values that uniquify the shared prompt template for this brand. The wording around them is fixed in code.':
    'Значения, которые уникализируют общий шаблон промпта под этот бренд. Формулировки вокруг них зафиксированы в коде.',
  'Escalation trigger words': 'Слова-триггеры эскалации',
  'Two keyword lists (high-risk + "call a human") checked BEFORE the model — a match hands off without burning tokens.':
    'Два списка стоп-слов (высокорисковые + «позовите человека»), проверяемые ДО модели, — совпадение передаёт чат человеку, не тратя токены.',
  'Everything the player reads in the widget': 'Всё, что игрок читает в виджете',
  'Widget chrome, service replies, the escalation card and its per-language contact link (`contact_url`), topic names — per language.':
    'Интерфейс виджета, служебные ответы, карточка эскалации и её контактная ссылка для каждого языка (`contact_url`), названия тем — по языкам.',
  'Pages the assistant may link to': 'Страницы, на которые ассистент может ссылаться',
  'The official site pages (shared with the Telegram bot). The assistant never invents URLs — it links only these.':
    'Официальные страницы сайта (общие с Telegram-ботом). Ассистент никогда не придумывает URL — он ссылается только на эти.',
  'Anti-spam and chat limits': 'Антиспам и лимиты чата',
  'Rate limits, cooldowns, message caps, the injection and low-content guards.':
    'Rate-лимиты, кулдауны, лимиты сообщений, защита от инъекций и пустых сообщений.',
  'Model, languages, technical limits': 'Модель, языки, технические лимиты',
  'System → Settings': 'Система → Настройки',
  'Shared by both bots: the OpenAI model and its budgets, the supported languages, request limits.':
    'Общее для обоих ботов: модель OpenAI и её бюджеты, поддерживаемые языки, лимиты запросов.',
  'The prompt WORDING itself (the rules around your variables) is deliberately not editable here — it lives in the code as the one shared template, so every brand runs the same tested behaviour. What you can always do is READ it: **Prompt → Preview** shows the complete assembled prompt exactly as the model receives it.':
    'Сами ФОРМУЛИРОВКИ промпта (правила вокруг ваших переменных) намеренно не редактируются здесь — они живут в коде как единый общий шаблон, поэтому каждый бренд работает на одном и том же проверенном поведении. Но их всегда можно ПРОЧИТАТЬ: «**Промпт → Просмотр**» показывает полный собранный промпт ровно таким, каким его получает модель.',
  'Topics and automatic routing': 'Темы и автоматическая маршрутизация',
  'The player picks a topic first; only that topic’s KB is loaded. The topic buttons and their per-language names come from **Knowledge base** + **Translations → Topic names**.':
    'Игрок сначала выбирает тему; загружается только её БЗ. Кнопки тем и их названия по языкам берутся из «**Базы знаний**» + «**Переводы → Названия тем**».',
  '**Wrong-topic questions route automatically**: when a question plainly belongs to another topic, the widget shows a "switching to …" notice, switches, and re-asks the question against the right KB — the player never sees an answer produced without the matching KB.':
    '**Вопросы не по теме маршрутизируются автоматически**: когда вопрос явно относится к другой теме, виджет показывает уведомление «переключаюсь на…», переключается и заново задаёт вопрос уже с правильной БЗ — игрок никогда не видит ответ, сгенерированный без подходящей БЗ.',
  '`other` is the general entry topic with its own KB. It routes players onward more often, but answers from its own KB exactly like the rest.':
    '`other` — общая входная тема со своей собственной БЗ. Она чаще перенаправляет игроков дальше, но отвечает по своей БЗ точно так же, как остальные.',
  'Escalation — how a chat reaches a human': 'Эскалация — как чат попадает к человеку',
  '**Soft** (trigger words): the contact card is shown but the chat stays open — a false positive never kills a live conversation.':
    '**Мягкая** (слова-триггеры): карточка контакта показывается, но чат остаётся открытым — ложное срабатывание никогда не убивает живой диалог.',
  '**Hard** (the model gives up, the message cap, or the player taps the escalate button): the card is shown and the conversation ends.':
    '**Жёсткая** (модель сдаётся, достигнут лимит сообщений или игрок нажал кнопку эскалации): карточка показывается, и диалог завершается.',
  'The card’s button target is the per-language `contact_url` in **Translations**. When this product’s **Telegram retention bot** is enabled, the button instead deep-links the player straight into the bot (subscription gate on the way in, "go to a manager" in its menu).':
    'Кнопка карточки ведёт на `contact_url` для языка игрока из «**Переводов**». Если у продукта включён **Telegram-ретеншен-бот**, кнопка вместо этого ведёт игрока по deeplink прямо в бота (с проверкой подписки на входе и пунктом «к менеджеру» в меню).',
  'Escalated and abandoned chats queue up in **Escalations** for triage, grouped by topic.':
    'Эскалированные и брошенные чаты собираются в «**Эскалациях**» для разбора, сгруппированные по темам.',
  'Suggestions and finishing a chat': 'Подсказки и завершение чата',
  'After an answer the assistant may offer up to two one-tap follow-up questions whose answers ARE in the KB — they pull the player toward the exact entry they need.':
    'После ответа ассистент может предложить до двух вопросов-продолжений в один тап, ответы на которые ЕСТЬ в БЗ, — они подводят игрока к нужной записи.',
  'A separate green option lets the player close the chat ("Issue solved."); when the assistant judges the question fully answered, the widget also shows a "finish chat" button. A finished chat is marked resolved and leaves the open-sessions metric.':
    'Отдельная зелёная опция позволяет игроку закрыть чат («Проблема решена»); когда ассистент считает вопрос полностью отвеченным, виджет также показывает кнопку «завершить чат». Завершённый чат помечается решённым и уходит из метрики открытых сессий.',
  'The widget opens in the browser’s language; the ANSWERS follow the player — switch language mid-chat and the assistant (and the widget chrome) switch too.':
    'Виджет открывается на языке браузера; ОТВЕТЫ следуют за игроком — смените язык посреди чата, и ассистент (и интерфейс виджета) переключатся тоже.',
  'The supported set and the default live in **System → Settings → Languages**. A newly added language starts on English copy and becomes fully translatable in **Translations**.':
    'Набор поддерживаемых языков и язык по умолчанию — в «**Система → Настройки → Языки**». Новый язык стартует с английскими текстами и полностью переводится в «**Переводах**».',
  'The KB stays in English on purpose (most token-efficient for the model) — the assistant still answers in the player’s language.':
    'БЗ намеренно остаётся на английском (самый экономный по токенам язык для модели) — ассистент всё равно отвечает на языке игрока.',
  'Select the product in the header switcher and check its content: topics + KB texts (**Knowledge base**), persona values (**Prompt → Prompt variables**), the contact link and widget copy (**Translations**).':
    'Выберите продукт в переключателе шапки и проверьте его контент: темы + тексты БЗ («**База знаний**»), значения персонажа («**Промпт → Переменные промпта**»), контактную ссылку и тексты виджета («**Переводы**»).',
  'Set the **Test player** profile (Prompt → Prompt variables) — on a test deploy without the site handshake it stands in for the real player, so you can check the by-name greeting and VIP personalization.':
    'Задайте профиль **тестового игрока** (Промпт → Переменные промпта) — на тестовом деплое без хендшейка сайта он заменяет реального игрока, так что можно проверить приветствие по имени и VIP-персонализацию.',
  'Open the test page (the service root `/`) or embed the snippet from **Structure** on a staging page, pick a topic and ask real questions from the KB — including ones phrased differently from how the KB is written.':
    'Откройте тестовую страницу (корень сервиса `/`) или вставьте код из «**Структуры**» на staging-страницу, выберите тему и задавайте реальные вопросы из БЗ — в том числе сформулированные иначе, чем написано в БЗ.',
  'Ask a question that belongs to ANOTHER topic and watch the automatic switch notice + the re-ask. Then trigger an escalation ("I want to talk to a human") and check the card — its button, language, and (with retention on) the bot deeplink.':
    'Задайте вопрос из ДРУГОЙ темы и проследите за уведомлением об автоматическом переключении + повторным вопросом. Затем вызовите эскалацию («хочу поговорить с человеком») и проверьте карточку — её кнопку, язык и (при включённом ретеншене) deeplink в бота.',
  'Review the results in **Conversations** (transcript, per-turn cost, switch markers) and the **Dashboard** (sessions, escalation rate, cost). Wrong or vague answers almost always mean a KB gap — fix the KB text, not the prompt.':
    'Просмотрите результаты в «**Диалогах**» (переписка, стоимость каждого хода, маркеры переключений) и на «**Дашборде**» (сессии, доля эскалаций, стоимость). Неверные или расплывчатые ответы почти всегда означают пробел в БЗ — правьте текст БЗ, а не промпт.',
  'Each answer is one model call; its token cost is stored per turn and summed per session, topic and language on the **Dashboard**. The prompt is built so its expensive fixed part is cached by the provider — editing prompt variables or a KB text resets that cache briefly, which is normal.':
    'Каждый ответ — один вызов модели; его стоимость в токенах записывается на каждый ход и суммируется по сессиям, темам и языкам на «**Дашборде**». Промпт устроен так, что его дорогая фиксированная часть кэшируется провайдером — правка переменных промпта или текста БЗ ненадолго сбрасывает этот кэш, это нормально.',

  // ----- proactive agent page: status header -----
  'last processed': 'обработано',
  'last decision': 'последнее решение',
  'no decisions': 'решений нет',
  'delivered': 'доставлено',
  'Switches and knobs live in Settings → Retention bot («Proactive agent» + «Send-frequency guards»). The worker interval is a live setting too — 5s means near-realtime reactions. Dry-run ships ON: the agent decides and logs to the ledger below without sending — review its decisions, then turn dry-run off. New here? Read the «How it works & testing» tab.':
    'Переключатели и настройки живут в Настройки → Ретеншен-бот («Проактивный агент» + «Ограничители частоты»). Интервал воркера — тоже «горячая» настройка: 5с — реакции почти в реальном времени. Dry-run включён по умолчанию: агент принимает решения и пишет их в журнал ниже, но ничего не отправляет — проверьте решения и выключите dry-run. Впервые здесь? Прочитайте вкладку «Как это работает и тестирование».',

  // ----- proactive agent page: simulator -----
  'Event simulator — inject a canonical event as if the casino sent it':
    'Симулятор событий — отправьте каноническое событие, как будто его прислало казино',
  'Payload is not valid JSON': 'Данные — не валидный JSON',
  'Event injected': 'Событие отправлено',
  'Simulation failed': 'Симуляция не удалась',
  'auto = the player’s most recently active link': 'auto = последняя активная привязка игрока',
  'auto (by player id)': 'авто (по ID игрока)',
  'the casino player_id': 'player_id в казино',
  'Sample payloads:': 'Примеры данных:',
  'state food — wakes the agent only when the 24h net loss crosses the high-loss threshold':
    'данные о состоянии — будит агента, только когда чистый проигрыш за 24ч превышает порог крупного проигрыша',
  'wakes the agent (a decision will be ledgered)': 'будит агента (решение попадёт в журнал)',
  'state food only (no decision, feeds player state)': 'только данные о состоянии (без решения, обновляет состояние игрока)',

  // ----- proactive agent page: simulator sample payload labels -----
  'regular deposit': 'обычный депозит',
  'first deposit': 'первый депозит',
  'big + profile refresh': 'крупный + обновление профиля',
  'card deposit started': 'начат депозит картой',
  'card declined': 'карта отклонена',
  '3-D Secure failed': 'ошибка 3-D Secure',
  'payout received': 'выплата получена',
  'big win payout': 'выплата крупного выигрыша',
  'losing bet': 'проигрышная ставка',
  'winning bet': 'выигрышная ставка',
  'big loss (crosses threshold)': 'крупный проигрыш (превышает порог)',
  'bonus-money round (excluded)': 'раунд на бонусные деньги (не учитывается)',
  'mobile login': 'вход с мобильного',
  'desktop login': 'вход с компьютера',
  'session over': 'сессия завершена',
  'deposit match granted': 'начислен бонус на депозит',
  'free spins granted': 'начислены фриспины',
  'bonus activated': 'бонус активирован',
  'wagering done, payout': 'вейджер отыгран, выплата',
  'free spins expired unused': 'фриспины сгорели неиспользованными',
  'match bonus expired': 'бонус на депозит истёк',
  'verification started': 'верификация начата',
  'verification passed': 'верификация пройдена',
  'document unreadable': 'документ нечитаем',
  'mission XP': 'XP за миссию',
  'new level': 'новый уровень',
  'level + fresh VIP tier': 'уровень + новый VIP-уровень',
  'new loyalty class': 'новый класс лояльности',
  'class downgraded': 'класс понижен',
  'pack opened': 'пак открыт',
  'pack completed': 'пак завершён',
  'daily check-in': 'ежедневный чек-ин',
  'mission done': 'миссия выполнена',
  'empty': 'пусто',

  // ----- proactive agent page: events / decisions / logs tables -----
  'Clear all': 'Очистить всё',
  'The event log is also the state resolver’s memory (loss window, recent activity) — deleting rows rewrites that derived state. Meant for wiping simulator/test rows.':
    'Журнал событий — это ещё и память резолвера состояния (окно проигрыша, недавняя активность): удаление строк переписывает это производное состояние. Предназначено для очистки тестовых строк из симулятора.',
  'Delete ALL of this product\'s events (decisions stay, minus the event link).':
    'Удалить ВСЕ события этого продукта (решения остаются, но без ссылки на событие).',
  'When (casino time)': 'Когда (время казино)',
  'Source': 'Источник',
  'Payload': 'Данные',
  'Processed': 'Обработано',
  'queued': 'в очереди',
  'Delete this event': 'Удалить это событие',
  'No events yet. The casino posts them to `POST /partner/{product_id}/event`, or inject one with the simulator above.':
    'Событий пока нет. Казино отправляет их на `POST /partner/{product_id}/event`, либо отправьте событие через симулятор выше.',
  'Deleting a decision “refunds” its cost from today’s budget and re-arms the same-event cooldown for that event type — so a wiped test decision can be re-run immediately.':
    'Удаление решения «возвращает» его стоимость в дневной бюджет и сбрасывает кулдаун одинаковых событий для этого типа — стёртое тестовое решение можно повторить сразу.',
  'Delete ALL of this product\'s decisions (resets today\'s budget counter and all same-event cooldowns).':
    'Удалить ВСЕ решения этого продукта (сбрасывает счётчик дневного бюджета и все кулдауны одинаковых событий).',
  'Decision': 'Решение',
  'Tone': 'Тон',
  'Why / brief': 'Почему / бриф',
  'Guards': 'Ограничители',
  'Delivered': 'Доставлено',
  'Cost': 'Стоимость',
  'brief:': 'бриф:',
  'comfort window': 'окно поддержки',
  'clear': 'без блокировок',
  'Delete this decision': 'Удалить это решение',
  'No decisions yet — inject an event and press «Process queue now».':
    'Решений пока нет — отправьте событие и нажмите «Обработать очередь».',
  'Every agent action leaves a durable trace here: decisions, simulator injections, manual queue runs, deletes. The same facts stream to the deploy (Railway) logs as `retention_v2_*` lines — decisions, guard blocks and failed sends included — so this view and the deploy logs always tell one story.':
    'Каждое действие агента оставляет здесь постоянный след: решения, события из симулятора, ручные запуски очереди, удаления. Те же факты идут в логи деплоя (Railway) строками `retention_v2_*` — включая решения, блокировки ограничителей и неудачные отправки, — так что этот экран и логи деплоя всегда рассказывают одну историю.',
  'Type': 'Тип',
  'Details': 'Детали',
  'No log entries yet — they appear as soon as the pipeline processes an event (or you inject one).':
    'Записей пока нет — они появятся, как только пайплайн обработает событие (или вы отправите его сами).',

  // ----- proactive agent page: guide, section 1 -----
  'An event-driven agent that reacts to what just happened at the casino. A canonical event (deposit, big loss, level-up, …) arrives, a cheap AI call decides whether Nika should say something, and if yes the normal retention persona writes the message. Very often the correct decision is **silence** — that is by design, and silence is logged too.':
    'Событийный агент, реагирующий на то, что только что произошло в казино. Приходит каноническое событие (депозит, крупный проигрыш, новый уровень, …), дешёвый AI-вызов решает, стоит ли Нике что-то сказать, и если да — сообщение пишет обычный ретеншен-персонаж. Очень часто правильное решение — **молчание**: так и задумано, и молчание тоже попадает в журнал.',
  'The pipeline for every event, in order:': 'Пайплайн для каждого события, по порядку:',
  '**Event arrives** — from the casino’s webhook `POST /partner/{product_id}/event` or from the simulator on this page. Events are idempotent by `event_id`: a retried webhook is counted, not stored twice.':
    '**Событие приходит** — из вебхука казино `POST /partner/{product_id}/event` или из симулятора на этой странице. События идемпотентны по `event_id`: повторный вебхук учитывается, но не сохраняется дважды.',
  '**State resolver (deterministic)** — computes the player snapshot the agent will see: user status (registered/active/at-risk/dormant), risk state, lifecycle stage, and the 24h net-loss window summed from `bet_settled` payloads.':
    '**Резолвер состояния (детерминированный)** — считает снимок игрока, который увидит агент: статус (registered/active/at-risk/dormant), риск-состояние, этап жизненного цикла и окно чистого проигрыша за 24ч, суммируемое из данных `bet_settled`.',
  '**Guards (deterministic)** — decide whether contact is allowed at all and which actions are permitted (message / photo / silence). The model can never override a guard. See the table below.':
    '**Ограничители (детерминированные)** — решают, разрешён ли контакт вообще и какие действия допустимы (сообщение / фото / молчание). Модель не может обойти ограничитель. См. таблицу ниже.',
  '**Agent decision** — one cheap strict-JSON model call. Input: the state snapshot, the event, the player’s recent events, the tail of their Telegram conversation, and the guard constraints. Output: `action` (silence/message/photo), `tone` (warm/celebrate/comfort/neutral), and a short `intent` brief. Anything malformed degrades to silence.':
    '**Решение агента** — один дешёвый вызов модели со строгим JSON. Вход: снимок состояния, событие, недавние события игрока, хвост его Telegram-диалога и ограничения от ограничителей. Выход: `action` (silence/message/photo), `tone` (warm/celebrate/comfort/neutral) и короткий бриф `intent`. Всё некорректное деградирует в молчание.',
  '**Message generation** — the SAME persona stack that answers Telegram chats writes the text from the agent’s brief. Nothing here is agent-specific: persona, tone of voice, KB, language all come from the regular retention configuration (next section).':
    '**Генерация сообщения** — текст по брифу агента пишет ТОТ ЖЕ стек персонажа, что отвечает в Telegram-чатах. Здесь нет ничего специфичного для агента: персонаж, тон, база знаний и язык берутся из обычной конфигурации ретеншена (следующий раздел).',
  '**Ledger** — ONE row per decision, whatever the outcome (sent, silence, blocked, dry-run), with the state snapshot, guard verdict, the agent’s reasoning and the summed cost. “Why did/didn’t the bot write?” is always answerable from the Decisions tab.':
    '**Журнал решений** — ОДНА строка на решение при любом исходе (отправлено, молчание, заблокировано, dry-run): снимок состояния, вердикт ограничителей, рассуждение агента и суммарная стоимость. На вопрос «почему бот написал / не написал?» всегда отвечает вкладка «Решения».',

  // ----- proactive agent page: guide, section 2 -----
  '**Agent enabled** (Settings → Retention bot → «Proactive agent») is the per-product switch. Off = the agent never writes first; queued events wait unprocessed and the ledger stays readable. The dialogue bot (replies to players who write), escalation hand-offs and the photo machinery inside dialogue are never affected.':
    '**«Агент включён»** (Настройки → Ретеншен-бот → «Проактивный агент») — переключатель на продукт. Выкл = агент никогда не пишет первым; события ждут в очереди необработанными, журнал остаётся доступным для чтения. Диалоговый бот (ответы пишущим игрокам), эскалации и механика фото внутри диалога не затрагиваются.',
  '**Dry-run** keeps the agent deciding and logging without sending — the safe review mode.':
    '**Dry-run** — агент принимает решения и пишет их в журнал, но не отправляет: безопасный режим проверки.',
  '**Worker interval** (same Settings section) is how often the background worker drains the event queue — it applies live on the next tick, and 5 seconds gives near-realtime reactions.':
    '**Интервал воркера** (тот же раздел Настроек) — как часто фоновый воркер разбирает очередь событий; применяется сразу со следующего тика, 5 секунд дают реакции почти в реальном времени.',
  '**Deploy-level master switch**: `RETENTION_SCHEDULER_ENABLED` (Railway env) starts the background worker at all; with it off only «Process queue now» moves the queue. The worker chip in the header shows this.':
    '**Главный переключатель уровня деплоя**: `RETENTION_SCHEDULER_ENABLED` (env Railway) вообще запускает фоновый воркер; когда он выключен, очередь двигает только «Обработать очередь». Чип воркера в шапке это показывает.',

  // ----- proactive agent page: guide, section 3 -----
  '**Persona & tone of voice** — Retention → Prompt variables (persona name, role, brand, products, `retention_tone_of_voice`). The agent only writes a short brief; the persona prompt writes the actual words. The full assembled prompt is visible in Retention → Prompt preview.':
    '**Персонаж и тон** — Ретеншен → Переменные промпта (имя персонажа, роль, бренд, продукты, `retention_tone_of_voice`). Агент пишет только короткий бриф; сами слова пишет промпт персонажа. Полный собранный промпт виден в Ретеншен → Просмотр промпта.',
  '**Facts the bot may use** — the Retention KB document (Retention → KB), same as in dialogue.':
    '**Факты, которыми может пользоваться бот** — документ базы знаний ретеншена (Ретеншен → База знаний), как и в диалоге.',
  '**The message header** — every proactive message goes out under the italic “✨ A little note from {persona}” line: the `rtn_ping_header` key in Translations (per language).':
    '**Заголовок сообщения** — каждое проактивное сообщение уходит под курсивной строкой «✨ A little note from {persona}»: ключ `rtn_ping_header` в «Переводах» (по языкам).',
  '**The inline button** — when the model attaches a `[[LINK:url]]` matching the occasion, the validated Site map page (Support chat → Site map) rides under the message as one button. Comfort mode strips it.':
    '**Кнопка под сообщением** — когда модель прикладывает `[[LINK:url]]`, подходящий к поводу, проверенная страница из Карты сайта (Чат поддержки → Карта сайта) едет под сообщением одной кнопкой. Режим поддержки после проигрыша её убирает.',
  '**Photos** — the Media library (Retention → Media), same stage × VIP gating and daily caps as in dialogue. Only positive occasions may carry a photo:':
    '**Фото** — библиотека «Медиа» (Ретеншен → Медиа), те же ограничения Stage × VIP и дневные лимиты, что и в диалоге. Фото допускается только по позитивным поводам:',
  '**Language** — the player’s sticky conversation language (the same one their Telegram chat drifted to).':
    '**Язык** — «липкий» язык диалога игрока (тот, на который перешёл его Telegram-чат).',

  // ----- proactive agent page: guide, section 4 -----
  '**Decision-worthy** (the agent is consulted, a ledger row appears):':
    '**Требуют решения** (агент подключается, появляется строка в журнале):',
  '**Special:** `bet_settled` wakes the agent only when the player’s 24h net loss crosses the high-loss threshold (Settings → «High-loss threshold»); below it the event silently feeds the loss window.':
    '**Особый случай:** `bet_settled` будит агента, только когда чистый проигрыш игрока за 24ч превышает порог крупного проигрыша (Настройки → «Порог крупного проигрыша»); ниже порога событие молча пополняет окно проигрыша.',
  '**State food only** (no decision — they update activity timestamps, the loss window and the profile snapshot):':
    '**Только данные о состоянии** (без решения — обновляют временные метки активности, окно проигрыша и снимок профиля):',
  'Every stored event also refreshes the player\'s activity timestamps: `deposit_confirmed → last_deposit_at`, `session_started/ended → last_login_at`, `bet_settled → last_played_at` — the state resolver (idle days, days since deposit) reads them.':
    'Каждое сохранённое событие также обновляет временные метки активности игрока: `deposit_confirmed → last_deposit_at`, `session_started/ended → last_login_at`, `bet_settled → last_played_at` — их читает резолвер состояния (дни без активности, дни с последнего депозита).',

  // ----- proactive agent page: guide, section 5 (guards) -----
  'Deterministic rails the model can never override. They are the knobs that decide the send frequency — all editable live in Settings → Retention bot → «Send-frequency guards». Current values for this product are shown in the table. Each blocked decision lists its reasons in the Guards column of the ledger:':
    'Детерминированные рамки, которые модель не может обойти. Именно они определяют частоту отправки — все правятся на лету в Настройки → Ретеншен-бот → «Ограничители частоты». Текущие значения для этого продукта показаны в таблице. Каждое заблокированное решение перечисляет причины в колонке «Ограничители» журнала:',
  'Guard reason': 'Причина блокировки',
  'Current value': 'Текущее значение',
  'What it means / which setting drives it': 'Что это значит / какая настройка этим управляет',
  'The player has not passed the channel-subscription gate.': 'Игрок не прошёл проверку подписки на канал.',
  'The player sent /stop (they can /resume).': 'Игрок отправил /stop (может вернуться через /resume).',
  'Telegram returned 403 — the player blocked the bot.': 'Telegram вернул 403 — игрок заблокировал бота.',
  '«Max proactive messages per player per day» — the hard daily ceiling.':
    '«Макс. проактивных сообщений игроку в день» — жёсткий дневной потолок.',
  '«Min gap between messages (hours)» — spacing between any two proactive messages to one player (0 = off). Lower it to react to several events per day.':
    '«Мин. интервал между сообщениями (часы)» — промежуток между любыми двумя проактивными сообщениями одному игроку (0 = выкл). Уменьшите, чтобы реагировать на несколько событий в день.',
  '«Same-event cooldown (hours)» — one reaction per event type per player per window. Set 0 while testing to re-run the same event.':
    '«Кулдаун одинаковых событий (часы)» — одна реакция на тип события на игрока за окно. Поставьте 0 на время тестирования, чтобы повторять одно и то же событие.',
  '«Quiet hours start/end/UTC offset» — no proactive contact at night.':
    '«Начало/конец тихих часов, смещение UTC» — никаких проактивных сообщений ночью.',
  '«Daily AI budget (USD)» — today’s ledger cost hit the budget.':
    '«Дневной AI-бюджет (USD)» — стоимость решений за сегодня достигла бюджета.',
  '«Loss comfort window» + «High-loss threshold» — after a big loss: empathetic tone only, no photo, no link, no play talk.':
    '«Окно поддержки после проигрыша» + «Порог крупного проигрыша» — после крупного проигрыша: только эмпатичный тон, без фото, без ссылок, без разговоров об игре.',
  '/ day': '/ день',
  'h': 'ч',
  'no budget': 'без бюджета',

  // ----- proactive agent page: guide, section 6 (testing) -----
  'Select the product in the header switcher, then in Settings → Retention bot → «Proactive agent» turn **Agent enabled** ON and leave **dry-run** ON (safe: nothing is sent).':
    'Выберите продукт в переключателе в шапке, затем в Настройки → Ретеншен-бот → «Проактивный агент» включите **«Агент включён»** и оставьте **dry-run** включённым (безопасно: ничего не отправляется).',
  'Link a test player to the Telegram bot: open the bot through a deeplink (easiest: escalate in the support-chat widget, or `POST /api/retention/deeplink` with a test `user_context`), press /start and subscribe to the channel. The `player_id` from that handshake is the id you feed the simulator.':
    'Привяжите тестового игрока к Telegram-боту: откройте бота по deeplink (проще всего — эскалация в виджете чата поддержки, либо `POST /api/retention/deeplink` с тестовым `user_context`), нажмите /start и подпишитесь на канал. `player_id` из этого хендшейка — тот id, который вы вводите в симулятор.',
  'In the simulator pick an event (e.g. `deposit_confirmed`), enter that player id, pick a sample payload, «Inject event». If several Telegram accounts are linked to the same test player, pick the exact recipient in «Telegram recipient» — on «auto» the message goes to the player’s most recently active link (the Decisions tab shows the actual @username either way).':
    'В симуляторе выберите событие (напр. `deposit_confirmed`), введите этот id игрока, выберите пример данных, «Отправить событие». Если к одному тестовому игроку привязано несколько Telegram-аккаунтов, укажите точного получателя в «Получатель в Telegram» — на «авто» сообщение уйдёт на последнюю активную привязку игрока (вкладка «Решения» в любом случае показывает фактический @username).',
  'Press «Process queue now» and open the Decisions tab: you should see the action, tone, the agent’s brief and reasoning, the guard verdict and the cost. Try a losing-day scenario: inject a few `bet_settled` «big loss» samples and watch the comfort constraints appear.':
    'Нажмите «Обработать очередь» и откройте вкладку «Решения»: вы увидите действие, тон, бриф и рассуждение агента, вердикт ограничителей и стоимость. Попробуйте сценарий проигрышного дня: отправьте несколько примеров `bet_settled` «крупный проигрыш» и посмотрите, как появляются ограничения режима поддержки.',
  'Blocked? The Guards column names the reason and the table above names the setting. For repeated testing: set «Same-event cooldown» to 0, raise the daily cap, widen quiet hours — or simply delete the previous decision row (that re-arms the cooldown and refunds the budget).':
    'Заблокировано? Колонка «Ограничители» называет причину, а таблица выше — настройку. Для повторных тестов: поставьте «Кулдаун одинаковых событий» в 0, поднимите дневной лимит, расширьте тихие часы — или просто удалите предыдущую строку решения (это сбрасывает кулдаун и возвращает бюджет).',
  'When the decisions look right, turn **dry-run OFF** and re-inject: the message reaches the player in Telegram — italic header + persona text (+ button/photo when chosen). It is also persisted into the player’s Retention → Conversations transcript.':
    'Когда решения выглядят правильно, выключите **dry-run** и отправьте событие снова: сообщение дойдёт до игрока в Telegram — курсивный заголовок + текст персонажа (+ кнопка/фото, если выбраны). Оно также сохраняется в переписку игрока в Ретеншен → Диалоги.',
  'Clean up after yourself: delete test rows one by one or «Clear all» on both tabs. Costs already logged to Analytics stay (they were real OpenAI calls).':
    'Приберитесь за собой: удалите тестовые строки по одной или «Очистить всё» на обеих вкладках. Затраты, уже записанные в Аналитику, остаются (это были реальные вызовы OpenAI).',

  // ----- proactive agent page: guide, section 7 (costs) -----
  'Every decision is one cheap model call; a sent message adds one generation call. Both land in `ai_interaction_logs` and in the Telegram cost split on Retention → Analytics. The daily budget (Settings) is a hard stop: when the day’s summed ledger cost reaches it, the agent stays quiet until tomorrow.':
    'Каждое решение — один дешёвый вызов модели; отправленное сообщение добавляет один вызов генерации. Оба попадают в `ai_interaction_logs` и в разбивку Telegram-затрат в Ретеншен → Аналитика. Дневной бюджет (Настройки) — жёсткий стоп: когда суммарная стоимость решений за день его достигает, агент молчит до завтра.',

  // ----- proactive agent page: shell / notifications -----
  'Status load failed': 'Не удалось загрузить статус',
  'events': 'событий',
  'decisions': 'решений',
  'Run failed': 'Не удалось выполнить',
  'Deleted': 'Удалено',
  'Delete failed': 'Не удалось удалить',
  'The agent is OFF for this product — no proactive messages are sent (the dialogue bot still answers players who write). Enable it in Settings → Retention bot → «Proactive agent» (dry-run stays on until you turn it off, so enabling is safe).':
    'Агент ВЫКЛЮЧЕН для этого продукта — проактивные сообщения не отправляются (диалоговый бот по-прежнему отвечает пишущим игрокам). Включите его в Настройки → Ретеншен-бот → «Проактивный агент» (dry-run остаётся включённым, пока вы его не выключите, так что включать безопасно).',
  'Delete ALL events for this product? The loss window and recent-activity state derived from them resets too.':
    'Удалить ВСЕ события этого продукта? Окно проигрыша и производное состояние недавней активности тоже сбросятся.',
  'Delete ALL decisions for this product? Today\'s budget counter and all same-event cooldowns reset.':
    'Удалить ВСЕ решения этого продукта? Счётчик дневного бюджета и все кулдауны одинаковых событий сбросятся.',

  // ----- retention: setup guide -----
  '1 · Create the bot': '1 · Создайте бота',
  'Open [@BotFather](https://t.me/BotFather) → `/newbot`, pick a name and a username, copy the **token**. Optionally set the description, about text and avatar there too. Menu commands are not needed — players enter only via a deeplink from the site.':
    'Откройте [@BotFather](https://t.me/BotFather) → `/newbot`, задайте имя и username, скопируйте **токен**. Там же при желании настройте описание, текст «о боте» и аватар. Команды меню не нужны — игроки попадают в бота только по deeplink с сайта.',
  '2 · Create the channel (subscription gate)': '2 · Создайте канал (проверка подписки)',
  'Create a Telegram **channel** and add the bot as a **channel administrator** — without admin rights the subscription check (`getChatMember`) fails and the gate never passes. Note the channel id (`@name` for public, `-100…` for private) and the channel URL (the gate\'s "open channel" button leads there).':
    'Создайте Telegram-**канал** и добавьте бота **администратором канала** — без прав администратора проверка подписки (`getChatMember`) не работает, и гейт никогда не пройдёт. Запишите id канала (`@name` для публичного, `-100…` для приватного) и URL канала (туда ведёт кнопка «открыть канал» на проверке подписки).',
  '3 · Deploy env (Railway)': '3 · Переменные окружения деплоя (Railway)',
  'Set on the service (not per product): `PUBLIC_BASE_URL` (public address, used to build the webhook URL), `TELEGRAM_WEBHOOK_SECRET` (random string, verified in the webhook header), `RETENTION_MEDIA_DIR` (mount path of an attached **Volume**, so photos survive redeploys) and `SECRETS_MASTER_KEY` (encrypts product secrets). The full env table is in the repo\'s README.':
    'Задайте на сервисе (не на продукте): `PUBLIC_BASE_URL` (публичный адрес, из него строится URL вебхука), `TELEGRAM_WEBHOOK_SECRET` (случайная строка, проверяется в заголовке вебхука), `RETENTION_MEDIA_DIR` (путь монтирования подключённого **Volume**, чтобы фото переживали редеплой) и `SECRETS_MASTER_KEY` (шифрует секреты продуктов). Полная таблица переменных — в README репозитория.',
  '4 · Connect this product': '4 · Подключите этот продукт',
  'On the [Telegram config](#/retention?tab=config) tab: switch on **Retention bot enabled**, fill the bot username, channel id and channel URL → **Save config**. In **Secrets** paste the bot token (and the Player API key, if the casino exposes a profile endpoint) → **Save secrets**. Then press **Register Telegram webhook** — it must report the webhook URL back.':
    'На вкладке [Настройка Telegram](#/retention?tab=config): включите **Ретеншен-бот включён**, заполните username бота, id канала и URL канала → **Сохранить настройки**. В блоке **Секреты** вставьте токен бота (и ключ Player API, если казино отдаёт эндпоинт профиля) → **Сохранить секреты**. Затем нажмите **Зарегистрировать вебхук Telegram** — в ответ должен вернуться URL вебхука.',
  '5 · Content and tuning': '5 · Контент и настройка',
  'Review the [Retention KB](#/retention?tab=kb) (one text document — what Nika may offer and talk about; a generic English starter is pre-filled, replace it with the brand\'s own), tune the Telegram persona in [Prompt variables](#/retention?tab=variables) (name/role/tone — empty fields use the built-in retention defaults), upload photos in [Media](#/retention?tab=photos) (bulk upload, then select them and press **Generate metadata** to have the AI fill the description, tags, `stage` = explicitness and `level_min` = VIP tier) and add live [Managers](#/retention?tab=managers) (round-robin, sticky). Thresholds (daily photo cap, stage progression, VIP tiers, nonce TTL) are the `retention` group in [Settings](#/settings?module=retention); bot texts are the `rtn_*` keys in [Translations](#/translations).':
    'Проверьте [Базу знаний бота](#/retention?tab=kb) (один текстовый документ — что Ника может предлагать и о чём говорить; общий английский стартовый текст уже заполнен, замените его контентом бренда), настройте Telegram-персону в [Переменных промпта](#/retention?tab=variables) (имя/роль/тон — пустые поля используют встроенные значения ретеншена), загрузите фото в [Медиа](#/retention?tab=photos) (массовая загрузка, затем выделите их и нажмите **Сгенерировать метаданные** — AI заполнит описание, теги, `stage` = откровенность и `level_min` = VIP-уровень) и добавьте живых [Менеджеров](#/retention?tab=managers) (round-robin, закрепляются за игроком). Пороги (дневной лимит фото, прогрессия стадий, VIP-уровни, TTL нонса) — группа `retention` в [Настройках](#/settings?module=retention); тексты бота — ключи `rtn_*` в [Переводах](#/translations).',
  '6 · Entry points': '6 · Точки входа',
  'Nothing extra to integrate for the main path: once the bot is enabled, the support widget\'s **escalation button** automatically routes the player into the bot (one-time deeplink, subscription gate on the way in, "go to a manager" in the menu). Optionally the site can mint its own per-player deeplink via `POST /api/retention/deeplink` — the full contract (handshake signing, profile pull/push) is documented at [/integration-telegram](/integration-telegram).':
    'Для основного пути ничего дополнительно интегрировать не нужно: как только бот включён, **кнопка эскалации** виджета поддержки автоматически ведёт игрока в бота (одноразовый deeplink, проверка подписки на входе, «перейти к менеджеру» в меню). Дополнительно сайт может выпускать собственный deeplink на игрока через `POST /api/retention/deeplink` — полный контракт (подпись handshake, pull/push профиля) описан на [/integration-telegram](/integration-telegram).',
  'Quick test: open the deeplink → pass the channel gate → chat with Nika → ask for a photo → it arrives; write "my account is blocked" → she routes you out instead of answering support questions herself.':
    'Быстрый тест: откройте deeplink → пройдите проверку подписки на канал → пообщайтесь с Никой → попросите фото → оно приходит; напишите «мой аккаунт заблокирован» → она перенаправит вас к поддержке, а не станет сама отвечать на вопросы поддержки.',

  // ----- retention: telegram config -----
  'Webhook URL:': 'URL вебхука:',
  'Telegram bot token': 'Токен Telegram-бота',
  'Player API key': 'Ключ Player API',
  'Clear {label}? It falls back to the deploy env value.': 'Очистить {label}? Значение вернётся к переменной окружения деплоя.',
  '{label} cleared': '{label} — очищено',
  'Webhook registered:': 'Вебхук зарегистрирован:',
  'Webhook registration failed': 'Не удалось зарегистрировать вебхук',
  'Clear failed': 'Не удалось очистить',
  'Create failed': 'Не удалось создать',
  'Upload failed': 'Не удалось загрузить файлы',

  // ----- retention: KB / prompt variables / prompt preview -----
  'The whole retention knowledge base as one text (Layer 2 of the retention prompt — what Nika may offer and talk about in Telegram). Keep it in English: it is the most token-efficient language for the model, and Nika answers in the player\'s language regardless. `{placeholders}` are substituted from KB variables.':
    'Вся база знаний ретеншена одним текстом (слой 2 промпта ретеншена — что Ника может предлагать и о чём говорить в Telegram). Держите её на английском: это самый экономный по токенам язык для модели, а Ника в любом случае отвечает на языке игрока. `{placeholders}` подставляются из переменных БЗ.',
  'These values uniquify the **Telegram retention persona** — a **separate prompt**, fully independent from the [support-chat prompt variables](#/prompt?tab=variables). An empty field **uses the built-in retention default** (shown as the placeholder); a support edit never leaks into the bot. Fill a field only where you want the Telegram persona to differ from that default.':
    'Эти значения уникализируют **Telegram-ретеншен-персону** — это **отдельный промпт**, полностью независимый от [переменных промпта чата поддержки](#/prompt?tab=variables). Пустое поле **использует встроенное значение ретеншена** (показано как плейсхолдер); правка в чате поддержки никогда не попадает в бота. Заполняйте поле только там, где Telegram-персона должна отличаться от этого значения.',
  'Empty = the built-in retention default.': 'Пусто = встроенное значение ретеншена.',
  'Total': 'Итого',
  'The complete retention prompt as the model receives it in the Telegram chat (read-only; language: {lang}). To change the wording, edit `prompts.py` and redeploy; the brand values are on the [Prompt variables](#/retention?tab=variables) tab.':
    'Полный промпт ретеншена в том виде, в каком его получает модель в Telegram-чате (только чтение; язык: {lang}). Чтобы изменить формулировки, отредактируйте `prompts.py` и передеплойте; брендовые значения — на вкладке [Переменные промпта](#/retention?tab=variables).',
  'System message (retention Layer 1 core + Layer 2 retention KB)':
    'Системное сообщение (слой 1 — ядро ретеншена + слой 2 — база знаний ретеншена)',
  'User message (Layer 3: profile, language, photo candidates, guardrails)':
    'Сообщение пользователя (слой 3: профиль, язык, фото-кандидаты, ограничители)',

  // ----- retention: media / photos -----
  'Pick any number of files at once. The fields below apply to every uploaded photo — leave them empty and use **Generate metadata** afterwards to have the AI fill the description, tags, explicitness stage and VIP level per photo.':
    'Выберите сразу любое количество файлов. Поля ниже применяются к каждому загруженному фото — оставьте их пустыми и после загрузки нажмите **Сгенерировать метаданные**, чтобы AI заполнил описание, теги, стадию откровенности и VIP-уровень для каждого фото.',
  'VIP tier to unlock': 'VIP-уровень для доступа',
  '1 = softest': '1 = самое мягкое',
  'Choose files': 'Выбрать файлы',
  '{n} files chosen': 'Выбрано файлов: {n}',
  '{n} photo(s) uploaded': 'Загружено фото: {n}',
  'active': 'активные',
  'inactive': 'неактивные',
  '{shown} of {total} photos': '{shown} из {total} фото',
  'Generate metadata for {n} photo(s)? The AI fills the description, tags, stage and VIP level; current values are overwritten.':
    'Сгенерировать метаданные для фото ({n} шт.)? AI заполнит описание, теги, стадию и VIP-уровень; текущие значения будут перезаписаны.',
  'Metadata: {ok} generated, {failed} failed': 'Метаданные: {ok} сгенерировано, {failed} с ошибкой',
  'Metadata generated for {n} photo(s)': 'Метаданные сгенерированы для {n} фото',
  'request failed': 'запрос не выполнен',
  "AI (the product's own model + API key) fills the description, tags, stage and minimum VIP level for every selected photo.":
    'AI (собственная модель и API-ключ продукта) заполняет описание, теги, стадию и минимальный VIP-уровень для каждого выбранного фото.',
  'No photos yet — upload the first ones above.': 'Фото пока нет — загрузите первые выше.',
  'stage': 'стадия',
  'level': 'уровень',
  'Description': 'Описание',
  'Level (min VIP)': 'Level (мин. VIP)',
  'Delete': 'Удалить',
  'cached in TG': 'кэшировано в TG',
  'Delete this photo?': 'Удалить это фото?',
  'of': 'из',
  'items': 'записей',
  'photos': 'фото',

  // ----- retention: managers -----
  'Username': 'Username',

  // ----- retention: conversations -----
  'Telegram chats with Nika, kept apart from the support-widget conversations. An idle chat closes automatically (the “Session idle (min)” knob in Settings → Retention bot); when the player returns, a new chat starts and Nika is shown the tail of the previous one for continuity. Click a row for the transcript.':
    'Telegram-чаты с Никой, отдельно от диалогов виджета поддержки. Неактивный чат закрывается автоматически (настройка «Неактивность чата (мин)» в Настройки → Ретеншен-бот); когда игрок возвращается, начинается новый чат, и Ника видит хвост предыдущего для непрерывности. Клик по строке открывает переписку.',
  'Delete selected': 'Удалить выбранные',
  'TG user': 'TG-пользователь',
  'Started': 'Начат',
  'Last activity': 'Последняя активность',
  'Delete {n} Telegram chats? This permanently removes their messages and logs AND purges each linked player (identity, seen photos, pings) from analytics.':
    'Удалить Telegram-чаты ({n} шт.)? Это безвозвратно удалит их сообщения и логи И вычистит каждого привязанного игрока (идентичность, просмотренные фото, пинги) из аналитики.',
  'Delete this Telegram chat? This permanently removes its messages and logs AND purges the linked player (identity, seen photos, pings) from analytics.':
    'Удалить этот Telegram-чат? Это безвозвратно удалит его сообщения и логи И вычистит привязанного игрока (идентичность, просмотренные фото, пинги) из аналитики.',
  '{n} chats deleted': 'Чатов удалено: {n}',
  'Chat deleted': 'Чат удалён',
  'No Telegram chats yet.': 'Telegram-чатов пока нет.',
  'chats': 'чатов',
  'Telegram chat': 'Telegram-чат',
  'proactive:': 'проактивно:',
  'Total cost:': 'Общая стоимость:',
  'Close': 'Закрыть',

  // ----- retention: analytics -----
  '{pct}% reply rate': '{pct}% ответов на пинги',
  'no pings in range': 'нет пингов за период',
  'Entry funnel': 'Воронка входа',
  'Stage distribution': 'Распределение по стадиям',
  'Players per unlocked photo stage (lifetime).': 'Игроков на каждой разблокированной фото-стадии (за всё время).',
  'Players': 'Игроки',
  'Photos': 'Фото',
  'Pings': 'Пинги',
  'Deeplinks minted': 'Создано deeplink-ссылок',
  '/start redemptions': 'Активации /start',
  'New linked players': 'Новые привязанные игроки',
  'Subscribed to channel': 'Подписались на канал',
  'Engaged (wrote a message)': 'Вовлечены (написали сообщение)',
  'Received a photo': 'Получили фото',
  'Handed off': 'Переданы менеджеру',
  'Entry': 'Вход',
  'VIP': 'VIP',
  'Manager': 'Менеджер',
  'Last active': 'Последняя активность',

  // ----- dashboard (charts / KPIs / retention block) -----
  'latest': 'последнее',
  'avg': 'среднее',
  'No data for the period.': 'Нет данных за период.',
  'subscribed': 'подписаны',
  'lifetime': 'за всё время',
  'reply rate': 'доля ответов',
  'TG turns + photo metadata': 'TG-диалоги + метаданные фото',
  'Messages & pings over time': 'Сообщения и пинги по дням',

  // ----- charts (Telegram cost panels / funnel) -----
  'Telegram cost over time': 'Стоимость Telegram по дням',
  'Telegram cost by source': 'Стоимость Telegram по источникам',
  'Engagement dialog vs on-demand photo-metadata generation.':
    'Диалоги с игроками против генерации метаданных фото по запросу.',
  'Dialog': 'Диалог',
  'Photo metadata': 'Метаданные фото',
  'of previous': 'от предыдущего',

  // ----- Users: memberships (role × scope) -----
  'Access (role × scope)': 'Доступ (роль × область)',
  'no access (no memberships)': 'нет доступа (нет членств)',
  'What this account may see and edit. Each row is one role over one scope: the whole hub (global), one partner (all its products), or a single product. Granting the same scope again replaces its role.':
    'Что этот аккаунт может видеть и редактировать. Каждая строка — одна роль над одной областью: весь хаб (глобально), один партнёр (все его продукты) или один продукт. Повторная выдача той же области заменяет её роль.',
  'This account has no memberships — it can log in but sees no data. Grant it a scope below.':
    'У этого аккаунта нет членств — он может войти, но не видит данных. Выдайте ему область ниже.',
  'Access granted': 'Доступ выдан',
  'Access revoked': 'Доступ отозван',
  'Revoke access': 'Отозвать доступ',
  'Revoke': 'Отозвать',
  'Grant access': 'Выдать доступ',
  'You may grant or revoke only scopes you hold an admin role over. You cannot change your own memberships.':
    'Выдавать и отзывать можно только области, где у вас роль администратора. Свои собственные членства менять нельзя.',
  'What the account may access: the whole hub, one partner (all its products), or a single product. More scopes can be added after creation on the edit page.':
    'К чему у аккаунта будет доступ: весь хаб, один партнёр (все его продукты) или один продукт. Дополнительные области можно выдать после создания на странице редактирования.',
  'admin may edit within the scope; manager is read-only.':
    'admin может редактировать в пределах области; manager — только чтение.',

  // ----- Media normalizer -----
  'Normalize media now': 'Нормализовать медиа сейчас',
  'Normalizing…': 'Нормализация…',
  'Media normalized: {n} converted, {f} failed, {mb} MB freed':
    'Медиа нормализовано: {n} сконвертировано, {f} с ошибкой, освобождено {mb} МБ',
  'Re-encodes heavy uploads (multi-MB JPG/PNG) to Telegram-sized WebP and deletes the originals. Runs automatically on a schedule — the button is the immediate run.':
    'Пережимает тяжёлые загрузки (многомегабайтные JPG/PNG) в WebP под размеры Telegram и удаляет оригиналы. Запускается автоматически по расписанию — кнопка выполняет проход немедленно.',

  // ----- API keys page -----
  'Global (everything)': 'Глобально (всё)',
  'Partner (all its products)': 'Партнёр (все его продукты)',
  'Single product': 'Один продукт',
  'Global': 'Глобально',
  'Service API keys are credentials — only admin accounts may view or manage them.':
    'Сервисные API-ключи — это учётные данные: просматривать их и управлять ими могут только администраторы.',
  "Service keys for machine consumers of the admin API (partner back-offices, BI, CI). A key behaves like an admin account with exactly one role × scope and is sent as `Authorization: Bearer sak_…`. The token is shown once at creation — store it in the consumer's secret store.":
    'Сервисные ключи для машинных потребителей админ-API (бэк-офисы партнёров, BI, CI). Ключ работает как админ-аккаунт ровно с одной парой роль × область и передаётся как `Authorization: Bearer sak_…`. Токен показывается один раз при создании — сохраните его в хранилище секретов потребителя.',
  'Create key': 'Создать ключ',
  'Create': 'Создать',
  'Give a key the narrowest scope that works — a read-only manager key per product for pulls, an admin key only when the consumer must write.':
    'Выдавайте ключу минимально достаточную область — manager-ключ (только чтение) на продукт для выгрузок, admin-ключ только когда потребитель должен писать.',
  'Token': 'Токен',
  'Last used': 'Последнее использование',
  'never': 'никогда',
  'Delete the key': 'Удалить ключ',
  'Consumers using it stop working immediately.': 'Потребители, использующие его, сразу перестанут работать.',
  'Key deleted': 'Ключ удалён',
  'Token copied': 'Токен скопирован',
  'No API keys yet — create the first one above.': 'API-ключей пока нет — создайте первый выше.',
  'Key created — copy the token now': 'Ключ создан — скопируйте токен сейчас',
  "This token is shown ONCE and cannot be recovered. Copy it into the consumer's secret store before closing; if it is lost, delete the key and mint a new one.":
    'Этот токен показывается ОДИН раз, восстановить его нельзя. Скопируйте его в хранилище секретов потребителя до закрытия окна; если токен утерян — удалите ключ и создайте новый.',
  'Copy token': 'Скопировать токен',
  'Done': 'Готово',

  // ----- login -----
  'Email': 'Email',

  // ----- prompt preview -----
  'The complete prompt as the model receives it (read-only; example topic:':
    'Полный промпт в том виде, в каком его получает модель (только чтение; пример темы:',
  'language:': 'язык:',
  'To change the wording, edit `prompts.py` and redeploy; the brand values are on the Prompt variables page.':
    'Чтобы изменить формулировки, отредактируйте `prompts.py` и передеплойте; брендовые значения — на странице «Переменные промпта».',
  'System message (Layer 1 core + directives + Layer 2 KB)':
    'Системное сообщение (ядро слоя 1 + директивы + БЗ слоя 2)',
  'User message (Layer 3 dynamic directives)': 'Сообщение пользователя (динамические директивы слоя 3)',

  // ----- prompt variables -----
  'Prompt variables saved': 'Переменные промпта сохранены',
  'Escalation keywords saved': 'Ключевые слова эскалации сохранены',
  'Test profile saved': 'Тестовый профиль сохранён',
  'Brand values substituted into the shared prompt template. Empty values fall back to the built-in defaults. The prompt wording itself is edited in `prompts.py` (see the read-only Prompt preview page). The Telegram retention persona has its own variables in [Telegram · Retention → Prompt variables](#/retention?tab=variables) — a separate prompt: empty retention fields fall back to the built-in retention defaults, never to these support values.':
    'Брендовые значения, подставляемые в общий шаблон промпта. Пустые значения возвращаются к встроенным. Сами формулировки промпта редактируются в `prompts.py` (см. страницу просмотра промпта, только чтение). У Telegram-персонажа ретеншена свои переменные в [Telegram · Ретеншен → Переменные промпта](#/retention?tab=variables) — это отдельный промпт: пустые поля ретеншена падают на встроенные значения ретеншена и никогда — на эти значения поддержки.',
  'Escalation keyword lists': 'Списки ключевых слов эскалации',
  "One entry per line; multilingual stems scan the player's raw message before the model call (soft hand-off, no tokens burned).":
    'По одной записи на строку; многоязычные основы сканируют исходное сообщение игрока до вызова модели (мягкая передача, токены не тратятся).',
  'High-risk keywords (fraud / legal)': 'Ключевые слова высокого риска (мошенничество / юридические)',
  'Human-request keywords': 'Ключевые слова запроса живого оператора',
  'Save keywords': 'Сохранить ключевые слова',
  'Test player profile': 'Тестовый профиль игрока',
  "A handshake secret is configured — the host site supplies the player context, so this test profile is ignored at session create. To use this profile instead, clear the product's [Widget handshake secret in Structure](#/structure) (use its Clear button). A deploy-wide `WIDGET_HANDSHAKE_SECRET` env value can only be removed in Railway.":
    'Настроен handshake-секрет — контекст игрока передаёт сайт, поэтому при создании сессии этот тестовый профиль игнорируется. Чтобы использовать профиль, очистите [handshake-секрет виджета в «Структуре»](#/structure) (кнопка «Очистить»). Env-значение `WIDGET_HANDSHAKE_SECRET` уровня деплоя убирается только в Railway.',
  'Enabled (used when no handshake secret is set)': 'Включён (используется, когда handshake-секрет не задан)',
  'Save test profile': 'Сохранить тестовый профиль',

  // ----- site map -----
  'Cashier': 'Касса',
  'where players top up their balance': 'где игроки пополняют баланс',

  // ----- structure -----
  'OpenAI key (primary)': 'Ключ OpenAI (основной)',
  'OpenAI key (fallback)': 'Ключ OpenAI (резервный)',
  'Widget handshake secret': 'Handshake-секрет виджета',
  'Turnstile secret key': 'Секретный ключ Turnstile',
  'Product saved': 'Продукт сохранён',
  'Rotate the widget key? Old embeds stop working immediately.':
    'Сменить ключ виджета? Старые вставки на сайтах сразу перестанут работать.',
  'Widget key rotated': 'Ключ виджета сменён',
  'Rotate failed': 'Не удалось сменить ключ',
  'Nothing to update': 'Нечего обновлять',
  'Secrets saved (write-only; never shown back)': 'Секреты сохранены (только запись; обратно не показываются)',
  'Clear': 'Очистить',
  'It falls back to the deploy env value.': 'Значение вернётся к env-переменной деплоя.',
  'cleared': 'очищено',
  'Rename': 'Переименовать',
  'Widget key & embed': 'Ключ виджета и код вставки',
  'Widget key copied': 'Ключ виджета скопирован',
  'Embed snippet copied': 'Код для вставки скопирован',
  'Copy embed snippet': 'Скопировать код вставки',
  'Rotate key': 'Сменить ключ',
  "Each client domain runs its own Turnstile widget (create it as an Invisible widget in the Cloudflare dashboard) — set that widget's site key here (the secret key goes into Secrets below). Leave empty to fall back to the deploy env keys. Verification is advisory: if Turnstile is blocked or unreachable for a player, the check is skipped and the other anti-spam layers still apply.":
    'На каждом клиентском домене работает свой виджет Turnstile (создайте его в панели Cloudflare как Invisible) — укажите здесь site key этого виджета (секретный ключ вносится в «Секреты» ниже). Пустое поле — используются env-ключи деплоя. Проверка рекомендательная: если Turnstile у игрока заблокирован или недоступен, проверка пропускается, а остальные уровни антиспама продолжают работать.',
  'Save site key': 'Сохранить site key',
  "Telegram hand-off 'support on the site' button lands here":
    'Сюда ведёт кнопка «поддержка на сайте» при передаче из Telegram',
  'Save site URL': 'Сохранить URL сайта',
  'Write-only (encrypted at rest). A green check means a value is configured. Leave a field untouched to keep it; use Clear to remove it (fall back to env).':
    'Только запись (хранятся в зашифрованном виде). Зелёная галочка — значение задано. Не трогайте поле, чтобы оставить его как есть; «Очистить» удаляет значение (возврат к env).',
  'Product created (seeded with the starter KB)': 'Продукт создан (заполнен стартовой базой знаний)',
  'Add product': 'Добавить продукт',
  'Partner saved': 'Партнёр сохранён',
  'Partner created': 'Партнёр создан',
  'Add partner': 'Добавить партнёра',

  // ----- conversations / escalations -----
  'escalated': 'эскалирована',
  'switched': 'переключение',
  'proactive': 'проактивно',

  // ----- knowledge base -----
  'order': 'порядок',
  'KB': 'БЗ',
  'Title (en)': 'Название (en)',
  'Order': 'Порядок',
  'Has KB': 'Есть БЗ',

  // ----- KB variables -----
  'Key': 'Ключ',
  'Value': 'Значение',
  'Updated': 'Обновлено',
  'Updated by': 'Кем обновлено',

  // ----- components -----
  'Set': 'Задано',
  'Not set': 'Не задано',
  '1M input tokens': '1 млн входных токенов',

  // ----- misc chrome (were wrapped but missing) -----
  'Profile': 'Профиль',
  'Level': 'Уровень',
  'Edit': 'Изменить',
  'Cancel': 'Отмена',
  'Copy widget key': 'Копировать ключ виджета',
  'partner #{id}': 'партнёр #{id}',
  'product #{id}': 'продукт #{id}',

  // ----- settings schema: fields missing from the dict (translated at render) -----
  'Media normalization (photo storage)': 'Нормализация медиа (хранение фото)',
  'Play reminder every ~N replies': 'Приглашение играть примерно каждые N ответов',
  'Roughly every N-th of Nika’s Telegram replies weaves in a light in-context invitation to play, with a one-tap site button picked from the Site map (0 = off). The actual cadence drifts ±2 around N (…after 3, then 7, then 5…) so the pattern can’t be clocked.':
    'Примерно каждый N-й ответ Ники в Telegram содержит лёгкое приглашение поиграть с кнопкой сайта из Карты сайта в один тап (0 = выкл). Реальный интервал плавает ±2 вокруг N (…через 3, потом 7, потом 5…), чтобы закономерность нельзя было вычислить.',
  'Max messages per reply (burst)': 'Макс. сообщений на ответ (серия)',
  'A reply with blank lines is delivered as a burst of separate Telegram messages (with a typing pause between them). This caps the burst; longer replies collapse into the last message. 1 = always one message.':
    'Ответ с пустыми строками отправляется серией отдельных сообщений в Telegram (с паузой «печатает» между ними). Этот параметр ограничивает серию; лишнее сваливается в последнее сообщение. 1 = всегда одно сообщение.',
  'Auto-normalize uploaded photos': 'Авто-нормализация загруженных фото',
  'The periodic sweep re-encodes heavy uploads (multi-MB JPG/PNG) to WebP at Telegram-appropriate dimensions and DELETES the heavy originals — Telegram re-compresses photos anyway, so the originals only burn storage. GIFs are left alone.':
    'Периодический проход пережимает тяжёлые загрузки (JPG/PNG в несколько МБ) в WebP под размеры Telegram и УДАЛЯЕТ тяжёлые оригиналы — Telegram всё равно пережимает фото, так что оригиналы только занимают место. GIF не трогаются.',
  'Normalize sweep interval (sec)': 'Интервал прохода нормализации (сек)',
  'How often the media sweep runs — ONE loop serves every product, so this is a deploy-wide (global) setting. Default 3600 (hourly). The «Normalize now» button on the Media tab runs one product immediately.':
    'Как часто выполняется проход нормализации — ОДИН цикл обслуживает все продукты, поэтому это общесистемная (глобальная) настройка. По умолчанию 3600 (раз в час). Кнопка «Нормализовать сейчас» на вкладке «Медиа» запускает один продукт немедленно.',
  'Max photo side (px)': 'Макс. сторона фото (px)',
  'Longest side after normalization. Telegram re-compresses photos to ~2560 px anyway, so 2048 keeps full delivered quality at a fraction of the size.':
    'Самая длинная сторона после нормализации. Telegram всё равно пережимает фото до ~2560 px, так что 2048 сохраняет полное качество доставки при меньшем размере.',
  'WebP quality (40–100)': 'Качество WebP (40–100)',
  'Compression quality of the normalized WebP. 82 is visually lossless for chat photos; raise it only if you see artifacts.':
    'Качество сжатия нормализованного WebP. 82 визуально без потерь для чат-фото; повышайте только если видите артефакты.',
  'Idle re-engagement pings': 'Пинги возврата неактивных',
  'The agent’s inactivity trigger: the Idle pings rules ladder («quiet N days → Nika writes first», Retention → Idle pings tab). Off = the agent reacts to casino events only; a quiet player is never written to.':
    'Триггер неактивности агента: лестница правил пингов («тишина N дней → Ника пишет первой», Ретеншен → Пинги неактивности). Выкл = агент реагирует только на события казино; замолчавшему игроку никто не пишет.',
  'Idle rules sweep interval (sec)': 'Интервал прохода правил неактивности (сек)',
  'How often the idle-rules ladder is re-evaluated per product. The rules move on a scale of days, so the default (600 = 10 min) is plenty; «Run now» on the Idle pings tab bypasses it.':
    'Как часто пересчитывается лестница правил неактивности по продукту. Правила работают в масштабе дней, поэтому по умолчанию (600 = 10 мин) более чем достаточно; «Запустить сейчас» на вкладке пингов обходит интервал.',
  'Send delay, min (seconds)': 'Задержка отправки, мин (сек)',
  'A proactive reaction goes out a RANDOM delay after the event, never instantly — an instant thank-you after a deposit reads as surveillance. Default 300 (5 min); each event gets its own delay between min and max. «Process queue now» bypasses the delay.':
    'Проактивная реакция уходит через СЛУЧАЙНУЮ задержку после события, никогда мгновенно — мгновенное спасибо после депозита выглядит как слежка. По умолчанию 300 (5 мин); каждое событие получает свою задержку между мин и макс. «Обработать очередь» обходит задержку.',
  'Send delay, max (seconds)': 'Задержка отправки, макс (сек)',
  'Upper bound of the random per-event send delay. Default 900 (15 min) — so reactions land 5–15 minutes after the event, ~10 on average. Set min = max for an exact delay; both 0 = react immediately.':
    'Верхняя граница случайной задержки отправки на событие. По умолчанию 900 (15 мин) — реакции приходят через 5–15 минут после события, ~10 в среднем. Задайте мин = макс для точной задержки; оба 0 = реагировать сразу.',
  'Level-up congratulation message': 'Сообщение-поздравление с новой стадией',
  'When a player actually unlocks the next photo stage, Nika follows up with a short celebratory note: you two got closer, more daring photos from now on, keep chatting to unlock even more. Persisted with its trigger, so she can later explain what the message was about.':
    'Когда игрок реально разблокирует следующую фото-стадию, Ника отправляет короткое поздравление: вы стали ближе, дальше — более смелые фото, продолжай общаться, чтобы открыть ещё. Сохраняется с триггером, поэтому позже она может объяснить, о чём было сообщение.',
  'How often the background worker drains the event queue — ONE loop serves every product, so this is a deploy-wide (global) setting. Applies live on the next tick (no redeploy).':
    'Как часто фоновый воркер разбирает очередь событий — ОДИН цикл обслуживает все продукты, поэтому это общесистемная (глобальная) настройка. Применяется сразу со следующего тика (без редеплоя).',

  // ----- scope switcher -----
  'All products': 'Все продукты',
  'all products': 'все продукты',
};

const current = getAdminLang();

/** Translate an English source string; falls back to the source. */
export const t = (s) => (current === 'ru' && RU[s]) || s;

export default t;
