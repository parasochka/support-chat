"""Quality review — the LLM-as-judge pass over finished conversations.

The service can already tell you what it SPENT and what it SENT; until now
nothing said whether the conversations were any GOOD. This worker reads
finished chats — both facades, the support widget and the Telegram retention
bot — and stores one verdict per conversation: a 1..5 score, tags from the
fixed `prompts.REVIEW_TAGS` taxonomy, a one-line summary, quoted issues and the
player questions the knowledge base could not answer.

Design notes:

  - It CHANGES NOTHING. The verdicts are read by the admin Quality page; a
    human decides what to fix. An automated judge that edited the KB or the
    settings would be a second, unreviewable author.
  - It is BOUNDED. One cheap call per conversation, at most
    `general.quality_review_daily_max` per product per day (the same UTC day
    boundary the retention budget uses), only conversations that are finished
    (resolved/escalated or dormant) and long enough to judge, and a chat is
    re-reviewed only after it has grown since its last verdict.
  - It runs on the PRODUCT's own OpenAI keys and model group (like the photo
    cataloguing), and every call lands in `ai_interaction_logs` with
    `session_id=NULL` — invariant §4, without polluting the reviewed session's
    own per-turn costs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from app.ai import openai_client
from app.ai import prompts
from app.core import config
from app.core import db
from app.core import settings
from app.core import tenancy

log = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.S)
_VALID_TAGS = frozenset(slug for slug, _desc in prompts.REVIEW_TAGS)


def parse_review(text: str) -> Optional[dict[str, Any]]:
    """Parse + clamp the judge's JSON. None when nothing usable came back.

    Everything is bounded to what the storage and the admin page expect:
    unknown tags are dropped (the taxonomy is closed on purpose — free-form
    tags cannot be counted), the score is clamped to 1..5, and the free-text
    fields are length-capped.
    """
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    score: Optional[int]
    try:
        score = int(raw.get("score"))
    except (TypeError, ValueError):
        score = None
    if score is not None:
        score = max(1, min(5, score))
    tags = [str(t).strip().lower() for t in (raw.get("tags") or [])
            if isinstance(t, (str, int))]
    tags = [t for t in dict.fromkeys(tags) if t in _VALID_TAGS][:4]
    issues: list[dict[str, str]] = []
    for item in (raw.get("issues") or [])[:3]:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()[:400]
        why = str(item.get("why") or "").strip()[:300]
        if quote or why:
            issues.append({"quote": quote, "why": why})
    gaps = [str(g).strip()[:300] for g in (raw.get("kb_gaps") or [])
            if str(g or "").strip()][:3]
    return {
        "score": score,
        "tags": tags,
        "summary": str(raw.get("summary") or "").strip()[:600],
        "issues": issues,
        "kb_gaps": gaps,
    }


async def review_session(product_id: int, session: dict[str, Any]
                         ) -> Optional[dict[str, Any]]:
    """Judge ONE conversation and store the verdict. Returns it, or None when
    the transcript is unusable or the model call failed."""
    session_id = str(session["id"])
    # The judge's spend belongs to the facade of the conversation it read: a
    # review of a Telegram chat is a retention cost, a review of a widget chat a
    # support cost. Both land in ai_interaction_logs with session_id NULL (the
    # reviewed session's own per-turn costs stay clean), so the label is the ONLY
    # thing that tells the two apart.
    consumer = session.get("consumer") or "web"
    history = await db.get_history(session_id, limit=200)
    if not any(m.get("role") == "user" for m in history):
        # Nothing the player said — there is no handling to judge. Still book
        # the attempt (free — no model call): the selection SQL's EXISTS guard
        # checks the WHOLE transcript, so a long session whose only user turn
        # is older than this window would otherwise be re-selected on every
        # pass, permanently wasting a batch slot.
        await db.record_review_attempt(
            product_id, session_id, consumer=consumer,
            reviewed_msg_count=int(session.get("message_count") or 0))
        return None
    topic = None
    if session.get("topic_id"):
        topic = await _topic_slug(product_id, session["topic_id"])
    messages = prompts.build_conversation_review_messages(
        history, consumer=consumer, topic=topic,
        lang=session.get("lang"),
        status=("escalated" if session.get("escalated")
                else session.get("status")))
    client = await openai_client.client_for_product(product_id)
    try:
        # `review`: a whole transcript per call and nobody waiting for it — its
        # own (longer) request timeout, and no fallback-key race.
        result = await client.complete(messages, purpose="review")
    except Exception as exc:  # noqa: BLE001 - one failed review is not fatal
        await db.log_ai_interaction(
            None, settings.model()["model"], "none", 0, 0, 0, 0.0, 0, False,
            f"quality_review: {exc.__class__.__name__}", product_id=product_id,
            consumer=consumer, source="review")
        log.warning("quality_review_model_failed product=%s session=%s error=%s",
                    product_id, session_id, exc)
        return None
    cost = openai_client.compute_cost(result.model, result.tokens_in,
                                      result.tokens_out, result.cached_in)
    await db.log_ai_interaction(
        None, result.model, result.key_used, result.tokens_in,
        result.tokens_out, result.cached_in, cost, result.latency_ms, True,
        None, product_id=product_id, consumer=consumer, source="review")
    verdict = parse_review(result.text)
    if verdict is None:
        # Record the failed ATTEMPT — bookkeeping only, never through the
        # verdict upsert. Without a row `sessions_for_review` keeps selecting
        # this chat (it filters on `reviewed_msg_count`), so an unparseable
        # transcript would be paid for on every pass forever — and outside the
        # daily cap, which only counts stored rows. `record_review_attempt`
        # advances the bookkeeping WITHOUT touching verdict fields: a session
        # that was reviewed, grew, and then failed its re-review keeps its
        # previously stored verdict on the Quality page.
        await db.record_review_attempt(
            product_id, session_id, consumer=consumer,
            reviewed_msg_count=int(session.get("message_count") or 0),
            model=result.model, cost_usd=float(cost or 0))
        log.warning("quality_review_unparseable product=%s session=%s",
                    product_id, session_id)
        return None
    await db.upsert_conversation_review(
        product_id, session_id, consumer=consumer,
        score=verdict["score"], tags=verdict["tags"],
        summary=verdict["summary"], issues=verdict["issues"],
        kb_gaps=verdict["kb_gaps"],
        reviewed_msg_count=int(session.get("message_count") or 0),
        model=result.model, cost_usd=float(cost or 0))
    return {**verdict, "session_id": session_id,
            "cost_usd": float(cost or 0)}


async def _topic_slug(product_id: int, topic_id: Any) -> Optional[str]:
    try:
        topics = await db.list_topics(product_id)
    except Exception:  # noqa: BLE001 - context only
        log.warning("quality_review_topic_lookup_failed product=%s",
                    product_id, exc_info=True)
        return None
    for t in topics:
        if t.get("id") == topic_id:
            return t.get("slug")
    return None


async def run_product_reviews(product: dict[str, Any]) -> dict[str, Any]:
    """Review this product's pending conversations, honouring its daily cap.

    The scope is bound with `scoped_product`, not left set: this runs inside the
    worker's single long-lived task, so a bare set would leave the last product
    of the pass bound for everything the task does afterwards.
    """
    pid = int(product["id"])
    with tenancy.scoped_product(pid):
        cfg = settings.general()
        if not cfg.get("quality_review_enabled"):
            return {"skipped": "disabled"}
        daily_max = int(cfg.get("quality_review_daily_max") or 0)
        if daily_max <= 0:
            return {"skipped": "daily_max_zero"}
        done_today = await db.reviews_today(pid)
        budget = daily_max - done_today
        if budget <= 0:
            return {"skipped": "daily_max_reached"}
        batch = min(budget, _PASS_BATCH)
        sessions = await db.sessions_for_review(
            pid, min_messages=int(cfg.get("quality_review_min_messages") or 4),
            idle_minutes=config.QUALITY_REVIEW_IDLE_MINUTES, limit=batch)
        reviewed = failed = 0
        for session in sessions:
            try:
                if await review_session(pid, session):
                    reviewed += 1
                else:
                    failed += 1
            except Exception:  # noqa: BLE001 - one bad chat must not stop the pass
                log.exception("quality_review_failed product=%s session=%s",
                              pid, session.get("id"))
                failed += 1
        if reviewed or failed:
            log.info("quality_review_pass product=%s reviewed=%s failed=%s",
                     pid, reviewed, failed)
        return {"reviewed": reviewed, "failed": failed,
                "considered": len(sessions)}


async def run_due_reviews() -> dict[str, Any]:
    """One pass across every active product (advisory-locked, like the
    retention worker: two instances must not review the same chat twice).

    The lock rides a DEDICATED connection (db.dedicated_connection), not a
    pool slot — the same contract as retention_v2.run_due_events and the media
    normalizer: this pass holds the lock across every product × up to
    _PASS_BATCH review calls with the 120s `review` timeout, i.e. potentially
    tens of minutes, and parking that on a pooled connection starves one of
    the pool's 10 request slots for the duration (and the unbounded
    pool.acquire() wait opted this path out of DB_ACQUIRE_TIMEOUT_SEC).
    """
    conn = await db.dedicated_connection()
    try:
        got = await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                  _ADVISORY_LOCK_KEY)
        if not got:
            return {"skipped": "another instance holds the lock"}
        try:
            totals = {"products": 0, "reviewed": 0, "failed": 0}
            for product in await db.list_active_products():
                stats = await run_product_reviews(product)
                totals["products"] += 1
                for k in ("reviewed", "failed"):
                    if stats.get(k):
                        totals[k] += stats[k]
            return totals
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)",
                               _ADVISORY_LOCK_KEY)
    finally:
        # Closing the session releases the advisory lock too, so a failed
        # explicit unlock can never wedge the sweep for other instances.
        await conn.close()


# Arbitrary but stable advisory-lock key for the review sweep ("QREV").
_ADVISORY_LOCK_KEY = 0x51524556

# Conversations judged per product per sweep tick — the daily cap still wins.
_PASS_BATCH = 10


async def scheduler_loop() -> None:
    """Background loop: review finished conversations every
    QUALITY_REVIEW_INTERVAL_SEC. Started from main.py's lifespan under the same
    deploy switch as the other background workers."""
    log.info("quality review worker started (interval=%ss)",
             config.QUALITY_REVIEW_INTERVAL_SEC)
    while True:
        try:
            await run_due_reviews()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            raise
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("quality_review_sweep_failed")
        await asyncio.sleep(max(int(config.QUALITY_REVIEW_INTERVAL_SEC), 60))
