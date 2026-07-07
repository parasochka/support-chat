"""When a product runs the retention bot, the escalation contact button routes
the player INTO the bot (an escalation-entry deeplink) instead of the static
`contact_url`. The widget is the primary channel, so this rides in the same
escalation payload every hand-off path already returns.

`escalation.build_payload_for_session` is the single seam: retention ON -> a
`t.me/<bot>?start=<nonce>` deeplink + a `retention` marker; retention OFF (or no
bot, or a mint failure) -> the static contact_url, unchanged. It is per-product:
the product is resolved from the session's own `product_id`.
"""
from __future__ import annotations

import asyncio

import escalation
import retention


def _run(coro):
    return asyncio.run(coro)


def _session(**over):
    base = {"id": "sess-1", "product_id": 1, "user_context": {"full_name": "Andrey"}}
    base.update(over)
    return base


def test_retention_on_routes_button_to_the_bot(monkeypatch):
    """Retention enabled + a bot username -> the button URL is a minted escalation
    deeplink and the payload is marked `retention`."""
    product = {"id": 1, "active": True, "retention_enabled": True,
               "telegram_bot_username": "nika_bot"}

    async def _get_product(pid):
        return product
    monkeypatch.setattr("db.get_product", _get_product)

    captured = {}

    async def _create_deeplink(prod, context, escalation, lang=None):  # noqa: A002 - mirror API
        captured["product_id"] = prod["id"]
        captured["escalation"] = escalation
        captured["context"] = context
        captured["lang"] = lang
        return {"nonce": "n1", "deep_link": "https://t.me/nika_bot?start=n1"}
    monkeypatch.setattr(retention, "create_deeplink", _create_deeplink)

    payload = _run(escalation.build_payload_for_session(_session(), "ru"))

    assert payload["active"] is True
    assert payload["button"]["url"] == "https://t.me/nika_bot?start=n1"
    assert payload.get("retention") is True
    # An ESCALATION-entry deeplink (bot menu offers "go to a manager"), carrying
    # the player's session profile snapshot so Nika greets them by name.
    assert captured["escalation"] is True
    assert captured["product_id"] == 1
    assert captured["context"] == {"full_name": "Andrey"}
    # The turn's answer language rides in the nonce so the bot opens in it.
    assert captured["lang"] == "ru"


def test_retention_off_falls_back_to_static_contact_url(monkeypatch):
    """Retention disabled -> the static contact_url is used and there is no
    `retention` marker (create_deeplink is never called)."""
    product = {"id": 1, "active": True, "retention_enabled": False,
               "telegram_bot_username": "nika_bot"}

    async def _get_product(pid):
        return product
    monkeypatch.setattr("db.get_product", _get_product)

    def _boom(*a, **k):
        raise AssertionError("create_deeplink must not run when retention is off")
    monkeypatch.setattr(retention, "create_deeplink", _boom)

    static = escalation.build_payload("ru")
    payload = _run(escalation.build_payload_for_session(_session(), "ru"))

    assert payload["button"]["url"] == static["button"]["url"]
    assert "retention" not in payload


def test_inactive_product_falls_back(monkeypatch):
    """A deactivated product must not route players into its bot (mirrors the
    deeplink endpoint's gating)."""
    product = {"id": 1, "active": False, "retention_enabled": True,
               "telegram_bot_username": "nika_bot"}

    async def _get_product(pid):
        return product
    monkeypatch.setattr("db.get_product", _get_product)

    def _boom(*a, **k):
        raise AssertionError("inactive product -> no deeplink mint")
    monkeypatch.setattr(retention, "create_deeplink", _boom)

    payload = _run(escalation.build_payload_for_session(_session(), "ru"))
    assert "retention" not in payload


def test_retention_on_but_no_bot_falls_back(monkeypatch):
    """Retention enabled but no bot username configured -> static fallback."""
    product = {"id": 1, "active": True, "retention_enabled": True,
               "telegram_bot_username": None}

    async def _get_product(pid):
        return product
    monkeypatch.setattr("db.get_product", _get_product)

    def _boom(*a, **k):
        raise AssertionError("no bot -> no deeplink mint")
    monkeypatch.setattr(retention, "create_deeplink", _boom)

    payload = _run(escalation.build_payload_for_session(_session(), "ru"))
    assert "retention" not in payload


def test_mint_failure_degrades_to_static(monkeypatch):
    """A failure while minting the deeplink must never break escalation — it
    degrades to the static contact link."""
    product = {"id": 1, "active": True, "retention_enabled": True,
               "telegram_bot_username": "nika_bot"}

    async def _get_product(pid):
        return product
    monkeypatch.setattr("db.get_product", _get_product)

    async def _create_deeplink(prod, context, escalation, lang=None):  # noqa: A002
        raise RuntimeError("db down")
    monkeypatch.setattr(retention, "create_deeplink", _create_deeplink)

    static = escalation.build_payload("ru")
    payload = _run(escalation.build_payload_for_session(_session(), "ru"))
    assert payload["button"]["url"] == static["button"]["url"]
    assert "retention" not in payload


def test_no_product_scope_is_static(monkeypatch):
    """A session with no product_id (pre-tenancy / test) stays on the static
    payload and never touches the DB."""
    def _boom(*a, **k):
        raise AssertionError("must not resolve a product without a product_id")
    monkeypatch.setattr("db.get_product", _boom)

    payload = _run(escalation.build_payload_for_session(
        _session(product_id=None), "ru"))
    assert "retention" not in payload
    # final flag still respected on the fallback path
    soft = _run(escalation.build_payload_for_session(
        _session(product_id=None), "ru", final=False))
    assert soft["final"] is False
