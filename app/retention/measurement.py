"""Measurement: holdout control group + uplift (DOC-1).

The retention stack could always say what it sent and what came back
(outcomes.py); this module answers the harder question — what would have
happened WITHOUT the touch. A deterministic share of players
(`retention.holdout_pct`, ships at 15% by owner decision) is held out of every
proactive contact; their would-have-been touches are recorded as VIRTUAL
outcome rows (kind='holdout', nothing is sent), the same attribution sweep
fills in what they did anyway, and uplift = treatment rate minus holdout rate
is the honest answer to "does retention work".

Invariants:
  - The split is a pure function of (product_id, player_id, salt) — one player
    is in one group for a given salt, across restarts and instances. Rotating
    the salt starts a NEW experiment (every player re-buckets lazily).
  - Holdout is a GUARD (guard_check / the idle sweep consult it BEFORE any
    model call); the model can never override it.
  - Everything here is read/annotate-only beyond that guard verdict: no path
    in this module sends anything.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

TREATMENT = "treatment"
HOLDOUT = "holdout"


def holdout_pct(cfg: dict[str, Any]) -> int:
    v = cfg.get("holdout_pct")
    return config.RETENTION_HOLDOUT_PCT if v is None else int(v)


def holdout_salt(cfg: dict[str, Any]) -> str:
    return str(cfg.get("holdout_salt") or config.RETENTION_HOLDOUT_SALT)


def bucket_of(product_id: int, player_id: str, salt: str) -> int:
    """Deterministic 0..99 bucket for the (product, player, salt) triple."""
    h = hashlib.sha256(f"{product_id}:{player_id}:{salt}".encode()).hexdigest()
    return int(h[:8], 16) % 100


async def resolve_holdout_group(product_id: int, ru: dict[str, Any],
                                cfg: dict[str, Any]) -> str:
    """The player's group for the CURRENT experiment ('treatment'/'holdout').

    Reads the cached label when it was computed under the current salt, else
    recomputes and caches (best-effort — the value is deterministic, so a
    failed cache write only costs a rehash next time). Players without a
    player_id (no casino link yet) are always treatment: they cannot convert
    in the casino, so holding them out only cuts reach.

    Mutates `ru` in place with the resolved group so the same request's later
    readers (the ledger snapshot, outcomes.record) see it without a re-query.
    """
    pct = holdout_pct(cfg)
    player_id = str(ru.get("player_id") or "")
    if pct <= 0 or not player_id:
        ru["holdout_group"] = TREATMENT
        return TREATMENT
    salt = holdout_salt(cfg)
    cached = ru.get("holdout_group")
    if cached in (TREATMENT, HOLDOUT) and ru.get("holdout_salt") == salt:
        return str(cached)
    grp = HOLDOUT if bucket_of(product_id, player_id, salt) < pct else TREATMENT
    ru["holdout_group"] = grp
    ru["holdout_salt"] = salt
    rid = ru.get("id")
    if rid is not None:
        try:
            await db.set_holdout_group(product_id, int(rid), grp, salt)
        except Exception:  # noqa: BLE001 - deterministic; recomputed next time
            log.debug("holdout_cache_write_failed product=%s player=%s",
                      product_id, player_id, exc_info=True)
    return grp


async def open_virtual_outcome(product_id: int, ru: dict[str, Any],
                               event_name: Optional[str]) -> None:
    """The control group's base-rate marker: the moment a touch WOULD have
    gone out to a held-out player, record a virtual outcome row (no send, no
    cost). The normal attribution sweep fills in whether the player returned /
    deposited anyway — that is the base rate uplift compares against.
    Best-effort by contract (never breaks the pipeline)."""
    try:
        await db.record_retention_outcome(
            product_id,
            retention_user_id=int(ru["id"]) if ru.get("id") else None,
            player_id=ru.get("player_id"), kind="holdout",
            event_name=event_name, action="held_out",
            holdout_group=HOLDOUT)
    except Exception:  # noqa: BLE001
        log.exception("holdout_virtual_outcome_failed product=%s", product_id)


async def compute_uplift(product_id: int, dt_from: Any, dt_to: Any, *,
                         snapshot: bool = False) -> list[dict[str, Any]]:
    """Uplift per conversion type over a window (optionally materialized)."""
    rows = await db.retention_uplift(product_id, dt_from, dt_to)
    if snapshot:
        for row in rows:
            try:
                await db.write_uplift_snapshot(product_id, dt_from, dt_to, row)
            except Exception:  # noqa: BLE001 - the live answer still returns
                log.exception("uplift_snapshot_write_failed product=%s",
                              product_id)
    return rows
