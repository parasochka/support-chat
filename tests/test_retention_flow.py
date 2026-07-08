"""Flow-level tests for retention.handle_update with a fake Telegram + fake DB.

Exercises the wiring: /start nonce redemption, the subscription gate, the entry
menu, the callback actions, and a Nika text turn that sends a photo. DB helpers
and the model call are monkeypatched; the assertions are on what the bot sends.
"""
from __future__ import annotations

import chat_service
import retention


class FakeTelegram:
    def __init__(self, *a, **k):
        self.messages = []       # (chat_id, text, reply_markup)
        self.photos = []         # (chat_id, file_id, caption)
        self.answered = []
        self.subscribed = True   # what is_subscribed returns

    async def send_message(self, chat_id, text, *, reply_markup=None, parse_mode=None):
        self.messages.append((chat_id, text, reply_markup))
        return {"message_id": 1}

    async def send_photo_file_id(self, chat_id, file_id, *, caption=None, parse_mode=None):
        self.photos.append((chat_id, file_id, caption))
        return {"ok": True}

    async def send_photo_bytes(self, chat_id, content, filename, *, caption=None, parse_mode=None):
        self.photos.append((chat_id, "uploaded", caption))
        return {"photo": [{"file_id": "newid"}]}

    async def answer_callback(self, cb_id, text=None):
        self.answered.append(cb_id)

    async def is_subscribed(self, chat_id, user_id):
        return self.subscribed


def _patch_common(monkeypatch, tg):
    async def _token(pid):
        return "bot-token"
    monkeypatch.setattr(retention.db, "get_product_telegram_token", _token)
    monkeypatch.setattr(retention, "TelegramClient", lambda *a, **k: tg)

    async def _set_conv_lang(rid, lang):
        pass
    monkeypatch.setattr(retention.db, "set_retention_conv_lang", _set_conv_lang)

    async def _log_event(*a, **k):
        pass
    monkeypatch.setattr(retention.db, "log_admin_event", _log_event)


PRODUCT = {"id": 1, "active": True, "retention_enabled": True,
           "telegram_bot_username": "nika_bot", "telegram_channel_id": None,
           "telegram_channel_url": "https://t.me/chan"}


async def test_start_without_valid_nonce(monkeypatch):
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    async def _redeem(nonce):
        return None
    monkeypatch.setattr(retention.db, "redeem_retention_nonce", _redeem)

    # Unknown player (never linked) + no usable nonce -> "open from the site".
    async def _get_ru(pid, uid):
        return None
    monkeypatch.setattr(retention.db, "get_retention_user", _get_ru)

    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7}, "chat": {"id": 7}, "text": "/start bad"}})
    assert tg.messages, "expected a message"
    assert "site" in tg.messages[0][1].lower() or "сайт" in tg.messages[0][1].lower()


async def test_start_known_player_without_payload_reopens_menu(monkeypatch):
    """Telegram often drops the deeplink payload on the native START re-tap of an
    existing chat (bare `/start`), and the nonce is single-use + short-TTL. An
    already-linked player must NOT be dead-ended on "open from the site" — the
    subscription gate re-runs and the menu re-opens."""
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    async def _redeem(nonce):
        return None  # no/expired/used nonce (payload didn't come through)
    monkeypatch.setattr(retention.db, "redeem_retention_nonce", _redeem)

    async def _get_ru(pid, uid):
        return {"id": 10, "tg_user_id": uid, "entry_type": "escalation",
                "conv_lang": None, "full_name": "Andrey"}
    monkeypatch.setattr(retention.db, "get_retention_user", _get_ru)

    async def _sub(rid, val):
        pass
    monkeypatch.setattr(retention.db, "set_retention_subscribed", _sub)

    # Bare `/start` (payload stripped by the Telegram client).
    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7, "username": "andr"}, "chat": {"id": 7},
        "text": "/start"}})

    assert tg.messages, "menu expected"
    text = tg.messages[-1][1]
    assert "Andrey" in text and "Nika" in text
    markup = tg.messages[-1][2]
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert any("manager" in l.lower() or "менеджер" in l.lower() for l in labels)


async def test_start_valid_nonce_shows_menu(monkeypatch):
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    async def _redeem(nonce):
        return {"product_id": 1, "payload": {"id": "p9", "full_name": "Andrey",
                "vip_level": "Gold"}, "escalation": True}
    monkeypatch.setattr(retention.db, "redeem_retention_nonce", _redeem)

    captured = {}

    async def _upsert(product_id, tg_user_id, **kw):
        captured.update(kw)
        return {"id": 10, "tg_user_id": tg_user_id, "entry_type": kw["entry_type"],
                "conv_lang": None, "full_name": kw["profile"].get("full_name")}
    monkeypatch.setattr(retention.db, "upsert_retention_user", _upsert)

    async def _sub(rid, val):
        pass
    monkeypatch.setattr(retention.db, "set_retention_subscribed", _sub)

    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7, "username": "andr"}, "chat": {"id": 7},
        "text": "/start goodnonce"}})

    # escalation entry -> the manager button is offered alongside Nika
    assert captured["entry_type"] == "escalation"
    assert tg.messages, "menu expected"
    # the menu opens with a personalized persona greeting (first name only)
    text = tg.messages[-1][1]
    assert "Andrey" in text and "Nika" in text
    markup = tg.messages[-1][2]
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert any("manager" in l.lower() or "менеджер" in l.lower() for l in labels)
    assert any("nika" in l.lower() or "ник" in l.lower() for l in labels)


async def test_start_adopts_deeplink_language(monkeypatch):
    """A nonce minted from a Russian conversation opens the bot in Russian: the
    payload's `lang` is persisted as the retention user's conv_lang and the menu
    is localized to it (not to the Telegram client language)."""
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    async def _redeem(nonce):
        return {"product_id": 1, "payload": {"id": "p9", "full_name": "Андрей",
                "lang": "ru"}, "escalation": False}
    monkeypatch.setattr(retention.db, "redeem_retention_nonce", _redeem)

    async def _upsert(product_id, tg_user_id, **kw):
        return {"id": 10, "tg_user_id": tg_user_id, "entry_type": kw["entry_type"],
                "conv_lang": None, "full_name": "Андрей Иванов"}
    monkeypatch.setattr(retention.db, "upsert_retention_user", _upsert)

    saved = {}

    async def _set_conv_lang(rid, lang):
        saved["lang"] = lang
    monkeypatch.setattr(retention.db, "set_retention_conv_lang", _set_conv_lang)

    async def _sub(rid, val):
        pass
    monkeypatch.setattr(retention.db, "set_retention_subscribed", _sub)

    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7, "language_code": "en"}, "chat": {"id": 7},
        "text": "/start goodnonce"}})

    assert saved["lang"] == "ru"
    text = tg.messages[-1][1]
    assert "Привет" in text and "Андрей" in text  # RU greeting, first name only
    assert "Иванов" not in text


async def test_callback_nika_starts_chat(monkeypatch):
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    async def _get_ru(pid, tg_id):
        return {"id": 10, "tg_user_id": tg_id, "entry_type": "retention",
                "conv_lang": None, "subscribed": True}
    monkeypatch.setattr(retention.db, "get_retention_user", _get_ru)

    await retention.handle_update(PRODUCT, {"callback_query": {
        "id": "cb", "from": {"id": 7}, "data": retention.CB_MENU_NIKA,
        "message": {"chat": {"id": 7}}}})
    assert tg.answered == ["cb"]
    assert tg.messages, "a Nika-start line expected"


async def test_nika_turn_sends_photo(monkeypatch):
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)

    ru = {"id": 10, "tg_user_id": 7, "entry_type": "retention", "conv_lang": None,
          "subscribed": True, "vip_level": "Gold", "unlocked_stage": 2,
          "photos_sent_today": 0, "photos_day": None, "msgs_since_photo": 9,
          "meaningful_msgs": 3, "player_id": "p9", "session_id": "sess-1"}

    async def _get_ru(pid, tg_id):
        return dict(ru)
    monkeypatch.setattr(retention.db, "get_retention_user", _get_ru)

    async def _get_session(sid):
        return {"id": "sess-1", "product_id": 1, "user_context": {}, "lang": "en",
                "conv_lang": None, "message_count": 0, "status": "open"}
    monkeypatch.setattr(retention.db, "get_session", _get_session)

    async def _bump(rid, *, meaningful):
        return dict(ru)
    monkeypatch.setattr(retention.db, "bump_retention_activity", _bump)

    async def _candidates(pid, rid, **kw):
        return [{"id": 55, "stage": 2, "description": "beach", "tags": []}]
    monkeypatch.setattr(retention.db, "candidate_photos", _candidates)

    async def _get_photo(pid):
        return {"id": 55, "active": True, "telegram_file_id": "cachedfile",
                "storage_ref": "x.jpg"}
    monkeypatch.setattr(retention.db, "get_retention_photo", _get_photo)

    async def _record(rid, photo_id, product_id, session_id=None):
        pass
    monkeypatch.setattr(retention.db, "record_retention_photo_view", _record)

    async def _advance(rid, stage):
        pass
    monkeypatch.setattr(retention.db, "advance_retention_stage", _advance)

    # The model "sends" photo 55 with a caption.
    async def _handle(session, text, candidates):
        assert candidates and candidates[0]["id"] == 55
        return chat_service.RetentionReply(
            reply="here you go", lang="en", message_count=1, photo_id=55)
    monkeypatch.setattr(chat_service, "handle_retention_message", _handle)

    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7}, "chat": {"id": 7}, "text": "покажи фото"}})

    assert tg.photos, "a photo should have been sent"
    assert tg.photos[0] == (7, "cachedfile", "here you go")


async def test_nika_handoff_retention_entry_routes_to_support(monkeypatch):
    tg = FakeTelegram()
    _patch_common(monkeypatch, tg)
    ru = {"id": 10, "tg_user_id": 7, "entry_type": "retention", "conv_lang": None,
          "subscribed": True, "vip_level": "none", "unlocked_stage": 1,
          "photos_sent_today": 0, "photos_day": None, "msgs_since_photo": 0,
          "meaningful_msgs": 1, "player_id": "p9", "session_id": "sess-1"}

    async def _get_ru(pid, tg_id):
        return dict(ru)
    monkeypatch.setattr(retention.db, "get_retention_user", _get_ru)

    async def _get_session(sid):
        return {"id": "sess-1", "product_id": 1, "user_context": {}, "lang": "en",
                "conv_lang": None, "message_count": 0, "status": "open"}
    monkeypatch.setattr(retention.db, "get_session", _get_session)

    async def _bump(rid, *, meaningful):
        return dict(ru)
    monkeypatch.setattr(retention.db, "bump_retention_activity", _bump)

    async def _candidates(pid, rid, **kw):
        return []
    monkeypatch.setattr(retention.db, "candidate_photos", _candidates)

    async def _log(*a, **k):
        pass
    monkeypatch.setattr(retention.db, "log_admin_event", _log)

    async def _handle(session, text, candidates):
        return chat_service.RetentionReply(
            reply="let me pass you along", lang="en", message_count=1, handoff=True)
    monkeypatch.setattr(chat_service, "handle_retention_message", _handle)

    await retention.handle_update(PRODUCT, {"message": {
        "from": {"id": 7}, "chat": {"id": 7}, "text": "my account is blocked"}})

    texts = " ".join(m[1] for m in tg.messages).lower()
    assert "support" in texts or "поддержк" in texts
