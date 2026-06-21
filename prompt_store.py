"""Live system-prompt core loader + deterministic A/B assignment.

Phase 1 baked the core into the byte-stable `prompts.SYSTEM_CORE` constant.
Phase 2 loads the live core from the `prompt_versions` table instead, keyed by
`prompt_version_id`. Within a published version the body is immutable, so the
in-process cache below keeps the OpenAI prefix warm exactly as Phase 1 — the
cache boundary is unchanged; only *which* core string fronts it can change, and
only via a deliberate publish (a one-time cache reset).

Assignment: at session creation a version is picked deterministically from the
active A/B split (published rows with ab_weight > 0) via a stable hash of the
session id, so attribution is reproducible. With no active split, the live
default is used.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

import db
import prompts

# version_id -> body. Published bodies are immutable, so this never goes stale
# for live traffic; invalidate() is offered for after a publish, out of caution.
_body_cache: dict[int, str] = {}


def invalidate() -> None:
    _body_cache.clear()


async def core_for_version(version_id: Optional[int]) -> str:
    """Return the core body for a version id, falling back to the Phase 1 core.

    Used per request via the session's `prompt_version_id`. A missing id or row
    falls back to `prompts.SYSTEM_CORE` so the service never serves an empty core.
    """
    if version_id is None:
        return prompts.SYSTEM_CORE
    if version_id in _body_cache:
        return _body_cache[version_id]
    row = await db.get_prompt_version(version_id)
    if row is None:
        return prompts.SYSTEM_CORE
    _body_cache[version_id] = row["body"]
    return row["body"]


def assign_version(session_id: str, versions: list[dict[str, Any]]) -> Optional[int]:
    """Deterministically pick a version id from a weighted A/B split.

    `versions` is a list of rows with `id` and `ab_weight`. The pick is a stable
    function of `session_id` (so the same session always maps to the same
    version), weighted by ab_weight. Returns None if there is nothing to pick.
    """
    active = [(v["id"], int(v["ab_weight"])) for v in versions
              if int(v.get("ab_weight", 0)) > 0]
    total = sum(w for _, w in active)
    if total <= 0:
        return None
    # Stable 0..total-1 bucket from a hash of the session id.
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    bucket = int(digest, 16) % total
    cursor = 0
    for vid, weight in active:
        cursor += weight
        if bucket < cursor:
            return vid
    return active[-1][0]  # pragma: no cover - guarded by total math above


async def resolve_for_new_session(session_id: str) -> Optional[int]:
    """Choose the prompt_version_id for a brand-new session (A/B or default)."""
    ab = await db.get_active_ab_versions()
    if ab:
        picked = assign_version(session_id, ab)
        if picked is not None:
            return picked
    default = await db.get_default_prompt_version()
    return default["id"] if default else None
