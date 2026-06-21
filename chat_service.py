"""Orchestration: build prompt -> call model -> persist turn -> build reply.

Ties together prompts, kb, language, the two-key OpenAI client, escalation, and
the atomic turn persistence in db.persist_turn. Keeps API handlers thin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import config
import db
import escalation
import kb
import language
import openai_client
import prompts


@dataclass
class ChatReply:
    reply: str
    lang: str
    escalation: dict
    message_count: int
    # {slug, title} when the model judged the question belongs to another topic
    # whose KB isn't loaded, so the front-end can offer a one-tap switch. Else None.
    suggested_topic: Optional[dict] = None


async def _on_failover(session_id: Optional[str], reason: str) -> None:
    """Callback for openai_client: record the key failover as an admin event."""
    await db.log_admin_event(session_id, "key_failover", {"reason": reason})


async def handle_message(session: dict[str, Any], user_text: str) -> ChatReply:
    """Process one user turn for an already-validated session.

    The caller (api/chat.py) has already enforced token/rate/cooldown/length
    gates and the message-cap fast path. Here we build the prompt, call the
    model, decide escalation, and persist the turn atomically.
    """
    session_id = session["id"]
    topic_id = session.get("topic_id")
    topic_slug = None
    if topic_id is not None:
        topic = await db.get_topic_by_id(topic_id)
        topic_slug = topic["slug"] if topic else None

    # --- language resolution -------------------------------------------------
    # When the player picked a language by hand (header switcher), it is a hard
    # override: answer strictly in it, regardless of the message language.
    force_lang = bool(session.get("lang_locked"))
    resolved = language.resolve(
        lang=None, locale=None, session_lang=session.get("lang")
    )

    # --- audit: injection scan on the user message ---------------------------
    # (handled in api layer too; safe to re-scan is avoided — api owns it)

    # --- build prompt --------------------------------------------------------
    # The other support topics (current one + 'other' excluded) are offered to
    # the model in Layer 3 so it can route a mismatched question via [[TOPIC:slug]].
    # Localize their titles with the resolved default (the UI re-localizes if the
    # player switches anyway); fall back to the service default for AUTO.
    title_lang = resolved if resolved != language.AUTO else config.DEFAULT_LANGUAGE
    suggestable = await kb.suggestable_topics(exclude_topic_id=topic_id, lang=title_lang)
    kb_block = await kb.kb_block_for_topic(topic_id)
    history = await db.get_history(session_id, limit=20)
    messages = prompts.build_messages(
        session=session,
        kb_block=kb_block,
        history=history,
        user_text=user_text,
        resolved_lang=resolved,
        force_lang=force_lang,
        available_topics=suggestable,
    )

    # --- call model (two-key failover) --------------------------------------
    client = openai_client.get_client()
    error: Optional[str] = None
    try:
        result = await client.complete(
            messages, session_id=session_id, on_failover=_on_failover
        )
        raw_text = result.text
        ok = True
    except Exception as exc:  # noqa: BLE001
        # Model failure: log it, fall back to a graceful escalation reply.
        error = f"{exc.__class__.__name__}: {exc}"
        ok = False
        result = openai_client.ChatResult(
            text="", lang=None, tokens_in=0, tokens_out=0, cached_in=0,
            model=config.OPENAI_MODEL, key_used="none", latency_ms=0,
        )
        raw_text = ""

    # --- strip control sentinels (escalation + topic suggestion) ------------
    model_signalled = False
    suggested_slug: Optional[str] = None
    clean_text = raw_text
    if raw_text:
        clean_text, model_signalled = prompts.strip_escalation_tag(raw_text)
        clean_text, suggested_slug = prompts.strip_topic_suggestion(clean_text)

    # Resolve a suggested slug to a real, switchable topic (must be one we just
    # offered: valid, not the current topic, not the hidden 'other'). Anything
    # else the model invents is dropped silently.
    suggested_topic: Optional[dict] = None
    if suggested_slug:
        suggested_topic = next(
            (t for t in suggestable if t["slug"] == suggested_slug), None
        )

    # --- pick a language for payloads (escalation button etc.) --------------
    # The model mirrors the player's actual message language regardless of this;
    # `answer_lang` is only the fallback used for escalation/contact copy and as
    # the recorded metadata. We deliberately do NOT persist a speculative
    # default here — locking the session to DEFAULT would stop later turns from
    # mirroring the player (the bug where a Russian question got an English reply).
    answer_lang = session.get("lang") or config.DEFAULT_LANGUAGE
    if resolved != language.AUTO:
        answer_lang = resolved

    # --- cost accounting -----------------------------------------------------
    cost = openai_client.compute_cost(
        result.model, result.tokens_in, result.tokens_out, result.cached_in
    )

    # --- escalation decision -------------------------------------------------
    # message_count after this turn (current + 1) drives the cap check.
    prospective_count = session.get("message_count", 0) + 1
    decision = escalation.decide(
        user_text=user_text,
        model_signalled=model_signalled or not ok,
        message_count=prospective_count,
        topic_slug=topic_slug,
        already_escalated=session.get("escalated", False),
    )

    if not ok and not clean_text:
        # Model totally failed: give a graceful, escalation-flavoured message.
        clean_text = escalation.build_payload(answer_lang)["message"]

    # --- persist the turn atomically ----------------------------------------
    new_count = await db.persist_turn(
        session_id=session_id,
        user_text=user_text,
        user_lang=resolved if resolved != language.AUTO else None,
        assistant_text=clean_text,
        assistant_lang=answer_lang,
        ai_meta={
            "model": result.model,
            "key_used": result.key_used,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cached_in": result.cached_in,
            "cost_usd": cost,
            "latency_ms": result.latency_ms,
            "ok": ok,
            "error": error,
        },
    )

    # --- apply escalation side effects --------------------------------------
    esc_payload = escalation.inactive_payload()
    if decision.active:
        if not session.get("escalated", False):
            await db.mark_escalated(session_id)
            await db.log_admin_event(
                session_id, "escalation", {"reason": decision.reason}
            )
        esc_payload = escalation.build_payload(answer_lang)

    return ChatReply(
        reply=clean_text,
        lang=answer_lang,
        escalation=esc_payload,
        message_count=new_count,
        suggested_topic=suggested_topic,
    )
