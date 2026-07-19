"""KB load helpers + topic catalogue access (product-scoped).

Thin layer over db.* so chat_service / api never reach into tables directly.
KB content is stored in Russian (source language) and injected as Layer 2.
Every helper resolves the PRODUCT from the request's tenancy scope (set by the
API layer from the widget key / session / admin selection) unless the caller
passes `product_id` explicitly, so each casino sees only its own topics, KB
texts and {placeholder} variables.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import db
import tenancy

_VARIABLE_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

# The always-available catch-all topic. A NORMAL, visible topic like any other
# (nothing is ever hidden from the picker); it is only excluded as a routing
# TARGET in suggestable_topics — the model may route out of it, never into it.
OTHER_SLUG = "other"


def _pid(product_id: Optional[int]) -> Optional[int]:
    return product_id if product_id is not None else tenancy.current_product_id()


async def catalogue(lang: str = "en",
                    product_id: Optional[int] = None) -> list[dict[str, str]]:
    """Topic picker for the widget: [{slug, title}] — the full catalogue,
    'other' included (it sorts last; no topic is hidden)."""
    topics = await db.list_topics(_pid(product_id))
    out: list[dict[str, str]] = []
    for t in topics:
        title = t["title"]
        out.append({"slug": t["slug"], "title": _pick_title(title, lang)})
    return out


def _pick_title(title: dict[str, str], lang: str) -> str:
    if not isinstance(title, dict):
        return str(title)
    return title.get(lang) or title.get("en") or next(iter(title.values()), "")


def localize_title(title: Any, lang: str = "en") -> str:
    """Public wrapper over `_pick_title` for callers outside this module."""
    return _pick_title(title, lang)


async def topic_by_slug(slug: str,
                        product_id: Optional[int] = None) -> Optional[dict[str, Any]]:
    return await db.get_topic_by_slug(_pid(product_id), slug)


async def kb_block_for_topic(topic_id: Optional[int],
                             product_id: Optional[int] = None) -> Optional[str]:
    """Return the KB chunk for the selected topic (Layer 2), or None.

    Variables like ``{min_deposit}`` are resolved from the admin-managed
    ``kb_variables`` registry before the text is injected into the model prompt,
    so the prompt receives the enriched, concrete knowledge rather than raw
    placeholders. Unknown placeholders are intentionally left untouched to make
    missing registry items visible in the prompt preview.
    """
    if topic_id is None:
        return None
    content = await db.get_kb_content(topic_id)
    if not content:
        return content
    return await render_variables(content, product_id=product_id)


async def render_variables(text: str, product_id: Optional[int] = None) -> str:
    """Replace ``{variable}`` placeholders with admin-managed values."""
    variables = await db.get_kb_variables_map(_pid(product_id))

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _VARIABLE_RE.sub(repl, text)


async def suggestable_topics(
    exclude_topic_id: Optional[int] = None, lang: str = "en",
    product_id: Optional[int] = None
) -> list[dict[str, str]]:
    """Topics the model may route the player to: [{slug, title}].

    The catalogue minus the current topic and minus 'other' — 'other' is a
    normal visible topic in the picker, but it is never offered as a routing
    TARGET (the model may route out of it, never dump a player into it). Used
    to build the Layer-3 routing list and to resolve a suggested slug back to
    a localized title for the front-end payload.
    """
    topics = await db.list_topics(_pid(product_id))
    out: list[dict[str, str]] = []
    for t in topics:
        if t["slug"] == OTHER_SLUG:
            continue
        if exclude_topic_id is not None and t["id"] == exclude_topic_id:
            continue
        out.append({"slug": t["slug"], "title": _pick_title(t["title"], lang)})
    return out
