"""Orchestration: build prompt -> call model -> persist turn -> build reply.

Ties together prompts, kb, language, the two-key OpenAI client, escalation, and
the atomic turn persistence in db.persist_turn. Keeps API handlers thin.
"""
from __future__ import annotations

import asyncio
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
import telegram_format
import tenancy
import translations


log = logging.getLogger(__name__)


# The system-supplied closing bubble ("issue solved"). The model generates ONLY
# the guiding questions; this fixed, localized option is appended by the backend
# whenever guiding bubbles are shown, so its wording is always exact and tapping
# it reliably ends the chat. The copy lives in the translations registry
# (admin Translations tab > built-in defaults in translations.py).
def closing_suggestion_for(lang: str) -> str:
    """The localized closing-bubble text (translations registry, EN fallback)."""
    return translations.text("closing_suggestion", lang)


# Model-free reply for a TRANSIENT model failure (all retries + failover
# exhausted, e.g. an OpenAI outage). The turn is not persisted and the session
# stays open, so the player can simply resend — a temporary provider blip must
# never escalate and close a live conversation.
def _model_error_reply(lang: str) -> str:
    return translations.text("model_error_reply", lang)


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


async def _none() -> None:
    """A ready no-op awaitable — a gather slot for a read we skip this turn."""
    return None


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
    product_id = session.get("product_id")
    # Bind the tenancy scope to the session's product (idempotent when the API
    # layer already did it; direct callers/tests get the right scope too), so
    # settings / prompt variables / translations / KB resolve per product.
    tenancy.set_current_product(product_id)
    log.info(
        "chat_generation_started session_id=%s product_id=%s topic_id=%s message_count=%s chars=%s",
        session_id, product_id, session.get("topic_id"),
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
        esc_payload = await escalation.build_payload_for_session(
            session, base_lang, final=False)
        new_count = await db.persist_turn(
            session_id=session_id,
            user_text=user_text,
            user_lang=None,
            assistant_text=esc_payload["message"],
            assistant_lang=base_lang,
            ai_meta=None,  # no OpenAI call happened
            product_id=product_id,
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
    # --- build prompt --------------------------------------------------------
    # Only feed the model turns from the current topic context. After a topic
    # switch `context_reset_id` marks the boundary, so the previous topic's
    # transcript can't re-trigger a [[TOPIC:...]] suggestion back to it (the
    # switch loop). 0 (default) means the whole transcript.
    context_reset_id = session.get("context_reset_id", 0) or 0
    # The other support topics (current one + 'other' excluded) are offered to
    # the model in Layer 3 so it can route a mismatched question via [[TOPIC:slug]].
    # Localize their titles with the resolved default (the UI re-localizes if the
    # player switches anyway); fall back to the service default for AUTO.
    title_lang = resolved if resolved != language.AUTO else language.default_code()

    # Current topic, routing catalogue, KB block, history and the OpenAI client
    # are independent, so fetch them concurrently rather than as a chain of
    # round-trips before the model call.
    topic, suggestable, kb_block, history, client = await asyncio.gather(
        db.get_topic_by_id(topic_id) if topic_id is not None else _none(),
        kb.suggestable_topics(exclude_topic_id=topic_id, lang=title_lang),
        kb.kb_block_for_topic(topic_id),
        db.get_history(
            session_id, limit=settings.general()["history_max_turns"],
            after_id=context_reset_id,
        ),
        openai_client.client_for_product(product_id),
    )
    topic_slug = topic["slug"] if topic else None
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

    # --- call model (two-key failover; the product's own keys when set) ------
    # `client` was resolved above, concurrently with the prompt inputs.
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
            0, 0, 0, 0.0, 0, False, error, product_id=product_id,
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
        # Mechanically scrub the typographic "AI tells" the FORMATTING directive
        # forbids (em/en dashes, guillemet/curly quotes) in case the model emitted
        # them anyway - the same deterministic pass the retention channel applies,
        # so a stray dash or « » never reaches the widget even when the prompt rule
        # was not perfectly followed. The persisted transcript matches what the
        # player saw (the scrub runs before both).
        clean_text = telegram_format.normalize_punctuation(clean_text)
    # Only trust a [[LANG:xx]] code the model can actually answer in.
    if detected_lang and detected_lang not in language.supported_codes():
        detected_lang = None

    # Resolve a suggested slug to a real, switchable topic (must be one we just
    # offered: valid, not the current topic, not 'other' — a visible topic but
    # never a routing target). Anything else the model invents is dropped silently.
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
            cost, result.latency_ms, ok, error, product_id=product_id,
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
        esc_payload = await escalation.build_payload_for_session(session, answer_lang)

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
        product_id=product_id,
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


# ===========================================================================
# RETENTION mode (Telegram bot) — reuses the same AI core, different assembly.
# The transport (telegram_transport) and orchestration (retention.py) sit above
# this; here we build the retention prompt, call the model, strip the retention
# sentinels, and persist the turn atomically. No support mechanics (topic
# routing / escalation restraint / suggestions) run in this mode.
# ===========================================================================
@dataclass
class RetentionReply:
    reply: str                 # visible text / photo caption (sentinels stripped)
    lang: str
    message_count: int
    photo_id: Optional[int] = None   # validated id to send, or None
    handoff: bool = False            # route the player OUT (support/manager)
    stage_up_hint: bool = False      # the model proposed advancing the stage
    ok: bool = True                  # False on a transient model failure
    link_url: Optional[str] = None   # validated site-map CTA button url, or None
    link_label: Optional[str] = None  # the site-map page title for the button


def play_nudge_due(message_count: int) -> bool:
    """True when THIS reply is the N-th assistant turn that carries the periodic
    play invitation (`retention.play_reminder_every_msgs`; 0 = off).

    `message_count` is the session's persisted turn counter BEFORE this reply
    (one bump per persisted turn), so the upcoming reply is turn N+1. The very
    first reply never nudges — the engagement directive forbids a casino pitch
    in the opening turns.
    """
    every = int(settings.retention()["play_reminder_every_msgs"])
    if every <= 0:
        return False
    upcoming = int(message_count or 0) + 1
    return upcoming > 1 and upcoming % every == 0


def resolve_site_link(url: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Validate a model-emitted [[LINK:url]] against the product's site map.

    Only an EXACT match with an admin-configured site-map page survives (the
    model can never button-ify an invented address); the page's title becomes
    the button label (falling back to the url itself for a title-less row).
    Returns (url, label) or (None, None).
    """
    candidate = (url or "").strip()
    if not candidate:
        return None, None
    for page in settings.site_map() or []:
        if not isinstance(page, dict):
            continue
        page_url = str(page.get("url", "")).strip()
        if page_url and page_url == candidate:
            label = str(page.get("title", "")).strip() or page_url
            return page_url, label
    log.info("retention_link_rejected url=%s", candidate[:200])
    return None, None


async def handle_retention_message(
    session: dict[str, Any],
    user_text: str,
    photo_candidates: Optional[list[dict[str, Any]]] = None,
) -> RetentionReply:
    """Process one retention (Telegram) turn for an already-linked session.

    `photo_candidates` is the pre-filtered allowed set (tier x stage x unseen x
    daily-cap x proactive-cooldown), computed by retention.py; the model may only
    pick a [[PHOTO:id]] from it and we re-validate here.
    """
    started = time.monotonic()
    session_id = session["id"]
    product_id = session.get("product_id")
    tenancy.set_current_product(product_id)
    candidates = photo_candidates or []
    candidate_ids = {int(c["id"]) for c in candidates}

    base_lang = (session.get("conv_lang") or session.get("lang")
                 or language.default_code())

    # --- retention KB (Layer 2, loaded WHOLE) + history ---------------------
    kb_text = await db.retention_kb_block(product_id) if product_id else ""
    kb_block = await kb.render_variables(kb_text, product_id=product_id) if kb_text else None
    history = await db.get_history(
        session_id, limit=settings.general()["history_max_turns"],
    )
    # Returning-player continuity: on the FIRST turn of a fresh session that
    # rolled over from an idle chat (prev_session_id), carry the tail of the
    # previous conversation into Layer 3 so Nika greets them with continuity.
    previous_history: list[dict[str, Any]] = []
    prev_sid = session.get("prev_session_id")
    if not history and prev_sid:
        carry = int(settings.retention()["carry_context_turns"])
        if carry > 0:
            previous_history = await db.get_history(prev_sid, limit=carry)
    # Periodic play reminder: every N-th reply carries the Layer-3 nudge task
    # (a light in-context invitation to play + a one-tap site-map button).
    nudge = play_nudge_due(session.get("message_count", 0))
    messages = prompts.build_retention_messages(
        session=session,
        kb_block=kb_block,
        history=history,
        user_text=user_text,
        resolved_lang=base_lang,
        photo_candidates=candidates,
        previous_history=previous_history or None,
        play_nudge=nudge,
    )
    log.info(
        "retention_prompt_built session_id=%s history=%s prev_carry=%s "
        "candidates=%s kb_chars=%s play_nudge=%s",
        session_id, len(history), len(previous_history), len(candidates),
        len(kb_block or ""), nudge,
    )

    # --- call model (product keys / env failover) ---------------------------
    client = await openai_client.client_for_product(product_id)
    error: Optional[str] = None
    try:
        result = await client.complete(
            messages, session_id=session_id, on_failover=_on_failover
        )
        raw_text = result.text
    except Exception as exc:  # noqa: BLE001
        error = f"{exc.__class__.__name__}: {exc}"
        log.exception("retention_model_failed session_id=%s error=%s", session_id, error)
        await db.log_ai_interaction(
            session_id, settings.model()["model"], "none",
            0, 0, 0, 0.0, 0, False, error, product_id=product_id,
        )
        await db.log_admin_event_sampled(session_id, "model_error", {"error": error[:300]})
        return RetentionReply(
            reply=_model_error_reply(base_lang), lang=base_lang,
            message_count=session.get("message_count", 0), ok=False,
        )

    # --- strip retention sentinels ------------------------------------------
    detected_lang: Optional[str] = None
    handoff = False
    stage_up = False
    photo_id: Optional[int] = None
    link_raw: Optional[str] = None
    clean_text = raw_text or ""
    if clean_text:
        clean_text, detected_lang = prompts.strip_language_tag(clean_text)
        clean_text, handoff = prompts.strip_handoff_tag(clean_text)
        clean_text, stage_up = prompts.strip_stage_up_tag(clean_text)
        clean_text, photo_id = prompts.strip_photo_tag(clean_text)
        clean_text, link_raw = prompts.strip_link_tag(clean_text)
        # Deterministically scrub the "AI-tell" typography (em dashes, guillemet
        # quotes) the persona is told to avoid but the model keeps emitting.
        clean_text = telegram_format.normalize_punctuation(clean_text)
    if detected_lang and detected_lang not in language.supported_codes():
        detected_lang = None
    # Only honour a photo id from the allowed candidate set.
    if photo_id is not None and photo_id not in candidate_ids:
        log.info("retention_photo_id_rejected session_id=%s id=%s", session_id, photo_id)
        photo_id = None
    # Only honour a CTA link that exactly matches an admin-configured site-map
    # page (never on a hand-off — the player is leaving for support/a manager).
    link_url, link_label = (None, None) if handoff else resolve_site_link(link_raw)

    answer_lang = detected_lang or base_lang
    if detected_lang and detected_lang != session.get("conv_lang"):
        await db.set_conv_lang(session_id, detected_lang)

    cost = openai_client.compute_cost(
        result.model, result.tokens_in, result.tokens_out, result.cached_in
    )

    if not clean_text:
        # A photo turn may legitimately carry only a caption; if even that is
        # empty, fall back to a warm nudge so the player never gets silence.
        clean_text = "" if photo_id is not None else antispam.low_content_reply(answer_lang)

    new_count = await db.persist_turn(
        session_id=session_id,
        user_text=user_text,
        user_lang=detected_lang,
        assistant_text=clean_text or ("[photo]" if photo_id else ""),
        assistant_lang=answer_lang,
        product_id=product_id,
        ai_meta={
            "model": result.model, "key_used": result.key_used,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cached_in": result.cached_in, "cost_usd": cost,
            "latency_ms": result.latency_ms, "ok": True, "error": None,
        },
    )
    log.info(
        "retention_turn_done session_id=%s photo=%s handoff=%s stage_up=%s elapsed_ms=%s",
        session_id, photo_id, handoff, stage_up,
        int((time.monotonic() - started) * 1000),
    )
    return RetentionReply(
        reply=clean_text, lang=answer_lang, message_count=new_count,
        photo_id=photo_id, handoff=handoff, stage_up_hint=stage_up,
        link_url=link_url, link_label=link_label,
    )


@dataclass
class PingDraft:
    """A generated (NOT yet persisted) proactive ping message.

    The worker sends it first and persists only what was actually delivered
    (db.persist_ping_turn); on a send failure it logs the AI cost directly so
    invariant §4 (every OpenAI call -> ai_interaction_logs) still holds.
    """
    text: str
    lang: str
    photo_id: Optional[int]
    ai_meta: dict[str, Any]
    link_url: Optional[str] = None    # validated site-map CTA button url
    link_label: Optional[str] = None  # the site-map page title for the button


async def generate_retention_ping(
    session: dict[str, Any],
    *,
    idle_days: int,
    reason: str,
    intent: str,
    photo_candidates: Optional[list[dict[str, Any]]] = None,
) -> Optional[PingDraft]:
    """Generate ONE proactive re-engagement message for a matched ping rule.

    Returns None on a transient model failure — the worker then simply skips
    the player this run (a ping is never replaced by a canned broadcast; the
    whole point is a personal message). The failure is AI-logged here.
    """
    session_id = session["id"]
    product_id = session.get("product_id")
    tenancy.set_current_product(product_id)
    candidates = photo_candidates or []
    candidate_ids = {int(c["id"]) for c in candidates}
    lang = (session.get("conv_lang") or session.get("lang")
            or language.default_code())

    kb_text = await db.retention_kb_block(product_id) if product_id else ""
    kb_block = await kb.render_variables(kb_text, product_id=product_id) if kb_text else None
    history = await db.get_history(
        session_id, limit=settings.general()["history_max_turns"],
    )
    messages = prompts.build_retention_ping_messages(
        session=session,
        kb_block=kb_block,
        history=history,
        resolved_lang=lang,
        idle_days=idle_days,
        reason=reason,
        intent=intent,
        photo_candidates=candidates,
    )
    client = await openai_client.client_for_product(product_id)
    try:
        result = await client.complete(
            messages, session_id=session_id, on_failover=_on_failover
        )
        raw_text = result.text
    except Exception as exc:  # noqa: BLE001
        error = f"{exc.__class__.__name__}: {exc}"
        log.exception("retention_ping_model_failed session_id=%s error=%s",
                      session_id, error)
        await db.log_ai_interaction(
            session_id, settings.model()["model"], "none",
            0, 0, 0, 0.0, 0, False, error, product_id=product_id,
        )
        await db.log_admin_event_sampled(session_id, "model_error",
                                         {"error": error[:300], "ping": True})
        return None

    clean_text = raw_text or ""
    detected_lang: Optional[str] = None
    photo_id: Optional[int] = None
    link_raw: Optional[str] = None
    if clean_text:
        clean_text, detected_lang = prompts.strip_language_tag(clean_text)
        # A ping never hands off or advances stages; strip defensively anyway.
        clean_text, _ = prompts.strip_handoff_tag(clean_text)
        clean_text, _ = prompts.strip_stage_up_tag(clean_text)
        clean_text, photo_id = prompts.strip_photo_tag(clean_text)
        clean_text, link_raw = prompts.strip_link_tag(clean_text)
        clean_text = telegram_format.normalize_punctuation(clean_text)
    if detected_lang and detected_lang not in language.supported_codes():
        detected_lang = None
    if photo_id is not None and photo_id not in candidate_ids:
        photo_id = None
    link_url, link_label = resolve_site_link(link_raw)
    if not clean_text and photo_id is None:
        # Nothing usable came back — treat like a failure, skip the ping.
        log.warning("retention_ping_empty session_id=%s", session_id)
        return None

    cost = openai_client.compute_cost(
        result.model, result.tokens_in, result.tokens_out, result.cached_in
    )
    answer_lang = detected_lang or lang
    return PingDraft(
        text=clean_text,
        lang=answer_lang,
        photo_id=photo_id,
        link_url=link_url,
        link_label=link_label,
        ai_meta={
            "model": result.model, "key_used": result.key_used,
            "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
            "cached_in": result.cached_in, "cost_usd": cost,
            "latency_ms": result.latency_ms, "ok": True, "error": None,
            "lang": answer_lang,
        },
    )
