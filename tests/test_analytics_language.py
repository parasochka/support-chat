"""Admin reports key on the CONVERSATION language, not the browser locale.

`chat_sessions.lang` is the browser locale resolved once at session create and
deliberately never overwritten; `conv_lang` is the language the player actually
drifted to. Every admin-facing report must read the latter (falling back to the
locale), otherwise a Russian-speaking player on an en-US browser is reported as
English — which is what the by-language breakdown used to do.

Also pins `prune_app_logs` counting ROWS rather than doing id arithmetic.

db.* is normally not unit-tested (no Postgres in the suite), so these use a tiny
fake pool that records the SQL — enough to pin which column the query keys on.
"""
from __future__ import annotations

import datetime as dt

from app.core import db
from app.core import settings
from app.i18n import translations


class FakePool:
    """Records every statement; returns canned rows."""

    def __init__(self, rows=None, val=0):
        self.rows = rows or []
        self.val = val
        self.sql: list[str] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.rows

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        return self.val

    async def execute(self, sql, *args):
        self.sql.append(sql)
        return None


_FROM = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
_TO = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)


async def test_by_language_groups_on_conversation_language(monkeypatch):
    pool = FakePool(rows=[{"lang": "ru", "sessions": 3, "escalated": 1,
                           "cost_usd_total": 0.5}])
    monkeypatch.setattr(db, "_pool", pool)

    out = await db.by_language(_FROM, _TO)

    sql = pool.sql[0]
    assert "COALESCE(s.conv_lang, s.lang)" in sql
    # The bare browser column must not be what the report groups on.
    assert "GROUP BY COALESCE(s.lang" not in sql
    assert out[0]["lang"] == "ru"


async def test_list_sessions_reports_and_filters_conversation_language(monkeypatch):
    now = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
    pool = FakePool(rows=[{
        "id": "s-1", "lang": "ru", "ui_lang": "en", "status": "open",
        "escalated": False, "message_count": 3, "created_at": now,
        "updated_at": now, "topic": "withdrawals", "product_id": 1,
        "product_name": "NikaBet", "cost_usd_total": 0.004,
    }], val=1)
    monkeypatch.setattr(db, "_pool", pool)

    out = await db.list_sessions(_FROM, _TO, lang="ru")

    # Both languages travel to the admin: the conversation one as `lang` (what
    # the column and the by-language chart agree on) and the browser locale
    # alongside it, so a divergence stays visible instead of being erased.
    item = out["items"][0]
    assert item["lang"] == "ru"
    assert item["ui_lang"] == "en"
    select_sql = pool.sql[-1]
    assert "COALESCE(s.conv_lang, s.lang) AS lang" in select_sql
    assert "s.lang AS ui_lang" in select_sql
    # The `lang` filter matches the conversation language too — filtering on the
    # browser locale would contradict the column it filters.
    assert "COALESCE(s.conv_lang, s.lang) = $" in select_sql


async def test_unresolved_queue_reports_conversation_language(monkeypatch):
    now = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
    pool = FakePool(rows=[{
        "topic": "withdrawals", "title": {}, "session_id": "s-1",
        "lang": "ru", "ui_lang": "en", "status": "open", "escalated": True,
        "message_count": 2, "created_at": now, "updated_at": now,
        "cost_usd_total": 0.002, "rn": 1, "full_count": 1,
        "first_message": "привет",
    }])
    monkeypatch.setattr(db, "_pool", pool)

    groups = await db.unresolved_by_topic(_FROM, _TO)

    session = groups[0]["sessions"][0]
    assert session["lang"] == "ru"
    assert session["ui_lang"] == "en"
    assert "COALESCE(s.conv_lang, s.lang) AS lang" in pool.sql[0]


async def test_prune_app_logs_counts_rows_not_id_arithmetic(monkeypatch):
    """`MAX(id) - keep` spans fewer than `keep` rows whenever the sequence has
    gaps (rolled-back insert, cached block dropped on restart), silently
    shortening the retained history."""
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    await db.prune_app_logs(5000)

    sql = pool.sql[0]
    assert "ORDER BY id DESC OFFSET $1 LIMIT 1" in sql
    assert "MAX(id)" not in sql


def test_contact_url_falls_back_to_english(monkeypatch):
    """A per-language copy key set only for some languages resolves through the
    default-language/English chain — including the ADMIN OVERRIDE for English,
    not just the shipped default. Without this an escalating es/tr/pt player
    would get a contact card with no button link."""
    monkeypatch.setitem(settings._cache, "translations", {
        "en": {"contact_url": "https://example.test/support"},
        "ru": {"contact_url": "https://example.test/support"},
    })
    for lang in ("en", "ru", "es", "tr", "pt", "de", None):
        assert translations.text("contact_url", lang) == "https://example.test/support"
