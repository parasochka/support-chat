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
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OPS / AUDIT ------------------------------------------------------
-- product_id is denormalized onto both log tables (copied from the session /
-- the admin's selected scope) so per-product dashboards aggregate without a
-- chat_sessions join.
CREATE TABLE IF NOT EXISTS ai_interaction_logs (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  product_id  INT,
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

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_kb_entries_topic ON kb_entries(topic_id) WHERE active;
CREATE INDEX IF NOT EXISTS idx_kb_variables_updated ON kb_variables(updated_at);
CREATE INDEX IF NOT EXISTS idx_admin_events_session ON admin_events(session_id);
CREATE INDEX IF NOT EXISTS idx_admin_events_type ON admin_events(type, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_created ON chat_sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_logs_created ON ai_interaction_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_products_partner ON products(partner_id);
CREATE INDEX IF NOT EXISTS idx_admin_memberships_email ON admin_memberships(email);
"""
# NB: indexes over the product_id columns of PRE-TENANCY tables live in
# _ensure_columns — they must run AFTER the ADD COLUMN guards (_SCHEMA runs
# first and would fail on a legacy database that lacks the columns).


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
        "CREATE INDEX IF NOT EXISTS idx_chat_sessions_product "
        "ON chat_sessions(product_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ai_logs_product "
        "ON ai_interaction_logs(product_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_admin_events_product "
        "ON admin_events(product_id, created_at)",
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

    import tenancy

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
    await conn.execute(
        "INSERT INTO admin_memberships (email, scope_type, role) "
        "SELECT u.email, 'global', u.role FROM admin_users u "
        "WHERE NOT EXISTS "
        "  (SELECT 1 FROM admin_memberships m WHERE m.email = u.email)"
    )
    return product_id


async def init_db() -> None:
    """Create the pool, then create tables, run column guards + tenancy adoption."""
    await connect()
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_SCHEMA)
            await _ensure_columns(conn)
            default_product_id = await _migrate_tenancy(conn)
            await seed_kb_variables(conn, default_product_id)


# ---------------------------------------------------------------------------
# KB helpers (all product-scoped)
# ---------------------------------------------------------------------------
async def upsert_topic(product_id: int, slug: str, title: dict[str, str],
                       display_order: int, active: bool = True) -> int:
    row = await _pool.fetchrow(
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
    return row["id"]


async def get_topic_by_slug(product_id: int, slug: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, product_id, slug, title, display_order, active "
        "FROM kb_topics WHERE product_id = $1 AND slug = $2",
        product_id, slug,
    )
    return _row_to_topic(row) if row else None


async def get_topic_by_id(topic_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
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
    rows = await _pool.fetch(
        "SELECT id, product_id, slug, title, display_order, active FROM kb_topics "
        "WHERE product_id = $1 AND active "
        "ORDER BY (slug = 'other'), display_order, id",
        product_id,
    )
    return [_row_to_topic(r) for r in rows]


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
    target = conn or _pool
    await target.executemany(
        """
        INSERT INTO kb_variables (product_id, key, description, value)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (product_id, key) DO NOTHING
        """,
        [(product_id, k, d, v) for k, d, v in _DEFAULT_KB_VARIABLES],
    )


async def seed_starter_kb(product_id: int) -> None:
    """Seed a new product with the generic starter topics + KB texts.

    Gives a freshly created casino a working, brand-neutral knowledge base
    (see starter_kb.py) until the owner uniquifies the content. Inserts only
    topics the product does not have yet (ON CONFLICT DO NOTHING) and writes
    a KB entry only for a topic this call actually created — it can never
    overwrite an existing topic or KB text.
    """
    import starter_kb  # local import (starter_kb → prompts) to avoid a cycle

    for order, (slug, titles, content) in enumerate(starter_kb.STARTER_TOPICS,
                                                    start=1):
        row = await _pool.fetchrow(
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
        await _pool.execute(
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
    rows = await _pool.fetch(
        "SELECT key, description, value, updated_at, updated_by "
        "FROM kb_variables WHERE product_id = $1 ORDER BY key",
        product_id,
    )
    return [_row_to_kb_variable(r) for r in rows]


async def get_kb_variables_map(product_id: int) -> dict[str, str]:
    rows = await _pool.fetch(
        "SELECT key, value FROM kb_variables WHERE product_id = $1", product_id
    )
    return {r["key"]: r["value"] for r in rows}


async def set_kb_variable(product_id: int, key: str, description: str, value: str,
                          updated_by: Optional[str] = None) -> dict[str, Any]:
    row = await _pool.fetchrow(
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
    return _row_to_kb_variable(row)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
async def create_session(consumer: str, player_id: Optional[str],
                         lang: Optional[str], user_context: dict[str, Any],
                         session_id: Optional[str] = None,
                         product_id: Optional[int] = None) -> str:
    sid = session_id or str(uuid.uuid4())
    await _pool.execute(
        "INSERT INTO chat_sessions "
        "(id, consumer, product_id, player_id, lang, user_context) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb)",
        sid, consumer, product_id, player_id, lang,
        json.dumps(user_context or {}),
    )
    return sid


async def get_session(session_id: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, consumer, product_id, player_id, lang, conv_lang, topic_id, "
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
    product_id: Optional[int] = None,
) -> int:
    """Insert user + assistant rows, bump counters, write the AI log — atomically.

    Returns the new `message_count` for the session.
    When present, `ai_meta` carries: model, key_used, tokens_in, tokens_out,
    cached_in, cost_usd, latency_ms, ok, error. Model-free backend replies
    (for example the message-cap hand-off) still persist the visible chat turn
    but intentionally skip `ai_interaction_logs` because no API call happened.
    `product_id` (the session's product) is denormalized onto the AI log row so
    per-product cost dashboards aggregate without a join.
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
                    "(session_id, product_id, model, key_used, tokens_in, "
                    " tokens_out, cached_in, cost_usd, latency_ms, ok, error) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
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


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
async def log_ai_interaction(
    session_id: Optional[str], model: Optional[str], key_used: Optional[str],
    tokens_in: Optional[int], tokens_out: Optional[int], cached_in: Optional[int],
    cost_usd: Optional[float], latency_ms: Optional[int], ok: bool,
    error: Optional[str], product_id: Optional[int] = None,
) -> None:
    await _pool.execute(
        "INSERT INTO ai_interaction_logs "
        "(session_id, product_id, model, key_used, tokens_in, tokens_out, "
        " cached_in, cost_usd, latency_ms, ok, error) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        session_id, product_id, model, key_used, tokens_in, tokens_out,
        cached_in, cost_usd, latency_ms, ok, error,
    )


async def log_admin_event(session_id: Optional[str], type_: str,
                          payload: Optional[dict[str, Any]] = None,
                          product_id: Optional[int] = None) -> None:
    # When the caller doesn't pass the product explicitly, fall back to the
    # request's tenancy scope so per-product dashboards see admin actions too.
    if product_id is None:
        import tenancy  # local import: db must stay importable standalone
        product_id = tenancy.current_product_id()
    await _pool.execute(
        "INSERT INTO admin_events (session_id, product_id, type, payload) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        session_id, product_id, type_, json.dumps(payload or {}),
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
# product_settings (per-product overrides; resolution product > global > env)
# ---------------------------------------------------------------------------
async def get_all_product_settings() -> dict[int, dict[str, Any]]:
    """Every product's overrides keyed by product_id (for the settings cache)."""
    rows = await _pool.fetch("SELECT product_id, key, value FROM product_settings")
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out.setdefault(r["product_id"], {})[r["key"]] = _json_value(r["value"])
    return out


async def get_product_settings(product_id: int) -> dict[str, Any]:
    rows = await _pool.fetch(
        "SELECT key, value FROM product_settings WHERE product_id = $1", product_id
    )
    return {r["key"]: _json_value(r["value"]) for r in rows}


async def set_product_setting(product_id: int, key: str, value: Any,
                              updated_by: Optional[str] = None) -> None:
    await _pool.execute(
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
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


_PRODUCT_COLS = ("id, partner_id, slug, name, widget_key, active, "
                 "openai_key_primary_enc, openai_key_fallback_enc, "
                 "handshake_secret_enc, created_at, updated_at")


async def list_partners() -> list[dict[str, Any]]:
    rows = await _pool.fetch(
        "SELECT id, slug, name, active, created_at, updated_at "
        "FROM partners ORDER BY id"
    )
    return [_row_to_partner(r) for r in rows]


async def get_partner(partner_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "SELECT id, slug, name, active, created_at, updated_at "
        "FROM partners WHERE id = $1", partner_id
    )
    return _row_to_partner(row) if row else None


async def create_partner(slug: str, name: str) -> Optional[dict[str, Any]]:
    """Insert a partner; returns None when the slug is already taken."""
    row = await _pool.fetchrow(
        "INSERT INTO partners (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO NOTHING "
        "RETURNING id, slug, name, active, created_at, updated_at",
        slug, name,
    )
    return _row_to_partner(row) if row else None


async def update_partner(partner_id: int, *, name: Optional[str] = None,
                         active: Optional[bool] = None) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "UPDATE partners SET name = COALESCE($2, name), "
        "active = COALESCE($3, active), updated_at = now() WHERE id = $1 "
        "RETURNING id, slug, name, active, created_at, updated_at",
        partner_id, name, active,
    )
    return _row_to_partner(row) if row else None


async def list_products(partner_id: Optional[int] = None,
                        product_ids: Optional[list[int]] = None
                        ) -> list[dict[str, Any]]:
    where, args = [], []
    if partner_id is not None:
        args.append(partner_id)
        where.append(f"partner_id = ${len(args)}")
    if product_ids is not None:
        args.append(product_ids)
        where.append(f"id = ANY(${len(args)}::int[])")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = await _pool.fetch(
        f"SELECT {_PRODUCT_COLS} FROM products {where_sql} ORDER BY id", *args
    )
    return [_row_to_product(r) for r in rows]


async def get_product(product_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE id = $1", product_id
    )
    return _row_to_product(row) if row else None


async def get_product_by_widget_key(widget_key: str) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE widget_key = $1", widget_key
    )
    return _row_to_product(row) if row else None


async def get_default_product() -> Optional[dict[str, Any]]:
    """The boot-seeded product a key-less widget lands on (see tenancy.py)."""
    import tenancy
    row = await _pool.fetchrow(
        f"SELECT {_PRODUCT_COLS} FROM products WHERE slug = $1",
        tenancy.DEFAULT_PRODUCT_SLUG,
    )
    return _row_to_product(row) if row else None


def _new_widget_key() -> str:
    import secrets as _secrets
    return "wk_" + _secrets.token_urlsafe(24)


async def create_product(partner_id: int, slug: str, name: str
                         ) -> Optional[dict[str, Any]]:
    """Insert a product and seed its brand-neutral baseline.

    A new casino starts working out of the box: widget key generated, the KB
    variables registry, the generic starter topics + KB texts (starter_kb.py)
    and the full prompt-variables set (template defaults, brand_name = the
    product's name) are all seeded into the PRODUCT layer — so nothing is
    inherited from another brand's global overrides. The owner then
    translates/uniquifies everything from the admin panel.

    Returns None when the slug is already taken.
    """
    import starter_kb  # local import (starter_kb → prompts) to avoid a cycle

    row = await _pool.fetchrow(
        "INSERT INTO products (partner_id, slug, name, widget_key) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT (slug) DO NOTHING "
        f"RETURNING {_PRODUCT_COLS}",
        partner_id, slug, name, _new_widget_key(),
    )
    if row is None:
        return None
    await seed_kb_variables(product_id=row["id"])
    await seed_starter_kb(row["id"])
    await set_product_setting(row["id"], "prompt_variables",
                              starter_kb.starter_prompt_variables(name),
                              updated_by="starter-seed")
    return _row_to_product(row)


async def update_product(product_id: int, *, name: Optional[str] = None,
                         active: Optional[bool] = None) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        "UPDATE products SET name = COALESCE($2, name), "
        "active = COALESCE($3, active), updated_at = now() WHERE id = $1 "
        f"RETURNING {_PRODUCT_COLS}",
        product_id, name, active,
    )
    return _row_to_product(row) if row else None


async def rotate_widget_key(product_id: int) -> Optional[str]:
    """Mint a fresh widget key (old embeds stop resolving immediately)."""
    return await _pool.fetchval(
        "UPDATE products SET widget_key = $2, updated_at = now() "
        "WHERE id = $1 RETURNING widget_key",
        product_id, _new_widget_key(),
    )


# Sentinel distinguishing "leave this secret unchanged" from "clear it" ("").
UNSET: Any = object()


async def set_product_secrets(product_id: int, *,
                              openai_key_primary: Any = UNSET,
                              openai_key_fallback: Any = UNSET,
                              handshake_secret: Any = UNSET) -> bool:
    """Write per-product secrets (encrypted at rest). Empty string clears one.

    Values are encrypted with secretbox before they touch the table; the
    plaintext is never stored or logged. Returns False for an unknown product.
    """
    import secretbox
    sets: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    for col, val in (("openai_key_primary_enc", openai_key_primary),
                     ("openai_key_fallback_enc", openai_key_fallback),
                     ("handshake_secret_enc", handshake_secret)):
        if val is UNSET:
            continue
        args.append(secretbox.encrypt(val.strip()) if isinstance(val, str)
                    and val.strip() else None)
        sets.append(f"{col} = ${len(args)}")
    args.append(product_id)
    row = await _pool.fetchrow(
        f"UPDATE products SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING id",
        *args,
    )
    return row is not None


async def get_product_openai_keys(product_id: int) -> Optional[dict[str, Optional[str]]]:
    """Decrypted per-product OpenAI keys, or None when the product has none.

    A decryption failure (e.g. SECRETS_MASTER_KEY was rotated) is logged and
    treated as "no product keys" so the chat degrades to the env fallback
    instead of hard-failing every turn.
    """
    import logging

    import secretbox
    row = await _pool.fetchrow(
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

    import secretbox
    enc = await _pool.fetchval(
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
    rows = await _pool.fetch(
        _MEMBERSHIP_SELECT + "WHERE m.email = $1 ORDER BY m.id",
        email.strip().lower(),
    )
    return [_row_to_membership(r) for r in rows]


async def list_all_memberships() -> list[dict[str, Any]]:
    rows = await _pool.fetch(_MEMBERSHIP_SELECT + "ORDER BY m.email, m.id")
    return [_row_to_membership(r) for r in rows]


async def add_membership(email: str, scope_type: str,
                         partner_id: Optional[int], product_id: Optional[int],
                         role: str) -> dict[str, Any]:
    """Upsert one (user, scope) membership — a repeat write updates the role."""
    email = email.strip().lower()
    existing = await _pool.fetchval(
        "SELECT id FROM admin_memberships WHERE email = $1 AND scope_type = $2 "
        "AND COALESCE(partner_id, 0) = COALESCE($3, 0) "
        "AND COALESCE(product_id, 0) = COALESCE($4, 0)",
        email, scope_type, partner_id, product_id,
    )
    if existing is not None:
        await _pool.execute(
            "UPDATE admin_memberships SET role = $2 WHERE id = $1", existing, role
        )
        mid = existing
    else:
        mid = await _pool.fetchval(
            "INSERT INTO admin_memberships (email, scope_type, partner_id, product_id, role) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            email, scope_type, partner_id, product_id, role,
        )
    row = await _pool.fetchrow(_MEMBERSHIP_SELECT + "WHERE m.id = $1", mid)
    return _row_to_membership(row)


async def get_membership(membership_id: int) -> Optional[dict[str, Any]]:
    row = await _pool.fetchrow(
        _MEMBERSHIP_SELECT + "WHERE m.id = $1", membership_id
    )
    return _row_to_membership(row) if row else None


async def delete_membership(membership_id: int) -> bool:
    res = await _pool.execute(
        "DELETE FROM admin_memberships WHERE id = $1", membership_id
    )
    return res.upper().startswith("DELETE") and not res.endswith(" 0")


async def product_ids_for_partners(partner_ids: list[int]) -> list[int]:
    """All product ids under the given partners (for scope expansion)."""
    if not partner_ids:
        return []
    rows = await _pool.fetch(
        "SELECT id FROM products WHERE partner_id = ANY($1::int[])", partner_ids
    )
    return [r["id"] for r in rows]


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
async def list_topics_with_counts(product_id: int) -> list[dict[str, Any]]:
    rows = await _pool.fetch(
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


def _product_clause(product_ids: Optional[list[int]], args: list[Any],
                    col: str = "product_id") -> str:
    """Append an `AND <col> = ANY($n)` filter when a product scope is set.

    `None` means no scope (global view — all products); an empty list means an
    admin with no accessible products, which must match nothing.
    """
    if product_ids is None:
        return ""
    args.append(product_ids)
    return f" AND {col} = ANY(${len(args)}::int[])"


async def overview_aggregates(dt_from: Any, dt_to: Any,
                              product_ids: Optional[list[int]] = None
                              ) -> dict[str, Any]:
    """Raw aggregate counters for the dashboard overview within [from, to)."""
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args)
    sess = await _pool.fetchrow(
        "SELECT "
        "  COUNT(*) AS sessions_total, "
        "  COUNT(*) FILTER (WHERE message_count > 0) AS sessions_engaged, "
        "  COUNT(*) FILTER (WHERE status = 'open' AND message_count > 0) AS sessions_open, "
        "  COUNT(*) FILTER (WHERE escalated) AS sessions_escalated, "
        "  COALESCE(AVG(message_count) FILTER (WHERE message_count > 0), 0) "
        "    AS avg_messages_per_session "
        f"FROM chat_sessions WHERE created_at >= $1 AND created_at < $2{scope}",
        *args,
    )
    cost = await _pool.fetchrow(
        "SELECT "
        "  COALESCE(SUM(cost_usd), 0) AS cost_usd_total, "
        "  COALESCE(SUM(cached_in), 0) AS cached_in_total, "
        "  COALESCE(SUM(tokens_in), 0) AS tokens_in_total, "
        "  COUNT(DISTINCT session_id) AS sessions_with_ai, "
        "  COUNT(*) FILTER (WHERE NOT ok) AS failed_calls "
        f"FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2{scope}",
        *args,
    )
    ev = await _pool.fetch(
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
        "events": events,
    }


async def timeseries(metric: str, dt_from: Any, dt_to: Any,
                     bucket: str = "day",
                     product_ids: Optional[list[int]] = None
                     ) -> list[dict[str, Any]]:
    """Per-bucket series for sessions | cost | cost_per_session | escalation_rate."""
    trunc = "day" if bucket not in ("hour", "day", "week", "month") else bucket
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args)
    if metric == "cost":
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COALESCE(SUM(cost_usd), 0) AS value "
            f"FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
    elif metric == "cost_per_session":
        # Average spend per session per bucket: total cost / distinct sessions that
        # had at least one OpenAI call in the bucket. The "average price per day".
        rows = await _pool.fetch(
            f"SELECT date_trunc('{trunc}', created_at) AS bucket, "
            "COALESCE(SUM(cost_usd), 0) AS cost, "
            "COUNT(DISTINCT session_id) AS sessions "
            f"FROM ai_interaction_logs WHERE created_at >= $1 AND created_at < $2{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
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
            f"FROM chat_sessions WHERE created_at >= $1 AND created_at < $2{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
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
            f"FROM chat_sessions WHERE created_at >= $1 AND created_at < $2{scope} "
            "GROUP BY bucket ORDER BY bucket",
            *args,
        )
    return [{"bucket": r["bucket"].isoformat(), "value": float(r["value"])}
            for r in rows]


async def by_topic(dt_from: Any, dt_to: Any,
                   product_ids: Optional[list[int]] = None) -> list[dict[str, Any]]:
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args, col="s.product_id")
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
        f"WHERE s.created_at >= $1 AND s.created_at < $2{scope} "
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


async def by_language(dt_from: Any, dt_to: Any,
                      product_ids: Optional[list[int]] = None
                      ) -> list[dict[str, Any]]:
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args, col="s.product_id")
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
        f"WHERE s.created_at >= $1 AND s.created_at < $2{scope} "
        "GROUP BY COALESCE(s.lang, 'unknown') ORDER BY sessions DESC",
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
                        page: int = 1, page_size: int = 25) -> dict[str, Any]:
    where = ["s.created_at >= $1", "s.created_at < $2"]
    args: list[Any] = [dt_from, dt_to]
    if product_ids is not None:
        args.append(product_ids); where.append(f"s.product_id = ANY(${len(args)}::int[])")
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
        f"  s.product_id, p.name AS product_name, "
        f"  COALESCE(c.cost_usd_total, 0) AS cost_usd_total "
        f"FROM chat_sessions s LEFT JOIN kb_topics t ON t.id = s.topic_id "
        f"LEFT JOIN products p ON p.id = s.product_id "
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


async def unresolved_by_topic(dt_from: Any, dt_to: Any,
                              product_ids: Optional[list[int]] = None
                              ) -> list[dict[str, Any]]:
    """Open or escalated engaged sessions grouped by topic.

    The admin page is an operational queue for conversations that still need KB
    or human attention, so it includes both escalated sessions and abandoned open
    chats with at least one user turn. Resolved sessions are excluded.
    """
    args: list[Any] = [dt_from, dt_to]
    scope = _product_clause(product_ids, args, col="s.product_id")
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
        f"  AND s.created_at >= $1 AND s.created_at < $2{scope} "
        "ORDER BY topic, s.created_at DESC",
        *args,
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
