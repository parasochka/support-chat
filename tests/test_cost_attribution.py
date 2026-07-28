"""AI spend is attributed per facade AND per source — not guessed from the row.

Every OpenAI call lands in `ai_interaction_logs` (invariant §4), but half of
them have no session to join: the quality judge, the proactive agent's decision
call and the media cataloguer all log with `session_id NULL`. The dashboards
used to read that NULL as "photo metadata", so the judge's and the agent's spend
was charged to the media bucket — and a review of a SUPPORT conversation was
charged to the Telegram dashboard entirely.

These pin the fix: writers stamp `consumer` (which facade the money belongs to)
and `source` (what the call was), and the money-reporting queries read those
labels instead of `session_id IS NULL`.

db.* is normally not unit-tested (no Postgres in the suite), so the query tests
use a tiny fake pool that records the SQL — enough to pin what it keys on.
"""
from __future__ import annotations

import datetime as dt

from app.ai import reviewer
from app.core import db
from app.core import metrics

_FROM = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
_TO = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)


class _Row(dict):
    """A row that answers any column with 0 — the queries under test are read
    for their SQL, not their values."""

    def __missing__(self, key):
        return 0


class FakePool:
    """Records every statement (and the args of the last execute)."""

    def __init__(self):
        self.sql: list[str] = []
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        return []

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        return _Row()

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        return 0

    async def execute(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        return None


def _sql_with(pool: FakePool, needle: str) -> str:
    for sql in pool.sql:
        if needle in sql:
            return sql
    raise AssertionError(f"no statement mentioning {needle!r}")


# --- the writer ------------------------------------------------------------
async def test_log_ai_interaction_stores_the_attribution_labels(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    await db.log_ai_interaction(
        None, "gpt-5-mini", "primary", 10, 5, 0, 0.001, 400, True, None,
        product_id=3, consumer="telegram", source="review")

    sql = _sql_with(pool, "INSERT INTO ai_interaction_logs")
    assert "consumer" in sql and "source" in sql
    assert "telegram" in pool.args[-1] and "review" in pool.args[-1]


# --- the judge inherits the facade it judged -------------------------------
async def _run_review(monkeypatch, consumer: str) -> dict:
    logged: dict = {}

    class _Result:
        text = '{"score": 4, "tags": [], "summary": "ok", "issues": []}'
        model, key_used = "gpt-5-mini", "primary"
        tokens_in, tokens_out, cached_in, latency_ms = 100, 20, 0, 500

    class _Client:
        async def complete(self, messages, **kw):
            return _Result()

    async def _history(*a, **kw):
        return [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]

    async def _log(*a, **kw):
        logged.update(kw)

    monkeypatch.setattr(db, "get_history", _history)
    monkeypatch.setattr(db, "upsert_conversation_review",
                        lambda *a, **kw: _noop())
    monkeypatch.setattr(db, "log_ai_interaction", _log)
    monkeypatch.setattr(reviewer.openai_client, "client_for_product",
                        lambda pid: _client(_Client()))
    await reviewer.review_session(
        7, {"id": "11111111-1111-1111-1111-111111111111", "consumer": consumer,
            "message_count": 4, "status": "resolved"})
    return logged


async def _noop():
    return None


async def _client(c):
    return c


async def test_review_of_a_support_chat_is_support_spend(monkeypatch):
    logged = await _run_review(monkeypatch, "web")
    assert logged["consumer"] == "web"      # NOT the retention dashboard
    assert logged["source"] == "review"     # NOT the media bucket


async def test_review_of_a_telegram_chat_is_retention_spend(monkeypatch):
    logged = await _run_review(monkeypatch, "telegram")
    assert logged["consumer"] == "telegram"
    assert logged["source"] == "review"


# --- the money queries read the labels -------------------------------------
async def test_support_overview_splits_dialogue_from_the_judge(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    await db.overview_aggregates(_FROM, _TO)

    sql = _sql_with(pool, "cost_usd_total")
    # The dialogue cost (which every per-session metric divides) counts only
    # 'chat' rows, and the judge's passes come back as their own figure.
    assert "= 'chat'" in sql and "cost_review_usd" in sql
    # Support spend is now selected by the facade label, not by "not telegram".
    assert "s.consumer <> 'telegram'" not in sql


async def test_retention_overview_splits_every_driver(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    out = await db.retention_overview([1], _FROM, _TO)

    sql = _sql_with(pool, "AS dialog")
    for source in ("'chat'", "'agent'", "'media'", "'review'"):
        assert source in sql
    # The old guess — session-less ⇒ photo metadata — must be gone.
    assert "(WHERE l.session_id IS NULL), 0) AS photo" not in sql
    assert {"cost_agent_usd", "cost_photo_usd", "cost_review_usd"} <= set(
        out["range"])


async def test_retention_timeseries_splits_every_driver(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    await db.retention_timeseries([1], _FROM, _TO)

    sql = _sql_with(pool, "AS dialog")
    for source in ("'chat'", "'agent'", "'media'", "'review'"):
        assert source in sql


# --- the derived payload ---------------------------------------------------
def test_overview_reports_review_cost_apart_from_the_dialogue_cost():
    o = metrics.overview({
        "sessions_total": 10, "sessions_engaged": 8, "sessions_open": 1,
        "sessions_escalated": 2, "avg_messages_per_session": 3.0,
        "sessions_with_ai": 8, "cost_usd_total": 4.0,
        "cached_in_total": 0, "tokens_in_total": 1, "events": {},
        "cost_review_usd": 1.5, "review_calls": 6,
    })
    assert o["cost_usd_total"] == 4.0
    assert o["cost_review_usd"] == 1.5
    assert o["review_calls"] == 6
    # The judge's spend must NOT move the per-session price: it belongs to no
    # session, so folding it in would make every chat look more expensive.
    assert o["cost_usd_per_session"] == round(4.0 / 8, 6)
