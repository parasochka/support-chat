"""The atomic turn write (invariant §3) and its spend attribution (§4).

`db.persist_turn` is the one place a visible chat turn becomes durable: two
`chat_messages` rows, the `chat_sessions.message_count` bump and the
`ai_interaction_logs` row must land in ONE transaction, or a crash between them
leaves a half-turn — an answer with no question, a counter that disagrees with
the transcript, or a billed OpenAI call with nothing to attribute it to.

Every existing test of the chat flow monkeypatches this helper away, so nothing
executed it: the invariant the whole design rests on had no test at all. These
run the real function against the shared fake connection from conftest.
"""
from __future__ import annotations

from app.core import db
from tests.conftest import FakeConn, FakePool, _Tx

_SID = "00000000-0000-0000-0000-000000000000"
_AI_META = {
    "model": "gpt-5-mini", "key_used": "primary", "tokens_in": 900,
    "tokens_out": 120, "cached_in": 800, "cost_usd": 0.0012,
    "latency_ms": 1500, "ok": True, "error": None,
}


def _conn():
    # The final UPDATE ... RETURNING message_count answers the row.
    return FakeConn(row={"message_count": 7})


def _assert_all_writes_inside_tx(conn, *needles):
    """Every statement matching a needle sits between BEGIN and COMMIT.

    `conn.transactions == 1` alone cannot see a write that moved OUTSIDE the
    `async with conn.transaction()` block — the count would still be 1 and only
    the ordering betrays the split turn.
    """
    sql = conn.sql
    begin, commit = sql.index(_Tx.BEGIN), sql.index(_Tx.COMMIT)
    for needle in needles:
        for i, s in enumerate(sql):
            if needle in s:
                assert begin < i < commit, (
                    f"statement outside the transaction: {s[:60]!r}")


async def test_turn_is_written_in_one_transaction(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    count = await db.persist_turn(
        _SID, "как вывести деньги?", "ru", "Вот как вывести:", "ru",
        ai_meta=_AI_META, product_id=3)

    assert count == 7
    # Exactly one transaction wraps the whole turn.
    assert conn.transactions == 1
    joined = " ".join(conn.sql)
    assert "INSERT INTO chat_messages" in joined
    assert "INSERT INTO ai_interaction_logs" in joined
    assert "UPDATE chat_sessions" in joined
    # Both message rows + the log + the counter bump, nothing split out.
    assert sum("INSERT INTO chat_messages" in s for s in conn.sql) == 2
    assert sum("INSERT INTO ai_interaction_logs" in s for s in conn.sql) == 1
    assert sum("UPDATE chat_sessions" in s for s in conn.sql) == 1
    # ... and every one of them INSIDE the transaction, not around it.
    _assert_all_writes_inside_tx(conn, "INSERT INTO chat_messages",
                                 "INSERT INTO ai_interaction_logs",
                                 "UPDATE chat_sessions")


async def test_turn_carries_product_and_attribution_labels(monkeypatch):
    """§4: the AI log row must carry product_id + the facade/source labels.

    Readers go through db._LOG_SOURCE / _LOG_IS_SUPPORT / _LOG_IS_RETENTION,
    which key on these columns — the spender is never re-derived from
    `session_id IS NULL`. An unlabelled row cannot be told apart at read time.
    """
    conn = _conn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.persist_turn(_SID, "hi", "en", "hello", "en",
                          ai_meta=_AI_META, product_id=3, consumer="telegram")

    sql, args = next((s, a) for s, a in conn.calls
                     if "INSERT INTO ai_interaction_logs" in s)
    # source is literal in the statement (this helper only writes dialogue).
    assert "'chat'" in sql
    assert args[0] == _SID          # session_id
    assert args[1] == 3             # product_id, denormalized for the dashboards
    assert args[2] == "telegram"    # consumer = the facade the money belongs to


async def test_model_free_turn_persists_without_an_ai_log(monkeypatch):
    """A turn with no OpenAI call behind it (the message-cap hand-off, the
    low-content nudge) still persists the visible turn and the counter — but
    writes NO ai_interaction_logs row, since §4 scopes that log to actual
    calls."""
    conn = _conn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    count = await db.persist_turn(_SID, "?", "en", "Could you say a bit more?",
                                  "en", ai_meta=None, product_id=3)

    assert count == 7
    assert conn.transactions == 1
    assert sum("INSERT INTO chat_messages" in s for s in conn.sql) == 2
    assert not any("ai_interaction_logs" in s for s in conn.sql)
    assert sum("UPDATE chat_sessions" in s for s in conn.sql) == 1
    _assert_all_writes_inside_tx(conn, "INSERT INTO chat_messages",
                                 "UPDATE chat_sessions")


async def test_ping_turn_is_atomic_and_labelled_agent(monkeypatch):
    """`persist_ping_turn` — the proactive writer of invariant §4's other half.

    Same atomic contract as persist_turn minus the user row, and its AI log
    row must be labelled consumer='telegram' / source='agent' LITERALLY in the
    statement: a ping is written by the agent, never by a player's turn, so
    the retention dashboards key on exactly these labels.
    """
    conn = _conn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    count = await db.persist_ping_turn(
        _SID, "Заходи — у нас новый турнир!",
        ai_meta={"model": "gpt-5-mini", "key_used": "primary", "lang": "ru",
                 "tokens_in": 300, "tokens_out": 60, "cached_in": 0,
                 "cost_usd": 0.0004, "latency_ms": 900, "ok": True},
        product_id=3, ping_context="deposit_confirmed")

    assert count == 7
    assert conn.transactions == 1
    # One assistant row (no user row), the agent-labelled log, the bump.
    assert sum("INSERT INTO chat_messages" in s for s in conn.sql) == 1
    assert sum("INSERT INTO ai_interaction_logs" in s for s in conn.sql) == 1
    assert sum("UPDATE chat_sessions" in s for s in conn.sql) == 1
    _assert_all_writes_inside_tx(conn, "INSERT INTO chat_messages",
                                 "INSERT INTO ai_interaction_logs",
                                 "UPDATE chat_sessions")
    sql, args = next((s, a) for s, a in conn.calls
                     if "INSERT INTO ai_interaction_logs" in s)
    assert "'telegram'" in sql and "'agent'" in sql
    assert args[0] == _SID
    assert args[1] == 3  # product_id, denormalized for the dashboards


async def test_model_free_ping_persists_without_an_ai_log(monkeypatch):
    """A canned ping (no model call — ai_meta has no model) still persists the
    visible turn + counter atomically but writes NO ai_interaction_logs row."""
    conn = _conn()
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    count = await db.persist_ping_turn(_SID, "Мы скучаем!", ai_meta=None,
                                       product_id=3)

    assert count == 7
    assert conn.transactions == 1
    assert sum("INSERT INTO chat_messages" in s for s in conn.sql) == 1
    assert not any("ai_interaction_logs" in s for s in conn.sql)
    _assert_all_writes_inside_tx(conn, "INSERT INTO chat_messages",
                                 "UPDATE chat_sessions")
