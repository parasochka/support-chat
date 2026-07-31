"""RG Guard Layer — responsible-gaming rails over every proactive touch and
value grant (DOC-3, MVP scope per owner decision).

The casino platform is the source of truth for a player's RG status
(`self_exclude` / `rg_hold` / `cool_off` / `ok`), delivered via the partner
player-update webhook (or set manually from the admin while the feed is not
wired). The orchestrator never computes self-exclusion itself — it receives
the status and RESPECTS it:

  - PERMANENT block (`self_exclude`, `rg_hold`): no proactive touch, no offer,
    no game CTA — ever. Cleared only by a new status from the casino.
  - CONDITIONAL block (`cool_off` until its timestamp, a missing marketing
    consent when the consent gate is on, an enabled behavioral signal): game
    touches (offers, loss-recovery, play invitations) are blocked; a general,
    non-game touch may still pass WITH the "no play invitations" constraint.
  - Behavioral signals are config-driven per product (`rg_signal_config`) and
    ship DISABLED (MVP: no licensed-market requirements); `computed` signals
    are derived from the event feed, `casino_flag` signals are accepted
    pre-computed from the partner's own risk engine.

Every evaluation — including 'pass' — lands in the append-only
`rg_guard_audit` (5+ years retention; read access is global-admin-only at
MVP, the owner acting as compliance). The guard verdict always beats the
model; the RG cascade runs FIRST, before every other guard.
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any, Optional

from app.core import config
from app.core import db

log = logging.getLogger(__name__)

PASS = "pass"
BLOCK_CONDITIONAL = "block_conditional"
BLOCK_PERMANENT = "block_permanent"

# Trigger classes. GAME triggers are blocked by a conditional verdict too;
# a general touch (a warm event reaction) passes conditional with the
# no-play-talk constraint appended. An idle re-engagement ping is a come-back
# invitation — game marketing by nature — so it counts as a game trigger.
GAME_TRIGGERS = frozenset({"offer_grant", "loss_recovery", "play_invite",
                           "idle_ping"})

# Statuses the casino may report.
RG_STATUSES = ("ok", "cool_off", "rg_hold", "self_exclude")

# The dialogue-side constraint injected for a blocked player who writes to the
# bot himself (reactive replies keep working; only game CTAs are stripped).
DIALOGUE_SUPPRESSION_NOTE = (
    "RG PROTECTION: this player is under a responsible-gaming restriction. "
    "Never invite them to play, bet or deposit, never mention bonuses, "
    "promotions or winning, and do not attach site links to games or the "
    "cashier. Answer warmly and helpfully on everything else."
)

# In-process TTL cache for the per-product signal config (the guard runs on
# the hot path; the signals ship disabled, so most reads are empty).
_signal_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_SIGNAL_CACHE_TTL = 60.0


def rg_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg.get("rg_enabled")
    return config.RETENTION_RG_ENABLED if v is None else bool(v)


def _unknown_policy(cfg: dict[str, Any]) -> str:
    return str(cfg.get("rg_unknown_status_policy")
               or config.RETENTION_RG_UNKNOWN_STATUS_POLICY)


def _require_consent(cfg: dict[str, Any]) -> bool:
    v = cfg.get("rg_require_consent")
    return config.RETENTION_RG_REQUIRE_CONSENT if v is None else bool(v)


async def _signals_for(product_id: int) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _signal_cache.get(product_id)
    if cached and now - cached[0] < _SIGNAL_CACHE_TTL:
        return cached[1]
    try:
        rows = await db.list_rg_signal_config(product_id, only_enabled=True)
    except Exception:  # noqa: BLE001 - signals are additive; never wedge the guard
        rows = []
    _signal_cache[product_id] = (now, rows)
    return rows


def reset_signal_cache() -> None:
    _signal_cache.clear()


async def _signal_active(product_id: int, ru: dict[str, Any],
                         sig: dict[str, Any]) -> bool:
    key = str(sig.get("signal_key") or "")
    params = sig.get("params") or {}
    if sig.get("source") == "casino_flag":
        flags = ru.get("rg_signals") or {}
        return flags.get(key) is True
    player_id = str(ru.get("player_id") or "")
    if not player_id:
        return False
    try:
        if key == "chase_pattern":
            n = await db.rg_chase_count(
                product_id, player_id,
                window_days=int(params.get("window_days", 7)),
                hours_after_loss=int(params.get("hours_after_loss", 2)))
            return n >= int(params.get("min_occurrences", 2))
        if key == "deposit_frequency_spike":
            d7, d30 = await db.rg_deposit_counts(product_id, player_id)
            baseline = (d30 / 30.0) * 7.0
            if baseline <= 0:
                return False
            return d7 >= float(params.get("multiplier", 3.0)) * baseline
    except Exception:  # noqa: BLE001 - a broken signal never blocks by accident
        log.exception("rg_signal_compute_failed product=%s signal=%s",
                      product_id, key)
    return False


async def evaluate(product_id: int, ru: dict[str, Any], trigger: str,
                   cfg: dict[str, Any]) -> dict[str, Any]:
    """The RG decision cascade for one player/trigger. Order fixed:
    permanent statuses -> hard statuses -> consent -> behavioral signals.
    First hit wins."""
    status = str(ru.get("rg_status") or "unknown")
    signals: list[str] = []

    if status == "self_exclude":
        return {"decision": BLOCK_PERMANENT, "reason": "permanent_self_exclude",
                "signals": signals, "allow_general": False, "status": status}
    if status == "rg_hold":
        return {"decision": BLOCK_PERMANENT, "reason": "permanent_rg_hold",
                "signals": signals, "allow_general": False, "status": status}
    if status == "cool_off":
        until = db._as_ts(ru.get("rg_status_until"))
        if until is None or until > _dt.datetime.now(
                until.tzinfo if until else _dt.timezone.utc):
            return {"decision": BLOCK_CONDITIONAL,
                    "reason": "conditional_cool_off", "signals": signals,
                    "allow_general": True, "status": status}
        # Expired cool_off: lazily cleared to 'ok' by the caller (audited).
        status = "ok"

    if status == "unknown" and _unknown_policy(cfg) == "block":
        return {"decision": BLOCK_CONDITIONAL,
                "reason": "conditional_status_unknown", "signals": signals,
                "allow_general": True, "status": status}

    if _require_consent(cfg) and ru.get("marketing_consent") is not True:
        return {"decision": BLOCK_CONDITIONAL,
                "reason": "no_marketing_consent", "signals": signals,
                "allow_general": True, "status": status}

    for sig in await _signals_for(product_id):
        if await _signal_active(product_id, ru, sig):
            signals.append(str(sig.get("signal_key")))
            block = (BLOCK_PERMANENT if sig.get("block_class") == "permanent"
                     else BLOCK_CONDITIONAL)
            return {"decision": block,
                    "reason": f"conditional_{sig.get('signal_key')}"
                    if block == BLOCK_CONDITIONAL
                    else f"permanent_{sig.get('signal_key')}",
                    "signals": signals,
                    "allow_general": block == BLOCK_CONDITIONAL,
                    "status": status}

    reason = "pass" if status != "unknown" else "pass_status_unknown"
    return {"decision": PASS, "reason": reason, "signals": signals,
            "allow_general": True, "status": status}


async def gate(product_id: int, ru: dict[str, Any], trigger: str,
               cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The guard-side entry: evaluate + AUDIT (always), return a deny dict
    when this trigger is blocked, else None (optionally with a constraint).

    Returns None (allowed, no restriction), or
      {"deny": True, "reason": ...}                  — do not touch at all
      {"deny": False, "constraint": "..."}           — touch allowed, no game talk
    """
    if not rg_enabled(cfg):
        return None
    rg = await evaluate(product_id, ru, trigger, cfg)

    # Lazy auto-clear of an expired cool_off (evaluate already demoted it).
    if (str(ru.get("rg_status") or "") == "cool_off"
            and rg["status"] == "ok" and ru.get("id") is not None):
        try:
            await db.set_rg_status(product_id, int(ru["id"]), "ok",
                                   source="auto_clear")
            ru["rg_status"] = "ok"
            await db.log_admin_event(None, "rg_status_auto_cleared",
                                     {"player_id": ru.get("player_id")},
                                     product_id=product_id)
        except Exception:  # noqa: BLE001
            log.exception("rg_auto_clear_failed product=%s", product_id)

    # Audit EVERY evaluation, pass included (best-effort — an audit hiccup
    # must not turn into a block or a send failure).
    try:
        await db.insert_rg_audit(
            product_id, player_id=str(ru.get("player_id") or ""),
            trigger=trigger, decision=rg["decision"], reason=rg["reason"],
            signals=rg["signals"], status_snapshot=rg["status"],
            allow_general=rg["allow_general"])
    except Exception:  # noqa: BLE001
        log.exception("rg_audit_write_failed product=%s", product_id)

    if rg["decision"] == BLOCK_PERMANENT:
        return {"deny": True, "reason": f"rg_{rg['reason']}"}
    if rg["decision"] == BLOCK_CONDITIONAL:
        if trigger in GAME_TRIGGERS:
            return {"deny": True, "reason": f"rg_{rg['reason']}"}
        return {"deny": False,
                "constraint": ("RG protection - this player is under a "
                               "responsible-gaming restriction: no play "
                               "invitations, no deposit suggestions, no "
                               "bonus/promo talk, no game links.")}
    return None


def dialogue_suppression_due(ru: dict[str, Any], cfg: dict[str, Any]) -> bool:
    """Should the REACTIVE dialogue strip game CTAs for this player?

    Cheap and synchronous (no audit, no signal computation): keyed on the
    casino-reported status alone — the dialogue path must not grow a DB query
    per turn for a protection that keys on pushed state."""
    if not rg_enabled(cfg):
        return False
    v = cfg.get("rg_dialogue_suppression")
    if not (config.RETENTION_RG_DIALOGUE_SUPPRESSION if v is None else bool(v)):
        return False
    return str(ru.get("rg_status") or "") in ("self_exclude", "rg_hold",
                                              "cool_off")
