"""Adaptive frequency + Smart Send Time (DOC-4).

Two deterministic layers over the static anti-annoyance guards:

  - ADAPTIVE FREQUENCY: caps become priority-aware (P1/P2 touches are never
    cut by a cap; P4/P5 promo yields first) and cohort-aware (a VIP row may
    relax, a dormant row may tighten), with the channel as an axis from day
    one (email rides its OWN cap row and does NOT consume the intrusive-touch
    budget — intrusive = push + Telegram, owner decision). OFF by default:
    the legacy static guards (`ping_daily_cap` / `ping_min_gap_hours`) then
    apply bit-for-bit.
  - SMART SEND TIME: a deterministic per-player activity profile (median /
    peak hours from the event feed, in the player's timezone) shifts
    NON-timing-critical touches into the hours the player is actually around,
    max +-`sst_max_shift_hours` from the hint. Timing-critical touches
    (loss rescue) keep the short humanizing delay. Never violates quiet
    hours, RG, or the frequency caps — it picks the hour INSIDE the allowed
    window, never widens it.

Player timezone resolution (owner decision, Б3): the casino sends `timezone`
in player-update (geo-IP on their side) -> else the product's
`quiet_hours_utc_offset`. Everything degrades to the product offset, so the
feature works from day one and sharpens as data arrives.
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

# Priorities (P1 critical .. P5 discovery). P1/P2 are never cut by caps.
DEFAULT_TOUCH_PRIORITIES: dict[str, int] = {
    "rg_warning": 1,
    "loss_rescue": 2,
    "level_up": 2,
    "event_reaction": 3,
    "idle_ping": 3,
    "daily_reminder": 3,
    "journey_step": 3,
    "reload_offer": 4,
    "offer_grant": 4,
    "highlights_drop": 5,
}

# Touch types where timing is the point — Smart Send Time never delays them.
TIMING_CRITICAL = frozenset({"rg_warning", "loss_rescue"})

# Built-in cap rows used when a product has no stored matrix. telegram/mass
# mirrors the legacy static guards; email is deliberately its own budget.
DEFAULT_CAPS: dict[tuple[str, str], dict[str, int]] = {
    ("telegram", "mass"): {"per_day": 3, "per_week": 10, "burst_per_hour": 1},
    ("telegram", "vip"): {"per_day": 5, "per_week": 15, "burst_per_hour": 2},
    ("telegram", "vip_plus"): {"per_day": 6, "per_week": 20,
                               "burst_per_hour": 2},
    ("push", "mass"): {"per_day": 2, "per_week": 8, "burst_per_hour": 1},
    ("email", "mass"): {"per_day": 1, "per_week": 4, "burst_per_hour": 1},
    ("in_app", "mass"): {"per_day": 3, "per_week": 12, "burst_per_hour": 1},
}

# In-process TTL caches (hot path; the tables are tiny).
_caps_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_prio_cache: dict[int, tuple[float, dict[str, dict[str, Any]]]] = {}
_CACHE_TTL = 60.0

def reset_caches() -> None:
    _caps_cache.clear()
    _prio_cache.clear()


def adaptive_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("adaptive_frequency_enabled")
    return (config.RETENTION_ADAPTIVE_FREQUENCY_ENABLED if v is None
            else bool(v))


def sst_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("smart_send_time_enabled")
    return (config.RETENTION_SMART_SEND_TIME_ENABLED if v is None
            else bool(v))


def player_tz_offset_hours(ru: dict[str, Any], cfg: dict[str, Any]) -> float:
    """The player's UTC offset in hours: their `tz` (IANA name or '+HH:MM')
    when the casino sent one, else the product's audience offset."""
    tz = str(ru.get("tz") or "").strip()
    if tz:
        try:
            if tz.startswith(("+", "-")):
                sign = 1 if tz[0] == "+" else -1
                parts = tz[1:].split(":")
                return sign * (int(parts[0])
                               + (int(parts[1]) / 60 if len(parts) > 1 else 0))
            offset = _dt.datetime.now(ZoneInfo(tz)).utcoffset()
            if offset is not None:
                return offset.total_seconds() / 3600.0
        except Exception:  # noqa: BLE001 - a bad tz falls back to the product
            pass
    try:
        return float(cfg.get("quiet_hours_utc_offset") or 0)
    except (TypeError, ValueError):
        return 0.0


def cohort_of(ru: dict[str, Any]) -> str:
    """The cap-matrix cohort for a player: the VIP segment when scored, else
    mass. (Dormancy cohorts may get their own rows later — additive.)"""
    seg = str(ru.get("vip_segment") or "")
    return seg if seg in ("vip", "vip_plus") else "mass"


async def _caps_for(product_id: int) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _caps_cache.get(product_id)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        rows = await db.list_frequency_caps(product_id, only_enabled=True)
    except Exception:  # noqa: BLE001
        rows = []
    _caps_cache[product_id] = (now, rows)
    return rows


async def _priorities_for(product_id: int) -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    cached = _prio_cache.get(product_id)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        rows = await db.list_touch_priorities(product_id)
    except Exception:  # noqa: BLE001
        rows = []
    by_type = {str(r["touch_type"]): r for r in rows}
    _prio_cache[product_id] = (now, by_type)
    return by_type


async def resolve_priority(product_id: int, touch_type: str
                           ) -> tuple[int, bool]:
    """(priority 1..5, channel_switch_on_cap) for a touch type."""
    stored = (await _priorities_for(product_id)).get(touch_type)
    if stored:
        return (int(stored.get("priority") or 3),
                bool(stored.get("channel_switch_on_cap")))
    return DEFAULT_TOUCH_PRIORITIES.get(touch_type, 3), False


async def resolve_caps(product_id: int, channel: str, cohort: str
                       ) -> dict[str, int]:
    """The cap row for (channel, cohort): stored exact -> stored (channel,
    mass) -> built-in exact -> built-in (channel, mass) -> telegram/mass."""
    rows = await _caps_for(product_id)
    by_key = {(str(r["channel"]), str(r["cohort"])): r for r in rows}
    for key in ((channel, cohort), (channel, "mass")):
        r = by_key.get(key)
        if r:
            return {"per_day": int(r["per_day"]),
                    "per_week": int(r["per_week"]),
                    "burst_per_hour": int(r["burst_per_hour"])}
    return (DEFAULT_CAPS.get((channel, cohort))
            or DEFAULT_CAPS.get((channel, "mass"))
            or DEFAULT_CAPS[("telegram", "mass")])


async def gate(product_id: int, ru: dict[str, Any], touch_type: str,
               cfg: dict[str, Any], *, channel: str = "telegram"
               ) -> Optional[dict[str, Any]]:
    """The priority/cohort-aware frequency verdict.

    Returns None when adaptive frequency is OFF (the caller keeps its legacy
    static guards), else {"allow", "reason", "priority", "cohort",
    "would_switch_channel"}.
    """
    if not adaptive_enabled(cfg):
        return None
    priority, switch = await resolve_priority(product_id, touch_type)
    cohort = cohort_of(ru)
    verdict = {"allow": True, "reason": None, "priority": priority,
               "cohort": cohort, "channel": channel,
               "would_switch_channel": False}
    if priority <= 2:
        return verdict  # P1/P2 are never cut by a frequency cap
    caps = await resolve_caps(product_id, channel, cohort)
    try:
        counts = await db.touch_counts(product_id, int(ru["id"]), channel)
    except Exception:  # noqa: BLE001 - fail open to the legacy guards' floor
        log.exception("frequency_counts_failed product=%s", product_id)
        return verdict
    if counts["hour"] >= caps["burst_per_hour"]:
        verdict.update(allow=False, reason="burst_cap")
        return verdict
    if counts["day"] >= caps["per_day"] or counts["week"] >= caps["per_week"]:
        verdict.update(allow=False,
                       reason="freq_cap_priority_skip",
                       would_switch_channel=(priority == 3 and switch))
        return verdict
    return verdict


# ---------------------------------------------------------------------------
# Smart Send Time
# ---------------------------------------------------------------------------
def resolve_send_hour(profile: Optional[dict[str, Any]], hint_hour: int,
                      cfg: dict[str, Any], *, mode: str = "intelligent"
                      ) -> int:
    """The local hour a non-urgent touch should land at.

    fixed -> the hint; optimal -> the profile's median hour; intelligent ->
    the median clamped to hint +- sst_max_shift_hours. A thin profile
    (sample below sst_min_sample) always answers the hint.
    """
    min_sample = int(cfg.get("sst_min_sample")
                     or config.RETENTION_SST_MIN_SAMPLE)
    max_shift = int(cfg.get("sst_max_shift_hours")
                    or config.RETENTION_SST_MAX_SHIFT_HOURS)
    if (not profile or int(profile.get("sample_size") or 0) < min_sample
            or profile.get("median_active_hour") is None):
        return hint_hour % 24
    target = int(profile["median_active_hour"]) % 24
    if mode == "fixed":
        return hint_hour % 24
    if mode == "optimal":
        return target
    lo, hi = hint_hour - max_shift, hint_hour + max_shift
    return max(lo, min(hi, _nearest_on_circle(target, hint_hour))) % 24


def _nearest_on_circle(target: int, anchor: int) -> int:
    """Map `target` onto the representation nearest `anchor` on the 24h circle
    (so clamping 23 vs anchor 1 moves to -1, not down from 23)."""
    for cand in (target, target - 24, target + 24):
        if abs(cand - anchor) <= 12:
            return cand
    return target


def clamp_outside_quiet_hours(hour: int, cfg: dict[str, Any]) -> int:
    """Shift an hour that falls inside the product's quiet window to the
    window's end (never violate quiet hours)."""
    start = int(cfg.get("quiet_hours_start") or 0)
    end = int(cfg.get("quiet_hours_end") or 0)
    if start == end:
        return hour % 24
    h = hour % 24
    in_window = (start <= h < end) if start < end else (h >= start or h < end)
    return end % 24 if in_window else h


async def next_send_time(product_id: int, ru: dict[str, Any],
                         cfg: dict[str, Any], *, touch_type: str,
                         hint_hour: Optional[int] = None,
                         earliest: Optional[_dt.datetime] = None
                         ) -> Optional[_dt.datetime]:
    """When a scheduled touch (journey step) should become claimable, in UTC.

    None = no shift (send at `earliest` / now): SST off, a timing-critical
    touch, or the chosen hour is already now. Otherwise the next UTC moment
    the player's local clock hits the resolved hour (>= earliest).
    """
    if not sst_enabled(cfg) or touch_type in TIMING_CRITICAL:
        return None
    tz_off = player_tz_offset_hours(ru, cfg)
    now = _dt.datetime.now(_dt.timezone.utc)
    base = earliest or now
    local_base = base + _dt.timedelta(hours=tz_off)
    hint = hint_hour if hint_hour is not None else local_base.hour
    profile = None
    try:
        profile = await db.get_activity_profile(product_id,
                                                str(ru.get("player_id") or ""))
    except Exception:  # noqa: BLE001 - default hint
        pass
    hour = resolve_send_hour(profile, hint, cfg)
    hour = clamp_outside_quiet_hours(hour, cfg)
    target_local = local_base.replace(minute=0, second=0, microsecond=0)
    target_local = target_local.replace(hour=hour)
    if target_local < local_base:
        target_local += _dt.timedelta(days=1)
    target_utc = target_local - _dt.timedelta(hours=tz_off)
    if target_utc <= base + _dt.timedelta(minutes=5):
        return None
    return target_utc


async def run_product_activity_profiles(product_id: int, cfg: dict[str, Any],
                                        *, force: bool = False,
                                        limit: int = 200) -> dict[str, Any]:
    """Paced recompute of the per-player activity profiles (SST input)."""
    if not sst_enabled(cfg):
        return {"skipped": "sst_disabled"}
    interval = int(cfg.get("activity_profile_sweep_interval_sec")
                   or config.RETENTION_ACTIVITY_PROFILE_SWEEP_INTERVAL_SEC)
    # Paced in Postgres (retention_worker_jobs) — see scoring.
    if not force and not await db.claim_worker_job(product_id, "profiles",
                                                   interval):
        return {"skipped": "paced"}
    updated = 0
    try:
        players = await db.activity_profile_candidates(product_id, limit=limit)
        for p in players:
            tz_off = player_tz_offset_hours(p, cfg)
            hist = await db.activity_hour_histogram(
                product_id, p["player_id"], tz_offset_hours=tz_off)
            await db.upsert_activity_profile(product_id, p["player_id"], hist)
            updated += 1
    except Exception:  # noqa: BLE001
        log.exception("activity_profile_sweep_failed product=%s", product_id)
        return {"error": "sweep_failed", "updated": updated}
    return {"updated": updated}
