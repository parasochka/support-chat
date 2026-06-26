"""Users tab create/update path: the handler must hash the password itself.

Regression: `api/admin.py` called `auth.hash_password(...)` without importing
`auth`, so every "Create user" (and password update) raised NameError → an
unhandled 500 in the Users tab. These tests exercise the handlers with the DB
helpers stubbed, so a missing import (or any pre-DB crash) fails the suite
instead of only surfacing as a 500 in production.
"""
from __future__ import annotations

import auth
import db
from api import admin as admin_api
from api.admin import UserCreate, UserUpdate


def _stub_db(monkeypatch):
    created: dict = {}

    async def get_admin_user(email):
        return created.get(email)

    async def create_admin_user(email, password_hash, role):
        # Store the hash so the test can assert the handler hashed (never stored
        # plaintext) the password before it reached the DB layer.
        row = {"email": email, "password_hash": password_hash, "role": role,
               "active": True, "created_at": None, "updated_at": None}
        created[email] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def update_admin_user(email, *, role=None, active=None, password_hash=None):
        row = created[email]
        if password_hash is not None:
            row["password_hash"] = password_hash
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def log_admin_event(*a, **k):
        return None

    monkeypatch.setattr(db, "get_admin_user", get_admin_user)
    monkeypatch.setattr(db, "create_admin_user", create_admin_user)
    monkeypatch.setattr(db, "update_admin_user", update_admin_user)
    monkeypatch.setattr(db, "log_admin_event", log_admin_event)
    return created


async def test_create_user_hashes_password(monkeypatch):
    created = _stub_db(monkeypatch)
    admin = {"role": "owner", "email": None}
    res = await admin_api.create_user(
        UserCreate(email="artem@nowplix.com", password="G#EwiXCcvgo4", role="admin"),
        admin=admin)
    assert res.status_code == 200
    stored = created["artem@nowplix.com"]["password_hash"]
    # The handler must hash, never store the plaintext.
    assert stored != "G#EwiXCcvgo4"
    assert auth.verify_password("G#EwiXCcvgo4", stored)


async def test_update_user_password_hashes(monkeypatch):
    created = _stub_db(monkeypatch)
    admin = {"role": "owner", "email": None}
    await admin_api.create_user(
        UserCreate(email="a@b.com", password="initialpw1", role="manager"),
        admin=admin)
    await admin_api.update_user(
        "a@b.com", UserUpdate(password="rotatedpw9"), admin=admin)
    stored = created["a@b.com"]["password_hash"]
    assert auth.verify_password("rotatedpw9", stored)
