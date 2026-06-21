"""KB load helpers + topic catalogue access.

Thin layer over db.* so chat_service / api never reach into tables directly.
KB content is stored in Russian (source language) and injected as Layer 2.
"""
from __future__ import annotations

from typing import Any, Optional

import db

# The hidden free-text topic that routes to high escalation priority.
OTHER_SLUG = "other"


async def catalogue(lang: str = "en") -> list[dict[str, str]]:
    """Visible topic picker for the widget: [{slug, title}], 'other' excluded."""
    topics = await db.list_topics(include_hidden=False)
    out: list[dict[str, str]] = []
    for t in topics:
        title = t["title"]
        out.append({"slug": t["slug"], "title": _pick_title(title, lang)})
    return out


def _pick_title(title: dict[str, str], lang: str) -> str:
    if not isinstance(title, dict):
        return str(title)
    return title.get(lang) or title.get("en") or next(iter(title.values()), "")


async def topic_by_slug(slug: str) -> Optional[dict[str, Any]]:
    return await db.get_topic_by_slug(slug)


async def kb_block_for_topic(topic_id: Optional[int]) -> Optional[str]:
    """Return the Russian KB chunk for the selected topic (Layer 2), or None."""
    if topic_id is None:
        return None
    return await db.get_kb_content(topic_id, lang="ru")


def is_other(slug: str) -> bool:
    return slug == OTHER_SLUG
