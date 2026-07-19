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

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
# Telegram getChatMember statuses that count as "subscribed" to the channel.
_SUBSCRIBED_STATUSES = {"member", "administrator", "creator", "restricted"}
# Upper bound on how long a single 429 retry will wait. Telegram's retry_after
# is usually 1-5s; cap it so a pathological value can't park a send (and, on the
# webhook path, a background task) for minutes.
_MAX_RETRY_AFTER_SEC = 10


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

    async def _post(self, method: str, *,
                    json_body: Optional[dict[str, Any]] = None,
                    form_data: Optional[dict[str, Any]] = None,
                    files: Optional[dict[str, Any]] = None
                    ) -> Optional[dict[str, Any]]:
        """POST a Bot API method once and return the parsed JSON envelope, or
        None on a transport exception (a send must never break the webhook).

        The ONE HTTP choke point for every Bot API call, so the single 429
        rate-limit retry lives here: Telegram answers a rate-limited send with
        `ok=false, error_code=429, parameters.retry_after=<sec>`. Left unhandled
        the message is silently dropped, which bites hardest exactly when the
        proactive-agent sweep fans a burst of pings and trips the per-second
        limit. We honour retry_after ONCE (capped), then give up to the caller's
        normal failure handling — never an unbounded retry loop."""
        for attempt in (0, 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(self._url(method), json=json_body,
                                             data=form_data, files=files)
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 - a send must never break the webhook
                log.warning("telegram_api_call_failed method=%s error=%s",
                            method, exc)
                return None
            if (attempt == 0 and not data.get("ok")
                    and data.get("error_code") == 429):
                params = data.get("parameters") or {}
                try:
                    retry_after = int(params.get("retry_after") or 0)
                except (TypeError, ValueError):
                    retry_after = 0
                wait = min(max(retry_after, 1), _MAX_RETRY_AFTER_SEC)
                log.warning("telegram_rate_limited method=%s retry_after=%s wait=%s",
                            method, retry_after, wait)
                await asyncio.sleep(wait)
                continue
            return data
        return data  # pragma: no cover - loop always returns inside

    async def _call(self, method: str, payload: dict[str, Any]
                    ) -> Optional[dict[str, Any]]:
        """POST a Bot API method; return the `result` dict or None on failure."""
        data = await self._post(method, json_body=payload)
        if data is None:
            return None
        if not data.get("ok"):
            log.warning("telegram_api_error method=%s desc=%s",
                        method, data.get("description"))
            return None
        return data.get("result")

    async def _call_verbose(self, method: str, payload: dict[str, Any]
                            ) -> tuple[Optional[dict[str, Any]],
                                       Optional[int], Optional[str]]:
        """Like _call but returns (result, error_code, description) so callers can
        act on the specific error (e.g. 403 -> the player blocked the bot)."""
        data = await self._post(method, json_body=payload)
        if data is None:
            return None, None, "request_failed"
        if not data.get("ok"):
            log.warning("telegram_api_error method=%s desc=%s",
                        method, data.get("description"))
            return None, data.get("error_code"), data.get("description")
        return data.get("result"), None, None

    async def send_message(self, chat_id: int, text: str, *,
                           reply_markup: Optional[dict[str, Any]] = None,
                           parse_mode: Optional[str] = None,
                           disable_notification: bool = False
                           ) -> Optional[dict[str, Any]]:
        result, _code, _desc = await self.send_message_verbose(
            chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode,
            disable_notification=disable_notification)
        return result

    async def send_message_verbose(self, chat_id: int, text: str, *,
                                   reply_markup: Optional[dict[str, Any]] = None,
                                   parse_mode: Optional[str] = None,
                                   disable_notification: bool = False
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
        if disable_notification:
            payload["disable_notification"] = True
        data = await self._post("sendMessage", json_body=payload)
        if data is None:
            return None, None, "request_failed"
        if not data.get("ok"):
            log.warning("telegram_api_error method=sendMessage desc=%s",
                        data.get("description"))
            return None, data.get("error_code"), data.get("description")
        return data.get("result"), None, None

    async def send_photo_file_id_verbose(
            self, chat_id: int, file_id: str, *,
            caption: Optional[str] = None, parse_mode: Optional[str] = None,
            reply_markup: Optional[dict[str, Any]] = None,
            disable_notification: bool = False
            ) -> tuple[Optional[dict[str, Any]], Optional[int], Optional[str]]:
        """sendPhoto by cached file_id, surfacing (error_code, description) so the
        caller can tell a 403 (player blocked the bot -> unreachable) from a stale
        file_id (retry via re-upload)."""
        payload: dict[str, Any] = {"chat_id": chat_id, "photo": file_id}
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if disable_notification:
            payload["disable_notification"] = True
        return await self._call_verbose("sendPhoto", payload)

    async def send_photo_file_id(self, chat_id: int, file_id: str, *,
                                 caption: Optional[str] = None,
                                 parse_mode: Optional[str] = None,
                                 reply_markup: Optional[dict[str, Any]] = None,
                                 disable_notification: bool = False
                                 ) -> Optional[dict[str, Any]]:
        """Send an already-uploaded photo by its cached file_id (no re-upload)."""
        result, _code, _desc = await self.send_photo_file_id_verbose(
            chat_id, file_id, caption=caption, parse_mode=parse_mode,
            reply_markup=reply_markup, disable_notification=disable_notification)
        return result

    async def send_photo_bytes_verbose(
            self, chat_id: int, content: bytes, filename: str, *,
            caption: Optional[str] = None, parse_mode: Optional[str] = None,
            reply_markup: Optional[dict[str, Any]] = None,
            disable_notification: bool = False
            ) -> tuple[Optional[dict[str, Any]], Optional[int], Optional[str]]:
        """Upload a photo from bytes (first send), surfacing (error_code,
        description). Returns the result so the caller can cache the file_id."""
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        if parse_mode:
            data["parse_mode"] = parse_mode
        if disable_notification:
            data["disable_notification"] = "true"
        if reply_markup is not None:
            # multipart form fields must be strings — serialize the keyboard.
            data["reply_markup"] = json.dumps(reply_markup)
        files = {"photo": (filename, content)}
        j = await self._post("sendPhoto", form_data=data, files=files)
        if j is None:
            return None, None, "request_failed"
        if not j.get("ok"):
            log.warning("telegram_send_photo_error desc=%s", j.get("description"))
            return None, j.get("error_code"), j.get("description")
        return j.get("result"), None, None

    async def send_photo_bytes(self, chat_id: int, content: bytes, filename: str,
                               *, caption: Optional[str] = None,
                               parse_mode: Optional[str] = None,
                               reply_markup: Optional[dict[str, Any]] = None,
                               disable_notification: bool = False
                               ) -> Optional[dict[str, Any]]:
        """Upload a photo from bytes (first send). Returns the result so the
        caller can cache the returned file_id for later sends."""
        result, _code, _desc = await self.send_photo_bytes_verbose(
            chat_id, content, filename, caption=caption, parse_mode=parse_mode,
            reply_markup=reply_markup, disable_notification=disable_notification)
        return result

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

    async def send_chat_action(self, chat_id: int, action: str = "typing"
                               ) -> None:
        """Show a native "typing…" indicator in the player's chat.

        Telegram clears the action automatically after ~5 seconds or as soon
        as a message arrives, so a long model turn re-sends it on a timer
        (see retention._typing). Fire-and-forget: an error only skips the
        indicator, never the reply."""
        await self._call("sendChatAction", {"chat_id": chat_id,
                                            "action": action})

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

    async def subscription_state(self, chat_id: Any, user_id: int
                                 ) -> Optional[bool]:
        """Tri-state membership check: True = member, False = definitively NOT a
        member, None = the check could not be completed (network/API error, a
        Telegram 5xx/429, or a bot-not-admin misconfig).

        The None case matters: a transient outage must NOT be read as "the player
        left the channel". Callers fail open on None instead of dropping the
        player's message and flipping them to unsubscribed."""
        result = await self._call("getChatMember",
                                  {"chat_id": chat_id, "user_id": user_id})
        if not result:
            return None
        status = result.get("status")
        # A "restricted" member record exists even after the user leaves the
        # channel — only its is_member flag says whether they are actually in.
        if status == "restricted":
            return bool(result.get("is_member"))
        return status in _SUBSCRIBED_STATUSES

    async def is_subscribed(self, chat_id: Any, user_id: int) -> bool:
        """Boolean membership (an error/unknown collapses to False). Prefer
        subscription_state where an outage must not look like 'not subscribed'."""
        return bool(await self.subscription_state(chat_id, user_id))

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
