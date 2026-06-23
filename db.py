"""Direct asyncpg data layer — no ORM, no migration files.

The schema *is* the code in `init_db()`. To change schema, edit `init_db()`.
Adding a column to an existing table needs an explicit guard because
`CREATE TABLE IF NOT EXISTS` will not ALTER an existing table — see
`_ensure_columns()`.

Every read/write goes through a `db.<name>(...)` helper; nothing else touches
tables directly.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import asyncpg

import config

_pool: Optional[asyncpg.Pool] = None


async def connect() -> asyncpg.Pool:
    """Create the global connection pool (idempotent)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call db.connect()/db.init_db() first")
    return _pool


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
-- KB ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_topics (
  id            SERIAL PRIMARY KEY,
  slug          TEXT UNIQUE NOT NULL,
  title         JSONB NOT NULL,
  display_order INT NOT NULL DEFAULT 0,
  active        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS kb_entries (
  id         SERIAL PRIMARY KEY,
  topic_id   INT NOT NULL REFERENCES kb_topics(id),
  content    TEXT NOT NULL,
  active     BOOLEAN NOT NULL DEFAULT TRUE
);

-- SESSIONS & MESSAGES ----------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            UUID PRIMARY KEY,
  consumer      TEXT NOT NULL DEFAULT 'web',
  player_id     TEXT,
  lang          TEXT,
  topic_id      INT REFERENCES kb_topics(id),
  user_context  JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL DEFAULT 'open',
  escalated     BOOLEAN NOT NULL DEFAULT FALSE,
  message_count INT NOT NULL DEFAULT 0,
  -- Prompt-history boundary: only chat_messages with id > this value are fed
  -- into the model prompt. Bumped on every topic switch so the previous
  -- topic's transcript can't keep re-triggering a [[TOPIC:...]] suggestion
  -- back to it (the topic-switch loop). The full transcript is still stored.
  context_reset_id BIGINT NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID NOT NULL REFERENCES chat_sessions(id),
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  lang        TEXT,
  model       TEXT,
  key_used    TEXT,
  tokens_in   INT,
  tokens_out  INT,
  cached_in   INT,
  cost_usd    NUMERIC(12,6),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OPS / AUDIT ------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_interaction_logs (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  model       TEXT,
  key_used    TEXT,
  tokens_in   INT,
  tokens_out  INT,
  cached_in   INT,
  cost_usd    NUMERIC(12,6),
  latency_ms  INT,
  ok          BOOLEAN,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_events (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  type        TEXT NOT NULL,
  payload     JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rate_limit_hits (
  ip          TEXT NOT NULL,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Runtime-tunable settings (hot-reloaded; precedence app_settings > env > default).
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_admin_events_session ON admin_events(session_id);
CREATE INDEX IF NOT EXISTS idx_admin_events_type ON admin_events(type, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_created ON chat_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_logs_created ON ai_interaction_logs(created_at);
"""


async def _ensure_columns(conn: asyncpg.Connection) -> None:
    """Explicit ALTER guards for columns added after a table first shipped.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so any new
    column on an already-deployed table must be added here. Each statement is
    idempotent via `ADD COLUMN IF NOT EXISTS`. (None needed yet — Phase 1
    baseline — but the seam is here so the rule from the brief is honoured.)
    """
    alters: list[str] = [
        # Prompt-history boundary bumped on each topic switch (loop fix); only
        # messages newer than this id are sent to the model.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "context_reset_id BIGINT NOT NULL DEFAULT 0",
        # Sticky CONVERSATION language: the language the answers have drifted to
        # because the player started writing in it (separate from `lang`, which
        # stays the browser/UI language so the widget chrome is untouched). NULL
        # until the player writes in a different supported language; then every
        # later turn answers in it until they switch again.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "conv_lang TEXT",
        # Removed feature: system-prompt versioning + A/B. The prompt is now
        # sourced solely from prompts.py (the file is the single source of truth),
        # so drop the table and the per-session attribution column. Idempotent —
        # a no-op once they're gone.
        "ALTER TABLE chat_sessions DROP COLUMN IF EXISTS prompt_version_id",
        "DROP TABLE IF EXISTS prompt_versions",
    ]
    for stmt in alters:
        await conn.execute(stmt)


async def init_db() -> None:
    """Create the pool, then create tables and run column guards."""
    await connect()
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SCHEMA)
            await _ensure_columns(conn)


# ---------------------------------------------------------------------------
# KB helpers
# ---------------------------------------------------------------------------
async def upsert_topic(slug: str, title: dict[str, str], display_order: int,
                       active: bool = True) -> int:
    row = await _pool.fetchrow(
        """
        INSERT INTO kb_topics (slug, title, display_order, active)
        VALUES ($1, $2::jsonb, $3, $4)
        ON CONFLICT (slug) DO UPDATE
          SET title = EXCLUDED.title,
              display_order = EXCLUDED.display_order,
              active = EXCLUDED.active
        RETURNING id
        """,
        slug, json.dumps(title), display_order, active,
    )
    return row["id"]


async def get_topic_by_slug(slug: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, slug, title, display_order, active FROM kb_topics WHERE slug = $1",
        slug,
    )
    return _row_to_topic(row) if row else None


async def get_topic_by_id(topic_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, slug, title, display_order, active FROM kb_topics WHERE id = $1",
        topic_id,
    )
    return _row_to_topic(row) if row else None


async def list_topics(include_hidden: bool = False) -> list[dict[str, Any]]:
    """Visible catalogue for the widget. `other` is hidden from the picker."""
    rows = await _pool.fetch(
        "SELECT id, slug, title, display_order, active FROM kb_topics "
        "WHERE active ORDER BY display_order, id"
    )
    topics = [_row_to_topic(r) for r in rows]
    if not include_hidden:
        topics = [t for t in topics if t["slug"] != "other"]
    return topics


async def get_kb_content(topic_id: int) -> Optional[str]:
    row = await _pool.fetchrow(
        "SELECT content FROM kb_entries "
        "WHERE topic_id = $1 AND active ORDER BY id DESC LIMIT 1",
        topic_id,
    )
    return row["content"] if row else None


def _row_to_topic(row: asyncpg.Record) -> dict[str, Any]:
    title = row["title"]
    if isinstance(title, str):
        title = json.loads(title)
    return {
        "id": row["id"],
        "slug": row["slug"],
        "title": title,
        "display_order": row["display_order"],
        "active": row["active"],
    }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
async def create_session(consumer: str, player_id: Optional[str],
                         lang: Optional[str], user_context: dict[str, Any],
                         session_id: Optional[str] = None) -> str:
    sid = session_id or str(uuid.uuid4())
    await _pool.execute(
        "INSERT INTO chat_sessions "
        "(id, consumer, player_id, lang, user_context) "
        "VALUES ($1, $2, $3, $4, $5::jsonb)",
        sid, consumer, player_id, lang,
        json.dumps(user_context or {}),
    )
    return sid


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, consumer, player_id, lang, conv_lang, topic_id, "
        "user_context, status, escalated, message_count, "
        "context_reset_id, created_at, updated_at "
        "FROM chat_sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    ctx = d.get("user_context")
    if isinstance(ctx, str):
        d["user_context"] = json.loads(ctx)
    return d


async def set_session_topic(session_id: str, topic_id: int) -> None:
    """Point the session at a topic and reset the prompt-history boundary to the
    latest message.

    Switching topics loads a different KB and a topic-routing directive that, on
    a *new* topic, lists the topic the player just came from. If the previous
    topic's transcript were still fed into the prompt, the model would keep
    seeing that conversation and re-suggest switching back — an endless ping-pong.
    By snapshotting the current max message id as the boundary, the first turn
    after a switch carries ONLY the triggering message, and later turns carry
    only messages from the new topic onward. The full transcript is untouched
    (resume/admin views still show everything); only prompt building honours it.
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            boundary = await conn.fetchval(
                "SELECT COALESCE(MAX(id), 0) FROM chat_messages WHERE session_id = $1",
                session_id,
            )
            await conn.execute(
                "UPDATE chat_sessions SET topic_id = $1, context_reset_id = $2, "
                "updated_at = now() WHERE id = $3",
                topic_id, boundary, session_id,
            )


async def set_conv_lang(session_id: str, conv_lang: str) -> None:
    """Persist the sticky CONVERSATION language (the language the player started
    writing in). Independent of `lang` (the browser/UI language), so the widget
    chrome is untouched while the answers follow the player from this turn on."""
    await _pool.execute(
        "UPDATE chat_sessions SET conv_lang = $1, updated_at = now() WHERE id = $2",
        conv_lang, session_id,
    )


async def mark_escalated(session_id: str) -> None:
    await _pool.execute(
        "UPDATE chat_sessions SET status = 'escalated', escalated = TRUE, "
        "updated_at = now() WHERE id = $1",
        session_id,
    )


async def mark_resolved(session_id: str) -> None:
    """Close a session the player ended via the 'finish chat' nudge.

    Sets status='resolved' so it drops out of the open-session metric. An
    escalated session is left untouched — a pending hand-off to a human must not
    be silently closed by the player tapping finish.
    """
    await _pool.execute(
        "UPDATE chat_sessions SET status = 'resolved', updated_at = now() "
        "WHERE id = $1 AND status <> 'escalated'",
        session_id,
    )


async def get_history(session_id: str, limit: int = 50,
                      after_id: int = 0) -> list[dict[str, Any]]:
    """Return messages oldest-first (last `limit` turns).

    `after_id` restricts to messages newer than that id — used for prompt
    building so that turns from before a topic switch (the session's
    `context_reset_id`) are excluded. Default 0 returns the whole transcript
    (resume/admin views).
    """
    rows = await _pool.fetch(
        "SELECT role, content, lang, created_at FROM ("
        "  SELECT role, content, lang, created_at, id FROM chat_messages "
        "  WHERE session_id = $1 AND id > $2 ORDER BY id DESC LIMIT $3"
        ") sub ORDER BY id ASC",
        session_id, after_id, limit,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Atomic turn persistence (INVARIANT: one transaction)
# ---------------------------------------------------------------------------
async def persist_turn(
    session_id: str,
    user_text: str,
    user_lang: Optional[str],
    assistant_text: str,
    assistant_lang: Optional[str],
    ai_meta: dict[str, Any],
) -> int:
    """Insert user + assistant rows, bump counters, write the AI log — atomically.

    Returns the new `message_count` for the session.
    `ai_meta` carries: model, key_used, tokens_in, tokens_out, cached_in,
    cost_usd, latency_ms, ok, error.
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, lang) "
                "VALUES ($1, 'user', $2, $3)",
                session_id, user_text, user_lang,
            )
            await conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, lang, model, key_used, tokens_in, "
                " tokens_out, cached_in, cost_usd) "
                "VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7, $8, $9)",
                session_id, assistant_text, assistant_lang,
                ai_meta.get("model"), ai_meta.get("key_used"),
                ai_meta.get("tokens_in"), ai_meta.get("tokens_out"),
                ai_meta.get("cached_in"), ai_meta.get("cost_usd"),
            )
            await conn.execute(
                "INSERT INTO ai_interaction_logs "
                "(session_id, model, key_used, tokens_in, tokens_out, cached_in, "
                " cost_usd, latency_ms, ok, error) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                session_id, ai_meta.get("model"), ai_meta.get("key_used"),
                ai_meta.get("tokens_in"), ai_meta.get("tokens_out"),
                ai_meta.get("cached_in"), ai_meta.get("cost_usd"),
                ai_meta.get("latency_ms"), ai_meta.get("ok", True),
                ai_meta.get("error"),
            )
            row = await conn.fetchrow(
                "UPDATE chat_sessions "
                "SET message_count = message_count + 1, updated_at = now() "
                "WHERE id = $1 RETURNING message_count",
                session_id,
            )
            return row["message_count"]


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
async def log_ai_interaction(
    session_id: Optional[str], model: Optional[str], key_used: Optional[str],
    tokens_in: Optional[int], tokens_out: Optional[int], cached_in: Optional[int],
    cost_usd: Optional[float], latency_ms: Optional[int], ok: bool,
    error: Optional[str],
) -> None:
    await _pool.execute(
        "INSERT INTO ai_interaction_logs "
        "(session_id, model, key_used, tokens_in, tokens_out, cached_in, "
        " cost_usd, latency_ms, ok, error) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        session_id, model, key_used, tokens_in, tokens_out, cached_in,
        cost_usd, latency_ms, ok, error,
    )


async def log_admin_event(session_id: Optional[str], type_: str,
                          payload: Optional[dict[str, Any]] = None) -> None:
    await _pool.execute(
        "INSERT INTO admin_events (session_id, type, payload) VALUES ($1, $2, $3::jsonb)",
        session_id, type_, json.dumps(payload or {}),
    )


async def record_rate_hit(ip: str) -> None:
    await _pool.execute("INSERT INTO rate_limit_hits (ip) VALUES ($1)", ip)


async def ping() -> bool:
    """Liveness check for /healthz."""
    val = await _pool.fetchval("SELECT 1")
    return val == 1


# ===========================================================================
# Settings, KB CRUD, metrics
# ===========================================================================

# ---------------------------------------------------------------------------
# app_settings (runtime-tunable; precedence app_settings > env > default)
# ---------------------------------------------------------------------------
def _json_value(value: Any) -> Any:
    """JSONB columns come back as str under some drivers; decode defensively."""
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


async def get_all_settings() -> dict[str, Any]:
    rows = await _pool.fetch("SELECT key, value FROM app_settings")
    return {r["key"]: _json_value(r["value"]) for r in rows}


async def get_setting(key: str) -> Optional[Any]:
    row = await _pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
    return _json_value(row["value"]) if row else None


async def set_setting(key: str, value: Any, updated_by: Optional[str] = None) -> None:
    await _pool.execute(
        "INSERT INTO app_settings (key, value, updated_at, updated_by) "
        "VALUES ($1, $2::jsonb, now(), $3) "
        "ON CONFLICT (key) DO UPDATE "
        "  SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by",
        key, json.dumps(value), updated_by,
    )


# ---------------------------------------------------------------------------
# KB CRUD (admin management; reads still go through kb.py helpers)
# ---------------------------------------------------------------------------
async def list_topics_with_counts() -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT t.id, t.slug, t.title, t.display_order, t.active, "
        "  COUNT(e.id) FILTER (WHERE e.active) AS entry_count "
        "FROM kb_topics t LEFT JOIN kb_entries e ON e.topic_id = t.id "
        "GROUP BY t.id ORDER BY t.display_order, t.id"
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        topic = _row_to_topic(r)
        topic["entry_count"] = r["entry_count"]
        out.append(topic)
    return out


async def update_topic(topic_id: int, title: Optional[dict[str, str]],
                       display_order: Optional[int],
                       active: Optional[bool]) -> Optional[dict[str, Any]]:
    await _pool.execute(
        "UPDATE kb_topics SET "
        "title = COALESCE($2::jsonb, title), "
        "display_order = COALESCE($3, display_order), "
        "active = COALESCE($4, active) WHERE id = $1",
        topic_id,
        json.dumps(title) if title is not None else None,
        display_order, active,
    )
    return await get_topic_by_id(topic_id)


async def get_kb_entry(topic_id: int) -> Optional[dict[str, Any]]:
    """The topic's single active KB entry, or None. One entry per topic."""
    row = await _pool.fetchrow(
        "SELECT id, topic_id, content, active FROM kb_entries "
        "WHERE topic_id = $1 AND active ORDER BY id DESC LIMIT 1",
        topic_id,
    )
    return dict(row) if row else None


async def set_kb_content(topic_id: int, content: str) -> int:
    """Set the topic's KB text (one entry per topic). Updates the existing active
    entry in place, or inserts one when the topic has none. Returns the entry id.
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT id FROM kb_entries WHERE topic_id = $1 AND active "
                "ORDER BY id DESC LIMIT 1",
                topic_id,
            )
            if existing is not None:
                await conn.execute(
                    "UPDATE kb_entries SET content = $2 WHERE id = $1",
                    existing, content,
                )
                return existing
            row = await conn.fetchrow(
                "INSERT INTO kb_entries (topic_id, content, active) "
                "VALUES ($1, $2, TRUE) RETURNING id",
                topic_id, content,
            )
            return row["id"]


async def clear_kb_content(topic_id: int) -> bool:
    """Soft-delete the topic's active KB entry. Returns True if one was cleared."""
    res = await _pool.execute(
        "UPDATE kb_entries SET active = FALSE WHERE topic_id = $1 AND active",
        topic_id,
    )
    return not res.endswith(" 0")


# ---------------------------------------------------------------------------
# Metrics / dashboard aggregation (raw rows; derived rates computed in metrics.py)
# ---------------------------------------------------------------------------
def _range_clause(col: str, idx_from: int, idx_to: int) -> str:
    return f"({col} >= ${idx_from} AND {col} < ${idx_to})"


async def overview_aggregates(dt_from: Any, dt_to: Any) -> dict[str, Any]:
    """Raw aggregate counters for the dashboard overview within [from, to)."""
    sess = await _pool.fetchrow(
        "SELECT "
        "  COUNT(*) AS sessions_total, "
        "  COUNT(*) FILTER (WHERE message_count > 0) AS sessions_engaged, "
        "  COUNT(*) FILTER (WHERE status = 'open' AND message_count > 0) AS sessions_open, "
        "  COUNT(*) FILTER (WHERE escalated) AS sessions_escalated, "
        "  COALESCE(AVG(message_count) FILTER (WHERE message_count > 0), 0) "
        "    AS avg_messages_per_session "
        "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2",
        dt_from, dt_to,
    )
    cost = await _pool.fetchrow(
        "SELECT "
        "  COALESCE(SUM(cost_usd), 0) AS cost_usd_total, "
        "  COALESCE(SUM(cached_in), 0) AS cached_in_total, "
        "  COALESCE(SUM(tokens_in), 0) AS tokens_in_total, "
        "  COUNT(*) FILTER (WHERE NOT ok) AS failed_calls "
        "FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2",
        dt_from, dt_to,
    )
    ev = await _pool.fetch(
        "SELECT type, COUNT(*) AS n FROM admin_events "
        "WHERE created_at >= $1 AND created_at < $2 GROUP BY type",
        dt_from, dt_to,
    )
    events = {r["type"]: r["n"] for r in ev}
    return {
        "sessions_total": sess["sessions_total"],
        "sessions_engaged": sess["sessions_engaged"],
        "sessions_open": sess["sessions_open"],
        "sessions_escalated": sess["sessions_escalated"],
        "avg_messages_per_session": float(sess["avg_messages_per_session"]),
        "cost_usd_total": float(cost["cost_usd_total"]),
        "cached_in_total": int(cost["cached_in_total"]),
        "tokens_in_total": int(cost["tokens_in_total"]),
        "events": events,
    }


async def timeseries(metric: str, dt_from: Any, dt_to: Any,
                     bucket: str = "day") -> list[dict[str, Any]]:
    """Per-bucket series for sessions | cost | escalation_rate."""
    trunc = "day" if bucket not in ("hour", "day", "week", "month") else bucket
    if metric == "cost":
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COALESCE(SUM(cost_usd), 0) AS value "
            "FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2 "
            "GROUP BY bucket ORDER BY bucket",
            dt_from, dt_to,
        )
    elif metric == "escalation_rate":
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COUNT(*) FILTER (WHERE message_count > 0) AS engaged, "
            "COUNT(*) FILTER (WHERE escalated) AS escalated "
            "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2 "
            "GROUP BY bucket ORDER BY bucket",
            dt_from, dt_to,
        )
        return [
            {"bucket": r["bucket"].isoformat(),
             "value": (r["escalated"] / r["engaged"]) if r["engaged"] else 0.0}
            for r in rows
        ]
    else:  # sessions
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COUNT(*) AS value "
            "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2 "
            "GROUP BY bucket ORDER BY bucket",
            dt_from, dt_to,
        )
    return [{"bucket": r["bucket"].isoformat(), "value": float(r["value"])}
            for r in rows]


async def by_topic(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT t.slug, t.title, "
        "  COUNT(s.id) AS sessions, "
        "  COUNT(s.id) FILTER (WHERE s.escalated) AS escalated, "
        "  COALESCE(AVG(s.message_count) FILTER (WHERE s.message_count > 0), 0) AS avg_messages "
        "FROM chat_sessions s JOIN kb_topics t ON t.id = s.topic_id "
        "WHERE s.created_at >= $1 AND s.created_at < $2 "
        "GROUP BY t.slug, t.title ORDER BY sessions DESC",
        dt_from, dt_to,
    )
    out = []
    for r in rows:
        title = _json_value(r["title"]) if r["title"] is not None else {}
        out.append({
            "slug": r["slug"],
            "title": title,
            "sessions": r["sessions"],
            "escalated": r["escalated"],
            "escalation_rate": (r["escalated"] / r["sessions"]) if r["sessions"] else 0.0,
            "avg_messages": float(r["avg_messages"]),
        })
    return out


async def by_language(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT COALESCE(lang, 'unknown') AS lang, "
        "  COUNT(*) AS sessions, "
        "  COUNT(*) FILTER (WHERE escalated) AS escalated "
        "FROM chat_sessions "
        "WHERE created_at >= $1 AND created_at < $2 "
        "GROUP BY COALESCE(lang, 'unknown') ORDER BY sessions DESC",
        dt_from, dt_to,
    )
    return [
        {"lang": r["lang"], "sessions": r["sessions"], "escalated": r["escalated"],
         "escalation_rate": (r["escalated"] / r["sessions"]) if r["sessions"] else 0.0}
        for r in rows
    ]


async def list_sessions(dt_from: Any, dt_to: Any, *, topic: Optional[str] = None,
                        lang: Optional[str] = None, status: Optional[str] = None,
                        escalated: Optional[bool] = None, q: Optional[str] = None,
                        page: int = 1, page_size: int = 25) -> dict[str, Any]:
    where = ["s.created_at >= $1", "s.created_at < $2"]
    args: list[Any] = [dt_from, dt_to]
    if topic:
        args.append(topic); where.append(f"t.slug = ${len(args)}")
    if lang:
        args.append(lang); where.append(f"s.lang = ${len(args)}")
    if status:
        args.append(status); where.append(f"s.status = ${len(args)}")
    if escalated is not None:
        args.append(escalated); where.append(f"s.escalated = ${len(args)}")
    if q:
        args.append(f"%{q}%")
        where.append(
            f"EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id = s.id "
            f"AND m.content ILIKE ${len(args)})"
        )
    where_sql = " AND ".join(where)
    total = await _pool.fetchval(
        f"SELECT COUNT(*) FROM chat_sessions s "
        f"LEFT JOIN kb_topics t ON t.id = s.topic_id WHERE {where_sql}",
        *args,
    )
    page = max(page, 1)
    args2 = args + [page_size, (page - 1) * page_size]
    rows = await _pool.fetch(
        f"SELECT s.id, s.lang, s.status, s.escalated, s.message_count, "
        f"  s.created_at, s.updated_at, t.slug AS topic "
        f"FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        f"WHERE {where_sql} ORDER BY s.created_at DESC "
        f"LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
        *args2,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat()
        d["updated_at"] = d["updated_at"].isoformat()
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def session_detail(session_id: str) -> Optional[dict[str, Any]]:
    session = await get_session(session_id)
    if session is None:
        return None
    msgs = await _pool.fetch(
        "SELECT role, content, lang, model, key_used, tokens_in, tokens_out, "
        "cached_in, cost_usd, created_at FROM chat_messages "
        "WHERE session_id = $1 ORDER BY id ASC",
        session_id,
    )
    logs = await _pool.fetch(
        "SELECT model, key_used, tokens_in, tokens_out, cached_in, cost_usd, "
        "latency_ms, ok, error, created_at FROM ai_interaction_logs "
        "WHERE session_id = $1 ORDER BY id ASC",
        session_id,
    )
    def _msg(r):
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        d["cost_usd"] = float(d["cost_usd"]) if d["cost_usd"] is not None else None
        return d
    cost_total = sum((float(r["cost_usd"]) for r in msgs if r["cost_usd"]), 0.0)
    # Serialize session timestamps.
    for ts in ("created_at", "updated_at"):
        if session.get(ts) is not None and not isinstance(session[ts], str):
            session[ts] = session[ts].isoformat()
    return {
        "session": session,
        "messages": [_msg(r) for r in msgs],
        "logs": [_msg(r) for r in logs],
        "cost_usd_total": round(cost_total, 6),
    }


async def unresolved_by_topic(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    """Escalated/unresolved sessions grouped by topic, with a sample first
    user message per session, sorted by frequency (most unresolved first)."""
    rows = await _pool.fetch(
        "SELECT COALESCE(t.slug, 'unknown') AS topic, "
        "  COALESCE(t.title, '{}'::jsonb) AS title, "
        "  s.id AS session_id, s.message_count, s.created_at, "
        "  (SELECT m.content FROM chat_messages m "
        "    WHERE m.session_id = s.id AND m.role = 'user' "
        "    ORDER BY m.id ASC LIMIT 1) AS first_message "
        "FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        "WHERE s.escalated AND s.created_at >= $1 AND s.created_at < $2 "
        "ORDER BY topic, s.created_at DESC",
        dt_from, dt_to,
    )
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        topic = r["topic"]
        g = groups.setdefault(topic, {
            "topic": topic,
            "title": _json_value(r["title"]) if r["title"] is not None else {},
            "count": 0,
            "sessions": [],
        })
        g["count"] += 1
        g["sessions"].append({
            "session_id": str(r["session_id"]),
            "message_count": r["message_count"],
            "first_message": r["first_message"],
            "created_at": r["created_at"].isoformat(),
        })
    return sorted(groups.values(), key=lambda x: x["count"], reverse=True)
