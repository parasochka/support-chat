"""Orchestration: build prompt -> call model -> persist turn -> build reply.

Ties together prompts, kb, language, the two-key OpenAI client, escalation, and
the atomic turn persistence in db.persist_turn. Keeps API handlers thin.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Optional

import antispam
import db
import escalation
import kb
import language
import openai_client
import prompts
import settings


log = logging.getLogger(__name__)


# The system-supplied closing bubble ("issue solved"). The model generates ONLY
# the guiding questions; this fixed, localized option is appended by the backend
# whenever guiding bubbles are shown, so its wording is always exact and tapping
# it reliably ends the chat. House style: no em dashes, no guillemets.
_CLOSING_SUGGESTIONS = {
    "en": "Issue solved.",
    "ru": "Проблема решена.",
    "es": "Problema resuelto.",
    "tr": "Sorun çözüldü.",
    "pt": "Problema resolvido.",
}


def closing_suggestion_for(lang: str) -> str:
    """The localized closing-bubble text, falling back to English."""
    return _CLOSING_SUGGESTIONS.get(lang, _CLOSING_SUGGESTIONS["en"])


# Model-free reply for a TRANSIENT model failure (all retries + failover
# exhausted, e.g. an OpenAI outage). The turn is not persisted and the session
# stays open, so the player can simply resend — a temporary provider blip must
# never escalate and close a live conversation.
_MODEL_ERROR_REPLY = {
    "en": "Sorry, I'm having a brief technical hiccup. Please send your message again in a moment.",
    "ru": "Извини, у меня небольшие технические неполадки. Пожалуйста, отправь сообщение ещё раз через минуту.",
    "es": "Perdona, tengo un problema técnico temporal. Por favor, envía tu mensaje de nuevo en un momento.",
    "tr": "Kusura bakma, geçici bir teknik sorun yaşıyorum. Lütfen mesajını birazdan tekrar gönder.",
    "pt": "Desculpe, estou com um problema técnico temporário. Por favor, envie sua mensagem novamente em instantes.",
}


def _model_error_reply(lang: str) -> str:
    return _MODEL_ERROR_REPLY.get(lang, _MODEL_ERROR_REPLY["en"])


@dataclass
class ChatReply:
    reply: str
    lang: str
    escalation: dict
    message_count: int
    # {slug, title} when the model judged the question belongs to another topic
    # whose KB isn't loaded, so the front-end can offer a one-tap switch. Else None.
    suggested_topic: Optional[dict] = None
    # Up to 3 short follow-up/clarifying questions (player's POV) the model offered
    # to steer the player toward a concrete KB answer; the widget renders them as
    # one-tap bubbles by the input field. Empty list when none.
    suggestions: Optional[list] = None
    # The trailing closing/resolution suggestion (declarative, e.g. "Issue solved.")
    # that the widget renders as a distinct finish-the-chat bubble: tapping it ends
    # the conversation (marks it resolved) instead of sending another question.
    # None when the model offered no closing option (or on escalation/topic switch).
    closing_suggestion: Optional[str] = None
    # True when the model signalled the question looks fully resolved, so the widget
    # can offer a "finish chat" button nudging the player toward closing the chat.
    resolved: bool = False


async def _on_failover(session_id: Optional[str], reason: str) -> None:
    """Callback for openai_client: record the key failover as an admin event."""
    await db.log_admin_event(session_id, "key_failover", {"reason": reason})


async def handle_message(
    session: dict[str, Any], user_text: str, closing: bool = False
) -> ChatReply:
    """Process one user turn for an already-validated session.

    The caller (api/chat.py) has already enforced token/rate/cooldown/length
    gates and the message-cap fast path. Here we build the prompt, call the
    model, decide escalation, and persist the turn atomically.
    """
    started = time.monotonic()
    session_id = session["id"]
    log.info(
        "chat_generation_started session_id=%s topic_id=%s message_count=%s chars=%s",
        session_id, session.get("topic_id"),
        session.get("message_count", 0), len(user_text),
    )
    # --- language resolution -------------------------------------------------
    # The conversation language FOLLOWS the player. The BASE/fallback is the
    # session's sticky `conv_lang` (the language the player previously switched
    # to), else the browser language resolved at create (`session.lang`). The
    # model is told to answer in the language of the player's CURRENT message and
    # to fall back to this base when the message is too short/ambiguous; it
    # reports the language it used via a [[LANG:xx]] tag we strip below. The
    # widget chrome is untouched (it keeps the browser language client-side).
    base_lang = (
        session.get("conv_lang")
        or session.get("lang")
        or language.default_code()
    )
    resolved = base_lang

    # --- pre-model keyword escalation (SOFT, saves the model call) ----------
    # High-risk (fraud/legal) stems and explicit asks for a human don't depend
    # on the model, so they are decided BEFORE the OpenAI call: the hand-off
    # turn burns no tokens. The escalation is SOFT — the contact card is shown
    # and the session is flagged for the metrics/queue, but it stays OPEN so a
    # fuzzy keyword false positive ("судя по всему...") can't kill a live chat.
    keyword_reason = escalation.keyword_trigger(user_text)
    if keyword_reason:
        log.info(
            "chat_keyword_escalation session_id=%s reason=%s",
            session_id, keyword_reason,
        )
        esc_payload = escalation.build_payload(base_lang, final=False)
        new_count = await db.persist_turn(
            session_id=session_id,
            user_text=user_text,
            user_lang=None,
            assistant_text=esc_payload["message"],
            assistant_lang=base_lang,
            ai_meta=None,  # no OpenAI call happened
        )
        if not session.get("escalated", False):
            await db.mark_escalated_soft(session_id)
            await db.log_admin_event(
                session_id, "escalation",
                {"reason": keyword_reason, "mode": "soft"},
            )
        return ChatReply(
            # The hand-off copy rides in the escalation card (the widget renders
            # message + button there); an identical chat bubble on top of it
            # would duplicate the text. The transcript still has it — the turn
            # was persisted with the copy as the assistant text.
            reply="",
            lang=base_lang,
            escalation=esc_payload,
            message_count=new_count,
            suggested_topic=None,
            suggestions=[],
            closing_suggestion=None,
            resolved=False,
        )

    topic_id = session.get("topic_id")
    topic_slug = None
    topic = None
    if topic_id is not None:
        topic = await db.get_topic_by_id(topic_id)
        topic_slug = topic["slug"] if topic else None

    # --- build prompt --------------------------------------------------------
    # The other support topics (current one + 'other' excluded) are offered to
    # the model in Layer 3 so it can route a mismatched question via [[TOPIC:slug]].
    # Localize their titles with the resolved default (the UI re-localizes if the
    # player switches anyway); fall back to the service default for AUTO.
    title_lang = resolved if resolved != language.AUTO else language.default_code()
    suggestable = await kb.suggestable_topics(exclude_topic_id=topic_id, lang=title_lang)
    log.info(
        "chat_prompt_context_loaded session_id=%s base_lang=%s title_lang=%s suggestable_topics=%s",
        session_id, base_lang, title_lang, len(suggestable),
    )
    # Name the CURRENT topic so the model answers in-topic questions from the
    # loaded KB instead of bouncing the player to another branch on keyword
    # overlap (e.g. both deposits and withdrawals mention crypto networks).
    current_topic = None
    if topic_slug:
        current_topic = {
            "slug": topic_slug,
            "title": kb.localize_title(topic.get("title"), title_lang) if topic else topic_slug,
        }
    kb_block = await kb.kb_block_for_topic(topic_id)
    # Only feed the model turns from the current topic context. After a topic
    # switch `context_reset_id` marks the boundary, so the previous topic's
    # transcript can't re-trigger a [[TOPIC:...]] suggestion back to it (the
    # switch loop). 0 (default) means the whole transcript.
    context_reset_id = session.get("context_reset_id", 0) or 0
    history = await db.get_history(
        session_id, limit=20, after_id=context_reset_id
    )
    messages = prompts.build_messages(
        session=session,
        kb_block=kb_block,
        history=history,
        user_text=user_text,
        resolved_lang=resolved,
        available_topics=suggestable,
        current_topic=current_topic,
        # The player tapped the "Issue solved." closing bubble: this turn is a
        # farewell, so the prompt asks for a pure goodbye with no continuation.
        closing=closing,
        # After a topic switch the prompt history is cut at the reset boundary,
        # so the model would otherwise treat the next turn as a brand-new chat
        # and greet again mid-conversation.
        ongoing=context_reset_id > 0,
    )
    log.info(
        "chat_prompt_built session_id=%s topic_slug=%s history_turns=%s kb_chars=%s messages=%s",
        session_id, topic_slug, len(history), len(kb_block or ""), len(messages),
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
        log.info(
            "chat_model_completed session_id=%s model=%s key=%s latency_ms=%s tokens_in=%s tokens_out=%s cached_in=%s raw_chars=%s",
            session_id, result.model, result.key_used, result.latency_ms,
            result.tokens_in, result.tokens_out, result.cached_in, len(raw_text or ""),
        )
    except Exception as exc:  # noqa: BLE001
        # TRANSIENT model failure (retries + failover exhausted, e.g. an OpenAI
        # outage). Do NOT escalate and do NOT close the session — a provider
        # blip must not kill a live conversation. Return a localized "try again
        # in a moment" nudge; the turn is not persisted (no answer exists, the
        # player simply resends), but the failed call is logged for accounting
        # (invariant §4) and surfaced as an admin event so outages are visible.
        error = f"{exc.__class__.__name__}: {exc}"
        log.exception("chat_model_failed session_id=%s error=%s", session_id, error)
        await db.log_ai_interaction(
            session_id, settings.model()["model"], "none",
            0, 0, 0, 0.0, 0, False, error,
        )
        await db.log_admin_event_sampled(
            session_id, "model_error", {"error": error[:300]}
        )
        return ChatReply(
            reply=_model_error_reply(base_lang),
            lang=base_lang,
            escalation=escalation.inactive_payload(),
            message_count=session.get("message_count", 0),
            suggested_topic=None,
            suggestions=[],
            closing_suggestion=None,
            resolved=False,
        )

    # --- strip control sentinels (escalation + topic + language + suggest) --
    model_signalled = False
    suggested_slug: Optional[str] = None
    detected_lang: Optional[str] = None
    suggestions: list = []
    closing_suggestion: Optional[str] = None
    resolved = False
    clean_text = raw_text
    if raw_text:
        clean_text, model_signalled = prompts.strip_escalation_tag(raw_text)
        clean_text, suggested_slug = prompts.strip_topic_suggestion(clean_text)
        clean_text, detected_lang = prompts.strip_language_tag(clean_text)
        clean_text, suggestions = prompts.strip_suggestions(clean_text)
        clean_text, resolved = prompts.strip_resolved_tag(clean_text)
    # Only trust a [[LANG:xx]] code the model can actually answer in.
    if detected_lang and detected_lang not in language.supported_codes():
        detected_lang = None

    # Resolve a suggested slug to a real, switchable topic (must be one we just
    # offered: valid, not the current topic, not the hidden 'other'). Anything
    # else the model invents is dropped silently.
    suggested_topic: Optional[dict] = None
    if suggested_slug:
        log.info(
            "chat_model_suggested_topic session_id=%s slug=%s",
            session_id, suggested_slug,
        )
        suggested_topic = next(
            (t for t in suggestable if t["slug"] == suggested_slug), None
        )

    # --- pick a language for payloads (escalation button etc.) --------------
    # The language the model actually answered in (from the [[LANG:xx]] tag),
    # else the base/fallback. Used for the escalation/contact copy and recorded
    # as the turn metadata. If the player drifted to a new supported language,
    # persist it as the session's sticky conversation language so later turns
    # (incl. the model-free cap / low-content paths) stay in it until they switch.
    answer_lang = detected_lang or base_lang
    if detected_lang and detected_lang != session.get("conv_lang"):
        await db.set_conv_lang(session_id, detected_lang)

    # The widget renders the cross-topic switch notice ("switching you to «X»…")
    # in the language the model ANSWERED in (the player's current message
    # language), not the session base. The suggested topic's title was localized
    # to the base (`title_lang`) when `suggestable` was built, so a player who
    # opened in one language but wrote in another would see the notice in their
    # language but the topic name still in the base — e.g. a Russian line naming
    # the topic in English ("Deposits"). Re-localize the title to `answer_lang`
    # so the whole notice reads in one language.
    if suggested_topic and answer_lang != title_lang:
        raw_topic = await db.get_topic_by_slug(suggested_topic["slug"])
        if raw_topic:
            suggested_topic = {
                **suggested_topic,
                "title": kb.localize_title(raw_topic.get("title"), answer_lang),
            }

    # --- cost accounting -----------------------------------------------------
    cost = openai_client.compute_cost(
        result.model, result.tokens_in, result.tokens_out, result.cached_in
    )

    # --- escalation decision (post-model HARD triggers) ----------------------
    # message_count after this turn (current + 1) drives the cap check. The
    # keyword triggers were already handled pre-model (SOFT path above), and a
    # soft-escalated session keeps chatting — no "already escalated" re-trigger.
    prospective_count = session.get("message_count", 0) + 1
    decision = escalation.decide(
        model_signalled=model_signalled,
        message_count=prospective_count,
    )

    # --- cross-topic auto-switch: routing-only turn (answer suppressed) ------
    # When the model routes the question to a DIFFERENT topic (and this is not an
    # escalation), the in-place answer it produced was generated WITHOUT that
    # topic's KB loaded — so it is ungrounded and must never reach the player.
    # Instead of persisting/showing it, return a routing-only result: the widget
    # auto-switches the session to the suggested topic and re-asks the original
    # question against the CORRECT KB (that re-ask is the one persisted, counted
    # turn). We deliberately persist NO chat turn here and do not bump the message
    # cap, but we DO log the detect call's token cost so OpenAI spend stays
    # accounted for (invariant §4: every OpenAI call -> ai_interaction_logs).
    if suggested_topic and not decision.active:
        log.info(
            "chat_auto_switch_routing session_id=%s slug=%s suppressed_answer_chars=%s",
            session_id, suggested_topic["slug"], len(clean_text or ""),
        )
        await db.log_ai_interaction(
            session_id, result.model, result.key_used,
            result.tokens_in, result.tokens_out, result.cached_in,
            cost, result.latency_ms, ok, error,
        )
        # Record the switch itself so the admin session view can trace the whole
        # path: this detect call belongs to NO chat_messages turn (the answer was
        # suppressed), so without this marker its cost looks orphaned and the
        # per-turn costs no longer add up to the session total. The marker carries
        # the from/to topics, the triggering message and this call's cost.
        await db.log_admin_event(
            session_id,
            "topic_switch",
            {
                "from": topic_slug or prompts.OTHER_TOPIC_SLUG,
                "to": suggested_topic["slug"],
                "from_title": (current_topic or {}).get("title"),
                "to_title": suggested_topic.get("title"),
                "trigger": user_text[:200],
                "cost_usd": cost,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
            },
        )
        return ChatReply(
            reply="",
            lang=answer_lang,
            escalation=escalation.inactive_payload(),
            message_count=session.get("message_count", 0),
            suggested_topic=suggested_topic,
            suggestions=[],
            closing_suggestion=None,
            resolved=False,
        )

    esc_payload = escalation.inactive_payload()
    if decision.active:
        esc_payload = escalation.build_payload(answer_lang)

    if not clean_text:
        log.warning(
            "chat_empty_model_reply session_id=%s ok=%s escalation_active=%s raw_chars=%s",
            session_id, ok, decision.active, len(raw_text or ""),
        )

    if not clean_text and decision.active:
        # The model may return only the [[ESCALATE]] control tag, which strips to
        # an empty answer. Persist and return the localized hand-off copy instead
        # of leaving the widget to render a blank assistant bubble before the
        # escalation card.
        clean_text = esc_payload["message"]
    elif not clean_text and not suggested_topic:
        # No usable text, and this is neither an escalation nor a topic switch
        # (e.g. a reasoning model still truncated to empty even after the client's
        # retry). Never leave the widget with a blank bubble that reads as a frozen
        # chat — return a gentle, localized "rephrase" nudge so the player can keep
        # going. (When suggested_topic is set the widget shows the switch card, so
        # an empty answer there is fine and we leave it as-is.)
        clean_text = antispam.low_content_reply(answer_lang)
        log.warning(
            "chat_empty_reply_fallback session_id=%s answer_lang=%s",
            session_id, answer_lang,
        )

    # The closing "issue solved" bubble is SYSTEM-supplied (the model only
    # generates the guiding questions): whenever guiding bubbles are shown,
    # append the fixed localized option so the player can end a quickly-solved
    # chat in one tap. (When the model set [[RESOLVED]] instead, the widget
    # shows the green finish button and ignores this field.)
    closing_suggestion = closing_suggestion_for(answer_lang) if suggestions else None

    # On a hand-off the player is being routed to a human, so the guide-to-KB
    # bubbles and the "finish chat" nudge are out of place — drop both. (The
    # directive already tells the model to skip them when escalating; this is the
    # backend guarantee.)
    if decision.active:
        suggestions = []
        closing_suggestion = None
        resolved = False

    # When the model says this question belongs to a different topic, the only
    # useful next step is the topic-switch prompt. Follow-up bubbles would keep
    # the player in the current topic, so never return them alongside a switch.
    if suggested_topic:
        suggestions = []
        closing_suggestion = None
        resolved = False

    # --- persist the turn atomically ----------------------------------------
    # `detected_lang` is the language the model ANSWERED in (from [[LANG:xx]]).
    # Per the language directive the model answers in the language of the player's
    # CURRENT message, so it is a faithful proxy for the user turn's language too;
    # None when the model emitted no tag (kept null rather than guessed).
    log.info(
        "chat_persisting_turn session_id=%s ok=%s answer_lang=%s clean_chars=%s escalation_active=%s suggested_topic=%s suggestions=%s resolved=%s",
        session_id, ok, answer_lang, len(clean_text or ""), decision.active,
        suggested_topic["slug"] if suggested_topic else None, len(suggestions or []), resolved,
    )
    new_count = await db.persist_turn(
        session_id=session_id,
        user_text=user_text,
        user_lang=detected_lang,
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
    if decision.active:
        # Always set the HARD state (idempotent) — a session that was earlier
        # soft-escalated (escalated=TRUE, status still 'open') must still be
        # CLOSED when the model itself signals the hand-off or the cap fires.
        await db.mark_escalated(session_id)
        if session.get("status") != "escalated":
            await db.log_admin_event(
                session_id, "escalation", {"reason": decision.reason}
            )

    log.info(
        "chat_generation_finished session_id=%s ok=%s message_count=%s elapsed_ms=%s",
        session_id, ok, new_count, int((time.monotonic() - started) * 1000),
    )

    return ChatReply(
        reply=clean_text,
        lang=answer_lang,
        escalation=esc_payload,
        message_count=new_count,
        suggested_topic=suggested_topic,
        suggestions=suggestions,
        closing_suggestion=closing_suggestion,
        resolved=resolved,
    )
