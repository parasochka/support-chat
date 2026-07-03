"""GET /api/chat/topics: the session-free catalogue that lets the widget paint
the category buttons instantly (no reCaptcha, no token, no DB write) while the
session create runs in the background. Multi-tenancy: the endpoint resolves the
product from the widget key (default product when absent) and scopes the list.
"""
from __future__ import annotations

import json

import config
import db
from api import chat as chat_api

_PRODUCT = {"id": 1, "slug": "default", "name": "Default product", "active": True}

_TOPICS = [
    {"id": 1, "slug": "deposits", "title": {"en": "Deposits", "ru": "Депозиты"}},
    {"id": 2, "slug": "withdrawals", "title": {"en": "Withdrawals", "ru": "Выводы"}},
    # 'other' is a normal, never-hidden topic; db.list_topics sorts it last.
    {"id": 3, "slug": "other", "title": {"en": "Other", "ru": "Другое"}},
]


def _payload(resp):
    return json.loads(bytes(resp.body))


def _stub_tenancy(monkeypatch, expected_product_id=1):
    async def fake_default_product():
        return dict(_PRODUCT)

    async def fake_by_key(key):
        return dict(_PRODUCT) if key == "wk_test" else None

    async def fake_list_topics(product_id):
        assert product_id == expected_product_id
        return _TOPICS

    monkeypatch.setattr(db, "get_default_product", fake_default_product)
    monkeypatch.setattr(db, "get_product_by_widget_key", fake_by_key)
    monkeypatch.setattr(db, "list_topics", fake_list_topics)


async def test_catalogue_localizes_and_includes_other(monkeypatch):
    _stub_tenancy(monkeypatch)

    resp = await chat_api.list_catalogue(lang="ru")
    data = _payload(resp)
    assert data["lang"] == "ru"
    # The full catalogue is served — 'other' included, last (never hidden).
    assert [t["slug"] for t in data["topics"]] == ["deposits", "withdrawals", "other"]
    assert data["topics"][0]["title"] == "Депозиты"
    assert data["topics"][-1]["title"] == "Другое"
    assert data["languages"] == config.SUPPORTED_LANGUAGES


async def test_catalogue_resolves_locale_then_default(monkeypatch):
    _stub_tenancy(monkeypatch)

    # Browser locale maps to a base code.
    by_locale = _payload(await chat_api.list_catalogue(locale="es-MX"))
    assert by_locale["lang"] == "es"

    # Nothing supplied -> service default, never AUTO leaking to the client.
    by_default = _payload(await chat_api.list_catalogue())
    assert by_default["lang"] == config.DEFAULT_LANGUAGE


async def test_catalogue_is_browser_cacheable(monkeypatch):
    _stub_tenancy(monkeypatch)

    resp = await chat_api.list_catalogue()
    assert "max-age" in resp.headers.get("cache-control", "")


async def test_catalogue_accepts_widget_key_and_rejects_unknown(monkeypatch):
    _stub_tenancy(monkeypatch)

    ok = await chat_api.list_catalogue(widget_key="wk_test")
    assert _payload(ok)["topics"]

    bad = await chat_api.list_catalogue(widget_key="wk_nope")
    assert bad.status_code == 403
    assert _payload(bad)["error"] == "bad_widget_key"
