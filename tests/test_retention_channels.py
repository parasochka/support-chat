"""Channel abstraction (DOC-7): the deterministic router with STRICT opt-in,
executable channels, delivery lifecycle ordering, retry semantics."""
from __future__ import annotations

from app.core import db
from app.retention import channels


def _cfg(**over):
    base = {
        "multichannel_enabled": True,
        "channel_auto_priority": "push,in_app,email",
        "delivery_retry_enabled": True,
        "push_delivery_timeout_sec": 10,
    }
    base.update(over)
    return base


def _ru(**over):
    base = {"id": 10, "player_id": "p1", "subscribed": True,
            "pings_muted": False, "unreachable": False,
            "email": "p@x.io", "email_opt_in": None, "email_verified": None,
            "push_opt_in": None, "push_available": None,
            "in_app_available": None, "sms_opt_in": None}
    base.update(over)
    return base


def _stub_channels(monkeypatch, rows):
    async def _list(pid):
        return rows

    monkeypatch.setattr(db, "list_channel_config", _list)
    channels.reset_caches()


# ---------------------------------------------------------------------------
# Executability + the router
# ---------------------------------------------------------------------------
async def test_multichannel_off_only_telegram(monkeypatch):
    _stub_channels(monkeypatch, [{"channel": "email", "enabled": True}])
    execu = await channels.executable_channels(
        1, _cfg(multichannel_enabled=False))
    assert execu == {"telegram"}
    ch = await channels.route_channel(1, _ru(),
                                      _cfg(multichannel_enabled=False))
    assert ch == "telegram"


async def test_router_strict_opt_in(monkeypatch):
    _stub_channels(monkeypatch, [
        {"channel": "email", "enabled": True},
        {"channel": "push", "enabled": True}])
    cfg = _cfg()
    # No consent anywhere except telegram -> auto lands on telegram.
    ch = await channels.route_channel(1, _ru(), cfg)
    assert ch == "telegram"
    # Push consented + available -> highest priority wins.
    ru = _ru(push_opt_in=True, push_available=True)
    assert await channels.route_channel(1, ru, cfg) == "push"
    # Push consented but NOT available -> falls through to telegram.
    ru = _ru(push_opt_in=True, push_available=False)
    assert await channels.route_channel(1, ru, cfg) == "telegram"
    # Explicit email without opt-in -> None (NEVER a non-consented send),
    # even though telegram would be possible — no fallback was named.
    ru = _ru()
    assert await channels.route_channel(1, ru, cfg, wanted="email") is None
    # Explicit email with a telegram fallback -> telegram.
    assert await channels.route_channel(1, ru, cfg, wanted="email",
                                        fallback="telegram") == "telegram"
    # Nothing consented at all -> undeliverable.
    ru = _ru(subscribed=False)
    assert await channels.route_channel(1, ru, cfg) is None


async def test_router_email_needs_opt_in_and_address(monkeypatch):
    _stub_channels(monkeypatch, [{"channel": "email", "enabled": True}])
    cfg = _cfg(channel_auto_priority="email")
    ru = _ru(email_opt_in=True, subscribed=False)
    assert await channels.route_channel(1, ru, cfg) == "email"
    ru = _ru(email_opt_in=True, email="", subscribed=False)
    assert await channels.route_channel(1, ru, cfg) is None
    # An unsubscribe (opt-in flips off) immediately stops routing to email.
    ru = _ru(email_opt_in=False, subscribed=False)
    assert await channels.route_channel(1, ru, cfg) is None


def test_delivery_id_deterministic():
    a = channels.delivery_id_for(1, "j:x:1:10", "push")
    assert a == channels.delivery_id_for(1, "j:x:1:10", "push")
    assert a != channels.delivery_id_for(1, "j:x:1:10", "email")
    assert a.startswith("dl_")


# ---------------------------------------------------------------------------
# Delivery lifecycle
# ---------------------------------------------------------------------------
async def test_status_callback_never_moves_backwards(monkeypatch):
    store = {"status": "delivered"}
    updates = []

    async def _get(pid, did):
        return dict(store)

    async def _update(pid, did, **kw):
        updates.append(kw)
        if kw.get("status"):
            store["status"] = kw["status"]

    monkeypatch.setattr(db, "get_delivery", _get)
    monkeypatch.setattr(db, "update_delivery", _update)
    # Forward move applies.
    assert await channels.apply_delivery_status(1, "dl_1", "opened") is True
    assert store["status"] == "opened"
    # Late 'sent' callback is ignored (idempotent, no backwards move).
    assert await channels.apply_delivery_status(1, "dl_1", "sent") is True
    assert store["status"] == "opened"
    # Bounce is terminal + permanent.
    assert await channels.apply_delivery_status(1, "dl_1", "bounced") is True
    assert any(u.get("status") == "bounced" and u.get("permanent_fail")
               for u in updates)
    # Unknown status rejected.
    assert await channels.apply_delivery_status(1, "dl_1", "weird") is False


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------
async def test_retry_respects_permanent_and_backoff(monkeypatch):
    updates = []

    async def _due(pid, limit=20):
        return [{"delivery_id": "dl_a", "channel": "push", "attempts": 0,
                 "retention_user_id": 10, "title": "t", "body": "b",
                 "cta_url": None}]

    async def _get_ru(pid, rid):
        return _ru()

    async def _update(pid, did, **kw):
        updates.append(kw)

    async def _delegated(product, ru, **kw):
        return False, "no_mobile_token", True, None  # permanent

    monkeypatch.setattr(db, "due_delivery_retries", _due)
    monkeypatch.setattr(db, "get_retention_user_by_id", _get_ru)
    monkeypatch.setattr(db, "update_delivery", _update)
    monkeypatch.setattr(channels, "_send_delegated", _delegated)
    n = await channels.drain_delivery_retries({"id": 1}, _cfg())
    assert n == 1
    assert updates[-1]["permanent_fail"] is True
    assert updates[-1]["next_attempt_at"] is None  # never retried again


async def test_retry_disabled_or_singlechannel_noop(monkeypatch):
    async def _boom(*a, **kw):
        raise AssertionError("no retry reads when disabled")

    monkeypatch.setattr(db, "due_delivery_retries", _boom)
    assert await channels.drain_delivery_retries(
        {"id": 1}, _cfg(delivery_retry_enabled=False)) == 0
    assert await channels.drain_delivery_retries(
        {"id": 1}, _cfg(multichannel_enabled=False)) == 0
