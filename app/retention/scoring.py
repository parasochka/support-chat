"""Player scoring: dormancy cohorts, RFM, value tiers, VIP segment (DOC-5).

A deterministic scoring layer over the existing state resolver — no ML, no
new infrastructure. The heavy computation runs in the worker sweep and is
CACHED on retention_users; the hot path (guards, the resolver, the offer
eligibility, the frequency cohort) only reads the cache.

Dimensions (each tagged with its source):
  - dormancy_cohort (computed): active / dormant_d7 / d10 / d14 / d21 / d30 /
    lost — the five EPIC-5 recovery cohorts + the >60d archive, classified
    from the same idle clock the resolver uses. A transition is logged once
    per day per cohort (dedup index) and the return to activity flips the
    cohort back on the HOT path (an event, not the sweep) — a returned player
    must never be greeted as a dormant one.
  - rfm (computed): deterministic banded recency/frequency/monetary from the
    event feed, composite 1..5.
  - value_tier (computed): low / mid / high / whale by lifetime deposits.
  - vip_segment (from_casino): mapped from the casino's loyalty class
    (`vip_level` snapshot; EPIC-7 classes player..platinum -> mass,
    vip -> vip, vip_plus -> vip_plus). The orchestrator never computes
    loyalty itself.

`scoring_enabled` OFF (the default) leaves the resolver snapshot exactly as
before — consumers see cohort None and treat the player as 'mass'.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

COHORTS = ("active", "dormant_d7", "dormant_d10", "dormant_d14",
           "dormant_d21", "dormant_d30", "lost")

# Default cohort boundaries (inclusive day ranges, EPIC-5 §3.2) — hot-config
# via retention_scoring_config('dormancy_boundaries').
DEFAULT_DORMANCY_BOUNDARIES: dict[str, Any] = {
    "d7": [7, 9], "d10": [10, 13], "d14": [14, 20],
    "d21": [21, 29], "d30": [30, 60], "lost": 61,
}

# Default lifetime-deposit thresholds (USD) for the value tiers.
DEFAULT_VALUE_TIERS: dict[str, float] = {
    "low": 0, "mid": 100, "high": 500, "whale": 5000,
}

# Casino loyalty class -> orchestrator VIP segment (EPIC-7 classes). The
# casino's own naming may differ per brand — hot-config via
# retention_scoring_config('vip_mapping').
DEFAULT_VIP_MAPPING: dict[str, str] = {
    "player": "mass", "bronze": "mass", "silver": "mass", "gold": "mass",
    "platinum": "mass", "vip": "vip", "vip+": "vip_plus",
    "vip_plus": "vip_plus",
}


def scoring_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("scoring_enabled")
    return config.RETENTION_SCORING_ENABLED if v is None else bool(v)


def classify_cohort(idle_days: Optional[float],
                    boundaries: Optional[dict[str, Any]] = None) -> str:
    """The dormancy cohort for an idle-days value (None = no signal = active:
    a player the casino has never reported on cannot be 'dormant 60 days')."""
    b = {**DEFAULT_DORMANCY_BOUNDARIES, **(boundaries or {})}
    if idle_days is None or idle_days < b["d7"][0]:
        return "active"
    if idle_days <= b["d7"][1]:
        return "dormant_d7"
    if idle_days <= b["d10"][1]:
        return "dormant_d10"
    if idle_days <= b["d14"][1]:
        return "dormant_d14"
    if idle_days <= b["d21"][1]:
        return "dormant_d21"
    if idle_days <= b["d30"][1]:
        return "dormant_d30"
    return "lost"


def classify_value_tier(lifetime_usd: float,
                        tiers: Optional[dict[str, float]] = None) -> str:
    t = {**DEFAULT_VALUE_TIERS, **(tiers or {})}
    if lifetime_usd >= float(t["whale"]):
        return "whale"
    if lifetime_usd >= float(t["high"]):
        return "high"
    if lifetime_usd >= float(t["mid"]):
        return "mid"
    return "low"


def map_vip_segment(vip_level: Optional[str],
                    mapping: Optional[dict[str, str]] = None) -> str:
    """Casino loyalty class -> mass/vip/vip_plus. Unknown classes are 'mass'
    (never guess a player INTO the VIP handling)."""
    m = {**DEFAULT_VIP_MAPPING, **{k.lower(): v for k, v in
                                   (mapping or {}).items()}}
    return m.get(str(vip_level or "").strip().lower(), "mass")


def is_vip(ru: dict[str, Any]) -> bool:
    """The ONE VIP predicate (DOC-2 vip_suppress, DOC-4 relaxed caps read it).
    Prefers the cached segment; falls back to mapping the raw casino class
    directly so the check works before the first scoring sweep."""
    seg = ru.get("vip_segment")
    if seg:
        return str(seg) in ("vip", "vip_plus")
    return map_vip_segment(ru.get("vip_level")) in ("vip", "vip_plus")


# Deterministic RFM bands (documented, fixed — comparability beats cleverness).
def _band_recency(days: Optional[float]) -> int:
    if days is None:
        return 1
    if days <= 3:
        return 5
    if days <= 7:
        return 4
    if days <= 14:
        return 3
    if days <= 30:
        return 2
    return 1


def _band_frequency(deposits: int) -> int:
    if deposits >= 10:
        return 5
    if deposits >= 5:
        return 4
    if deposits >= 3:
        return 3
    if deposits >= 1:
        return 2
    return 1


def _band_monetary(sum_usd: float) -> int:
    if sum_usd >= 1000:
        return 5
    if sum_usd >= 500:
        return 4
    if sum_usd >= 100:
        return 3
    if sum_usd > 0:
        return 2
    return 1


def rfm_score(recency_days: Optional[float], deposits: int,
              sum_usd: float) -> int:
    return round((_band_recency(recency_days) + _band_frequency(deposits)
                  + _band_monetary(sum_usd)) / 3)


def _idle_days(ru: dict[str, Any], now: Optional[_dt.datetime] = None
               ) -> Optional[float]:
    from app.retention import retention_v2
    candidates = [d for d in (
        retention_v2.days_since(ru.get("last_login_at"), now),
        retention_v2.days_since(ru.get("last_played_at"), now)) if d is not None]
    return min(candidates) if candidates else None


async def recompute_player(product_id: int, ru: dict[str, Any],
                           cfg: dict[str, Any]) -> dict[str, Any]:
    """Recompute + cache one player's scoring. Returns the cached fields."""
    sc_cfg = await db.get_scoring_config(product_id)
    idle = _idle_days(ru)
    cohort = classify_cohort(idle, sc_cfg.get("dormancy_boundaries"))
    prev = ru.get("dormancy_cohort")
    window = int(cfg.get("rfm_window_days")
                 or config.RETENTION_RFM_WINDOW_DAYS)
    inputs = await db.rfm_inputs(product_id, str(ru.get("player_id") or ""),
                                 window_days=window)
    recency = idle
    freq = int(inputs["deposits_window"])
    monetary = float(inputs["sum_window"])
    lifetime = float(inputs["sum_lifetime"])
    fields = {
        "dormancy_cohort": cohort,
        "rfm_recency": int(recency) if recency is not None else None,
        "rfm_frequency": freq,
        "rfm_monetary": monetary,
        "rfm_score": rfm_score(recency, freq, monetary),
        "value_tier": classify_value_tier(lifetime,
                                          sc_cfg.get("value_tiers")),
        "vip_segment": map_vip_segment(ru.get("vip_level"),
                                       sc_cfg.get("vip_mapping")),
        "total_deposit_lifetime": lifetime,
    }
    changed = cohort != (prev or "active") and cohort != prev
    await db.cache_player_scoring(product_id, int(ru["id"]), fields,
                                  cohort_changed=changed)
    if changed:
        await db.log_cohort_transition(
            product_id, str(ru.get("player_id") or ""),
            from_cohort=prev or "active", to_cohort=cohort,
            days_inactive=int(idle or 0))
        await db.log_admin_event(
            None, "dormancy_state_changed",
            {"player_id": ru.get("player_id"), "from": prev or "active",
             "to": cohort, "days": int(idle or 0)}, product_id=product_id)
    ru.update(fields)
    return fields


async def mark_recovered(product_id: int, ru: dict[str, Any]) -> None:
    """HOT-path return detection: any real activity flips a dormant cohort to
    'active' immediately (the sweep would lag by up to its interval)."""
    prev = ru.get("dormancy_cohort")
    if prev in (None, "active"):
        return
    try:
        await db.cache_player_scoring(product_id, int(ru["id"]),
                                      {"dormancy_cohort": "active"},
                                      cohort_changed=True)
        await db.log_cohort_transition(product_id,
                                       str(ru.get("player_id") or ""),
                                       from_cohort=str(prev),
                                       to_cohort="active", days_inactive=0)
        await db.log_admin_event(
            None, "dormancy_recovered",
            {"player_id": ru.get("player_id"), "from": prev},
            product_id=product_id)
        ru["dormancy_cohort"] = "active"
    except Exception:  # noqa: BLE001 - never break the event pipeline
        log.exception("dormancy_recovered_failed product=%s", product_id)


def annotate_state(state: dict[str, Any], ru: dict[str, Any],
                   cfg: dict[str, Any]) -> dict[str, Any]:
    """Additive resolver extension: append the cached scoring dimensions (with
    their sources) when scoring is enabled. OFF = the snapshot is unchanged."""
    if not scoring_enabled(cfg):
        return state
    state.update({
        "dormancy_cohort": ru.get("dormancy_cohort"),
        "rfm": {"recency": ru.get("rfm_recency"),
                "frequency": ru.get("rfm_frequency"),
                "monetary": ru.get("rfm_monetary"),
                "score": ru.get("rfm_score")},
        "value_tier": ru.get("value_tier"),
        "vip_segment": ru.get("vip_segment"),
        "total_deposit_lifetime": ru.get("total_deposit_lifetime"),
        "sources": {"dormancy_cohort": "computed", "rfm": "computed",
                    "value_tier": "computed", "vip_segment": "from_casino"},
    })
    return state


async def run_product_scoring(product_id: int, cfg: dict[str, Any], *,
                              force: bool = False,
                              limit: int = 200) -> dict[str, Any]:
    """One paced scoring pass over the product's least-recently-scored players
    (rides the worker tick like the attribution sweep)."""
    if not scoring_enabled(cfg):
        return {"skipped": "scoring_disabled"}
    interval = int(cfg.get("scoring_sweep_interval_sec")
                   or config.RETENTION_SCORING_SWEEP_INTERVAL_SEC)
    # Paced in Postgres (retention_worker_jobs), not in process memory: the
    # dict reset on every deploy and gave each worker instance its own clock.
    if not force and not await db.claim_worker_job(product_id, "scoring",
                                                   interval):
        return {"skipped": "paced"}
    scored = 0
    try:
        for ru in await db.scoring_candidates(product_id, limit=limit):
            await recompute_player(product_id, ru, cfg)
            scored += 1
    except Exception:  # noqa: BLE001 - one bad pass must not wedge the worker
        log.exception("scoring_sweep_failed product=%s", product_id)
        return {"error": "sweep_failed", "scored": scored}
    if scored:
        log.info("scoring_sweep product=%s scored=%s", product_id, scored)
    return {"scored": scored}
