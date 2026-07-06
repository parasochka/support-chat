"""Regression: partner-supplied NUMBERS in profile fields must not 500 a TEXT write.

A host handshake / nonce payload / CRM push may serialize `id`, `balance`,
`vip_level` etc. as JSON numbers. asyncpg binds strictly, so an int destined for
a TEXT column raised DataError and 500'd session create / the retention link.
`db._as_text` coerces scalars to str at the write boundary; these tests pin it.
"""
from __future__ import annotations

import db


def test_as_text_coerces_scalars_keeps_none_and_structures():
    assert db._as_text(12345) == "12345"
    assert db._as_text(1500.5) == "1500.5"
    assert db._as_text(True) == "True"
    assert db._as_text("already") == "already"
    assert db._as_text(None) is None
    # non-scalars are left untouched (jsonb column / caller bug, not masked)
    assert db._as_text({"a": 1}) == {"a": 1}
    assert db._as_text([1, 2]) == [1, 2]


class _FakePool:
    """Captures the positional args bound to each execute/fetchrow call."""

    def __init__(self, fetchrow_result=None):
        self.calls: list = []
        self._fetchrow_result = fetchrow_result

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self._fetchrow_result


async def test_create_session_binds_player_id_as_text(monkeypatch):
    fake = _FakePool()
    monkeypatch.setattr(db, "_pool", fake)
    # numeric id from a signed handshake (host serialized `id` as a number)
    sid = await db.create_session(
        consumer="web", player_id=12345, lang="en",
        user_context={"id": 12345}, session_id="sess-1", product_id=1,
    )
    assert sid == "sess-1"
    _query, args = fake.calls[-1]
    # (id, consumer, product_id, player_id, lang, user_context, tg_user_id)
    assert args[3] == "12345" and isinstance(args[3], str)


async def test_upsert_retention_user_coerces_numeric_profile(monkeypatch):
    fake = _FakePool(fetchrow_result={"ok": True})
    monkeypatch.setattr(db, "_pool", fake)

    async def _no_existing(product_id, tg_user_id):
        return None

    monkeypatch.setattr(db, "get_retention_user", _no_existing)
    monkeypatch.setattr(db, "_row_to_retention_user", lambda row: row)

    await db.upsert_retention_user(
        1, 99, player_id=777,
        profile={"balance": 1500.0, "vip_level": 3, "full_name": "Ann"},
    )
    _query, args = fake.calls[-1]
    # player_id + every numeric profile value must be bound as a string
    assert "777" in args and 777 not in args
    assert "1500.0" in args and 1500.0 not in args
    assert "3" in args and 3 not in args
    assert "Ann" in args
