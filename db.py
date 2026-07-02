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
from collections import deque as _deque
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

CREATE TABLE IF NOT EXISTS kb_variables (
  key         TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by  TEXT
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

-- Runtime-tunable settings (hot-reloaded; precedence app_settings > env > default).
CREATE TABLE IF NOT EXISTS app_settings (
  key        TEXT PRIMARY KEY,
  value      JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_kb_variables_updated ON kb_variables(updated_at);
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
            await seed_kb_variables(conn)


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




# Default KB variables. Source: NikaBet knowledge-base variables registry; the
# "Test value (TEST)" column is used until owners confirm final values.
_DEFAULT_KB_VARIABLES: tuple[tuple[str, str, str], ...] = (
    ("deposit_methods", "Deposit methods (per market)", "USDT (crypto: TRC20/ERC20/BEP20), Visa/Mastercard, local LATAM methods"),
    ("crypto_networks", "Supported crypto networks", "TRC20, ERC20, BEP20"),
    ("card_deposit", "Card availability by market", "Visa, Mastercard (availability depends on market)"),
    ("local_methods_latam", "Local LATAM payment systems", "Rapipago, Pago Facil, Mercado Pago, PIX (by country) (test)"),
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
    ("withdrawal_taxes", "Withdrawal taxation", "per local jurisdiction (LATAM); not withheld by NikaBet"),
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
    ("welcome_bonus", "Welcome bonus value", "100% Match up to 200 USDT + 50 FS (test, pending unification with live 5000 USDT+80FS)"),
    ("welcome_min_deposit", "Minimum deposit for Welcome bonus", "10 USDT"),
    ("game_weighting", "Game weighting for wagering (table)", "slots 100%, live and table 10%, blackjack and baccarat 0%"),
    ("promo_code_field", "Promo code input field", "Cashier/Deposit page -> 'Promo code' field"),
    ("level_rewards_map", "Level rewards map", "per-level rewards (FS, bonus cash, perks) defined in the Loyalty Engine reward map"),
    ("vip_thresholds", "VIP class thresholds", "Player -> Bronze -> Silver -> Gold -> Platinum -> VIP by accumulated XP (50 levels / 6 classes)"),
    ("multi_bonus_policy", "Multiple-bonus policy", "one deposit bonus active at a time; Welcome has top priority"),
    ("daily_card_super_prize", "Bonus card super prize", "Reload 20% on next deposit"),
    ("license_info", "License and regulator", "Curacao eGaming license (test)"),
    ("restricted_countries", "Restricted countries", "US, UK, FR, NL, AU (test list)"),
    ("locales", "Site languages", "es-AR (primary), es-419, pt-BR, es-CL, en"),
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
    ("privacy_policy", "Privacy policy URL", "https://nikabet.com/privacy (test URL)"),
    ("terms_url", "Terms and Conditions URL", "https://nikabet.com/terms (test URL)"),
    ("vip_withdrawal_limits", "Withdrawal limits by status/VIP", "higher limits for VIP (e.g. daily 20000 USDT) (test)"),
    ("social_links", "Official social links and channels", "Telegram: t.me/nikabet (test)"),
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


async def seed_kb_variables(conn: Optional[asyncpg.Connection] = None) -> None:
    """Insert default variables without overwriting admin-edited values."""
    target = conn or _pool
    await target.executemany(
        """
        INSERT INTO kb_variables (key, description, value) VALUES ($1, $2, $3)
        ON CONFLICT (key) DO NOTHING
        """,
        _DEFAULT_KB_VARIABLES,
    )


def _row_to_kb_variable(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    # `updated_at` is a datetime; JSONResponse (Starlette json.dumps) cannot
    # serialize it, so render it as an ISO string like every other admin payload.
    updated_at = d.get("updated_at")
    d["updated_at"] = updated_at.isoformat() if updated_at is not None else None
    return d


async def list_kb_variables() -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT key, description, value, updated_at, updated_by FROM kb_variables ORDER BY key"
    )
    return [_row_to_kb_variable(r) for r in rows]


async def get_kb_variables_map() -> dict[str, str]:
    rows = await _pool.fetch("SELECT key, value FROM kb_variables")
    return {r["key"]: r["value"] for r in rows}


async def set_kb_variable(key: str, description: str, value: str, updated_by: Optional[str] = None) -> dict[str, Any]:
    row = await _pool.fetchrow(
        """
        INSERT INTO kb_variables (key, description, value, updated_at, updated_by)
        VALUES ($1, $2, $3, now(), $4)
        ON CONFLICT (key) DO UPDATE
          SET description = EXCLUDED.description,
              value = EXCLUDED.value,
              updated_at = now(),
              updated_by = EXCLUDED.updated_by
        RETURNING key, description, value, updated_at, updated_by
        """,
        key, description, value, updated_by,
    )
    return _row_to_kb_variable(row)


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


async def mark_escalated_soft(session_id: str) -> None:
    """Flag a SOFT (keyword-triggered) escalation without closing the session.

    Sets only `escalated = TRUE` so the metrics and the Unresolved queue see the
    hand-off, but `status` stays 'open' and the player can keep chatting — a
    fuzzy keyword false positive must never kill a live conversation. A later
    HARD escalation (model [[ESCALATE]], cap, explicit) still closes it via
    mark_escalated.
    """
    await _pool.execute(
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
    ai_meta: Optional[dict[str, Any]] = None,
) -> int:
    """Insert user + assistant rows, bump counters, write the AI log — atomically.

    Returns the new `message_count` for the session.
    When present, `ai_meta` carries: model, key_used, tokens_in, tokens_out,
    cached_in, cost_usd, latency_ms, ok, error. Model-free backend replies
    (for example the message-cap hand-off) still persist the visible chat turn
    but intentionally skip `ai_interaction_logs` because no API call happened.
    """
    ai_meta = ai_meta or {}
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
            if ai_meta:
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


# High-volume event types (rate-limit blocks, injection blocks, low-content
# nudges, dev recaptcha skips) fire once per REJECTED request, so an attacker
# hammering a 429'd endpoint would otherwise grow admin_events without bound —
# the rate limiter rejects the request but never stopped the logging. The
# sampled writer keeps a small in-memory budget per event type and silently
# drops the excess: the dashboard still sees that the blocking happened (the
# first N events per window), the table stays bounded. In-memory like the rate
# limiter itself — per instance, reset on restart, which is fine for sampling.
_EVENT_SAMPLE_WINDOW_SEC = 300.0
_EVENT_SAMPLE_MAX_PER_WINDOW = 20
_event_sample_hits: dict[str, "_deque[float]"] = {}


async def log_admin_event_sampled(session_id: Optional[str], type_: str,
                                  payload: Optional[dict[str, Any]] = None) -> None:
    """log_admin_event with a per-type budget; excess events are dropped."""
    import time as _time
    now = _time.monotonic()
    hits = _event_sample_hits.setdefault(type_, _deque())
    while hits and now - hits[0] > _EVENT_SAMPLE_WINDOW_SEC:
        hits.popleft()
    if len(hits) >= _EVENT_SAMPLE_MAX_PER_WINDOW:
        return
    hits.append(now)
    await log_admin_event(session_id, type_, payload)


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
# admin_users (named login accounts; password hash never leaves this module)
# ---------------------------------------------------------------------------
def _row_to_admin_user(row: asyncpg.Record, *, include_hash: bool = False) -> dict[str, Any]:
    """Serialize a user row for the API — the password hash is dropped by default."""
    d = dict(row)
    if not include_hash:
        d.pop("password_hash", None)
    for ts in ("created_at", "updated_at"):
        if d.get(ts) is not None and not isinstance(d[ts], str):
            d[ts] = d[ts].isoformat()
    return d


async def get_admin_user(email: str) -> Optional[dict[str, Any]]:
    """Fetch a user INCLUDING the password hash (login path only)."""
    row = await _pool.fetchrow(
        "SELECT email, password_hash, role, active, created_at, updated_at "
        "FROM admin_users WHERE email = $1",
        email.strip().lower(),
    )
    return _row_to_admin_user(row, include_hash=True) if row else None


async def list_admin_users() -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT email, role, active, created_at, updated_at "
        "FROM admin_users ORDER BY email"
    )
    return [_row_to_admin_user(r) for r in rows]


async def create_admin_user(email: str, password_hash: str, role: str) -> dict[str, Any]:
    row = await _pool.fetchrow(
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
    row = await _pool.fetchrow(
        f"UPDATE admin_users SET {', '.join(sets)} WHERE email = ${len(args)} "
        f"RETURNING email, role, active, created_at, updated_at",
        *args,
    )
    return _row_to_admin_user(row) if row else None


async def delete_admin_user(email: str) -> bool:
    res = await _pool.execute(
        "DELETE FROM admin_users WHERE email = $1", email.strip().lower()
    )
    return res.upper().startswith("DELETE") and not res.endswith(" 0")


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
        "  COUNT(DISTINCT session_id) AS sessions_with_ai, "
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
        # Sessions that actually made >= 1 OpenAI call (distinct session_id in
        # ai_interaction_logs). This is the precise denominator for average cost:
        # greeting-only "zero" sessions (chat opened, canned greeting shown, no API
        # call) never appear here, so they don't dilute cost-per-session.
        "sessions_with_ai": int(cost["sessions_with_ai"]),
        "events": events,
    }


async def timeseries(metric: str, dt_from: Any, dt_to: Any,
                     bucket: str = "day") -> list[dict[str, Any]]:
    """Per-bucket series for sessions | cost | cost_per_session | escalation_rate."""
    trunc = "day" if bucket not in ("hour", "day", "week", "month") else bucket
    if metric == "cost":
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COALESCE(SUM(cost_usd), 0) AS value "
            "FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2 "
            "GROUP BY bucket ORDER BY bucket",
            dt_from, dt_to,
        )
    elif metric == "cost_per_session":
        # Average spend per session per bucket: total cost / distinct sessions that
        # had at least one OpenAI call in the bucket. The "average price per day".
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COALESCE(SUM(cost_usd), 0) AS cost, "
            "COUNT(DISTINCT session_id) AS sessions "
            "FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2 "
            "GROUP BY bucket ORDER BY bucket",
            dt_from, dt_to,
        )
        return [
            {"bucket": r["bucket"].isoformat(),
             "value": (float(r["cost"]) / r["sessions"]) if r["sessions"] else 0.0}
            for r in rows
        ]
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
        "WITH costs AS ("
        "  SELECT session_id, SUM(cost_usd) AS cost_usd_total "
        "  FROM ai_interaction_logs GROUP BY session_id"
        ") "
        "SELECT t.slug, t.title, "
        # Count only engaged sessions (>= 1 message): greeting-only "zero" sessions
        # had no OpenAI call and must not dilute the per-topic counts or rates.
        "  COUNT(s.id) FILTER (WHERE s.message_count > 0) AS sessions, "
        "  COUNT(s.id) FILTER (WHERE s.escalated AND s.message_count > 0) AS escalated, "
        "  COALESCE(AVG(s.message_count) FILTER (WHERE s.message_count > 0), 0) AS avg_messages, "
        "  COALESCE(SUM(costs.cost_usd_total), 0) AS cost_usd_total "
        "FROM chat_sessions s JOIN kb_topics t ON t.id = s.topic_id "
        "LEFT JOIN costs ON costs.session_id = s.id "
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
            "cost_usd_total": round(float(r["cost_usd_total"]), 6),
        })
    return out


async def by_language(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "WITH costs AS ("
        "  SELECT session_id, SUM(cost_usd) AS cost_usd_total "
        "  FROM ai_interaction_logs GROUP BY session_id"
        ") "
        "SELECT COALESCE(s.lang, 'unknown') AS lang, "
        # Engaged sessions only — exclude greeting-only "zero" sessions (no OpenAI
        # call) so the per-language counts and escalation rates aren't diluted.
        "  COUNT(*) FILTER (WHERE s.message_count > 0) AS sessions, "
        "  COUNT(*) FILTER (WHERE s.escalated AND s.message_count > 0) AS escalated, "
        "  COALESCE(SUM(costs.cost_usd_total), 0) AS cost_usd_total "
        "FROM chat_sessions s LEFT JOIN costs ON costs.session_id = s.id "
        "WHERE s.created_at >= $1 AND s.created_at < $2 "
        "GROUP BY COALESCE(s.lang, 'unknown') ORDER BY sessions DESC",
        dt_from, dt_to,
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
    if min_messages is not None:
        args.append(min_messages); where.append(f"s.message_count >= ${len(args)}")
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
        f"  s.created_at, s.updated_at, t.slug AS topic, "
        f"  COALESCE(c.cost_usd_total, 0) AS cost_usd_total "
        f"FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        f"LEFT JOIN (SELECT session_id, SUM(cost_usd) AS cost_usd_total "
        f"           FROM ai_interaction_logs GROUP BY session_id) c "
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
    # Topic-switch markers: a cross-topic routing turn suppresses its (ungrounded)
    # answer and persists no chat_messages row, so its detect-call cost would look
    # orphaned in the transcript. Returning these lets the admin view interleave a
    # "switched X -> Y" marker (with that call's cost) into the timeline, so the
    # path is traceable and the per-step costs add up to cost_usd_total.
    events = await _pool.fetch(
        "SELECT type, payload, created_at FROM admin_events "
        "WHERE session_id = $1 AND type = 'topic_switch' ORDER BY id ASC",
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
        "cost_usd_total": round(cost_total, 6),
    }


async def unresolved_by_topic(dt_from: Any, dt_to: Any) -> list[dict[str, Any]]:
    """Open or escalated engaged sessions grouped by topic.

    The admin page is an operational queue for conversations that still need KB
    or human attention, so it includes both escalated sessions and abandoned open
    chats with at least one user turn. Resolved sessions are excluded.
    """
    rows = await _pool.fetch(
        "WITH costs AS ("
        "  SELECT session_id, SUM(cost_usd) AS cost_usd_total "
        "  FROM ai_interaction_logs GROUP BY session_id"
        ") "
        "SELECT COALESCE(t.slug, 'unknown') AS topic, "
        "  COALESCE(t.title, '{}'::jsonb) AS title, "
        "  s.id AS session_id, s.lang, s.status, s.escalated, s.message_count, "
        "  s.created_at, s.updated_at, "
        "  COALESCE(costs.cost_usd_total, 0) AS cost_usd_total, "
        "  (SELECT m.content FROM chat_messages m "
        "    WHERE m.session_id = s.id AND m.role = 'user' "
        "    ORDER BY m.id ASC LIMIT 1) AS first_message "
        "FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        "LEFT JOIN costs ON costs.session_id = s.id "
        "WHERE s.message_count > 0 "
        "  AND (s.escalated OR s.status = 'open') "
        "  AND s.created_at >= $1 AND s.created_at < $2 "
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
            "lang": r["lang"],
            "status": r["status"],
            "escalated": r["escalated"],
            "message_count": r["message_count"],
            "first_message": r["first_message"],
            "cost_usd_total": round(float(r["cost_usd_total"] or 0), 6),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        })
    return sorted(groups.values(), key=lambda x: x["count"], reverse=True)
