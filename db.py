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
  lang       TEXT NOT NULL DEFAULT 'ru',
  content    TEXT NOT NULL,
  version    INT NOT NULL DEFAULT 1,
  active     BOOLEAN NOT NULL DEFAULT TRUE
);

-- SESSIONS & MESSAGES ----------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            UUID PRIMARY KEY,
  consumer      TEXT NOT NULL DEFAULT 'web',
  player_id     TEXT,
  lang          TEXT,
  lang_locked   BOOLEAN NOT NULL DEFAULT FALSE,
  topic_id      INT REFERENCES kb_topics(id),
  user_context  JSONB NOT NULL DEFAULT '{}',
  status        TEXT NOT NULL DEFAULT 'open',
  escalated     BOOLEAN NOT NULL DEFAULT FALSE,
  message_count INT NOT NULL DEFAULT 0,
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

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_admin_events_session ON admin_events(session_id);
"""


async def _ensure_columns(conn: asyncpg.Connection) -> None:
    """Explicit ALTER guards for columns added after a table first shipped.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so any new
    column on an already-deployed table must be added here. Each statement is
    idempotent via `ADD COLUMN IF NOT EXISTS`. (None needed yet — Phase 1
    baseline — but the seam is here so the rule from the brief is honoured.)
    """
    alters: list[str] = [
        # Manual language switcher: a true value means the player picked the
        # answer/UI language by hand, which hard-overrides auto-mirroring.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "lang_locked BOOLEAN NOT NULL DEFAULT FALSE",
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


async def replace_topic_entry(topic_id: int, lang: str, content: str) -> None:
    """Idempotent seed: deactivate existing entries for the topic+lang, insert fresh.

    Keeps the seed re-runnable without piling up duplicate active chunks.
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE kb_entries SET active = FALSE WHERE topic_id = $1 AND lang = $2",
                topic_id, lang,
            )
            await conn.execute(
                "INSERT INTO kb_entries (topic_id, lang, content, version, active) "
                "VALUES ($1, $2, $3, 1, TRUE)",
                topic_id, lang, content,
            )


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


async def get_kb_content(topic_id: int, lang: str = "ru") -> Optional[str]:
    row = await _pool.fetchrow(
        "SELECT content FROM kb_entries "
        "WHERE topic_id = $1 AND lang = $2 AND active "
        "ORDER BY version DESC, id DESC LIMIT 1",
        topic_id, lang,
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
                         lang: Optional[str], user_context: dict[str, Any]) -> str:
    sid = str(uuid.uuid4())
    await _pool.execute(
        "INSERT INTO chat_sessions (id, consumer, player_id, lang, user_context) "
        "VALUES ($1, $2, $3, $4, $5::jsonb)",
        sid, consumer, player_id, lang, json.dumps(user_context or {}),
    )
    return sid


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, consumer, player_id, lang, lang_locked, topic_id, user_context, "
        "status, escalated, message_count, created_at, updated_at "
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
    await _pool.execute(
        "UPDATE chat_sessions SET topic_id = $1, updated_at = now() WHERE id = $2",
        topic_id, session_id,
    )


async def set_session_lang(session_id: str, lang: str, locked: bool = False) -> None:
    """Persist the session's answer/UI language. `locked=True` records that the
    player chose it by hand, which hard-overrides auto language mirroring."""
    await _pool.execute(
        "UPDATE chat_sessions SET lang = $1, lang_locked = $2, updated_at = now() "
        "WHERE id = $3",
        lang, locked, session_id,
    )


async def mark_escalated(session_id: str) -> None:
    await _pool.execute(
        "UPDATE chat_sessions SET status = 'escalated', escalated = TRUE, "
        "updated_at = now() WHERE id = $1",
        session_id,
    )


async def get_history(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return messages oldest-first (last `limit` turns)."""
    rows = await _pool.fetch(
        "SELECT role, content, lang, created_at FROM ("
        "  SELECT role, content, lang, created_at, id FROM chat_messages "
        "  WHERE session_id = $1 ORDER BY id DESC LIMIT $2"
        ") sub ORDER BY id ASC",
        session_id, limit,
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
