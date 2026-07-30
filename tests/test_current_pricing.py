"""One price rules every cost figure: the CURRENT model's, applied on read.

Cost used to be frozen at call time — each row priced by the model id it named,
with the dollars stored in `cost_usd` and every dashboard summing that column.
That made a dashboard a mix of prices: OpenAI cut GPT-5.6 Luna 80% three weeks
after the family shipped, and the admin kept reporting the old rate forever,
because the number had already been written down.

Now the token counts are the durable record and the money is DERIVED at read
time (`db._cost_sql`) from the price of the model configured right now. These
pin that: the price comes from the live setting (never a module constant frozen
at import), and no money query reads the stored column back.
"""
from __future__ import annotations

import datetime as dt
import re

from app.ai import openai_client
from app.core import db
from tests.conftest import FakePool

_FROM = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
_TO = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)

# The money-reporting surfaces, one call each. Every one of them must price from
# tokens; a new dashboard that sums `cost_usd` instead shows stale prices with
# no other symptom, which is exactly the failure this list exists to catch.
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

# What a derived figure looks like in SQL, and what a frozen one looks like.
_DERIVED = "tokens_in IS NULL"
_STORED = re.compile(r"SUM\(\s*(?:\w+\.)?cost_usd\s*\)")


def _pin_model(monkeypatch, model_id: str) -> None:
    monkeypatch.setattr(openai_client.settings, "model", lambda: {"model": model_id})


# --- the price source ------------------------------------------------------
def test_the_expression_carries_the_current_model_price(monkeypatch):
    _pin_model(monkeypatch, "gpt-5.6-luna")
    inp, cached, out = openai_client.current_pricing()
    assert (inp, cached, out) == (0.20, 0.02, 1.20)

    sql = db._cost_sql("l")
    for price in (inp, cached, out):
        assert f"{price:.6f}" in sql


def test_the_expression_follows_a_model_switch(monkeypatch):
    """Re-read per query — never cached in a module constant at import time.

    The whole point of deriving is that changing the model (a hot setting) or
    correcting a price re-prices the history on the next page load.
    """
    _pin_model(monkeypatch, "gpt-5.6-luna")
    luna = db._cost_sql("l")
    _pin_model(monkeypatch, "gpt-5.6-sol")
    sol = db._cost_sql("l")

    assert luna != sol
    assert "5.000000" in sol and "30.000000" in sol


def test_compute_cost_and_the_sql_agree_on_the_price(monkeypatch):
    """The write-time helper and the read-time SQL must never drift apart."""
    _pin_model(monkeypatch, "gpt-5.4-mini")
    inp, cached, out = openai_client.current_pricing()

    assert openai_client.compute_cost(1_000_000, 0, 0) == inp
    assert openai_client.compute_cost(1_000_000, 0, 1_000_000) == cached
    assert openai_client.compute_cost(0, 1_000_000, 0) == out

    sql = db._cost_sql("")
    assert [f"{p:.6f}" for p in (inp, cached, out)] == re.findall(
        r"\* (\d+\.\d{6})", sql
    )


def test_a_row_with_no_tokens_prices_as_null():
    """A user turn / model-free reply has no call to price — NULL, not $0.

    NULL keeps it out of SUM() and lets a per-turn view render "no AI call"
    instead of a misleading $0.000000 on every player message.
    """
    sql = db._cost_sql("")
    assert sql.startswith(
        "(CASE WHEN tokens_in IS NULL AND tokens_out IS NULL THEN NULL"
    )


# --- every money query derives ---------------------------------------------
async def test_every_money_query_prices_from_tokens(monkeypatch):
    for label, call in _MONEY_QUERIES:
        pool = FakePool()
        monkeypatch.setattr(db, "_pool", pool)

        await call()

        derived = [s for s in pool.sql if _DERIVED in s]
        assert derived, f"{label}: no query derives cost from token counts"
        stored = [s for s in pool.sql if _STORED.search(s)]
        assert not stored, f"{label}: still sums the stored cost_usd column"


async def test_session_detail_derives_the_per_turn_cost(monkeypatch):
    """Per-turn costs must add up to the total the sessions list shows."""
    pool = FakePool()
    monkeypatch.setattr(db, "_pool", pool)

    async def _session(sid):  # the fake pool has no rows to return one from
        return {"id": sid, "created_at": None, "updated_at": None}

    monkeypatch.setattr(db, "get_session", _session)

    await db.session_detail("00000000-0000-0000-0000-000000000001")

    priced = [s for s in pool.sql if _DERIVED in s]
    # Both halves of the transcript: the chat turns and the raw call log.
    assert any("FROM chat_messages" in s for s in priced)
    assert any("FROM ai_interaction_logs" in s for s in priced)


def test_no_query_in_db_sums_the_stored_column():
    """A static backstop for a surface these tests don't call yet.

    The per-touch ledgers are the deliberate exception: retention_outcomes,
    retention_v2_decisions and conversation_reviews store dollars, not tokens
    (a touch's cost is a dimension of the touch, frozen with the rest of them),
    so they have nothing to re-derive from.
    """
    src = open("app/core/db.py", encoding="utf-8").read()
    frozen = ("retention_outcomes", "retention_v2_decisions",
              "conversation_reviews")
    for m in _STORED.finditer(src):
        line = src[: m.start()].count("\n") + 1
        # `o` is the retention_outcomes alias; its aggregate lives in a shared
        # fragment constant, so the table name is not in the same statement.
        if m.group(0) == "SUM(o.cost_usd)":
            continue
        # Otherwise the statement's own FROM clause follows the SUM.
        stmt = src[m.start():m.start() + 800]
        assert any(t in stmt for t in frozen), (
            f"db.py:{line} sums the stored cost_usd of a token-bearing table — "
            "derive it with _cost_sql() instead, or it reports stale prices"
        )
