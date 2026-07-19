"""Outbound delivery seam — the ONE place a proactive persona message leaves
the service, and the channel abstraction future transports plug into.

Today there is one channel (Telegram). The proactive senders — the event agent
(`retention_v2._send_touch`) and the idle ladder (`retention_idle._send_idle_ping`)
— used to each hand-roll the same send mechanics: the persona-header
composition, the Markdown-subset → Telegram-HTML render with a plain-text
fallback, the 403 → `unreachable` bookkeeping, and the photo caption-only
fallback tri-state. Three copies of that logic had already started to drift;
it now lives here once. A future channel (email / push / on-site inbox / SMS)
implements the same `send_text`/`send_photo` surface and is returned by
`channel_for_product()` — the callers never talk to a transport client
directly.

Deliberately NOT routed through here: the DIALOGUE reply path
(`retention._send_ai_text` burst delivery + the typing indicator). Burst
splitting, typing pauses and reply pacing are Telegram-dialogue-specific
chrome; this seam is for messages the service INITIATES.

Outcome contract (mirrors the old tri-state):
  - delivered=True,  kind="photo" — the photo itself reached the player;
  - delivered=True,  kind="text"  — a text message reached the player (either
    a text send, or the caption-only fallback of a photo whose file was
    missing/inactive — the player DID get a message, so the caller must
    persist/record it as sent);
  - delivered=False, kind="none"  — nothing reached the player (`detail`
    carries the error).
"""
from __future__ import annotations

import html as _html
import logging
from dataclasses import dataclass
from typing import Any, Optional

import db
import telegram_format
from telegram_transport import TelegramClient, inline_keyboard

log = logging.getLogger(__name__)


@dataclass
class SendOutcome:
    delivered: bool
    kind: str = "none"              # "text" | "photo" | "none"
    detail: Optional[str] = None    # error detail when nothing was delivered


class TelegramChannel:
    """Proactive delivery over the product's Telegram bot."""

    def __init__(self, product: dict[str, Any], token: str, *,
                 silent: bool = False) -> None:
        self.product = product
        self.client = TelegramClient(token)
        self.silent = bool(silent)

    async def send_text(self, ru: dict[str, Any], text: str, *,
                        header: Optional[str] = None,
                        reply_markup: Optional[dict[str, Any]] = None
                        ) -> SendOutcome:
        """One proactive text message: italic chrome header above the persona
        text, HTML with a plain-text fallback; a Telegram 403 (the player
        blocked the bot) flips the `unreachable` flag so the guards stop
        retrying until the player writes again."""
        chat_id = int(ru["tg_user_id"])
        body_html = telegram_format.to_html(text)
        body_plain = text
        if header:
            body_html = f"<i>{_html.escape(header)}</i>\n\n{body_html}"
            body_plain = f"{header}\n\n{text}"
        result, err_code, err_desc = await self.client.send_message_verbose(
            chat_id, body_html, parse_mode="HTML", reply_markup=reply_markup,
            disable_notification=self.silent)
        if result is None and body_html != body_plain and err_code != 403:
            # Bad HTML (never a block) — retry once as plain text.
            result, err_code, err_desc = await self.client.send_message_verbose(
                chat_id, body_plain, reply_markup=reply_markup,
                disable_notification=self.silent)
        if result is not None:
            return SendOutcome(True, "text")
        detail = f"{err_code}: {err_desc}" if err_desc else "send_failed"
        if err_code == 403:
            await db.set_retention_unreachable(int(ru["id"]), True)
        return SendOutcome(False, "none", detail)

    async def send_photo(self, ru: dict[str, Any], photo_id: int,
                         caption: str, *,
                         header: Optional[str] = None,
                         reply_markup: Optional[dict[str, Any]] = None,
                         session_id: Optional[str] = None) -> SendOutcome:
        """One proactive photo (media-library id, file_id cache, caption-only
        fallback when the photo row/file is gone — see retention._send_photo)."""
        import retention  # late: retention imports modules that import this one
        if header:
            caption = f"{header}\n\n{caption}" if caption else header
        status = await retention._send_photo(
            self.client, self.product, ru, int(ru["tg_user_id"]), photo_id,
            caption, session_id=session_id, reply_markup=reply_markup,
            silent=self.silent)
        if status is None:
            return SendOutcome(False, "none", "photo_send_failed")
        return SendOutcome(True, status)  # "photo" | "text" (caption fallback)


def channel_for_product(product: dict[str, Any], token: Optional[str], *,
                        silent: bool = False) -> Optional[TelegramChannel]:
    """The delivery channel for a product, or None when it cannot be reached.

    Today a product's one channel is its Telegram bot; when other transports
    exist, this factory is where a product's configured channel(s) resolve —
    the proactive senders already only ever talk to the returned object.
    """
    if not token:
        return None
    return TelegramChannel(product, token, silent=silent)


async def deliver_draft(channel, ru: dict[str, Any], draft, *,
                        header: Optional[str], session_id,
                        photo_fallback_caption: str,
                        allow_photo: bool = True,
                        allow_link: bool = True
                        ) -> tuple[bool, Optional[str], bool]:
    """Send a generated PingDraft through a channel (the v2 agent + the idle
    ladder share this half): photo when the draft carries one (and the caller
    allows it), else text; the validated site-map link rides as ONE inline
    button. Returns (delivered, detail, link_attached) — `detail` is the
    failure reason, or "photo_fallback_text" when the photo degraded to text.
    """
    markup = None
    if draft.link_url and allow_link:
        markup = inline_keyboard([[{"text": draft.link_label or draft.link_url,
                                    "url": draft.link_url}]])
    if draft.photo_id is not None and allow_photo:
        caption = draft.text or photo_fallback_caption
        outcome = await channel.send_photo(ru, draft.photo_id, caption,
                                           header=header, reply_markup=markup,
                                           session_id=session_id)
        if not outcome.delivered:
            return False, outcome.detail or "photo_send_failed", markup is not None
        return True, ("photo_fallback_text" if outcome.kind == "text" else None), markup is not None
    outcome = await channel.send_text(ru, draft.text, header=header,
                                      reply_markup=markup)
    if not outcome.delivered:
        return False, outcome.detail or "send_failed", markup is not None
    return True, None, markup is not None


async def account_undelivered_generation(session_id, draft, detail, *,
                                         product_id: int, label: str) -> float:
    """Invariant §4 for a generated-but-undelivered proactive message: the
    OpenAI call happened, so its cost must land in ai_interaction_logs even
    though no chat turn was persisted. Returns the generation cost."""
    import db  # noqa: PLC0415 — lazy, keeps the seam import-light

    meta = draft.ai_meta
    cost = float(meta.get("cost_usd") or 0)
    await db.log_ai_interaction(
        session_id, meta.get("model"), meta.get("key_used"),
        meta.get("tokens_in"), meta.get("tokens_out"), meta.get("cached_in"),
        cost, meta.get("latency_ms"), False, f"{label} {detail}",
        product_id=product_id)
    return cost
