"""Quality-review admin surface (/admin/quality/*).

The READ endpoints behind the LLM-as-judge pass (ai/reviewer.py): the
score/tag/KB-gap aggregates and the reviews list. The judge itself runs only
from the background worker (reviewer.scheduler_loop) — there is deliberately
no manual re-run surface: reviews are automatic, bounded by the daily cap,
and a chat is re-reviewed only after it has grown. Every route authorizes
through the app/api/admin_auth.py choke points, exactly like the rest of
/admin/*.

Reading follows the dashboard scope convention (`resolve_scope_filter`): no
product selected = the caller's whole accessible scope.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.ai import prompts
from app.core import db
from app.api import admin_auth
from app.api.admin_auth import require_admin

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
