"""Outcome attribution — the feedback loop under every proactive touch.

The retention stack decides, writes and sends; until now nothing measured what
came back. This module closes that loop:

  - RECORDING. Every touch the bot actually delivered opens one
    `retention_outcomes` row at send time: the proactive agent's event
    reaction, an idle-ladder ping, a photo/video handed to the player, and a
    dialogue reply that carried a site-map CTA button. The row carries the
    DIMENSIONS of the touch (event / idle rung / media / link / tone / cost),
    denormalized on purpose — casino events are pruned and media can be
    deleted, so an attribution recomputed later would silently change.
  - ATTRIBUTION. A sweep (paced per product, riding the retention worker tick)
    looks forward from each open row: did the player answer, how fast, how much
    did he write, did he come back to the casino, did he deposit. Once both
    windows elapse the row is `closed` and never re-read — which is what makes
    the effectiveness numbers stable.
  - READING. `db.recent_touch_outcomes` feeds the agent's decision call (what
    this player answered to before), `db.outcome_by_*` feed the admin
    effectiveness cuts, and `db._PHOTO_OUTCOME_SCORE_SQL` orders the photo
    candidate feed by what actually earns replies.

Recording is deliberately BEST-EFFORT: a failure here must never break a send
that already went out, so every helper swallows its exceptions and logs.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

async def record(product_id: int, ru: Optional[dict[str, Any]], *, kind: str,
                 session_id: Optional[str] = None,
                 decision_id: Optional[int] = None,
                 rule_id: Optional[int] = None,
                 event_name: Optional[str] = None,
                 action: Optional[str] = None, tone: Optional[str] = None,
                 photo_id: Optional[int] = None,
                 media_type: Optional[str] = None,
                 link_url: Optional[str] = None,
                 cost_usd: Optional[float] = None) -> Optional[int]:
    """Open an attribution row for a delivered touch. Never raises."""
    try:
        return await db.record_retention_outcome(
            product_id,
            retention_user_id=int(ru["id"]) if ru else None,
            player_id=(ru or {}).get("player_id"),
            kind=kind, session_id=session_id, decision_id=decision_id,
            rule_id=rule_id, event_name=event_name, action=action, tone=tone,
            photo_id=photo_id, media_type=media_type, link_url=link_url,
            cost_usd=cost_usd,
            # The holdout label AT touch time (uplift cuts by it). A proactive
            # touch only ever goes to a treatment player; carrying the label
            # explicitly keeps the uplift query one flat filter.
            holdout_group=(ru or {}).get("holdout_group") or "treatment")
    except Exception:  # noqa: BLE001 - analytics must never break a send
        log.exception("retention_outcome_record_failed product=%s kind=%s",
                      product_id, kind)
        return None


async def record_dialogue_turn(product_id: int, ru: dict[str, Any],
                               session_id: Optional[str], *,
                               photo_id: Optional[int] = None,
                               media_type: Optional[str] = None,
                               link_url: Optional[str] = None
                               ) -> Optional[int]:
    """Attribution for a REACTIVE turn — recorded only when the reply carried
    something measurable (a photo/video or a CTA button). An ordinary text
    answer inside a live conversation has no outcome to attribute: the player
    is already talking.

    No cost is stored: a dialogue turn's model cost belongs to the
    conversation, not to the button it happened to carry, and folding it in
    would make the proactive cost-per-outcome figures meaningless.
    """
    if photo_id is None and not link_url:
        return None
    return await record(product_id, ru, kind="dialogue", session_id=session_id,
                        action="photo" if photo_id is not None else "message",
                        photo_id=photo_id, media_type=media_type,
                        link_url=link_url)


async def run_product_attribution(product_id: int, *, force: bool = False
                                  ) -> dict[str, Any]:
    """Settle this product's open attribution rows (paced; `force` skips it).

    Paced through `retention_worker_jobs` rather than an in-process clock: the
    old dict reset on every deploy (so a frequently-deployed service swept far
    more often than the interval says) and, once a second worker exists, both
    instances would sweep the same product at once.
    """
    interval = config.RETENTION_OUTCOME_SWEEP_INTERVAL_SEC
    if not force and not await db.claim_worker_job(product_id, "attribution",
                                                   interval):
        return {"skipped": "paced"}
    try:
        updated = await db.attribute_retention_outcomes(
            product_id,
            reply_window_hours=config.RETENTION_OUTCOME_REPLY_WINDOW_HOURS,
            conversion_window_hours=(
                config.RETENTION_OUTCOME_CONVERSION_WINDOW_HOURS),
            limit=config.RETENTION_OUTCOME_SWEEP_BATCH)
    except Exception:  # noqa: BLE001 - one bad sweep must not wedge the worker
        log.exception("retention_outcome_sweep_failed product=%s", product_id)
        return {"error": "sweep_failed"}
    if updated:
        log.info("retention_outcome_sweep product=%s rows=%s",
                 product_id, updated)
    return {"attributed": updated}
