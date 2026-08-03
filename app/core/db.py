"""Direct asyncpg data layer — no ORM, no migration files.

The schema *is* the code in `init_db()`. To change schema, edit `init_db()`.
Adding a column to an existing table needs an explicit guard because
`CREATE TABLE IF NOT EXISTS` will not ALTER an existing table — see
`_ensure_columns()`.

Every read/write goes through a `db.<name>(...)` helper; nothing else touches
tables directly.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque as _deque
from typing import Any, Optional, Sequence

import asyncpg

from app.core import config

_pool: Optional[asyncpg.Pool] = None


async def connect() -> asyncpg.Pool:
    """Create the global connection pool (idempotent)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=config.DATABASE_URL,
            # Sized per process ROLE (config.DB_POOL_MAX): a worker runs many
            # concurrent player shards and needs more slots than a web process
            # serving requests. Keep an eye on the server's max_connections
            # before raising it — every replica opens its own pool.
            min_size=config.DB_POOL_MIN,
            max_size=config.DB_POOL_MAX,
            command_timeout=30,
            # Cap establishing a NEW backend connection so a DOWN database fails
            # fast (raises) instead of blocking the acquire indefinitely on
            # connect. Query duration is separately bounded by command_timeout.
            timeout=config.DB_CONNECT_TIMEOUT_SEC,
        )
    return _pool


def _acquire():
    """Acquire a pooled connection with a bounded wait (use as `async with`).

    asyncpg's convenience helpers (`Pool.fetch/execute/...`) block on acquire
    with NO ceiling — `command_timeout` bounds how long a query may RUN, not how
    long it may wait for a free slot — so under pool exhaustion a request would
    hang forever. Every query in this module goes through this acquire (directly,
    or via the `_fetch/_fetchrow/_fetchval/_execute` wrappers below), so
    exhaustion surfaces as a retryable error instead of an unbounded hang
    (DB_ACQUIRE_TIMEOUT_SEC).
    """
    return _pool.acquire(timeout=config.DB_ACQUIRE_TIMEOUT_SEC)


# The one-liner query helpers. They exist so that a plain `SELECT` gets the
# bounded acquire above without every call site having to open an `async with`:
# reaching for `_fetch(...)` directly would silently opt that query out of
# the pool-exhaustion ceiling.
async def _fetch(query: str, *args) -> list[asyncpg.Record]:
    async with _acquire() as conn:
        return await conn.fetch(query, *args)


async def _fetchrow(query: str, *args) -> Optional[asyncpg.Record]:
    async with _acquire() as conn:
        return await conn.fetchrow(query, *args)


async def _fetchval(query: str, *args) -> Any:
    async with _acquire() as conn:
        return await conn.fetchval(query, *args)


async def _execute(query: str, *args) -> str:
    async with _acquire() as conn:
        return await conn.execute(query, *args)


async def _executemany(query: str, args) -> None:
    async with _acquire() as conn:
        return await conn.executemany(query, args)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call db.connect()/db.init_db() first")
    return _pool


async def dedicated_connection() -> asyncpg.Connection:
    """One connection OUTSIDE the pool, for long-lived session state.

    The media normalizer parks a session advisory lock on it for the whole
    sweep — video encodes hold the lock for minutes, and parking that on a
    pool connection would eat one of the 10 request slots (and the pool's
    command_timeout would kill a blocking pg_advisory_lock wait). No
    command_timeout on purpose. The caller MUST close() it; the advisory
    lock is released with the connection even if the explicit unlock fails.
    """
    return await asyncpg.connect(dsn=config.DATABASE_URL,
                                 timeout=config.DB_CONNECT_TIMEOUT_SEC)


def _as_text(value: Any) -> Optional[str]:
    """Coerce a value bound to a TEXT column to str (None stays SQL NULL).

    Player context arriving from a partner (a signed handshake, a nonce payload,
    a Player-API pull) is free-form JSON: fields like `id`, `balance` or
    `vip_level` may come across as numbers or booleans rather than strings.
    asyncpg binds strictly, so an int destined for a TEXT column raises DataError
    and 500s the write (session create / retention link). Coercing scalars to
    their string form keeps the column contract without trusting the caller's
    JSON types. Non-scalars (dict/list) are left untouched — those belong to a
    jsonb column or signal a caller bug we should not mask.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
-- TENANCY ------------------------------------------------------------
-- Partners own casino products; nearly every other table hangs off a
-- product (see CLAUDE.md "Multi-tenancy"). Boot wraps pre-tenancy data
-- into a 'default' partner/product so old deployments keep working.
CREATE TABLE IF NOT EXISTS partners (
  id            SERIAL PRIMARY KEY,
  slug          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
  id            SERIAL PRIMARY KEY,
  partner_id    INT NOT NULL REFERENCES partners(id),
  slug          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  -- Public embed identifier: the widget sends it on /session (and /topics,
  -- /i18n) so the service knows WHICH casino is talking. Not a secret (it
  -- ships in the host page's HTML) but unguessable, and rotatable from the
  -- admin Structure tab.
  widget_key    TEXT UNIQUE NOT NULL,
  -- Per-product secrets, ENCRYPTED at rest via secretbox.py (master key in
  -- env, never in the DB). NULL = not configured -> deploy-level env fallback
  -- (OPENAI_API_KEY[_FALLBACK] / WIDGET_HANDSHAKE_SECRET).
  openai_key_primary_enc  TEXT,
  openai_key_fallback_enc TEXT,
  handshake_secret_enc    TEXT,
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-product settings overrides. Same keys and validators as app_settings;
-- resolution is product_settings -> app_settings -> env -> built-in default
-- (settings._group merges field-by-field), so a product only stores the
-- knobs its owner actually changed.
CREATE TABLE IF NOT EXISTS product_settings (
  product_id  INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,
  value       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT,
  PRIMARY KEY (product_id, key)
);

-- KB ----------------------------------------------------------------
-- Topics are per product: slug is unique WITHIN a product (the unique index
-- lives in _ensure_columns so legacy tables get it after the column guard).
CREATE TABLE IF NOT EXISTS kb_topics (
  id            SERIAL PRIMARY KEY,
  product_id    INT REFERENCES products(id),
  slug          TEXT NOT NULL,
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

-- {placeholder} registry, one set per product (key unique within a product;
-- the unique index lives in _ensure_columns, same reason as kb_topics).
CREATE TABLE IF NOT EXISTS kb_variables (
  product_id  INT REFERENCES products(id),
  key         TEXT NOT NULL,
  description TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT
);

-- SESSIONS & MESSAGES ----------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            UUID PRIMARY KEY,
  consumer      TEXT NOT NULL DEFAULT 'web',
  product_id    INT REFERENCES products(id),
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
  -- Proactive (agent-initiated) assistant turns only: the trigger + occasion
  -- that made the bot write first ("deposit_confirmed: the player just made a
  -- deposit"). Rides into the prompt history so the persona later KNOWS why it
  -- wrote, and into the admin transcript. NULL on every ordinary turn.
  ping_context TEXT,
  -- The validated site-map CTA button attached to an assistant message
  -- ([[LINK:url]], retention). Buttons are chrome, not text — recorded here so
  -- the prompt history can show WHICH page was already linked (link rotation).
  link_url    TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OPS / AUDIT ------------------------------------------------------
-- product_id is denormalized onto both log tables (copied from the session /
-- the admin's selected scope) so per-product dashboards aggregate without a
-- chat_sessions join.
-- consumer + source are the SPEND ATTRIBUTION pair, denormalized for the same
-- reason product_id is: half these rows have no session to join (a background
-- pass logs with session_id NULL), so without them a dashboard has to GUESS who
-- spent the money — and guessing "session-less ⇒ photo metadata" charged the
-- quality judge's and the proactive agent's calls to the media bucket.
--   consumer: 'web' | 'telegram' — WHICH FACADE the spend belongs to. For a
--             quality review it is the facade of the REVIEWED conversation, so
--             a support review lands in the support dashboard and a Telegram
--             review in the retention one.
--   source:   'chat' | 'agent' | 'review' | 'media' — WHAT the call was
--             (dialogue turn / proactive agent / AI judge / media cataloguing).
-- Legacy rows carry NULL and are classified at read time (_LOG_SOURCE_SQL).
CREATE TABLE IF NOT EXISTS ai_interaction_logs (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  product_id  INT,
  consumer    TEXT,
  source      TEXT,
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
  product_id  INT,
  type        TEXT NOT NULL,
  payload     JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Named admin/manager users (email + password pair). These are the accounts
-- admins create from the Users tab; every admin login goes through this table
-- (there is no password-only owner login). role drives authorization: admin may
-- write, manager is read-only. The password is stored only as a salted PBKDF2
-- hash (auth.hash_password), never in plaintext.
CREATE TABLE IF NOT EXISTS admin_users (
  email         TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'manager',
  active        BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- user <-> scope <-> role memberships (multi-tenancy). scope_type is
-- 'global' (both ids NULL), 'partner' (partner_id set) or 'product'
-- (product_id set); role is 'admin' (write) or 'manager' (read-only) WITHIN
-- that scope. Authorization derives from these rows; the legacy
-- admin_users.role column survives only as the source for the boot migration
-- (each pre-tenancy account gets a global membership with its old role).
CREATE TABLE IF NOT EXISTS admin_memberships (
  id            SERIAL PRIMARY KEY,
  email         TEXT NOT NULL REFERENCES admin_users(email) ON DELETE CASCADE,
  scope_type    TEXT NOT NULL,
  partner_id    INT REFERENCES partners(id) ON DELETE CASCADE,
  product_id    INT REFERENCES products(id) ON DELETE CASCADE,
  role          TEXT NOT NULL DEFAULT 'manager',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Runtime-tunable settings (hot-reloaded; precedence app_settings > env > default).
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

-- RETENTION / TELEGRAM ----------------------------------------------
-- Second facade over the same AI core: a Telegram retention bot. Every table
-- hangs off a product (multi-tenant, like the KB). Support-KB is NOT reused
-- here (retention has its own flat scenario base). See the retention section
-- in CLAUDE.md.
-- NB: retention_managers is declared BEFORE retention_users because the latter
-- carries an FK to it (assigned_manager_id).

-- Pool of live managers a player is routed to (round-robin, sticky).
CREATE TABLE IF NOT EXISTS retention_managers (
  id             BIGSERIAL PRIMARY KEY,
  product_id     INT NOT NULL REFERENCES products(id),
  display_name   TEXT NOT NULL,
  username       TEXT NOT NULL,     -- Telegram @username (no leading @)
  active         BOOLEAN NOT NULL DEFAULT TRUE,
  assigned_count INT NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A player inside the Telegram bot: the tg<->player link + a snapshot of the
-- whitelisted profile (_CONTEXT_FIELDS) + progression/limit state.
CREATE TABLE IF NOT EXISTS retention_users (
  id                   BIGSERIAL PRIMARY KEY,
  product_id           INT NOT NULL REFERENCES products(id),
  tg_user_id           BIGINT NOT NULL,
  tg_username          TEXT,
  player_id            TEXT,
  entry_type           TEXT NOT NULL DEFAULT 'retention',  -- 'retention' | 'escalation'
  -- profile snapshot (mirrors prompts._CONTEXT_FIELDS)
  full_name            TEXT,
  email                TEXT,
  activation_status    TEXT,
  country              TEXT,
  balance              TEXT,
  vip_level            TEXT,
  registration_date    TEXT,
  profile_source       TEXT,        -- 'handshake' | 'pull' | 'push'
  profile_updated_at   TIMESTAMPTZ,
  unlocked_stage       INT NOT NULL DEFAULT 1,
  last_stage_advance_at TIMESTAMPTZ,
  assigned_manager_id  BIGINT REFERENCES retention_managers(id),
  subscribed           BOOLEAN NOT NULL DEFAULT FALSE,
  meaningful_msgs      INT NOT NULL DEFAULT 0,   -- lifetime meaningful player msgs
  msgs_since_photo     INT NOT NULL DEFAULT 0,   -- proactive-cooldown counter
  photos_day           DATE,                     -- day the daily counter counts
  photos_sent_today    INT NOT NULL DEFAULT 0,
  conv_lang            TEXT,
  session_id           UUID REFERENCES chat_sessions(id),
  last_active_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- casino-side activity signals (fed by the partner push webhook / Player-API
  -- pull; the agent's state resolver keys on them). NULL until the casino supplies them.
  last_login_at        TIMESTAMPTZ,
  last_played_at       TIMESTAMPTZ,
  last_deposit_at      TIMESTAMPTZ,
  -- proactive-ping state (the "don't annoy the player" ledger head)
  pings_muted          BOOLEAN NOT NULL DEFAULT FALSE,  -- /stop opt-out
  unreachable          BOOLEAN NOT NULL DEFAULT FALSE,  -- bot blocked by user
  last_ping_at         TIMESTAMPTZ,
  pings_day            DATE,
  pings_sent_today     INT NOT NULL DEFAULT 0,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The single flat retention-KB: scenario/offer playbooks + links, loaded WHOLE
-- into Layer 2 (byte-stable per product). NOT kb_topics (that is support-only).
CREATE TABLE IF NOT EXISTS retention_kb (
  id           BIGSERIAL PRIMARY KEY,
  product_id   INT NOT NULL REFERENCES products(id),
  title        TEXT NOT NULL,
  trigger_when TEXT,
  body         TEXT NOT NULL,
  links        JSONB NOT NULL DEFAULT '[]',
  sort_order   INT NOT NULL DEFAULT 0,
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  updated_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The media library: up to ~500 photos per brand, gated by level_min (VIP tier)
-- x stage (explicitness ladder). storage_ref is the on-disk filename; the first
-- Telegram send caches telegram_file_id so later sends skip the re-upload.
CREATE TABLE IF NOT EXISTS retention_photos (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  storage_ref       TEXT,
  media_type        TEXT NOT NULL DEFAULT 'photo',  -- 'photo' | 'video'
  telegram_file_id  TEXT,
  tg_width          INT,   -- video sendVideo attrs, probed at normalization
  tg_height         INT,
  tg_duration_sec   INT,
  description        TEXT NOT NULL DEFAULT '',
  tags              JSONB NOT NULL DEFAULT '[]',
  level_min         INT NOT NULL DEFAULT 0,   -- min VIP tier ordinal to unlock
  stage             INT NOT NULL DEFAULT 1,   -- explicitness ladder step
  category          TEXT,
  sort_order        INT NOT NULL DEFAULT 0,
  active            BOOLEAN NOT NULL DEFAULT TRUE,
  views_count       INT NOT NULL DEFAULT 0,
  created_by        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Who saw which photo (anti-repeat + per-photo counters).
CREATE TABLE IF NOT EXISTS retention_photo_views (
  id                BIGSERIAL PRIMARY KEY,
  photo_id          BIGINT NOT NULL REFERENCES retention_photos(id),
  retention_user_id BIGINT NOT NULL REFERENCES retention_users(id),
  product_id        INT NOT NULL REFERENCES products(id),
  viewed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One-time deeplink exchange: the site posts a handshake, gets a nonce; the bot
-- redeems it on /start. TTL-bounded, single-use.
CREATE TABLE IF NOT EXISTS retention_nonces (
  nonce       TEXT PRIMARY KEY,
  product_id  INT NOT NULL REFERENCES products(id),
  payload     JSONB NOT NULL DEFAULT '{}',
  escalation  BOOLEAN NOT NULL DEFAULT FALSE,
  used        BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL
);

-- Idle re-engagement rules (the agent's inactivity ladder) -------------
-- "Player quiet N days -> Nika writes first", edited in the admin
-- Retention -> Idle pings tab and swept by retention_idle.py from the
-- agent worker loop (shared guards/ledger). Table originally shipped with
-- the v1 ping matrix; the schema is unchanged, so historical rows and
-- retention_pings.rule_id references keep working.
CREATE TABLE IF NOT EXISTS retention_rules (
  id               BIGSERIAL PRIMARY KEY,
  product_id       INT NOT NULL REFERENCES products(id),
  name             TEXT NOT NULL,
  enabled          BOOLEAN NOT NULL DEFAULT TRUE,
  -- what idleness triggers the rule:
  --   'bot_inactivity'    days since the player last wrote to the bot
  --   'casino_inactivity' days since last_login_at/last_played_at (casino feed)
  --   'no_deposit'        days since last_deposit_at (casino feed)
  trigger_kind     TEXT NOT NULL DEFAULT 'bot_inactivity',
  inactivity_days  INT NOT NULL DEFAULT 7,
  action           TEXT NOT NULL DEFAULT 'message',  -- 'message' | 'photo'
  -- English hint the model receives ("miss them, suggest the weekly slots
  -- tournament", ...); free text, rendered into the ping prompt.
  intent           TEXT NOT NULL DEFAULT '',
  -- limit the rule to specific VIP tiers (lowercased names); [] = all tiers.
  vip_tiers        JSONB NOT NULL DEFAULT '[]',
  cooldown_days    INT NOT NULL DEFAULT 14,  -- per player per rule
  priority         INT NOT NULL DEFAULT 0,   -- higher wins when several match
  updated_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ledger of proactive sends: one row per attempted outbound message, so the
-- agent worker can enforce per-player caps/gaps and the analytics can audit
-- what was sent to whom (and what it cost). rule_id is legacy-v1 history.
CREATE TABLE IF NOT EXISTS retention_pings (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  retention_user_id BIGINT NOT NULL REFERENCES retention_users(id),
  rule_id           BIGINT REFERENCES retention_rules(id),
  action            TEXT NOT NULL,             -- 'message' | 'photo'
  -- 'sent' | 'failed' | 'skipped' | 'dry_run'. 'dry_run' is a ping that WOULD
  -- have gone out (shadow mode): it counts for the pacing memories below, so a
  -- dry rule paces exactly like a live one, but never for delivery metrics.
  status            TEXT NOT NULL,
  detail            TEXT,
  cost_usd          NUMERIC(12, 6),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RETENTION AGENT (event-driven) --------------------------------------
-- Canonical casino events (the EPIC-1 taxonomy) pushed by the partner event
-- webhook or the admin simulator. Append-only; idempotent by
-- (product_id, event_id). The same log also feeds the deterministic state
-- resolver (loss window, activity), so rows stay after processing.
--
-- QUEUE LIFECYCLE. `status` is the real queue state; `processed_at` records
-- when the pipeline finished with the row (and is what pruning ages on) — it
-- is NOT the claim marker any more. A claim LEASES the row
-- (status='processing' + locked_until) instead of stamping it processed, so a
-- crash, a deploy or an exception mid-pipeline returns the event to the queue
-- instead of losing it silently. `attempts` + `next_attempt_at` drive the
-- retry backoff; `status='dead'` is the dead-letter terminal after
-- `retention.event_max_attempts`. `priority` is assigned at ingest from
-- player_sync.EVENT_PRIORITY (1 = transactional … 5 = state food) so the claim
-- can serve lanes without a join, and so a backlog can be degraded by lane.
CREATE TABLE IF NOT EXISTS retention_events (
  id              BIGSERIAL PRIMARY KEY,
  product_id      INT NOT NULL REFERENCES products(id),
  event_id        TEXT NOT NULL,             -- partner's idempotency key (ULID)
  event_name      TEXT NOT NULL,             -- canonical name (deposit_confirmed, ...)
  event_version   TEXT NOT NULL DEFAULT '1.0',
  player_id       TEXT NOT NULL,
  ts              TIMESTAMPTZ NOT NULL,      -- when it happened at the casino
  payload         JSONB NOT NULL DEFAULT '{}',
  source          TEXT NOT NULL DEFAULT 'webhook',  -- 'webhook' | 'simulator'
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|done|dead
  attempts        INT NOT NULL DEFAULT 0,
  locked_until    TIMESTAMPTZ,               -- lease expiry while 'processing'
  next_attempt_at TIMESTAMPTZ,               -- retry backoff gate
  priority        SMALLINT NOT NULL DEFAULT 3,
  last_error      TEXT,
  worker_id       TEXT,                      -- which worker holds/held the lease
  processed_at    TIMESTAMPTZ,               -- when the pipeline finished with it
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, event_id)
);

-- The v2 decision ledger: ONE row per agent decision (event-triggered or
-- sweep), whatever the outcome — including 'silence' and guard blocks — so
-- "why did/didn't the bot write?" is always answerable from one row
-- (state snapshot + guard verdict + the agent's decision + cost + delivery).
CREATE TABLE IF NOT EXISTS retention_v2_decisions (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  retention_user_id BIGINT REFERENCES retention_users(id),
  player_id         TEXT,
  trigger_kind      TEXT NOT NULL,           -- 'event' | 'manual'
  event_pk          BIGINT REFERENCES retention_events(id),
  event_name        TEXT,
  state             JSONB NOT NULL DEFAULT '{}',  -- resolved state snapshot
  guard             JSONB NOT NULL DEFAULT '{}',  -- guard verdict + reasons
  action            TEXT NOT NULL,           -- 'message'|'photo'|'silence'|'blocked'|'skipped'
  intent            TEXT,                    -- the agent's brief for the text generator
  tone              TEXT,                    -- 'warm'|'celebrate'|'comfort'|'neutral'
  reason            TEXT,                    -- the agent's (or guard's) why
  dry_run           BOOLEAN NOT NULL DEFAULT FALSE,
  delivered         BOOLEAN NOT NULL DEFAULT FALSE,
  detail            TEXT,
  cost_usd          NUMERIC(12, 6),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OUTCOME ATTRIBUTION -------------------------------------------------
-- ONE row per TOUCH the bot delivered: a proactive agent/idle message, a
-- delivered photo/video, a message carrying a site-map CTA button. The
-- dimensions (what kind of touch it was) are denormalized onto the row on
-- purpose: retention_events are pruned and a photo may be deleted, so an
-- attribution recomputed later would silently change — the row must stay
-- readable on its own. The outcome fields are filled by the attribution sweep
-- (retention/outcomes.py) once the windows elapse; `closed` marks a row the
-- sweep is done with.
CREATE TABLE IF NOT EXISTS retention_outcomes (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  retention_user_id BIGINT REFERENCES retention_users(id),
  player_id         TEXT,
  session_id        UUID,
  kind              TEXT NOT NULL,           -- 'proactive' | 'dialogue'
  -- dimensions (NULL when not applicable)
  -- ON DELETE SET NULL: the admin may clear the decision ledger for
  -- live-testing cleanup, and that must neither fail on this FK nor erase the
  -- record that a real message reached a real player (the same "detach, keep
  -- the history" rule the event log's links follow).
  decision_id       BIGINT REFERENCES retention_v2_decisions(id) ON DELETE SET NULL,
  rule_id           BIGINT,                  -- idle rung (retention_rules.id)
  event_name        TEXT,                    -- casino event / 'idle:<kind>'
  action            TEXT,                    -- 'message' | 'photo'
  tone              TEXT,
  photo_id          BIGINT,                  -- media delivered with this touch
  media_type        TEXT,                    -- 'photo' | 'video'
  link_url          TEXT,                    -- site-map CTA button attached
  cost_usd          NUMERIC(12, 6),          -- model cost of the touch
  sent_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- outcome (attribution sweep)
  replied_at        TIMESTAMPTZ,
  reply_latency_sec INT,
  player_msgs       INT NOT NULL DEFAULT 0,  -- player turns inside the window
  returned_at       TIMESTAMPTZ,             -- any casino activity after the touch
  deposit_at        TIMESTAMPTZ,
  closed            BOOLEAN NOT NULL DEFAULT FALSE,
  attributed_at     TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_retention_outcomes_product
  ON retention_outcomes(product_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_retention_outcomes_open
  ON retention_outcomes(product_id, sent_at) WHERE NOT closed;
CREATE INDEX IF NOT EXISTS idx_retention_outcomes_player
  ON retention_outcomes(product_id, player_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_retention_outcomes_photo
  ON retention_outcomes(photo_id) WHERE photo_id IS NOT NULL;

-- MEASUREMENT (holdout / uplift) --------------------------------------
-- Holdout experiment config per product (history kept append-style; the
-- ACTIVE row is the newest with active=TRUE). The hot settings keys
-- (retention.holdout_pct/holdout_salt) mirror the active row for the guard's
-- hot path; PUT /admin/retention/holdout/config writes both in one call.
CREATE TABLE IF NOT EXISTS retention_holdout_config (
  id           BIGSERIAL PRIMARY KEY,
  product_id   INT NOT NULL REFERENCES products(id),
  holdout_pct  INT NOT NULL DEFAULT 0,          -- 0..50; 0 = off
  holdout_salt TEXT NOT NULL DEFAULT 'default', -- rotation = new experiment
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  note         TEXT,
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_retention_holdout_product
  ON retention_holdout_config(product_id, id DESC);

-- Materialized uplift slices for the dashboard (a projection, not the source
-- of truth — that is retention_outcomes + the holdout labels).
CREATE TABLE IF NOT EXISTS retention_uplift_snapshots (
  id                    BIGSERIAL PRIMARY KEY,
  product_id            INT NOT NULL REFERENCES products(id),
  window_from           TIMESTAMPTZ NOT NULL,
  window_to             TIMESTAMPTZ NOT NULL,
  conversion_type       TEXT NOT NULL,          -- 'reply'|'return'|'deposit'
  treatment_players     INT NOT NULL DEFAULT 0,
  holdout_players       INT NOT NULL DEFAULT 0,
  treatment_conversions INT NOT NULL DEFAULT 0,
  holdout_conversions   INT NOT NULL DEFAULT 0,
  treatment_rate        NUMERIC,
  holdout_rate          NUMERIC,
  uplift_pp             NUMERIC,
  computed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_retention_uplift_product
  ON retention_uplift_snapshots(product_id, computed_at);

-- RG GUARD (responsible gaming) ---------------------------------------
-- Append-only compliance audit: one row per RG evaluation (including 'pass').
-- NEVER pruned (5+ years licensing retention); read access is gated to
-- global-admin (the MVP compliance role).
CREATE TABLE IF NOT EXISTS rg_guard_audit (
  id                 BIGSERIAL PRIMARY KEY,
  product_id         INT NOT NULL REFERENCES products(id),
  player_id          TEXT NOT NULL,
  trigger            TEXT NOT NULL,   -- 'event_reaction'|'idle_ping'|'offer_grant'|'journey_step'|'dialogue'
  decision           TEXT NOT NULL,   -- 'pass'|'block_conditional'|'block_permanent'
  reason             TEXT NOT NULL,
  signals_triggered  JSONB NOT NULL DEFAULT '[]',
  rg_status_snapshot TEXT,
  allow_general      BOOLEAN NOT NULL DEFAULT TRUE,
  decided_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rg_guard_audit_product
  ON rg_guard_audit(product_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_rg_guard_audit_player
  ON rg_guard_audit(product_id, player_id, decided_at);

-- Config-driven behavioral signal thresholds (hot, compliance-logged).
CREATE TABLE IF NOT EXISTS rg_signal_config (
  id          BIGSERIAL PRIMARY KEY,
  product_id  INT NOT NULL REFERENCES products(id),
  signal_key  TEXT NOT NULL,           -- 'chase_pattern'|'deposit_frequency_spike'|...
  enabled     BOOLEAN NOT NULL DEFAULT FALSE,
  block_class TEXT NOT NULL DEFAULT 'conditional',  -- 'permanent'|'conditional'
  params      JSONB NOT NULL DEFAULT '{}',
  source      TEXT NOT NULL DEFAULT 'computed',     -- 'computed'|'casino_flag'
  updated_by  TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, signal_key)
);

-- ADAPTIVE FREQUENCY + SMART SEND TIME --------------------------------
-- Caps per product x channel x cohort. The telegram/mass row defaults to the
-- legacy static guards (ping_daily_cap / min-gap-as-burst); email rides its
-- OWN row and does NOT consume the intrusive-touch budget (business decision:
-- intrusive touches = push + Telegram pings, email is counted separately).
CREATE TABLE IF NOT EXISTS retention_frequency_caps (
  id             BIGSERIAL PRIMARY KEY,
  product_id     INT NOT NULL REFERENCES products(id),
  channel        TEXT NOT NULL DEFAULT 'telegram',
  cohort         TEXT NOT NULL DEFAULT 'mass',   -- 'mass'|'vip'|'vip_plus'|'dormant'|...
  per_day        INT NOT NULL DEFAULT 3,
  per_week       INT NOT NULL DEFAULT 10,
  burst_per_hour INT NOT NULL DEFAULT 1,
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  updated_by     TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, channel, cohort)
);

-- Touch type -> priority (P1 critical .. P5 discovery). P1/P2 are never cut
-- by a frequency cap.
CREATE TABLE IF NOT EXISTS retention_touch_priority (
  id                    BIGSERIAL PRIMARY KEY,
  product_id            INT NOT NULL REFERENCES products(id),
  touch_type            TEXT NOT NULL,   -- 'rg_warning'|'loss_rescue'|'level_up'|'event_reaction'|'idle_ping'|'reload_offer'|...
  priority              INT NOT NULL DEFAULT 3,
  channel_switch_on_cap BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by            TEXT,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, touch_type)
);

-- Deterministic per-player activity profile (Smart Send Time input),
-- recomputed by the sweep; the hot path only reads it.
CREATE TABLE IF NOT EXISTS retention_activity_profile (
  product_id         INT NOT NULL REFERENCES products(id),
  player_id          TEXT NOT NULL,
  median_active_hour INT,
  peak_hours         JSONB NOT NULL DEFAULT '[]',
  dow_pattern        JSONB NOT NULL DEFAULT '{}',
  response_by_hour   JSONB NOT NULL DEFAULT '{}',
  sample_size        INT NOT NULL DEFAULT 0,
  computed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (product_id, player_id)
);

-- PLAYER SCORING (dormancy cohorts / RFM / value tiers) ----------------
-- Cohort transition log (analytics + the one-transition-per-day dedup).
CREATE TABLE IF NOT EXISTS retention_cohort_transitions (
  id              BIGSERIAL PRIMARY KEY,
  product_id      INT NOT NULL REFERENCES products(id),
  player_id       TEXT NOT NULL,
  from_cohort     TEXT NOT NULL,
  to_cohort       TEXT NOT NULL,
  days_inactive   INT NOT NULL DEFAULT 0,
  transitioned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_retention_cohort_transition_dedup
  ON retention_cohort_transitions(product_id, player_id, to_cohort,
                                  ((transitioned_at at time zone 'utc')::date));
CREATE INDEX IF NOT EXISTS idx_retention_cohort_transitions_product
  ON retention_cohort_transitions(product_id, transitioned_at);

-- Config-driven scoring thresholds (dormancy boundaries, value tiers, RFM
-- windows) — one row per config key per product.
CREATE TABLE IF NOT EXISTS retention_scoring_config (
  id         BIGSERIAL PRIMARY KEY,
  product_id INT NOT NULL REFERENCES products(id),
  config_key TEXT NOT NULL,   -- 'dormancy_boundaries'|'value_tiers'|'vip_mapping'
  params     JSONB NOT NULL DEFAULT '{}',
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, config_key)
);

-- OFFER ENGINE --------------------------------------------------------
-- Offer catalog: the bonus-CMS-ID model — the casino's Bonus Engine owns the
-- bonus mechanics; our catalog row references the CMS bonus by its ID and
-- carries only what the orchestrator needs (cost estimate for the budget
-- guard, eligibility, enablement).
CREATE TABLE IF NOT EXISTS retention_offer_catalog (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  offer_key         TEXT NOT NULL,     -- stable key: 'loss_cashback'|'reload_bonus'|...
  offer_type        TEXT NOT NULL DEFAULT 'bonus',  -- 'cashback'|'reload'|'free_spins'|'bonus'
  partner_bonus_id  TEXT,              -- the bonus ID in the casino's bonus CMS
  params            JSONB NOT NULL DEFAULT '{}',
  cost_estimate_usd NUMERIC(12, 2) NOT NULL DEFAULT 0,
  enabled           BOOLEAN NOT NULL DEFAULT FALSE,
  min_deposit_usd   NUMERIC(12, 2),
  allowed_countries JSONB NOT NULL DEFAULT '[]',
  description       TEXT NOT NULL DEFAULT '',
  updated_by        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, offer_key)
);

-- Deterministic trigger -> offer mapping (the MVP direct path; journeys
-- take over cohort-driven granting once active — mutually exclusive by
-- enabled flags).
CREATE TABLE IF NOT EXISTS retention_offer_triggers (
  id           BIGSERIAL PRIMARY KEY,
  product_id   INT NOT NULL REFERENCES products(id),
  trigger_key  TEXT NOT NULL,   -- 'loss_mid'|'loss_high'|'idle_d10'|'idle_d14'|'ftd_d1'
  offer_key    TEXT NOT NULL,
  vip_suppress BOOLEAN NOT NULL DEFAULT TRUE,
  enabled      BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, trigger_key)
);

-- Grant ledger: one row per offer grant attempt, idempotent by
-- (product_id, offer_grant_id).
CREATE TABLE IF NOT EXISTS retention_offer_grants (
  id               BIGSERIAL PRIMARY KEY,
  product_id       INT NOT NULL REFERENCES products(id),
  player_id        TEXT NOT NULL,
  offer_grant_id   TEXT NOT NULL,
  decision_id      BIGINT REFERENCES retention_v2_decisions(id) ON DELETE SET NULL,
  offer_key        TEXT NOT NULL,
  offer_type       TEXT NOT NULL DEFAULT 'bonus',
  partner_bonus_id TEXT,
  params_snapshot  JSONB NOT NULL DEFAULT '{}',
  cost_usd         NUMERIC(12, 2) NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'pending', -- 'pending'|'granted'|'fraud_hold'|'failed'|'dry_run'
  partner_ref      TEXT,
  detail           TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at       TIMESTAMPTZ,
  UNIQUE (product_id, offer_grant_id)
);
CREATE INDEX IF NOT EXISTS idx_retention_offer_grants_product
  ON retention_offer_grants(product_id, created_at);

-- Per-player offer anti-abuse state (separate from message cooldowns).
CREATE TABLE IF NOT EXISTS retention_offer_cooldowns (
  product_id           INT NOT NULL REFERENCES products(id),
  player_id            TEXT NOT NULL,
  last_offer_at        TIMESTAMPTZ,
  offers_granted_total INT NOT NULL DEFAULT 0,
  PRIMARY KEY (product_id, player_id)
);

-- JOURNEY ENGINE ------------------------------------------------------
-- Journey definitions as data (declarative trigger + conditions + steps).
CREATE TABLE IF NOT EXISTS retention_journeys (
  id               BIGSERIAL PRIMARY KEY,
  product_id       INT NOT NULL REFERENCES products(id),
  journey_key      TEXT NOT NULL,
  name             TEXT NOT NULL,
  version          INT NOT NULL DEFAULT 1,
  status           TEXT NOT NULL DEFAULT 'draft',   -- 'draft'|'active'|'paused'
  trigger          JSONB NOT NULL DEFAULT '{}',     -- {type:'event'|'scheduled', ...}
  entry_conditions JSONB NOT NULL DEFAULT '[]',
  exit_conditions  JSONB NOT NULL DEFAULT '[]',
  steps            JSONB NOT NULL DEFAULT '[]',
  dry_run          BOOLEAN NOT NULL DEFAULT TRUE,
  priority         INT NOT NULL DEFAULT 3,
  metadata         JSONB NOT NULL DEFAULT '{}',
  is_starter       BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by       TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, journey_key, version)
);

-- Active/finished journey passes per player.
CREATE TABLE IF NOT EXISTS retention_journey_enrollments (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  player_id         TEXT NOT NULL,
  retention_user_id BIGINT REFERENCES retention_users(id),
  journey_key       TEXT NOT NULL,
  journey_version   INT NOT NULL DEFAULT 1,
  current_step      INT NOT NULL DEFAULT 0,
  status            TEXT NOT NULL DEFAULT 'active', -- 'active'|'completed'|'exited_goal'|'exited_return'|'exited_terminal'|'skipped_exit'
  enrolled_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  next_step_at      TIMESTAMPTZ,
  exit_reason       TEXT,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_journey_enrollment_active
  ON retention_journey_enrollments(product_id, player_id, journey_key)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_journey_enrollments_due
  ON retention_journey_enrollments(product_id, next_step_at)
  WHERE status = 'active';

-- Step execution audit.
CREATE TABLE IF NOT EXISTS retention_journey_steps_log (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT NOT NULL REFERENCES products(id),
  enrollment_id BIGINT NOT NULL REFERENCES retention_journey_enrollments(id),
  step_id       INT NOT NULL,
  outcome       TEXT NOT NULL,  -- 'sent'|'offer_granted'|'skipped_condition'|'blocked_guard'|'deferred_frequency'|'channel_unavailable'|'dry_run'|'waited'
  decision_id   BIGINT REFERENCES retention_v2_decisions(id) ON DELETE SET NULL,
  detail        TEXT,
  executed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_journey_steps_log_enrollment
  ON retention_journey_steps_log(enrollment_id, executed_at);

-- TEMPLATE LIBRARY ----------------------------------------------------
-- Structured briefs (persona_brief mode: the persona writes the text) or
-- verbatim copy, referenced from journey steps by template_key.
CREATE TABLE IF NOT EXISTS retention_templates (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT NOT NULL REFERENCES products(id),
  template_key  TEXT NOT NULL,
  type          TEXT NOT NULL DEFAULT 'telegram', -- channel metadata: 'telegram'|'push'|'email'|'in_app'
  version       INT NOT NULL DEFAULT 1,
  status        TEXT NOT NULL DEFAULT 'draft',    -- 'draft'|'active'
  mode          TEXT NOT NULL DEFAULT 'persona_brief',  -- 'persona_brief'|'verbatim'
  intent        TEXT NOT NULL DEFAULT '',         -- model-facing English brief
  localizations JSONB NOT NULL DEFAULT '{}',      -- {lang: {title, body, cta}}
  variables     JSONB NOT NULL DEFAULT '{}',
  ab_variants   JSONB,
  is_starter    BOOLEAN NOT NULL DEFAULT FALSE,
  updated_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, template_key, version)
);

-- CHANNEL ABSTRACTION -------------------------------------------------
-- Per-product channel enablement + routing priority. Secrets (email API key,
-- delivery endpoint key) live in the encrypted product secrets, not here.
CREATE TABLE IF NOT EXISTS retention_channel_config (
  id         BIGSERIAL PRIMARY KEY,
  product_id INT NOT NULL REFERENCES products(id),
  channel    TEXT NOT NULL,    -- 'telegram'|'email'|'push'|'in_app'|'vip_host'
  enabled    BOOLEAN NOT NULL DEFAULT FALSE,
  priority   INT NOT NULL DEFAULT 100,
  config     JSONB NOT NULL DEFAULT '{}',
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, channel)
);

-- Unified delivery tracking across channels (lifecycle + retry state).
-- Also the SEND QUEUE: a decision never sends, it enqueues a row here and the
-- send worker claims it under the same lease discipline as retention_events
-- (status 'sending' + locked_until). `payload` carries everything the channel
-- adapter needs (text, media id, keyboard, silent flag), `scheduled_at`
-- absorbs the humanizing send delay and Smart Send Time, and `priority`
-- (inherited from the triggering event) is the lane a burst is drained in.
CREATE TABLE IF NOT EXISTS retention_deliveries (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  player_id         TEXT,
  retention_user_id BIGINT REFERENCES retention_users(id),
  delivery_id       TEXT NOT NULL,
  decision_id       BIGINT REFERENCES retention_v2_decisions(id) ON DELETE SET NULL,
  channel           TEXT NOT NULL,
  intended_channel  TEXT,
  status            TEXT NOT NULL DEFAULT 'queued', -- queued|sending|sent|delivered|opened|clicked|bounced|failed|suppressed|undeliverable|routed_host
  attempts          INT NOT NULL DEFAULT 0,
  next_attempt_at   TIMESTAMPTZ,
  provider_ref      TEXT,
  fail_reason       TEXT,
  permanent_fail    BOOLEAN NOT NULL DEFAULT FALSE,
  title             TEXT,
  body              TEXT,
  cta_url           TEXT,
  priority          SMALLINT NOT NULL DEFAULT 3,
  scheduled_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_until      TIMESTAMPTZ,
  worker_id         TEXT,
  payload           JSONB NOT NULL DEFAULT '{}',
  outcome_id        BIGINT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, delivery_id)
);
CREATE INDEX IF NOT EXISTS idx_retention_deliveries_product
  ON retention_deliveries(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retention_deliveries_retry
  ON retention_deliveries(product_id, next_attempt_at)
  WHERE status = 'failed' AND NOT permanent_fail;

-- BACKGROUND JOB PACING ------------------------------------------------
-- The maintenance sweeps (idle ladder, attribution, scoring, activity
-- profiles, journeys) used to pace themselves on an in-process dict, which
-- neither survives a restart nor holds across instances. This table is the
-- shared "is it my turn" clock: db.claim_worker_job flips next_run_at
-- atomically, so exactly one worker runs a given (product, job) per interval.
-- It doubles as the admin's background-jobs health view.
CREATE TABLE IF NOT EXISTS retention_worker_jobs (
  product_id   INT NOT NULL REFERENCES products(id),
  job          TEXT NOT NULL,
  last_run_at  TIMESTAMPTZ,
  next_run_at  TIMESTAMPTZ,
  last_status  TEXT,
  last_error   TEXT,
  duration_ms  INT,
  PRIMARY KEY (product_id, job)
);

-- OUTBOUND RATE SHAPING ------------------------------------------------
-- A token bucket per send scope ('tg:<product>', 'tg:chat:<chat>',
-- 'email:<product>'), refilled lazily inside one atomic UPDATE. Telegram
-- allows ~30 msg/s per bot and ~1/s per chat; a 10k broadcast without this
-- collects 429s instead of delivering. Shared state on purpose — it must hold
-- across worker instances, which an in-process limiter cannot.
CREATE TABLE IF NOT EXISTS retention_rate_budget (
  scope        TEXT PRIMARY KEY,
  tokens       DOUBLE PRECISION NOT NULL,
  rate_per_sec DOUBLE PRECISION NOT NULL,
  burst        DOUBLE PRECISION NOT NULL,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- VIP-host task queue (a ROUTE, not a send: a human handles it).
CREATE TABLE IF NOT EXISTS retention_host_tasks (
  id         BIGSERIAL PRIMARY KEY,
  product_id INT NOT NULL REFERENCES products(id),
  player_id  TEXT NOT NULL,
  reason     TEXT NOT NULL,
  context    JSONB NOT NULL DEFAULT '{}',
  status     TEXT NOT NULL DEFAULT 'open',  -- 'open'|'claimed'|'done'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_retention_host_tasks_product
  ON retention_host_tasks(product_id, status, created_at);

-- INTEGRATION CHECKLIST -----------------------------------------------
-- The living list of external integration dependencies (what we still need
-- from the casino platform / partner teams to wire everything up). Global
-- (deploy-level), edited from the admin System section; seeded with the
-- known items on boot (insert-only, never overwrites operator edits).
CREATE TABLE IF NOT EXISTS integration_checklist (
  id          BIGSERIAL PRIMARY KEY,
  item_key    TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  owner       TEXT NOT NULL DEFAULT '',       -- who provides it (e.g. 'casino platform')
  status      TEXT NOT NULL DEFAULT 'waiting',-- 'waiting'|'in_progress'|'received'|'wired'|'not_needed'
  blocking    TEXT NOT NULL DEFAULT '',       -- what it blocks on our side
  notes       TEXT NOT NULL DEFAULT '',
  updated_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- QUALITY REVIEW ------------------------------------------------------
-- The LLM-as-judge verdict on ONE finished conversation (support widget or
-- Telegram). One row per review; a conversation is re-reviewed only when it
-- grew since (reviewed_msg_count), so the pass is idempotent per state.
CREATE TABLE IF NOT EXISTS conversation_reviews (
  id                BIGSERIAL PRIMARY KEY,
  product_id        INT NOT NULL REFERENCES products(id),
  session_id        UUID NOT NULL,
  consumer          TEXT NOT NULL DEFAULT 'web',   -- 'web' | 'telegram'
  score             INT,                      -- 1..5 overall quality
  tags              JSONB NOT NULL DEFAULT '[]',   -- taxonomy slugs
  summary           TEXT NOT NULL DEFAULT '',
  issues            JSONB NOT NULL DEFAULT '[]',   -- [{quote, why}]
  kb_gaps           JSONB NOT NULL DEFAULT '[]',   -- player questions the KB missed
  reviewed_msg_count INT NOT NULL DEFAULT 0,
  model             TEXT,
  cost_usd          NUMERIC(12, 6),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversation_reviews_product
  ON conversation_reviews(product_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_reviews_session
  ON conversation_reviews(session_id);

-- MACHINE ADMIN CREDENTIALS -------------------------------------------
-- Service API keys for the /admin API (an external master admin panel, a
-- partner backend). Bearer 'sak_...' tokens; only the SHA-256 hash is stored.
-- Scoped exactly like admin_memberships: one role at one scope per key.
CREATE TABLE IF NOT EXISTS admin_api_keys (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  token_hash   TEXT NOT NULL UNIQUE,
  token_hint   TEXT NOT NULL DEFAULT '',     -- last 4 chars, for display only
  role         TEXT NOT NULL DEFAULT 'manager',  -- 'admin' | 'manager'
  scope_type   TEXT NOT NULL DEFAULT 'global',   -- 'global'|'partner'|'product'
  partner_id   INT REFERENCES partners(id),
  product_id   INT REFERENCES products(id),
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_kb_variables_updated ON kb_variables(updated_at);
CREATE INDEX IF NOT EXISTS idx_admin_events_session ON admin_events(session_id);
CREATE INDEX IF NOT EXISTS idx_admin_events_type ON admin_events(type, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_created ON chat_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_logs_created ON ai_interaction_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_logs_session ON ai_interaction_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_products_partner ON products(partner_id);
CREATE INDEX IF NOT EXISTS idx_admin_memberships_email ON admin_memberships(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_retention_users_product_tg
  ON retention_users(product_id, tg_user_id);
CREATE INDEX IF NOT EXISTS idx_retention_kb_product
  ON retention_kb(product_id, sort_order) WHERE active;
CREATE INDEX IF NOT EXISTS idx_retention_photos_product
  ON retention_photos(product_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_retention_photo_views_user
  ON retention_photo_views(retention_user_id, photo_id);
CREATE INDEX IF NOT EXISTS idx_retention_managers_product
  ON retention_managers(product_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_retention_rules_product
  ON retention_rules(product_id, priority) WHERE enabled;
CREATE INDEX IF NOT EXISTS idx_retention_pings_product
  ON retention_pings(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retention_pings_user
  ON retention_pings(retention_user_id, rule_id, created_at);
CREATE INDEX IF NOT EXISTS idx_retention_nonces_expires
  ON retention_nonces(expires_at);
-- NB: the queue indexes over the lifecycle columns live in _ensure_columns —
-- they must run AFTER the ADD COLUMN guards, or a legacy database (whose
-- retention_events has no `status` yet) fails at boot.
CREATE INDEX IF NOT EXISTS idx_retention_events_player
  ON retention_events(product_id, player_id, ts);
CREATE INDEX IF NOT EXISTS idx_retention_v2_decisions_product
  ON retention_v2_decisions(product_id, created_at);

-- RUNTIME LOG MIRROR ---------------------------------------------------
-- Recent application log records (the "Railway logs"), captured in-process by
-- logcapture.py and batch-flushed here so the admin panel can show them without
-- leaving for Railway. Bounded: db.prune_app_logs keeps only the newest N rows.
CREATE TABLE IF NOT EXISTS app_logs (
  id          BIGSERIAL PRIMARY KEY,
  level       TEXT NOT NULL,            -- DEBUG|INFO|WARNING|ERROR|CRITICAL
  logger      TEXT NOT NULL DEFAULT '',
  message     TEXT NOT NULL DEFAULT '',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_logs_id ON app_logs(id);
CREATE INDEX IF NOT EXISTS idx_app_logs_level ON app_logs(level, id);

-- Per-admin "last read" marker for the runtime-log unread badge: unread =
-- warnings/errors with id greater than this reader's last_read_id.
CREATE TABLE IF NOT EXISTS app_log_reads (
  reader        TEXT PRIMARY KEY,       -- admin account email
  last_read_id  BIGINT NOT NULL DEFAULT 0,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ADMIN ACTION AUDIT ---------------------------------------------------
-- One row per successful mutating /admin/* request: who (actor + their role
-- over the affected scope), what (method + path + a friendly action label),
-- where (product/partner, NULL for hub-global actions), when. Written by the
-- audit middleware in main.py. Scope-tiered visibility is applied at read time
-- (db.list_audit): you see actions on products within your reach, and managers
-- see only manager-authored actions while admins see everything in reach.
CREATE TABLE IF NOT EXISTS admin_audit_log (
  id           BIGSERIAL PRIMARY KEY,
  actor_email  TEXT NOT NULL,
  actor_role   TEXT,                    -- 'admin'|'manager' over the affected scope
  method       TEXT NOT NULL,
  path         TEXT NOT NULL,
  action       TEXT,                    -- friendly label ("Updated settings", …)
  product_id   INT REFERENCES products(id),
  partner_id   INT REFERENCES partners(id),
  status       INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_log(id);
CREATE INDEX IF NOT EXISTS idx_audit_product ON admin_audit_log(product_id, id);
"""
# NB: indexes over the product_id columns of PRE-TENANCY tables live in
# _ensure_columns — they must run AFTER the ADD COLUMN guards (_SCHEMA runs
# first and would fail on a legacy database that lacks the columns).


async def _ensure_columns(conn: asyncpg.Connection) -> None:
    """Explicit ALTER guards for columns added after a table first shipped.

    `CREATE TABLE IF NOT EXISTS` never alters an existing table, so any new
    column on an already-deployed table must be added here. Each statement is
    idempotent via `ADD COLUMN IF NOT EXISTS`. """
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
        # Telegram chat lifecycle: an idle retention conversation is closed and
        # a FRESH session is created for the player's next message; the new
        # session points at the closed one so the first prompt can carry a short
        # continuity tail (returning-player greeting). NULL everywhere else.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "prev_session_id UUID REFERENCES chat_sessions(id)",
        # The validated site-map CTA button attached to an assistant message
        # ([[LINK:url]]). Buttons are chrome, not text, so without this column
        # the model can't see WHICH page it already linked — and keeps
        # attaching the same one on every play nudge (the rotation bug).
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
        "link_url TEXT",
        # Video delivery attributes probed at normalization time (ffprobe) and
        # passed explicitly to sendVideo: without them Telegram may fail to
        # detect the geometry/duration and deliver the message as a
        # download-first file with a squished 00:00 bubble. NULL for photos
        # and for videos normalized before this shipped (the sweep backfills).
        "ALTER TABLE retention_photos ADD COLUMN IF NOT EXISTS "
        "tg_width INT",
        "ALTER TABLE retention_photos ADD COLUMN IF NOT EXISTS "
        "tg_height INT",
        "ALTER TABLE retention_photos ADD COLUMN IF NOT EXISTS "
        "tg_duration_sec INT",
        # Removed feature: system-prompt versioning + A/B. The prompt is now
        # sourced solely from prompts.py (the file is the single source of truth),
        # so drop the table and the per-session attribution column. Idempotent —
        # a no-op once they're gone.
        "ALTER TABLE chat_sessions DROP COLUMN IF EXISTS prompt_version_id",
        "DROP TABLE IF EXISTS prompt_versions",
        # Named-login accounts (Users tab). The table shipped after the initial
        # baseline, so a database created by an earlier deploy may have an older
        # admin_users (or none of these columns). CREATE TABLE IF NOT EXISTS never
        # alters an existing table, so the create-user path (which RETURNs these
        # columns) 500s with UndefinedColumnError until they are backfilled here.
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS "
        "active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        # --- Multi-tenancy scope columns (partners -> products) -------------
        # Pre-tenancy tables gain a product_id; _migrate_tenancy backfills the
        # NULLs to the boot-seeded default product.
        "ALTER TABLE kb_topics ADD COLUMN IF NOT EXISTS "
        "product_id INT REFERENCES products(id)",
        "ALTER TABLE kb_variables ADD COLUMN IF NOT EXISTS "
        "product_id INT REFERENCES products(id)",
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "product_id INT REFERENCES products(id)",
        "ALTER TABLE ai_interaction_logs ADD COLUMN IF NOT EXISTS "
        "product_id INT",
        # Spend attribution (see the ai_interaction_logs comment in _SCHEMA):
        # which facade the money belongs to and what kind of call it was. Rows
        # written before this shipped stay NULL and are classified at read time,
        # so no backfill scan runs on every boot.
        "ALTER TABLE ai_interaction_logs ADD COLUMN IF NOT EXISTS "
        "consumer TEXT",
        "ALTER TABLE ai_interaction_logs ADD COLUMN IF NOT EXISTS "
        "source TEXT",
        "ALTER TABLE admin_events ADD COLUMN IF NOT EXISTS "
        "product_id INT",
        # Slugs/keys are now unique WITHIN a product, not globally: drop the
        # legacy single-column constraints (no-ops on a fresh database — the
        # new _SCHEMA no longer declares them) and add the composite unique
        # indexes that back the ON CONFLICT upserts.
        "ALTER TABLE kb_topics DROP CONSTRAINT IF EXISTS kb_topics_slug_key",
        "ALTER TABLE kb_variables DROP CONSTRAINT IF EXISTS kb_variables_pkey",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_topics_product_slug "
        "ON kb_topics (product_id, slug)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_variables_product_key "
        "ON kb_variables (product_id, key)",
        # One membership row per (user, exact scope); COALESCE folds the NULL
        # ids so 'global'/'partner'/'product' rows can share one guard index.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_memberships_unique "
        "ON admin_memberships (email, scope_type, COALESCE(partner_id, 0), "
        "COALESCE(product_id, 0))",
        # Dead table cleanup: rate_limit_hits was never read or pruned (its
        # writer was removed) — drop it on already-deployed databases too.
        "DROP TABLE IF EXISTS rate_limit_hits",
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_product "
        "ON chat_sessions(product_id, created_at)",
        # The Telegram conversations list sorts by updated_at — without this
        # partial index Postgres fetches + sorts ALL of the product's Telegram
        # sessions on every page load.
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_tg_updated "
        "ON chat_sessions(product_id, updated_at DESC) "
        "WHERE consumer = 'telegram'",
        "CREATE INDEX IF NOT EXISTS idx_ai_logs_product "
        "ON ai_interaction_logs(product_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_admin_events_product "
        "ON admin_events(product_id, created_at)",
        # --- Retention / Telegram (second facade) ---------------------------
        # Per-product Telegram + player-API config on the product row. The two
        # secret columns (bot token, player-API key) are encrypted at rest via
        # secretbox, exactly like openai_key_*_enc / handshake_secret_enc; the
        # rest are plain config.
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "telegram_bot_token_enc TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "telegram_bot_username TEXT",
        # Per-product webhook routing token (NON-secret, like widget_key): the
        # bot's webhook is registered at /telegram/webhook/{this}, so an incoming
        # update resolves to its product by this column. Minted when the bot token
        # is first saved.
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "telegram_webhook_secret TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_tg_webhook "
        "ON products(telegram_webhook_secret) "
        "WHERE telegram_webhook_secret IS NOT NULL",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "telegram_channel_id TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "telegram_channel_url TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "player_api_url TEXT",
        # The product's public main-site URL (its home page). Used as the
        # "support on the site" hand-off destination in the Telegram bot so a
        # route-out lands on the site itself, not a Telegram/contact link.
        # Public, non-secret, edited in Structure.
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "site_url TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "player_api_key_enc TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "retention_enabled BOOLEAN NOT NULL DEFAULT FALSE",
        # The Telegram user this session belongs to (nullable; the durable
        # tg<->player link lives in retention_users). Only set on telegram
        # sessions; web sessions leave it NULL.
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
        "tg_user_id BIGINT",
        # Proactive-turn context: which trigger/occasion made the retention
        # agent write first. NULL on ordinary turns; feeds the prompt history
        # and the admin transcript (see persist_ping_turn / get_history).
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS "
        "ping_context TEXT",
        # --- Ping matrix (proactive retention) -------------------------------
        # Casino-side activity signals + proactive-ping state on the player row.
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "last_login_at TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "last_played_at TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "last_deposit_at TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "pings_muted BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "unreachable BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "last_ping_at TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "pings_day DATE",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "pings_sent_today INT NOT NULL DEFAULT 0",
        # Retention analytics + the Telegram-session join key on the ping
        # reply-rate metric probe by (product, tg_user).
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_product_tg "
        "ON chat_sessions(product_id, tg_user_id) WHERE tg_user_id IS NOT NULL",
        # Media library: photos + short videos share one catalogue/stream —
        # media_type tells the delivery path which Telegram send to use.
        "ALTER TABLE retention_photos ADD COLUMN IF NOT EXISTS "
        "media_type TEXT NOT NULL DEFAULT 'photo'",
        # --- Per-product Cloudflare Turnstile --------------------------------
        # Each product (domain) runs its own Turnstile widget (INVISIBLE mode):
        # the site key is public config served to the chat widget; the secret is
        # encrypted at rest like every product secret. NULL = fall back to the
        # deploy env pair (TURNSTILE_SITE_KEY/TURNSTILE_SECRET) — default
        # product behaviour.
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "turnstile_site_key TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "turnstile_secret_enc TEXT",
        # The reCAPTCHA -> Turnstile migration: the old Google keys are useless
        # with Cloudflare (nothing to carry over), so the legacy columns are
        # simply dropped on deployments that still have them.
        "ALTER TABLE products DROP COLUMN IF EXISTS recaptcha_site_key",
        "ALTER TABLE products DROP COLUMN IF EXISTS recaptcha_secret_enc",
        # --- Photo view ↔ chat session link ----------------------------------
        # Which chat session a photo was sent in, so the admin transcript can
        # show the delivered image inline (the retention Conversations view).
        # NULL for rows written before this column existed.
        "ALTER TABLE retention_photo_views ADD COLUMN IF NOT EXISTS "
        "session_id UUID",
        "CREATE INDEX IF NOT EXISTS idx_photo_views_session "
        "ON retention_photo_views(session_id) WHERE session_id IS NOT NULL",
        # --- Retention orchestrator (measurement / RG / frequency / scoring /
        # offers / journeys / channels) — columns on pre-existing tables. -----
        # Holdout (measurement): the cached deterministic group + the salt it
        # was computed under (salt rotation invalidates the cache lazily).
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "holdout_group TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "holdout_salt TEXT",
        # RG (responsible gaming) status from the casino + computed block cache.
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_status TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_status_source TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_status_until TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_block_level TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_signals JSONB",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "marketing_consent BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rg_updated_at TIMESTAMPTZ",
        # Player timezone (IANA name or a fixed offset like '+03:00'), fed by
        # the casino (geo-IP detection on their side) via player-update; falls
        # back to the product's quiet_hours_utc_offset when absent.
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "tz TEXT",
        # Scoring cache (dormancy cohort / RFM / value tier / VIP segment) —
        # recomputed by the sweep, read by the hot path.
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "dormancy_cohort TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "dormancy_since TIMESTAMPTZ",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rfm_recency INT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rfm_frequency INT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rfm_monetary NUMERIC(14, 2)",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "rfm_score INT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "value_tier TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "vip_segment TEXT",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "total_deposit_lifetime NUMERIC(14, 2)",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "score_computed_at TIMESTAMPTZ",
        # Cashier abandonment timer (armed on deposit_initiated, cleared on
        # deposit_confirmed).
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "deposit_initiated_at TIMESTAMPTZ",
        # Per-channel opt-in / availability (fed by player-update; strict
        # opt-in — the router never falls back to a non-consented channel).
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "email_opt_in BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "email_verified BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "push_opt_in BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "push_available BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "in_app_available BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "sms_opt_in BOOLEAN",
        "ALTER TABLE retention_users ADD COLUMN IF NOT EXISTS "
        "channel_prefs JSONB",
        # Idle rules: a rule may fire on SEVERAL inactivity dimensions at once
        # (chat / casino / deposit). trigger_kinds is the list; the legacy
        # trigger_kind column stays for stored-data compatibility (rows with a
        # NULL/empty trigger_kinds fall back to it).
        "ALTER TABLE retention_rules ADD COLUMN IF NOT EXISTS "
        "trigger_kinds JSONB",
        # Backfill: existing rules (all shipped with the single
        # trigger_kind='bot_inactivity') switch to the new DEFAULT — a rung
        # watches ALL THREE inactivity dimensions (chat / casino / deposit) at
        # once (owner decision). Only rows that never had trigger_kinds are
        # touched, so an operator's later narrowing is never overwritten.
        "UPDATE retention_rules SET trigger_kinds = "
        "'[\"bot_inactivity\", \"casino_inactivity\", \"no_deposit\"]'::jsonb "
        "WHERE trigger_kinds IS NULL",
        # Outcome rows: the holdout group snapshot at touch time (uplift cuts
        # by the group AT the touch, not the current one). Virtual rows for the
        # control group carry kind='holdout'.
        "ALTER TABLE retention_outcomes ADD COLUMN IF NOT EXISTS "
        "holdout_group_at_time TEXT",
        # Outbound partner endpoints (orchestrator -> casino calls): the bonus
        # grant endpoint (offer engine) and the delegated push/in_app delivery
        # endpoint. Bearer secret = partner_out_key_enc (encrypted).
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "offer_grant_url TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "delivery_endpoint_url TEXT",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "partner_out_key_enc TEXT",
        # Email channel (Customer.io App API) credential, encrypted at rest.
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "email_api_key_enc TEXT",
        # --- EVENT QUEUE LIFECYCLE ------------------------------------------
        # The claim used to stamp processed_at at SELECTION time, so an event
        # whose pipeline then threw stayed marked processed — lost, silently,
        # with no retry and no dead-letter. These columns turn the claim into a
        # LEASE (see the retention_events comment in _SCHEMA).
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "status TEXT NOT NULL DEFAULT 'pending'",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "attempts INT NOT NULL DEFAULT 0",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "locked_until TIMESTAMPTZ",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "next_attempt_at TIMESTAMPTZ",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "priority SMALLINT NOT NULL DEFAULT 3",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "last_error TEXT",
        "ALTER TABLE retention_events ADD COLUMN IF NOT EXISTS "
        "worker_id TEXT",
        # --- SEND QUEUE -----------------------------------------------------
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "priority SMALLINT NOT NULL DEFAULT 3",
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "locked_until TIMESTAMPTZ",
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "worker_id TEXT",
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "payload JSONB NOT NULL DEFAULT '{}'",
        "ALTER TABLE retention_deliveries ADD COLUMN IF NOT EXISTS "
        "outcome_id BIGINT",
        # The old queue index keyed on processed_at IS NULL, which no longer
        # means "queued" — replaced by the status-partial indexes below.
        "DROP INDEX IF EXISTS idx_retention_events_queue",
        # The drain's index. It is also what keeps the boot backfill below
        # cheap on every later start: after the first pass only the genuinely
        # pending rows (the live queue depth) are scanned.
        "CREATE INDEX IF NOT EXISTS idx_retention_events_ready "
        "ON retention_events (product_id, priority, id) "
        "WHERE status = 'pending'",
        "CREATE INDEX IF NOT EXISTS idx_retention_events_lease "
        "ON retention_events (locked_until) WHERE status = 'processing'",
        "CREATE INDEX IF NOT EXISTS idx_retention_events_inflight "
        "ON retention_events (product_id, player_id) "
        "WHERE status = 'processing'",
        "CREATE INDEX IF NOT EXISTS idx_retention_events_dead "
        "ON retention_events (product_id, id DESC) WHERE status = 'dead'",
        "CREATE INDEX IF NOT EXISTS idx_retention_deliveries_ready "
        "ON retention_deliveries (product_id, priority, scheduled_at) "
        "WHERE status IN ('queued', 'failed') AND NOT permanent_fail",
        # The idle sweep's candidate query (eligible_ping_users) was a seq scan
        # + sort over every player of the product, every pass.
        "CREATE INDEX IF NOT EXISTS idx_retention_users_idle_candidates "
        "ON retention_users (product_id, last_active_at ASC) "
        "WHERE subscribed AND NOT pings_muted AND NOT unreachable",
        # At-least-once delivery means an event CAN reach the pipeline twice
        # (lease expiry, reclaim after a crash). This index is the hard
        # guarantee that a retry cannot produce a second message: the decision
        # insert is ON CONFLICT DO NOTHING and a NULL return means "already
        # decided". Wrapped in a subtransaction because a pre-existing
        # duplicate (none can exist by construction, but boot must not be
        # bet on that) would otherwise abort the whole init transaction.
        "DO $$ BEGIN "
        "  BEGIN "
        "    CREATE UNIQUE INDEX IF NOT EXISTS idx_retention_v2_decisions_event "
        "      ON retention_v2_decisions (product_id, event_pk) "
        "      WHERE event_pk IS NOT NULL AND action <> 'skipped'; "
        "  EXCEPTION WHEN unique_violation THEN "
        "    RAISE NOTICE 'retention_v2_decisions: duplicate event decisions "
        "exist, unique index skipped'; "
        "  END; "
        "END $$",
        # NB: the lifecycle BACKFILL of pre-existing rows is deliberately NOT
        # here. init_db runs as one transaction on a POOL connection, so a
        # command_timeout=30 applies to it; an UPDATE over millions of legacy
        # retention_events rows would time out, roll the whole boot back, and
        # do it again on every restart. It runs batched, after boot, from
        # backfill_event_lifecycle() — and the claim does not depend on it
        # having finished (it also tests processed_at IS NULL).
    ]
    for stmt in alters:
        await conn.execute(stmt)


async def _migrate_tenancy(conn: asyncpg.Connection) -> int:
    """Ensure the default partner/product exist and adopt pre-tenancy rows.

    Idempotent, runs on every boot inside the init transaction:
      1. seed the 'default' partner + 'default' product (widget_key generated
         once; ON CONFLICT keeps an existing row untouched);
      2. backfill product_id = <default product> on every pre-tenancy row that
         still has NULL (kb_topics, kb_variables, chat_sessions, and — via the
         session join — ai_interaction_logs / admin_events);
      3. give every legacy admin_users account with NO memberships a GLOBAL
         membership carrying its old role, so nobody is locked out by the
         switch to membership-based authorization.

    Returns the default product id (used by init_db to seed KB variables).
    """
    import secrets as _secrets

    from app.core import tenancy

    await conn.execute(
        "INSERT INTO partners (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO NOTHING",
        tenancy.DEFAULT_PARTNER_SLUG, "Default partner",
    )
    partner_id = await conn.fetchval(
        "SELECT id FROM partners WHERE slug = $1", tenancy.DEFAULT_PARTNER_SLUG
    )
    await conn.execute(
        "INSERT INTO products (partner_id, slug, name, widget_key) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (slug) DO NOTHING",
        partner_id, tenancy.DEFAULT_PRODUCT_SLUG, "Default product",
        "wk_" + _secrets.token_urlsafe(24),
    )
    product_id = await conn.fetchval(
        "SELECT id FROM products WHERE slug = $1", tenancy.DEFAULT_PRODUCT_SLUG
    )
    # Record the default product for sync "is this the default scope?" checks
    # (deploy-level env fallbacks apply to the default product only).
    tenancy.set_default_product_id(product_id)

    await conn.execute(
        "UPDATE kb_topics SET product_id = $1 WHERE product_id IS NULL", product_id
    )
    await conn.execute(
        "UPDATE kb_variables SET product_id = $1 WHERE product_id IS NULL", product_id
    )
    await conn.execute(
        "UPDATE chat_sessions SET product_id = $1 WHERE product_id IS NULL", product_id
    )
    await conn.execute(
        "UPDATE ai_interaction_logs l SET product_id = s.product_id "
        "FROM chat_sessions s "
        "WHERE l.product_id IS NULL AND l.session_id = s.id"
    )
    await conn.execute(
        "UPDATE admin_events e SET product_id = s.product_id "
        "FROM chat_sessions s "
        "WHERE e.product_id IS NULL AND e.session_id = s.id"
    )
    # Adopt legacy PRE-tenancy admin_users into a global membership — but ONLY on
    # the very first boot after tenancy was introduced, detected by an entirely
    # empty memberships table. On every LATER boot a zero-membership account is a
    # DELIBERATE "no access" state (its last membership was revoked, a legitimate
    # state per _can_manage_user), and re-granting it a global membership would
    # silently resurrect — and ESCALATE — access on the next restart, with no
    # audit trail. Once any membership exists the feature is in use, so this
    # backfill must never run again.
    has_memberships = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM admin_memberships)")
    if not has_memberships:
        await conn.execute(
            "INSERT INTO admin_memberships (email, scope_type, role) "
            "SELECT u.email, 'global', u.role FROM admin_users u "
            "WHERE NOT EXISTS "
            "  (SELECT 1 FROM admin_memberships m WHERE m.email = u.email)"
        )
    return product_id


async def _migrate_legacy_contact_url(conn, default_product_id: int) -> None:
    """One-time move of the legacy hidden contact URL into its admin-visible home.

    Early builds edited `general.contact_form_url` from the Settings tab; the
    field later left the UI, but a value already stored in `app_settings` kept
    feeding the escalation contact button — a link the owner could no longer see
    or edit anywhere in the admin ("where is this URL coming from?"). This moves
    that stored value into the DEFAULT product's per-product translations as the
    English `contact_url` (the resolution chain ends at English, so every
    language without its own URL still reaches it, exactly like the old
    fallback) and then deletes the legacy key — so the URL lives in exactly one,
    admin-visible place: the Translations tab. Product-scoped on purpose: a
    global translations override would leak the default brand's link into every
    other product.

    Runs on every boot but is one-time by construction: after the move there is
    no legacy key left, so it no-ops. If ANY contact_url override already exists
    (global or default-product), the owner has already adopted the new home —
    the legacy key is dead weight and is dropped without copying.
    """
    row = await conn.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'general'")
    general = _json_value(row["value"]) if row else None
    if not isinstance(general, dict) or "contact_form_url" not in general:
        return
    url = general.get("contact_form_url")
    url = url.strip() if isinstance(url, str) else ""

    prow = await conn.fetchrow(
        "SELECT value FROM product_settings "
        "WHERE product_id = $1 AND key = 'translations'", default_product_id)
    prod_trans = _json_value(prow["value"]) if prow else None
    prod_trans = prod_trans if isinstance(prod_trans, dict) else {}
    grow = await conn.fetchrow(
        "SELECT value FROM app_settings WHERE key = 'translations'")
    glob_trans = _json_value(grow["value"]) if grow else None
    glob_trans = glob_trans if isinstance(glob_trans, dict) else {}

    def _has_contact_url(trans: dict) -> bool:
        return any(isinstance(v, dict) and str(v.get("contact_url") or "").strip()
                   for v in trans.values())

    if url and not _has_contact_url(prod_trans) and not _has_contact_url(glob_trans):
        en = prod_trans.get("en")
        en = dict(en) if isinstance(en, dict) else {}
        en["contact_url"] = url
        prod_trans["en"] = en
        await conn.execute(
            "INSERT INTO product_settings (product_id, key, value, updated_at, updated_by) "
            "VALUES ($1, 'translations', $2::jsonb, now(), 'migration') "
            "ON CONFLICT (product_id, key) DO UPDATE "
            "  SET value = EXCLUDED.value, updated_at = now(), "
            "      updated_by = EXCLUDED.updated_by",
            default_product_id, json.dumps(prod_trans),
        )

    del general["contact_form_url"]
    await conn.execute(
        "UPDATE app_settings SET value = $1::jsonb, updated_at = now(), "
        "updated_by = 'migration' WHERE key = 'general'",
        json.dumps(general),
    )


async def init_db() -> None:
    """Create the pool, then create tables, run column guards + tenancy adoption."""
    await connect()
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SCHEMA)
            await _ensure_columns(conn)
            default_product_id = await _migrate_tenancy(conn)
            await seed_kb_variables(conn, default_product_id)
            await _migrate_legacy_contact_url(conn, default_product_id)
            await _seed_integration_checklist(conn)


# The known external integration dependencies (what the casino platform /
# partner teams still owe us to wire everything up). Seeded insert-only —
# the operator's status edits and notes are never overwritten on boot.
_INTEGRATION_CHECKLIST_SEED: tuple[dict[str, str], ...] = (
    {"item_key": "bonus_cms_contract",
     "title": "Bonus CMS: grant endpoint + bonus ID list",
     "description": "Endpoint that credits a bonus by its CMS ID to a player "
                    "(our POST payload: offer_grant_id, player_id, bonus_id, "
                    "params). Idempotent by offer_grant_id; statuses granted "
                    "/ fraud_hold / duplicate / failed. Plus the catalogue "
                    "of bonus IDs with descriptions.",
     "owner": "casino platform (Anton)",
     "blocking": "Offer engine production enablement (until then: dry-run)"},
    {"item_key": "rg_status_feed",
     "title": "RG status feed in player-update",
     "description": "Send rg_status (ok/cool_off/rg_hold/self_exclude), "
                    "rg_status_until, marketing_consent and optional "
                    "rg_flags on every change + an initial backfill. The "
                    "platform is the source of truth for self-exclusion.",
     "owner": "casino platform (Anton)",
     "blocking": "Real RG blocks (manual admin marking works meanwhile)"},
    {"item_key": "player_update_extensions",
     "title": "player-update: timezone + channel consents",
     "description": "Optional fields: timezone (geo-IP derived), "
                    "email_opt_in, email_verified, push_opt_in, "
                    "push_available, in_app_available, channel_prefs; plus "
                    "a consent-change event.",
     "owner": "casino platform (Anton)",
     "blocking": "Smart Send Time precision; email/push routing consents"},
    {"item_key": "push_delivery_endpoint",
     "title": "Delegated push/in_app delivery endpoint",
     "description": "Endpoint accepting our delivery order (delivery_id, "
                    "player_id, channel, title, body, cta_url, ttl_sec), "
                    "idempotent by delivery_id, permanent/transient error "
                    "split, plus the delivery-status callback to "
                    "POST /partner/{id}/delivery-status.",
     "owner": "casino platform (Anton)",
     "blocking": "Push / in-app channels going live"},
    {"item_key": "email_customerio",
     "title": "Email: Customer.io workspace + credentials",
     "description": "Customer.io App API key (stored as a product secret), "
                    "region (us/eu), sending domain with SPF/DKIM/DMARC, "
                    "From/Reply-To, unsubscribe handling.",
     "owner": "email/devops",
     "blocking": "Email channel going live"},
    {"item_key": "vip_host_process",
     "title": "VIP host process for the host-task queue",
     "description": "Who works the retention_host_tasks queue (loss-rescue "
                    "for VIPs) and whether an export (webhook/email) to an "
                    "external CRM is needed.",
     "owner": "operations",
     "blocking": "VIP loss-rescue handling beyond the queue itself"},
    {"item_key": "bi_export",
     "title": "BI / warehouse export of fact rows",
     "description": "If the platform builds cross-surface attribution: agree "
                    "the export of our fact rows (touches, conversions, "
                    "deliveries) — direct read access or periodic export.",
     "owner": "BI / platform",
     "blocking": "Nothing on our side (uplift works locally)"},
)


async def _seed_integration_checklist(conn: asyncpg.Connection) -> None:
    for item in _INTEGRATION_CHECKLIST_SEED:
        await conn.execute(
            "INSERT INTO integration_checklist (item_key, title, "
            " description, owner, blocking) VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (item_key) DO NOTHING",
            item["item_key"], item["title"], item["description"],
            item["owner"], item["blocking"])


async def list_integration_checklist() -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM integration_checklist ORDER BY id")
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        _iso_fields(d, "created_at", "updated_at")
        items.append(d)
    return items


async def update_integration_checklist(item_key: str, *,
                                       status: Optional[str] = None,
                                       notes: Optional[str] = None,
                                       title: Optional[str] = None,
                                       description: Optional[str] = None,
                                       owner: Optional[str] = None,
                                       blocking: Optional[str] = None,
                                       updated_by: Optional[str] = None
                                       ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "UPDATE integration_checklist SET "
        " status = COALESCE($2, status), notes = COALESCE($3, notes), "
        " title = COALESCE($4, title), "
        " description = COALESCE($5, description), "
        " owner = COALESCE($6, owner), blocking = COALESCE($7, blocking), "
        " updated_by = $8, updated_at = now() "
        "WHERE item_key = $1 RETURNING *",
        item_key, status, notes, title, description, owner, blocking,
        updated_by)
    if row is None:
        return None
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "created_at", "updated_at")
    return d


async def create_integration_checklist_item(item_key: str, *, title: str,
                                            description: str, owner: str,
                                            blocking: str,
                                            updated_by: Optional[str]
                                            ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "INSERT INTO integration_checklist (item_key, title, description, "
        " owner, blocking, updated_by) VALUES ($1, $2, $3, $4, $5, $6) "
        "ON CONFLICT (item_key) DO NOTHING RETURNING *",
        item_key, title, description, owner, blocking, updated_by)
    if row is None:
        return None
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "created_at", "updated_at")
    return d


# ---------------------------------------------------------------------------
# KB helpers (all product-scoped)
# ---------------------------------------------------------------------------
# The topic catalogue, per-topic KB text and the variables registry change only
# on an admin edit but are read on every chat turn, so they are served from an
# in-process cache and dropped whenever a KB write goes through the helpers below
# (same accepted per-instance model as the settings cache).
_kb_topics_cache: dict[int, list[dict[str, Any]]] = {}   # product_id -> topics
_kb_content_cache: dict[int, Optional[str]] = {}         # topic_id  -> content
_kb_vars_cache: dict[int, dict[str, str]] = {}           # product_id -> vars map


def _invalidate_kb_cache() -> None:
    _kb_topics_cache.clear()
    _kb_content_cache.clear()
    _kb_vars_cache.clear()


def clear_kb_caches() -> None:
    """Public cache-drop for the periodic refresh loop. The KB caches are
    invalidated only by writes routed through THIS process, so on a multi-instance
    deployment an edit on instance A left instance B serving stale topics/KB/vars
    forever. main._settings_refresh_loop calls this every 60s (the same cadence
    the settings cache re-pulls at), bounding cross-instance KB staleness to ~60s
    instead of "until restart"."""
    _invalidate_kb_cache()


async def upsert_topic(product_id: int, slug: str, title: dict[str, str],
                       display_order: int, active: bool = True) -> int:
    row = await _fetchrow(
        """
        INSERT INTO kb_topics (product_id, slug, title, display_order, active)
        VALUES ($1, $2, $3::jsonb, $4, $5)
        ON CONFLICT (product_id, slug) DO UPDATE
          SET title = EXCLUDED.title,
              display_order = EXCLUDED.display_order,
              active = EXCLUDED.active
        RETURNING id
        """,
        product_id, slug, json.dumps(title), display_order, active,
    )
    _invalidate_kb_cache()
    return row["id"]


async def get_topic_by_slug(product_id: int, slug: str) -> Optional[dict[str, Any]]:
    # AND active: a deactivated topic must not be selectable (a stale widget or
    # handcrafted slug could otherwise load its soft-cleared KB), mirroring the
    # list_topics filter.
    row = await _fetchrow(
        "SELECT id, product_id, slug, title, display_order, active "
        "FROM kb_topics WHERE product_id = $1 AND slug = $2 AND active",
        product_id, slug,
    )
    return _row_to_topic(row) if row else None


async def get_topic_by_id(topic_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT id, product_id, slug, title, display_order, active "
        "FROM kb_topics WHERE id = $1",
        topic_id,
    )
    return _row_to_topic(row) if row else None


async def list_topics(product_id: int) -> list[dict[str, Any]]:
    """Full topic catalogue for a product — no topic is ever hidden.

    `other` is a normal, player-selectable topic like any other; the only
    special treatment it gets is ordering — as the always-available escape
    hatch it sorts last in the picker regardless of display_order.
    """
    cached = _kb_topics_cache.get(product_id)
    if cached is not None:
        return cached
    rows = await _fetch(
        "SELECT id, product_id, slug, title, display_order, active FROM kb_topics "
        "WHERE product_id = $1 AND active "
        "ORDER BY (slug = 'other'), display_order, id",
        product_id,
    )
    topics = [_row_to_topic(r) for r in rows]
    _kb_topics_cache[product_id] = topics
    return topics


async def get_kb_content(topic_id: int) -> Optional[str]:
    if topic_id in _kb_content_cache:
        return _kb_content_cache[topic_id]
    row = await _fetchrow(
        "SELECT content FROM kb_entries "
        "WHERE topic_id = $1 AND active ORDER BY id DESC LIMIT 1",
        topic_id,
    )
    content = row["content"] if row else None
    _kb_content_cache[topic_id] = content
    return content


def _row_to_topic(row: asyncpg.Record) -> dict[str, Any]:
    title = row["title"]
    if isinstance(title, str):
        title = json.loads(title)
    out = {
        "id": row["id"],
        "slug": row["slug"],
        "title": title,
        "display_order": row["display_order"],
        "active": row["active"],
    }
    # product_id rides along when the query selected it (authorization checks
    # need to know which product a topic belongs to).
    if "product_id" in row.keys():
        out["product_id"] = row["product_id"]
    return out




# Default KB variables. Keys/descriptions follow the original knowledge-base
# variables registry; VALUES are brand-neutral test placeholders until owners
# confirm final per-product values (this registry seeds every product, so no
# brand name/URL may appear in it — {{PLACEHOLDER}} marks values that only
# make sense once set per brand).
_DEFAULT_KB_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("deposit_methods", "Deposit methods (per market)", "USDT (crypto: TRC20/ERC20/BEP20), Visa/Mastercard, local payment methods"),
    ("crypto_networks", "Supported crypto networks", "TRC20, ERC20, BEP20"),
    ("card_deposit", "Card availability by market", "Visa, Mastercard (availability depends on market)"),
    ("local_payment_methods", "Local payment systems (per market)", "{{LOCAL_PAYMENT_METHODS}} (set per brand/market)"),
    ("min_deposit", "Minimum deposit", "10 USDT"),
    ("max_deposit", "Maximum deposit", "1000 USDT"),
    ("deposit_fee", "Deposit fee", "0% (no deposit fee)"),
    ("deposit_speed", "Deposit crediting speed", "crypto: after network confirmations (usually minutes); cards: instant"),
    ("deposit_flow", "Exact deposit steps (UI)", "wallet -> method -> amount -> confirm"),
    ("currencies", "Account currencies", "USDT"),
    ("fiat_conversion", "Fiat-to-crypto conversion", "applied at the transaction rate, shown before confirmation (test)"),
    ("decline_reasons", "Payment decline reasons/codes", "security checks, method/country limits, bank restrictions, incorrect details"),
    ("deposit_address_policy", "Deposit address policy", "address tied to account; take the current one from the deposit page before each transfer"),
    ("withdrawal_methods", "Withdrawal methods (per market)", "USDT, BTC (crypto); cards/e-wallets where available"),
    ("withdrawal_to_source", "Return-to-source rule", "closed-loop: withdraw to the deposit method"),
    ("min_withdrawal", "Minimum withdrawal", "20 USDT"),
    ("max_withdrawal", "Maximum withdrawal per transaction", "1000 USDT per withdrawal"),
    ("daily_withdrawal_limit", "Daily withdrawal limit", "5000 USDT/day (test)"),
    ("withdrawal_period_limits", "Weekly/monthly withdrawal limit", "weekly 20000 USDT, monthly 50000 USDT (test)"),
    ("withdrawal_fee_pct", "Withdrawal fee, percent", "0%"),
    ("withdrawal_processing_time", "Withdrawal processing time (SLA)", "up to 24h (crypto usually faster)"),
    ("withdrawal_flow", "Exact withdrawal steps (UI)", "wallet -> withdraw -> method -> amount -> details -> confirm"),
    ("cancel_withdrawal_policy", "Withdrawal cancellation availability", "can cancel while Pending via support"),
    ("withdrawal_taxes", "Withdrawal taxation", "per local jurisdiction; not withheld by the casino"),
    ("kyc_documents", "KYC document list", "government ID + proof of address + selfie with document"),
    ("kyc_trigger", "Mandatory verification trigger", "before first withdrawal"),
    ("kyc_sla", "Document review time", "up to 24h"),
    ("kyc_doc_format", "Document format requirements", "JPG/PNG/PDF, clear, full document visible, no glare"),
    ("min_age", "Minimum age", "18+"),
    ("reg_fields", "Registration form fields", "email, password, date of birth, country"),
    ("editable_profile_fields", "Editable profile fields", "contacts (email, phone) editable; KYC-confirmed data (name, DOB) locked"),
    ("gdpr_deletion_process", "Account deletion process (GDPR)", "request via support; processed after active bonuses are closed and balance withdrawn"),
    ("twofa_methods", "Available 2FA methods", "authenticator app, email, SMS"),
    ("password_policy", "Password requirements", "min 8 chars, upper and lower case, a digit and a symbol"),
    ("lockout_time_min", "Lockout after failed logins, minutes", "15 minutes"),
    ("session_timeout_min", "Session timeout, minutes", "30 minutes"),
    ("min_bet", "Minimum bet", "0.20 USDT"),
    ("max_bet", "Maximum bet", "up to 100 USDT (varies by game/table) (test)"),
    ("max_win_multiplier", "Maximum win multiplier", "5000x (varies by game) (test)"),
    ("active_sports", "Active sports", "football, basketball, tennis, esports (after sports section launch) (test)"),
    ("providers", "Provider list", "NetEnt, Pragmatic Play, Evolution, Play'n GO, Hacksaw Gaming"),
    ("provably_fair", "Provably Fair for crash/fast games", "available for crash and instant games; verifiable by seed/hash per round"),
    ("demo_mode", "Demo mode (fun play)", "available for most slots (fun play, no real winnings) (test)"),
    ("welcome_bonus", "Welcome bonus value", "100% Match up to 200 USDT + 50 FS (test)"),
    ("welcome_min_deposit", "Minimum deposit for Welcome bonus", "10 USDT"),
    ("game_weighting", "Game weighting for wagering (table)", "slots 100%, live and table 10%, blackjack and baccarat 0%"),
    ("promo_code_field", "Promo code input field", "Cashier/Deposit page -> 'Promo code' field"),
    ("level_rewards_map", "Level rewards map", "per-level rewards (FS, bonus cash, perks) defined in the Loyalty Engine reward map"),
    ("vip_thresholds", "VIP class thresholds", "Player -> Bronze -> Silver -> Gold -> Platinum -> VIP by accumulated XP (50 levels / 6 classes)"),
    ("multi_bonus_policy", "Multiple-bonus policy", "one deposit bonus active at a time; Welcome has top priority"),
    ("daily_card_super_prize", "Bonus card super prize", "Reload 20% on next deposit"),
    ("license_info", "License and regulator", "{{LICENSE_INFO}} (set per brand)"),
    ("restricted_countries", "Restricted countries", "{{RESTRICTED_COUNTRIES}} (set per brand)"),
    ("locales", "Site languages", "{{LOCALES}} (set per brand)"),
    ("support_languages", "Support languages", "Spanish, Portuguese, English"),
    ("support_channels", "Support channels", "24/7 on-site chat, Telegram bot"),
    ("app_platforms", "App platforms (PWA)", "iOS, Android (PWA)"),
    ("supported_browsers", "Supported browsers", "Chrome, Safari, Opera, Firefox (current versions)"),
    ("telegram_link_flow", "Telegram linking steps", "one-time token deep-link on bot subscription"),
    ("referral_reward", "Referral reward", "10 USDT per 5 friends with KYC, wager x3, 14 days"),
    ("daily_card_days", "Bonus card length, days", "15"),
    ("support_hours", "Support hours", "24/7"),
    ("responsible_gaming_tools", "Responsible gaming tools", "deposit/bet/time limits, self-exclusion, reality-check"),
    ("mirror_channels", "Mirror channels", "Telegram channel, email newsletter, live bookmarks"),
    ("kyc_address_docs", "Address verification documents", "utility bill or bank statement (recent, with name and address)"),
    ("liveness_check", "Selfie/liveness verification requirement", "selfie with document; short liveness check when required"),
    ("source_of_funds", "Source-of-funds verification (enhanced review)", "for large withdrawals: payslip / bank statement / proof of crypto origin (test)"),
    ("social_login", "Social/Google login availability", "Google login available (test)"),
    ("username_change", "Username change availability", "username can be changed in profile; KYC-confirmed data stays locked"),
    ("active_promos", "Current active promotions list", "dynamic list from Bonuses Module (e.g. Kickstart Monday, Spin Mission Tuesday)"),
    ("bonus_optout", "Deposit without bonus availability (opt-out)", "yes - deposit without a bonus by declining it at the cashier"),
    ("welcome_wager_fs", "Welcome free spins wager", "x20"),
    ("welcome_wager_match", "Welcome match bonus wager", "x35 (test)"),
    ("crash_multibet", "Two-bet support per crash round", "2 simultaneous bets per round with separate cash-outs"),
    ("feature_buy", "Feature buy availability", "available on selected slots (high-risk) (test)"),
    ("live_limits", "Live table betting limits", "per table; typically 0.50-5000 USDT (varies) (test)"),
    ("new_games_section", "New games catalogue section", "'New games' section in the casino catalog"),
    ("video_poker", "Video poker / poker formats availability", "available (e.g. Jacks or Better) (test)"),
    ("privacy_policy", "Privacy policy URL", "{{PRIVACY_POLICY_URL}} (set per brand)"),
    ("terms_url", "Terms and Conditions URL", "{{TERMS_URL}} (set per brand)"),
    ("vip_withdrawal_limits", "Withdrawal limits by status/VIP", "higher limits for VIP (e.g. daily 20000 USDT) (test)"),
    ("social_links", "Official social links and channels", "{{SOCIAL_LINKS}} (set per brand)"),
    ("affiliate_program", "Affiliate program terms and onboarding", "revenue-share affiliate program; terms via support (test)"),
    ("agent_program", "Agent/cooperation program terms", "agent/partner cooperation program; terms via support (test)"),
    ("crypto_assets", "Accepted cryptocurrencies", "USDT, BTC, ETH"),
    ("crypto_confirmations", "Network confirmations for deposits/withdrawals", "USDT TRC20: 1; ERC20: 12; BTC: 2 (test)"),
    ("deposit_refund", "Deposit refund / chargeback policy", "disputed transactions handled via support; no bank chargebacks"),
    ("ewallets", "E-wallets and Apple/Google Pay", "Skrill, Neteller (where available) (test)"),
    ("network_fee_policy", "Network/gas fee payer and policy", "network/gas fee paid by the sender; shown before confirmation"),
    ("third_party_deposit", "Third-party deposit policy", "own payment instruments only; third-party payments not accepted"),
    ("third_party_withdrawal", "Third-party withdrawal policy", "own details only; no third-party payouts (AML)"),
    ("support_sla", "Support response time target", "chat under 2 min; email under 24h (test)"),
    ("bet_types", "Sports bet types (single/accumulator/system)", "single, accumulator, system (after sports launch)"),
    ("live_betting", "Live betting (in-play) availability", "in-play betting after the sports section launch"),
    ("max_payout_sports", "Maximum sports bet payout", "50000 USDT cap (after sports launch) (test)"),
    ("odds_format", "Supported odds formats", "decimal (default), fractional, American"),
    ("sports_cashout", "Sports cash-out (early settlement)", "early cash-out after the sports section launch"),
)


async def seed_kb_variables(conn: Optional[asyncpg.Connection] = None,
                            product_id: Optional[int] = None) -> None:
    """Insert default variables for a product without overwriting edited values.

    Runs at boot for the default product and at creation time for every new
    product, so each casino starts with the full placeholder registry.
    """
    query = """
        INSERT INTO kb_variables (product_id, key, description, value)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (product_id, key) DO NOTHING
        """
    rows = [(product_id, k, d, v) for k, d, v in _DEFAULT_KB_VARIABLES]
    if conn is not None:
        await conn.executemany(query, rows)
    else:
        # The bounded module helper, NOT _pool.executemany — a bare pool call
        # waits for a free slot with no ceiling (this was the one query in the
        # module left bypassing _acquire()).
        await _executemany(query, rows)


async def seed_starter_kb(product_id: int) -> None:
    """Seed a new product with the generic starter topics + KB texts.

    Gives a freshly created casino a working, brand-neutral knowledge base
    (see starter_kb.py) until the owner uniquifies the content. Inserts only
    topics the product does not have yet (ON CONFLICT DO NOTHING) and writes
    a KB entry only for a topic this call actually created — it can never
    overwrite an existing topic or KB text.
    """
    from app.ai import starter_kb  # local import (starter_kb → prompts) to avoid a cycle

    for order, (slug, titles, content) in enumerate(starter_kb.STARTER_TOPICS,
                                                    start=1):
        row = await _fetchrow(
            """
            INSERT INTO kb_topics (product_id, slug, title, display_order, active)
            VALUES ($1, $2, $3::jsonb, $4, TRUE)
            ON CONFLICT (product_id, slug) DO NOTHING
            RETURNING id
            """,
            product_id, slug, json.dumps(titles), order,
        )
        if row is None:
            continue
        await _execute(
            "INSERT INTO kb_entries (topic_id, content, active) "
            "VALUES ($1, $2, TRUE)",
            row["id"], content,
        )


def _row_to_kb_variable(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    # `updated_at` is a datetime; JSONResponse (Starlette json.dumps) cannot
    # serialize it, so render it as an ISO string like every other admin payload.
    updated_at = d.get("updated_at")
    d["updated_at"] = updated_at.isoformat() if updated_at is not None else None
    return d


async def list_kb_variables(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT key, description, value, updated_at, updated_by "
        "FROM kb_variables WHERE product_id = $1 ORDER BY key",
        product_id,
    )
    return [_row_to_kb_variable(r) for r in rows]


async def get_kb_variables_map(product_id: int) -> dict[str, str]:
    cached = _kb_vars_cache.get(product_id)
    if cached is not None:
        return cached
    rows = await _fetch(
        "SELECT key, value FROM kb_variables WHERE product_id = $1", product_id
    )
    vars_map = {r["key"]: r["value"] for r in rows}
    _kb_vars_cache[product_id] = vars_map
    return vars_map


async def set_kb_variable(product_id: int, key: str, description: str, value: str,
                          updated_by: Optional[str] = None) -> dict[str, Any]:
    row = await _fetchrow(
        """
        INSERT INTO kb_variables (product_id, key, description, value, updated_at, updated_by)
        VALUES ($1, $2, $3, $4, now(), $5)
        ON CONFLICT (product_id, key) DO UPDATE
          SET description = EXCLUDED.description,
              value = EXCLUDED.value,
              updated_at = now(),
              updated_by = EXCLUDED.updated_by
        RETURNING key, description, value, updated_at, updated_by
        """,
        product_id, key, description, value, updated_by,
    )
    _invalidate_kb_cache()
    return _row_to_kb_variable(row)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
async def create_session(consumer: str, player_id: Optional[str],
                         lang: Optional[str], user_context: dict[str, Any],
                         session_id: Optional[str] = None,
                         product_id: Optional[int] = None,
                         tg_user_id: Optional[int] = None,
                         prev_session_id: Optional[str] = None) -> str:
    sid = session_id or str(uuid.uuid4())
    await _execute(
        "INSERT INTO chat_sessions "
        "(id, consumer, product_id, player_id, lang, user_context, tg_user_id, "
        " prev_session_id) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)",
        sid, consumer, product_id, _as_text(player_id), lang,
        json.dumps(user_context or {}), tg_user_id, prev_session_id,
    )
    return sid


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT id, consumer, product_id, player_id, lang, conv_lang, topic_id, "
        "user_context, status, escalated, message_count, "
        "context_reset_id, prev_session_id, created_at, updated_at "
        "FROM chat_sessions WHERE id = $1",
        session_id,
    )
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    if d.get("prev_session_id") is not None:
        d["prev_session_id"] = str(d["prev_session_id"])
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
    async with _acquire() as conn:
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
    await _execute(
        "UPDATE chat_sessions SET conv_lang = $1, updated_at = now() WHERE id = $2",
        conv_lang, session_id,
    )


async def mark_escalated(session_id: str) -> None:
    await _execute(
        "UPDATE chat_sessions SET status = 'escalated', escalated = TRUE, "
        "updated_at = now() WHERE id = $1",
        session_id,
    )


async def mark_escalated_soft(session_id: str) -> None:
    """Flag a SOFT (keyword-triggered) escalation without closing the session.

    Sets only `escalated = TRUE` so the metrics and the Unresolved queue see the
    hand-off, but `status` stays 'open' and the player can keep chatting — a
    fuzzy keyword false positive must never kill a live conversation. A later
    HARD escalation (model [[ESCALATE]], cap, explicit) still closes it via
    mark_escalated.
    """
    await _execute(
        "UPDATE chat_sessions SET escalated = TRUE, updated_at = now() "
        "WHERE id = $1",
        session_id,
    )


async def mark_resolved(session_id: str) -> None:
    """Close a session the player ended via the 'finish chat' nudge.

    Sets status='resolved' so it drops out of the open-session metric. An
    escalated session is left untouched — a pending hand-off to a human must not
    be silently closed by the player tapping finish.
    """
    await _execute(
        "UPDATE chat_sessions SET status = 'resolved', updated_at = now() "
        "WHERE id = $1 AND status <> 'escalated'",
        session_id,
    )


async def get_history(session_id: str, limit: int = 50,
                      after_id: int = 0) -> list[dict[str, Any]]:
    """Return messages oldest-first, at most `limit` ROWS.

    `limit` counts rows, not turns — a turn is two rows (the player's message
    and the reply), so a caller thinking in turns must pass `turns * 2`.

    `after_id` restricts to messages newer than that id — used for prompt
    building so that turns from before a topic switch (the session's
    `context_reset_id`) are excluded. Default 0 returns the whole transcript
    (resume/admin views).
    """
    rows = await _fetch(
        "SELECT role, content, lang, ping_context, link_url, created_at FROM ("
        "  SELECT role, content, lang, ping_context, link_url, created_at, id "
        "  FROM chat_messages "
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
    ai_meta: Optional[dict[str, Any]] = None,
    product_id: Optional[int] = None,
    link_url: Optional[str] = None,
    consumer: str = "web",
) -> int:
    """Insert user + assistant rows, bump counters, write the AI log — atomically.

    Returns the new `message_count` for the session.
    When present, `ai_meta` carries: model, key_used, tokens_in, tokens_out,
    cached_in, cost_usd, latency_ms, ok, error. Model-free backend replies
    (for example the message-cap hand-off) still persist the visible chat turn
    but intentionally skip `ai_interaction_logs` because no API call happened.
    `product_id` (the session's product) is denormalized onto the AI log row so
    per-product cost dashboards aggregate without a join, and so are the spend
    attribution labels: `consumer` (the caller's facade — 'web' for the widget,
    'telegram' for a retention dialogue turn) with source 'chat', since this
    helper only ever persists a DIALOGUE turn. `link_url` records
    the validated CTA button attached to the assistant message (retention),
    so the prompt history can show which page was already linked.
    """
    ai_meta = ai_meta or {}
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, lang) "
                "VALUES ($1, 'user', $2, $3)",
                session_id, user_text, user_lang,
            )
            await conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, lang, model, key_used, tokens_in, "
                " tokens_out, cached_in, cost_usd, link_url) "
                "VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                session_id, assistant_text, assistant_lang,
                ai_meta.get("model"), ai_meta.get("key_used"),
                ai_meta.get("tokens_in"), ai_meta.get("tokens_out"),
                ai_meta.get("cached_in"), ai_meta.get("cost_usd"),
                link_url,
            )
            if ai_meta:
                await conn.execute(
                    "INSERT INTO ai_interaction_logs "
                    "(session_id, product_id, consumer, source, model, key_used, "
                    " tokens_in, tokens_out, cached_in, cost_usd, latency_ms, ok, "
                    " error) "
                    "VALUES ($1, $2, $3, 'chat', $4, $5, $6, $7, $8, $9, $10, "
                    " $11, $12)",
                    session_id, product_id, consumer,
                    ai_meta.get("model"), ai_meta.get("key_used"),
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
    error: Optional[str], product_id: Optional[int] = None,
    consumer: Optional[str] = None, source: Optional[str] = None,
) -> None:
    """Account one OpenAI call (invariant §4).

    `consumer` ('web'|'telegram') and `source` ('chat'|'agent'|'review'|'media')
    attribute the spend — pass BOTH on every new call site, especially the
    session-less background ones: a row without them cannot be told apart from
    the others at read time.
    """
    await _execute(
        "INSERT INTO ai_interaction_logs "
        "(session_id, product_id, consumer, source, model, key_used, tokens_in, "
        " tokens_out, cached_in, cost_usd, latency_ms, ok, error) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)",
        session_id, product_id, consumer, source, model, key_used, tokens_in,
        tokens_out, cached_in, cost_usd, latency_ms, ok, error,
    )


async def log_admin_event(session_id: Optional[str], type_: str,
                          payload: Optional[dict[str, Any]] = None,
                          product_id: Optional[int] = None) -> None:
    # When the caller doesn't pass the product explicitly, fall back to the
    # request's tenancy scope so per-product dashboards see admin actions too.
    if product_id is None:
        from app.core import tenancy  # local import: db must stay importable standalone
        product_id = tenancy.current_product_id()
    await _execute(
        "INSERT INTO admin_events (session_id, product_id, type, payload) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        session_id, product_id, type_, json.dumps(payload or {}),
    )


# High-volume event types (rate-limit blocks, injection blocks, low-content
# nudges, turnstile skips) fire once per REJECTED request, so an attacker
# hammering a 429'd endpoint would otherwise grow admin_events without bound —
# the rate limiter rejects the request but never stopped the logging. The
# sampled writer keeps a small in-memory budget per event type and silently
# drops the excess: the dashboard still sees that the blocking happened (the
# first N events per window), the table stays bounded. In-memory like the rate
# limiter itself — per instance, reset on restart, which is fine for sampling.
_EVENT_SAMPLE_WINDOW_SEC = 300.0
_EVENT_SAMPLE_MAX_PER_WINDOW = 20
# Keyed by (type_, product_id): the budget is now PER TENANT, so one product's
# flood can't exhaust the shared per-type budget and blank out another product's
# abuse audit during a simultaneous attack (product_id=None keeps a global bucket
# for scope-less callers).
_event_sample_hits: dict[tuple[str, Optional[int]], "_deque[float]"] = {}


async def log_admin_event_sampled(session_id: Optional[str], type_: str,
                                  payload: Optional[dict[str, Any]] = None,
                                  product_id: Optional[int] = None) -> None:
    """log_admin_event with a per-type budget; excess events are dropped.

    `product_id` should be passed by callers with NO tenancy scope on the request
    (the Telegram webhook path never sets it): without it these rows land with
    product_id NULL and never heal, so a product-scoped admin's dashboard shows
    zero abuse events for the product under attack. Callers on a scoped request
    can omit it (log_admin_event falls back to the tenancy ContextVar)."""
    import time as _time
    now = _time.monotonic()
    hits = _event_sample_hits.setdefault((type_, product_id), _deque())
    while hits and now - hits[0] > _EVENT_SAMPLE_WINDOW_SEC:
        hits.popleft()
    if len(hits) >= _EVENT_SAMPLE_MAX_PER_WINDOW:
        return
    hits.append(now)
    # Only forward product_id when the caller set it explicitly; otherwise call
    # exactly as before so log_admin_event's tenancy-ContextVar fallback applies
    # (and callers/tests stubbing log_admin_event without the kwarg keep working).
    if product_id is not None:
        await log_admin_event(session_id, type_, payload, product_id=product_id)
    else:
        await log_admin_event(session_id, type_, payload)


async def ping() -> bool:
    """Fast DB probe for /healthz. Bounded so a stalled/exhausted pool can't hold
    the liveness probe open for the platform's whole healthcheck window (which
    would drive a restart/crash loop). Returns False on timeout or any error."""
    try:
        val = await asyncio.wait_for(
            _fetchval("SELECT 1"),
            timeout=config.DB_HEALTHCHECK_TIMEOUT_SEC,
        )
        return val == 1
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - probe fails soft
        return False


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
    rows = await _fetch("SELECT key, value FROM app_settings")
    return {r["key"]: _json_value(r["value"]) for r in rows}


async def set_setting(key: str, value: Any, updated_by: Optional[str] = None) -> None:
    await _execute(
        "INSERT INTO app_settings (key, value, updated_at, updated_by) "
        "VALUES ($1, $2::jsonb, now(), $3) "
        "ON CONFLICT (key) DO UPDATE "
        "  SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by",
        key, json.dumps(value), updated_by,
    )


# ---------------------------------------------------------------------------
# product_settings (per-product overrides; resolution product > global > env)
# ---------------------------------------------------------------------------
async def get_all_product_settings() -> dict[int, dict[str, Any]]:
    """Every product's overrides keyed by product_id (for the settings cache)."""
    rows = await _fetch("SELECT product_id, key, value FROM product_settings")
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["product_id"], {})[r["key"]] = _json_value(r["value"])
    return out


async def get_product_settings(product_id: int) -> dict[str, Any]:
    rows = await _fetch(
        "SELECT key, value FROM product_settings WHERE product_id = $1", product_id
    )
    return {r["key"]: _json_value(r["value"]) for r in rows}


async def set_product_setting(product_id: int, key: str, value: Any,
                              updated_by: Optional[str] = None) -> None:
    await _execute(
        "INSERT INTO product_settings (product_id, key, value, updated_at, updated_by) "
        "VALUES ($1, $2, $3::jsonb, now(), $4) "
        "ON CONFLICT (product_id, key) DO UPDATE "
        "  SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by",
        product_id, key, json.dumps(value), updated_by,
    )


# ---------------------------------------------------------------------------
# Tenancy: partners & products (+ encrypted per-product secrets)
#
# Secret columns never leave this module in encrypted OR raw form through the
# generic row serializers — _row_to_product exposes only has_* presence flags.
# The decrypted values are returned ONLY by the two dedicated getters used by
# the OpenAI client and the handshake verifier.
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _iso_fields(d: dict[str, Any], *names: str) -> dict[str, Any]:
    """Normalize timestamp fields to isoformat strings in place (JSON-safe)."""
    for ts in names:
        if ts in d:
            d[ts] = _iso(d[ts])
    return d


def _affected(result: Optional[str]) -> int:
    """Rows affected from an asyncpg command tag ("UPDATE 3" / "DELETE 0")."""
    try:
        return int((result or "").split()[-1])
    except (ValueError, IndexError):
        return 0


def _row_to_partner(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "active": row["active"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _row_to_product(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "partner_id": row["partner_id"],
        "slug": row["slug"],
        "name": row["name"],
        "widget_key": row["widget_key"],
        "active": row["active"],
        "has_openai_key": row["openai_key_primary_enc"] is not None,
        "has_openai_key_fallback": row["openai_key_fallback_enc"] is not None,
        "has_handshake_secret": row["handshake_secret_enc"] is not None,
        # Retention / Telegram config (secrets exposed as presence flags only).
        "has_telegram_bot_token": row["telegram_bot_token_enc"] is not None,
        "telegram_bot_username": row["telegram_bot_username"],
        "telegram_webhook_secret": row["telegram_webhook_secret"],
        "telegram_channel_id": row["telegram_channel_id"],
        "telegram_channel_url": row["telegram_channel_url"],
        "player_api_url": row["player_api_url"],
        "site_url": row["site_url"],
        "has_player_api_key": row["player_api_key_enc"] is not None,
        "retention_enabled": row["retention_enabled"],
        # Per-product Turnstile: the site key is public widget config; the
        # secret (encrypted) surfaces as a presence flag only.
        "turnstile_site_key": row["turnstile_site_key"],
        "has_turnstile_secret": row["turnstile_secret_enc"] is not None,
        # Orchestrator outbound endpoints (plain config) + their secrets
        # (presence flags only, like every product secret).
        "offer_grant_url": row["offer_grant_url"],
        "delivery_endpoint_url": row["delivery_endpoint_url"],
        "has_partner_out_key": row["partner_out_key_enc"] is not None,
        "has_email_api_key": row["email_api_key_enc"] is not None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


_PRODUCT_COLS = ("id, partner_id, slug, name, widget_key, active, "
                 "openai_key_primary_enc, openai_key_fallback_enc, "
                 "handshake_secret_enc, telegram_bot_token_enc, "
                 "telegram_bot_username, telegram_webhook_secret, "
                 "telegram_channel_id, telegram_channel_url, player_api_url, "
                 "site_url, player_api_key_enc, retention_enabled, "
                 "turnstile_site_key, turnstile_secret_enc, "
                 "offer_grant_url, delivery_endpoint_url, "
                 "partner_out_key_enc, email_api_key_enc, "
                 "created_at, updated_at")


async def list_partners() -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT id, slug, name, active, created_at, updated_at "
        "FROM partners ORDER BY id"
    )
    return [_row_to_partner(r) for r in rows]


async def get_partner(partner_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT id, slug, name, active, created_at, updated_at "
        "FROM partners WHERE id = $1", partner_id
    )
    return _row_to_partner(row) if row else None


async def create_partner(slug: str, name: str) -> Optional[dict[str, Any]]:
    """Insert a partner; returns None when the slug is already taken."""
    row = await _fetchrow(
        "INSERT INTO partners (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO NOTHING "
        "RETURNING id, slug, name, active, created_at, updated_at",
        slug, name,
    )
    return _row_to_partner(row) if row else None


async def update_partner(partner_id: int, *, name: Optional[str] = None,
                         active: Optional[bool] = None) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "UPDATE partners SET name = COALESCE($2, name), "
        "active = COALESCE($3, active), updated_at = now() WHERE id = $1 "
        "RETURNING id, slug, name, active, created_at, updated_at",
        partner_id, name, active,
    )
    return _row_to_partner(row) if row else None


async def list_products(product_ids: Optional[list[int]] = None
                        ) -> list[dict[str, Any]]:
    where_sql, args = "", []
    if product_ids is not None:
        args.append(product_ids)
        where_sql = f"WHERE id = ANY(${len(args)}::int[])"
    rows = await _fetch(
        f"SELECT {_PRODUCT_COLS} FROM products {where_sql} ORDER BY id", *args
    )
    return [_row_to_product(r) for r in rows]


async def get_product(product_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE id = $1", product_id
    )
    return _row_to_product(row) if row else None


async def get_product_by_widget_key(widget_key: str) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE widget_key = $1", widget_key
    )
    return _row_to_product(row) if row else None


async def get_default_product() -> Optional[dict[str, Any]]:
    """The boot-seeded product a key-less widget lands on (see tenancy.py)."""
    from app.core import tenancy
    row = await _fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE slug = $1",
        tenancy.DEFAULT_PRODUCT_SLUG,
    )
    return _row_to_product(row) if row else None


async def list_active_products() -> list[dict[str, Any]]:
    """Every active product — the sweep set of workers that are not
    retention-specific (the quality reviewer covers both facades)."""
    rows = await _fetch(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE active ORDER BY id")
    return [_row_to_product(r) for r in rows]


async def list_retention_products() -> list[dict[str, Any]]:
    """Active products running the retention bot (the ping worker's sweep set)."""
    rows = await _fetch(
        f"SELECT {_PRODUCT_COLS} FROM products "
        "WHERE active AND retention_enabled ORDER BY id"
    )
    return [_row_to_product(r) for r in rows]


def _new_widget_key() -> str:
    import secrets as _secrets
    return "wk_" + _secrets.token_urlsafe(24)


async def create_product(partner_id: int, slug: str, name: str
                         ) -> Optional[dict[str, Any]]:
    """Insert a product and seed its brand-neutral baseline.

    A new casino starts working out of the box: widget key generated, the KB
    variables registry, the generic starter topics + KB texts (starter_kb.py),
    the starter retention-KB document and the
    full prompt-variables sets — support AND retention (template defaults,
    brand name = the product's name) — are all seeded into the PRODUCT layer,
    so nothing is inherited from another brand's global overrides. The owner then translates/uniquifies
    everything from the admin panel. (Translations and the retention settings group need no seed: their
    English/five-language defaults ship in the registries and resolve for
    every product until overridden.)

    Returns None when the slug is already taken.
    """
    from app.ai import starter_kb  # local import (starter_kb → prompts) to avoid a cycle

    row = await _fetchrow(
        "INSERT INTO products (partner_id, slug, name, widget_key) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (slug) DO NOTHING "
        f"RETURNING {_PRODUCT_COLS}",
        partner_id, slug, name, _new_widget_key(),
    )
    if row is None:
        return None
    await seed_kb_variables(product_id=row["id"])
    await seed_starter_kb(row["id"])
    await seed_starter_retention_kb(row["id"])
    # The default 7/14/30 idle re-engagement ladder (retention_idle.py) — only
    # when the product has no rules, so a re-run can never duplicate.
    from app.retention import retention_idle  # local import (retention_idle → db) to avoid a cycle
    await retention_idle.seed_starter_idle_rules(row["id"])
    await set_product_setting(row["id"], "prompt_variables",
                              starter_kb.starter_prompt_variables(name),
                              updated_by="starter-seed")
    await set_product_setting(row["id"], "retention_prompt_variables",
                              starter_kb.starter_retention_prompt_variables(name),
                              updated_by="starter-seed")
    return _row_to_product(row)


async def update_product(product_id: int, *, name: Optional[str] = None,
                         active: Optional[bool] = None) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "UPDATE products SET name = COALESCE($2, name), "
        "active = COALESCE($3, active), updated_at = now() WHERE id = $1 "
        f"RETURNING {_PRODUCT_COLS}",
        product_id, name, active,
    )
    return _row_to_product(row) if row else None


async def rotate_widget_key(product_id: int) -> Optional[str]:
    """Mint a fresh widget key (old embeds stop resolving immediately)."""
    return await _fetchval(
        "UPDATE products SET widget_key = $2, updated_at = now() "
        "WHERE id = $1 RETURNING widget_key",
        product_id, _new_widget_key(),
    )


# Sentinel distinguishing "leave this secret unchanged" from "clear it" ("").
UNSET: Any = object()


async def set_product_secrets(product_id: int, *,
                              openai_key_primary: Any = UNSET,
                              openai_key_fallback: Any = UNSET,
                              handshake_secret: Any = UNSET,
                              telegram_bot_token: Any = UNSET,
                              player_api_key: Any = UNSET,
                              turnstile_secret: Any = UNSET,
                              partner_out_key: Any = UNSET,
                              email_api_key: Any = UNSET) -> bool:
    """Write per-product secrets (encrypted at rest). Empty string clears one.

    Values are encrypted with secretbox before they touch the table; the
    plaintext is never stored or logged. Returns False for an unknown product.
    Setting a non-empty telegram_bot_token also mints the product's non-secret
    webhook routing token if it has none yet (so the webhook URL can be built).
    """
    from app.core import secretbox
    import secrets as _secrets
    sets: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    for col, val in (("openai_key_primary_enc", openai_key_primary),
                     ("openai_key_fallback_enc", openai_key_fallback),
                     ("handshake_secret_enc", handshake_secret),
                     ("telegram_bot_token_enc", telegram_bot_token),
                     ("player_api_key_enc", player_api_key),
                     ("turnstile_secret_enc", turnstile_secret),
                     ("partner_out_key_enc", partner_out_key),
                     ("email_api_key_enc", email_api_key)):
        if val is UNSET:
            continue
        args.append(secretbox.encrypt(val.strip()) if isinstance(val, str)
                    and val.strip() else None)
        sets.append(f"{col} = ${len(args)}")
    # Mint the webhook routing token when a bot token is (re)set and none exists.
    if isinstance(telegram_bot_token, str) and telegram_bot_token.strip():
        args.append("tgwh_" + _secrets.token_urlsafe(24))
        sets.append(f"telegram_webhook_secret = "
                    f"COALESCE(telegram_webhook_secret, ${len(args)})")
    args.append(product_id)
    row = await _fetchrow(
        f"UPDATE products SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING id",
        *args,
    )
    return row is not None


async def get_product_telegram_token(product_id: int) -> Optional[str]:
    """Decrypted per-product Telegram bot token, or None when unset/undecryptable."""
    import logging
    from app.core import secretbox
    enc = await _fetchval(
        "SELECT telegram_bot_token_enc FROM products WHERE id = $1", product_id
    )
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=telegram", product_id
        )
        return None


async def get_product_player_api_key(product_id: int) -> Optional[str]:
    """Decrypted per-product player-API key, or None when unset/undecryptable."""
    import logging
    from app.core import secretbox
    enc = await _fetchval(
        "SELECT player_api_key_enc FROM products WHERE id = $1", product_id
    )
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=player_api", product_id
        )
        return None


async def update_product_telegram_config(
    product_id: int, *,
    telegram_bot_username: Any = UNSET,
    telegram_channel_id: Any = UNSET,
    telegram_channel_url: Any = UNSET,
    player_api_url: Any = UNSET,
    retention_enabled: Any = UNSET,
) -> Optional[dict[str, Any]]:
    """Set the NON-secret Telegram/player config on a product (partial update)."""
    sets: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    for col, val in (("telegram_bot_username", telegram_bot_username),
                     ("telegram_channel_id", telegram_channel_id),
                     ("telegram_channel_url", telegram_channel_url),
                     ("player_api_url", player_api_url),
                     ("retention_enabled", retention_enabled)):
        if val is UNSET:
            continue
        if isinstance(val, str):
            val = val.strip() or None
        args.append(val)
        sets.append(f"{col} = ${len(args)}")
    args.append(product_id)
    row = await _fetchrow(
        f"UPDATE products SET {', '.join(sets)} WHERE id = ${len(args)} "
        f"RETURNING {_PRODUCT_COLS}",
        *args,
    )
    return _row_to_product(row) if row else None


async def get_product_by_telegram_webhook_secret(secret: str
                                                 ) -> Optional[dict[str, Any]]:
    """Resolve the product an incoming Telegram update belongs to (webhook path).

    The webhook path segment is the product's non-secret routing token
    (telegram_webhook_secret), so this is the multi-tenant entry point for the
    bot — the Telegram analogue of get_product_by_widget_key.
    """
    if not secret:
        return None
    row = await _fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE telegram_webhook_secret = $1",
        secret,
    )
    return _row_to_product(row) if row else None


async def get_product_openai_keys(product_id: int) -> Optional[dict[str, Optional[str]]]:
    """Decrypted per-product OpenAI keys, or None when the product has none.

    A decryption failure (e.g. SECRETS_MASTER_KEY was rotated) is logged and
    treated as "no product keys" so the chat degrades to the env fallback
    instead of hard-failing every turn.
    """
    import logging

    from app.core import secretbox
    row = await _fetchrow(
        "SELECT openai_key_primary_enc, openai_key_fallback_enc "
        "FROM products WHERE id = $1", product_id,
    )
    if not row or row["openai_key_primary_enc"] is None:
        return None
    try:
        primary = secretbox.decrypt(row["openai_key_primary_enc"])
        fallback = (secretbox.decrypt(row["openai_key_fallback_enc"])
                    if row["openai_key_fallback_enc"] else None)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=openai "
            "(SECRETS_MASTER_KEY rotated? re-enter the keys in the admin panel)",
            product_id,
        )
        return None
    return {"primary": primary, "fallback": fallback}


async def get_product_handshake_secret(product_id: int) -> Optional[str]:
    """Decrypted per-product handshake secret, or None (env fallback applies)."""
    import logging

    from app.core import secretbox
    enc = await _fetchval(
        "SELECT handshake_secret_enc FROM products WHERE id = $1", product_id
    )
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=handshake",
            product_id,
        )
        return None


async def get_product_turnstile_secret(product_id: int) -> Optional[str]:
    """Decrypted per-product Turnstile secret, or None (env fallback applies)."""
    import logging

    from app.core import secretbox
    enc = await _fetchval(
        "SELECT turnstile_secret_enc FROM products WHERE id = $1", product_id
    )
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=turnstile",
            product_id,
        )
        return None


async def set_product_turnstile_site_key(product_id: int,
                                         site_key: Optional[str]
                                         ) -> Optional[dict[str, Any]]:
    """Set the NON-secret Turnstile site key (public widget config)."""
    row = await _fetchrow(
        f"UPDATE products SET turnstile_site_key = $2, updated_at = now() "
        f"WHERE id = $1 RETURNING {_PRODUCT_COLS}",
        product_id, (site_key or "").strip() or None,
    )
    return _row_to_product(row) if row else None


async def set_product_site_url(product_id: int,
                               site_url: Optional[str]
                               ) -> Optional[dict[str, Any]]:
    """Set the product's public main-site URL (its home page). Public config;
    the Telegram hand-off's "support on the site" button lands here."""
    row = await _fetchrow(
        f"UPDATE products SET site_url = $2, updated_at = now() "
        f"WHERE id = $1 RETURNING {_PRODUCT_COLS}",
        product_id, (site_url or "").strip() or None,
    )
    return _row_to_product(row) if row else None


# ---------------------------------------------------------------------------
# Admin memberships (user <-> scope <-> role)
# ---------------------------------------------------------------------------
def _row_to_membership(row: asyncpg.Record) -> dict[str, Any]:
    d = {
        "id": row["id"],
        "email": row["email"],
        "scope_type": row["scope_type"],
        "partner_id": row["partner_id"],
        "product_id": row["product_id"],
        "role": row["role"],
    }
    for extra in ("partner_name", "product_name", "partner_slug", "product_slug"):
        if extra in row.keys():
            d[extra] = row[extra]
    return d


_MEMBERSHIP_SELECT = (
    "SELECT m.id, m.email, m.scope_type, m.partner_id, m.product_id, m.role, "
    "  pa.name AS partner_name, pa.slug AS partner_slug, "
    "  pr.name AS product_name, pr.slug AS product_slug "
    "FROM admin_memberships m "
    "LEFT JOIN partners pa ON pa.id = m.partner_id "
    "LEFT JOIN products pr ON pr.id = m.product_id "
)


async def memberships_for(email: str) -> list[dict[str, Any]]:
    rows = await _fetch(
        _MEMBERSHIP_SELECT + "WHERE m.email = $1 ORDER BY m.id",
        email.strip().lower(),
    )
    return [_row_to_membership(r) for r in rows]


async def list_all_memberships() -> list[dict[str, Any]]:
    rows = await _fetch(_MEMBERSHIP_SELECT + "ORDER BY m.email, m.id")
    return [_row_to_membership(r) for r in rows]


async def add_membership(email: str, scope_type: str,
                         partner_id: Optional[int], product_id: Optional[int],
                         role: str) -> dict[str, Any]:
    """Upsert one (user, scope) membership — a repeat write updates the role."""
    email = email.strip().lower()
    existing = await _fetchval(
        "SELECT id FROM admin_memberships WHERE email = $1 AND scope_type = $2 "
        "AND COALESCE(partner_id, 0) = COALESCE($3, 0) "
        "AND COALESCE(product_id, 0) = COALESCE($4, 0)",
        email, scope_type, partner_id, product_id,
    )
    if existing is not None:
        await _execute(
            "UPDATE admin_memberships SET role = $2 WHERE id = $1", existing, role
        )
        mid = existing
    else:
        mid = await _fetchval(
            "INSERT INTO admin_memberships (email, scope_type, partner_id, product_id, role) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            email, scope_type, partner_id, product_id, role,
        )
    row = await _fetchrow(_MEMBERSHIP_SELECT + "WHERE m.id = $1", mid)
    return _row_to_membership(row)


async def get_membership(membership_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        _MEMBERSHIP_SELECT + "WHERE m.id = $1", membership_id
    )
    return _row_to_membership(row) if row else None


async def delete_membership(membership_id: int) -> bool:
    res = await _execute(
        "DELETE FROM admin_memberships WHERE id = $1", membership_id
    )
    return _affected(res) > 0


async def product_ids_for_partners(partner_ids: list[int]) -> list[int]:
    """All product ids under the given partners (for scope expansion)."""
    if not partner_ids:
        return []
    rows = await _fetch(
        "SELECT id FROM products WHERE partner_id = ANY($1::int[])", partner_ids
    )
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# admin_api_keys (machine credentials for the /admin API — external panels)
# ---------------------------------------------------------------------------
_API_KEY_COLS = ("id, name, token_hint, role, scope_type, partner_id, "
                 "product_id, active, created_by, created_at, last_used_at")


def _row_to_api_key(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "created_at", "last_used_at")
    return d


def _hash_api_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_admin_api_key(*, name: str, role: str, scope_type: str,
                               partner_id: Optional[int],
                               product_id: Optional[int],
                               created_by: Optional[str]
                               ) -> tuple[dict[str, Any], str]:
    """Mint a service key. Returns (row, PLAINTEXT token) — the token is shown
    exactly once at creation; only its SHA-256 hash is stored."""
    import secrets as _secrets
    token = "sak_" + _secrets.token_urlsafe(32)
    row = await _fetchrow(
        "INSERT INTO admin_api_keys "
        "(name, token_hash, token_hint, role, scope_type, partner_id, "
        " product_id, created_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        f"RETURNING {_API_KEY_COLS}",
        name, _hash_api_token(token), token[-4:], role, scope_type,
        partner_id, product_id, created_by,
    )
    return _row_to_api_key(row), token


async def list_admin_api_keys() -> list[dict[str, Any]]:
    rows = await _fetch(
        f"SELECT {_API_KEY_COLS} FROM admin_api_keys ORDER BY id")
    return [_row_to_api_key(r) for r in rows]


async def get_admin_api_key(key_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_API_KEY_COLS} FROM admin_api_keys WHERE id = $1", key_id)
    return _row_to_api_key(row) if row else None


async def get_admin_api_key_by_token(token: str) -> Optional[dict[str, Any]]:
    """Resolve an ACTIVE key row from a presented plaintext token (auth path).

    last_used_at is refreshed at most once a minute: an external panel polling
    with a key would otherwise rewrite the same tuple on every request (WAL +
    vacuum churn in the hot path of every keyed admin call).
    """
    h = _hash_api_token(token)
    row = await _fetchrow(
        f"SELECT {_API_KEY_COLS} FROM admin_api_keys "
        "WHERE token_hash = $1 AND active", h,
    )
    if row is None:
        return None
    import datetime as _dt
    if (row["last_used_at"] is None
            or (_dt.datetime.now(_dt.timezone.utc)
                - row["last_used_at"]).total_seconds() > 60):
        await _execute(
            "UPDATE admin_api_keys SET last_used_at = now() "
            "WHERE token_hash = $1", h)
    return _row_to_api_key(row)


async def update_admin_api_key(key_id: int, *, active: Optional[bool] = None,
                               name: Optional[str] = None
                               ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "UPDATE admin_api_keys SET active = COALESCE($2, active), "
        "name = COALESCE($3, name) WHERE id = $1 "
        f"RETURNING {_API_KEY_COLS}",
        key_id, active, name,
    )
    return _row_to_api_key(row) if row else None


async def delete_admin_api_key(key_id: int) -> bool:
    result = await _execute(
        "DELETE FROM admin_api_keys WHERE id = $1", key_id)
    return result.endswith("1")


# ---------------------------------------------------------------------------
# admin_users (named login accounts; password hash never leaves this module)
# ---------------------------------------------------------------------------
def _row_to_admin_user(row: asyncpg.Record, *, include_hash: bool = False) -> dict[str, Any]:
    """Serialize a user row for the API — the password hash is dropped by default."""
    d = dict(row)
    if not include_hash:
        d.pop("password_hash", None)
    _iso_fields(d, "created_at", "updated_at")
    return d


async def get_admin_user(email: str) -> Optional[dict[str, Any]]:
    """Fetch a user INCLUDING the password hash (login path only)."""
    row = await _fetchrow(
        "SELECT email, password_hash, role, active, created_at, updated_at "
        "FROM admin_users WHERE email = $1",
        email.strip().lower(),
    )
    return _row_to_admin_user(row, include_hash=True) if row else None


async def list_admin_users() -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT email, role, active, created_at, updated_at "
        "FROM admin_users ORDER BY email"
    )
    return [_row_to_admin_user(r) for r in rows]


async def create_admin_user(email: str, password_hash: str, role: str) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO admin_users (email, password_hash, role) "
        "VALUES ($1, $2, $3) "
        "RETURNING email, role, active, created_at, updated_at",
        email.strip().lower(), password_hash, role,
    )
    return _row_to_admin_user(row)


async def update_admin_user(email: str, *, role: Optional[str] = None,
                            active: Optional[bool] = None,
                            password_hash: Optional[str] = None
                            ) -> Optional[dict[str, Any]]:
    sets: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    if role is not None:
        args.append(role); sets.append(f"role = ${len(args)}")
    if active is not None:
        args.append(active); sets.append(f"active = ${len(args)}")
    if password_hash is not None:
        args.append(password_hash); sets.append(f"password_hash = ${len(args)}")
    args.append(email.strip().lower())
    row = await _fetchrow(
        f"UPDATE admin_users SET {', '.join(sets)} WHERE email = ${len(args)} "
        f"RETURNING email, role, active, created_at, updated_at",
        *args,
    )
    return _row_to_admin_user(row) if row else None


async def delete_admin_user(email: str) -> bool:
    res = await _execute(
        "DELETE FROM admin_users WHERE email = $1", email.strip().lower()
    )
    return _affected(res) > 0


# ---------------------------------------------------------------------------
# KB CRUD (admin management; reads still go through kb.py helpers)
# ---------------------------------------------------------------------------
async def list_topics_with_counts(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT t.id, t.product_id, t.slug, t.title, t.display_order, t.active, "
        "  COUNT(e.id) FILTER (WHERE e.active) AS entry_count "
        "FROM kb_topics t LEFT JOIN kb_entries e ON e.topic_id = t.id "
        "WHERE t.product_id = $1 "
        "GROUP BY t.id ORDER BY t.display_order, t.id",
        product_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        topic = _row_to_topic(r)
        topic["entry_count"] = r["entry_count"]
        out.append(topic)
    return out


async def get_kb_entry(topic_id: int) -> Optional[dict[str, Any]]:
    """The topic's single active KB entry, or None. One entry per topic."""
    row = await _fetchrow(
        "SELECT id, topic_id, content, active FROM kb_entries "
        "WHERE topic_id = $1 AND active ORDER BY id DESC LIMIT 1",
        topic_id,
    )
    return dict(row) if row else None


async def set_kb_content(topic_id: int, content: str) -> int:
    """Set the topic's KB text (one entry per topic). Updates the existing active
    entry in place, or inserts one when the topic has none. Returns the entry id.
    """
    async with _acquire() as conn:
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
                entry_id = existing
            else:
                row = await conn.fetchrow(
                    "INSERT INTO kb_entries (topic_id, content, active) "
                    "VALUES ($1, $2, TRUE) RETURNING id",
                    topic_id, content,
                )
                entry_id = row["id"]
    _invalidate_kb_cache()
    return entry_id


async def clear_kb_content(topic_id: int) -> bool:
    """Soft-delete the topic's active KB entry. Returns True if one was cleared."""
    res = await _execute(
        "UPDATE kb_entries SET active = FALSE WHERE topic_id = $1 AND active",
        topic_id,
    )
    _invalidate_kb_cache()
    return _affected(res) > 0


# ---------------------------------------------------------------------------
# Metrics / dashboard aggregation (raw rows; derived rates computed in metrics.py)
# ---------------------------------------------------------------------------
def _product_clause(product_ids: Optional[list[int]], args: list[Any]) -> str:
    """Append an `AND product_id = ANY($n)` filter when a product scope is set.

    `None` means no scope (global view — all products); an empty list means an
    admin with no accessible products, which must match nothing.
    """
    if product_ids is None:
        return ""
    args.append(product_ids)
    return f" AND product_id = ANY(${len(args)}::int[])"


def _scope_clauses(product_ids: Optional[list[int]],
                   args: list[Any]) -> tuple[str, str]:
    """Product scope for a query that also uses a scoped cost CTE.

    Appends the product filter ONCE and returns two clause strings referencing
    the SAME positional arg — one for the outer `chat_sessions s`, one for the
    CTE's `chat_sessions cs` — so the cost aggregate can be bounded to the same
    window+scope without double-binding the parameter. Empty strings when no
    scope is set (global view).
    """
    if product_ids is None:
        return "", ""
    args.append(product_ids)
    n = len(args)
    return (f" AND s.product_id = ANY(${n}::int[])",
            f" AND cs.product_id = ANY(${n}::int[])")


# ---------------------------------------------------------------------------
# AI-spend attribution (ai_interaction_logs.consumer / .source)
#
# Every dashboard that reports money reads the spend through these two
# expressions instead of guessing from `session_id IS NULL` — that guess charged
# the quality judge and the proactive agent to the photo-metadata bucket, and
# charged reviews of SUPPORT conversations to the Telegram dashboard.
#
# Rows written before the columns existed carry NULL and are classified here, at
# read time (no boot-time backfill scan over an unbounded log table): a legacy
# session-bound row was always a dialogue turn, and a legacy session-less one was
# a background call whose kind is no longer knowable ('legacy'). Those legacy
# background rows were all retention (photo metadata / agent decisions), which is
# exactly where the old dashboards counted them — so history keeps its totals and
# only the labelling gets honest going forward.
#
# Every query using them needs `LEFT JOIN chat_sessions s ON s.id = l.session_id`.
# ---------------------------------------------------------------------------
_LOG_SOURCE = ("COALESCE(l.source, CASE WHEN l.session_id IS NOT NULL "
               "THEN 'chat' ELSE 'legacy' END)")
_LOG_FACADE = "COALESCE(l.consumer, s.consumer)"
_LOG_IS_SUPPORT = f"{_LOG_FACADE} = 'web'"
_LOG_IS_RETENTION = (f"({_LOG_FACADE} = 'telegram' "
                     "OR (l.consumer IS NULL AND l.session_id IS NULL))")


async def overview_aggregates(dt_from: Any, dt_to: Any,
                              product_ids: Optional[list[int]] = None
                              ) -> dict[str, Any]:
    """Raw aggregate counters for the dashboard overview within [from, to).

    This is the SUPPORT (web-widget) dashboard: telegram/retention data is
    excluded end-to-end. Sessions filter `consumer <> 'telegram'`; the cost
    aggregate keeps only rows attributed to the support facade
    (`_LOG_IS_SUPPORT`) and reports the DIALOGUE spend as `cost_usd_total` —
    the number every per-session metric divides — with the AI judge's passes
    over support conversations broken out as `cost_review_usd`, so background
    spend never inflates the price of a chat. The retention side has its own
    `retention_overview` / `retention_timeseries`.
    """
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args)
    # The cost query joins chat_sessions, so its product filter must qualify the
    # log table; sess/ev keep the bare `scope` (one table each). Same $n param.
    scope_l = scope.replace("product_id", "l.product_id")
    sess = await _fetchrow(
        "SELECT "
        "  COUNT(*) AS sessions_total, "
        "  COUNT(*) FILTER (WHERE message_count > 0) AS sessions_engaged, "
        "  COUNT(*) FILTER (WHERE status = 'open' AND message_count > 0) AS sessions_open, "
        "  COUNT(*) FILTER (WHERE escalated) AS sessions_escalated, "
        "  COALESCE(AVG(message_count) FILTER (WHERE message_count > 0), 0) "
        "    AS avg_messages_per_session "
        "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2 "
        f"  AND consumer <> 'telegram'{scope}",
        *args,
    )
    cost = await _fetchrow(
        "SELECT "
        f"  COALESCE(SUM(l.cost_usd) FILTER (WHERE {_LOG_SOURCE} = 'chat'), 0) "
        "    AS cost_usd_total, "
        f"  COALESCE(SUM(l.cached_in) FILTER (WHERE {_LOG_SOURCE} = 'chat'), 0) "
        "    AS cached_in_total, "
        f"  COALESCE(SUM(l.tokens_in) FILTER (WHERE {_LOG_SOURCE} = 'chat'), 0) "
        "    AS tokens_in_total, "
        "  COUNT(DISTINCT l.session_id) AS sessions_with_ai, "
        "  COUNT(*) AS ai_calls_total, "
        "  COALESCE(AVG(l.latency_ms) FILTER "
        "    (WHERE l.ok AND l.latency_ms IS NOT NULL), 0) AS avg_latency_ms, "
        "  COUNT(*) FILTER (WHERE NOT l.ok) AS failed_calls, "
        # The AI judge's passes over SUPPORT conversations: they log with
        # session_id NULL, so they belong to no chat turn — a separate line,
        # never folded into the dialogue cost the per-session metrics divide.
        f"  COALESCE(SUM(l.cost_usd) FILTER (WHERE {_LOG_SOURCE} = 'review'), 0) "
        "    AS cost_review_usd, "
        f"  COUNT(*) FILTER (WHERE {_LOG_SOURCE} = 'review') AS review_calls "
        "FROM ai_interaction_logs l "
        "LEFT JOIN chat_sessions s ON s.id = l.session_id "
        f"WHERE {_LOG_IS_SUPPORT} "
        f"  AND l.created_at >= $1 AND l.created_at < $2{scope_l}",
        *args,
    )
    ev = await _fetch(
        "SELECT type, COUNT(*) AS n FROM admin_events "
        f"WHERE created_at >= $1 AND created_at < $2{scope} GROUP BY type",
        *args,
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
        # Sessions that actually made >= 1 OpenAI call (distinct session_id in
        # ai_interaction_logs). This is the precise denominator for average cost:
        # greeting-only "zero" sessions (chat opened, canned greeting shown, no API
        # call) never appear here, so they don't dilute cost-per-session.
        "sessions_with_ai": int(cost["sessions_with_ai"]),
        # AI-API health: total OpenAI calls and the average end-to-end latency of
        # the successful ones (ms). Failed calls carry no meaningful latency, so
        # they are excluded from the average but still counted in ai_calls_total.
        "ai_calls_total": int(cost["ai_calls_total"]),
        "avg_latency_ms": float(cost["avg_latency_ms"]),
        "failed_calls": int(cost["failed_calls"]),
        # Background spend on THIS facade, kept apart from the dialogue cost:
        # the AI judge reading finished SUPPORT conversations. A review of a
        # Telegram chat is retention spend and shows up there instead.
        "cost_review_usd": float(cost["cost_review_usd"]),
        "review_calls": int(cost["review_calls"]),
        "events": events,
    }


async def timeseries(metric: str, dt_from: Any, dt_to: Any,
                     bucket: str = "day",
                     product_ids: Optional[list[int]] = None
                     ) -> list[dict[str, Any]]:
    """Per-bucket series for sessions | cost | cost_per_session | escalation_rate.

    SUPPORT (web-widget) only — telegram/retention is excluded the same way as
    `overview_aggregates`. The two cost metrics are DIALOGUE spend
    (`_LOG_IS_SUPPORT` + source 'chat'): background passes over support
    conversations (the AI judge) are real support spend but belong to no
    session, so folding them in would silently inflate "cost per session" — they
    are reported as their own overview figure instead. Session metrics filter
    `consumer <> 'telegram'`. Telegram spend has its own series in
    `retention_timeseries`.
    """
    trunc = "day" if bucket not in ("hour", "day", "week", "month") else bucket
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args)
    # Cost metrics join chat_sessions, so their product filter qualifies the log
    # table; session metrics keep the bare `scope`. Same positional param.
    scope_l = scope.replace("product_id", "l.product_id")
    if metric == "cost":
        rows = await _fetch(
            f"SELECT date_trunc('{trunc}', l.created_at) AS bucket, "
            "COALESCE(SUM(l.cost_usd), 0) AS value "
            "FROM ai_interaction_logs l "
            "LEFT JOIN chat_sessions s ON s.id = l.session_id "
            f"WHERE {_LOG_IS_SUPPORT} AND {_LOG_SOURCE} = 'chat' "
            f"  AND l.created_at >= $1 AND l.created_at < $2{scope_l} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
    elif metric == "cost_per_session":
        # Average spend per session per bucket: total cost / distinct sessions that
        # had at least one OpenAI call in the bucket. The "average price per day".
        rows = await _fetch(
            f"SELECT date_trunc('{trunc}', l.created_at) AS bucket, "
            "COALESCE(SUM(l.cost_usd), 0) AS cost, "
            "COUNT(DISTINCT l.session_id) AS sessions "
            "FROM ai_interaction_logs l "
            "LEFT JOIN chat_sessions s ON s.id = l.session_id "
            f"WHERE {_LOG_IS_SUPPORT} AND {_LOG_SOURCE} = 'chat' "
            f"  AND l.created_at >= $1 AND l.created_at < $2{scope_l} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
        return [
            {"bucket": r["bucket"].isoformat(),
             "value": (float(r["cost"]) / r["sessions"]) if r["sessions"] else 0.0}
            for r in rows
        ]
    elif metric == "escalation_rate":
        rows = await _fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COUNT(*) FILTER (WHERE message_count > 0) AS engaged, "
            "COUNT(*) FILTER (WHERE escalated) AS escalated "
            "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2 "
            f"  AND consumer <> 'telegram'{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
        return [
            {"bucket": r["bucket"].isoformat(),
             "value": (r["escalated"] / r["engaged"]) if r["engaged"] else 0.0}
            for r in rows
        ]
    else:  # sessions
        rows = await _fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COUNT(*) AS value "
            "FROM chat_sessions WHERE created_at >= $1 AND created_at < $2 "
            f"  AND consumer <> 'telegram'{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
    return [{"bucket": r["bucket"].isoformat(), "value": float(r["value"])}
            for r in rows]


async def ai_cost_timeseries(dt_from: Any, dt_to: Any,
                             product_ids: Optional[list[int]] = None
                             ) -> list[dict[str, Any]]:
    """Daily OpenAI spend across EVERY call in scope, split by `source`.

    The platform-wide cost view (System → Settings, under the AI-model group):
    unlike the support/retention dashboards it does NOT split by facade — it
    sums both bots plus every background pass, so the buckets add up to the
    whole OpenAI bill for the scope. Split follows the row's own attribution
    label (`_LOG_SOURCE`): chat = dialogue turns (widget + Telegram), agent =
    the proactive agent, media = photo/video cataloguing, review = the AI
    judge, legacy = session-less rows written before attribution shipped.
    `product_ids` follows the dashboard scope convention: None = all
    accessible, [] = none.
    """
    args: list[Any] = [dt_from, dt_to]
    pid = _pid_where(product_ids, args, "l.product_id")
    rows = await _fetch(
        "SELECT date_trunc('day', l.created_at) AS day, "
        "  COALESCE(SUM(l.cost_usd) FILTER "
        f"    (WHERE {_LOG_SOURCE} = 'chat'), 0) AS chat, "
        "  COALESCE(SUM(l.cost_usd) FILTER "
        f"    (WHERE {_LOG_SOURCE} = 'agent'), 0) AS agent, "
        "  COALESCE(SUM(l.cost_usd) FILTER "
        f"    (WHERE {_LOG_SOURCE} = 'media'), 0) AS media, "
        "  COALESCE(SUM(l.cost_usd) FILTER "
        f"    (WHERE {_LOG_SOURCE} = 'review'), 0) AS review, "
        "  COALESCE(SUM(l.cost_usd) FILTER "
        f"    (WHERE {_LOG_SOURCE} = 'legacy'), 0) AS legacy, "
        "  COALESCE(SUM(l.cost_usd), 0) AS total, "
        "  COUNT(*) AS calls "
        "FROM ai_interaction_logs l "
        f"WHERE {pid} AND l.created_at >= $1 AND l.created_at < $2 "
        "GROUP BY 1 ORDER BY 1",
        *args,
    )
    return [
        {"date": str(r["day"])[:10],
         "cost_chat_usd": round(float(r["chat"]), 4),
         "cost_agent_usd": round(float(r["agent"]), 4),
         "cost_media_usd": round(float(r["media"]), 4),
         "cost_review_usd": round(float(r["review"]), 4),
         "cost_legacy_usd": round(float(r["legacy"]), 4),
         "cost_usd": round(float(r["total"]), 4),
         "calls": int(r["calls"])}
        for r in rows
    ]


def _cost_cte(scope_cte: str, *, support_only: bool = True) -> str:
    """`costs AS (...)` — the windowed, product-scoped per-session cost CTE.

    Scoped to the same window+product as the outer query (via a join to
    chat_sessions) instead of aggregating the entire, unbounded
    ai_interaction_logs table on every dashboard load: per-session totals are
    identical (the outer LEFT JOIN only uses in-window sessions anyway), the
    scan is O(sessions in window) and uses the created_at/product indexes.
    `support_only` adds the dashboard's telegram exclusion.
    """
    telegram = "    AND cs.consumer <> 'telegram' " if support_only else ""
    return (
        "costs AS ("
        "  SELECT l.session_id, SUM(l.cost_usd) AS cost_usd_total "
        "  FROM ai_interaction_logs l "
        "  JOIN chat_sessions cs ON cs.id = l.session_id "
        f"  WHERE cs.created_at >= $1 AND cs.created_at < $2{scope_cte} "
        f"{telegram}"
        "  GROUP BY l.session_id"
        ")"
    )


async def by_topic(dt_from: Any, dt_to: Any,
                   product_ids: Optional[list[int]] = None) -> list[dict[str, Any]]:
    args: list[Any] = [dt_from, dt_to]
    scope, scope_cte = _scope_clauses(product_ids, args)
    rows = await _fetch(
        f"WITH {_cost_cte(scope_cte)} "
        "SELECT t.slug, t.title, "
        # Count only engaged sessions (>= 1 message): greeting-only "zero" sessions
        # had no OpenAI call and must not dilute the per-topic counts or rates.
        "  COUNT(s.id) FILTER (WHERE s.message_count > 0) AS sessions, "
        "  COUNT(s.id) FILTER (WHERE s.escalated AND s.message_count > 0) AS escalated, "
        "  COALESCE(AVG(s.message_count) FILTER (WHERE s.message_count > 0), 0) AS avg_messages, "
        "  COALESCE(SUM(costs.cost_usd_total), 0) AS cost_usd_total "
        "FROM chat_sessions s JOIN kb_topics t ON t.id = s.topic_id "
        "LEFT JOIN costs ON costs.session_id = s.id "
        f"WHERE s.created_at >= $1 AND s.created_at < $2{scope} "
        "  AND s.consumer <> 'telegram' "
        "GROUP BY t.slug, t.title ORDER BY sessions DESC",
        *args,
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
            "cost_usd_total": round(float(r["cost_usd_total"]), 6),
        })
    return out


# The language a session's CONVERSATION ran in, for every admin-facing report.
# `chat_sessions.lang` is the BROWSER locale resolved once at session create and
# deliberately never overwritten; `conv_lang` is the language the player actually
# drifted to (persisted per turn from the model's [[LANG:xx]] sentinel). Reports
# must key on the latter, falling back to the locale when the conversation never
# drifted — otherwise a Russian-speaking player on an en-US browser is reported
# as English. Kept as one constant so the session list, the unresolved queue and
# the by-language breakdown can never disagree about what "lang" means.
_CONV_LANG_SQL = "COALESCE(s.conv_lang, s.lang)"


async def by_language(dt_from: Any, dt_to: Any,
                      product_ids: Optional[list[int]] = None
                      ) -> list[dict[str, Any]]:
    args: list[Any] = [dt_from, dt_to]
    scope, scope_cte = _scope_clauses(product_ids, args)
    rows = await _fetch(
        f"WITH {_cost_cte(scope_cte)} "
        # The language the conversation actually ran in: `conv_lang` (the sticky
        # answer language the player drifted to) when set, else the browser
        # locale stored at session create. Grouping on `lang` alone reported the
        # BROWSER mix — a player on an en-US browser who writes in Russian was
        # counted as English, so the per-language view measured locales, not
        # languages.
        f"SELECT COALESCE({_CONV_LANG_SQL}, 'unknown') AS lang, "
        # Engaged sessions only — exclude greeting-only "zero" sessions (no OpenAI
        # call) so the per-language counts and escalation rates aren't diluted.
        "  COUNT(*) FILTER (WHERE s.message_count > 0) AS sessions, "
        "  COUNT(*) FILTER (WHERE s.escalated AND s.message_count > 0) AS escalated, "
        "  COALESCE(SUM(costs.cost_usd_total), 0) AS cost_usd_total "
        "FROM chat_sessions s LEFT JOIN costs ON costs.session_id = s.id "
        f"WHERE s.created_at >= $1 AND s.created_at < $2{scope} "
        "  AND s.consumer <> 'telegram' "
        f"GROUP BY COALESCE({_CONV_LANG_SQL}, 'unknown') ORDER BY sessions DESC",
        *args,
    )
    return [
        {"lang": r["lang"], "sessions": r["sessions"], "escalated": r["escalated"],
         "escalation_rate": (r["escalated"] / r["sessions"]) if r["sessions"] else 0.0,
         "cost_usd_total": round(float(r["cost_usd_total"]), 6)}
        for r in rows
    ]


async def list_sessions(dt_from: Any, dt_to: Any, *, topic: Optional[str] = None,
                        lang: Optional[str] = None, status: Optional[str] = None,
                        escalated: Optional[bool] = None, q: Optional[str] = None,
                        min_messages: Optional[int] = None,
                        product_ids: Optional[list[int]] = None,
                        page: int = 1) -> dict[str, Any]:
    # Telegram (retention-bot) chats live in the Retention section of the admin
    # (list_retention_sessions) — the support Conversations list never mixes
    # them in.
    where = ["s.created_at >= $1", "s.created_at < $2",
             "s.consumer <> 'telegram'"]
    args: list[Any] = [dt_from, dt_to]
    if product_ids is not None:
        args.append(product_ids); where.append(f"s.product_id = ANY(${len(args)}::int[])")
    if topic:
        args.append(topic); where.append(f"t.slug = ${len(args)}")
    if lang:
        # Filter on the conversation language, matching the column the list
        # shows and the by-language breakdown (see _CONV_LANG_SQL).
        args.append(lang); where.append(f"{_CONV_LANG_SQL} = ${len(args)}")
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
    if min_messages is not None:
        args.append(min_messages); where.append(f"s.message_count >= ${len(args)}")
    where_sql = " AND ".join(where)
    total = await _fetchval(
        f"SELECT COUNT(*) FROM chat_sessions s "
        f"LEFT JOIN kb_topics t ON t.id = s.topic_id WHERE {where_sql}",
        *args,
    )
    page = max(page, 1)
    page_size = 25
    args2 = args + [page_size, (page - 1) * page_size]
    rows = await _fetch(
        f"SELECT s.id, {_CONV_LANG_SQL} AS lang, s.lang AS ui_lang, "
        f"  s.status, s.escalated, s.message_count, "
        f"  s.created_at, s.updated_at, t.slug AS topic, "
        f"  s.product_id, p.name AS product_name, "
        f"  COALESCE(c.cost_usd_total, 0) AS cost_usd_total "
        f"FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        f"LEFT JOIN products p ON p.id = s.product_id "
        # Bound the cost aggregate to the same date window ($1/$2) rather than
        # scanning the whole unbounded ai_interaction_logs — per-session totals
        # for the (windowed, paginated) rows shown are unchanged.
        f"LEFT JOIN (SELECT l.session_id, SUM(l.cost_usd) AS cost_usd_total "
        f"           FROM ai_interaction_logs l "
        f"           JOIN chat_sessions cs ON cs.id = l.session_id "
        f"           WHERE cs.created_at >= $1 AND cs.created_at < $2 "
        f"           GROUP BY l.session_id) c "
        f"  ON c.session_id = s.id "
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
        d["cost_usd_total"] = round(float(d.get("cost_usd_total") or 0), 6)
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def session_detail(session_id: str) -> Optional[dict[str, Any]]:
    session = await get_session(session_id)
    if session is None:
        return None
    msgs = await _fetch(
        "SELECT role, content, lang, model, key_used, tokens_in, tokens_out, "
        "cached_in, cost_usd, ping_context, created_at FROM chat_messages "
        "WHERE session_id = $1 ORDER BY id ASC",
        session_id,
    )
    logs = await _fetch(
        "SELECT model, key_used, tokens_in, tokens_out, cached_in, cost_usd, "
        "latency_ms, ok, error, created_at FROM ai_interaction_logs "
        "WHERE session_id = $1 ORDER BY id ASC",
        session_id,
    )
    # Topic-switch markers: a cross-topic routing turn suppresses its (ungrounded)
    # answer and persists no chat_messages row, so its detect-call cost would look
    # orphaned in the transcript. Returning these lets the admin view interleave a
    # "switched X -> Y" marker (with that call's cost) into the timeline, so the
    # path is traceable and the per-step costs add up to cost_usd_total.
    events = await _fetch(
        "SELECT type, payload, created_at FROM admin_events "
        "WHERE session_id = $1 AND type = 'topic_switch' ORDER BY id ASC",
        session_id,
    )
    # Media (photos AND videos) delivered in this (Telegram retention) session,
    # so the transcript can render the sent item inline alongside its caption
    # message. `media_type` + `storage_ref` ride along because the admin preview
    # renders the two kinds differently: a video shows its extracted poster
    # frame (?poster=1), not the video binary — without the type the transcript
    # requested a video as an <img> and painted a broken image.
    photos = await _fetch(
        "SELECT v.photo_id, v.viewed_at, p.description, p.stage, p.level_min, "
        "p.media_type, p.storage_ref "
        "FROM retention_photo_views v "
        "JOIN retention_photos p ON p.id = v.photo_id "
        "WHERE v.session_id = $1 ORDER BY v.id ASC",
        session_id,
    )
    def _msg(r):
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        d["cost_usd"] = float(d["cost_usd"]) if d["cost_usd"] is not None else None
        return d
    def _event(r):
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {
            "type": r["type"],
            "payload": payload or {},
            "created_at": r["created_at"].isoformat(),
        }
    def _photo(r):
        return {
            "photo_id": r["photo_id"],
            "description": r["description"],
            "stage": r["stage"],
            "level_min": r["level_min"],
            "media_type": r["media_type"] or "photo",
            "storage_ref": r["storage_ref"],
            "created_at": r["viewed_at"].isoformat(),
        }
    # Cost is summed from ai_interaction_logs (the canonical OpenAI-spend source,
    # invariant §4) — NOT from chat_messages. A routing-only turn logs its detect
    # call to ai_interaction_logs but persists no chat_messages row, so summing
    # from msgs would undercount and disagree with the Sessions list / overview /
    # by-topic aggregations, which all sum ai_interaction_logs.
    cost_total = sum((float(r["cost_usd"]) for r in logs if r["cost_usd"]), 0.0)
    # Serialize session timestamps.
    for ts in ("created_at", "updated_at"):
        if session.get(ts) is not None and not isinstance(session[ts], str):
            session[ts] = session[ts].isoformat()
    return {
        "session": session,
        "messages": [_msg(r) for r in msgs],
        "logs": [_msg(r) for r in logs],
        "events": [_event(r) for r in events],
        "photos": [_photo(r) for r in photos],
        "cost_usd_total": round(cost_total, 6),
    }


async def _purge_retention_player(conn, product_id: Optional[int],
                                  tg_user_id: Optional[int]) -> None:
    """Delete a Telegram player's whole analytics footprint under one product:
    the seen-photo ledger and the ping ledger (both FK NOT NULL to
    `retention_users`, so they go first), then the `retention_users` row itself.

    Keyed by (product_id, tg_user_id) — the durable player identity — NOT by the
    session, so it fires even when the deleted conversation is an old rolled-over
    one whose `retention_users.session_id` already points at a newer session.
    Product-level historical counters (deeplinks minted / /start redemptions —
    logged session-less, not attributable to one player) are intentionally left
    untouched.
    """
    if product_id is None or tg_user_id is None:
        return
    ids = [r["id"] for r in await conn.fetch(
        "SELECT id FROM retention_users WHERE product_id = $1 AND tg_user_id = $2",
        product_id, tg_user_id,
    )]
    if not ids:
        return
    await conn.execute(
        "DELETE FROM retention_photo_views WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    await conn.execute(
        "DELETE FROM retention_pings WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    # The attribution ledger is per-player analytics like the two above — it
    # goes with the player (leaving it behind would keep a deleted player's
    # touches in the effectiveness cuts).
    await conn.execute(
        "DELETE FROM retention_outcomes WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    # The agent's decision ledger references the player with a NULLABLE FK —
    # detach (the audit rows themselves stay: they are product-level history,
    # and deleting them would rewrite today's budget / cooldown state).
    #
    # The send queue and the journey enrollments carry the SAME nullable FK and
    # were missed: with no ON DELETE clause on either, the DELETE below aborted
    # the whole transaction on a foreign-key violation and the admin's delete
    # returned 500 having removed nothing. Both are operational state about a
    # player who is going away, so they are detached the same way — an orphan
    # delivery is closed out rather than left claimable, and an orphan
    # enrollment can no longer resolve a player to touch.
    await conn.execute(
        "UPDATE retention_v2_decisions SET retention_user_id = NULL "
        "WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    await conn.execute(
        "UPDATE retention_deliveries "
        "SET retention_user_id = NULL, status = 'suppressed', "
        "    permanent_fail = TRUE, locked_until = NULL, "
        "    next_attempt_at = NULL, "
        "    fail_reason = COALESCE(fail_reason, 'player deleted'), "
        "    updated_at = now() "
        "WHERE retention_user_id = ANY($1::bigint[]) "
        "  AND status IN ('queued', 'sending', 'failed')",
        ids,
    )
    await conn.execute(
        "UPDATE retention_deliveries SET retention_user_id = NULL "
        "WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    await conn.execute(
        "UPDATE retention_journey_enrollments "
        "SET retention_user_id = NULL, next_step_at = NULL, "
        "    status = CASE WHEN status = 'active' THEN 'exited_terminal' "
        "                  ELSE status END, "
        "    exit_reason = COALESCE(exit_reason, 'player deleted'), "
        "    updated_at = now() "
        "WHERE retention_user_id = ANY($1::bigint[])",
        ids,
    )
    await conn.execute(
        "DELETE FROM retention_users WHERE id = ANY($1::bigint[])", ids
    )


async def delete_session(session_id: str) -> bool:
    """Hard-delete a chat/retention session and everything hanging off it.

    `chat_messages.session_id` is a NOT NULL FK without ON DELETE CASCADE, and
    `retention_users.session_id` / `chat_sessions.prev_session_id` reference the
    row too, so the dependents are cleared in one transaction before the session
    row itself. `ai_interaction_logs` / `admin_events` carry a bare (unconstrained)
    `session_id`, so their rows are removed by value. Used by the admin
    Conversations / Unresolved / Telegram-chats delete controls.

    For a Telegram/retention conversation the delete also **purges the linked
    player** (identity, seen photos, pings) via `_purge_retention_player`, so the
    player disappears from the retention dashboards too — a retention session is
    just one facade over a durable player, and leaving the player behind made a
    deleted conversation keep showing up in analytics.
    """
    async with _acquire() as conn:
        async with conn.transaction():
            srow = await conn.fetchrow(
                "SELECT consumer, product_id, tg_user_id "
                "FROM chat_sessions WHERE id = $1", session_id
            )
            if srow is None:
                return False
            # Detach references that would otherwise block the delete.
            await conn.execute(
                "UPDATE retention_users SET session_id = NULL WHERE session_id = $1",
                session_id,
            )
            await conn.execute(
                "UPDATE chat_sessions SET prev_session_id = NULL "
                "WHERE prev_session_id = $1",
                session_id,
            )
            await conn.execute(
                "DELETE FROM chat_messages WHERE session_id = $1", session_id
            )
            await conn.execute(
                "DELETE FROM ai_interaction_logs WHERE session_id = $1", session_id
            )
            await conn.execute(
                "DELETE FROM admin_events WHERE session_id = $1", session_id
            )
            # Session-keyed analytics: the judge's verdict and any attribution
            # rows opened by turns of this conversation.
            await conn.execute(
                "DELETE FROM conversation_reviews WHERE session_id = $1",
                session_id
            )
            await conn.execute(
                "DELETE FROM retention_outcomes WHERE session_id = $1",
                session_id
            )
            await conn.execute(
                "DELETE FROM chat_sessions WHERE id = $1", session_id
            )
            if srow["consumer"] == "telegram":
                await _purge_retention_player(
                    conn, srow["product_id"], srow["tg_user_id"])
    return True


# Per-topic session cap for the Unresolved queue payload (the group count
# stays the full number; only the listed rows are bounded).
_UNRESOLVED_PER_TOPIC = 100


async def unresolved_by_topic(dt_from: Any, dt_to: Any,
                              product_ids: Optional[list[int]] = None
                              ) -> list[dict[str, Any]]:
    """Open or escalated engaged sessions grouped by topic.

    The admin page is an operational queue for conversations that still need KB
    or human attention, so it includes both escalated sessions and abandoned open
    chats with at least one user turn. Resolved sessions are excluded — including
    a soft-escalated chat the player later finished (escalated=TRUE stays set,
    but status='resolved' means nobody needs to triage it).

    Each topic group returns at most `_UNRESOLVED_PER_TOPIC` newest sessions
    (the group's `count` is still the FULL count) so a busy deployment with a
    wide date range can't materialize thousands of rows in one JSON payload.
    """
    args: list[Any] = [dt_from, dt_to]
    scope, scope_cte = _scope_clauses(product_ids, args)
    rows = await _fetch(
        f"WITH {_cost_cte(scope_cte, support_only=False)}"
        ", unresolved AS ("
        "  SELECT COALESCE(t.slug, 'unknown') AS topic, "
        "    COALESCE(t.title, '{}'::jsonb) AS title, "
        f"    s.id AS session_id, {_CONV_LANG_SQL} AS lang, s.lang AS ui_lang, "
        "    s.status, s.escalated, s.message_count, "
        "    s.created_at, s.updated_at, "
        "    COALESCE(costs.cost_usd_total, 0) AS cost_usd_total, "
        "    ROW_NUMBER() OVER (PARTITION BY COALESCE(t.slug, 'unknown') "
        "                       ORDER BY s.created_at DESC) AS rn, "
        "    COUNT(*) OVER (PARTITION BY COALESCE(t.slug, 'unknown')) AS full_count "
        "  FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        "  LEFT JOIN costs ON costs.session_id = s.id "
        "  WHERE s.message_count > 0 "
        "    AND s.consumer <> 'telegram' "
        "    AND s.status <> 'resolved' "
        "    AND (s.escalated OR s.status = 'open') "
        f"    AND s.created_at >= $1 AND s.created_at < $2{scope} "
        ") "
        "SELECT u.*, "
        "  (SELECT m.content FROM chat_messages m "
        "    WHERE m.session_id = u.session_id AND m.role = 'user' "
        "    ORDER BY m.id ASC LIMIT 1) AS first_message "
        f"FROM unresolved u WHERE u.rn <= {_UNRESOLVED_PER_TOPIC} "
        "ORDER BY u.topic, u.created_at DESC",
        *args,
    )
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        topic = r["topic"]
        g = groups.setdefault(topic, {
            "topic": topic,
            "title": _json_value(r["title"]) if r["title"] is not None else {},
            "count": int(r["full_count"]),
            "sessions": [],
        })
        g["sessions"].append({
            "session_id": str(r["session_id"]),
            "lang": r["lang"],
            "ui_lang": r["ui_lang"],
            "status": r["status"],
            "escalated": r["escalated"],
            "message_count": r["message_count"],
            "first_message": r["first_message"],
            "cost_usd_total": round(float(r["cost_usd_total"] or 0), 6),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        })
    return sorted(groups.values(), key=lambda x: x["count"], reverse=True)


# ===========================================================================
# RETENTION / TELEGRAM helpers (all product-scoped)
# ===========================================================================
_RETENTION_PROFILE_FIELDS = (
    "full_name", "email", "activation_status", "country",
    "balance", "vip_level", "registration_date",
)


def _row_to_retention_user(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if d.get("id") is not None:
        d["id"] = int(d["id"])
    if d.get("session_id") is not None:
        d["session_id"] = str(d["session_id"])
    for jf in ("rg_signals", "channel_prefs"):
        if isinstance(d.get(jf), str):
            try:
                d[jf] = json.loads(d[jf])
            except ValueError:
                d[jf] = None
    for nf in ("rfm_monetary", "total_deposit_lifetime"):
        if d.get(nf) is not None:
            d[nf] = float(d[nf])
    _iso_fields(d, "profile_updated_at", "last_stage_advance_at", "last_active_at",
               "created_at", "updated_at", "photos_day", "last_login_at",
               "last_played_at", "last_deposit_at", "last_ping_at", "pings_day",
               "rg_status_until", "rg_updated_at", "dormancy_since",
               "score_computed_at", "deposit_initiated_at")
    return d


_RU_COLS = (
    "id, product_id, tg_user_id, tg_username, player_id, entry_type, "
    "full_name, email, activation_status, country, balance, vip_level, "
    "registration_date, profile_source, profile_updated_at, unlocked_stage, "
    "last_stage_advance_at, assigned_manager_id, subscribed, meaningful_msgs, "
    "msgs_since_photo, photos_day, photos_sent_today, conv_lang, session_id, "
    "last_active_at, last_login_at, last_played_at, last_deposit_at, "
    "pings_muted, unreachable, last_ping_at, pings_day, pings_sent_today, "
    "holdout_group, holdout_salt, "
    "rg_status, rg_status_source, rg_status_until, rg_block_level, "
    "rg_signals, marketing_consent, rg_updated_at, tz, "
    "dormancy_cohort, dormancy_since, rfm_recency, rfm_frequency, "
    "rfm_monetary, rfm_score, value_tier, vip_segment, "
    "total_deposit_lifetime, score_computed_at, deposit_initiated_at, "
    "email_opt_in, email_verified, push_opt_in, push_available, "
    "in_app_available, sms_opt_in, channel_prefs, "
    "created_at, updated_at"
)

# Casino-side activity timestamps accepted from the partner feed (push webhook /
# Player-API pull) alongside the TEXT profile snapshot. ISO-8601 strings are
# parsed; unparsable values are dropped (never break a partial update).
_RETENTION_ACTIVITY_FIELDS = ("last_login_at", "last_played_at", "last_deposit_at")


def _as_ts(value: Any) -> Optional[Any]:
    """Coerce an incoming activity value to a tz-aware datetime (or None)."""
    import datetime as _dt
    if value is None or isinstance(value, _dt.datetime):
        return value
    try:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


async def get_retention_user(product_id: int, tg_user_id: int
                             ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_RU_COLS} FROM retention_users "
        f"WHERE product_id = $1 AND tg_user_id = $2",
        product_id, tg_user_id,
    )
    return _row_to_retention_user(row) if row else None


async def upsert_retention_user(product_id: int, tg_user_id: int, *,
                                tg_username: Optional[str] = None,
                                player_id: Optional[str] = None,
                                entry_type: str = "retention",
                                profile: Optional[dict[str, Any]] = None,
                                profile_source: str = "handshake"
                                ) -> dict[str, Any]:
    """Create/refresh the tg<->player link + profile snapshot (last-wins).

    Called on every nonce redemption. Profile fields present in `profile`
    overwrite the snapshot; absent ones are left untouched (partial update).
    """
    profile = profile or {}
    # Build the dynamic SET for the profile fields actually supplied.
    prof_cols = [f for f in _RETENTION_PROFILE_FIELDS if f in profile]
    existing = await get_retention_user(product_id, tg_user_id)
    if existing is None:
        cols = ["product_id", "tg_user_id", "tg_username", "player_id",
                "entry_type", "profile_source", "profile_updated_at"]
        vals: list[Any] = [product_id, tg_user_id, tg_username, _as_text(player_id),
                           entry_type, profile_source]
        placeholders = ["$1", "$2", "$3", "$4", "$5", "$6", "now()"]
        for f in prof_cols:
            vals.append(_as_text(profile.get(f)))
            cols.append(f)
            placeholders.append(f"${len(vals)}")
        # ON CONFLICT: two concurrent /start redemptions (double-tap, Telegram
        # redelivery) race the SELECT-then-INSERT — fall through to the UPDATE
        # branch below instead of raising UniqueViolationError.
        row = await _fetchrow(
            f"INSERT INTO retention_users ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) "
            "ON CONFLICT (product_id, tg_user_id) DO NOTHING "
            f"RETURNING {_RU_COLS}",
            *vals,
        )
        if row is not None:
            return _row_to_retention_user(row)
        existing = await get_retention_user(product_id, tg_user_id)
    sets = ["tg_username = COALESCE($3, tg_username)",
            "entry_type = $4",
            "profile_source = $5",
            "profile_updated_at = now()",
            "last_active_at = now()",
            "updated_at = now()"]
    args: list[Any] = [product_id, tg_user_id, tg_username, entry_type,
                       profile_source]
    if player_id is not None:
        args.append(_as_text(player_id))
        sets.append(f"player_id = ${len(args)}")
    for f in prof_cols:
        args.append(_as_text(profile.get(f)))
        sets.append(f"{f} = ${len(args)}")
    row = await _fetchrow(
        f"UPDATE retention_users SET {', '.join(sets)} "
        f"WHERE product_id = $1 AND tg_user_id = $2 RETURNING {_RU_COLS}",
        *args,
    )
    return _row_to_retention_user(row)


async def update_retention_profile(product_id: int, player_id: str,
                                   profile: dict[str, Any],
                                   profile_source: str = "push") -> int:
    """Partial profile update by player_id (Player-API pull / push). Returns rows.

    Accepts the TEXT snapshot fields plus the casino activity timestamps
    (last_login_at / last_played_at / last_deposit_at, ISO-8601); an activity
    value that doesn't parse is dropped rather than failing the update.
    """
    prof_cols = [f for f in _RETENTION_PROFILE_FIELDS if f in profile]
    act_cols = [f for f in _RETENTION_ACTIVITY_FIELDS
                if f in profile and _as_ts(profile.get(f)) is not None]
    if not prof_cols and not act_cols:
        return 0
    sets = ["profile_source = $3", "profile_updated_at = now()",
            "updated_at = now()"]
    args: list[Any] = [product_id, player_id, profile_source]
    for f in prof_cols:
        args.append(_as_text(profile.get(f)))
        sets.append(f"{f} = ${len(args)}")
    for f in act_cols:
        args.append(_as_ts(profile.get(f)))
        n = len(args)
        # Activity timestamps are forward-only + future-clamped, matching the
        # event bridge (touch_retention_activity's GREATEST + _validate_event's
        # future clamp). Without this the lazy Player-API pull / push webhook could
        # REWIND a fresher event-set value with a stale snapshot (a no_deposit rule
        # then pings a player who deposited today) or PIN a future timestamp from a
        # skewed partner clock. now() clamps the future; GREATEST keeps forward-only.
        sets.append(
            f"{f} = GREATEST(COALESCE({f}, LEAST(${n}::timestamptz, now())), "
            f"LEAST(${n}::timestamptz, now()))")
    result = await _execute(
        f"UPDATE retention_users SET {', '.join(sets)} "
        f"WHERE product_id = $1 AND player_id = $2",
        *args,
    )
    return _affected(result)  # asyncpg returns "UPDATE <n>"


async def set_retention_subscribed(rid: int, subscribed: bool) -> None:
    await _execute(
        "UPDATE retention_users SET subscribed = $2, last_active_at = now(), "
        "updated_at = now() WHERE id = $1",
        rid, subscribed,
    )


async def set_retention_session(rid: int, session_id: str) -> None:
    await _execute(
        "UPDATE retention_users SET session_id = $2, updated_at = now() "
        "WHERE id = $1", rid, session_id,
    )


async def set_retention_conv_lang(rid: int, conv_lang: str) -> None:
    await _execute(
        "UPDATE retention_users SET conv_lang = $2, updated_at = now() "
        "WHERE id = $1", rid, conv_lang,
    )


async def set_retention_pings_muted(rid: int, muted: bool) -> None:
    """Player opt-out/in for proactive pings (/stop and /resume)."""
    await _execute(
        "UPDATE retention_users SET pings_muted = $2, updated_at = now() "
        "WHERE id = $1", rid, muted,
    )


async def set_retention_unreachable(rid: int, unreachable: bool = True) -> None:
    """Mark a player the bot can no longer message (blocked the bot). The next
    inbound message from them clears the flag (they are reachable again)."""
    await _execute(
        "UPDATE retention_users SET unreachable = $2, updated_at = now() "
        "WHERE id = $1", rid, unreachable,
    )


# ---------------------------------------------------------------------------
# Idle re-engagement rules (the agent's inactivity ladder) — the admin-managed
# "player quiet N days -> Nika writes first" rules in retention_rules. Removed
# with the v1 ping matrix, restored as PART of the agent regime: the same
# worker sweeps them, the same guards/ledgers bound them.
# ---------------------------------------------------------------------------
_RULE_COLS = ("id, product_id, name, enabled, trigger_kind, trigger_kinds, "
              "inactivity_days, "
              "action, intent, vip_tiers, cooldown_days, priority, updated_by, "
              "created_at, updated_at")

# The default for a rule that doesn't name its trigger kinds: watch all three
# inactivity dimensions at once (owner decision — chat, casino AND deposit).
RULE_DEFAULT_TRIGGER_KINDS = ("bot_inactivity", "casino_inactivity",
                              "no_deposit")


def _row_to_rule(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    tiers = d.get("vip_tiers")
    if isinstance(tiers, str):
        try:
            d["vip_tiers"] = json.loads(tiers)
        except ValueError:
            d["vip_tiers"] = []
    kinds = d.get("trigger_kinds")
    if isinstance(kinds, str):
        try:
            kinds = json.loads(kinds)
        except ValueError:
            kinds = None
    if not kinds:
        # Pre-migration row: fall back to the legacy single kind.
        kinds = [d.get("trigger_kind") or "bot_inactivity"]
    d["trigger_kinds"] = [str(k) for k in kinds]
    _iso_fields(d, "created_at", "updated_at")
    return d


async def list_retention_rules(product_id: int,
                               only_enabled: bool = False) -> list[dict[str, Any]]:
    q = (f"SELECT {_RULE_COLS} FROM retention_rules WHERE product_id = $1 "
         + ("AND enabled " if only_enabled else "")
         + "ORDER BY priority DESC, id")
    rows = await _fetch(q, product_id)
    return [_row_to_rule(r) for r in rows]


async def create_retention_rule(product_id: int, fields: dict[str, Any],
                                updated_by: Optional[str] = None) -> dict[str, Any]:
    # trigger_kinds (the list) is authoritative; the legacy trigger_kind column
    # keeps the first kind so pre-migration readers stay coherent. A rule that
    # names neither watches ALL THREE dimensions (the default).
    kinds = fields.get("trigger_kinds")
    if not kinds:
        single = fields.get("trigger_kind")
        kinds = [single] if single else list(RULE_DEFAULT_TRIGGER_KINDS)
    kinds = [str(k) for k in kinds]
    row = await _fetchrow(
        "INSERT INTO retention_rules (product_id, name, enabled, trigger_kind, "
        " trigger_kinds, "
        " inactivity_days, action, intent, vip_tiers, cooldown_days, priority, "
        " updated_by) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12) "
        f"RETURNING {_RULE_COLS}",
        product_id, fields["name"], bool(fields.get("enabled", True)),
        kinds[0], json.dumps(kinds),
        int(fields.get("inactivity_days", 7)),
        fields.get("action", "message"), fields.get("intent", ""),
        json.dumps(fields.get("vip_tiers") or []),
        int(fields.get("cooldown_days", 14)), int(fields.get("priority", 0)),
        updated_by,
    )
    return _row_to_rule(row)


async def update_retention_rule(rule_id: int, product_id: int,
                                fields: dict[str, Any],
                                updated_by: Optional[str] = None
                                ) -> Optional[dict[str, Any]]:
    """Partial update; only supplied fields change. Scoped by product_id."""
    sets = ["updated_at = now()", "updated_by = $3"]
    args: list[Any] = [rule_id, product_id, updated_by]
    scalar = {"name": str, "trigger_kind": str, "action": str, "intent": str}
    # A single-kind write (legacy clients) collapses trigger_kinds to it so the
    # two columns never disagree.
    if "trigger_kind" in fields and "trigger_kinds" not in fields:
        fields = dict(fields)
        fields["trigger_kinds"] = [str(fields["trigger_kind"])]
    for f, cast in scalar.items():
        if f in fields:
            args.append(cast(fields[f]))
            sets.append(f"{f} = ${len(args)}")
    for f in ("inactivity_days", "cooldown_days", "priority"):
        if f in fields:
            args.append(int(fields[f]))
            sets.append(f"{f} = ${len(args)}")
    if "enabled" in fields:
        args.append(bool(fields["enabled"]))
        sets.append(f"enabled = ${len(args)}")
    if "vip_tiers" in fields:
        args.append(json.dumps(fields.get("vip_tiers") or []))
        sets.append(f"vip_tiers = ${len(args)}")
    if "trigger_kinds" in fields:
        kinds = [str(k) for k in (fields.get("trigger_kinds") or [])]
        if not kinds:
            kinds = list(RULE_DEFAULT_TRIGGER_KINDS)
        args.append(json.dumps(kinds))
        sets.append(f"trigger_kinds = ${len(args)}::jsonb")
        if "trigger_kind" not in fields:
            args.append(kinds[0])
            sets.append(f"trigger_kind = ${len(args)}")
    row = await _fetchrow(
        f"UPDATE retention_rules SET {', '.join(sets)} "
        f"WHERE id = $1 AND product_id = $2 RETURNING {_RULE_COLS}",
        *args,
    )
    return _row_to_rule(row) if row else None


async def delete_retention_rule(rule_id: int, product_id: int) -> bool:
    # The ledger keeps history: detach its rows instead of failing on the FK.
    # BOTH statements are scoped by product_id: rule_id is globally unique, so an
    # unscoped UPDATE would let a product-P admin NULL another tenant's ping->rule
    # links (corrupting that tenant's per-rule cooldown) while the scoped DELETE
    # matched nothing and returned 404 — a cross-tenant write. Mirrors
    # delete_retention_event, which already scopes both statements.
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE retention_pings SET rule_id = NULL "
                "WHERE rule_id = $1 AND product_id = $2",
                rule_id, product_id)
            result = await conn.execute(
                "DELETE FROM retention_rules WHERE id = $1 AND product_id = $2",
                rule_id, product_id)
    return result.endswith("1")


async def eligible_ping_users(product_id: int, *, min_gap_hours: int,
                              daily_cap: int, limit: int = 50
                              ) -> list[dict[str, Any]]:
    """Players the idle sweep may consider this run: subscribed, not opted out,
    not unreachable, past the global inter-ping gap and under the daily ping
    cap. Most-idle first. Per-rule thresholds/cooldowns are evaluated by the
    caller.
    """
    rows = await _fetch(
        f"SELECT {_RU_COLS} FROM retention_users "
        "WHERE product_id = $1 AND subscribed AND NOT pings_muted "
        "  AND NOT unreachable "
        "  AND (last_ping_at IS NULL "
        "       OR last_ping_at < now() - make_interval(hours => $2)) "
        # pings_day is written as the UTC date (record_retention_ping) and the
        # Python-side cap checks read the UTC clock, so compare against the UTC
        # date here too — CURRENT_DATE is the DB session-timezone date, which
        # disagrees for several hours a day on a non-UTC Postgres and would let a
        # capped player slip back through the prefilter.
        "  AND (pings_day IS DISTINCT FROM (now() at time zone 'utc')::date "
        "       OR pings_sent_today < $3) "
        "ORDER BY last_active_at ASC LIMIT $4",
        product_id, int(min_gap_hours), int(daily_cap), int(limit),
    )
    return [_row_to_retention_user(r) for r in rows]


async def list_retention_pings(product_id: int, page: int = 1,
                               page_size: int = 50) -> dict[str, Any]:
    """The ping ledger for the admin (joined with player + rule names)."""
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_pings WHERE product_id = $1", product_id)
    rows = await _fetch(
        "SELECT p.id, p.retention_user_id, p.rule_id, p.action, p.status, "
        "       p.detail, p.cost_usd, p.created_at, "
        "       u.tg_username, u.full_name, u.player_id, r.name AS rule_name "
        "FROM retention_pings p "
        "JOIN retention_users u ON u.id = p.retention_user_id "
        "LEFT JOIN retention_rules r ON r.id = p.rule_id "
        "WHERE p.product_id = $1 "
        "ORDER BY p.id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["retention_user_id"] = int(d["retention_user_id"])
        if d.get("rule_id") is not None:
            d["rule_id"] = int(d["rule_id"])
        if d.get("cost_usd") is not None:
            d["cost_usd"] = float(d["cost_usd"])
        if hasattr(d.get("created_at"), "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        items.append(d)
    return {"items": items, "total": int(total or 0)}


# A ping that FIRED, for pacing purposes: really sent, or shadow-fired in
# dry-run mode. Excluding dry runs made the whole idle ladder pace as if nothing
# had happened while `v2_dry_run` was on — which it is BY DEFAULT — so a rule
# re-matched the same player every `ping_min_gap_hours` and filled the decision
# ledger with duplicates of a ping it had already "made".
_PING_FIRED = "p.status IN ('sent', 'dry_run')"


async def ping_rule_recently_fired(rid: int, rule_id: int,
                                   cooldown_days: int) -> bool:
    """True when this rule already pinged this player within its cooldown
    (a dry-run ping counts — see _PING_FIRED)."""
    val = await _fetchval(
        "SELECT EXISTS(SELECT 1 FROM retention_pings p "
        "WHERE p.retention_user_id = $1 AND p.rule_id = $2 "
        f"AND {_PING_FIRED} "
        "AND p.created_at > now() - make_interval(days => $3))",
        rid, rule_id, int(cooldown_days),
    )
    return bool(val)


async def idle_rule_thresholds_fired_since(rid: int, since: Any,
                                           trigger_kind: Optional[str] = None
                                           ) -> dict[str, int]:
    """Max `inactivity_days` of the idle rules already fired to this player
    since `since`, per trigger kind (a dry-run rung counts — see _PING_FIRED).

    The anti-cascade guard: per-rule cooldowns alone let a long-quiet player
    receive the ENTIRE ladder in reverse — after "quiet 60 days" fired, the
    45/30/21/… rungs each matched on the next sweep (their own cooldowns were
    clean) and cascaded out at min-gap pace. During ONE silence stretch only a
    rung ABOVE the highest already-fired one may fire; the moment the current
    silence stretch is broken every rung is eligible again.

    `since` MUST be the start of the current silence stretch for `trigger_kind`
    (see retention_idle._idle_anchor_for) — the SAME clock the rung measures
    idleness on: `last_active_at` for bot_inactivity, but the casino
    login/played/deposit timestamps for casino_inactivity/no_deposit. Anchoring
    every kind on `last_active_at` (which a bot reply bumps) wrongly wiped the
    fired memory of casino-anchored ladders while the casino silence continued,
    re-opening the reverse-cascade for players who reply to pings. Pass
    `trigger_kind` so the caller can query per-kind with the matching anchor;
    None counts every fired ping across kinds."""
    since_ts = _as_ts(since)
    if trigger_kind is not None:
        # A multi-kind rung (trigger_kinds list) counts toward EVERY kind it
        # watches — the ping ledger doesn't record which dimension matched, and
        # counting it everywhere is the anti-spam-safe direction.
        val = await _fetchval(
            "SELECT MAX(r.inactivity_days) FROM retention_pings p "
            "JOIN retention_rules r ON r.id = p.rule_id "
            f"WHERE p.retention_user_id = $1 AND {_PING_FIRED} "
            "  AND p.created_at > COALESCE($2::timestamptz, "
            "                              '-infinity'::timestamptz) "
            "  AND (r.trigger_kinds ? $3 "
            "       OR (r.trigger_kinds IS NULL AND r.trigger_kind = $3))",
            rid, since_ts, trigger_kind,
        )
        return {trigger_kind: int(val)} if val is not None else {}
    rows = await _fetch(
        "SELECT r.trigger_kind, MAX(r.inactivity_days) AS days "
        "FROM retention_pings p "
        "JOIN retention_rules r ON r.id = p.rule_id "
        f"WHERE p.retention_user_id = $1 AND {_PING_FIRED} "
        "  AND p.created_at > COALESCE($2::timestamptz, "
        "                              '-infinity'::timestamptz)"
        " GROUP BY r.trigger_kind",
        rid, since_ts,
    )
    return {str(r["trigger_kind"]): int(r["days"]) for r in rows
            if r["days"] is not None}


# ---------------------------------------------------------------------------
# Proactive-send ledger + the shared per-player anti-annoyance counters
# ---------------------------------------------------------------------------
async def record_retention_ping(product_id: int, rid: int,
                                rule_id: Optional[int], action: str,
                                status: str, detail: Optional[str] = None,
                                cost_usd: Optional[float] = None) -> None:
    """Ledger row + the per-player ping state, atomically.

    `last_ping_at` is stamped on EVERY outcome (it means "last attempt"): a
    failed ping must also wait out `ping_min_gap_hours`, otherwise a player
    whose sends persistently fail would be retried — with a fresh model call —
    on every worker sweep, forever. The daily counter only counts real sends.
    """
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO retention_pings (product_id, retention_user_id, "
                " rule_id, action, status, detail, cost_usd) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                product_id, rid, rule_id, action, status, detail, cost_usd,
            )
            if status == "sent":
                await conn.execute(
                    # UTC day (not CURRENT_DATE = session tz): the Python-side
                    # cap checks read the UTC clock, both must agree.
                    "UPDATE retention_users SET last_ping_at = now(), "
                    "pings_sent_today = CASE WHEN pings_day = "
                    "  (now() at time zone 'utc')::date "
                    "  THEN pings_sent_today + 1 ELSE 1 END, "
                    "pings_day = (now() at time zone 'utc')::date, "
                    "updated_at = now() "
                    "WHERE id = $1", rid,
                )
            else:
                await conn.execute(
                    "UPDATE retention_users SET last_ping_at = now(), "
                    "updated_at = now() WHERE id = $1", rid,
                )


async def touch_last_ping(rid: int) -> None:
    """Stamp `last_ping_at` alone — no ledger row, no daily counter.

    For the QUEUED send path: the min-gap guard reads `last_ping_at`, and with
    the send worker on it would otherwise not move until the touch actually
    left, minutes later. In that window the next event for the same player
    passes the gap check and a second touch is decided. Stamping at enqueue
    closes it. The daily counter still only counts real sends, so it is
    deliberately not touched here, and no `retention_pings` row is written —
    the ledger records what happened, and nothing has happened yet.
    """
    await _execute(
        "UPDATE retention_users SET last_ping_at = now(), updated_at = now() "
        "WHERE id = $1", int(rid))


# ---------------------------------------------------------------------------
# Retention agent: canonical event log + the decision ledger
# ---------------------------------------------------------------------------
def _row_to_retention_event(row: asyncpg.Record) -> dict[str, Any]:
    d = {
        "id": int(row["id"]),
        "product_id": row["product_id"],
        "event_id": row["event_id"],
        "event_name": row["event_name"],
        "event_version": row["event_version"],
        "player_id": row["player_id"],
        "ts": _iso(row["ts"]),
        "payload": _json_value(row["payload"]) or {},
        "source": row["source"],
        "processed_at": _iso(row["processed_at"]),
        "created_at": _iso(row["created_at"]),
    }
    # Lifecycle fields — tolerated as absent so a SELECT of an older column set
    # (or a test's canned row) still round-trips.
    for key in ("status", "last_error", "worker_id"):
        if key in row:
            d[key] = row[key]
    for key in ("attempts", "priority"):
        if key in row and row[key] is not None:
            d[key] = int(row[key])
    for key in ("locked_until", "next_attempt_at"):
        if key in row:
            d[key] = _iso(row[key])
    return d


async def ingest_retention_event(product_id: int, *, event_id: str,
                                 event_name: str, player_id: str,
                                 ts: Any, payload: dict[str, Any],
                                 event_version: str = "1.0",
                                 source: str = "webhook",
                                 priority: int = 3,
                                 queue: bool = True) -> Optional[int]:
    """Append one canonical event. Idempotent by (product_id, event_id):
    a duplicate returns None and writes nothing (at-least-once delivery).

    `queue=False` stores the row already COMPLETE ('done'): the high-volume
    "state food" events (spins, session pings) exist only to move the activity
    counters and feed the state resolver, and letting them into the drain would
    bury the events that actually deserve a reaction. The bridge and the
    resolver read the row exactly the same either way — only the queue never
    sees it. See player_sync.EVENT_PRIORITY / `queue_bypass_state_events`.
    """
    status = "pending" if queue else "done"
    row = await _fetchrow(
        "INSERT INTO retention_events (product_id, event_id, event_name, "
        " event_version, player_id, ts, payload, source, priority, status, "
        " processed_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, "
        "        CASE WHEN $10 = 'done' THEN now() ELSE NULL END) "
        "ON CONFLICT (product_id, event_id) DO NOTHING RETURNING id",
        product_id, event_id, event_name, event_version, player_id,
        _as_ts(ts), json.dumps(payload or {}), source, int(priority), status,
    )
    return int(row["id"]) if row else None


async def claim_retention_events(product_id: int, limit: int = 50,
                                 delay_min_sec: int = 0,
                                 delay_max_sec: int = 0, *,
                                 lease_sec: int = 300,
                                 worker_id: Optional[str] = None,
                                 max_priority: int = 5
                                 ) -> list[dict[str, Any]]:
    """Atomically LEASE a batch of pending events (highest lane, oldest first).

    Claiming = flipping status to 'processing' with an expiry in the same
    statement that selects the rows (FOR UPDATE SKIP LOCKED), so two concurrent
    drainers — the worker sweep and the admin «Process queue now» button, or
    two service instances — can never pick up the same event and each send a
    message for it (the bug behind one deposit producing two thank-you notes).

    The lease is what makes the queue DURABLE. The previous version stamped
    `processed_at` here, so an event whose pipeline then threw (or whose worker
    was killed mid-batch by a deploy) counted as processed and was lost with no
    trace. Now the caller MUST close every claimed row —
    `complete_retention_event` on success, `fail_retention_event` on error —
    and anything left behind is picked back up by
    `reclaim_expired_event_leases` once `locked_until` passes.

    The claim also enforces PLAYER EXCLUSION (see the SQL): a player with an
    event already in flight is skipped entirely, so "one decision per player at
    a time" holds ACROSS worker replicas and not merely inside one batch.
    Grouping the batch by player would only serialize one worker's own events.

    `max_priority` is the backpressure lane gate: under a growing backlog the
    worker lowers it so transactional events (P1/P2) still move while state
    food waits. `delay_min_sec`/`delay_max_sec` implement the humanizing SEND
    DELAY: an event is not claimable until a per-event pseudo-random min..max
    seconds have passed since it arrived (id-keyed, so the delay is stable
    across sweeps and instances). Both 0 = claim immediately (the admin
    «Process queue now» path).
    """
    lo = max(int(delay_min_sec), 0)
    span = max(int(delay_max_sec) - lo, 0) + 1  # modulo divisor, >= 1
    rows = await _fetch(
        "UPDATE retention_events SET status = 'processing', "
        "  attempts = attempts + 1, worker_id = $6, "
        "  locked_until = now() + make_interval(secs => $5) "
        "WHERE id IN ("
        "  SELECT cand.id FROM ("
        "    SELECT e.id, e.product_id, e.player_id FROM retention_events e "
        "     WHERE e.product_id = $1 AND e.status = 'pending' "
        # A row the pipeline already finished with always carries
        # processed_at, and a genuinely pending one never does. Testing both
        # makes the claim correct on a database whose legacy rows have not been
        # backfilled to status='done' yet — otherwise the first boot after the
        # upgrade would re-react to the entire event history.
        "       AND e.processed_at IS NULL "
        "       AND e.priority <= $7 "
        "       AND (e.next_attempt_at IS NULL "
        "            OR e.next_attempt_at <= now()) "
        "       AND e.created_at <= now() "
        "           - make_interval(secs => $3 + (e.id % $4)) "
        # A player who already has an event in flight ANYWHERE is skipped
        # whole: his next event must wait for the one being decided, or two
        # workers read the same guard counters and he gets two messages for
        # what should have been one.
        "       AND NOT EXISTS (SELECT 1 FROM retention_events b "
        "                        WHERE b.product_id = e.product_id "
        "                          AND b.player_id = e.player_id "
        "                          AND b.status = 'processing') "
        "     ORDER BY e.priority ASC, e.id ASC "
        "     LIMIT $2 FOR UPDATE SKIP LOCKED) AS cand "
        # ...and the advisory lock closes the instant the check above cannot:
        # two claims running at the same moment both see "no rows in flight"
        # for a player, and only one may win. Transaction-scoped, so it is
        # released the moment the claim commits — from then on the 'processing'
        # rows keep the other worker out. Re-entrant, so every event of the
        # winning player still comes back in this one batch, which is exactly
        # what the per-player grouping wants. Applied AFTER the LIMIT so at
        # most `limit` locks are ever taken.
        "  WHERE pg_try_advisory_xact_lock("
        "          hashtext('retention_player'), "
        "          hashtext(cand.product_id::text || ':' || cand.player_id))) "
        "RETURNING *",
        product_id, int(limit), float(lo), int(span), float(max(lease_sec, 1)),
        worker_id, int(max_priority),
    )
    rows = sorted(rows, key=lambda r: r["id"])  # UPDATE..RETURNING has no ORDER
    return [_row_to_retention_event(r) for r in rows]


async def renew_retention_event_lease(event_pk: int, lease_sec: int) -> None:
    """Push a held lease forward — the chain is alive, just not finished.

    The lease is taken once for the whole claimed batch, but a player's events
    are processed STRICTLY SERIALLY and each one can spend a 90-second model
    call. Four bursty events from the same player therefore outlive a
    300-second lease, the reclaimer hands them back as 'pending', and a second
    worker starts a concurrent chain for exactly the player the exclusion in
    the claim exists to protect. Renewing before each event in the chain keeps
    the lease meaning "this work is still moving" rather than "this batch was
    small enough".
    """
    await _execute(
        "UPDATE retention_events "
        "SET locked_until = now() + make_interval(secs => $2::float8) "
        "WHERE id = $1 AND status = 'processing'",
        int(event_pk), float(max(int(lease_sec), 1)))


async def complete_retention_event(event_pk: int) -> None:
    """Close a leased event: the pipeline is done with it, for good."""
    await _execute(
        "UPDATE retention_events SET status = 'done', processed_at = now(), "
        "  locked_until = NULL, last_error = NULL WHERE id = $1",
        int(event_pk),
    )


async def fail_retention_event(event_pk: int, error: str, *,
                               max_attempts: int = 5,
                               backoff_base_sec: int = 30) -> str:
    """Close a leased event that FAILED: retry with backoff, or dead-letter.

    Returns the resulting status ('pending' = will be retried, 'dead' = the
    attempt ceiling was reached and a human has to look). The backoff is
    exponential on the attempt count already stamped by the claim, capped at an
    hour so a permanently broken event cannot spin.
    """
    row = await _fetchrow(
        "UPDATE retention_events SET "
        "  status = CASE WHEN attempts >= $2 THEN 'dead' ELSE 'pending' END, "
        "  processed_at = CASE WHEN attempts >= $2 THEN now() "
        "                      ELSE processed_at END, "
        "  locked_until = NULL, "
        "  next_attempt_at = CASE WHEN attempts >= $2 THEN NULL ELSE "
        "    now() + make_interval(secs => LEAST($3 * power(2, "
        "      GREATEST(attempts - 1, 0)), 3600)) END, "
        "  last_error = $4 "
        "WHERE id = $1 RETURNING status",
        int(event_pk), int(max_attempts), float(max(backoff_base_sec, 1)),
        (error or "")[:2000],
    )
    return str(row["status"]) if row else "pending"


async def release_retention_events(event_pks: list[int]) -> int:
    """Hand leased events straight back to the queue, no attempt penalty.

    Two callers: the graceful shutdown (a batch the worker will not finish must
    be retried IMMEDIATELY, not after its lease expires) and the player-shard
    dispatcher (another worker holds that player's lock, so these events belong
    to the next tick). `attempts` is decremented because the claim increments
    optimistically and neither case is a failed attempt.
    """
    if not event_pks:
        return 0
    result = await _execute(
        "UPDATE retention_events SET status = 'pending', locked_until = NULL, "
        "  attempts = GREATEST(attempts - 1, 0), worker_id = NULL "
        "WHERE id = ANY($1::bigint[]) AND status = 'processing'",
        [int(x) for x in event_pks],
    )
    return _affected(result)


async def reclaim_expired_event_leases(*, limit: int = 1000,
                                       max_attempts: int = 5) -> int:
    """Return events whose lease expired to the queue (or dead-letter them).

    This is what turns "the worker was killed / the process OOMed / the model
    call hung past the lease" into a retry instead of a lost touch. Runs from
    the maintenance loop and once at worker startup.
    """
    result = await _execute(
        "UPDATE retention_events SET "
        "  status = CASE WHEN attempts >= $2 THEN 'dead' ELSE 'pending' END, "
        "  locked_until = NULL, "
        "  last_error = COALESCE(last_error, 'lease expired') "
        "WHERE id IN (SELECT id FROM retention_events "
        "             WHERE status = 'processing' AND locked_until < now() "
        "             ORDER BY locked_until LIMIT $1 FOR UPDATE SKIP LOCKED)",
        int(limit), int(max_attempts),
    )
    return _affected(result)


async def backfill_event_lifecycle(*, batch: int = 20_000,
                                   max_batches: int = 50) -> int:
    """Bring pre-lifecycle rows onto the new contract. Batched, post-boot.

    Rows written before the queue had a lifecycle default to
    `status='pending'` even though `processed_at` says the worker finished with
    them. Left alone they would keep the partial queue index (and the lag
    metric) permanently polluted with the whole event history.

    Deliberately NOT part of init_db: that runs as one transaction on a pool
    connection with a 30s command timeout, and this UPDATE can cover millions
    of rows — it would time out, roll the boot back, and repeat forever. Each
    batch is its own short transaction, so partial progress always sticks and
    a few maintenance passes finish the job. `max_batches` bounds one pass so
    the caller's loop keeps its cadence.

    The claim does not wait for this: it tests `processed_at IS NULL` too.
    """
    done = 0
    for _ in range(max_batches):
        result = await _execute(
            "UPDATE retention_events SET status = 'done' "
            "WHERE id IN (SELECT id FROM retention_events "
            "             WHERE status = 'pending' AND processed_at IS NOT NULL "
            "             LIMIT $1)",
            int(batch))
        n = _affected(result)
        done += n
        if n < int(batch):
            break
    # Lane backfill for whatever is genuinely still queued, so an upgrade does
    # not leave transactional events sitting in the default lane. Bounded by
    # the live queue depth, which is small by construction.
    await _execute(
        "UPDATE retention_events SET priority = CASE "
        "  WHEN event_name IN ('deposit_confirmed', 'deposit_failed', "
        "       'withdrawal_settled', 'kyc_approved', 'kyc_rejected') THEN 1 "
        "  WHEN event_name IN ('bonus_granted', 'bonus_claimed', "
        "       'bonus_completed', 'bonus_expired', 'level_up', 'class_up', "
        "       'downgrade', 'mission_completed', "
        "       'highlights_pack_completed') THEN 2 "
        "  WHEN event_name IN ('bet_settled', 'session_started', "
        "       'session_ended', 'xp_granted', 'check_in_done', "
        "       'deposit_initiated', 'highlights_pack_opened') THEN 5 "
        "  ELSE 3 END "
        "WHERE status = 'pending' AND processed_at IS NULL AND priority = 3 "
        "  AND event_name NOT IN ('kyc_started')")
    return done


async def list_dead_retention_events(product_id: int, page: int = 1,
                                     page_size: int = 50) -> dict[str, Any]:
    """The dead-letter queue for the admin (newest first)."""
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_events "
        "WHERE product_id = $1 AND status = 'dead'", product_id)
    rows = await _fetch(
        "SELECT * FROM retention_events "
        "WHERE product_id = $1 AND status = 'dead' "
        "ORDER BY id DESC LIMIT $2 OFFSET $3",
        product_id, int(page_size), int(offset))
    return {"items": [_row_to_retention_event(r) for r in rows],
            "total": int(total or 0)}


async def requeue_retention_events(product_id: int,
                                   event_pks: Optional[list[int]] = None
                                   ) -> int:
    """Put dead-lettered events back in the queue (admin retry).

    `event_pks=None` requeues every dead row of the product. Attempts reset to
    zero — the operator is asserting the cause is fixed.
    """
    if event_pks is not None and not event_pks:
        return 0
    sql = ("UPDATE retention_events SET status = 'pending', attempts = 0, "
           "  locked_until = NULL, next_attempt_at = NULL, processed_at = NULL "
           "WHERE product_id = $1 AND status = 'dead'")
    args: list[Any] = [product_id]
    if event_pks is not None:
        args.append([int(x) for x in event_pks])
        sql += " AND id = ANY($2::bigint[])"
    result = await _execute(sql, *args)
    return _affected(result)


async def count_unprocessed_retention_events(product_id: int) -> int:
    """Queue depth for the admin status header (pending + in flight)."""
    val = await _fetchval(
        "SELECT COUNT(*) FROM retention_events "
        "WHERE product_id = $1 AND status IN ('pending', 'processing')",
        product_id,
    )
    return int(val or 0)


# A pending row is only LAG once the claim would actually take it. A row still
# inside its humanizing send delay, or parked on a retry backoff, is waiting by
# DESIGN — counting it made the lag read 300..900s (the shipped send-delay
# window) on every product with traffic, which is already past the p3 rung.
_QUEUE_DUE = (
    "product_id = $1 AND status = 'pending' AND processed_at IS NULL "
    "  AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
    # Mirrors claim_retention_events exactly, including the id-keyed jitter —
    # the parameters sit inside make_interval(), whose signature is what lets
    # Postgres type them (see touch_retention_activity for the expression case).
    "  AND created_at <= now() - make_interval(secs => $2 + (id % $3))"
)


def _queue_due_args(delay_min_sec: int, delay_max_sec: int) -> tuple[float, int]:
    lo = max(int(delay_min_sec), 0)
    return float(lo), max(int(delay_max_sec) - lo, 0) + 1


async def retention_queue_lag(product_id: int, *,
                              delay_min_sec: int = 0,
                              delay_max_sec: int = 0) -> int:
    """Age in seconds of the oldest CLAIMABLE event nobody has picked up yet.

    The hot-path half of `retention_queue_stats`: the drain reads this on every
    tick to decide which lanes it may still claim, so it is one indexed
    MIN() rather than the admin view's full breakdown. Pass the product's send
    delay so the number means "work that is overdue", not "work that has not
    come due yet".
    """
    lo, span = _queue_due_args(delay_min_sec, delay_max_sec)
    val = await _fetchval(
        "SELECT COALESCE(EXTRACT(EPOCH FROM now() - MIN(created_at)), 0) "
        f"FROM retention_events WHERE {_QUEUE_DUE}",
        product_id, lo, span)
    return int(float(val or 0))


async def retention_queue_lag_by_lane(product_id: int, *,
                                      delay_min_sec: int = 0,
                                      delay_max_sec: int = 0
                                      ) -> dict[int, int]:
    """The lag the backpressure ladder reads: one entry per LANE CEILING.

    `{2: …, 3: …, 5: …}` — how far behind the work at or above each lane is.
    The ladder MUST evaluate each rung against the lanes it does not shed:
    keyed on the whole queue instead, shedding was self-latching — the lanes it
    stopped claiming stayed pending, stayed the oldest rows, and held the
    ladder down until the pruner deleted them. Lane 5 backing up can no longer
    argue for shedding lane 5.
    """
    lo, span = _queue_due_args(delay_min_sec, delay_max_sec)
    row = await _fetchrow(
        "SELECT "
        "  COALESCE(EXTRACT(EPOCH FROM now() - MIN(created_at) "
        "    FILTER (WHERE priority <= 2)), 0) AS lag2, "
        "  COALESCE(EXTRACT(EPOCH FROM now() - MIN(created_at) "
        "    FILTER (WHERE priority <= 3)), 0) AS lag3, "
        "  COALESCE(EXTRACT(EPOCH FROM now() - MIN(created_at)), 0) AS lag5 "
        f"FROM retention_events WHERE {_QUEUE_DUE}",
        product_id, lo, span)
    row = row or {}
    return {2: int(float(row.get("lag2") or 0)),
            3: int(float(row.get("lag3") or 0)),
            5: int(float(row.get("lag5") or 0))}


async def retention_queue_stats(product_id: int) -> dict[str, Any]:
    """Everything the SLA is read from, in two queries.

    `oldest_pending_sec` is the QUEUE LAG — the age of the oldest event nobody
    has picked up yet. It is the number the degradation ladder keys on and the
    one an alert should watch; a rising lag is the only early warning that the
    drain is losing the race.
    """
    row = await _fetchrow(
        "SELECT "
        "  COUNT(*) FILTER (WHERE status = 'pending') AS pending, "
        "  COUNT(*) FILTER (WHERE status = 'processing') AS processing, "
        "  COUNT(*) FILTER (WHERE status = 'dead') AS dead, "
        "  COALESCE(EXTRACT(EPOCH FROM now() - MIN(created_at) "
        "    FILTER (WHERE status = 'pending')), 0) AS oldest_pending_sec, "
        "  COUNT(*) FILTER (WHERE status = 'pending' AND priority <= 2) "
        "    AS pending_p1_p2, "
        "  COUNT(*) FILTER (WHERE status = 'pending' AND priority = 3) "
        "    AS pending_p3, "
        "  COUNT(*) FILTER (WHERE status = 'pending' AND priority >= 4) "
        "    AS pending_p5 "
        "FROM retention_events WHERE product_id = $1",
        product_id)
    dl = await _fetchrow(
        "SELECT "
        "  COUNT(*) FILTER (WHERE status IN ('queued', 'sending')) AS queued, "
        "  COUNT(*) FILTER (WHERE status = 'failed' AND NOT permanent_fail) "
        "    AS failed, "
        "  COUNT(*) FILTER (WHERE permanent_fail) AS permanent_failed, "
        "  COALESCE(EXTRACT(EPOCH FROM now() - MIN(scheduled_at) "
        "    FILTER (WHERE status = 'queued')), 0) AS oldest_queued_sec "
        "FROM retention_deliveries WHERE product_id = $1",
        product_id)
    out = {
        "pending": int((row or {}).get("pending") or 0),
        "processing": int((row or {}).get("processing") or 0),
        "dead": int((row or {}).get("dead") or 0),
        "oldest_pending_sec": int(float((row or {}).get("oldest_pending_sec")
                                        or 0)),
        "pending_by_priority": {
            "p1_p2": int((row or {}).get("pending_p1_p2") or 0),
            "p3": int((row or {}).get("pending_p3") or 0),
            "p5": int((row or {}).get("pending_p5") or 0),
        },
        "deliveries_queued": int((dl or {}).get("queued") or 0),
        "deliveries_failed": int((dl or {}).get("failed") or 0),
        "deliveries_permanent_failed": int(
            (dl or {}).get("permanent_failed") or 0),
        "oldest_queued_delivery_sec": int(
            float((dl or {}).get("oldest_queued_sec") or 0)),
    }
    return out


async def retention_latency_percentiles(product_id: int, hours: int = 24
                                        ) -> dict[str, Any]:
    """p50/p95/p99 of event -> delivered touch, in seconds. THE SLA metric.

    Measured end to end from when the event ARRIVED (not when it was claimed)
    to when the touch actually left, so every queue wait, humanizing delay and
    send-shaping pause is inside the number. Only event-triggered touches can
    be measured — an idle ping has no originating event, so it is excluded
    rather than silently counted as instantaneous.
    """
    row = await _fetchrow(
        "SELECT COUNT(*) AS n, "
        "  PERCENTILE_CONT(0.5) WITHIN GROUP "
        "    (ORDER BY EXTRACT(EPOCH FROM o.sent_at - e.created_at)) AS p50, "
        "  PERCENTILE_CONT(0.95) WITHIN GROUP "
        "    (ORDER BY EXTRACT(EPOCH FROM o.sent_at - e.created_at)) AS p95, "
        "  PERCENTILE_CONT(0.99) WITHIN GROUP "
        "    (ORDER BY EXTRACT(EPOCH FROM o.sent_at - e.created_at)) AS p99 "
        "FROM retention_outcomes o "
        "JOIN retention_v2_decisions d ON d.id = o.decision_id "
        "JOIN retention_events e ON e.id = d.event_pk "
        "WHERE o.product_id = $1 AND o.kind <> 'holdout' "
        "  AND o.sent_at > now() - make_interval(hours => $2) "
        "  AND o.sent_at >= e.created_at",
        product_id, int(hours))
    def _sec(key: str) -> Optional[int]:
        v = (row or {}).get(key)
        return int(float(v)) if v is not None else None
    return {"samples": int((row or {}).get("n") or 0),
            "p50_sec": _sec("p50"), "p95_sec": _sec("p95"),
            "p99_sec": _sec("p99")}


async def prune_retention_events(keep_days: int = 90, *,
                                 batch: int = 20_000,
                                 state_keep_days: Optional[int] = None,
                                 state_priority_from: int = 5) -> int:
    """Delete canonical events older than keep_days (all products).

    The event log is append-only and can grow by millions of rows/month (partners
    stream bet_settled per settled bet), while the state resolver only reads recent
    events (the 24h loss window + recent activity), so old rows are dead
    weight with no reaper — unlike app_logs, which is capped.

    Age is measured from `processed_at` when the row was claimed, else from
    `created_at`. Pruning only PROCESSED rows left the table unbounded whenever
    nothing was claiming: `claim_retention_events` is the only writer of
    `processed_at` and it is skipped entirely when a product has `v2_enabled`
    off (or when RETENTION_SCHEDULER_ENABLED is off deployment-wide), so a
    partner streaming events at an agent that is switched off grew the table
    forever. An unclaimed row this old is not a backlog worth keeping either —
    `retention_v2._fresh_enough` refuses to react to anything older than
    _MAX_REACTION_AGE_HOURS, orders of magnitude below keep_days.

    Decision-ledger rows that referenced a pruned event keep their event_name
    snapshot and drop the FK link (there is no ON DELETE clause, so the link
    must be nulled first).

    BATCHED on purpose: the very deployments this prune exists for arrive with
    a backlog of millions of rows, and one unbatched DELETE of that backlog
    cannot finish inside the pool's command_timeout — it would time out, roll
    back whole, and retry identically on every pass forever. Each batch is its
    own short transaction, so partial progress always sticks and the hourly
    cadence drains any backlog within a few passes.

    `state_keep_days` gives the high-volume lane (priority >= 5: spins, session
    pings — the events that exist only to move counters) its own, much shorter
    retention. They are 90%+ of the rows and worth nothing after the state
    resolver's windows have passed, so keeping them as long as the decision
    events is what makes the table grow without bound. None = one retention for
    everything (the previous behaviour)."""
    cutoff = ("COALESCE(processed_at, created_at) "
              "< now() - make_interval(days => CASE WHEN priority >= $3::int "
              "                                THEN $4::int ELSE $1::int END)")
    state_days = (int(keep_days) if state_keep_days is None
                  else min(int(state_keep_days), int(keep_days)))
    removed = 0
    while True:
        async with _acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    f"SELECT id FROM retention_events WHERE {cutoff} "
                    "ORDER BY id LIMIT $2",
                    int(keep_days), int(batch), int(state_priority_from),
                    state_days)
                ids = [r["id"] for r in rows]
                if not ids:
                    return removed
                await conn.execute(
                    "UPDATE retention_v2_decisions SET event_pk = NULL "
                    "WHERE event_pk = ANY($1::bigint[])", ids)
                result = await conn.execute(
                    "DELETE FROM retention_events WHERE id = ANY($1::bigint[])",
                    ids)
        removed += _affected(result)
        if len(ids) < int(batch):
            return removed


async def list_retention_events(product_id: int, page: int = 1,
                                page_size: int = 50) -> dict[str, Any]:
    """The event log for the admin tab (newest first)."""
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_events WHERE product_id = $1",
        product_id)
    rows = await _fetch(
        "SELECT * FROM retention_events WHERE product_id = $1 "
        "ORDER BY id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset,
    )
    return {"items": [_row_to_retention_event(r) for r in rows],
            "total": int(total or 0)}


async def recent_retention_events_for_player(product_id: int, player_id: str,
                                             limit: int = 10
                                             ) -> list[dict[str, Any]]:
    """The player's recent event tail (newest first) — decision-prompt context
    and the state resolver's activity/loss inputs."""
    rows = await _fetch(
        "SELECT * FROM retention_events "
        "WHERE product_id = $1 AND player_id = $2 "
        "ORDER BY ts DESC LIMIT $3",
        product_id, player_id, int(limit),
    )
    return [_row_to_retention_event(r) for r in rows]


# NULL-safe numeric extraction from an event payload: partner-fed JSON may
# carry non-numeric strings ("12.50 USD"); a raw ::numeric cast would then
# raise on EVERY read of that player's window until the row ages out — a
# silent decision blackout. Non-numeric values count as 0 instead.
def _payload_num(field: str) -> str:
    return (
        f"CASE WHEN payload->>'{field}' ~ '^-?[0-9]+(\\.[0-9]+)?$' "
        f"THEN (payload->>'{field}')::numeric ELSE 0 END"
    )


async def player_net_loss_24h(product_id: int, player_id: str) -> float:
    """Net real-money loss over the last 24h from bet_settled events
    (bets minus wins; bonus-money rounds excluded) — the EPIC-5 loss window.
    Negative = the player is up; clamped to 0 by the caller if needed.

    Summed PER CURRENCY (payload->>'currency', absent = its own bucket) and
    the worst bucket wins: a blind cross-currency sum added apples to oranges
    (100 TRY + 50 EUR = "150") and compared it with the USD-denominated
    threshold. A single-currency player — the normal case — is exact; a
    mixed-currency player is judged by his worst single-currency loss."""
    val = await _fetchval(
        "SELECT MAX(loss) FROM ("
        "  SELECT COALESCE(payload->>'currency', '') AS cur, "
        f"         SUM({_payload_num('amount')} - {_payload_num('win_amount')})"
        "          AS loss "
        "  FROM retention_events "
        "  WHERE product_id = $1 AND player_id = $2 "
        "    AND event_name = 'bet_settled' "
        "    AND COALESCE((payload->>'bonus_money') IN ('true', 't', '1'), "
        "        FALSE) = FALSE "
        "    AND ts > now() - interval '24 hours' "
        "  GROUP BY 1) AS per_currency",
        product_id, player_id,
    )
    return float(val or 0)


async def last_loss_signal_at(product_id: int, player_id: str) -> Optional[str]:
    """When the player last had a losing settled bet (comfort-window anchor)."""
    val = await _fetchval(
        "SELECT MAX(ts) FROM retention_events "
        "WHERE product_id = $1 AND player_id = $2 "
        "  AND event_name = 'bet_settled' "
        f"  AND {_payload_num('win_amount')} < {_payload_num('amount')}",
        product_id, player_id,
    )
    return _iso(val) if val else None


async def touch_retention_activity(product_id: int, player_id: str,
                                   field: str, ts: Any,
                                   debounce_sec: int = 0) -> int:
    """Forward-only bump of one casino-activity timestamp (the legacy bridge:
    canonical events feed the same last_login/played/deposit_at fields the v1
    state resolver keys on). GREATEST guards out-of-order event delivery — an
    older event never rewinds the timestamp. No linked player row = no-op.

    `debounce_sec` skips the write when the stored value is already within that
    many seconds of the new one. This is a hot-row UPDATE on the player row and
    it fires on EVERY high-volume event; at scale that is the single busiest
    write in the system, and re-stamping "active" three seconds later buys
    nothing (every reader works on scales of hours). Pass 0 — the default and
    what `last_deposit_at` always uses — to keep the timestamp exact.
    """
    if field not in _RETENTION_ACTIVITY_FIELDS:
        raise ValueError(f"not an activity field: {field!r}")
    parsed = _as_ts(ts)
    if parsed is None:
        return 0
    # EXPLICIT CASTS, not decoration: `$3 - make_interval(...)` gives Postgres
    # two operands where one is unknown, so it resolves $3 to the OTHER one's
    # type — `interval` — and the whole statement then fails at PREPARE time
    # with "operator does not exist: timestamptz < interval". A bare $n is only
    # inferable inside a function call (which has a signature); inside an
    # expression it has nothing to infer from. conftest stubs asyncpg, so no
    # test can catch this — scripts/check_queue_sql.py covers it instead.
    result = await _execute(
        f"UPDATE retention_users SET {field} = "
        f"GREATEST(COALESCE({field}, $3::timestamptz), $3::timestamptz), "
        "updated_at = now() "
        "WHERE product_id = $1 AND player_id = $2 "
        f"  AND ({field} IS NULL OR $4::float8 <= 0 "
        f"       OR {field} < $3::timestamptz "
        "            - make_interval(secs => $4::float8))",
        product_id, player_id, parsed, float(max(int(debounce_sec), 0)),
    )
    return _affected(result)


async def get_retention_user_by_player(product_id: int, player_id: str
                                       ) -> Optional[dict[str, Any]]:
    """The Telegram-linked retention user for a casino player_id, if any."""
    row = await _fetchrow(
        f"SELECT {_RU_COLS} FROM retention_users "
        "WHERE product_id = $1 AND player_id = $2 "
        "ORDER BY updated_at DESC LIMIT 1",
        product_id, player_id,
    )
    return _row_to_retention_user(row) if row else None


async def insert_retention_v2_decision(
        product_id: int, *, retention_user_id: Optional[int],
        player_id: Optional[str], trigger_kind: str,
        event_pk: Optional[int], event_name: Optional[str],
        state: dict[str, Any], guard: dict[str, Any], action: str,
        intent: Optional[str] = None, tone: Optional[str] = None,
        reason: Optional[str] = None, dry_run: bool = False,
        delivered: bool = False, detail: Optional[str] = None,
        cost_usd: Optional[float] = None) -> Optional[int]:
    """Append one decision. Returns None when this event ALREADY has one.

    The None return is the at-least-once safety net: an event can legitimately
    reach the pipeline twice (an expired lease, a reclaim after a crash), and
    the unique partial index on (product_id, event_pk) turns the second pass
    into a no-op instead of a second message to the player. `action='skipped'`
    rows are outside the index on purpose — they are diagnostics, several of
    them per event are fine, and they never send anything.
    """
    row = await _fetchrow(
        "INSERT INTO retention_v2_decisions (product_id, retention_user_id, "
        " player_id, trigger_kind, event_pk, event_name, state, guard, action, "
        " intent, tone, reason, dry_run, delivered, detail, cost_usd) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, "
        "        $12, $13, $14, $15, $16) "
        "ON CONFLICT DO NOTHING RETURNING id",
        product_id, retention_user_id, player_id, trigger_kind, event_pk,
        event_name, json.dumps(state or {}), json.dumps(guard or {}), action,
        intent, tone, reason, dry_run, delivered, detail, cost_usd,
    )
    return int(row["id"]) if row else None


async def update_retention_v2_decision(decision_id: int, *,
                                       action: Optional[str] = None,
                                       intent: Optional[str] = None,
                                       reason: Optional[str] = None,
                                       delivered: Optional[bool] = None,
                                       detail: Optional[str] = None,
                                       cost_usd: Optional[float] = None
                                       ) -> None:
    """Fill in a RESERVED decision row once the touch has been attempted.

    The row is inserted before the irreversible steps (bonus grant, send) so
    the unique index can reject a replay in front of them; what actually
    happened is written back here.
    """
    sets: list[str] = []
    args: list[Any] = [int(decision_id)]
    for col, val in (("action", action), ("intent", intent),
                     ("reason", reason), ("delivered", delivered),
                     ("detail", detail), ("cost_usd", cost_usd)):
        if val is not None:
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    if not sets:
        return
    await _execute(
        f"UPDATE retention_v2_decisions SET {', '.join(sets)} WHERE id = $1",
        *args)


async def list_retention_v2_decisions(product_id: int, page: int = 1,
                                      page_size: int = 50) -> dict[str, Any]:
    """The decision ledger for the admin tab (newest first, player joined)."""
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_v2_decisions WHERE product_id = $1",
        product_id)
    rows = await _fetch(
        # The attribution row (when the touch was actually delivered) rides
        # along, so the ledger answers "did it work?" next to "what did we
        # decide?" — the two halves of one decision.
        "SELECT d.*, u.tg_username, u.full_name, "
        "  o.replied_at, o.reply_latency_sec, o.returned_at, o.deposit_at, "
        "  o.closed AS outcome_settled "
        "FROM retention_v2_decisions d "
        "LEFT JOIN retention_users u ON u.id = d.retention_user_id "
        "LEFT JOIN retention_outcomes o ON o.decision_id = d.id "
        "WHERE d.product_id = $1 ORDER BY d.id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        for k in ("retention_user_id", "event_pk"):
            if d.get(k) is not None:
                d[k] = int(d[k])
        for k in ("replied_at", "returned_at", "deposit_at"):
            d[k] = _iso(d.get(k))
        if d.get("reply_latency_sec") is not None:
            d["reply_latency_sec"] = int(d["reply_latency_sec"])
        for k in ("state", "guard"):
            d[k] = _json_value(d[k]) or {}
        if d.get("cost_usd") is not None:
            d["cost_usd"] = float(d["cost_usd"])
        d["created_at"] = _iso(d["created_at"])
        items.append(d)
    return {"items": items, "total": int(total or 0)}


async def recent_v2_decision_exists(product_id: int, player_id: str, *,
                                    hours: int,
                                    event_name: Optional[str] = None,
                                    include_actions: Optional[
                                        tuple[str, ...]] = None) -> bool:
    """True when a v2 decision for this player exists within the window —
    the same-event cooldown (one reaction per event type per window) reads
    this. `include_actions` restricts which ledger actions count (None = any):
    the normal cooldown counts real reactions ('message', 'photo'); for
    bet_settled the caller also counts 'silence', so one considered-and-
    declined look latches the window instead of re-running a paid decision
    call on every settled bet of a losing streak."""
    q = ("SELECT EXISTS(SELECT 1 FROM retention_v2_decisions "
         "WHERE product_id = $1 AND player_id = $2 "
         "  AND created_at > now() - make_interval(hours => $3)")
    args: list[Any] = [product_id, player_id, int(hours)]
    if event_name is not None:
        args.append(event_name)
        q += f" AND event_name = ${len(args)}"
    if include_actions is not None:
        args.append(list(include_actions))
        q += f" AND action = ANY(${len(args)})"
    q += ")"
    val = await _fetchval(q, *args)
    return bool(val)


async def delete_retention_event(product_id: int, event_pk: int) -> bool:
    """Delete ONE canonical event (admin test-cleanup). Ledger rows that
    referenced it keep their event_name snapshot but drop the FK (SET NULL by
    hand — the column has no ON DELETE clause). NB: the event log also feeds
    the state resolver (loss window / recent activity), so deleting real
    partner events rewrites history — this exists for wiping simulator rows."""
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE retention_v2_decisions SET event_pk = NULL "
                "WHERE product_id = $1 AND event_pk = $2",
                product_id, int(event_pk))
            result = await conn.execute(
                "DELETE FROM retention_events WHERE product_id = $1 AND id = $2",
                product_id, int(event_pk))
    return _affected(result) == 1


async def clear_retention_events(product_id: int) -> int:
    """Delete ALL of one product's canonical events (admin test-cleanup).
    Returns the number of rows removed."""
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE retention_v2_decisions SET event_pk = NULL "
                "WHERE product_id = $1 AND event_pk IS NOT NULL",
                product_id)
            result = await conn.execute(
                "DELETE FROM retention_events WHERE product_id = $1",
                product_id)
    return _affected(result)


async def delete_retention_v2_decision(product_id: int,
                                       decision_pk: int) -> bool:
    """Delete ONE decision-ledger row (admin test-cleanup). NB: the daily
    budget and the same-event cooldown read this ledger, so deleting a row
    also 'refunds' its cost and re-arms the cooldown for that event type."""
    result = await _execute(
        "DELETE FROM retention_v2_decisions WHERE product_id = $1 AND id = $2",
        product_id, int(decision_pk))
    return _affected(result) == 1


async def clear_retention_v2_decisions(product_id: int) -> int:
    """Delete ALL of one product's decision-ledger rows (admin test-cleanup).
    Returns the number of rows removed."""
    result = await _execute(
        "DELETE FROM retention_v2_decisions WHERE product_id = $1",
        product_id)
    return _affected(result)


async def retention_v2_activity(product_id: int) -> dict[str, Any]:
    """Liveness snapshot for the v2 status header — derived from the durable
    tables (not an in-process heartbeat), so it is correct across multiple
    instances: when an event last arrived / was last processed, when the agent
    last decided, and today's decision mix by action."""
    ev = await _fetchrow(
        "SELECT MAX(created_at) AS last_event_at, "
        "       MAX(processed_at) AS last_processed_at "
        "FROM retention_events WHERE product_id = $1",
        product_id)
    last_decision = await _fetchval(
        "SELECT MAX(created_at) FROM retention_v2_decisions "
        "WHERE product_id = $1",
        product_id)
    today = await _fetch(
        "SELECT action, COUNT(*) AS n, "
        "       COUNT(*) FILTER (WHERE delivered) AS delivered "
        "FROM retention_v2_decisions "
        "WHERE product_id = $1 AND created_at >= date_trunc('day', now()) "
        "GROUP BY action",
        product_id)
    return {
        "last_event_at": _iso(ev["last_event_at"]) if ev else None,
        "last_processed_at": _iso(ev["last_processed_at"]) if ev else None,
        "last_decision_at": _iso(last_decision),
        "decisions_today": {r["action"]: int(r["n"]) for r in today},
        "delivered_today": sum(int(r["delivered"]) for r in today),
    }


async def list_retention_v2_logs(product_id: int, page: int = 1,
                                 page_size: int = 50) -> dict[str, Any]:
    """The v2 system-log view: the durable `retention_v2_*` admin events
    (decisions, simulator injections, manual runs, deletes/clears) — the same
    facts the Railway log lines carry, readable from the admin."""
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM admin_events "
        "WHERE product_id = $1 AND type LIKE 'retention_v2%'",
        product_id)
    rows = await _fetch(
        "SELECT id, type, payload, created_at FROM admin_events "
        "WHERE product_id = $1 AND type LIKE 'retention_v2%' "
        "ORDER BY id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset,
    )
    return {"items": [{
        "id": int(r["id"]),
        "type": r["type"],
        "payload": _json_value(r["payload"]) or {},
        "created_at": _iso(r["created_at"]),
    } for r in rows], "total": int(total or 0)}


async def retention_v2_cost_today(product_id: int) -> float:
    """Summed decision-ledger cost since midnight UTC — the daily-budget stop
    switch reads this before every new decision.

    Truncate in UTC explicitly: date_trunc('day', now()) truncates in the DB
    session timezone, so on a non-UTC Postgres the budget window would silently
    disagree with the day boundary the rest of the retention pacing uses."""
    val = await _fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM retention_v2_decisions "
        "WHERE product_id = $1 "
        "AND created_at >= date_trunc('day', now() at time zone 'utc') "
        "                  at time zone 'utc'",
        product_id,
    )
    return float(val or 0)


# ---------------------------------------------------------------------------
# Outcome attribution — "did the touch work?"
#
# Every delivered touch (proactive message, photo/video, CTA-carrying reply)
# writes one retention_outcomes row at send time; the sweep below fills in what
# happened afterwards. The dimensions are denormalized onto the row because the
# sources are transient (retention_events are pruned, media can be deleted), so
# a later recomputation would silently change history.
# ---------------------------------------------------------------------------
async def record_retention_outcome(
        product_id: int, *, retention_user_id: Optional[int],
        player_id: Optional[str], kind: str,
        session_id: Optional[str] = None,
        decision_id: Optional[int] = None, rule_id: Optional[int] = None,
        event_name: Optional[str] = None, action: Optional[str] = None,
        tone: Optional[str] = None, photo_id: Optional[int] = None,
        media_type: Optional[str] = None, link_url: Optional[str] = None,
        cost_usd: Optional[float] = None,
        holdout_group: Optional[str] = None) -> int:
    """Open one attribution row for a touch that was actually delivered
    (kind='holdout' opens the VIRTUAL row for a held-out control player —
    no touch went out, but the would-have-been moment and the following
    conversions are recorded so uplift has a base rate to compare against)."""
    row = await _fetchrow(
        "INSERT INTO retention_outcomes (product_id, retention_user_id, "
        " player_id, session_id, kind, decision_id, rule_id, event_name, "
        " action, tone, photo_id, media_type, link_url, cost_usd, "
        " holdout_group_at_time) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, "
        "        $15) "
        "RETURNING id",
        product_id, retention_user_id, player_id, session_id, kind,
        decision_id, rule_id, event_name, action, tone, photo_id, media_type,
        link_url, cost_usd, holdout_group,
    )
    return int(row["id"])


async def attribute_retention_outcomes(
        product_id: int, *, reply_window_hours: int,
        conversion_window_hours: int, limit: int = 500) -> int:
    """Fill the outcome fields of this product's open rows. Returns rows touched.

    One statement: for each open row, look forward from `sent_at` for (a) the
    player's own messages in ANY session of his Telegram identity — the idle
    rollover opens a new session, so keying on the session would lose the reply
    — and (b) casino events in the conversion window. A row is `closed` once
    both windows have elapsed, after which the sweep never looks at it again
    (which is also what makes the effectiveness numbers stable).
    """
    window = max(int(reply_window_hours), int(conversion_window_hours))
    result = await _execute(
        "WITH open AS ("
        "  SELECT o.id, o.product_id, o.player_id, o.sent_at, ru.tg_user_id "
        "  FROM retention_outcomes o "
        "  LEFT JOIN retention_users ru ON ru.id = o.retention_user_id "
        "  WHERE o.product_id = $1 AND NOT o.closed "
        "  ORDER BY o.id LIMIT $5"
        ") "
        "UPDATE retention_outcomes t SET "
        "  replied_at = COALESCE(t.replied_at, rep.first_reply), "
        "  reply_latency_sec = COALESCE(t.reply_latency_sec, "
        "    CASE WHEN rep.first_reply IS NOT NULL "
        "         THEN EXTRACT(EPOCH FROM rep.first_reply - t.sent_at)::int "
        "    END), "
        "  player_msgs = GREATEST(t.player_msgs, COALESCE(rep.msgs, 0)), "
        "  returned_at = COALESCE(t.returned_at, conv.returned_at), "
        "  deposit_at = COALESCE(t.deposit_at, conv.deposit_at), "
        "  closed = now() >= t.sent_at + make_interval(hours => $4), "
        "  attributed_at = now() "
        "FROM open o "
        "LEFT JOIN LATERAL ("
        "  SELECT MIN(m.created_at) AS first_reply, COUNT(*) AS msgs "
        "  FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
        "  WHERE o.tg_user_id IS NOT NULL AND s.product_id = o.product_id "
        "    AND s.tg_user_id = o.tg_user_id AND m.role = 'user' "
        "    AND m.created_at > o.sent_at "
        "    AND m.created_at <= o.sent_at + make_interval(hours => $2)"
        ") rep ON TRUE "
        "LEFT JOIN LATERAL ("
        "  SELECT MIN(e.ts) FILTER (WHERE e.event_name = 'deposit_confirmed') "
        "           AS deposit_at, "
        "         MIN(e.ts) AS returned_at "
        "  FROM retention_events e "
        "  WHERE o.player_id IS NOT NULL AND o.player_id <> '' "
        "    AND e.product_id = o.product_id AND e.player_id = o.player_id "
        "    AND e.ts > o.sent_at "
        "    AND e.ts <= o.sent_at + make_interval(hours => $3)"
        ") conv ON TRUE "
        "WHERE t.id = o.id",
        product_id, int(reply_window_hours), int(conversion_window_hours),
        window, int(limit),
    )
    return _affected(result)


async def recent_touch_outcomes(product_id: int, player_id: str,
                                limit: int = 6) -> list[dict[str, Any]]:
    """This player's last proactive touches WITH how he reacted — the feedback
    the agent's decision call reads before choosing to write again."""
    if not player_id:
        return []
    rows = await _fetch(
        "SELECT sent_at, event_name, action, tone, photo_id, link_url, "
        "       replied_at, reply_latency_sec, player_msgs, deposit_at, "
        "       returned_at, closed "
        "FROM retention_outcomes "
        "WHERE product_id = $1 AND player_id = $2 AND kind = 'proactive' "
        "ORDER BY sent_at DESC LIMIT $3",
        product_id, player_id, int(limit),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "sent_at": _iso(r["sent_at"]),
            "event_name": r["event_name"],
            "action": r["action"],
            "tone": r["tone"],
            "with_photo": r["photo_id"] is not None,
            "with_link": bool(r["link_url"]),
            "replied": r["replied_at"] is not None,
            "reply_latency_sec": (int(r["reply_latency_sec"])
                                  if r["reply_latency_sec"] is not None else None),
            "player_msgs": int(r["player_msgs"] or 0),
            "deposited": r["deposit_at"] is not None,
            "returned": r["returned_at"] is not None,
            "settled": bool(r["closed"]),
        })
    return out


# ---------------------------------------------------------------------------
# RG guard: status, signal config, the append-only compliance audit (DOC-3)
# ---------------------------------------------------------------------------
_RG_PROFILE_FIELDS = ("rg_status", "rg_status_until", "marketing_consent",
                      "rg_signals", "tz")


async def set_rg_status(product_id: int, rid: int, status: str, *,
                        source: str = "manual",
                        until: Any = None) -> None:
    """Set a player's RG status (manual admin action or the lazy auto-clear).
    The partner feed writes through update_retention_profile instead."""
    await _execute(
        "UPDATE retention_users SET rg_status = $3, rg_status_source = $4, "
        "rg_status_until = $5, rg_updated_at = now(), updated_at = now() "
        "WHERE product_id = $1 AND id = $2",
        product_id, rid, status, source, _as_ts(until),
    )


async def update_rg_profile(product_id: int, player_id: str,
                            fields: dict[str, Any], *,
                            source: str = "casino_push") -> int:
    """RG/channel fields arriving on the partner player-update push. Partial:
    absent fields stay untouched. Returns rows affected."""
    sets: list[str] = []
    args: list[Any] = [product_id, player_id]
    if "rg_status" in fields:
        args.append(str(fields["rg_status"]))
        sets.append(f"rg_status = ${len(args)}")
        args.append(source)
        sets.append(f"rg_status_source = ${len(args)}")
        sets.append("rg_updated_at = now()")
    if "rg_status_until" in fields:
        args.append(_as_ts(fields["rg_status_until"]))
        sets.append(f"rg_status_until = ${len(args)}")
    if "marketing_consent" in fields:
        args.append(bool(fields["marketing_consent"])
                    if fields["marketing_consent"] is not None else None)
        sets.append(f"marketing_consent = ${len(args)}")
    if "rg_flags" in fields and isinstance(fields["rg_flags"], dict):
        args.append(json.dumps(fields["rg_flags"]))
        sets.append(f"rg_signals = ${len(args)}::jsonb")
    if "timezone" in fields and fields["timezone"]:
        args.append(str(fields["timezone"])[:64])
        sets.append(f"tz = ${len(args)}")
    for f in ("email_opt_in", "email_verified", "push_opt_in",
              "push_available", "in_app_available", "sms_opt_in"):
        if f in fields and fields[f] is not None:
            args.append(bool(fields[f]))
            sets.append(f"{f} = ${len(args)}")
    if "channel_prefs" in fields and isinstance(fields["channel_prefs"], dict):
        args.append(json.dumps(fields["channel_prefs"]))
        sets.append(f"channel_prefs = ${len(args)}::jsonb")
    if not sets:
        return 0
    sets.append("updated_at = now()")
    result = await _execute(
        f"UPDATE retention_users SET {', '.join(sets)} "
        "WHERE product_id = $1 AND player_id = $2",
        *args,
    )
    return _affected(result)


async def insert_rg_audit(product_id: int, *, player_id: str, trigger: str,
                          decision: str, reason: str,
                          signals: list[str],
                          status_snapshot: Optional[str],
                          allow_general: bool) -> int:
    row = await _fetchrow(
        "INSERT INTO rg_guard_audit (product_id, player_id, trigger, "
        " decision, reason, signals_triggered, rg_status_snapshot, "
        " allow_general) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8) RETURNING id",
        product_id, player_id, trigger, decision, reason,
        json.dumps(signals or []), status_snapshot, allow_general,
    )
    return int(row["id"])


async def list_rg_audit(product_id: int, *, page: int = 1,
                        page_size: int = 50,
                        decision: Optional[str] = None) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    where = "product_id = $1" + (" AND decision = $4" if decision else "")
    args: list[Any] = [product_id, page_size, offset]
    if decision:
        args.append(decision)
    total = await _fetchval(
        f"SELECT COUNT(*) FROM rg_guard_audit WHERE {where}",
        *([product_id] + ([decision] if decision else [])))
    rows = await _fetch(
        f"SELECT * FROM rg_guard_audit WHERE {where} "
        "ORDER BY id DESC LIMIT $2 OFFSET $3", *args)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["signals_triggered"] = _json_value(d.get("signals_triggered")) or []
        _iso_fields(d, "decided_at")
        items.append(d)
    return {"items": items, "total": int(total or 0)}


async def rg_audit_summary(product_id: int) -> dict[str, Any]:
    rows = await _fetch(
        "SELECT decision, COUNT(*) AS n FROM rg_guard_audit "
        "WHERE product_id = $1 GROUP BY decision", product_id)
    by_decision = {str(r["decision"]): int(r["n"]) for r in rows}
    statuses = await _fetch(
        "SELECT COALESCE(rg_status, 'unknown') AS status, COUNT(*) AS n "
        "FROM retention_users WHERE product_id = $1 GROUP BY 1", product_id)
    return {"checks": sum(by_decision.values()), "by_decision": by_decision,
            "players_by_status": {str(r["status"]): int(r["n"])
                                  for r in statuses}}


async def list_rg_signal_config(product_id: int,
                                only_enabled: bool = False
                                ) -> list[dict[str, Any]]:
    q = ("SELECT * FROM rg_signal_config WHERE product_id = $1 "
         + ("AND enabled " if only_enabled else "") + "ORDER BY signal_key")
    rows = await _fetch(q, product_id)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["params"] = _json_value(d.get("params")) or {}
        _iso_fields(d, "updated_at")
        items.append(d)
    return items


async def upsert_rg_signal_config(product_id: int, *, signal_key: str,
                                  enabled: bool, block_class: str,
                                  params: dict[str, Any], source: str,
                                  updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO rg_signal_config (product_id, signal_key, enabled, "
        " block_class, params, source, updated_by) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7) "
        "ON CONFLICT (product_id, signal_key) DO UPDATE SET "
        " enabled = EXCLUDED.enabled, block_class = EXCLUDED.block_class, "
        " params = EXCLUDED.params, source = EXCLUDED.source, "
        " updated_by = EXCLUDED.updated_by, updated_at = now() "
        "RETURNING *",
        product_id, signal_key, enabled, block_class,
        json.dumps(params or {}), source, updated_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    d["params"] = _json_value(d.get("params")) or {}
    _iso_fields(d, "updated_at")
    return d


async def rg_chase_count(product_id: int, player_id: str, *,
                         window_days: int, hours_after_loss: int) -> int:
    """Computed chase_pattern signal: deposits made within N hours after a
    losing settled bet, inside the window."""
    val = await _fetchval(
        "SELECT COUNT(*) FROM retention_events d "
        "WHERE d.product_id = $1 AND d.player_id = $2 "
        "  AND d.event_name = 'deposit_confirmed' "
        "  AND d.ts > now() - make_interval(days => $3) "
        "  AND EXISTS (SELECT 1 FROM retention_events l "
        "      WHERE l.product_id = d.product_id "
        "        AND l.player_id = d.player_id "
        "        AND l.event_name = 'bet_settled' "
        f"       AND {_payload_num('win_amount')} < {_payload_num('amount')} "
        "        AND l.ts < d.ts "
        "        AND l.ts > d.ts - make_interval(hours => $4))",
        product_id, player_id, int(window_days), int(hours_after_loss),
    )
    return int(val or 0)


async def rg_deposit_counts(product_id: int, player_id: str) -> tuple[int, int]:
    """(deposits last 7d, deposits last 30d) for the frequency-spike signal."""
    row = await _fetchrow(
        "SELECT COUNT(*) FILTER (WHERE ts > now() - interval '7 days') AS d7, "
        "       COUNT(*) FILTER (WHERE ts > now() - interval '30 days') AS d30 "
        "FROM retention_events "
        "WHERE product_id = $1 AND player_id = $2 "
        "  AND event_name = 'deposit_confirmed'",
        product_id, player_id,
    )
    return int(row["d7"] or 0), int(row["d30"] or 0)


# ---------------------------------------------------------------------------
# Template library (DOC-6b)
# ---------------------------------------------------------------------------
def _row_to_template(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    d["localizations"] = _json_value(d.get("localizations")) or {}
    d["variables"] = _json_value(d.get("variables")) or {}
    d["ab_variants"] = _json_value(d.get("ab_variants"))
    _iso_fields(d, "created_at", "updated_at")
    return d


async def list_templates(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_templates WHERE product_id = $1 "
        "ORDER BY template_key, version DESC", product_id)
    return [_row_to_template(r) for r in rows]


async def get_template(product_id: int, template_key: str
                       ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT * FROM retention_templates WHERE product_id = $1 "
        "AND template_key = $2 ORDER BY version DESC LIMIT 1",
        product_id, template_key)
    return _row_to_template(row) if row else None


async def upsert_template(product_id: int, *, template_key: str, type_: str,
                          mode: str, intent: str,
                          localizations: dict[str, Any],
                          variables: dict[str, Any], status: str,
                          is_starter: bool,
                          updated_by: Optional[str],
                          version: int = 1) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_templates (product_id, template_key, type, "
        " version, status, mode, intent, localizations, variables, "
        " is_starter, updated_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11) "
        "ON CONFLICT (product_id, template_key, version) DO UPDATE SET "
        " type = EXCLUDED.type, status = EXCLUDED.status, "
        " mode = EXCLUDED.mode, intent = EXCLUDED.intent, "
        " localizations = EXCLUDED.localizations, "
        " variables = EXCLUDED.variables, "
        " is_starter = EXCLUDED.is_starter, "
        " updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
        product_id, template_key, type_, int(version), status, mode, intent,
        json.dumps(localizations or {}), json.dumps(variables or {}),
        is_starter, updated_by,
    )
    return _row_to_template(row)


async def delete_template(product_id: int, template_key: str) -> bool:
    result = await _execute(
        "DELETE FROM retention_templates "
        "WHERE product_id = $1 AND template_key = $2",
        product_id, template_key)
    return not result.endswith("0")


# ---------------------------------------------------------------------------
# Channel abstraction (DOC-7): channel config + the delivery ledger
# ---------------------------------------------------------------------------
async def list_channel_config(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_channel_config WHERE product_id = $1 "
        "ORDER BY priority, channel", product_id)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["config"] = _json_value(d.get("config")) or {}
        _iso_fields(d, "updated_at")
        items.append(d)
    return items


async def upsert_channel_config(product_id: int, *, channel: str,
                                enabled: bool, priority: int,
                                channel_config: dict[str, Any],
                                updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_channel_config (product_id, channel, enabled, "
        " priority, config, updated_by) VALUES ($1, $2, $3, $4, $5::jsonb, $6) "
        "ON CONFLICT (product_id, channel) DO UPDATE SET "
        " enabled = EXCLUDED.enabled, priority = EXCLUDED.priority, "
        " config = EXCLUDED.config, updated_by = EXCLUDED.updated_by, "
        " updated_at = now() RETURNING *",
        product_id, channel, enabled, int(priority),
        json.dumps(channel_config or {}), updated_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    d["config"] = _json_value(d.get("config")) or {}
    _iso_fields(d, "updated_at")
    return d


def _row_to_delivery(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    if d.get("retention_user_id") is not None:
        d["retention_user_id"] = int(d["retention_user_id"])
    if "payload" in d:
        d["payload"] = _json_value(d.get("payload")) or {}
    if d.get("priority") is not None:
        d["priority"] = int(d["priority"])
    _iso_fields(d, "created_at", "updated_at", "next_attempt_at",
                "scheduled_at", "locked_until")
    return d


async def get_delivery(product_id: int, delivery_id: str
                       ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT * FROM retention_deliveries "
        "WHERE product_id = $1 AND delivery_id = $2",
        product_id, delivery_id)
    return _row_to_delivery(row) if row else None


async def upsert_delivery(product_id: int, delivery_id: str, *,
                          player_id: Optional[str],
                          retention_user_id: Optional[int],
                          channel: str, intended_channel: Optional[str],
                          status: str, title: Optional[str] = None,
                          body: Optional[str] = None,
                          cta_url: Optional[str] = None,
                          decision_id: Optional[int] = None) -> None:
    await _execute(
        "INSERT INTO retention_deliveries (product_id, delivery_id, "
        " player_id, retention_user_id, channel, intended_channel, status, "
        " title, body, cta_url, decision_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
        "ON CONFLICT (product_id, delivery_id) DO UPDATE SET "
        " status = EXCLUDED.status, updated_at = now()",
        product_id, delivery_id, player_id, retention_user_id, channel,
        intended_channel, status, title, body, cta_url, decision_id,
    )


async def update_delivery(product_id: int, delivery_id: str, *,
                          status: Optional[str] = None,
                          provider_ref: Optional[str] = None,
                          fail_reason: Optional[str] = None,
                          permanent_fail: Optional[bool] = None,
                          next_attempt_at: Any = UNSET,
                          attempts: Optional[int] = None) -> None:
    sets = ["updated_at = now()"]
    args: list[Any] = [product_id, delivery_id]
    if status is not None:
        args.append(status)
        sets.append(f"status = ${len(args)}")
    if provider_ref is not None:
        args.append(provider_ref)
        sets.append(f"provider_ref = ${len(args)}")
    if fail_reason is not None:
        args.append(fail_reason)
        sets.append(f"fail_reason = ${len(args)}")
    if permanent_fail is not None:
        args.append(bool(permanent_fail))
        sets.append(f"permanent_fail = ${len(args)}")
    if next_attempt_at is not UNSET:
        args.append(_as_ts(next_attempt_at))
        sets.append(f"next_attempt_at = ${len(args)}")
    if attempts is not None:
        args.append(int(attempts))
        sets.append(f"attempts = ${len(args)}")
    await _execute(
        f"UPDATE retention_deliveries SET {', '.join(sets)} "
        "WHERE product_id = $1 AND delivery_id = $2",
        *args,
    )


async def due_delivery_retries(product_id: int, limit: int = 20, *,
                               channels: Optional[Sequence[str]] = None
                               ) -> list[dict[str, Any]]:
    """Failed rows due for another attempt, optionally only on some channels.

    The channel filter is what keeps the two retry owners disjoint: the send
    worker claims the channels it can actually put on the wire, this sweep
    takes the rest. A retrier that picks up a row it cannot deliver does not
    merely fail — it destroys the row on the wrong transport.
    """
    sql = ("SELECT * FROM retention_deliveries "
           "WHERE product_id = $1 AND status = 'failed' AND NOT permanent_fail "
           "  AND next_attempt_at IS NOT NULL AND next_attempt_at <= now()")
    args: list[Any] = [product_id, int(limit)]
    if channels is not None:
        args.append([str(c) for c in channels])
        sql += f" AND channel = ANY(${len(args)}::text[])"
    rows = await _fetch(sql + " ORDER BY next_attempt_at LIMIT $2", *args)
    return [_row_to_delivery(r) for r in rows]


async def enqueue_delivery(product_id: int, delivery_id: str, *,
                           player_id: Optional[str],
                           retention_user_id: Optional[int],
                           channel: str,
                           intended_channel: Optional[str] = None,
                           decision_id: Optional[int] = None,
                           priority: int = 3,
                           scheduled_at: Any = None,
                           payload: Optional[dict[str, Any]] = None,
                           title: Optional[str] = None,
                           body: Optional[str] = None,
                           cta_url: Optional[str] = None
                           ) -> Optional[int]:
    """Queue a touch for the send worker. Returns None if it already exists.

    The None return is the send-side half of at-least-once safety: the caller
    derives `delivery_id` deterministically from the decision, so a replayed
    decision lands on the UNIQUE (product_id, delivery_id) and enqueues
    nothing instead of sending the same message twice.
    """
    row = await _fetchrow(
        "INSERT INTO retention_deliveries (product_id, delivery_id, player_id, "
        " retention_user_id, channel, intended_channel, decision_id, status, "
        " priority, scheduled_at, payload, title, body, cta_url) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, 'queued', $8, "
        "        COALESCE($9, now()), $10::jsonb, $11, $12, $13) "
        "ON CONFLICT (product_id, delivery_id) DO NOTHING RETURNING id",
        product_id, delivery_id, player_id, retention_user_id, channel,
        intended_channel, decision_id, int(priority), _as_ts(scheduled_at),
        json.dumps(payload or {}), title, body, cta_url,
    )
    return int(row["id"]) if row else None


async def claim_deliveries(product_id: int, *, limit: int = 50,
                           lease_sec: int = 120,
                           worker_id: Optional[str] = None,
                           max_priority: int = 5,
                           max_attempts: int = 0,
                           channels: Optional[Sequence[str]] = None
                           ) -> list[dict[str, Any]]:
    """Lease a batch of due touches, best lane first, oldest schedule first.

    Same discipline as the event queue: 'sending' + locked_until, closed by
    mark_delivery_sent / mark_delivery_failed / reschedule_delivery, and
    anything abandoned comes back via reclaim_expired_delivery_leases.

    `channels` restricts the claim to the transports the caller can actually
    deliver on — a claim is destructive (the row is closed on the transport
    that took it), so claiming a channel you cannot send is worse than leaving
    it queued.

    `max_attempts` (0 = unbounded) is the delivery queue's dead-letter ceiling,
    the counterpart of `event_max_attempts`. Without it a row that keeps
    failing transiently — a rotated bot token, a partner endpoint that is down
    — is re-claimed every backoff step forever, and every attempt re-bills the
    original generation into ai_interaction_logs.
    """
    args: list[Any] = [product_id, int(limit), float(max(lease_sec, 1)),
                       worker_id, int(max_priority)]
    extra = ""
    if channels is not None:
        args.append([str(c) for c in channels])
        extra += f" AND channel = ANY(${len(args)}::text[])"
    if int(max_attempts) > 0:
        args.append(int(max_attempts))
        extra += f" AND attempts < ${len(args)}"
    rows = await _fetch(
        "UPDATE retention_deliveries SET status = 'sending', "
        "  attempts = attempts + 1, worker_id = $4, updated_at = now(), "
        "  locked_until = now() + make_interval(secs => $3) "
        "WHERE id IN (SELECT id FROM retention_deliveries "
        "             WHERE product_id = $1 "
        "               AND status IN ('queued', 'failed') "
        "               AND NOT permanent_fail "
        "               AND priority <= $5 "
        "               AND scheduled_at <= now() "
        "               AND (next_attempt_at IS NULL "
        "                    OR next_attempt_at <= now()) "
        + extra +
        "             ORDER BY priority ASC, scheduled_at ASC "
        "             LIMIT $2 FOR UPDATE SKIP LOCKED) "
        "RETURNING *",
        *args,
    )
    out = [_row_to_delivery(r) for r in rows]
    out.sort(key=lambda d: (d.get("priority") or 3, d["id"]))
    return out


async def dead_letter_stale_deliveries(product_id: int, max_attempts: int
                                       ) -> int:
    """Close out rows that exhausted the attempt ceiling.

    The ceiling in `claim_deliveries` stops the retry loop, but a row left at
    `status='failed', permanent_fail=false` is indistinguishable from one still
    waiting its turn. Flipping it makes the dead ones visible (and countable)
    on the deliveries view instead of silently invisible.
    """
    if int(max_attempts) <= 0:
        return 0
    result = await _execute(
        "UPDATE retention_deliveries SET permanent_fail = TRUE, "
        "  next_attempt_at = NULL, updated_at = now(), "
        "  fail_reason = COALESCE(fail_reason, '') || ' (max attempts)' "
        "WHERE product_id = $1 AND status = 'failed' "
        "  AND NOT permanent_fail AND attempts >= $2",
        product_id, int(max_attempts))
    return _affected(result)


async def mark_delivery_sent(delivery_pk: int, *,
                             provider_ref: Optional[str] = None,
                             outcome_id: Optional[int] = None) -> None:
    await _execute(
        "UPDATE retention_deliveries SET status = 'sent', locked_until = NULL, "
        "  provider_ref = COALESCE($2, provider_ref), "
        "  outcome_id = COALESCE($3, outcome_id), "
        "  fail_reason = NULL, next_attempt_at = NULL, updated_at = now() "
        "WHERE id = $1",
        int(delivery_pk), provider_ref, outcome_id,
    )


async def mark_delivery_failed(delivery_pk: int, reason: str, *,
                               permanent: bool = False,
                               backoff_sec: Optional[int] = None) -> None:
    """Close a leased delivery that failed. Permanent = never retried again."""
    await _execute(
        "UPDATE retention_deliveries SET status = 'failed', "
        "  locked_until = NULL, permanent_fail = $3, fail_reason = $2, "
        "  next_attempt_at = CASE WHEN $3 THEN NULL "
        "    ELSE now() + make_interval(secs => $4) END, "
        "  updated_at = now() WHERE id = $1",
        int(delivery_pk), (reason or "")[:2000], bool(permanent),
        float(max(int(backoff_sec or 60), 1)),
    )


async def reschedule_delivery(delivery_pk: int, *, delay_sec: float) -> None:
    """Put a leased delivery back in the queue after `delay_sec`.

    Not a failure: this is what the send worker does when the channel's token
    bucket is empty. Sleeping on the claim instead would hold a lease (and a
    worker slot) doing nothing while a 10k burst drains at 25/s.
    """
    await _execute(
        "UPDATE retention_deliveries SET status = 'queued', "
        "  locked_until = NULL, attempts = GREATEST(attempts - 1, 0), "
        "  scheduled_at = now() + make_interval(secs => $2), "
        "  updated_at = now() WHERE id = $1",
        int(delivery_pk), float(max(delay_sec, 0.1)),
    )


async def release_deliveries(delivery_pks: list[int]) -> int:
    """Hand leased deliveries back untouched (graceful shutdown)."""
    if not delivery_pks:
        return 0
    result = await _execute(
        "UPDATE retention_deliveries SET status = 'queued', "
        "  locked_until = NULL, attempts = GREATEST(attempts - 1, 0), "
        "  worker_id = NULL, updated_at = now() "
        "WHERE id = ANY($1::bigint[]) AND status = 'sending'",
        [int(x) for x in delivery_pks],
    )
    return _affected(result)


async def reclaim_expired_delivery_leases(*, limit: int = 1000) -> int:
    """Return 'sending' rows whose lease expired to the queue."""
    result = await _execute(
        "UPDATE retention_deliveries SET status = 'queued', "
        "  locked_until = NULL, updated_at = now(), "
        "  fail_reason = COALESCE(fail_reason, 'lease expired') "
        "WHERE id IN (SELECT id FROM retention_deliveries "
        "             WHERE status = 'sending' AND locked_until < now() "
        "             ORDER BY locked_until LIMIT $1 FOR UPDATE SKIP LOCKED)",
        int(limit),
    )
    return _affected(result)


# ---------------------------------------------------------------------------
# Background job pacing (retention_worker_jobs) + outbound token bucket
# ---------------------------------------------------------------------------
async def claim_worker_job(product_id: int, job: str, interval_sec: int, *,
                           lease_sec: Optional[int] = None) -> bool:
    """Atomic "is it my turn to run (product, job)?".

    Replaces the in-process pacing dicts, which reset on every deploy and, with
    more than one worker, let both run the same sweep at the same time. The
    UPDATE moves next_run_at forward in the same statement that tests it, so
    exactly one caller wins per interval.

    It is also a LEASE: next_run_at jumps by `max(interval, lease)`, so a sweep
    that runs longer than its own interval cannot be started again underneath
    itself. `finish_worker_job` pulls the next run back to the real interval,
    so the lease only costs something when a run actually overruns or dies.
    """
    interval = float(max(int(interval_sec), 1))
    lease = float(max(int(lease_sec or 0), 0))
    row = await _fetchrow(
        "INSERT INTO retention_worker_jobs (product_id, job, last_run_at, "
        " next_run_at, last_status) "
        "VALUES ($1, $2, now(), "
        "        now() + make_interval(secs => GREATEST($3::float8, "
        "                                               $4::float8)), "
        "        'running') "
        "ON CONFLICT (product_id, job) DO UPDATE "
        "  SET last_run_at = now(), "
        "      next_run_at = now() + make_interval(secs => "
        "        GREATEST($3::float8, $4::float8)), "
        "      last_status = 'running' "
        "  WHERE retention_worker_jobs.next_run_at IS NULL "
        "     OR retention_worker_jobs.next_run_at <= now() "
        "RETURNING product_id",
        product_id, job, interval, lease,
    )
    return row is not None


async def finish_worker_job(product_id: int, job: str, *, status: str,
                            duration_ms: Optional[int] = None,
                            error: Optional[str] = None,
                            interval_sec: Optional[int] = None) -> None:
    """Record how a paced job ended (the admin's background-jobs health view).

    Passing `interval_sec` also releases the claim lease: the next run is
    re-anchored to the real interval from the START of this run, so a job that
    leased extra headroom does not skip its next slot.
    """
    sets = ["last_status = $3", "duration_ms = $4", "last_error = $5"]
    args: list[Any] = [product_id, job, status[:64],
                       int(duration_ms) if duration_ms is not None else None,
                       (error or None) and str(error)[:1000]]
    if interval_sec is not None:
        args.append(float(max(int(interval_sec), 1)))
        sets.append(f"next_run_at = last_run_at + make_interval(secs => ${len(args)})")
    await _execute(
        f"UPDATE retention_worker_jobs SET {', '.join(sets)} "
        "WHERE product_id = $1 AND job = $2",
        *args,
    )


async def list_worker_jobs(product_ids: Optional[list[int]] = None
                           ) -> list[dict[str, Any]]:
    """Background job health, newest run first. None = every product."""
    sql = ("SELECT j.*, p.name AS product_name FROM retention_worker_jobs j "
           "JOIN products p ON p.id = j.product_id")
    args: list[Any] = []
    if product_ids is not None:
        if not product_ids:
            return []
        args.append([int(x) for x in product_ids])
        sql += " WHERE j.product_id = ANY($1::int[])"
    sql += " ORDER BY j.last_run_at DESC NULLS LAST"
    rows = await _fetch(sql, *args)
    out = []
    for r in rows:
        d = dict(r)
        _iso_fields(d, "last_run_at", "next_run_at")
        out.append(d)
    return out


async def take_rate_token(scope: str, *, rate_per_sec: float, burst: float,
                          n: float = 1.0) -> bool:
    """Token bucket: True if `n` tokens were available for `scope`, else False.

    One atomic statement with a LAZY refill (tokens accrue from the elapsed
    time since the last take), so there is no refill timer to run and no state
    outside Postgres — which is the point: the limit has to hold across worker
    instances, and an in-process limiter multiplies by the replica count
    exactly when a broadcast is hammering the channel.

    False is not an error — the caller reschedules the touch a moment later.
    """
    rate = max(float(rate_per_sec), 0.0)
    cap = max(float(burst), float(n))
    if rate <= 0:
        return True  # unlimited scope: no bucket, no accounting
    # Every numeric parameter is cast explicitly: inside an expression
    # (`$2 - $4`) asyncpg has no function signature to infer from, prepares
    # both as `unknown`, and Postgres refuses the ambiguous operator.
    row = await _fetchrow(
        "INSERT INTO retention_rate_budget (scope, tokens, rate_per_sec, burst) "
        "VALUES ($1, $2::float8 - $4::float8, $3::float8, $2::float8) "
        "ON CONFLICT (scope) DO UPDATE SET "
        "  tokens = LEAST(EXCLUDED.burst, retention_rate_budget.tokens "
        "    + EXTRACT(EPOCH FROM now() - retention_rate_budget.updated_at) "
        "      * EXCLUDED.rate_per_sec) - $4::float8, "
        "  rate_per_sec = EXCLUDED.rate_per_sec, burst = EXCLUDED.burst, "
        "  updated_at = now() "
        "WHERE retention_rate_budget.tokens "
        "  + EXTRACT(EPOCH FROM now() - retention_rate_budget.updated_at) "
        "    * EXCLUDED.rate_per_sec >= $4::float8 "
        "RETURNING tokens",
        scope, cap, rate, float(n),
    )
    return row is not None


async def prune_rate_budget(idle_hours: int = 24) -> int:
    """Drop buckets nobody has touched (per-chat scopes are unbounded)."""
    result = await _execute(
        "DELETE FROM retention_rate_budget "
        "WHERE updated_at < now() - make_interval(hours => $1)",
        int(idle_hours))
    return _affected(result)


async def list_deliveries(product_id: int, status: Optional[str] = None,
                          page: int = 1, page_size: int = 50
                          ) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    where = "product_id = $1" + (" AND status = $4" if status else "")
    args: list[Any] = [product_id, page_size, offset]
    if status:
        args.append(status)
    total = await _fetchval(
        f"SELECT COUNT(*) FROM retention_deliveries WHERE {where}",
        *([product_id] + ([status] if status else [])))
    rows = await _fetch(
        f"SELECT * FROM retention_deliveries WHERE {where} "
        "ORDER BY id DESC LIMIT $2 OFFSET $3", *args)
    return {"items": [_row_to_delivery(r) for r in rows],
            "total": int(total or 0)}


# ---------------------------------------------------------------------------
# Journey engine (DOC-6a): definitions, enrollments, step log
# ---------------------------------------------------------------------------
def _row_to_journey(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    for jf in ("trigger", "entry_conditions", "exit_conditions", "steps",
               "metadata"):
        d[jf] = _json_value(d.get(jf)) or ({} if jf in ("trigger", "metadata")
                                           else [])
    _iso_fields(d, "created_at", "updated_at")
    return d


async def list_journeys(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_journeys WHERE product_id = $1 "
        "ORDER BY journey_key, version DESC", product_id)
    return [_row_to_journey(r) for r in rows]


async def list_active_journeys(product_id: int, *,
                               trigger_type: Optional[str] = None
                               ) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_journeys "
        "WHERE product_id = $1 AND status = 'active' "
        + ("AND trigger->>'type' = $2 " if trigger_type else "")
        + "ORDER BY priority, journey_key",
        *([product_id, trigger_type] if trigger_type else [product_id]))
    return [_row_to_journey(r) for r in rows]


async def get_journey(product_id: int, journey_key: str, *,
                      version: Optional[int] = None
                      ) -> Optional[dict[str, Any]]:
    if version is not None:
        row = await _fetchrow(
            "SELECT * FROM retention_journeys WHERE product_id = $1 "
            "AND journey_key = $2 AND version = $3",
            product_id, journey_key, int(version))
    else:
        row = await _fetchrow(
            "SELECT * FROM retention_journeys WHERE product_id = $1 "
            "AND journey_key = $2 ORDER BY version DESC LIMIT 1",
            product_id, journey_key)
    return _row_to_journey(row) if row else None


async def upsert_journey(product_id: int, journey: dict[str, Any], *,
                         updated_by: Optional[str] = None,
                         is_starter: bool = False) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_journeys (product_id, journey_key, name, "
        " version, status, trigger, entry_conditions, exit_conditions, "
        " steps, dry_run, priority, metadata, is_starter, updated_by) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, "
        "        $9::jsonb, $10, $11, $12::jsonb, $13, $14) "
        "ON CONFLICT (product_id, journey_key, version) DO UPDATE SET "
        " name = EXCLUDED.name, status = EXCLUDED.status, "
        " trigger = EXCLUDED.trigger, "
        " entry_conditions = EXCLUDED.entry_conditions, "
        " exit_conditions = EXCLUDED.exit_conditions, "
        " steps = EXCLUDED.steps, dry_run = EXCLUDED.dry_run, "
        " priority = EXCLUDED.priority, metadata = EXCLUDED.metadata, "
        " is_starter = EXCLUDED.is_starter, "
        " updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
        product_id, str(journey["journey_key"]),
        str(journey.get("name") or journey["journey_key"]),
        int(journey.get("version") or 1),
        str(journey.get("status") or "draft"),
        json.dumps(journey.get("trigger") or {}),
        json.dumps(journey.get("entry_conditions") or []),
        json.dumps(journey.get("exit_conditions") or []),
        json.dumps(journey.get("steps") or []),
        bool(journey.get("dry_run", True)),
        int(journey.get("priority") or 3),
        json.dumps(journey.get("metadata") or {}),
        is_starter, updated_by,
    )
    return _row_to_journey(row)


async def set_journey_status(product_id: int, journey_key: str, status: str,
                             *, dry_run: Optional[bool] = None) -> bool:
    result = await _execute(
        "UPDATE retention_journeys SET status = $3, "
        " dry_run = COALESCE($4, dry_run), updated_at = now() "
        "WHERE product_id = $1 AND journey_key = $2",
        product_id, journey_key, status, dry_run)
    return not result.endswith("0")


async def delete_journey(product_id: int, journey_key: str) -> bool:
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM retention_journey_steps_log WHERE enrollment_id "
                "IN (SELECT id FROM retention_journey_enrollments "
                "    WHERE product_id = $1 AND journey_key = $2)",
                product_id, journey_key)
            await conn.execute(
                "DELETE FROM retention_journey_enrollments "
                "WHERE product_id = $1 AND journey_key = $2",
                product_id, journey_key)
            result = await conn.execute(
                "DELETE FROM retention_journeys "
                "WHERE product_id = $1 AND journey_key = $2",
                product_id, journey_key)
    return not result.endswith("0")


def _row_to_enrollment(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    if d.get("retention_user_id") is not None:
        d["retention_user_id"] = int(d["retention_user_id"])
    _iso_fields(d, "enrolled_at", "next_step_at", "updated_at")
    return d


async def create_enrollment(product_id: int, *, player_id: str,
                            retention_user_id: Optional[int],
                            journey_key: str,
                            journey_version: int) -> Optional[int]:
    """Idempotent: one ACTIVE pass per (player, journey); a duplicate returns
    None (the partial unique index enforces it)."""
    try:
        row = await _fetchrow(
            "INSERT INTO retention_journey_enrollments (product_id, "
            " player_id, retention_user_id, journey_key, journey_version) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            product_id, player_id, retention_user_id, journey_key,
            int(journey_version))
    except asyncpg.UniqueViolationError:
        return None
    return int(row["id"])


async def count_active_enrollments(product_id: int, player_id: str) -> int:
    val = await _fetchval(
        "SELECT COUNT(*) FROM retention_journey_enrollments "
        "WHERE product_id = $1 AND player_id = $2 AND status = 'active'",
        product_id, player_id)
    return int(val or 0)


async def last_enrollment_at(product_id: int, player_id: str,
                             journey_key: str) -> Optional[str]:
    """When this player was last enrolled in this journey, whatever came of it.

    The one-active-enrollment index is PARTIAL (`WHERE status = 'active'`), so
    it says nothing about a journey the player already finished — and the
    scheduled matcher re-derives its candidates from live state (a dormancy
    cohort, a weekday) that is still true the moment the enrollment completes.
    Without this the sweep re-enrolled the same player every tick.
    """
    val = await _fetchval(
        "SELECT MAX(enrolled_at) FROM retention_journey_enrollments "
        "WHERE product_id = $1 AND player_id = $2 AND journey_key = $3",
        product_id, player_id, journey_key)
    return _iso(val) if val else None


async def due_enrollments(product_id: int, limit: int = 50
                          ) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_journey_enrollments "
        "WHERE product_id = $1 AND status = 'active' "
        "  AND next_step_at IS NOT NULL AND next_step_at <= now() "
        "ORDER BY next_step_at LIMIT $2",
        product_id, int(limit))
    return [_row_to_enrollment(r) for r in rows]


async def set_enrollment_next_step(product_id: int, enrollment_id: int, *,
                                   next_step_at: Any) -> None:
    await _execute(
        "UPDATE retention_journey_enrollments SET next_step_at = $3, "
        "updated_at = now() WHERE product_id = $1 AND id = $2",
        product_id, int(enrollment_id), _as_ts(next_step_at))


async def advance_enrollment(product_id: int, enrollment_id: int, *,
                             current_step: int) -> None:
    await _execute(
        "UPDATE retention_journey_enrollments SET current_step = $3, "
        "next_step_at = NULL, updated_at = now() "
        "WHERE product_id = $1 AND id = $2",
        product_id, int(enrollment_id), int(current_step))


async def finish_enrollment(product_id: int, enrollment_id: int,
                            status: str, *, reason: Optional[str]) -> None:
    await _execute(
        "UPDATE retention_journey_enrollments SET status = $3, "
        "exit_reason = $4, next_step_at = NULL, updated_at = now() "
        "WHERE product_id = $1 AND id = $2",
        product_id, int(enrollment_id), status, reason)


async def log_journey_step(product_id: int, enrollment_id: int, step_id: int,
                           outcome: str, decision_id: Optional[int],
                           detail: Optional[str]) -> None:
    await _execute(
        "INSERT INTO retention_journey_steps_log (product_id, enrollment_id, "
        " step_id, outcome, decision_id, detail) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        product_id, int(enrollment_id), int(step_id), outcome, decision_id,
        detail)


async def journey_enrollment_stats(product_id: int, journey_key: str
                                   ) -> dict[str, int]:
    rows = await _fetch(
        "SELECT status, COUNT(*) AS n FROM retention_journey_enrollments "
        "WHERE product_id = $1 AND journey_key = $2 GROUP BY status",
        product_id, journey_key)
    return {str(r["status"]): int(r["n"]) for r in rows}


async def list_enrollments(product_id: int, journey_key: Optional[str] = None,
                           status: Optional[str] = None,
                           limit: int = 100) -> list[dict[str, Any]]:
    where = ["product_id = $1"]
    args: list[Any] = [product_id]
    if journey_key:
        args.append(journey_key)
        where.append(f"journey_key = ${len(args)}")
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    args.append(int(limit))
    rows = await _fetch(
        f"SELECT * FROM retention_journey_enrollments "
        f"WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ${len(args)}",
        *args)
    return [_row_to_enrollment(r) for r in rows]


async def get_retention_user_by_id(product_id: int, rid: int
                                   ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_RU_COLS} FROM retention_users "
        "WHERE product_id = $1 AND id = $2", product_id, int(rid))
    return _row_to_retention_user(row) if row else None


async def journey_scheduled_candidates(product_id: int,
                                       match: dict[str, Any],
                                       limit: int = 100
                                       ) -> list[dict[str, Any]]:
    """Players matching a scheduled journey trigger. Supported match keys:
    dormancy_cohort, deposit_initiated_older_than_h (+ implicit
    not-confirmed: the timer column is cleared on confirmation),
    day_of_week (evaluated by the caller — every linked player is a
    candidate)."""
    where = ["product_id = $1", "player_id IS NOT NULL", "player_id <> ''",
             "subscribed", "NOT pings_muted", "NOT unreachable"]
    args: list[Any] = [product_id]
    if match.get("dormancy_cohort") is not None:
        args.append(str(match["dormancy_cohort"]))
        where.append(f"dormancy_cohort = ${len(args)}")
    if match.get("deposit_initiated_older_than_h") is not None:
        args.append(int(match["deposit_initiated_older_than_h"]))
        where.append("deposit_initiated_at IS NOT NULL "
                     f"AND deposit_initiated_at < now() - "
                     f"make_interval(hours => ${len(args)})")
    args.append(int(limit))
    rows = await _fetch(
        f"SELECT {_RU_COLS} FROM retention_users "
        f"WHERE {' AND '.join(where)} ORDER BY id LIMIT ${len(args)}",
        *args)
    return [_row_to_retention_user(r) for r in rows]


async def arm_deposit_initiated(product_id: int, player_id: str,
                                ts: Any) -> None:
    await _execute(
        "UPDATE retention_users SET deposit_initiated_at = $3, "
        "updated_at = now() WHERE product_id = $1 AND player_id = $2",
        product_id, player_id, _as_ts(ts))


async def clear_deposit_initiated(product_id: int, rid: int) -> None:
    await _execute(
        "UPDATE retention_users SET deposit_initiated_at = NULL, "
        "updated_at = now() WHERE product_id = $1 AND id = $2",
        product_id, int(rid))


async def clear_deposit_initiated_by_player(product_id: int,
                                            player_id: str) -> None:
    await _execute(
        "UPDATE retention_users SET deposit_initiated_at = NULL, "
        "updated_at = now() WHERE product_id = $1 AND player_id = $2",
        product_id, player_id)


# ---------------------------------------------------------------------------
# Offer engine (DOC-2, bonus-CMS-ID model) + the VIP host queue
# ---------------------------------------------------------------------------
def _row_to_offer(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    d["params"] = _json_value(d.get("params")) or {}
    d["allowed_countries"] = _json_value(d.get("allowed_countries")) or []
    for f in ("cost_estimate_usd", "min_deposit_usd"):
        if d.get(f) is not None:
            d[f] = float(d[f])
    _iso_fields(d, "created_at", "updated_at")
    return d


async def list_offer_catalog(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_offer_catalog WHERE product_id = $1 "
        "ORDER BY offer_key", product_id)
    return [_row_to_offer(r) for r in rows]


async def get_offer_by_key(product_id: int, offer_key: str
                           ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT * FROM retention_offer_catalog "
        "WHERE product_id = $1 AND offer_key = $2",
        product_id, offer_key)
    return _row_to_offer(row) if row else None


async def upsert_offer_catalog(product_id: int, *, offer_key: str,
                               offer_type: str,
                               partner_bonus_id: Optional[str],
                               params: dict[str, Any],
                               cost_estimate_usd: float, enabled: bool,
                               min_deposit_usd: Optional[float],
                               allowed_countries: list[str],
                               description: str,
                               updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_offer_catalog (product_id, offer_key, "
        " offer_type, partner_bonus_id, params, cost_estimate_usd, enabled, "
        " min_deposit_usd, allowed_countries, description, updated_by) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9::jsonb, $10, $11) "
        "ON CONFLICT (product_id, offer_key) DO UPDATE SET "
        " offer_type = EXCLUDED.offer_type, "
        " partner_bonus_id = EXCLUDED.partner_bonus_id, "
        " params = EXCLUDED.params, "
        " cost_estimate_usd = EXCLUDED.cost_estimate_usd, "
        " enabled = EXCLUDED.enabled, "
        " min_deposit_usd = EXCLUDED.min_deposit_usd, "
        " allowed_countries = EXCLUDED.allowed_countries, "
        " description = EXCLUDED.description, "
        " updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
        product_id, offer_key, offer_type, partner_bonus_id,
        json.dumps(params or {}), cost_estimate_usd, enabled,
        min_deposit_usd, json.dumps(allowed_countries or []), description,
        updated_by,
    )
    return _row_to_offer(row)


async def delete_offer_catalog(product_id: int, offer_key: str) -> bool:
    result = await _execute(
        "DELETE FROM retention_offer_catalog "
        "WHERE product_id = $1 AND offer_key = $2", product_id, offer_key)
    return result.endswith("1")


async def list_offer_triggers(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_offer_triggers WHERE product_id = $1 "
        "ORDER BY trigger_key", product_id)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        _iso_fields(d, "updated_at")
        items.append(d)
    return items


async def get_offer_trigger(product_id: int, trigger_key: str
                            ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT * FROM retention_offer_triggers "
        "WHERE product_id = $1 AND trigger_key = $2",
        product_id, trigger_key)
    if row is None:
        return None
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "updated_at")
    return d


async def upsert_offer_trigger(product_id: int, *, trigger_key: str,
                               offer_key: str, vip_suppress: bool,
                               enabled: bool,
                               updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_offer_triggers (product_id, trigger_key, "
        " offer_key, vip_suppress, enabled, updated_by) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "ON CONFLICT (product_id, trigger_key) DO UPDATE SET "
        " offer_key = EXCLUDED.offer_key, "
        " vip_suppress = EXCLUDED.vip_suppress, "
        " enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, "
        " updated_at = now() RETURNING *",
        product_id, trigger_key, offer_key, vip_suppress, enabled, updated_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "updated_at")
    return d


def _row_to_grant(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    d["params_snapshot"] = _json_value(d.get("params_snapshot")) or {}
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    if d.get("decision_id") is not None:
        d["decision_id"] = int(d["decision_id"])
    _iso_fields(d, "created_at", "expires_at")
    return d


async def get_offer_grant(product_id: int, offer_grant_id: str
                          ) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        "SELECT * FROM retention_offer_grants "
        "WHERE product_id = $1 AND offer_grant_id = $2",
        product_id, offer_grant_id)
    return _row_to_grant(row) if row else None


async def upsert_offer_grant(product_id: int, offer_grant_id: str, *,
                             player_id: str, decision_id: Optional[int],
                             offer_key: str, offer_type: str,
                             partner_bonus_id: Optional[str],
                             params_snapshot: dict[str, Any],
                             cost_usd: float, status: str) -> None:
    await _execute(
        "INSERT INTO retention_offer_grants (product_id, player_id, "
        " offer_grant_id, decision_id, offer_key, offer_type, "
        " partner_bonus_id, params_snapshot, cost_usd, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10) "
        "ON CONFLICT (product_id, offer_grant_id) DO UPDATE SET "
        " status = EXCLUDED.status",
        product_id, player_id, offer_grant_id, decision_id, offer_key,
        offer_type, partner_bonus_id, json.dumps(params_snapshot or {}),
        cost_usd, status,
    )


async def update_offer_grant(product_id: int, offer_grant_id: str, *,
                             status: str, partner_ref: Optional[str] = None,
                             detail: Optional[str] = None,
                             cost_usd: Optional[float] = None) -> None:
    await _execute(
        "UPDATE retention_offer_grants SET status = $3, "
        " partner_ref = COALESCE($4, partner_ref), "
        " detail = COALESCE($5, detail), "
        " cost_usd = COALESCE($6, cost_usd) "
        "WHERE product_id = $1 AND offer_grant_id = $2",
        product_id, offer_grant_id, status, partner_ref, detail, cost_usd,
    )


async def link_offer_grant_decision(product_id: int, offer_grant_id: str,
                                    decision_id: int) -> None:
    await _execute(
        "UPDATE retention_offer_grants SET decision_id = $3 "
        "WHERE product_id = $1 AND offer_grant_id = $2",
        product_id, offer_grant_id, int(decision_id),
    )


async def list_offer_grants(product_id: int, page: int = 1,
                            page_size: int = 50) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_offer_grants WHERE product_id = $1",
        product_id)
    rows = await _fetch(
        "SELECT * FROM retention_offer_grants WHERE product_id = $1 "
        "ORDER BY id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset)
    return {"items": [_row_to_grant(r) for r in rows],
            "total": int(total or 0)}


async def offers_cost_today(product_id: int) -> float:
    """Stimulus spend today (UTC) — the offer budget's numerator. Counts
    granted + pending (a pending call may still land)."""
    val = await _fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM retention_offer_grants "
        "WHERE product_id = $1 AND status IN ('granted', 'pending') "
        "  AND created_at >= date_trunc('day', now() at time zone 'utc') "
        "      at time zone 'utc'",
        product_id)
    return float(val or 0)


async def get_offer_cooldown(product_id: int, player_id: str
                             ) -> Optional[dict[str, Any]]:
    if not player_id:
        return None
    row = await _fetchrow(
        "SELECT * FROM retention_offer_cooldowns "
        "WHERE product_id = $1 AND player_id = $2",
        product_id, player_id)
    if row is None:
        return None
    d = dict(row)
    _iso_fields(d, "last_offer_at")
    return d


async def touch_offer_cooldown(product_id: int, player_id: str) -> None:
    await _execute(
        "INSERT INTO retention_offer_cooldowns (product_id, player_id, "
        " last_offer_at, offers_granted_total) VALUES ($1, $2, now(), 1) "
        "ON CONFLICT (product_id, player_id) DO UPDATE SET "
        " last_offer_at = now(), "
        " offers_granted_total = retention_offer_cooldowns"
        ".offers_granted_total + 1",
        product_id, player_id,
    )


async def create_host_task(product_id: int, player_id: str, *, reason: str,
                           context: dict[str, Any]) -> int:
    row = await _fetchrow(
        "INSERT INTO retention_host_tasks (product_id, player_id, reason, "
        " context) VALUES ($1, $2, $3, $4::jsonb) RETURNING id",
        product_id, player_id, reason, json.dumps(context or {}),
    )
    return int(row["id"])


async def list_host_tasks(product_id: int, status: Optional[str] = None,
                          limit: int = 100) -> list[dict[str, Any]]:
    where = "product_id = $1" + (" AND status = $3" if status else "")
    args: list[Any] = [product_id, int(limit)]
    if status:
        args.append(status)
    rows = await _fetch(
        f"SELECT * FROM retention_host_tasks WHERE {where} "
        "ORDER BY id DESC LIMIT $2", *args)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["context"] = _json_value(d.get("context")) or {}
        _iso_fields(d, "created_at", "updated_at")
        items.append(d)
    return items


async def update_host_task(product_id: int, task_id: int,
                           status: str) -> bool:
    result = await _execute(
        "UPDATE retention_host_tasks SET status = $3, updated_at = now() "
        "WHERE product_id = $1 AND id = $2",
        product_id, task_id, status)
    return result.endswith("1")


async def set_product_partner_endpoints(product_id: int, *,
                                        offer_grant_url: Optional[str],
                                        delivery_endpoint_url: Optional[str]
                                        ) -> bool:
    """The outbound casino endpoints (plain config; empty string clears)."""
    def _norm(v: Optional[str]) -> Any:
        if v is None:
            return UNSET
        v = v.strip()
        return v or None

    sets = ["updated_at = now()"]
    args: list[Any] = []
    for col, val in (("offer_grant_url", _norm(offer_grant_url)),
                     ("delivery_endpoint_url", _norm(delivery_endpoint_url))):
        if val is UNSET:
            continue
        args.append(val)
        sets.append(f"{col} = ${len(args)}")
    args.append(product_id)
    row = await _fetchrow(
        f"UPDATE products SET {', '.join(sets)} "
        f"WHERE id = ${len(args)} RETURNING id", *args)
    return row is not None


async def get_product_partner_out_key(product_id: int) -> Optional[str]:
    """Decrypted outbound partner key (offer-grant / deliver calls)."""
    import logging
    from app.core import secretbox
    enc = await _fetchval(
        "SELECT partner_out_key_enc FROM products WHERE id = $1", product_id)
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=partner_out",
            product_id)
        return None


async def get_product_email_api_key(product_id: int) -> Optional[str]:
    """Decrypted Customer.io App API key for the email channel."""
    import logging
    from app.core import secretbox
    enc = await _fetchval(
        "SELECT email_api_key_enc FROM products WHERE id = $1", product_id)
    if not enc:
        return None
    try:
        return secretbox.decrypt(enc)
    except secretbox.SecretBoxError:
        logging.getLogger(__name__).warning(
            "product_secret_undecryptable product_id=%s kind=email_api",
            product_id)
        return None


# ---------------------------------------------------------------------------
# Adaptive frequency + Smart Send Time (DOC-4)
# ---------------------------------------------------------------------------
async def list_frequency_caps(product_id: int, only_enabled: bool = False
                              ) -> list[dict[str, Any]]:
    q = ("SELECT * FROM retention_frequency_caps WHERE product_id = $1 "
         + ("AND enabled " if only_enabled else "")
         + "ORDER BY channel, cohort")
    rows = await _fetch(q, product_id)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        _iso_fields(d, "updated_at")
        items.append(d)
    return items


async def upsert_frequency_cap(product_id: int, *, channel: str, cohort: str,
                               per_day: int, per_week: int,
                               burst_per_hour: int, enabled: bool,
                               updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_frequency_caps (product_id, channel, cohort, "
        " per_day, per_week, burst_per_hour, enabled, updated_by) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (product_id, channel, cohort) DO UPDATE SET "
        " per_day = EXCLUDED.per_day, per_week = EXCLUDED.per_week, "
        " burst_per_hour = EXCLUDED.burst_per_hour, "
        " enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, "
        " updated_at = now() RETURNING *",
        product_id, channel, cohort, int(per_day), int(per_week),
        int(burst_per_hour), enabled, updated_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "updated_at")
    return d


async def list_touch_priorities(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT * FROM retention_touch_priority WHERE product_id = $1 "
        "ORDER BY priority, touch_type", product_id)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        _iso_fields(d, "updated_at")
        items.append(d)
    return items


async def upsert_touch_priority(product_id: int, *, touch_type: str,
                                priority: int, channel_switch_on_cap: bool,
                                updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_touch_priority (product_id, touch_type, "
        " priority, channel_switch_on_cap, updated_by) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (product_id, touch_type) DO UPDATE SET "
        " priority = EXCLUDED.priority, "
        " channel_switch_on_cap = EXCLUDED.channel_switch_on_cap, "
        " updated_by = EXCLUDED.updated_by, updated_at = now() RETURNING *",
        product_id, touch_type, int(priority), channel_switch_on_cap,
        updated_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "updated_at")
    return d


async def touch_counts(product_id: int, rid: int, channel: str
                       ) -> dict[str, int]:
    """Sent-touch counts (hour / UTC day / 7d) for the frequency gate.
    Telegram counts ride the ping ledger; other channels count deliveries."""
    if channel == "telegram":
        row = await _fetchrow(
            "SELECT COUNT(*) FILTER (WHERE created_at > now() - interval "
            "  '1 hour') AS h, "
            " COUNT(*) FILTER (WHERE created_at >= date_trunc('day', "
            "  now() at time zone 'utc') at time zone 'utc') AS d, "
            " COUNT(*) FILTER (WHERE created_at > now() - interval '7 days') "
            "  AS w "
            "FROM retention_pings "
            "WHERE product_id = $1 AND retention_user_id = $2 "
            "  AND status IN ('sent', 'dry_run')",
            product_id, rid,
        )
    else:
        row = await _fetchrow(
            "SELECT COUNT(*) FILTER (WHERE created_at > now() - interval "
            "  '1 hour') AS h, "
            " COUNT(*) FILTER (WHERE created_at >= date_trunc('day', "
            "  now() at time zone 'utc') at time zone 'utc') AS d, "
            " COUNT(*) FILTER (WHERE created_at > now() - interval '7 days') "
            "  AS w "
            "FROM retention_deliveries "
            "WHERE product_id = $1 AND retention_user_id = $2 "
            "  AND channel = $3 AND status NOT IN "
            "  ('failed', 'undeliverable', 'suppressed')",
            product_id, rid, channel,
        )
    return {"hour": int(row["h"] or 0), "day": int(row["d"] or 0),
            "week": int(row["w"] or 0)}


async def get_activity_profile(product_id: int, player_id: str
                               ) -> Optional[dict[str, Any]]:
    if not player_id:
        return None
    row = await _fetchrow(
        "SELECT * FROM retention_activity_profile "
        "WHERE product_id = $1 AND player_id = $2",
        product_id, player_id,
    )
    if row is None:
        return None
    d = dict(row)
    d["peak_hours"] = _json_value(d.get("peak_hours")) or []
    d["dow_pattern"] = _json_value(d.get("dow_pattern")) or {}
    d["response_by_hour"] = _json_value(d.get("response_by_hour")) or {}
    _iso_fields(d, "computed_at")
    return d


async def activity_profile_candidates(product_id: int, limit: int = 200
                                      ) -> list[dict[str, Any]]:
    """Players due a profile recompute: linked + least-recently computed."""
    rows = await _fetch(
        "SELECT u.player_id, u.tz FROM retention_users u "
        "LEFT JOIN retention_activity_profile p "
        "  ON p.product_id = u.product_id AND p.player_id = u.player_id "
        "WHERE u.product_id = $1 AND u.player_id IS NOT NULL "
        "  AND u.player_id <> '' "
        "ORDER BY p.computed_at ASC NULLS FIRST LIMIT $2",
        product_id, int(limit),
    )
    return [{"player_id": str(r["player_id"]), "tz": r["tz"]} for r in rows]


async def activity_hour_histogram(product_id: int, player_id: str, *,
                                  tz_offset_hours: float = 0.0,
                                  window_days: int = 30) -> dict[str, Any]:
    """Deterministic activity profile inputs: local-hour histogram of the
    player's activity events over the window."""
    rows = await _fetch(
        "SELECT EXTRACT(hour FROM ts + make_interval(hours => $4))::int AS h, "
        "       COUNT(*) AS n "
        "FROM retention_events "
        "WHERE product_id = $1 AND player_id = $2 "
        "  AND event_name IN ('session_started', 'session_ended', "
        "                     'bet_settled', 'deposit_confirmed', "
        "                     'check_in_done') "
        "  AND ts > now() - make_interval(days => $3) "
        "GROUP BY 1 ORDER BY 1",
        product_id, player_id, int(window_days), float(tz_offset_hours),
    )
    hist = {int(r["h"]) % 24: int(r["n"]) for r in rows}
    sample = sum(hist.values())
    if not sample:
        return {"median_active_hour": None, "peak_hours": [],
                "sample_size": 0}
    # Median hour by cumulative count; top-3 peak hours by count.
    ordered: list[int] = []
    for h in sorted(hist):
        ordered.extend([h] * hist[h])
    median = ordered[len(ordered) // 2]
    peaks = [h for h, _n in sorted(hist.items(),
                                   key=lambda kv: (-kv[1], kv[0]))[:3]]
    return {"median_active_hour": int(median), "peak_hours": peaks,
            "sample_size": sample}


async def upsert_activity_profile(product_id: int, player_id: str,
                                  profile: dict[str, Any]) -> None:
    await _execute(
        "INSERT INTO retention_activity_profile (product_id, player_id, "
        " median_active_hour, peak_hours, sample_size, computed_at) "
        "VALUES ($1, $2, $3, $4::jsonb, $5, now()) "
        "ON CONFLICT (product_id, player_id) DO UPDATE SET "
        " median_active_hour = EXCLUDED.median_active_hour, "
        " peak_hours = EXCLUDED.peak_hours, "
        " sample_size = EXCLUDED.sample_size, computed_at = now()",
        product_id, player_id, profile.get("median_active_hour"),
        json.dumps(profile.get("peak_hours") or []),
        int(profile.get("sample_size") or 0),
    )


# ---------------------------------------------------------------------------
# Player scoring: cohorts / RFM / value tiers (DOC-5)
# ---------------------------------------------------------------------------
async def get_scoring_config(product_id: int) -> dict[str, Any]:
    """All scoring config rows for a product as {config_key: params}."""
    rows = await _fetch(
        "SELECT config_key, params FROM retention_scoring_config "
        "WHERE product_id = $1", product_id)
    return {str(r["config_key"]): (_json_value(r["params"]) or {})
            for r in rows}


async def upsert_scoring_config(product_id: int, config_key: str,
                                params: dict[str, Any],
                                updated_by: Optional[str]) -> None:
    await _execute(
        "INSERT INTO retention_scoring_config (product_id, config_key, "
        " params, updated_by) VALUES ($1, $2, $3::jsonb, $4) "
        "ON CONFLICT (product_id, config_key) DO UPDATE SET "
        " params = EXCLUDED.params, updated_by = EXCLUDED.updated_by, "
        " updated_at = now()",
        product_id, config_key, json.dumps(params or {}), updated_by,
    )


async def rfm_inputs(product_id: int, player_id: str, *,
                     window_days: int) -> dict[str, Any]:
    """Deposit counts/sums for RFM (window) + lifetime (value tier)."""
    if not player_id:
        return {"deposits_window": 0, "sum_window": 0.0, "sum_lifetime": 0.0}
    row = await _fetchrow(
        "SELECT COUNT(*) FILTER (WHERE ts > now() - make_interval(days => $3))"
        "         AS dep_window, "
        f"       COALESCE(SUM({_payload_num('amount')}) FILTER "
        "         (WHERE ts > now() - make_interval(days => $3)), 0) "
        "         AS sum_window, "
        f"       COALESCE(SUM({_payload_num('amount')}), 0) AS sum_lifetime "
        "FROM retention_events "
        "WHERE product_id = $1 AND player_id = $2 "
        "  AND event_name = 'deposit_confirmed'",
        product_id, player_id, int(window_days),
    )
    return {"deposits_window": int(row["dep_window"] or 0),
            "sum_window": float(row["sum_window"] or 0),
            "sum_lifetime": float(row["sum_lifetime"] or 0)}


async def cache_player_scoring(product_id: int, rid: int,
                               fields: dict[str, Any], *,
                               cohort_changed: bool = False) -> None:
    """Write the scoring cache on retention_users (sweep output)."""
    sets = ["score_computed_at = now()", "updated_at = now()"]
    args: list[Any] = [product_id, rid]
    for f in ("dormancy_cohort", "value_tier", "vip_segment"):
        if f in fields:
            args.append(fields[f])
            sets.append(f"{f} = ${len(args)}")
    for f in ("rfm_recency", "rfm_frequency", "rfm_score"):
        if f in fields:
            args.append(int(fields[f]) if fields[f] is not None else None)
            sets.append(f"{f} = ${len(args)}")
    for f in ("rfm_monetary", "total_deposit_lifetime"):
        if f in fields:
            args.append(fields[f])
            sets.append(f"{f} = ${len(args)}")
    if cohort_changed:
        sets.append("dormancy_since = now()")
    await _execute(
        f"UPDATE retention_users SET {', '.join(sets)} "
        "WHERE product_id = $1 AND id = $2",
        *args,
    )


async def log_cohort_transition(product_id: int, player_id: str, *,
                                from_cohort: str, to_cohort: str,
                                days_inactive: int) -> bool:
    """One transition row; deduped to one per (player, cohort, day)."""
    row = await _fetchrow(
        "INSERT INTO retention_cohort_transitions (product_id, player_id, "
        " from_cohort, to_cohort, days_inactive) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (product_id, player_id, to_cohort, "
        " ((transitioned_at at time zone 'utc')::date)) "
        "DO NOTHING RETURNING id",
        product_id, player_id, from_cohort, to_cohort, int(days_inactive),
    )
    return row is not None


async def scoring_candidates(product_id: int, limit: int = 200
                             ) -> list[dict[str, Any]]:
    """Least-recently-scored players first (never-scored before all)."""
    rows = await _fetch(
        f"SELECT {_RU_COLS} FROM retention_users "
        "WHERE product_id = $1 AND player_id IS NOT NULL AND player_id <> '' "
        "ORDER BY score_computed_at ASC NULLS FIRST LIMIT $2",
        product_id, int(limit),
    )
    return [_row_to_retention_user(r) for r in rows]


async def scoring_distribution(product_id: int) -> dict[str, Any]:
    cohorts = await _fetch(
        "SELECT COALESCE(dormancy_cohort, 'unscored') AS k, COUNT(*) AS n "
        "FROM retention_users WHERE product_id = $1 GROUP BY 1", product_id)
    tiers = await _fetch(
        "SELECT COALESCE(value_tier, 'unscored') AS k, COUNT(*) AS n "
        "FROM retention_users WHERE product_id = $1 GROUP BY 1", product_id)
    segments = await _fetch(
        "SELECT COALESCE(vip_segment, 'unscored') AS k, COUNT(*) AS n "
        "FROM retention_users WHERE product_id = $1 GROUP BY 1", product_id)
    return {
        "by_cohort": {str(r["k"]): int(r["n"]) for r in cohorts},
        "by_value_tier": {str(r["k"]): int(r["n"]) for r in tiers},
        "by_vip_segment": {str(r["k"]): int(r["n"]) for r in segments},
    }


async def list_cohort_transitions(product_id: int, page: int = 1,
                                  page_size: int = 50) -> dict[str, Any]:
    offset = max(page - 1, 0) * page_size
    total = await _fetchval(
        "SELECT COUNT(*) FROM retention_cohort_transitions "
        "WHERE product_id = $1", product_id)
    rows = await _fetch(
        "SELECT * FROM retention_cohort_transitions WHERE product_id = $1 "
        "ORDER BY id DESC LIMIT $2 OFFSET $3",
        product_id, page_size, offset)
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        _iso_fields(d, "transitioned_at")
        items.append(d)
    return {"items": items, "total": int(total or 0)}


# ---------------------------------------------------------------------------
# Measurement: holdout config + the uplift cuts (DOC-1)
# ---------------------------------------------------------------------------
async def set_holdout_group(product_id: int, rid: int, group: str,
                            salt: str) -> None:
    """Cache a player's deterministic holdout group (+ the salt it was computed
    under, so a salt rotation lazily invalidates the cache)."""
    await _execute(
        "UPDATE retention_users SET holdout_group = $3, holdout_salt = $4, "
        "updated_at = now() WHERE product_id = $1 AND id = $2",
        product_id, rid, group, salt,
    )


async def get_holdout_config(product_id: int) -> Optional[dict[str, Any]]:
    """The newest active holdout experiment row for a product (audit trail;
    the hot path reads the mirrored settings keys instead)."""
    row = await _fetchrow(
        "SELECT * FROM retention_holdout_config "
        "WHERE product_id = $1 AND active ORDER BY id DESC LIMIT 1",
        product_id,
    )
    if row is None:
        return None
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "created_at")
    return d


async def insert_holdout_config(product_id: int, *, holdout_pct: int,
                                holdout_salt: str, note: Optional[str],
                                created_by: Optional[str]) -> dict[str, Any]:
    """Append a new experiment config row (history is kept; the newest active
    row is the current experiment)."""
    row = await _fetchrow(
        "INSERT INTO retention_holdout_config (product_id, holdout_pct, "
        " holdout_salt, note, created_by) VALUES ($1, $2, $3, $4, $5) "
        "RETURNING *",
        product_id, int(holdout_pct), holdout_salt, note, created_by,
    )
    d = dict(row)
    d["id"] = int(d["id"])
    _iso_fields(d, "created_at")
    return d


async def holdout_status(product_id: int) -> dict[str, int]:
    """Live population split by the cached holdout group."""
    rows = await _fetch(
        "SELECT COALESCE(holdout_group, 'unassigned') AS grp, COUNT(*) AS n "
        "FROM retention_users WHERE product_id = $1 GROUP BY 1",
        product_id,
    )
    out = {"treatment": 0, "holdout": 0, "unassigned": 0}
    for r in rows:
        out[str(r["grp"])] = int(r["n"])
    return out


async def retention_uplift(product_id: int, dt_from: Any, dt_to: Any
                           ) -> list[dict[str, Any]]:
    """Uplift per conversion type over the window: distinct players per group
    (by the group AT touch time), conversion = the player had at least one
    row with the outcome set. Treatment rows are real touches; holdout rows
    are the virtual would-have-touched markers (kind='holdout')."""
    rows = await _fetch(
        "SELECT holdout_group_at_time AS grp, "
        "       COUNT(DISTINCT player_id) AS players, "
        "       COUNT(DISTINCT player_id) FILTER "
        "         (WHERE returned_at IS NOT NULL) AS returned, "
        "       COUNT(DISTINCT player_id) FILTER "
        "         (WHERE deposit_at IS NOT NULL) AS deposited "
        "FROM retention_outcomes "
        "WHERE product_id = $1 AND holdout_group_at_time IS NOT NULL "
        "  AND player_id IS NOT NULL AND player_id <> '' "
        "  AND sent_at >= $2 AND sent_at < $3 "
        "GROUP BY 1",
        product_id, _as_ts(dt_from), _as_ts(dt_to),
    )
    by_grp = {str(r["grp"]): r for r in rows}
    out: list[dict[str, Any]] = []
    for conv, field in (("reactivation", "returned"), ("deposit", "deposited")):
        t = by_grp.get("treatment")
        h = by_grp.get("holdout")
        tp = int(t["players"]) if t else 0
        hp = int(h["players"]) if h else 0
        tc = int(t[field]) if t else 0
        hc = int(h[field]) if h else 0
        t_rate = round(tc / tp, 4) if tp else None
        h_rate = round(hc / hp, 4) if hp else None
        uplift = (round((t_rate - h_rate) * 100, 1)
                  if t_rate is not None and h_rate is not None else None)
        out.append({
            "conversion_type": conv,
            "treatment_players": tp, "holdout_players": hp,
            "treatment_conversions": tc, "holdout_conversions": hc,
            "treatment_rate": t_rate, "holdout_rate": h_rate,
            "uplift_pp": uplift,
            "low_confidence": hp < 30,
            **({"reason": "holdout_disabled"} if hp == 0 else {}),
        })
    return out


async def write_uplift_snapshot(product_id: int, window_from: Any,
                                window_to: Any, row: dict[str, Any]) -> None:
    await _execute(
        "INSERT INTO retention_uplift_snapshots (product_id, window_from, "
        " window_to, conversion_type, treatment_players, holdout_players, "
        " treatment_conversions, holdout_conversions, treatment_rate, "
        " holdout_rate, uplift_pp) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        product_id, _as_ts(window_from), _as_ts(window_to),
        row["conversion_type"], row["treatment_players"],
        row["holdout_players"], row["treatment_conversions"],
        row["holdout_conversions"], row["treatment_rate"], row["holdout_rate"],
        row["uplift_pp"],
    )


# The aggregate every effectiveness cut shares: sends, replies, conversions and
# what they cost. `closed` rows only would hide today's activity entirely, so
# the counts cover everything and `settled` says how much of it is final.
_OUTCOME_AGG = (
    "COUNT(*)::int AS sends, "
    "COUNT(*) FILTER (WHERE o.closed)::int AS settled, "
    "COUNT(*) FILTER (WHERE o.replied_at IS NOT NULL)::int AS replies, "
    "COUNT(*) FILTER (WHERE o.returned_at IS NOT NULL)::int AS returns, "
    "COUNT(*) FILTER (WHERE o.deposit_at IS NOT NULL)::int AS deposits, "
    "AVG(o.reply_latency_sec) FILTER (WHERE o.replied_at IS NOT NULL) "
    "  AS avg_reply_latency_sec, "
    "COALESCE(SUM(o.cost_usd), 0) AS cost_usd"
)


def _outcome_stats(row: asyncpg.Record) -> dict[str, Any]:
    sends = int(row["sends"] or 0)
    replies = int(row["replies"] or 0)
    returns = int(row["returns"] or 0)
    deposits = int(row["deposits"] or 0)
    latency = row["avg_reply_latency_sec"]
    return {
        "sends": sends,
        "settled": int(row["settled"] or 0),
        "replies": replies,
        "returns": returns,
        "deposits": deposits,
        "reply_rate": round(replies / sends, 3) if sends else None,
        "return_rate": round(returns / sends, 3) if sends else None,
        "deposit_rate": round(deposits / sends, 3) if sends else None,
        "avg_reply_latency_sec": (round(float(latency)) if latency is not None
                                  else None),
        "cost_usd": round(float(row["cost_usd"] or 0), 6),
    }


async def outcome_summary(product_id: int, dt_from: Any, dt_to: Any
                          ) -> dict[str, Any]:
    """Product-level totals + cost per outcome (the 'what did it buy us' tile).

    Cost rides on proactive rows only (a dialogue reply's cost belongs to the
    conversation, not to the CTA it happened to carry), so the per-outcome
    figures are computed over proactive touches alone.
    """
    row = await _fetchrow(
        f"SELECT {_OUTCOME_AGG} FROM retention_outcomes o "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.kind = 'proactive'",
        product_id, dt_from, dt_to,
    )
    stats = _outcome_stats(row)
    cost = stats["cost_usd"]
    stats["cost_per_reply_usd"] = (round(cost / stats["replies"], 4)
                                   if stats["replies"] else None)
    stats["cost_per_return_usd"] = (round(cost / stats["returns"], 4)
                                    if stats["returns"] else None)
    stats["cost_per_deposit_usd"] = (round(cost / stats["deposits"], 4)
                                     if stats["deposits"] else None)
    dialog = await _fetchrow(
        f"SELECT {_OUTCOME_AGG} FROM retention_outcomes o "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.kind = 'dialogue'",
        product_id, dt_from, dt_to,
    )
    return {"proactive": stats, "dialogue": _outcome_stats(dialog)}


async def outcome_by_media(product_id: int, dt_from: Any, dt_to: Any,
                           limit: int = 100) -> list[dict[str, Any]]:
    """Effectiveness per delivered photo/video (both facades: a photo sent in
    dialogue counts exactly like one sent proactively)."""
    rows = await _fetch(
        f"SELECT o.photo_id, {_OUTCOME_AGG}, "
        "  MAX(p.media_type) AS media_type, MAX(p.description) AS description, "
        "  MAX(p.storage_ref) AS storage_ref, MAX(p.stage) AS stage, "
        "  BOOL_OR(p.active) AS active "
        "FROM retention_outcomes o "
        "LEFT JOIN retention_photos p ON p.id = o.photo_id "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.photo_id IS NOT NULL "
        "GROUP BY o.photo_id ORDER BY COUNT(*) DESC, o.photo_id LIMIT $4",
        product_id, dt_from, dt_to, int(limit),
    )
    return [{"photo_id": int(r["photo_id"]),
             "media_type": r["media_type"] or "photo",
             "description": r["description"] or "",
             "storage_ref": r["storage_ref"],
             "stage": int(r["stage"]) if r["stage"] is not None else None,
             "active": bool(r["active"]) if r["active"] is not None else None,
             **_outcome_stats(r)} for r in rows]


async def outcome_by_link(product_id: int, dt_from: Any, dt_to: Any,
                          limit: int = 50) -> list[dict[str, Any]]:
    """Effectiveness per site-map CTA page the bot buttoned up."""
    rows = await _fetch(
        f"SELECT o.link_url, {_OUTCOME_AGG} FROM retention_outcomes o "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.link_url IS NOT NULL AND o.link_url <> '' "
        "GROUP BY o.link_url ORDER BY COUNT(*) DESC, o.link_url LIMIT $4",
        product_id, dt_from, dt_to, int(limit),
    )
    return [{"link_url": r["link_url"], **_outcome_stats(r)} for r in rows]


async def outcome_by_idle_rule(product_id: int, dt_from: Any, dt_to: Any
                               ) -> list[dict[str, Any]]:
    """Effectiveness per idle-ladder rung (which day thresholds bring players
    back and which are dead weight)."""
    rows = await _fetch(
        f"SELECT o.rule_id, {_OUTCOME_AGG}, "
        "  MAX(r.name) AS name, MAX(r.inactivity_days) AS inactivity_days, "
        "  MAX(r.trigger_kind) AS trigger_kind, MAX(r.action) AS action, "
        "  BOOL_OR(r.enabled) AS enabled "
        "FROM retention_outcomes o "
        "LEFT JOIN retention_rules r ON r.id = o.rule_id "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.rule_id IS NOT NULL "
        "GROUP BY o.rule_id ORDER BY MAX(r.inactivity_days) NULLS LAST, o.rule_id",
        product_id, dt_from, dt_to,
    )
    return [{"rule_id": int(r["rule_id"]), "name": r["name"] or "(deleted rule)",
             "inactivity_days": (int(r["inactivity_days"])
                                 if r["inactivity_days"] is not None else None),
             "trigger_kind": r["trigger_kind"], "action": r["action"],
             "enabled": bool(r["enabled"]) if r["enabled"] is not None else None,
             **_outcome_stats(r)} for r in rows]


async def outcome_by_event(product_id: int, dt_from: Any, dt_to: Any
                           ) -> list[dict[str, Any]]:
    """Effectiveness per proactive trigger (casino event / idle kind)."""
    rows = await _fetch(
        f"SELECT COALESCE(o.event_name, '(none)') AS event_name, {_OUTCOME_AGG} "
        "FROM retention_outcomes o "
        "WHERE o.product_id = $1 AND o.sent_at >= $2 AND o.sent_at < $3 "
        "  AND o.kind = 'proactive' "
        "GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 40",
        product_id, dt_from, dt_to,
    )
    return [{"event_name": r["event_name"], **_outcome_stats(r)} for r in rows]


async def top_links_by_outcome(product_id: int, limit: int = 3,
                               min_sends: int = 3) -> list[str]:
    """The CTA pages that actually pulled players back — fed to the play-nudge
    directive so link rotation prefers what works. Needs a few settled sends
    before a page counts (one lucky click is not evidence)."""
    rows = await _fetch(
        "SELECT link_url, COUNT(*) AS sends, "
        "  COUNT(*) FILTER (WHERE replied_at IS NOT NULL "
        "                     OR returned_at IS NOT NULL) AS hits "
        "FROM retention_outcomes "
        "WHERE product_id = $1 AND link_url IS NOT NULL AND link_url <> '' "
        "  AND closed "
        "GROUP BY link_url HAVING COUNT(*) >= $2 "
        "ORDER BY (COUNT(*) FILTER (WHERE replied_at IS NOT NULL "
        "                             OR returned_at IS NOT NULL) + 1.0) "
        "         / (COUNT(*) + 2.0) DESC, COUNT(*) DESC LIMIT $3",
        product_id, int(min_sends), int(limit),
    )
    return [r["link_url"] for r in rows]


# ---------------------------------------------------------------------------
# Quality review — the LLM-as-judge verdict on a finished conversation
# ---------------------------------------------------------------------------
async def sessions_for_review(product_id: int, *, min_messages: int,
                              idle_minutes: int, limit: int = 20
                              ) -> list[dict[str, Any]]:
    """Finished conversations of this product that still need a verdict.

    "Finished" = closed by the player/backend (resolved or escalated) OR simply
    dormant (`idle_minutes` since the last turn) — a widget chat is usually
    abandoned rather than closed, and a Telegram chat has no close button at
    all. A conversation is re-reviewed only after it has GROWN since its last
    verdict, so a stable transcript is never paid for twice. Oldest first, so
    a backlog drains in order instead of starving on the newest chats.

    There must also be something the PLAYER said: `persist_ping_turn` bumps
    `message_count` for a proactive ping, so a Telegram chat of nothing but
    unanswered pings clears the length bar with no handling to judge. Without
    the EXISTS check such a chat is picked on every pass forever, permanently
    occupying a slot in the batch.
    """
    rows = await _fetch(
        "SELECT s.id, s.consumer, s.lang, s.status, s.escalated, "
        "       s.message_count, s.topic_id "
        "FROM chat_sessions s "
        "LEFT JOIN conversation_reviews r ON r.session_id = s.id "
        "WHERE s.product_id = $1 AND s.message_count >= $2 "
        "  AND (s.status IN ('resolved', 'escalated') "
        "       OR s.updated_at < now() - make_interval(mins => $3)) "
        "  AND (r.id IS NULL OR r.reviewed_msg_count < s.message_count) "
        "  AND EXISTS (SELECT 1 FROM chat_messages m "
        "              WHERE m.session_id = s.id AND m.role = 'user') "
        "ORDER BY s.updated_at LIMIT $4",
        product_id, int(min_messages), int(idle_minutes), int(limit),
    )
    return [{"id": str(r["id"]), "consumer": r["consumer"], "lang": r["lang"],
             "status": r["status"], "escalated": bool(r["escalated"]),
             "message_count": int(r["message_count"] or 0),
             "topic_id": r["topic_id"]} for r in rows]


async def upsert_conversation_review(
        product_id: int, session_id: str, *, consumer: str,
        score: Optional[int], tags: list[str], summary: str,
        issues: list[dict[str, Any]], kb_gaps: list[str],
        reviewed_msg_count: int, model: Optional[str] = None,
        cost_usd: Optional[float] = None) -> int:
    """Store (or refresh) the verdict for one conversation.

    Replaces the row whole — call it only with a PARSED verdict. A failed
    attempt goes through `record_review_attempt` instead, which advances the
    bookkeeping without destroying a previously stored verdict. Score-NULL
    rows (failed attempts) are excluded from the Quality reads below — they
    are bookkeeping, not verdicts — but they DO count toward the daily cap
    (`reviews_today`), because the OpenAI call was billed.
    """
    row = await _fetchrow(
        "INSERT INTO conversation_reviews (product_id, session_id, consumer, "
        " score, tags, summary, issues, kb_gaps, reviewed_msg_count, model, "
        " cost_usd) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8::jsonb, $9, $10, "
        "        $11) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "  consumer = EXCLUDED.consumer, score = EXCLUDED.score, "
        "  tags = EXCLUDED.tags, summary = EXCLUDED.summary, "
        "  issues = EXCLUDED.issues, kb_gaps = EXCLUDED.kb_gaps, "
        "  reviewed_msg_count = EXCLUDED.reviewed_msg_count, "
        "  model = EXCLUDED.model, cost_usd = EXCLUDED.cost_usd, "
        "  created_at = now() "
        "RETURNING id",
        product_id, session_id, consumer, score, json.dumps(tags or []),
        summary, json.dumps(issues or []), json.dumps(kb_gaps or []),
        int(reviewed_msg_count), model, cost_usd,
    )
    return int(row["id"])


async def record_review_attempt(product_id: int, session_id: str, *,
                                consumer: str, reviewed_msg_count: int,
                                model: Optional[str] = None,
                                cost_usd: Optional[float] = None) -> int:
    """Book a FAILED review attempt without touching a stored verdict.

    `upsert_conversation_review` replaces the whole row, so writing the failed
    attempt through it destroyed a previously stored good verdict (session
    reviewed → grew → re-review unparseable → score=NULL overwrote score=4 and
    the verdict vanished from the Quality page). This helper only advances the
    bookkeeping: `reviewed_msg_count` (stops `sessions_for_review` from
    re-selecting the chat until it grows again), `created_at` (the attempt was
    a billed call, so it must count toward the `reviews_today` daily cap) and
    the accumulated `cost_usd`. Verdict fields (score/tags/summary/issues/
    kb_gaps) are written only when the row is NEW — an existing verdict, good
    or previously-failed, keeps its content.
    """
    row = await _fetchrow(
        "INSERT INTO conversation_reviews (product_id, session_id, consumer, "
        " score, tags, summary, issues, kb_gaps, reviewed_msg_count, model, "
        " cost_usd) "
        "VALUES ($1, $2, $3, NULL, '[]'::jsonb, '', '[]'::jsonb, '[]'::jsonb, "
        "        $4, $5, $6) "
        "ON CONFLICT (session_id) DO UPDATE SET "
        "  reviewed_msg_count = EXCLUDED.reviewed_msg_count, "
        "  cost_usd = COALESCE(conversation_reviews.cost_usd, 0) "
        "             + COALESCE(EXCLUDED.cost_usd, 0), "
        "  created_at = now() "
        "RETURNING id",
        product_id, session_id, consumer, int(reviewed_msg_count), model,
        cost_usd,
    )
    return int(row["id"])


async def reviews_today(product_id: int) -> int:
    """Reviews written since midnight UTC — the judge's daily cap reads this
    (same UTC day boundary as the retention budget)."""
    val = await _fetchval(
        "SELECT COUNT(*) FROM conversation_reviews WHERE product_id = $1 "
        "AND created_at >= date_trunc('day', now() at time zone 'utc') "
        "                  at time zone 'utc'",
        product_id,
    )
    return int(val or 0)


def _row_to_review(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    d["id"] = int(d["id"])
    d["session_id"] = str(d["session_id"])
    for k in ("tags", "issues", "kb_gaps"):
        d[k] = _json_value(d.get(k)) or []
    if d.get("score") is not None:
        d["score"] = int(d["score"])
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    d["created_at"] = _iso(d["created_at"])
    return d


async def list_conversation_reviews(dt_from: Any, dt_to: Any, *,
                                    product_ids: Optional[list[int]] = None,
                                    consumer: Optional[str] = None,
                                    tag: Optional[str] = None,
                                    max_score: Optional[int] = None,
                                    page: int = 1, page_size: int = 50
                                    ) -> dict[str, Any]:
    """The reviews list for the admin Quality page (worst first, then newest).

    `product_ids` follows the dashboard convention: None = the caller's whole
    scope, an empty list = match nothing. Failed-attempt rows (`score IS NULL`,
    see `upsert_conversation_review`) are bookkeeping, not verdicts, and never
    appear here.
    """
    where = ["r.created_at >= $1", "r.created_at < $2", "r.score IS NOT NULL"]
    args: list[Any] = [dt_from, dt_to]
    if product_ids is not None:
        args.append(product_ids)
        where.append(f"r.product_id = ANY(${len(args)}::int[])")
    if consumer:
        args.append(consumer)
        where.append(f"r.consumer = ${len(args)}")
    if tag:
        args.append(tag)
        # Containment rather than the `?` key operator: the parameter's type is
        # unambiguous here (and it is the GIN-indexable form).
        where.append(f"r.tags @> jsonb_build_array(${len(args)}::text)")
    if max_score is not None:
        args.append(int(max_score))
        where.append(f"r.score <= ${len(args)}")
    clause = " AND ".join(where)
    total = await _fetchval(
        f"SELECT COUNT(*) FROM conversation_reviews r WHERE {clause}", *args)
    args.extend([int(page_size), max(int(page) - 1, 0) * int(page_size)])
    rows = await _fetch(
        f"SELECT r.*, p.name AS product_name FROM conversation_reviews r "
        "LEFT JOIN products p ON p.id = r.product_id "
        f"WHERE {clause} ORDER BY r.score NULLS LAST, r.created_at DESC "
        f"LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return {"items": [_row_to_review(r) for r in rows], "total": int(total or 0)}


async def quality_overview(dt_from: Any, dt_to: Any, *,
                           product_ids: Optional[list[int]] = None
                           ) -> dict[str, Any]:
    """Score/tag/KB-gap aggregates behind the Quality page header."""
    where = ["created_at >= $1", "created_at < $2"]
    args: list[Any] = [dt_from, dt_to]
    if product_ids is not None:
        args.append(product_ids)
        where.append(f"product_id = ANY(${len(args)}::int[])")
    # Failed-attempt rows carry no verdict — they must not inflate `reviews`.
    where.append("score IS NOT NULL")
    clause = " AND ".join(where)
    head = await _fetchrow(
        "SELECT COUNT(*)::int AS reviews, AVG(score) AS avg_score, "
        "  COUNT(*) FILTER (WHERE score <= 2)::int AS poor, "
        "  COUNT(*) FILTER (WHERE score >= 4)::int AS good, "
        "  COUNT(*) FILTER (WHERE consumer = 'telegram')::int AS telegram, "
        "  COALESCE(SUM(cost_usd), 0) AS cost_usd "
        f"FROM conversation_reviews WHERE {clause}", *args)
    tags = await _fetch(
        "SELECT tag, COUNT(*)::int AS count FROM conversation_reviews, "
        "  LATERAL jsonb_array_elements_text(tags) AS tag "
        f"WHERE {clause} GROUP BY tag ORDER BY count DESC LIMIT 25", *args)
    gaps = await _fetch(
        "SELECT gap, COUNT(*)::int AS count, MAX(session_id::text) AS example "
        "FROM conversation_reviews, "
        "  LATERAL jsonb_array_elements_text(kb_gaps) AS gap "
        f"WHERE {clause} GROUP BY gap ORDER BY count DESC LIMIT 25", *args)
    avg = head["avg_score"]
    return {
        "reviews": int(head["reviews"] or 0),
        "avg_score": round(float(avg), 2) if avg is not None else None,
        "poor": int(head["poor"] or 0),
        "good": int(head["good"] or 0),
        "telegram": int(head["telegram"] or 0),
        "cost_usd": round(float(head["cost_usd"] or 0), 6),
        "tags": [{"tag": r["tag"], "count": r["count"]} for r in tags],
        "kb_gaps": [{"question": r["gap"], "count": r["count"],
                     "session_id": r["example"]} for r in gaps],
    }


async def persist_ping_turn(session_id: str, assistant_text: str,
                            ai_meta: Optional[dict[str, Any]] = None,
                            product_id: Optional[int] = None,
                            ping_context: Optional[str] = None,
                            link_url: Optional[str] = None) -> int:
    """Persist a PROACTIVE assistant message (a ping has no user turn).

    Same atomic contract as persist_turn — assistant message + AI log +
    message_count bump in one transaction — minus the user row.
    `ping_context` records the trigger/occasion that made the agent write
    ("deposit_confirmed: the player just made a deposit") so the prompt
    history and the admin transcript can explain the message later.
    """
    ai_meta = ai_meta or {}
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO chat_messages "
                "(session_id, role, content, lang, model, key_used, tokens_in, "
                " tokens_out, cached_in, cost_usd, ping_context, link_url) "
                "VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7, $8, $9, $10, "
                " $11)",
                session_id, assistant_text, ai_meta.get("lang"),
                ai_meta.get("model"), ai_meta.get("key_used"),
                ai_meta.get("tokens_in"), ai_meta.get("tokens_out"),
                ai_meta.get("cached_in"), ai_meta.get("cost_usd"),
                ping_context, link_url,
            )
            if ai_meta.get("model"):
                await conn.execute(
                    # A ping is written by the proactive AGENT, never by a
                    # player's turn — its spend belongs to the agent bucket.
                    "INSERT INTO ai_interaction_logs "
                    "(session_id, product_id, consumer, source, model, key_used, "
                    " tokens_in, tokens_out, cached_in, cost_usd, latency_ms, ok, "
                    " error) "
                    "VALUES ($1, $2, 'telegram', 'agent', $3, $4, $5, $6, $7, $8, "
                    " $9, $10, $11)",
                    session_id, product_id,
                    ai_meta.get("model"), ai_meta.get("key_used"),
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


async def bump_retention_activity(rid: int, *, meaningful: bool) -> dict[str, Any]:
    """Touch last_active + optionally increment the meaningful-message counters.

    Returns the refreshed row so the caller can evaluate stage-advance thresholds.
    """
    if meaningful:
        row = await _fetchrow(
            "UPDATE retention_users SET meaningful_msgs = meaningful_msgs + 1, "
            "msgs_since_photo = msgs_since_photo + 1, last_active_at = now(), "
            f"updated_at = now() WHERE id = $1 RETURNING {_RU_COLS}", rid,
        )
    else:
        row = await _fetchrow(
            "UPDATE retention_users SET last_active_at = now(), "
            f"updated_at = now() WHERE id = $1 RETURNING {_RU_COLS}", rid,
        )
    return _row_to_retention_user(row) if row else {}


async def advance_retention_stage(rid: int, new_stage: int) -> None:
    await _execute(
        "UPDATE retention_users SET unlocked_stage = $2, "
        "last_stage_advance_at = now(), updated_at = now() WHERE id = $1",
        rid, new_stage,
    )


async def record_retention_photo_view(rid: int, photo_id: int,
                                      product_id: int,
                                      session_id: Optional[str] = None) -> None:
    """Record a photo delivery: view row + per-photo counter + daily counter.

    Resets the daily counter when the stored day is not today, resets the
    proactive-cooldown counter, all in one transaction. `session_id` links the
    view to the chat session it was sent in, so the admin transcript can render
    the photo inline.
    """
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO retention_photo_views "
                "(photo_id, retention_user_id, product_id, session_id) "
                "VALUES ($1, $2, $3, $4)",
                photo_id, rid, product_id, session_id,
            )
            await conn.execute(
                "UPDATE retention_photos SET views_count = views_count + 1, "
                "updated_at = now() WHERE id = $1", photo_id,
            )
            await conn.execute(
                # UTC day — must match the Python-side cap check's clock.
                "UPDATE retention_users SET "
                "  photos_sent_today = CASE WHEN photos_day = "
                "    (now() at time zone 'utc')::date "
                "    THEN photos_sent_today + 1 ELSE 1 END, "
                "  photos_day = (now() at time zone 'utc')::date, "
                "  msgs_since_photo = 0, updated_at = now() "
                "WHERE id = $1", rid,
            )


async def has_photo_views(rid: int) -> bool:
    """Whether this player has EVER received a photo (any product session).

    Drives the introduction-photo rule: only a player who has never seen a
    photo qualifies, so the intro can't refire after a delivery (the view row
    lands in the same transaction as the send)."""
    row = await _fetchrow(
        "SELECT 1 FROM retention_photo_views WHERE retention_user_id = $1 "
        "LIMIT 1", rid,
    )
    return row is not None


async def set_photo_file_id(photo_id: int, file_id: str) -> None:
    await _execute(
        "UPDATE retention_photos SET telegram_file_id = $2, updated_at = now() "
        "WHERE id = $1", photo_id, file_id,
    )


async def set_retention_photo_storage_ref(photo_id: int,
                                          storage_ref: str) -> None:
    """Re-point a PHOTO row at a new stored binary (the media normalizer).

    telegram_file_id is deliberately KEPT: it references the copy already on
    Telegram's servers, which stays valid — only future first-uploads read the
    new file. (Photos only — Telegram re-compresses them anyway. Video
    re-points go through set_retention_video_normalized, which CLEARS the
    file_id: a video file_id pins the exact uploaded binary, so keeping it
    would serve the pre-normalization copy forever.)
    """
    await _execute(
        "UPDATE retention_photos SET storage_ref = $2, updated_at = now() "
        "WHERE id = $1", photo_id, storage_ref,
    )


async def set_retention_video_normalized(photo_id: int, *, storage_ref: str,
                                         width: Optional[int],
                                         height: Optional[int],
                                         duration_sec: Optional[int]) -> None:
    """Re-point a VIDEO row at its normalized binary, in one write: the new
    storage_ref, the probed sendVideo attrs, and telegram_file_id cleared
    (Telegram's cached copy is the old binary — the next send re-uploads)."""
    await _execute(
        "UPDATE retention_photos SET storage_ref = $2, tg_width = $3, "
        "tg_height = $4, tg_duration_sec = $5, telegram_file_id = NULL, "
        "updated_at = now() WHERE id = $1",
        photo_id, storage_ref, width, height, duration_sec,
    )


async def clear_photo_file_id(photo_id: int) -> None:
    """Drop a row's cached telegram_file_id on its own.

    The SAR repair calls this BEFORE swapping the on-disk file: if the process
    dies between the two, the next send re-uploads the (still-squished) file
    and the next sweep re-probes non-square and repairs again — the repair
    converges instead of leaving Telegram's broken copy pinned forever."""
    await _execute(
        "UPDATE retention_photos SET telegram_file_id = NULL, "
        "updated_at = now() WHERE id = $1", photo_id,
    )


async def set_retention_video_meta(photo_id: int, *, width: Optional[int],
                                   height: Optional[int],
                                   duration_sec: Optional[int],
                                   clear_file_id: bool = False) -> None:
    """Store/refresh a video's probed sendVideo attrs on the row.

    `clear_file_id=True` is the SAR-repair path: the on-disk binary was
    re-encoded in place, so the file_id Telegram holds points at the broken
    copy and must be dropped; the plain backfill keeps it."""
    await _execute(
        "UPDATE retention_photos SET tg_width = $2, tg_height = $3, "
        "tg_duration_sec = $4, "
        "telegram_file_id = CASE WHEN $5 THEN NULL ELSE telegram_file_id END, "
        "updated_at = now() WHERE id = $1",
        photo_id, width, height, duration_sec, clear_file_id,
    )


def _row_to_photo(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if d.get("id") is not None:
        d["id"] = int(d["id"])
    d["tags"] = _json_value(d.get("tags")) if d.get("tags") is not None else []
    _iso_fields(d, "created_at", "updated_at")
    return d


_PHOTO_COLS = ("id, product_id, storage_ref, media_type, telegram_file_id, "
               "tg_width, tg_height, tg_duration_sec, "
               "description, tags, level_min, stage, category, sort_order, "
               "active, views_count, created_by, created_at, updated_at")


async def list_retention_photos(product_id: int, *, active_only: bool = False
                                ) -> list[dict[str, Any]]:
    where = "product_id = $1" + (" AND active" if active_only else "")
    rows = await _fetch(
        f"SELECT {_PHOTO_COLS} FROM retention_photos WHERE {where} "
        "ORDER BY sort_order, id", product_id,
    )
    return [_row_to_photo(r) for r in rows]


async def get_retention_photo(photo_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_PHOTO_COLS} FROM retention_photos WHERE id = $1", photo_id)
    return _row_to_photo(row) if row else None


async def create_retention_photo(product_id: int, *, storage_ref: Optional[str],
                                 description: str, tags: list[str],
                                 level_min: int, stage: int,
                                 category: Optional[str], sort_order: int,
                                 created_by: Optional[str],
                                 media_type: str = "photo") -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_photos "
        "(product_id, storage_ref, media_type, description, tags, level_min, "
        " stage, category, sort_order, created_by) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10) "
        f"RETURNING {_PHOTO_COLS}",
        product_id, storage_ref, media_type if media_type == "video" else "photo",
        description or "", json.dumps(tags or []),
        level_min, stage, category, sort_order, created_by,
    )
    return _row_to_photo(row)


async def update_retention_photo(photo_id: int, **fields: Any
                                 ) -> Optional[dict[str, Any]]:
    allowed = ("description", "tags", "level_min", "stage", "category",
               "sort_order", "active", "storage_ref")
    sets = ["updated_at = now()"]
    args: list[Any] = []
    for col in allowed:
        if col in fields:
            args.append(json.dumps(fields[col]) if col == "tags" else fields[col])
            sets.append(f"{col} = ${len(args)}"
                        + ("::jsonb" if col == "tags" else ""))
    args.append(photo_id)
    row = await _fetchrow(
        f"UPDATE retention_photos SET {', '.join(sets)} "
        f"WHERE id = ${len(args)} RETURNING {_PHOTO_COLS}", *args,
    )
    return _row_to_photo(row) if row else None


async def delete_retention_photo(photo_id: int) -> Optional[dict[str, Any]]:
    """Hard-delete a media row and its seen-photo ledger, in one transaction.

    `retention_photo_views.photo_id` is a NOT NULL FK to `retention_photos`
    without ON DELETE CASCADE, so the view rows are removed FIRST or the delete
    would violate the constraint. Returns the deleted row's `{storage_ref,
    media_type}` (so the caller can remove the on-disk binary + video poster) or
    `None` when no such photo existed. (This used to be a soft-delete flipping
    `active=false`, which left the row — and the file — in place; the admin
    Delete button then only deactivated the item instead of removing it.)
    """
    async with _acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT storage_ref, media_type FROM retention_photos "
                "WHERE id = $1", photo_id)
            if row is None:
                return None
            await conn.execute(
                "DELETE FROM retention_photo_views WHERE photo_id = $1", photo_id)
            await conn.execute(
                "DELETE FROM retention_photos WHERE id = $1", photo_id)
    return {"storage_ref": row["storage_ref"], "media_type": row["media_type"]}


# Measured effectiveness of one media item, as a candidate-ordering key:
# the smoothed share of its deliveries the player answered (Laplace prior, so
# an unproven item scores a neutral 0.5 and a single lucky send can't jump the
# queue). Settled rows only — a photo sent an hour ago has not had its chance
# yet and must not count as a failure.
_PHOTO_OUTCOME_SCORE_SQL = (
    "(SELECT (COUNT(*) FILTER (WHERE o.replied_at IS NOT NULL) + 1.0) "
    "        / (COUNT(*) + 2.0) "
    "   FROM retention_outcomes o "
    "  WHERE o.photo_id = retention_photos.id AND o.closed)::float8 "
    "AS outcome_score"
)


def _video_slot_cap(limit: int) -> int:
    """How many candidate slots videos may occupy in the mixed feed.

    Photos stay the staple, but videos must stay PRESENT: the default list of
    6 carries 2 videos + 4 photos, and the video share never drops below 2
    while the list has room for it (limit 4 -> 2+2). Only a very small list
    shrinks it: limit 3 -> 1 video + 2 photos, limit 2 -> 1+1; a 1-slot list
    is photos-only (the feed is photo-first at the extreme). Larger lists
    scale at about a third (limit 9 -> 3, limit 12 -> 4).
    """
    if limit <= 1:
        return 0
    if limit <= 3:
        return 1
    return max(2, limit // 3)


# Only a NORMALIZED video (re-encoded to the .tg.mp4 delivery format) is ever
# offered as a candidate: a just-uploaded raw original (multi-hundred-MB .mov)
# must not be uploadable to Telegram before the transcode finishes.
# NB '.tg.mp4' is media_normalizer._VIDEO_NORM_SUFFIX — SQL can't import it,
# keep the two in sync.
_VIDEO_SENDABLE_SQL = "storage_ref LIKE '%.tg.mp4'"


async def candidate_photos(product_id: int, retention_user_id: int, *,
                           level_ordinal: int, max_stage: int, limit: int,
                           media: Optional[str] = None
                           ) -> list[dict[str, Any]]:
    """Media eligible for this player: active, within tier + stage gate, unseen.

    Ordered by stage then least-viewed then sort_order so the model sees a
    small, fresh, on-tier candidate set. Photos and videos ride ONE stream
    with a fixed video share (see _video_slot_cap: the default list of 6 =
    4 photos + 2 videos, never below 2 videos while the list has room); when
    there are fewer of one kind than its share, the other kind fills the freed
    slots (the feed only shrinks when BOTH kinds are exhausted).
    `media='video'`/'photo' restricts the set to one kind (the idle ladder's
    explicit video-ping action) — a video-only list is NOT share-capped.
    Un-normalized videos are never offered.
    """
    async def _fetch_videos(lim: int, offset: int = 0) -> list[asyncpg.Record]:
        if lim <= 0:
            return []
        return await _fetch(
            f"SELECT {_PHOTO_COLS}, {_PHOTO_OUTCOME_SCORE_SQL} "
            "FROM retention_photos "
            "WHERE product_id = $1 AND active AND media_type = 'video' "
            f"  AND {_VIDEO_SENDABLE_SQL} "
            "  AND level_min <= $2 AND stage <= $3 "
            "  AND id NOT IN (SELECT photo_id FROM retention_photo_views "
            "                 WHERE retention_user_id = $4) "
            "ORDER BY stage, views_count, outcome_score DESC, sort_order, id "
            "LIMIT $5 OFFSET $6",
            product_id, level_ordinal, max_stage, retention_user_id,
            lim, offset,
        )

    video_limit = (limit if media == "video"
                   else min(_video_slot_cap(limit), limit))
    videos = [] if media == "photo" else await _fetch_videos(video_limit)
    photos = [] if media == "video" else await _fetch(
        f"SELECT {_PHOTO_COLS}, {_PHOTO_OUTCOME_SCORE_SQL} "
        "FROM retention_photos "
        "WHERE product_id = $1 AND active AND media_type <> 'video' "
        "  AND level_min <= $2 AND stage <= $3 "
        "  AND id NOT IN (SELECT photo_id FROM retention_photo_views "
        "                 WHERE retention_user_id = $4) "
        "ORDER BY stage, views_count, outcome_score DESC, sort_order, id "
        "LIMIT $5",
        product_id, level_ordinal, max_stage, retention_user_id,
        max(limit - len(videos), 0),
    )
    # Symmetric backfill: a player who has seen every photo still gets a full
    # feed of the remaining videos. The ORDER BY is total, so OFFSET skips
    # exactly the rows fetched above.
    if media is None and len(videos) + len(photos) < limit:
        videos = list(videos) + await _fetch_videos(
            limit - len(videos) - len(photos), offset=len(videos))
    merged = [_row_to_photo(r) for r in list(photos) + list(videos)]
    # Same key as the SQL ORDER BY (the two feeds are merged in Python, so the
    # per-query ordering alone would not survive): stage, then freshness, then
    # measured effectiveness, then the librarian's manual order.
    merged.sort(key=lambda p: (p.get("stage") or 0, p.get("views_count") or 0,
                               -float(p.get("outcome_score") or 0.5),
                               p.get("sort_order") or 0, p["id"]))
    return merged


async def retention_appearance_context(product_id: int, retention_user_id: int
                                        ) -> dict[str, Any]:
    """The persona-appearance grounding for the retention Layer-3 block.

    Returns {"base": [...], "last_sent": description|None, "sent": [...]}:
    `base` is a small stable sample of the product's ACTIVE photo library
    (lowest stages first, deterministic order - the canonical look the model
    may describe even when no photo is currently sendable); `sent` is the
    media THIS player already received, oldest-first ({description,
    media_type, viewed_at}) - it feeds the SENT-media prompt block, so the
    model can DESCRIBE a photo it sent earlier instead of refusing or sending
    a new one ("describe the photo you showed me" used to dead-end because
    only the newest photo was carried); `last_sent` stays the newest one
    ("what you look like right now" to him). Descriptions doubling as
    appearance context is why they matter beyond captions - see
    prompts._appearance_directive / _sent_media_directive.
    """
    base_rows = await _fetch(
        "SELECT description FROM retention_photos "
        "WHERE product_id = $1 AND active AND description <> '' "
        "ORDER BY stage, sort_order, id LIMIT 3",
        product_id,
    )
    # Keyed on the player (not the session): views survive the idle chat
    # rollover, so "that photo from yesterday" is answerable in a fresh chat.
    sent_rows = await _fetch(
        "SELECT p.description, p.media_type, p.stage, v.viewed_at FROM ("
        "  SELECT photo_id, viewed_at FROM retention_photo_views "
        "  WHERE retention_user_id = $1 "
        "  ORDER BY viewed_at DESC, id DESC LIMIT 8"
        ") v JOIN retention_photos p ON p.id = v.photo_id "
        "WHERE p.description <> '' ORDER BY v.viewed_at ASC",
        retention_user_id,
    )
    # `stage` feeds the Layer-3 OPENNESS LEVEL ladder: the boldest photo the
    # player actually received bounds how bold Nika's WORDS may be. The ladder
    # keys on `max_sent_stage` — an aggregate over ALL views, not the 8-view
    # window above (and not gated on a description): a run of recent tamer
    # photos must not silently drop the verbal register back down after he has
    # already seen a bolder one.
    max_sent_stage = await _fetchval(
        "SELECT COALESCE(MAX(p.stage), 0) FROM retention_photo_views v "
        "JOIN retention_photos p ON p.id = v.photo_id "
        "WHERE v.retention_user_id = $1",
        retention_user_id,
    )
    sent = [{"description": r["description"], "media_type": r["media_type"],
             "stage": r["stage"], "viewed_at": r["viewed_at"]} for r in sent_rows]
    return {
        "base": [r["description"] for r in base_rows],
        "last_sent": sent[-1]["description"] if sent else None,
        "sent": sent,
        "max_sent_stage": int(max_sent_stage or 0),
    }


# --- retention_kb ----------------------------------------------------------
def _row_to_retention_kb(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if d.get("id") is not None:
        d["id"] = int(d["id"])
    d["links"] = _json_value(d.get("links")) if d.get("links") is not None else []
    _iso_fields(d, "created_at", "updated_at")
    return d


_RKB_COLS = ("id, product_id, title, trigger_when, body, links, sort_order, "
             "active, updated_by, created_at, updated_at")


async def list_retention_kb(product_id: int, *, active_only: bool = False
                            ) -> list[dict[str, Any]]:
    where = "product_id = $1" + (" AND active" if active_only else "")
    rows = await _fetch(
        f"SELECT {_RKB_COLS} FROM retention_kb WHERE {where} "
        "ORDER BY sort_order, id", product_id,
    )
    return [_row_to_retention_kb(r) for r in rows]


async def get_retention_kb_entry(entry_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_RKB_COLS} FROM retention_kb WHERE id = $1", entry_id)
    return _row_to_retention_kb(row) if row else None


async def create_retention_kb(product_id: int, *, title: str,
                              trigger_when: Optional[str], body: str,
                              links: list[str], sort_order: int,
                              active: bool = True,
                              updated_by: Optional[str]) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_kb "
        "(product_id, title, trigger_when, body, links, sort_order, active, "
        " updated_by) "
        "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8) RETURNING " + _RKB_COLS,
        product_id, title, trigger_when, body, json.dumps(links or []),
        sort_order, active, updated_by,
    )
    return _row_to_retention_kb(row)


async def update_retention_kb(entry_id: int, *, updated_by: Optional[str] = None,
                              **fields: Any) -> Optional[dict[str, Any]]:
    allowed = ("title", "trigger_when", "body", "links", "sort_order", "active")
    sets = ["updated_at = now()"]
    args: list[Any] = []
    for col in allowed:
        if col in fields:
            args.append(json.dumps(fields[col]) if col == "links" else fields[col])
            sets.append(f"{col} = ${len(args)}"
                        + ("::jsonb" if col == "links" else ""))
    args.append(updated_by)
    sets.append(f"updated_by = ${len(args)}")
    args.append(entry_id)
    row = await _fetchrow(
        f"UPDATE retention_kb SET {', '.join(sets)} "
        f"WHERE id = ${len(args)} RETURNING {_RKB_COLS}", *args,
    )
    return _row_to_retention_kb(row) if row else None


async def delete_retention_kb(entry_id: int) -> bool:
    row = await _fetchrow(
        "DELETE FROM retention_kb WHERE id = $1 RETURNING id", entry_id)
    return row is not None


# The retention KB is edited as ONE free-text document per product (like a
# support topic's KB text). It is stored as a single retention_kb row whose
# title is this sentinel; retention_kb_block emits its body verbatim (no "##"
# header). Legacy structured entries (title/when/body/links rows from the old
# per-entry editor) still render, and are folded into the document on the
# first save through set_retention_kb_text.
RETENTION_KB_DOC_TITLE = "__retention_kb_document__"


def _legacy_retention_entry_text(entry: dict[str, Any]) -> str:
    """Render one legacy structured entry in the same shape the prompt used."""
    block = [f"## {entry['title']}"]
    if entry.get("trigger_when"):
        block.append(f"When: {entry['trigger_when']}")
    block.append(entry["body"])
    links = entry.get("links") or []
    if links:
        block.append("Links: " + ", ".join(str(l) for l in links))
    return "\n".join(block)


async def get_retention_kb_text(product_id: int) -> str:
    """The retention KB as one editable text document.

    Single-doc storage returns the body as-is; legacy structured entries are
    rendered into the same text shape the prompt received, so nothing is lost
    when the owner edits and re-saves them as one document.
    """
    entries = await list_retention_kb(product_id)
    if len(entries) == 1 and entries[0]["title"] == RETENTION_KB_DOC_TITLE:
        return entries[0]["body"]
    return "\n\n".join(_legacy_retention_entry_text(e) for e in entries)


async def set_retention_kb_text(product_id: int, text: str,
                                updated_by: Optional[str] = None) -> None:
    """Replace the product's whole retention KB with one text document.

    Atomic: the legacy rows (or the previous document) and the new document
    swap in one transaction, so the prompt never sees a half-written KB.
    An empty text simply clears the KB (the retention prompt then carries no
    Layer-2 block).
    """
    async with _acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM retention_kb WHERE product_id = $1", product_id)
            if text.strip():
                await conn.execute(
                    "INSERT INTO retention_kb "
                    "(product_id, title, body, links, updated_by) "
                    "VALUES ($1, $2, $3, '[]'::jsonb, $4)",
                    product_id, RETENTION_KB_DOC_TITLE, text, updated_by,
                )


async def seed_starter_retention_kb(product_id: int) -> None:
    """Seed a new product's retention KB with the generic starter document.

    Mirrors seed_starter_kb: runs only from create_product, inserts only when
    the product has no retention KB at all — it can never overwrite content.
    """
    from app.ai import starter_kb  # local import (starter_kb → prompts) to avoid a cycle

    existing = await _fetchval(
        "SELECT count(*) FROM retention_kb WHERE product_id = $1", product_id)
    if existing:
        return
    await _execute(
        "INSERT INTO retention_kb (product_id, title, body, links, updated_by) "
        "VALUES ($1, $2, $3, '[]'::jsonb, 'starter-seed')",
        product_id, RETENTION_KB_DOC_TITLE, starter_kb.STARTER_RETENTION_KB,
    )


# --- retention_managers ----------------------------------------------------
def _row_to_manager(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    if d.get("id") is not None:
        d["id"] = int(d["id"])
    _iso_fields(d, "created_at", "updated_at")
    return d


_MGR_COLS = ("id, product_id, display_name, username, active, "
             "assigned_count, created_at, updated_at")


async def list_retention_managers(product_id: int) -> list[dict[str, Any]]:
    rows = await _fetch(
        f"SELECT {_MGR_COLS} FROM retention_managers WHERE product_id = $1 "
        "ORDER BY id", product_id,
    )
    return [_row_to_manager(r) for r in rows]


async def get_retention_manager(manager_id: int) -> Optional[dict[str, Any]]:
    row = await _fetchrow(
        f"SELECT {_MGR_COLS} FROM retention_managers WHERE id = $1", manager_id)
    return _row_to_manager(row) if row else None


async def create_retention_manager(product_id: int, *, display_name: str,
                                   username: str) -> dict[str, Any]:
    row = await _fetchrow(
        "INSERT INTO retention_managers (product_id, display_name, username) "
        f"VALUES ($1, $2, $3) RETURNING {_MGR_COLS}",
        product_id, display_name, username.lstrip("@"),
    )
    return _row_to_manager(row)


async def update_retention_manager(manager_id: int, **fields: Any
                                   ) -> Optional[dict[str, Any]]:
    allowed = ("display_name", "username", "active")
    sets = ["updated_at = now()"]
    args: list[Any] = []
    for col in allowed:
        if col in fields:
            val = fields[col]
            if col == "username" and isinstance(val, str):
                val = val.lstrip("@")
            args.append(val)
            sets.append(f"{col} = ${len(args)}")
    args.append(manager_id)
    row = await _fetchrow(
        f"UPDATE retention_managers SET {', '.join(sets)} "
        f"WHERE id = ${len(args)} RETURNING {_MGR_COLS}", *args,
    )
    return _row_to_manager(row) if row else None


async def delete_retention_manager(manager_id: int) -> bool:
    row = await _fetchrow(
        "DELETE FROM retention_managers WHERE id = $1 RETURNING id", manager_id)
    return row is not None


async def assign_round_robin_manager(product_id: int, retention_user_id: int
                                     ) -> Optional[dict[str, Any]]:
    """Pick + assign a manager for a player, round-robin and STICKY.

    A player already assigned to an ACTIVE manager keeps that manager (continuity).
    Otherwise the least-loaded active manager is chosen, its assigned_count bumped,
    and stored on the player. Returns the manager, or None when the pool is empty.
    """
    async with _acquire() as conn:
        async with conn.transaction():
            ru = await conn.fetchrow(
                "SELECT assigned_manager_id FROM retention_users WHERE id = $1 "
                "FOR UPDATE", retention_user_id,
            )
            if ru and ru["assigned_manager_id"] is not None:
                existing = await conn.fetchrow(
                    f"SELECT {_MGR_COLS} FROM retention_managers "
                    "WHERE id = $1 AND active", ru["assigned_manager_id"],
                )
                if existing:
                    return _row_to_manager(existing)
            chosen = await conn.fetchrow(
                f"SELECT {_MGR_COLS} FROM retention_managers "
                "WHERE product_id = $1 AND active "
                "ORDER BY assigned_count, id LIMIT 1 FOR UPDATE", product_id,
            )
            if chosen is None:
                return None
            await conn.execute(
                "UPDATE retention_managers SET assigned_count = assigned_count + 1, "
                "updated_at = now() WHERE id = $1", chosen["id"],
            )
            await conn.execute(
                "UPDATE retention_users SET assigned_manager_id = $2, "
                "updated_at = now() WHERE id = $1",
                retention_user_id, chosen["id"],
            )
            return _row_to_manager(chosen)


# --- retention_nonces ------------------------------------------------------
async def create_retention_nonce(nonce: str, product_id: int,
                                 payload: dict[str, Any], escalation: bool,
                                 ttl_sec: int) -> None:
    # Opportunistically reap expired nonces (indexed on expires_at, low volume)
    # so the single-use table can't grow without bound.
    await _execute("DELETE FROM retention_nonces WHERE expires_at < now()")
    await _execute(
        "INSERT INTO retention_nonces "
        "(nonce, product_id, payload, escalation, expires_at) "
        "VALUES ($1, $2, $3::jsonb, $4, now() + ($5 || ' seconds')::interval)",
        nonce, product_id, json.dumps(payload or {}), escalation, str(int(ttl_sec)),
    )


async def redeem_retention_nonce(nonce: str,
                                 product_id: Optional[int] = None
                                 ) -> Optional[dict[str, Any]]:
    """Atomically consume a valid, unused, unexpired nonce. Returns its data or None.

    ``product_id`` scopes the redemption to the bot the /start arrived on: a
    nonce minted for brand B must never link brand B's player profile into
    brand A's bot (a cross-tenant data leak).
    """
    row = await _fetchrow(
        "UPDATE retention_nonces SET used = TRUE "
        "WHERE nonce = $1 AND NOT used AND expires_at > now() "
        "AND ($2::int IS NULL OR product_id = $2) "
        "RETURNING product_id, payload, escalation",
        nonce, product_id,
    )
    if row is None:
        return None
    return {
        "product_id": row["product_id"],
        "payload": _json_value(row["payload"]) or {},
        "escalation": row["escalation"],
    }


# --- retention analytics ---------------------------------------------------
def _pid_where(product_ids: Optional[list[int]], args: list[Any],
               col: str = "product_id") -> str:
    """SQL filter for a product-id scope: None = all, [] = match nothing."""
    if product_ids is None:
        return "TRUE"
    args.append(product_ids)
    return f"{col} = ANY(${len(args)}::int[])"


async def retention_overview(product_ids: Optional[list[int]], dt_from: Any,
                             dt_to: Any) -> dict[str, Any]:
    """Retention KPIs, split into LIFETIME numbers (the player base as it is
    now) and RANGE numbers (what happened between dt_from and dt_to) so the
    two are never mixed in one row. `product_ids` follows the dashboard scope
    convention: None = all accessible, [] = none."""
    args: list[Any] = [dt_from, dt_to]
    pid = _pid_where(product_ids, args)
    args_msgs: list[Any] = [dt_from, dt_to]
    pid_msgs = _pid_where(product_ids, args_msgs, "s.product_id")
    args2: list[Any] = [dt_from, dt_to]
    pid2 = _pid_where(product_ids, args2, "p.product_id")
    args3: list[Any] = [dt_from, dt_to]
    # Telegram cost is scoped on the LOG row's product so it captures the dialog
    # turns (a telegram session) AND every background call, which log with
    # session_id IS NULL (no session to join). The four drivers are split by the
    # row's own attribution labels — dialogue, the proactive agent, media
    # cataloguing and the AI judge — because they answer different questions
    # ("is the bot too chatty?" vs "is the judge too expensive?") and used to be
    # lumped together under photo metadata.
    pid3 = _pid_where(product_ids, args3, "l.product_id")
    args4: list[Any] = []
    pid4 = _pid_where(product_ids, args4)
    # Independent aggregates — run them concurrently (this feeds the admin's
    # landing dashboard, so wall time matters more than anywhere else).
    users, photos, handoffs, messages, pings, cost, stage_rows = \
        await asyncio.gather(
            _fetchrow(
                "SELECT COUNT(*) AS total, "
                "  COUNT(*) FILTER (WHERE subscribed) AS subscribed, "
                "  COUNT(*) FILTER (WHERE pings_muted) AS pings_muted, "
                "  COUNT(*) FILTER (WHERE unreachable) AS unreachable, "
                "  COUNT(*) FILTER (WHERE last_active_at >= $1 "
                "    AND last_active_at < $2) AS active_in_range, "
                "  COUNT(*) FILTER (WHERE created_at >= $1 AND created_at < $2) "
                "    AS new_in_range, "
                "  COALESCE(AVG(unlocked_stage), 0) AS avg_stage "
                f"FROM retention_users WHERE {pid}", *args),
            _fetchval(
                "SELECT COUNT(*) FROM retention_photo_views "
                f"WHERE {pid} AND viewed_at >= $1 AND viewed_at < $2", *args),
            _fetchval(
                # Only 'retention_handoff': a [[HANDOFF]] with a manager
                # configured ALSO logs 'retention_manager_handoff' for the same
                # hand-off, so counting both doubled the KPI.
                "SELECT COUNT(*) FROM admin_events "
                f"WHERE {pid} AND type = 'retention_handoff' "
                "  AND created_at >= $1 AND created_at < $2", *args),
            _fetchrow(
                "SELECT COUNT(*) AS user_msgs, "
                "  COUNT(DISTINCT s.tg_user_id) AS senders "
                "FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
                f"WHERE s.consumer = 'telegram' AND {pid_msgs} "
                "  AND m.role = 'user' AND m.created_at >= $1 "
                "  AND m.created_at < $2", *args_msgs),
            _fetchrow(
                "SELECT COUNT(*) FILTER (WHERE p.status = 'sent') AS sent, "
                "  COUNT(*) FILTER (WHERE p.status = 'failed') AS failed, "
                "  COUNT(*) FILTER (WHERE p.status = 'sent' AND EXISTS ("
                "    SELECT 1 FROM chat_messages m "
                "    JOIN chat_sessions s ON s.id = m.session_id "
                "    JOIN retention_users u ON u.id = p.retention_user_id "
                "    WHERE s.tg_user_id = u.tg_user_id "
                "      AND s.product_id = p.product_id "
                "      AND m.role = 'user' AND m.created_at > p.created_at "
                "      AND m.created_at < p.created_at + interval '48 hours')) "
                "    AS replied "
                f"FROM retention_pings p WHERE {pid2} "
                "  AND p.created_at >= $1 AND p.created_at < $2", *args2),
            _fetchrow(
                "SELECT "
                "  COALESCE(SUM(l.cost_usd) FILTER "
                f"    (WHERE {_LOG_SOURCE} = 'chat'), 0) AS dialog, "
                "  COALESCE(SUM(l.cost_usd) FILTER "
                f"    (WHERE {_LOG_SOURCE} = 'agent'), 0) AS agent, "
                "  COALESCE(SUM(l.cost_usd) FILTER "
                f"    (WHERE {_LOG_SOURCE} = 'media'), 0) AS photo, "
                "  COALESCE(SUM(l.cost_usd) FILTER "
                f"    (WHERE {_LOG_SOURCE} = 'review'), 0) AS review, "
                "  COALESCE(SUM(l.cost_usd) FILTER "
                f"    (WHERE {_LOG_SOURCE} = 'legacy'), 0) AS legacy, "
                "  COALESCE(SUM(l.cost_usd), 0) AS total "
                "FROM ai_interaction_logs l "
                "LEFT JOIN chat_sessions s ON s.id = l.session_id "
                f"WHERE {pid3} AND {_LOG_IS_RETENTION} "
                "  AND l.created_at >= $1 AND l.created_at < $2", *args3),
            _fetch(
                "SELECT unlocked_stage AS stage, COUNT(*) AS users "
                f"FROM retention_users WHERE {pid4} "
                "GROUP BY unlocked_stage ORDER BY unlocked_stage", *args4),
        )
    sent = int(pings["sent"] or 0) if pings else 0
    replied = int(pings["replied"] or 0) if pings else 0
    return {
        "users": {
            "total": int(users["total"] or 0),
            "subscribed": int(users["subscribed"] or 0),
            "pings_muted": int(users["pings_muted"] or 0),
            "unreachable": int(users["unreachable"] or 0),
            "avg_stage": round(float(users["avg_stage"] or 0), 2),
        },
        "range": {
            "active_users": int(users["active_in_range"] or 0),
            "new_users": int(users["new_in_range"] or 0),
            "user_messages": int(messages["user_msgs"] or 0) if messages else 0,
            "photos_sent": int(photos or 0),
            "handoffs": int(handoffs or 0),
            "pings_sent": sent,
            "pings_failed": int(pings["failed"] or 0) if pings else 0,
            "ping_replies": replied,
            "ping_reply_rate": round(replied / sent, 3) if sent else None,
            # Total telegram AI spend for the range, plus its drivers, which sum
            # back to it: player dialogue, the proactive agent (decisions +
            # written pings), photo/video cataloguing, the AI judge reading
            # finished Telegram chats, and — for rows written before spend
            # attribution shipped — an unlabelled remainder.
            "cost_usd": round(float(cost["total"] if cost else 0), 4),
            "cost_dialog_usd": round(float(cost["dialog"] if cost else 0), 4),
            "cost_agent_usd": round(float(cost["agent"] if cost else 0), 4),
            "cost_photo_usd": round(float(cost["photo"] if cost else 0), 4),
            "cost_review_usd": round(float(cost["review"] if cost else 0), 4),
            "cost_legacy_usd": round(float(cost["legacy"] if cost else 0), 4),
        },
        "stage_distribution": [
            {"stage": int(r["stage"]), "users": int(r["users"])}
            for r in stage_rows
        ],
        # Legacy flat keys (pre-split API consumers).
        "users_total": int(users["total"] or 0),
        "users_subscribed": int(users["subscribed"] or 0),
        "users_active": int(users["active_in_range"] or 0),
        "avg_stage": round(float(users["avg_stage"] or 0), 2),
        "photos_sent": int(photos or 0),
        "handoffs": int(handoffs or 0),
    }


async def retention_funnel(product_ids: Optional[list[int]], dt_from: Any,
                           dt_to: Any) -> dict[str, Any]:
    """The entry funnel for the range: deeplinks minted -> /start redemptions ->
    new linked players -> subscribed -> engaged (wrote a message) -> got a photo
    -> handed off. Sources: durable admin_events for the two event steps (the
    nonce table is reaped on expiry, so it cannot be the denominator), the
    retention tables for the rest."""
    args: list[Any] = [dt_from, dt_to]
    pid = _pid_where(product_ids, args)
    events = await _fetchrow(
        "SELECT COUNT(*) FILTER (WHERE type = 'retention_deeplink_created') "
        "    AS deeplinks, "
        "  COUNT(*) FILTER (WHERE type = 'retention_start') AS starts, "
        # Only 'retention_handoff' (the manager event duplicates it per hand-off).
        "  COUNT(*) FILTER (WHERE type = 'retention_handoff') AS handoffs "
        f"FROM admin_events WHERE {pid} "
        "  AND created_at >= $1 AND created_at < $2", *args,
    )
    users = await _fetchrow(
        "SELECT COUNT(*) AS new_users, "
        "  COUNT(*) FILTER (WHERE subscribed) AS subscribed "
        f"FROM retention_users WHERE {pid} "
        "  AND created_at >= $1 AND created_at < $2", *args,
    )
    args2: list[Any] = [dt_from, dt_to]
    pid2 = _pid_where(product_ids, args2, "s.product_id")
    engaged = await _fetchval(
        "SELECT COUNT(DISTINCT s.tg_user_id) "
        "FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
        f"WHERE s.consumer = 'telegram' AND {pid2} AND m.role = 'user' "
        "  AND m.created_at >= $1 AND m.created_at < $2", *args2,
    )
    args3: list[Any] = [dt_from, dt_to]
    pid3 = _pid_where(product_ids, args3)
    photo_receivers = await _fetchval(
        "SELECT COUNT(DISTINCT retention_user_id) FROM retention_photo_views "
        f"WHERE {pid3} AND viewed_at >= $1 AND viewed_at < $2", *args3,
    )
    return {
        "deeplinks_created": int(events["deeplinks"] or 0) if events else 0,
        "starts": int(events["starts"] or 0) if events else 0,
        "new_users": int(users["new_users"] or 0) if users else 0,
        "subscribed": int(users["subscribed"] or 0) if users else 0,
        "engaged": int(engaged or 0),
        "photo_receivers": int(photo_receivers or 0),
        "handoffs": int(events["handoffs"] or 0) if events else 0,
    }


async def retention_timeseries(product_ids: Optional[list[int]], dt_from: Any,
                               dt_to: Any) -> list[dict[str, Any]]:
    """Daily retention activity: player messages + distinct active players,
    photos delivered, proactive pings sent, and OpenAI cost of telegram turns.
    One merged row per day (days with no activity in any source are omitted)."""
    out: dict[str, dict[str, Any]] = {}

    def _day(row_day: Any) -> str:
        return str(row_day)[:10]

    args: list[Any] = [dt_from, dt_to]
    pid = _pid_where(product_ids, args, "s.product_id")
    for r in await _fetch(
            "SELECT date_trunc('day', m.created_at) AS day, "
            "  COUNT(*) AS msgs, COUNT(DISTINCT s.tg_user_id) AS actives "
            "FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
            f"WHERE s.consumer = 'telegram' AND {pid} AND m.role = 'user' "
            "  AND m.created_at >= $1 AND m.created_at < $2 "
            "GROUP BY 1", *args):
        d = out.setdefault(_day(r["day"]), {})
        d["messages"] = int(r["msgs"])
        d["active_users"] = int(r["actives"])

    args = [dt_from, dt_to]
    pid = _pid_where(product_ids, args)
    for r in await _fetch(
            "SELECT date_trunc('day', viewed_at) AS day, COUNT(*) AS n "
            f"FROM retention_photo_views WHERE {pid} "
            "  AND viewed_at >= $1 AND viewed_at < $2 GROUP BY 1", *args):
        out.setdefault(_day(r["day"]), {})["photos"] = int(r["n"])

    args = [dt_from, dt_to]
    pid = _pid_where(product_ids, args)
    for r in await _fetch(
            "SELECT date_trunc('day', created_at) AS day, COUNT(*) AS n "
            f"FROM retention_pings WHERE {pid} AND status = 'sent' "
            "  AND created_at >= $1 AND created_at < $2 GROUP BY 1", *args):
        out.setdefault(_day(r["day"]), {})["pings"] = int(r["n"])

    # Telegram AI cost per day, scoped on the LOG product so it captures the
    # dialog turns AND every session-less background call, split by the row's own
    # attribution labels (see _LOG_SOURCE) rather than by "has a session".
    args = [dt_from, dt_to]
    pid = _pid_where(product_ids, args, "l.product_id")
    for r in await _fetch(
            "SELECT date_trunc('day', l.created_at) AS day, "
            "  COALESCE(SUM(l.cost_usd) FILTER "
            f"    (WHERE {_LOG_SOURCE} = 'chat'), 0) AS dialog, "
            "  COALESCE(SUM(l.cost_usd) FILTER "
            f"    (WHERE {_LOG_SOURCE} = 'agent'), 0) AS agent, "
            "  COALESCE(SUM(l.cost_usd) FILTER "
            f"    (WHERE {_LOG_SOURCE} = 'media'), 0) AS photo, "
            "  COALESCE(SUM(l.cost_usd) FILTER "
            f"    (WHERE {_LOG_SOURCE} = 'review'), 0) AS review, "
            "  COALESCE(SUM(l.cost_usd) FILTER "
            f"    (WHERE {_LOG_SOURCE} = 'legacy'), 0) AS legacy, "
            "  COALESCE(SUM(l.cost_usd), 0) AS cost "
            "FROM ai_interaction_logs l "
            "LEFT JOIN chat_sessions s ON s.id = l.session_id "
            f"WHERE {pid} AND {_LOG_IS_RETENTION} "
            "  AND l.created_at >= $1 AND l.created_at < $2 GROUP BY 1", *args):
        d = out.setdefault(_day(r["day"]), {})
        d["cost_usd"] = round(float(r["cost"]), 4)
        d["cost_dialog_usd"] = round(float(r["dialog"]), 4)
        d["cost_agent_usd"] = round(float(r["agent"]), 4)
        d["cost_photo_usd"] = round(float(r["photo"]), 4)
        d["cost_review_usd"] = round(float(r["review"]), 4)
        d["cost_legacy_usd"] = round(float(r["legacy"]), 4)

    series = []
    for day in sorted(out):
        row = {"date": day, "messages": 0, "active_users": 0, "photos": 0,
               "pings": 0, "cost_usd": 0.0, "cost_dialog_usd": 0.0,
               "cost_agent_usd": 0.0, "cost_photo_usd": 0.0,
               "cost_review_usd": 0.0, "cost_legacy_usd": 0.0}
        row.update(out[day])
        series.append(row)
    return series


async def list_retention_users(product_id: int, *, limit: int = 100,
                               offset: int = 0) -> list[dict[str, Any]]:
    rows = await _fetch(
        "SELECT u.id, u.tg_user_id, u.tg_username, u.player_id, u.entry_type, "
        "  u.vip_level, u.country, u.unlocked_stage, u.subscribed, "
        "  u.meaningful_msgs, u.photos_sent_today, u.conv_lang, u.last_active_at, "
        "  u.created_at, m.display_name AS manager_name, "
        "  (SELECT COUNT(*) FROM retention_photo_views v "
        "     WHERE v.retention_user_id = u.id) AS photos_total "
        "FROM retention_users u "
        "LEFT JOIN retention_managers m ON m.id = u.assigned_manager_id "
        "WHERE u.product_id = $1 ORDER BY u.last_active_at DESC "
        "LIMIT $2 OFFSET $3",
        product_id, limit, offset,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        for ts in ("last_active_at", "created_at"):
            if d.get(ts) is not None and hasattr(d[ts], "isoformat"):
                d[ts] = d[ts].isoformat()
        out.append(d)
    return out


async def count_retention_users(product_id: int) -> int:
    """Total linked players for this product (the paginated list's `total`)."""
    val = await _fetchval(
        "SELECT COUNT(*) FROM retention_users WHERE product_id = $1",
        product_id,
    )
    return int(val or 0)


async def close_retention_session(session_id: str,
                                  product_id: Optional[int] = None,
                                  reason: str = "idle") -> None:
    """Close an idle Telegram chat session (lifecycle rollover).

    Sets status='resolved' so the chat reads as finished in the admin; only an
    'open' session is touched (an escalated one keeps its state). Logged as an
    admin event (invariant §4 — every state transition leaves a trace).
    """
    result = await _execute(
        "UPDATE chat_sessions SET status = 'resolved', updated_at = now() "
        "WHERE id = $1 AND status = 'open'",
        session_id,
    )
    if result.endswith("1"):
        await log_admin_event(session_id, "retention_session_closed",
                              {"reason": reason}, product_id=product_id)


async def list_retention_sessions(product_id: int, *, page: int = 1,
                                  page_size: int = 25) -> dict[str, Any]:
    """Telegram (retention-bot) chat sessions for a product, newest activity
    first, joined with the player identity from retention_users and the summed
    OpenAI cost. The Retention → Conversations admin tab feeds from this — the
    support Conversations list excludes consumer='telegram' entirely."""
    total = await _fetchval(
        "SELECT COUNT(*) FROM chat_sessions "
        "WHERE product_id = $1 AND consumer = 'telegram'",
        product_id,
    )
    page = max(page, 1)
    rows = await _fetch(
        "SELECT s.id, s.status, s.escalated, s.message_count, "
        "  COALESCE(s.conv_lang, s.lang) AS lang, s.created_at, s.updated_at, "
        "  s.tg_user_id, u.tg_username, u.player_id, u.full_name, "
        "  COALESCE(c.cost_usd_total, 0) AS cost_usd_total "
        "FROM chat_sessions s "
        "LEFT JOIN retention_users u "
        "  ON u.product_id = s.product_id AND u.tg_user_id = s.tg_user_id "
        # Scope the cost aggregate to THIS product's logs (product_id = $1, an
        # indexed column) instead of scanning the whole unbounded
        # ai_interaction_logs table for every page load.
        "LEFT JOIN (SELECT session_id, SUM(cost_usd) AS cost_usd_total "
        "           FROM ai_interaction_logs WHERE product_id = $1 "
        "           GROUP BY session_id) c "
        "  ON c.session_id = s.id "
        "WHERE s.product_id = $1 AND s.consumer = 'telegram' "
        "ORDER BY s.updated_at DESC LIMIT $2 OFFSET $3",
        product_id, page_size, (page - 1) * page_size,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        for ts in ("created_at", "updated_at"):
            if d.get(ts) is not None and hasattr(d[ts], "isoformat"):
                d[ts] = d[ts].isoformat()
        d["cost_usd_total"] = round(float(d.get("cost_usd_total") or 0), 6)
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def retention_kb_block(product_id: int) -> str:
    """The whole active retention-KB rendered as a single Layer-2 text block.

    Compact and fully relevant, so it loads WHOLE and stays byte-stable per
    product. {placeholder} rendering is applied by the
    caller (kb.render_variables), same as the support KB.
    """
    entries = await list_retention_kb(product_id, active_only=True)
    parts: list[str] = []
    for e in entries:
        if e["title"] == RETENTION_KB_DOC_TITLE:
            # Single-document storage: the body IS the block (no header).
            parts.append(e["body"].strip())
            continue
        parts.append(_legacy_retention_entry_text(e))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Runtime log mirror (app_logs) + per-admin read markers
# ---------------------------------------------------------------------------
_WARN_LEVELS = ("WARNING", "ERROR", "CRITICAL")


async def insert_app_logs(items: list[dict[str, Any]]) -> None:
    """Batch-insert drained log records (logcapture.drain() output)."""
    if not items:
        return
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    rows = [
        (it.get("level", "INFO"), (it.get("logger") or "")[:200],
         (it.get("message") or "")[:8000],
         _dt.datetime.fromtimestamp(it["created"], tz=_dt.timezone.utc)
         if it.get("created") else now)
        for it in items
    ]
    await _executemany(
        "INSERT INTO app_logs (level, logger, message, created_at) "
        "VALUES ($1, $2, $3, $4)",
        rows,
    )


async def prune_app_logs(keep: int = 5000) -> None:
    """Keep only the newest `keep` ROWS (bounded table).

    Counts rows rather than doing id arithmetic (`MAX(id) - keep`): ids come
    from a sequence, and a sequence has gaps (a rolled-back insert, the cached
    block dropped on restart). With gaps the arithmetic cutoff spans FEWER than
    `keep` rows, so the view silently kept less history than configured. The
    subquery picks the id of the (keep+1)-th newest row — everything from there
    down goes; with fewer than `keep` rows it returns NULL and nothing matches.
    """
    await _execute(
        "DELETE FROM app_logs WHERE id <= "
        "(SELECT id FROM app_logs ORDER BY id DESC OFFSET $1 LIMIT 1)",
        keep,
    )


async def list_app_logs(level: Optional[str] = None, q: Optional[str] = None,
                        before_id: Optional[int] = None, limit: int = 100
                        ) -> list[dict[str, Any]]:
    """Recent log rows, newest first, with optional level / text filters.

    `level` filters to a MINIMUM severity ('warning' -> WARNING+ERROR+CRITICAL;
    'error' -> ERROR+CRITICAL) so the operator can jump straight to the bad
    ones; an exact level name also works.
    """
    where, args = [], []
    lvl = (level or "").strip().upper()
    if lvl in ("WARNING", "WARN"):
        where.append(f"level = ANY(${len(args) + 1})")
        args.append(list(_WARN_LEVELS))
    elif lvl in ("ERROR", "CRITICAL"):
        where.append(f"level = ANY(${len(args) + 1})")
        args.append(["ERROR", "CRITICAL"])
    elif lvl == "INFO":
        where.append(f"level = ${len(args) + 1}")
        args.append("INFO")
    if q:
        args.append(f"%{q}%")
        where.append(f"message ILIKE ${len(args)}")
    if before_id:
        args.append(before_id)
        where.append(f"id < ${len(args)}")
    args.append(max(1, min(limit, 500)))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = await _fetch(
        f"SELECT id, level, logger, message, created_at FROM app_logs "
        f"{where_sql} ORDER BY id DESC LIMIT ${len(args)}",
        *args,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def app_logs_unread_count(reader: str) -> int:
    """How many WARNING+ log rows are newer than this reader's last-read marker."""
    return await _fetchval(
        "SELECT COUNT(*) FROM app_logs WHERE level = ANY($1) AND id > "
        "COALESCE((SELECT last_read_id FROM app_log_reads WHERE reader = $2), 0)",
        list(_WARN_LEVELS), reader,
    ) or 0


async def mark_app_logs_read(reader: str) -> int:
    """Mark all current logs read for this reader (badge clears). Returns the id."""
    max_id = await _fetchval("SELECT COALESCE(MAX(id), 0) FROM app_logs") or 0
    await _execute(
        "INSERT INTO app_log_reads (reader, last_read_id, updated_at) "
        "VALUES ($1, $2, now()) ON CONFLICT (reader) DO UPDATE "
        "SET last_read_id = GREATEST(app_log_reads.last_read_id, EXCLUDED.last_read_id), "
        "updated_at = now()",
        reader, max_id,
    )
    return max_id


# ---------------------------------------------------------------------------
# Admin action audit (admin_audit_log)
# ---------------------------------------------------------------------------
async def log_audit(*, actor_email: str, actor_role: Optional[str], method: str,
                    path: str, action: Optional[str], product_id: Optional[int],
                    status: Optional[int]) -> None:
    """Record one mutating admin action. Best-effort (caller swallows errors).

    The partner is derived from the product so the read-time scope filter can
    match a partner-scoped viewer without a join per query.
    """
    partner_id = None
    if product_id is not None:
        partner_id = await _fetchval(
            "SELECT partner_id FROM products WHERE id = $1", product_id
        )
    await _execute(
        "INSERT INTO admin_audit_log "
        "(actor_email, actor_role, method, path, action, product_id, partner_id, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        actor_email, actor_role, method, path, action, product_id, partner_id, status,
    )


async def list_audit(product_ids: Optional[list[int]], *, include_admins: bool,
                     include_global: bool, q: Optional[str] = None,
                     before_id: Optional[int] = None, limit: int = 100
                     ) -> list[dict[str, Any]]:
    """Scoped audit rows, newest first.

    `product_ids`: None = every product (global viewer); a list = only those
    products. `include_global`: whether hub-global actions (product_id IS NULL,
    e.g. user management / system settings) are visible — only a global viewer
    sees them. `include_admins`: False restricts to manager-authored rows (a
    manager viewer sees only manager actions); True shows all actors (an admin
    viewer). Product-scope rows are always gated by `product_ids`.
    """
    conds, args = [], []
    # Scope (WHERE) — which products' rows this viewer may see.
    if product_ids is None:
        scope = "TRUE" if include_global else "product_id IS NOT NULL"
    else:
        if not product_ids:
            scope = "product_id IS NULL AND FALSE"  # no product reach
        else:
            args.append(product_ids)
            scope = f"product_id = ANY(${len(args)})"
        if include_global:
            scope = f"({scope} OR product_id IS NULL)"
    conds.append(scope)
    # Role depth (WHOSE) — managers see only manager-authored actions.
    if not include_admins:
        conds.append("(actor_role IS NULL OR actor_role = 'manager')")
    if q:
        args.append(f"%{q}%")
        conds.append(f"(actor_email ILIKE ${len(args)} OR action ILIKE ${len(args)} "
                     f"OR path ILIKE ${len(args)})")
    if before_id:
        args.append(before_id)
        # Qualify with the alias: the query LEFT JOINs products (which also has an
        # `id`), so a bare `id` is an ambiguous column reference (42702) and 500s
        # every "load more" page.
        conds.append(f"a.id < ${len(args)}")
    args.append(max(1, min(limit, 500)))
    where_sql = " AND ".join(f"({c})" for c in conds)
    rows = await _fetch(
        f"SELECT a.id, a.actor_email, a.actor_role, a.method, a.path, a.action, "
        f"a.product_id, a.partner_id, a.status, a.created_at, "
        f"p.name AS product_name FROM admin_audit_log a "
        f"LEFT JOIN products p ON p.id = a.product_id "
        f"WHERE {where_sql} ORDER BY a.id DESC LIMIT ${len(args)}",
        *args,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
