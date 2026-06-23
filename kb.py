"""KB load helpers + topic catalogue access.

Thin layer over db.* so chat_service / api never reach into tables directly.
KB content is stored in Russian (source language) and injected as Layer 2.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import db

_VARIABLE_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

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


def localize_title(title: Any, lang: str = "en") -> str:
    """Public wrapper over `_pick_title` for callers outside this module."""
    return _pick_title(title, lang)


async def topic_by_slug(slug: str) -> Optional[dict[str, Any]]:
    return await db.get_topic_by_slug(slug)


async def kb_block_for_topic(topic_id: Optional[int]) -> Optional[str]:
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
    return await render_variables(content)


async def render_variables(text: str) -> str:
    """Replace ``{variable}`` placeholders with admin-managed values."""
    variables = await db.get_kb_variables_map()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _VARIABLE_RE.sub(repl, text)


async def suggestable_topics(
    exclude_topic_id: Optional[int] = None, lang: str = "en"
) -> list[dict[str, str]]:
    """Topics the model may route the player to: [{slug, title}].

    The visible catalogue minus the current topic (and 'other', already hidden
    by db.list_topics). Used to build the Layer-3 routing list and to resolve a
    suggested slug back to a localized title for the front-end payload.
    """
    topics = await db.list_topics(include_hidden=False)
    out: list[dict[str, str]] = []
    for t in topics:
        if exclude_topic_id is not None and t["id"] == exclude_topic_id:
            continue
        out.append({"slug": t["slug"], "title": _pick_title(t["title"], lang)})
    return out


def is_other(slug: str) -> bool:
    return slug == OTHER_SLUG
