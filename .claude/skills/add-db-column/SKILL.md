---
name: add-db-column
description: >-
  Add a column to an existing table in the no-ORM, no-migrations db.py schema
  correctly, so it applies on already-deployed databases. Use whenever you need
  to store a new field on a table (chat_sessions, products, retention_users,
  etc.). Covers the guarded-ALTER gotcha and the atomic-turn / product_id
  invariants that break silently otherwise.
---

# add-db-column

The schema **is** `db.init_db()` — no ORM, no migration tool. `CREATE TABLE IF
NOT EXISTS` does **not** add a column to a table that already exists on a live
DB, so a new column added only to the `_SCHEMA` string silently never appears in
production. This is the #1 db.py footgun.

## 1. `db.py` `_SCHEMA` — the column for fresh databases

Add the column to the table's `CREATE TABLE` in the `_SCHEMA` string (so a brand
new database gets it).

## 2. `db.py` `_ensure_columns()` — the guarded ALTER for existing databases

**Required**, not optional. Add an idempotent statement (runs on every boot):

```python
await conn.execute(
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS "
    "my_col TEXT"                      # include DEFAULT / NOT NULL as needed
)
```
Indexes on the new column also go here (`CREATE INDEX IF NOT EXISTS …`), after
the column guards.

## 3. `db.py` helper — read/write through a `db.<name>()` async function

Nothing outside `db.py` touches tables directly. Add or extend the async helper
that reads/writes the column, and map rows through the existing `_row_to_*`
converter if the table has one. **Serialize non-JSON types**: a raw `datetime`
cannot be returned by `JSONResponse` — convert to an isoformat string in the
converter (the bug that shipped the KB-variables tab).

## 4. Honour the invariants (these break silently)

- **Per-turn data → the atomic write.** If the column is written per message,
  add it to the single `db.persist_turn` transaction (messages + counters + AI
  log). Do not split the transaction.
- **Per-turn / per-session rows carry `product_id`.** Copy it from the session,
  like `ai_interaction_logs` does, or per-product dashboards go blank.
- **Retention parity.** If the table feeds Telegram, remember support dashboards
  exclude `consumer='telegram'`; wire the retention counterpart if needed.

## 5. Tests + verify

Because there's no real DB in tests (`asyncpg` is stubbed), assert the helper's
row-mapping / serialization logic in a unit test rather than a live query. Then
`bash scripts/preflight.sh --checks`.
