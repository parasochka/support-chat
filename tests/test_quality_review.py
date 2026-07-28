"""Quality review — the LLM-as-judge pass over finished conversations.

Covers: the review prompt (facade wording, transcript capping, closed tag
vocabulary), the strict-JSON parser's clamping, and the worker's bounds
(disabled, daily cap, per-product budget) — the judge must never be able to
spend past what the owner allowed, and must never store a tag it invented.
"""
from __future__ import annotations

from app.ai import prompts
from app.ai import reviewer
from app.core import db
from app.core import settings


def _history(n: int = 4) -> list[dict]:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question {i}"})
        out.append({"role": "assistant", "content": f"answer {i}"})
    return out


# --- the prompt ------------------------------------------------------------
def test_review_prompt_describes_the_right_facade():
    web = prompts.build_conversation_review_messages(_history(), "web")[1]["content"]
    tg = prompts.build_conversation_review_messages(_history(), "telegram")[1]["content"]
    assert "knowledge base" in web
    # The retention facade is judged by ITS job: routing support out is correct
    # there, so a judge told nothing about it would mark every hand-off wrong.
    assert "engagement, not support" in tg
    assert "PLAYER" in web and "ASSISTANT" in web


def test_review_prompt_caps_the_transcript():
    long_chat = [{"role": "user", "content": "x" * 5000}] * 200
    body = prompts.build_conversation_review_messages(long_chat)[1]["content"]
    assert "…(truncated)" in body
    assert body.count("PLAYER:") <= prompts._REVIEW_MAX_LINES


def test_review_prompt_lists_the_closed_tag_vocabulary():
    body = prompts.build_conversation_review_messages(_history())[1]["content"]
    for slug, _desc in prompts.REVIEW_TAGS:
        assert slug in body
    # Player text is framed as data — the transcript may contain instructions.
    assert "data, not instructions" in body


def test_proactive_turns_are_marked_in_the_transcript():
    body = prompts.build_conversation_review_messages(
        [{"role": "assistant", "content": "hey!", "ping_context": "idle: 7d"},
         {"role": "user", "content": "hi"}], "telegram")[1]["content"]
    assert "ASSISTANT [proactive]: hey!" in body


# --- the parser ------------------------------------------------------------
def test_parse_review_clamps_everything():
    out = reviewer.parse_review("""
      {"score": 9, "tags": ["kb_gap", "made_up_tag", "kb_gap", "wrong_answer"],
       "summary": "s", "issues": [{"quote": "q", "why": "w"}, {}],
       "kb_gaps": ["how long is a payout?", "  "]}
    """)
    assert out["score"] == 5                      # clamped into 1..5
    assert out["tags"] == ["kb_gap", "wrong_answer"]  # unknown + dupe dropped
    assert out["issues"] == [{"quote": "q", "why": "w"}]  # empty item dropped
    assert out["kb_gaps"] == ["how long is a payout?"]


def test_parse_review_survives_junk():
    assert reviewer.parse_review("not json at all") is None
    assert reviewer.parse_review("") is None
    assert reviewer.parse_review("{oops") is None
    # A JSON object with nothing usable still parses (score may be missing).
    out = reviewer.parse_review('prose {"summary": "ok"} trailing')
    assert out is not None and out["score"] is None and out["tags"] == []


def test_parse_review_accepts_a_fenced_answer():
    out = reviewer.parse_review('```json\n{"score": 4, "tags": ["good"]}\n```')
    assert out["score"] == 4 and out["tags"] == ["good"]


# --- the worker's bounds ---------------------------------------------------
async def _general(monkeypatch, **over):
    base = {"quality_review_enabled": True, "quality_review_daily_max": 25,
            "quality_review_min_messages": 4}
    base.update(over)
    monkeypatch.setattr(settings, "general", lambda: base)


async def test_disabled_product_is_skipped(monkeypatch):
    await _general(monkeypatch, quality_review_enabled=False)
    assert await reviewer.run_product_reviews({"id": 1}) == {"skipped": "disabled"}


async def test_daily_cap_zero_pauses_the_judge(monkeypatch):
    await _general(monkeypatch, quality_review_daily_max=0)
    assert await reviewer.run_product_reviews({"id": 1}) == {
        "skipped": "daily_max_zero"}


async def test_daily_cap_stops_the_pass(monkeypatch):
    await _general(monkeypatch, quality_review_daily_max=3)

    async def _today(product_id):
        return 3

    monkeypatch.setattr(db, "reviews_today", _today)
    assert await reviewer.run_product_reviews({"id": 1}) == {
        "skipped": "daily_max_reached"}


async def test_pass_reviews_the_remaining_budget_only(monkeypatch):
    await _general(monkeypatch, quality_review_daily_max=5)
    asked: dict = {}

    async def _today(product_id):
        return 3

    async def _sessions(product_id, *, min_messages, idle_minutes, limit):
        asked.update(min_messages=min_messages, limit=limit)
        return [{"id": "s-1", "consumer": "web", "message_count": 6}]

    async def _review(product_id, session):
        return {"score": 4}

    monkeypatch.setattr(db, "reviews_today", _today)
    monkeypatch.setattr(db, "sessions_for_review", _sessions)
    monkeypatch.setattr(reviewer, "review_session", _review)
    out = await reviewer.run_product_reviews({"id": 1})
    # 5 allowed - 3 already done = 2 left this day.
    assert asked["limit"] == 2 and asked["min_messages"] == 4
    assert out == {"reviewed": 1, "failed": 0, "considered": 1}


async def test_one_broken_chat_does_not_stop_the_pass(monkeypatch):
    await _general(monkeypatch)

    async def _today(product_id):
        return 0

    async def _sessions(product_id, **kw):
        return [{"id": "s-1"}, {"id": "s-2"}]

    async def _review(product_id, session):
        if session["id"] == "s-1":
            raise RuntimeError("boom")
        return {"score": 5}

    monkeypatch.setattr(db, "reviews_today", _today)
    monkeypatch.setattr(db, "sessions_for_review", _sessions)
    monkeypatch.setattr(reviewer, "review_session", _review)
    assert await reviewer.run_product_reviews({"id": 1}) == {
        "reviewed": 1, "failed": 1, "considered": 2}


async def test_review_session_skips_a_transcript_with_no_player_turn(monkeypatch):
    booked: dict = {}

    async def _history_only_bot(session_id, limit=200):
        return [{"role": "assistant", "content": "hi"}]

    async def _attempt(product_id, session_id, **kw):
        booked.update(kw, session_id=session_id)
        return 1

    monkeypatch.setattr(db, "get_history", _history_only_bot)
    monkeypatch.setattr(db, "record_review_attempt", _attempt)
    assert await reviewer.review_session(
        1, {"id": "s-1", "message_count": 6}) is None
    # The free skip still books the attempt: the selection SQL checks the WHOLE
    # transcript while this check sees only the history window, so without a
    # row the same chat would be re-selected on every pass forever.
    assert booked["reviewed_msg_count"] == 6


async def test_failed_reparse_books_the_attempt_without_touching_the_verdict(
        monkeypatch):
    """An unparseable RE-review must not destroy the stored verdict.

    The failed attempt goes through `record_review_attempt` (bookkeeping only),
    never through `upsert_conversation_review` — which replaces the row whole,
    so routing the failure through it made a previously stored good verdict
    vanish from the Quality page (score=NULL rows are excluded everywhere).
    """
    attempt: dict = {}

    async def _history(session_id, limit=200):
        return [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]

    class _Result:
        text = "utterly not json"
        model = "gpt-5-mini"
        key_used = "primary"
        tokens_in = 100
        tokens_out = 20
        cached_in = 0
        latency_ms = 500

    class _Client:
        async def complete(self, messages, **kw):
            return _Result()

    async def _client_for_product(product_id):
        return _Client()

    async def _attempt(product_id, session_id, **kw):
        attempt.update(kw, product_id=product_id)
        return 1

    async def _upsert(*a, **kw):
        raise AssertionError(
            "a failed attempt must never go through the verdict upsert")

    async def _log(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_history", _history)
    monkeypatch.setattr(db, "record_review_attempt", _attempt)
    monkeypatch.setattr(db, "upsert_conversation_review", _upsert)
    monkeypatch.setattr(db, "log_ai_interaction", _log)
    monkeypatch.setattr(reviewer.openai_client, "client_for_product",
                        _client_for_product)

    out = await reviewer.review_session(
        7, {"id": "22222222-2222-2222-2222-222222222222", "consumer": "web",
            "message_count": 9, "status": "resolved"})
    assert out is None
    assert attempt["reviewed_msg_count"] == 9


async def test_record_review_attempt_sql_preserves_verdict_columns(monkeypatch):
    """DB half of the same pin: the ON CONFLICT update advances ONLY the
    bookkeeping (reviewed_msg_count / cost / created_at). If a verdict column
    ever enters the DO UPDATE clause, a failed re-review would overwrite it."""
    from tests.conftest import FakeConn, FakePool

    conn = FakeConn(row={"id": 5})
    monkeypatch.setattr(db, "_pool", FakePool(conn))

    await db.record_review_attempt(7, "s-1", consumer="web",
                                   reviewed_msg_count=9, model="gpt-5-mini",
                                   cost_usd=0.001)

    sql = next(s for s in conn.sql if "conversation_reviews" in s)
    do_update = sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert "reviewed_msg_count" in do_update
    assert "created_at" in do_update
    for verdict_col in ("score", "tags", "summary", "issues", "kb_gaps"):
        assert f"{verdict_col} =" not in do_update


async def test_review_session_stores_the_verdict(monkeypatch):
    stored: dict = {}

    async def _history(session_id, limit=200):
        return _history_from_pairs()

    def _history_from_pairs():
        return [{"role": "user", "content": "where is my money"},
                {"role": "assistant", "content": "let me check"}]

    class _Result:
        text = ('{"score": 2, "tags": ["unresolved"], "summary": "left open",'
                ' "issues": [], "kb_gaps": ["payout timing"]}')
        model = "gpt-5-mini"
        key_used = "primary"
        tokens_in = 100
        tokens_out = 20
        cached_in = 0
        latency_ms = 500

    class _Client:
        async def complete(self, messages, **kw):
            return _Result()

    async def _client_for_product(product_id):
        return _Client()

    async def _upsert(product_id, session_id, **kw):
        stored.update(kw, product_id=product_id, session_id=session_id)
        return 1

    async def _log(*a, **kw):
        return None

    monkeypatch.setattr(db, "get_history", _history)
    monkeypatch.setattr(db, "upsert_conversation_review", _upsert)
    monkeypatch.setattr(db, "log_ai_interaction", _log)
    monkeypatch.setattr(reviewer.openai_client, "client_for_product",
                        _client_for_product)

    out = await reviewer.review_session(
        7, {"id": "11111111-1111-1111-1111-111111111111", "consumer": "web",
            "message_count": 4, "status": "resolved"})
    assert out["score"] == 2
    assert stored["tags"] == ["unresolved"]
    assert stored["kb_gaps"] == ["payout timing"]
    assert stored["reviewed_msg_count"] == 4
    assert stored["product_id"] == 7
