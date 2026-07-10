"""Player data synchronization — the ONE module casino data enters through.

Rewritten from the three scattered entry points (handshake snapshot, the
player-update push webhook, the lazy Player-API pull) into a single seam, plus
the fourth input that powers the retention agent: CANONICAL EVENTS.

Inputs (all per product, all landing in the same stores):
  1. Profile push  — POST /partner/{id}/player-update (api/retention.py)
                     -> apply_profile_push()
  2. Profile pull  — the lazy Player-API refresh before a retention turn
                     -> maybe_pull_profile() (retention.py delegates here)
  3. Handshake     — the deeplink nonce snapshot (unchanged, written by
                     retention.py on /start; listed for the map)
  4. Events        — POST /partner/{id}/event (single or batch), the EPIC-1
                     canonical taxonomy -> ingest_event()/ingest_events()

Events are append-only rows in `retention_events` (idempotent by
(product_id, event_id)), and every event ALSO feeds the LEGACY BRIDGE: the
activity timestamps (`last_login_at` / `last_played_at` / `last_deposit_at`)
the state resolver keys on are bumped forward from the matching events, so a
partner that starts sending events automatically feeds the old regime too —
v1 needs to know nothing about v2. The bridge is forward-only (GREATEST), so
out-of-order delivery can never rewind a timestamp.

The v2 decision loop (retention_v2.py) consumes the same event log; which
events wake the agent is ITS call — this module only validates, stores and
bridges.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import ipaddress
import json
import logging
import socket
from typing import Any, Optional
from urllib.parse import urlsplit

import db
import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical event taxonomy (EPIC-1 §6.2). An event outside this set is
# rejected — the taxonomy is the contract, not a suggestion.
# ---------------------------------------------------------------------------
CANONICAL_EVENTS: frozenset[str] = frozenset({
    "session_started", "session_ended",
    "deposit_initiated", "deposit_confirmed", "deposit_failed",
    "withdrawal_settled",
    "bet_settled",
    "bonus_granted", "bonus_claimed", "bonus_completed", "bonus_expired",
    "kyc_started", "kyc_approved", "kyc_rejected",
    "xp_granted", "level_up", "class_up", "downgrade",
    "highlights_pack_opened", "highlights_pack_completed",
    "check_in_done", "mission_completed",
})

# SYNTHETIC events the service itself generates (never accepted from the
# partner webhook): the inactivity ladder, the one-shot follow-up after an
# unanswered touch, and the VIP-at-risk signal. They ride the same queue and
# pipeline as canonical events; the admin simulator may inject them too.
SYNTHETIC_EVENTS: frozenset[str] = frozenset({
    "inactivity_check", "touch_follow_up", "vip_at_risk",
})

# Legacy bridge: which canonical event bumps which v1 activity timestamp.
_ACTIVITY_BRIDGE: dict[str, str] = {
    "session_started": "last_login_at",
    "session_ended": "last_login_at",
    "bet_settled": "last_played_at",
    "deposit_confirmed": "last_deposit_at",
}

# Profile-ish payload fields an event may carry (rare, but a partner may ride
# a fresh balance/vip on deposit_confirmed); routed into the snapshot.
_PROFILE_PAYLOAD_FIELDS = ("full_name", "email", "activation_status",
                           "country", "balance", "vip_level",
                           "registration_date", "timezone", "rg_hold",
                           "self_excluded")

_MAX_EVENT_ID_LEN = 64
_MAX_PAYLOAD_BYTES = 8192


class EventError(ValueError):
    """A single event failed validation (message is safe to echo back)."""


def _validate_event(evt: dict[str, Any],
                    allow_synthetic: bool = False) -> dict[str, Any]:
    """Normalize + validate one incoming event dict. Raises EventError."""
    if not isinstance(evt, dict):
        raise EventError("event must be a JSON object")
    event_id = str(evt.get("event_id") or "").strip()
    if not event_id or len(event_id) > _MAX_EVENT_ID_LEN:
        raise EventError("event_id is required (non-empty, <= 64 chars)")
    event_name = str(evt.get("event_name") or "").strip()
    allowed = CANONICAL_EVENTS | (SYNTHETIC_EVENTS if allow_synthetic
                                  else frozenset())
    if event_name not in allowed:
        raise EventError(
            f"unknown event_name {event_name!r}; canonical names: "
            + ", ".join(sorted(CANONICAL_EVENTS)))
    player_id = str(evt.get("player_id") or evt.get("user_id") or "").strip()
    if not player_id:
        raise EventError("player_id is required")
    ts = evt.get("timestamp") or evt.get("ts")
    if ts is not None and db._as_ts(ts) is None:
        raise EventError("timestamp must be ISO-8601")
    if ts is None:
        ts = _dt.datetime.now(_dt.timezone.utc)
    payload = evt.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise EventError("payload must be a JSON object")
    # Optional explicit Telegram target: when one player_id is linked to
    # several Telegram accounts (typical in testing — one demo player, many
    # testers' phones), the sender otherwise picks the most recently updated
    # link. A `tg_user_id` (top-level or in payload) pins the exact recipient;
    # it rides in the payload so the append-only event row needs no new column.
    tg_target = evt.get("tg_user_id")
    if tg_target is None:
        tg_target = payload.get("tg_user_id")
    if tg_target is not None:
        try:
            # Via str() so non-integral floats and booleans are rejected too.
            tg_target = int(str(tg_target).strip())
        except (TypeError, ValueError):
            raise EventError("tg_user_id must be an integer Telegram user id")
        if tg_target <= 0:
            raise EventError("tg_user_id must be a positive integer")
        payload = dict(payload)
        payload["tg_user_id"] = tg_target
    if len(json.dumps(payload)) > _MAX_PAYLOAD_BYTES:
        raise EventError("payload too large (max 8 KiB)")
    version = str(evt.get("event_version") or "1.0").strip()[:16]
    return {"event_id": event_id, "event_name": event_name,
            "player_id": player_id, "ts": ts, "payload": payload,
            "event_version": version}


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Map partner-feed field names/shapes onto the snapshot columns.

    - `timezone` (IANA name or a UTC offset string) -> tz_name
    - `rg_hold` / `self_excluded` (booleans; truthy strings accepted) ->
      the single rg_hold flag. Either one TRUE holds; the hold clears only
      when the feed explicitly sends both false (self-exclusion alone never
      un-holds via an rg_hold=false).
    """
    out = {k: v for k, v in profile.items()
           if k not in ("timezone", "rg_hold", "self_excluded")}
    if profile.get("timezone") is not None:
        out["tz_name"] = str(profile["timezone"]).strip()[:64]
    flags = [profile.get("rg_hold"), profile.get("self_excluded")]
    supplied = [f for f in flags if f is not None]
    if supplied:
        out["rg_hold"] = any(_as_bool(f) for f in supplied)
    return out


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


async def ingest_event(product_id: int, evt: dict[str, Any],
                       source: str = "webhook") -> dict[str, Any]:
    """Validate + append one canonical event and run the legacy bridge.

    Returns {"stored": bool, "duplicate": bool, "id": int|None}. Raises
    EventError on a validation failure (the caller maps it to 422).
    """
    v = _validate_event(evt, allow_synthetic=source in ("system", "simulator"))
    pk = await db.ingest_retention_event(
        product_id, event_id=v["event_id"], event_name=v["event_name"],
        player_id=v["player_id"], ts=v["ts"], payload=v["payload"],
        event_version=v["event_version"], source=source)
    if pk is None:
        return {"stored": False, "duplicate": True, "id": None}

    # Legacy bridge (best-effort — a bridge failure must never fail the
    # ingest; the event row is already durable).
    try:
        field = _ACTIVITY_BRIDGE.get(v["event_name"])
        if field:
            await db.touch_retention_activity(product_id, v["player_id"],
                                              field, v["ts"])
        profile = normalize_profile(
            {k: v["payload"][k] for k in _PROFILE_PAYLOAD_FIELDS
             if v["payload"].get(k) is not None})
        if profile:
            await db.update_retention_profile(product_id, v["player_id"],
                                              profile, profile_source="event")
    except Exception:  # noqa: BLE001
        log.exception("player_sync_bridge_failed product=%s event=%s",
                      product_id, v["event_name"])
    if _ACTIVITY_BRIDGE.get(v["event_name"]):
        # Real casino activity ends the current idle spell: any live
        # (queued/deferred) inactivity or follow-up event is cancelled and the
        # ladder re-arms for the player's NEXT idle spell. Best-effort, on its
        # own — lifecycle bookkeeping must never mask a bridge write.
        try:
            await cancel_pending_touches(product_id, v["player_id"],
                                         trigger=v["event_name"])
        except Exception:  # noqa: BLE001
            log.exception("player_sync_cancel_failed product=%s", product_id)
    return {"stored": True, "duplicate": False, "id": pk}


async def cancel_pending_touches(product_id: int, player_id: str,
                                 trigger: str = "activity") -> int:
    """cancelled_by_return: the player is back (a casino activity event or an
    inbound Telegram message), so every live synthetic proactive event dies and
    the inactivity cycle restarts. Each cancellation lands in the decision
    ledger, so the lifecycle is auditable end to end. Returns cancellations."""
    cancelled = 0
    ladder_died = False
    ru = None
    for name in ("inactivity_check", "touch_follow_up", "vip_at_risk"):
        evt = await db.get_open_retention_event(product_id, player_id, name)
        if evt is None:
            continue
        await db.close_retention_event(evt["id"])
        if ru is None:
            ru = await db.get_retention_user_by_player(product_id, player_id)
        await db.insert_retention_v2_decision(
            product_id,
            retention_user_id=int(ru["id"]) if ru else None,
            player_id=player_id, trigger_kind="system",
            event_pk=evt["id"], event_name=name, state={}, guard={},
            action="cancelled",
            reason=f"cancelled_by_return: {trigger}", dry_run=False)
        cancelled += 1
        if name in ("inactivity_check", "vip_at_risk"):
            ladder_died = True
    # Restart the ladder for the next idle spell. Without a live event this
    # only fires when a step was consumed this cycle (cheap no-op otherwise);
    # with one, the bump is forced so the next spell's event_ids never collide
    # with the rows just closed.
    await db.bump_inactivity_cycle(product_id, player_id, force=ladder_died)
    return cancelled


async def ingest_events(product_id: int, events: list[dict[str, Any]],
                        source: str = "webhook") -> dict[str, Any]:
    """Batch ingest. Per-event outcomes; one bad event never kills the batch."""
    stored = duplicates = 0
    errors: list[dict[str, Any]] = []
    for i, evt in enumerate(events):
        try:
            res = await ingest_event(product_id, evt, source=source)
        except EventError as exc:
            errors.append({"index": i, "error": str(exc)})
            continue
        if res["duplicate"]:
            duplicates += 1
        else:
            stored += 1
    return {"stored": stored, "duplicates": duplicates, "errors": errors}


# ---------------------------------------------------------------------------
# Profile push (the player-update webhook body -> snapshot update)
# ---------------------------------------------------------------------------
async def apply_profile_push(product_id: int, player_id: str,
                             profile: dict[str, Any]) -> int:
    """Partial profile update from the partner CRM push. Returns rows touched.

    Accepts, beyond the snapshot fields and activity timestamps, the player's
    `timezone` and the responsible-gaming flags `rg_hold`/`self_excluded`
    (normalized into the single rg_hold hold — see normalize_profile).
    """
    return await db.update_retention_profile(
        product_id, player_id, normalize_profile(profile),
        profile_source="push")


# ---------------------------------------------------------------------------
# Lazy Player-API pull (moved here from retention.py; retention delegates)
# ---------------------------------------------------------------------------
_PROFILE_FIELDS = ("full_name", "email", "activation_status", "country",
                   "balance", "vip_level", "registration_date")


def _profile_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the whitelisted profile fields (_CONTEXT_FIELDS snapshot)."""
    return {f: payload.get(f) for f in _PROFILE_FIELDS
            if payload.get(f) is not None}


async def is_safe_outbound_url(url: str) -> bool:
    """SSRF guard for admin-configured outbound URLs (the Player API).

    `player_api_url` is set by a product-scoped admin — a semi-trusted role — so
    it must never be able to make the server reach internal/cloud-metadata
    addresses (169.254.169.254, RFC1918, loopback, link-local, …). We require an
    http(s) scheme and reject a host that resolves to any non-global IP. All
    resolved records are checked (a rebind that mixes public + private records is
    rejected). DNS is resolved off the event loop.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    host = parts.hostname
    # A literal IP host is checked directly; a name is resolved and every record
    # must be global (public).
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host,
            parts.port or (443 if parts.scheme == "https" else 80),
            0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_multicast:
            return False
    return True


async def maybe_pull_profile(product: dict[str, Any], ru: dict[str, Any],
                             url_guard: Any = None) -> dict[str, Any]:
    """Lazy profile refresh: if the snapshot is stale and the product exposes a
    Player API, pull the fresh profile and update the snapshot. Best-effort —
    any failure returns the existing row untouched. `url_guard` lets the caller
    inject the SSRF check (retention.py passes its module-level name so tests
    can monkeypatch it there); defaults to is_safe_outbound_url."""
    import httpx
    guard = url_guard or is_safe_outbound_url
    url = (product.get("player_api_url") or "").strip()
    player_id = ru.get("player_id")
    if not url or not player_id:
        return ru
    ttl = int(settings.retention()["profile_pull_ttl_sec"])
    if ttl <= 0:
        return ru
    last = ru.get("profile_updated_at")
    if last:
        try:
            last_dt = _dt.datetime.fromisoformat(str(last))
            now = _dt.datetime.now(last_dt.tzinfo)
            if (now - last_dt).total_seconds() < ttl:
                return ru  # fresh enough
        except (ValueError, TypeError):
            pass
    # SSRF guard: the product's OWN (decrypted) API key rides on this request
    # as a Bearer header — never connect to a non-public address.
    if not await guard(url):
        log.warning("retention_profile_pull_blocked_unsafe_url product=%s",
                    product.get("id"))
        return ru
    key = await db.get_product_player_api_key(product["id"])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params={"player_id": player_id},
                                    headers=headers)
        if resp.status_code != 200:
            log.warning("retention_profile_pull_http status=%s",
                        resp.status_code)
            return ru
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - a pull failure must not break the turn
        log.warning("retention_profile_pull_failed error=%s", exc)
        return ru
    payload = data if isinstance(data, dict) else {}
    profile = _profile_from_payload(payload)
    # The Player API may also report casino activity (the state resolver keys on
    # these), the player's timezone and the responsible-gaming hold; pass them
    # through — db/normalize validate them.
    for f in ("last_login_at", "last_played_at", "last_deposit_at",
              "timezone", "rg_hold", "self_excluded"):
        if payload.get(f) is not None:
            profile[f] = payload[f]
    profile = normalize_profile(profile)
    if not profile:
        return ru
    await db.update_retention_profile(product["id"], player_id, profile, "pull")
    refreshed = await db.get_retention_user(product["id"], ru["tg_user_id"])
    return refreshed or ru


# ---------------------------------------------------------------------------
# Player offers — the read-only platform contract (Stage 4)
# ---------------------------------------------------------------------------
# The platform owns offers end to end (catalogue, eligibility, granting); this
# module only READS the offers currently available to one player so the persona
# can mention a real offer with its real deeplink instead of speaking in
# generalities. Contract: GET {offers_api_url}?player_id=... with the product's
# player-API key as a Bearer header, returning {"offers": [{"id", "title",
# "description", "deeplink", "expires_at", "type"}, ...]} (or a bare list).
# Unconfigured / unreachable / malformed => [] — offers are an enrichment, the
# touch goes out without them.
_MAX_OFFERS = 5
_OFFER_TEXT_CAP = 200


def _normalize_offer(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or raw.get("name") or "").strip()
    deeplink = str(raw.get("deeplink") or raw.get("url") or "").strip()
    if not title:
        return None
    if deeplink and not deeplink.startswith(("http://", "https://")):
        deeplink = ""
    return {
        "id": str(raw.get("id") or raw.get("offer_id") or "")[:64],
        "title": title[:_OFFER_TEXT_CAP],
        "description": str(raw.get("description") or "").strip()[:_OFFER_TEXT_CAP],
        "deeplink": deeplink[:500],
        "expires_at": str(raw.get("expires_at") or "")[:40] or None,
        "type": str(raw.get("type") or "")[:40] or None,
    }


async def fetch_player_offers(product: dict[str, Any], player_id: str,
                              url_guard: Any = None) -> list[dict[str, Any]]:
    """The offers currently available to the player, per the platform's
    eligibility. Best-effort: any failure returns []."""
    import httpx
    url = (product.get("offers_api_url") or "").strip()
    if not url or not player_id:
        return []
    guard = url_guard or is_safe_outbound_url
    if not await guard(url):
        log.warning("player_offers_blocked_unsafe_url product=%s",
                    product.get("id"))
        return []
    key = await db.get_product_player_api_key(product["id"])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url, params={"player_id": player_id},
                                    headers=headers)
        if resp.status_code != 200:
            log.warning("player_offers_http product=%s status=%s",
                        product.get("id"), resp.status_code)
            return []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - offers are optional enrichment
        log.warning("player_offers_failed product=%s error=%s",
                    product.get("id"), exc)
        return []
    raw = data.get("offers") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:_MAX_OFFERS * 2]:
        offer = _normalize_offer(item)
        if offer:
            out.append(offer)
        if len(out) >= _MAX_OFFERS:
            break
    return out
