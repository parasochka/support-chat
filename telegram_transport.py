"""Thin async Telegram Bot API client + update parsing (retention bot transport).

This module is the TRANSPORT boundary only: it talks HTTP to the Telegram Bot
API (send messages/photos, inline keyboards, subscription checks, webhook
registration) and parses incoming updates into a small normalized shape. It
holds NO business logic — the retention orchestration lives in `retention.py`
and the AI turn in `chat_service.py`, so this transport can be swapped or moved
to its own service later (the border the spec keeps clean).

Every call takes the product's own decrypted bot token (multi-tenant: one bot
per product). Network errors are caught and logged, never raised into the
webhook handler — a failed send must not 500 the webhook (Telegram would retry
the whole update).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
# Telegram getChatMember statuses that count as "subscribed" to the channel.
_SUBSCRIBED_STATUSES = {"member", "administrator", "creator", "restricted"}


# ---------------------------------------------------------------------------
# Update parsing (normalized shape)
# ---------------------------------------------------------------------------
@dataclass
class ParsedUpdate:
    """A minimal, normalized view of a Telegram update we act on."""
    kind: str  # 'message' | 'callback' | 'other'
    tg_user_id: Optional[int] = None
    tg_username: Optional[str] = None
    chat_id: Optional[int] = None
    text: Optional[str] = None
    start_param: Optional[str] = None      # payload after /start
    callback_data: Optional[str] = None
    callback_id: Optional[str] = None
    message_id: Optional[int] = None
    language_code: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_update(update: dict[str, Any]) -> ParsedUpdate:
    """Turn a raw Telegram update dict into a ParsedUpdate."""
    if not isinstance(update, dict):
        return ParsedUpdate(kind="other", raw={})
    if "callback_query" in update:
        cq = update["callback_query"] or {}
        frm = cq.get("from") or {}
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        return ParsedUpdate(
            kind="callback",
            tg_user_id=frm.get("id"),
            tg_username=frm.get("username"),
            chat_id=chat.get("id") or frm.get("id"),
            callback_data=cq.get("data"),
            callback_id=cq.get("id"),
            message_id=msg.get("message_id"),
            language_code=frm.get("language_code"),
            raw=update,
        )
    msg = update.get("message") or update.get("edited_message") or {}
    if msg:
        frm = msg.get("from") or {}
        chat = msg.get("chat") or {}
        text = msg.get("text") or ""
        start_param = None
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            start_param = parts[1].strip() if len(parts) > 1 else ""
        return ParsedUpdate(
            kind="message",
            tg_user_id=frm.get("id"),
            tg_username=frm.get("username"),
            chat_id=chat.get("id") or frm.get("id"),
            text=text,
            start_param=start_param,
            message_id=msg.get("message_id"),
            language_code=frm.get("language_code"),
            raw=update,
        )
    return ParsedUpdate(kind="other", raw=update)


# ---------------------------------------------------------------------------
# Inline keyboard helpers
# ---------------------------------------------------------------------------
def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    """Build an inline_keyboard reply_markup from rows of button dicts.

    Each button is {'text': ..., 'callback_data': ...} or {'text':..., 'url':...}.
    """
    return {"inline_keyboard": rows}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class TelegramClient:
    """One bot token's worth of Telegram Bot API access."""

    def __init__(self, token: str, timeout: float = 15.0):
        self._token = token
        self._timeout = timeout

    def _url(self, method: str) -> str:
        return f"{_API_BASE}/bot{self._token}/{method}"

    async def _call(self, method: str, payload: dict[str, Any]
                    ) -> Optional[dict[str, Any]]:
        """POST a Bot API method; return the `result` dict or None on failure."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url(method), json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram_api_error method=%s desc=%s",
                            method, data.get("description"))
                return None
            return data.get("result")
        except Exception as exc:  # noqa: BLE001 - a send must never break the webhook
            log.warning("telegram_api_call_failed method=%s error=%s", method, exc)
            return None

    async def send_message(self, chat_id: int, text: str, *,
                           reply_markup: Optional[dict[str, Any]] = None,
                           parse_mode: Optional[str] = None
                           ) -> Optional[dict[str, Any]]:
        result, _code, _desc = await self.send_message_verbose(
            chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        return result

    async def send_message_verbose(self, chat_id: int, text: str, *,
                                   reply_markup: Optional[dict[str, Any]] = None,
                                   parse_mode: Optional[str] = None
                                   ) -> tuple[Optional[dict[str, Any]],
                                              Optional[int], Optional[str]]:
        """sendMessage that also surfaces (error_code, description) on failure.

        The proactive ping worker needs to tell 'the player blocked the bot'
        (403 — mark unreachable, stop pinging) apart from a transient error
        (retry next run); the fire-and-forget send_message drops that detail.
        This is the ONE sendMessage HTTP path — send_message delegates here.
        """
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url("sendMessage"), json=payload)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram_api_call_failed method=sendMessage error=%s", exc)
            return None, None, str(exc)
        if not data.get("ok"):
            log.warning("telegram_api_error method=sendMessage desc=%s",
                        data.get("description"))
            return None, data.get("error_code"), data.get("description")
        return data.get("result"), None, None

    async def send_photo_file_id(self, chat_id: int, file_id: str, *,
                                 caption: Optional[str] = None,
                                 parse_mode: Optional[str] = None,
                                 reply_markup: Optional[dict[str, Any]] = None
                                 ) -> Optional[dict[str, Any]]:
        """Send an already-uploaded photo by its cached file_id (no re-upload)."""
        payload: dict[str, Any] = {"chat_id": chat_id, "photo": file_id}
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendPhoto", payload)

    async def send_photo_bytes(self, chat_id: int, content: bytes, filename: str,
                               *, caption: Optional[str] = None,
                               parse_mode: Optional[str] = None,
                               reply_markup: Optional[dict[str, Any]] = None
                               ) -> Optional[dict[str, Any]]:
        """Upload a photo from bytes (first send). Returns the result so the
        caller can cache the returned file_id for later sends."""
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup is not None:
            # multipart form fields must be strings — serialize the keyboard.
            data["reply_markup"] = json.dumps(reply_markup)
        files = {"photo": (filename, content)}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url("sendPhoto"), data=data,
                                         files=files)
            j = resp.json()
            if not j.get("ok"):
                log.warning("telegram_send_photo_error desc=%s", j.get("description"))
                return None
            return j.get("result")
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram_send_photo_failed error=%s", exc)
            return None

    @staticmethod
    def extract_photo_file_id(send_photo_result: Optional[dict[str, Any]]
                              ) -> Optional[str]:
        """Pull the largest photo size's file_id out of a sendPhoto result."""
        if not send_photo_result:
            return None
        photos = send_photo_result.get("photo") or []
        if not photos:
            return None
        # The API returns sizes ascending; the last is the largest.
        return photos[-1].get("file_id")

    async def answer_callback(self, callback_id: str, text: Optional[str] = None
                              ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)

    async def get_chat_member_status(self, chat_id: Any, user_id: int
                                     ) -> Optional[str]:
        """Return the member status of `user_id` in `chat_id`, or None on error."""
        result = await self._call("getChatMember",
                                  {"chat_id": chat_id, "user_id": user_id})
        if not result:
            return None
        return result.get("status")

    async def is_subscribed(self, chat_id: Any, user_id: int) -> bool:
        result = await self._call("getChatMember",
                                  {"chat_id": chat_id, "user_id": user_id})
        if not result:
            return False
        status = result.get("status")
        # A "restricted" member record exists even after the user leaves the
        # channel — only its is_member flag says whether they are actually in.
        if status == "restricted":
            return bool(result.get("is_member"))
        return status in _SUBSCRIBED_STATUSES

    async def set_webhook(self, url: str, secret_token: str,
                          drop_pending: bool = True) -> Optional[dict[str, Any]]:
        payload = {
            "url": url,
            "secret_token": secret_token,
            "drop_pending_updates": drop_pending,
            "allowed_updates": ["message", "callback_query"],
        }
        return await self._call("setWebhook", payload)

    async def delete_webhook(self) -> Optional[dict[str, Any]]:
        return await self._call("deleteWebhook", {"drop_pending_updates": True})

    async def get_me(self) -> Optional[dict[str, Any]]:
        return await self._call("getMe", {})
