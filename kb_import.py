"""Bulk KB import parsers — JSON / CSV / Markdown (pure, testable).

Each parser returns a list of normalized rows:
    {"topic_slug": str, "lang": str, "content": str}

The admin endpoint (api/admin.py) maps these onto topics (by slug) and inserts
versioned entries via db.create_kb_entry. Parsing is side-effect-free so it can
be unit-tested without a database.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

DEFAULT_LANG = "ru"


class ImportError_(ValueError):
    """Raised on malformed import payloads (distinct from builtin ImportError)."""


def _norm_row(topic_slug: Any, lang: Any, content: Any) -> dict[str, str]:
    if not topic_slug or not str(topic_slug).strip():
        raise ImportError_("each row needs a non-empty topic_slug")
    if not content or not str(content).strip():
        raise ImportError_("each row needs non-empty content")
    return {
        "topic_slug": str(topic_slug).strip(),
        "lang": (str(lang).strip().lower() if lang else DEFAULT_LANG),
        "content": str(content).strip(),
    }


def parse_json(text: str) -> list[dict[str, str]]:
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise ImportError_(f"invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ImportError_("JSON import must be a list of objects")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            raise ImportError_("each JSON item must be an object")
        rows.append(_norm_row(item.get("topic_slug"), item.get("lang"),
                              item.get("content")))
    return rows


def parse_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "topic_slug" not in reader.fieldnames \
            or "content" not in reader.fieldnames:
        raise ImportError_("CSV header must include topic_slug,lang,content")
    rows = []
    for r in reader:
        rows.append(_norm_row(r.get("topic_slug"), r.get("lang"), r.get("content")))
    return rows


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return s or "imported"


def parse_markdown(text: str, fallback_slug: str = "",
                   lang: str = DEFAULT_LANG) -> list[dict[str, str]]:
    """One file per topic: first H1 (or fallback_slug/filename) -> topic, the
    remaining body -> one entry. Returns a single-row list."""
    lines = text.splitlines()
    slug = fallback_slug
    body_start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            slug = slugify(m.group(1))
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    if not slug:
        raise ImportError_("markdown import needs an H1 heading or a filename")
    return [_norm_row(slug, lang, body)]


def parse(content: str, fmt: str, *, fallback_slug: str = "",
          lang: str = DEFAULT_LANG) -> list[dict[str, str]]:
    fmt = (fmt or "").lower()
    if fmt == "json":
        return parse_json(content)
    if fmt == "csv":
        return parse_csv(content)
    if fmt in ("md", "markdown"):
        return parse_markdown(content, fallback_slug=fallback_slug, lang=lang)
    raise ImportError_(f"unsupported import format: {fmt!r}")
