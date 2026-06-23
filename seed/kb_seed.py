"""Seed the 6 visible topics + hidden 'other', with placeholder Russian KB.

Seed-once / NON-DESTRUCTIVE bootstrap (the seed contract — see CLAUDE.md): this
creates the built-in topics and their placeholder KB ONLY when they are missing.
A topic that already exists keeps its owner-edited title/order/active, and a topic
that already has any KB (active or archived) is never re-seeded — so admin-panel
edits survive every redeploy. Earlier this clobbered the live KB on each restart
(it deactivated all entries and re-inserted the placeholder at version 1), silently
wiping the owner's changes; never reintroduce that behaviour.

ALL player-facing numbers are {{PLACEHOLDER}} tokens — the owner replaces them
with real values later. Do NOT fabricate concrete numbers here.
"""
from __future__ import annotations

import db

# Each topic: slug, multilingual title, and a compact placeholder KB chunk (ru).
TOPICS: list[dict] = [
    {
        "slug": "deposits",
        "order": 1,
        "title": {
            "ru": "Депозиты", "en": "Deposits", "es": "Depósitos",
            "tr": "Para Yatırma", "pt": "Depósitos",
        },
        "kb": """[Депозиты — база знаний]
Платёж не прошёл: попроси игрока проверить, что метод поддерживается в его регионе,
что данные карты/реквизиты введены верно, и что не превышен лимит {{DEPOSIT_LIMIT}}.
Если деньги списались, но не зачислились в течение {{DEPOSIT_CREDIT_TIME}}, нужна
эскалация с указанием суммы, метода и времени операции.
Крипто-депозиты ({{SUPPORTED_CHAINS}}): средства зачисляются после {{CONFIRMATIONS}}
подтверждений сети. Если адрес/сеть выбраны неверно — это эскалация.
Никогда не запрашивай у игрока полный номер карты, CVV, пароль или seed-фразу.""",
    },
    {
        "slug": "withdrawals",
        "order": 2,
        "title": {
            "ru": "Выводы", "en": "Withdrawals", "es": "Retiros",
            "tr": "Para Çekme", "pt": "Saques",
        },
        "kb": """[Выводы — база знаний]
Статус "в обработке": стандартный срок вывода — {{WITHDRAWAL_TIME}}. Если срок
превышен, нужна эскалация с указанием суммы и метода.
Лимиты вывода: минимальная сумма {{MIN_WITHDRAWAL}}, максимум за период
{{MAX_WITHDRAWAL}}. Перед первым выводом может требоваться верификация (KYC).
Крипто-вывод: убедись, что игрок указал корректный адрес и сеть
({{SUPPORTED_CHAINS}}). Средства на неверный адрес вернуть нельзя — это эскалация.
Никогда не запрашивай у игрока приватные ключи или seed-фразу кошелька.""",
    },
    {
        "slug": "account_kyc",
        "order": 3,
        "title": {
            "ru": "Аккаунт и верификация", "en": "Account & verification",
            "es": "Cuenta y verificación", "tr": "Hesap ve doğrulama",
            "pt": "Conta e verificação",
        },
        "kb": """[Аккаунт и верификация — база знаний]
Проблемы со входом: предложи восстановление пароля по email. Никогда не проси
игрока прислать пароль в чат.
Верификация (KYC) проходит через Sumsub. Обычно нужны документ, удостоверяющий
личность, и подтверждение адреса. Срок проверки — {{KYC_REVIEW_TIME}}.
Если документы отклонены повторно или верификация "зависла" дольше
{{KYC_REVIEW_TIME}} — эскалация.
Не запрашивай и не храни в чате полные паспортные данные сверх необходимого.""",
    },
    {
        "slug": "bonuses",
        "order": 4,
        "title": {
            "ru": "Бонусы и промо", "en": "Bonuses & promotions",
            "es": "Bonos y promociones", "tr": "Bonuslar ve promosyonlar",
            "pt": "Bônus e promoções",
        },
        "kb": """[Бонусы и промо — база знаний]
Условие отыгрыша (вейджер): бонус нужно отыграть x{{WAGER_MULTIPLIER}} в течение
{{BONUS_VALID_PERIOD}}. Объясни прозрачно, без обещаний сверх правил.
Бонус не начислился: проверь, был ли активирован промокод до депозита и выполнены
ли условия акции. Если условия выполнены, а бонус не пришёл — эскалация.
Программа лояльности: уровни и кэшбэк описаны в правилах акций. Конкретные числа —
{{LOYALTY_DETAILS}}.
Не обещай индивидуальных бонусов и не выдумывай условия, которых нет в правилах.""",
    },
    {
        "slug": "betting_games",
        "order": 5,
        "title": {
            "ru": "Ставки и игры", "en": "Betting & games",
            "es": "Apuestas y juegos", "tr": "Bahis ve oyunlar",
            "pt": "Apostas e jogos",
        },
        "kb": """[Ставки и игры — база знаний]
Игра зависла / прервалась: попроси игрока обновить страницу. Раунд обычно
восстанавливается провайдером; результат сверяется с историей игры.
Ставка не рассчитана: расчёт по событию происходит после официального результата,
обычно в течение {{SETTLEMENT_TIME}}. Если результат известен, а ставка висит —
эскалация с указанием события и купона.
Спорные результаты и история раундов проверяются по логам провайдера — при
сомнениях это эскалация.
Не гарантируй выигрыш и не комментируй вероятности исходов как советы по ставкам.""",
    },
    {
        "slug": "technical",
        "order": 6,
        "title": {
            "ru": "Технические проблемы", "en": "Technical issues",
            "es": "Problemas técnicos", "tr": "Teknik sorunlar",
            "pt": "Problemas técnicos",
        },
        "kb": """[Технические проблемы — база знаний]
Сайт не загружается / ошибки: предложи базовые шаги — обновить страницу, очистить
кэш, сменить браузер или сеть, попробовать другое устройство.
Если проблема воспроизводится у многих или после базовых шагов сохраняется —
эскалация с указанием устройства, браузера, времени и текста ошибки.
Медленная работа: уточни регион и тип подключения; зеркала/домены —
{{ALT_DOMAINS}}.
Не проси игрока отключать защиту аккаунта или сообщать коды двухфакторной
аутентификации.""",
    },
    {
        "slug": "other",
        "order": 99,
        "hidden": True,
        "title": {
            "ru": "Другое", "en": "Other", "es": "Otro",
            "tr": "Diğer", "pt": "Outro",
        },
        "kb": """[Другое — база знаний]
Свободный запрос без явной темы. Постарайся понять суть; если вопрос выходит за
рамки базы знаний или содержит жалобу/претензию — приоритетная эскалация.
Не выдумывай факты. Если не уверен — честно скажи и предложи связаться с
поддержкой.""",
    },
]


async def run() -> None:
    """Create the built-in topics + placeholder KB, but only where missing.

    Non-destructive: never overwrites an existing topic's metadata or KB, so the
    owner's admin-panel edits are preserved across restarts/redeploys.
    """
    for t in TOPICS:
        existing = await db.get_topic_by_slug(t["slug"])
        if existing is None:
            topic_id = await db.upsert_topic(
                slug=t["slug"],
                title=t["title"],
                display_order=t["order"],
                active=True,  # 'other' stays active but is filtered from the picker
            )
        else:
            # Topic already there — leave owner-edited title/order/active alone.
            topic_id = existing["id"]
        # Seed the placeholder KB only when this topic has no entry at all (active
        # or archived). Once any content has existed, the seed keeps its hands off.
        if not await db.list_kb_entries(topic_id, include_inactive=True):
            await db.create_kb_entry(topic_id, lang="ru", content=t["kb"])
