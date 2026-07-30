"""One price rules every cost figure: the CURRENT model's, applied on read.

Cost used to be frozen at call time and summed back from `cost_usd`, so a price
correction changed nothing already recorded (OpenAI cut GPT-5.6 Luna 80% and the
admin kept reporting the old rate). Money is now derived from the stored token
counts at the live price — these pin that nothing sums the stored column and
that the price is never frozen in a module constant.
"""
from __future__ import annotations

import datetime as dt
import re

from app.ai import openai_client
from app.core import db
from tests.conftest import FakePool

_FROM = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
_TO = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)

# Every money-reporting surface, one call each: a new dashboard that sums
# `cost_usd` instead shows stale prices with no other symptom.
_MONEY_QUERIES = [
    ("support overview", lambda: db.overview_aggregates(_FROM, _TO)),
    ("cost timeseries", lambda: db.timeseries("cost", _FROM, _TO)),
    ("cost per session", lambda: db.timeseries("cost_per_session", _FROM, _TO)),
    ("ai cost histogram", lambda: db.ai_cost_timeseries(_FROM, _TO)),
    ("by topic", lambda: db.by_topic(_FROM, _TO)),
    ("by language", lambda: db.by_language(_FROM, _TO)),
    ("sessions list", lambda: db.list_sessions(_FROM, _TO)),
    ("unresolved queue", lambda: db.unresolved_by_topic(_FROM, _TO)),
    ("retention overview", lambda: db.retention_overview([1], _FROM, _TO)),
    ("retention timeseries", lambda: db.retention_timeseries([1], _FROM, _TO)),
    ("retention sessions", lambda: db.list_retention_sessions(1)),
]

_DERIVED = "tokens_in IS NULL"          # a derived figure in SQL
_STORED = re.compile(r"SUM\(\s*(?:\w+\.)?cost_usd\s*\)")   # a frozen one


def _pin_model(monkeypatch, model_id: str) -> None:
    monkeypatch.setattr(openai_client.settings, "model", lambda: {"model": model_id})


def test_the_expression_is_rebuilt_at_the_live_price(monkeypatch):
    """Never cached in a module constant — that is what makes it retroactive."""
    _pin_model(monkeypatch, "gpt-5.6-luna")
    assert openai_client.current_pricing() == (0.20, 0.02, 1.20)
    luna = db._cost_sql("l")
    assert all(f"{p:.6f}" in luna for p in (0.20, 0.02, 1.20))

    _pin_model(monkeypatch, "gpt-5.6-sol")
    assert "5.000000" in db._cost_sql("l")


def test_the_write_path_and_the_read_path_agree(monkeypatch):
    """compute_cost() and the SQL must never drift into two formulas."""
    _pin_model(monkeypatch, "gpt-5.4-mini")
    inp, cached, out = openai_client.current_pricing()

    assert openai_client.compute_cost(1_000_000, 0, 0) == inp
    assert openai_client.compute_cost(1_000_000, 0, 1_000_000) == cached
    assert openai_client.compute_cost(0, 1_000_000, 0) == out
    assert re.findall(r"\* (\d+\.\d{6})", db._cost_sql("")) == [
        f"{p:.6f}" for p in (inp, cached, out)
    ]


async def test_every_money_query_prices_from_tokens(monkeypatch):
    for label, call in _MONEY_QUERIES:
        pool = FakePool()
        monkeypatch.setattr(db, "_pool", pool)

        await call()

        assert any(_DERIVED in s for s in pool.sql), f"{label}: not derived"
        assert not any(_STORED.search(s) for s in pool.sql), (
            f"{label}: still sums the stored cost_usd")


async def test_session_detail_derives_the_per_turn_cost(monkeypatch):
    """Per-turn costs must add up to the total the sessions list shows."""
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    async def _session(sid):  # the fake pool has no rows to return one from
        return {"id": sid, "created_at": None, "updated_at": None}

    monkeypatch.setattr(db, "get_session", _session)

    await db.session_detail("00000000-0000-0000-0000-000000000001")

    priced = [s for s in pool.sql if _DERIVED in s]
    assert any("FROM chat_messages" in s for s in priced)
    assert any("FROM ai_interaction_logs" in s for s in priced)


def test_no_query_in_db_sums_the_stored_column():
    """Static backstop for a surface the sweep above doesn't call yet.

    The per-touch ledgers are the deliberate exception: they store dollars, not
    tokens, so they have nothing to re-derive from.
    """
    src = open("app/core/db.py", encoding="utf-8").read()
    frozen = ("retention_outcomes", "retention_v2_decisions",
              "conversation_reviews")
    for m in _STORED.finditer(src):
        # `o` is the retention_outcomes alias; its aggregate lives in a shared
        # fragment, so the table name is not in the same statement.
        if m.group(0) == "SUM(o.cost_usd)":
            continue
        stmt = src[m.start():m.start() + 800]   # the statement's own FROM clause
        assert any(t in stmt for t in frozen), (
            f"db.py:{src[:m.start()].count(chr(10)) + 1} sums a stored cost — "
            "derive it with _cost_sql() or it reports stale prices")
