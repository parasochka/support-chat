"""GET /api/chat/topics: the session-free catalogue that lets the widget paint
the category buttons instantly (no reCaptcha, no token, no DB write) while the
session create runs in the background.
"""
from __future__ import annotations

import json

import config
import db
from api import chat as chat_api


_TOPICS = [
    {"id": 1, "slug": "deposits", "title": {"en": "Deposits", "ru": "Депозиты"}},
    {"id": 2, "slug": "withdrawals", "title": {"en": "Withdrawals", "ru": "Выводы"}},
]


def _payload(resp):
    return json.loads(bytes(resp.body))


async def test_catalogue_localizes_and_excludes_other(monkeypatch):
    async def fake_list_topics(include_hidden=False):
        # 'other' is hidden by db.list_topics; mirror that here.
        assert include_hidden is False
        return _TOPICS

    monkeypatch.setattr(db, "list_topics", fake_list_topics)

    resp = await chat_api.list_catalogue(lang="ru")
    data = _payload(resp)
    assert data["lang"] == "ru"
    assert [t["slug"] for t in data["topics"]] == ["deposits", "withdrawals"]
    assert data["topics"][0]["title"] == "Депозиты"
    assert data["languages"] == config.SUPPORTED_LANGUAGES


async def test_catalogue_resolves_locale_then_default(monkeypatch):
    async def fake_list_topics(include_hidden=False):
        return _TOPICS

    monkeypatch.setattr(db, "list_topics", fake_list_topics)

    # Browser locale maps to a base code.
    by_locale = _payload(await chat_api.list_catalogue(locale="es-MX"))
    assert by_locale["lang"] == "es"

    # Nothing supplied -> service default, never AUTO leaking to the client.
    by_default = _payload(await chat_api.list_catalogue())
    assert by_default["lang"] == config.DEFAULT_LANGUAGE


async def test_catalogue_is_browser_cacheable(monkeypatch):
    async def fake_list_topics(include_hidden=False):
        return _TOPICS

    monkeypatch.setattr(db, "list_topics", fake_list_topics)

    resp = await chat_api.list_catalogue()
    assert "max-age" in resp.headers.get("cache-control", "")
