"""Widget-key product resolution on the public chat API: the embed's
widget_key names the casino; no key falls back to the default product; an
unknown/inactive key is rejected. The created session carries product_id and
the per-product handshake secret is used to verify signed context."""
from __future__ import annotations

import json
import types

import pytest

from app.chat import antispam
from app.core import auth
from app.core import config
from app.core import db
from app.ai import kb
from app.core import tenancy
from app.api import chat as chat_api

_DEFAULT = {"id": 1, "partner_id": 1, "slug": "default", "name": "Default",
            "active": True}
_CASINO = {"id": 2, "partner_id": 1, "slug": "lucky", "name": "Lucky",
           "active": True, "widget_key": "wk_lucky"}


def _req(ip="8.8.8.8"):
    return types.SimpleNamespace(
        headers={"x-forwarded-for": ip},
        client=types.SimpleNamespace(host=ip),
    )


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    antispam.reset_state()
    tenancy.set_current_product(None)
    created = {}

    async def verify_turnstile(token, ip, secret=None):
        return {"skipped": True, "reason": "test"}

    async def get_product_turnstile_secret(product_id):
        return None

    async def get_default_product():
        return dict(_DEFAULT)

    async def get_product_by_widget_key(key):
        return dict(_CASINO) if key == _CASINO["widget_key"] else None

    async def get_product_handshake_secret(product_id):
        return "lucky-hs-secret" if product_id == _CASINO["id"] else None

    async def create_session(consumer, player_id, lang, user_context,
                             session_id=None, product_id=None):
        created.update(product_id=product_id, user_context=user_context,
                       consumer=consumer)
        return session_id or "sid-1"

    async def catalogue(lang="en", product_id=None):
        created["catalogue_product_id"] = product_id
        return []

    async def log_admin_event(*a, **k):
        return None

    async def log_admin_event_sampled(*a, **k):
        return None

    monkeypatch.setattr(antispam, "verify_turnstile", verify_turnstile)
    monkeypatch.setattr(db, "get_default_product", get_default_product)
    monkeypatch.setattr(db, "get_product_by_widget_key", get_product_by_widget_key)
    monkeypatch.setattr(db, "get_product_handshake_secret", get_product_handshake_secret)
    monkeypatch.setattr(db, "get_product_turnstile_secret", get_product_turnstile_secret)
    monkeypatch.setattr(db, "create_session", create_session)
    monkeypatch.setattr(db, "log_admin_event", log_admin_event)
    monkeypatch.setattr(db, "log_admin_event_sampled", log_admin_event_sampled)
    monkeypatch.setattr(kb, "catalogue", catalogue)
    return created


def _body(**kw):
    return chat_api.SessionCreate(**kw)


async def test_no_widget_key_lands_on_default_product(_stubs):
    resp = await chat_api.create_session(_req(), _body())
    assert resp.status_code == 200
    assert _stubs["product_id"] == _DEFAULT["id"]
    assert _stubs["catalogue_product_id"] == _DEFAULT["id"]


async def test_widget_key_resolves_its_product(_stubs):
    resp = await chat_api.create_session(_req(), _body(widget_key="wk_lucky"))
    assert resp.status_code == 200
    assert _stubs["product_id"] == _CASINO["id"]


async def test_unknown_widget_key_rejected(_stubs):
    resp = await chat_api.create_session(_req(), _body(widget_key="wk_nope"))
    assert resp.status_code == 403
    assert json.loads(bytes(resp.body))["error"] == "bad_widget_key"
    assert "product_id" not in _stubs   # no session row was created


async def test_product_handshake_secret_verifies_signed_context(_stubs, monkeypatch):
    # No deploy-level secret: the PRODUCT's own secret must do the verifying.
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", None)
    blob = auth.sign_handshake({"id": "p9", "full_name": "Lucky Player"},
                               secret="lucky-hs-secret")
    resp = await chat_api.create_session(
        _req(), _body(widget_key="wk_lucky", signed_context=blob))
    assert resp.status_code == 200
    assert _stubs["user_context"]["full_name"] == "Lucky Player"


async def test_product_with_secret_ignores_unsigned_context(_stubs, monkeypatch):
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", None)
    resp = await chat_api.create_session(
        _req(), _body(widget_key="wk_lucky",
                      user_context={"full_name": "Spoofed"}))
    assert resp.status_code == 200
    assert _stubs["user_context"] == {}   # production mode: unsigned is zeroed


async def test_wrong_secret_signature_rejected(_stubs, monkeypatch):
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", None)
    blob = auth.sign_handshake({"id": "p9"}, secret="not-the-right-secret")
    resp = await chat_api.create_session(
        _req(), _body(widget_key="wk_lucky", signed_context=blob))
    assert resp.status_code == 401


async def test_deploy_handshake_secret_does_not_cross_into_another_product(
        _stubs, monkeypatch):
    """The deploy-level secret is the DEFAULT product's fallback, nobody else's.

    It used to apply to every product that had no secret of its own, so anyone
    holding it — the default brand's integrator, say — could sign a trusted
    player profile for ANOTHER partner's casino and open sessions (or mint
    retention deeplinks) as that brand's player.
    """
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", "deploy-wide-secret")
    tenancy.set_default_product_id(_DEFAULT["id"])

    async def _no_product_secret(product_id):
        return None

    monkeypatch.setattr(db, "get_product_handshake_secret", _no_product_secret)
    blob = auth.sign_handshake({"id": "p9", "full_name": "Someone Else"},
                               secret="deploy-wide-secret")

    # The non-default casino has no secret of its own -> no signed mode.
    resp = await chat_api.create_session(
        _req(), _body(widget_key="wk_lucky", signed_context=blob))
    assert resp.status_code == 401
    assert json.loads(bytes(resp.body))["error"] == "bad_handshake"

    # The default product still accepts it (single-product deployments).
    resp = await chat_api.create_session(_req(), _body(signed_context=blob))
    assert resp.status_code == 200
    assert _stubs["user_context"]["full_name"] == "Someone Else"


async def test_no_secret_product_zeroes_context_consistently(_stubs, monkeypatch):
    """A product with no signed mode must not fall back to TRUSTING the browser.

    The "is a secret in effect" branch and the signature check resolve through
    the same helper, so a non-default product with no secret rejects signatures
    AND ignores unsigned context — it never quietly trusts what the page sent.
    """
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", "deploy-wide-secret")
    tenancy.set_default_product_id(_DEFAULT["id"])

    async def _no_product_secret(product_id):
        return None

    monkeypatch.setattr(db, "get_product_handshake_secret", _no_product_secret)
    resp = await chat_api.create_session(
        _req(), _body(widget_key="wk_lucky",
                      user_context={"full_name": "Spoofed"}))
    assert resp.status_code == 200
    assert _stubs["user_context"] == {}


async def test_public_session_cannot_claim_the_telegram_facade(_stubs):
    """`consumer` decides which dashboard a session belongs to, and this
    endpoint is public: a client claiming 'telegram' would erase itself from
    every support metric and appear under Retention instead."""
    resp = await chat_api.create_session(_req(), _body(consumer="telegram"))
    assert resp.status_code == 200
    assert _stubs["consumer"] == "web"

    resp = await chat_api.create_session(_req(), _body(consumer="whatever"))
    assert resp.status_code == 200
    assert _stubs["consumer"] == "web"

    # The one accepted value still passes through.
    resp = await chat_api.create_session(_req(), _body(consumer="web"))
    assert _stubs["consumer"] == "web"
