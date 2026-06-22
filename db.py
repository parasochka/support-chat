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

-- PHASE 2 ----------------------------------------------------------
-- Runtime-tunable settings (hot-reloaded; precedence app_settings > env > default).
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

-- System-prompt versioning + A/B.
CREATE TABLE IF NOT EXISTS prompt_versions (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  body         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'draft',   -- draft | published | archived
  is_default   BOOLEAN NOT NULL DEFAULT FALSE,
  ab_weight    INT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ
);

-- Escalation tickets (snapshot + delivery state).
CREATE TABLE IF NOT EXISTS escalation_tickets (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID NOT NULL REFERENCES chat_sessions(id),
  reason      TEXT NOT NULL,
  channel     TEXT NOT NULL,            -- 'telegram' | 'button'
  delivered   BOOLEAN NOT NULL DEFAULT FALSE,
  payload     JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_admin_events_session ON admin_events(session_id);
CREATE INDEX IF NOT EXISTS idx_admin_events_type ON admin_events(type, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_created ON chat_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_logs_created ON ai_interaction_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_escalation_tickets_session ON escalation_tickets(session_id);
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
        # Phase 2: which prompt_versions row this session ran on (A/B attribution).
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "prompt_version_id INT",
        # Prompt-history boundary bumped on each topic switch (loop fix); only
        # messages newer than this id are sent to the model.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "context_reset_id BIGINT NOT NULL DEFAULT 0",
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
                         lang: Optional[str], user_context: dict[str, Any],
                         prompt_version_id: Optional[int] = None,
                         session_id: Optional[str] = None) -> str:
    sid = session_id or str(uuid.uuid4())
    await _pool.execute(
        "INSERT INTO chat_sessions "
        "(id, consumer, player_id, lang, user_context, prompt_version_id) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6)",
        sid, consumer, player_id, lang,
        json.dumps(user_context or {}), prompt_version_id,
    )
    return sid


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, consumer, player_id, lang, lang_locked, topic_id, user_context, "
        "status, escalated, message_count, prompt_version_id, context_reset_id, "
        "created_at, updated_at "
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
# PHASE 2 — settings, prompt versioning, escalation tickets, KB CRUD, metrics
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
# prompt_versions
# ---------------------------------------------------------------------------
def _row_to_prompt_version(row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for ts in ("created_at", "published_at"):
        if d.get(ts) is not None:
            d[ts] = d[ts].isoformat()
    return d


async def list_prompt_versions() -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT id, name, body, status, is_default, ab_weight, created_at, "
        "published_at FROM prompt_versions ORDER BY id DESC"
    )
    return [_row_to_prompt_version(r) for r in rows]


async def get_prompt_version(version_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, name, body, status, is_default, ab_weight, created_at, "
        "published_at FROM prompt_versions WHERE id = $1",
        version_id,
    )
    return _row_to_prompt_version(row)


async def get_default_prompt_version() -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, name, body, status, is_default, ab_weight, created_at, "
        "published_at FROM prompt_versions WHERE is_default ORDER BY id DESC LIMIT 1"
    )
    return _row_to_prompt_version(row)


async def get_active_ab_versions() -> list[dict[str, Any]]:
    """Published versions participating in an A/B split (ab_weight > 0)."""
    rows = await _pool.fetch(
        "SELECT id, name, body, status, is_default, ab_weight, created_at, "
        "published_at FROM prompt_versions "
        "WHERE status = 'published' AND ab_weight > 0 ORDER BY id"
    )
    return [_row_to_prompt_version(r) for r in rows]


async def create_prompt_version(name: str, body: str,
                                status: str = "draft",
                                is_default: bool = False) -> int:
    row = await _pool.fetchrow(
        "INSERT INTO prompt_versions (name, body, status, is_default, published_at) "
        "VALUES ($1, $2, $3, $4, CASE WHEN $4 THEN now() ELSE NULL END) RETURNING id",
        name, body, status, is_default,
    )
    return row["id"]


async def update_prompt_version(version_id: int, name: Optional[str],
                                body: Optional[str]) -> Optional[dict[str, Any]]:
    """Edit a DRAFT version's name/body. Published versions are immutable."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "SELECT status FROM prompt_versions WHERE id = $1", version_id
            )
            if cur is None:
                return None
            if cur["status"] != "draft":
                raise ValueError("only draft versions can be edited")
            await conn.execute(
                "UPDATE prompt_versions SET "
                "name = COALESCE($2, name), body = COALESCE($3, body) WHERE id = $1",
                version_id, name, body,
            )
    return await get_prompt_version(version_id)


async def publish_prompt_version(version_id: int) -> Optional[dict[str, Any]]:
    """Make `version_id` the live default; demote the previous default.

    Deliberate, one-time cache reset (the new core breaks the warm prefix).
    """
    async with _pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "SELECT id FROM prompt_versions WHERE id = $1", version_id
            )
            if cur is None:
                return None
            await conn.execute("UPDATE prompt_versions SET is_default = FALSE")
            await conn.execute(
                "UPDATE prompt_versions SET status = 'published', is_default = TRUE, "
                "published_at = COALESCE(published_at, now()) WHERE id = $1",
                version_id,
            )
    return await get_prompt_version(version_id)


async def archive_prompt_version(version_id: int) -> Optional[dict[str, Any]]:
    async with _pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "SELECT is_default FROM prompt_versions WHERE id = $1", version_id
            )
            if cur is None:
                return None
            if cur["is_default"]:
                raise ValueError("cannot archive the live default version")
            await conn.execute(
                "UPDATE prompt_versions SET status = 'archived', ab_weight = 0 "
                "WHERE id = $1",
                version_id,
            )
    return await get_prompt_version(version_id)


async def set_ab_weights(weights: list[dict[str, int]]) -> None:
    """Set ab_weight for the given published version ids (others left untouched)."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for w in weights:
                await conn.execute(
                    "UPDATE prompt_versions SET ab_weight = $2 "
                    "WHERE id = $1 AND status = 'published'",
                    int(w["id"]), int(w["weight"]),
                )


# ---------------------------------------------------------------------------
# escalation_tickets
# ---------------------------------------------------------------------------
async def create_escalation_ticket(session_id: str, reason: str, channel: str,
                                   delivered: bool, payload: dict[str, Any]) -> int:
    row = await _pool.fetchrow(
        "INSERT INTO escalation_tickets "
        "(session_id, reason, channel, delivered, payload) "
        "VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id",
        session_id, reason, channel, delivered, json.dumps(payload or {}),
    )
    return row["id"]


async def mark_ticket_delivered(ticket_id: int) -> None:
    await _pool.execute(
        "UPDATE escalation_tickets SET delivered = TRUE WHERE id = $1", ticket_id
    )


# ---------------------------------------------------------------------------
# KB CRUD (Phase 2 management; reads still go through kb.py helpers)
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


async def list_kb_entries(topic_id: int, include_inactive: bool = False
                          ) -> list[dict[str, Any]]:
    q = ("SELECT id, topic_id, lang, content, version, active "
         "FROM kb_entries WHERE topic_id = $1")
    if not include_inactive:
        q += " AND active"
    q += " ORDER BY lang, version DESC, id DESC"
    rows = await _pool.fetch(q, topic_id)
    return [dict(r) for r in rows]


async def create_kb_entry(topic_id: int, lang: str, content: str) -> int:
    """Create a new active entry for topic+lang at the next version number,
    deactivating prior active entries for that topic+lang (keeps history)."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            ver = await conn.fetchval(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM kb_entries "
                "WHERE topic_id = $1 AND lang = $2",
                topic_id, lang,
            )
            await conn.execute(
                "UPDATE kb_entries SET active = FALSE "
                "WHERE topic_id = $1 AND lang = $2 AND active",
                topic_id, lang,
            )
            row = await conn.fetchrow(
                "INSERT INTO kb_entries (topic_id, lang, content, version, active) "
                "VALUES ($1, $2, $3, $4, TRUE) RETURNING id",
                topic_id, lang, content, ver,
            )
            return row["id"]


async def update_kb_entry(entry_id: int, content: str) -> Optional[int]:
    """Edit = a new version row for the same topic+lang (history preserved).

    Returns the new entry id, or None if the source entry doesn't exist.
    """
    src = await _pool.fetchrow(
        "SELECT topic_id, lang FROM kb_entries WHERE id = $1", entry_id
    )
    if src is None:
        return None
    return await create_kb_entry(src["topic_id"], src["lang"], content)


async def soft_delete_kb_entry(entry_id: int) -> bool:
    res = await _pool.execute(
        "UPDATE kb_entries SET active = FALSE WHERE id = $1 AND active", entry_id
    )
    return res.endswith("1")


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


async def ab_results(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT s.prompt_version_id AS version_id, pv.name AS version_name, "
        "  COUNT(*) FILTER (WHERE s.message_count > 0) AS sessions, "
        "  COUNT(*) FILTER (WHERE s.escalated) AS escalated, "
        "  COALESCE(AVG(s.message_count) FILTER (WHERE s.message_count > 0), 0) AS avg_messages "
        "FROM chat_sessions s "
        "LEFT JOIN prompt_versions pv ON pv.id = s.prompt_version_id "
        "WHERE s.created_at >= $1 AND s.created_at < $2 "
        "  AND s.prompt_version_id IS NOT NULL "
        "GROUP BY s.prompt_version_id, pv.name ORDER BY s.prompt_version_id",
        dt_from, dt_to,
    )
    # Per-version cost from logs joined via session.
    cost_rows = await _pool.fetch(
        "SELECT s.prompt_version_id AS version_id, "
        "  COALESCE(SUM(l.cost_usd), 0) AS cost "
        "FROM ai_interaction_logs l JOIN chat_sessions s ON s.id = l.session_id "
        "WHERE l.created_at >= $1 AND l.created_at < $2 "
        "  AND s.prompt_version_id IS NOT NULL "
        "GROUP BY s.prompt_version_id",
        dt_from, dt_to,
    )
    cost_by_v = {r["version_id"]: float(r["cost"]) for r in cost_rows}
    out = []
    for r in rows:
        sessions = r["sessions"]
        cost = cost_by_v.get(r["version_id"], 0.0)
        out.append({
            "version_id": r["version_id"],
            "version_name": r["version_name"],
            "sessions": sessions,
            "escalated": r["escalated"],
            "escalation_rate": (r["escalated"] / sessions) if sessions else 0.0,
            "resolution_rate": (1 - (r["escalated"] / sessions)) if sessions else 0.0,
            "avg_messages": float(r["avg_messages"]),
            "avg_cost": (cost / sessions) if sessions else 0.0,
        })
    return out


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


async def count_admin_events(type_: str, dt_from: Any, dt_to: Any) -> int:
    return await _pool.fetchval(
        "SELECT COUNT(*) FROM admin_events "
        "WHERE type = $1 AND created_at >= $2 AND created_at < $3",
        type_, dt_from, dt_to,
    )
