"""Boot migration: the legacy hidden `general.contact_form_url` app_settings
value moves into the default product's Translations (`en.contact_url`) — the one
admin-visible home for the contact-button URL — and the legacy key is deleted.
"""
from __future__ import annotations

import json

import db

PID = 7  # the default product id used in these tests


class FakeConn:
    """Just enough of an asyncpg connection for _migrate_legacy_contact_url."""

    def __init__(self, app_settings=None, product_settings=None):
        # key -> value (already-decoded JSON)
        self.app = dict(app_settings or {})
        # (product_id, key) -> value
        self.prod = dict(product_settings or {})

    async def fetchrow(self, q, *args):
        if "FROM app_settings" in q:
            key = "general" if "'general'" in q else "translations"
            v = self.app.get(key)
            return None if v is None else {"value": v}
        if "FROM product_settings" in q:
            v = self.prod.get((args[0], "translations"))
            return None if v is None else {"value": v}
        raise AssertionError(f"unexpected query: {q}")

    async def execute(self, q, *args):
        if "INSERT INTO product_settings" in q:
            self.prod[(args[0], "translations")] = json.loads(args[1])
        elif "UPDATE app_settings" in q:
            self.app["general"] = json.loads(args[0])
        else:
            raise AssertionError(f"unexpected query: {q}")


async def test_legacy_url_moves_into_default_product_translations():
    conn = FakeConn(app_settings={
        "general": {"contact_form_url": "https://x/about/contact",
                    "session_ttl_hours": 24},
    })
    await db._migrate_legacy_contact_url(conn, PID)
    # Moved into the default product's translations, English slot (the end of
    # the per-language resolution chain, so every language still reaches it).
    assert conn.prod[(PID, "translations")]["en"]["contact_url"] == \
        "https://x/about/contact"
    # The legacy key is gone; sibling knobs survive.
    assert "contact_form_url" not in conn.app["general"]
    assert conn.app["general"]["session_ttl_hours"] == 24


async def test_migration_is_one_time():
    conn = FakeConn(app_settings={
        "general": {"contact_form_url": "https://x/c"}})
    await db._migrate_legacy_contact_url(conn, PID)
    moved = json.dumps(conn.prod[(PID, "translations")], sort_keys=True)
    # Second boot: no legacy key left -> nothing changes (even if the admin
    # cleared the migrated contact_url in the meantime, it never comes back).
    await db._migrate_legacy_contact_url(conn, PID)
    assert json.dumps(conn.prod[(PID, "translations")], sort_keys=True) == moved


async def test_existing_contact_url_wins_legacy_key_just_dropped():
    # The owner already set a contact_url (product or global scope) -> the
    # legacy value is dead weight: deleted, NOT copied over the owner's value.
    conn = FakeConn(
        app_settings={"general": {"contact_form_url": "https://old/legacy"}},
        product_settings={(PID, "translations"):
                          {"ru": {"contact_url": "https://new/admin"}}},
    )
    await db._migrate_legacy_contact_url(conn, PID)
    assert conn.prod[(PID, "translations")] == {
        "ru": {"contact_url": "https://new/admin"}}
    assert "contact_form_url" not in conn.app["general"]


async def test_noop_without_general_row_or_key():
    conn = FakeConn()
    await db._migrate_legacy_contact_url(conn, PID)  # no general row at all
    assert conn.prod == {} and conn.app == {}

    conn = FakeConn(app_settings={"general": {"session_ttl_hours": 24}})
    await db._migrate_legacy_contact_url(conn, PID)  # row without the key
    assert conn.prod == {}
    assert conn.app["general"] == {"session_ttl_hours": 24}


async def test_empty_legacy_value_is_dropped_without_copying():
    conn = FakeConn(app_settings={"general": {"contact_form_url": ""}})
    await db._migrate_legacy_contact_url(conn, PID)
    assert conn.prod == {}
    assert "contact_form_url" not in conn.app["general"]
