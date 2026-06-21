"""One-way Telegram escalation notifier (Bot API sendMessage).

Sends a formatted escalation ticket to the agent chat. Pure transport: the
caller (escalation.open_ticket) owns ticket persistence and the delivered flag.
Returns True on confirmed delivery, False otherwise (never raises) so escalation
always falls back to the contact button and the user is never stranded.
"""
from __future__ import annotations

import html
from typing import Any

import httpx

import config


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_AGENT_CHAT_ID)


def _deep_link(session_id: str) -> str:
    base = (config.PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/admin#/session/{session_id}"


def format_message(payload: dict[str, Any]) -> str:
    """Build the HTML message body for the agent chat from a ticket payload."""
    session_id = payload.get("session_id", "")
    reason = payload.get("reason", "")
    topic = payload.get("topic") or "—"
    lang = payload.get("lang") or "—"
    ctx = payload.get("user_context") or {}
    player_id = ctx.get("id") or payload.get("player_id") or "—"

    lines = [
        "🆘 <b>New support escalation</b>",
        f"<b>Reason:</b> {html.escape(str(reason))}",
        f"<b>Topic:</b> {html.escape(str(topic))}",
        f"<b>Language:</b> {html.escape(str(lang))}",
        f"<b>Player:</b> {html.escape(str(player_id))}",
    ]
    name = ctx.get("full_name")
    email = ctx.get("email")
    if name:
        lines.append(f"<b>Name:</b> {html.escape(str(name))}")
    if email:
        lines.append(f"<b>Email:</b> {html.escape(str(email))}")

    transcript = payload.get("transcript") or []
    if transcript:
        lines.append("\n<b>Transcript:</b>")
        for turn in transcript[-10:]:
            role = "👤" if turn.get("role") == "user" else "🤖"
            content = html.escape(str(turn.get("content", "")))[:500]
            lines.append(f"{role} {content}")

    link = _deep_link(session_id)
    if link:
        lines.append(f"\n🔗 {html.escape(link)}")
    return "\n".join(lines)


async def send_escalation(payload: dict[str, Any]) -> bool:
    """Send the ticket to the agent chat. Returns True on confirmed delivery."""
    if not is_configured():
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    body = {
        "chat_id": config.TELEGRAM_AGENT_CHAT_ID,
        "text": format_message(payload),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body)
        data = resp.json()
        return bool(data.get("ok"))
    except Exception:  # noqa: BLE001 - delivery failures must never raise
        return False
