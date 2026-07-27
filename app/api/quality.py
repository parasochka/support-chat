"""Quality-review admin surface (/admin/quality/*).

The read + run endpoints behind the LLM-as-judge pass (ai/reviewer.py): the
score/tag/KB-gap aggregates, the reviews list, and two manual triggers (review
one conversation now, run a product's pass now). Every route authorizes through
the app/api/admin_auth.py choke points, exactly like the rest of /admin/*.

Reading follows the dashboard scope convention (`resolve_scope_filter`): no
product selected = the caller's whole accessible scope. Running the judge is a
WRITE on one product (it spends the product's OpenAI budget), so it is
product-scoped and admin-only.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.ai import prompts
from app.ai import reviewer
from app.core import db
from app.api import admin_auth
from app.api.admin_auth import require_admin, require_admin_write

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/quality", tags=["quality"],
                   dependencies=[Depends(require_admin)])


@router.get("/overview")
async def overview(product_id: Optional[int] = None,
                   partner_id: Optional[int] = None,
                   from_: Optional[str] = Query(default=None, alias="from"),
                   to: Optional[str] = None,
                   admin=Depends(require_admin)) -> JSONResponse:
    """Score distribution, tag counts and the top KB gaps for the range."""
    from app.api.admin import _range
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    data = await db.quality_overview(dt_from, dt_to, product_ids=scope)
    # The tag vocabulary travels with the data so the page can label tags
    # without duplicating the taxonomy that lives in prompts.py.
    data["taxonomy"] = [{"tag": slug, "description": desc}
                        for slug, desc in prompts.REVIEW_TAGS]
    return JSONResponse(content=data)


@router.get("/reviews")
async def reviews(product_id: Optional[int] = None,
                  partner_id: Optional[int] = None,
                  from_: Optional[str] = Query(default=None, alias="from"),
                  to: Optional[str] = None,
                  consumer: Optional[str] = None,
                  tag: Optional[str] = None,
                  max_score: Optional[int] = None,
                  page: int = 1, page_size: int = 25,
                  admin=Depends(require_admin)) -> JSONResponse:
    """The reviews list (worst score first) — the operator's triage queue."""
    from app.api.admin import _range
    scope = await admin_auth.resolve_scope_filter(admin, product_id, partner_id)
    dt_from, dt_to = _range(from_, to)
    return JSONResponse(content=await db.list_conversation_reviews(
        dt_from, dt_to, product_ids=scope,
        consumer=consumer if consumer in ("web", "telegram") else None,
        tag=tag or None,
        max_score=max_score if max_score in (1, 2, 3, 4, 5) else None,
        page=max(int(page), 1),
        page_size=max(1, min(int(page_size), 100))))


class RunBody(BaseModel):
    product_id: int
    limit: Optional[int] = None


@router.post("/run")
async def run_now(body: RunBody,
                  admin=Depends(require_admin_write)) -> JSONResponse:
    """Run this product's review pass immediately (the page's «Review now»).

    Bounded by the same daily cap as the worker — the button cannot be used to
    spend past it — and advisory-locked inside the worker's own sweep helper is
    NOT needed here: reviews are idempotent per (session, message_count), so a
    concurrent pass at worst re-reads a chat it already stored.
    """
    await admin_auth.require_product_write(admin, body.product_id)
    product = await db.get_product(body.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    stats = await reviewer.run_product_reviews(
        product, limit=max(1, min(int(body.limit or 10), 50)))
    await db.log_admin_event(None, "quality_review_run", stats,
                             product_id=body.product_id)
    return JSONResponse(content=stats)


@router.post("/review/{session_id}")
async def review_one(session_id: str,
                     admin=Depends(require_admin_write)) -> JSONResponse:
    """Judge ONE conversation now (the transcript dialog's «Review» action)."""
    try:
        _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Session not found.")
    detail = await db.session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    session: dict[str, Any] = detail.get("session") or {}
    product_id = session.get("product_id")
    if product_id is None:
        admin_auth.require_global_write(admin)
        raise HTTPException(status_code=400,
                            detail="Session has no product to review under.")
    await admin_auth.require_product_write(admin, int(product_id))
    verdict = await reviewer.review_session(int(product_id), {
        "id": session_id,
        "consumer": session.get("consumer"),
        "lang": session.get("lang"),
        "status": session.get("status"),
        "escalated": session.get("escalated"),
        "topic_id": session.get("topic_id"),
        "message_count": session.get("message_count") or 0,
    })
    if verdict is None:
        raise HTTPException(status_code=502,
                            detail="The reviewer could not judge this chat.")
    return JSONResponse(content=verdict)
