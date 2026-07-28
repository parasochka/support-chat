"""Test bootstrap: stub native deps (openai/asyncpg) when absent, set test env.

Follows the Greekly pattern — the suite runs without a real DB or API key.
We set SUPPORT_CHAT_TEST_MODE=1 so config.py fills required vars with harmless
placeholders, and we install minimal stub modules for `openai` and `asyncpg`
when they are not importable.
"""
from __future__ import annotations

import os
import sys
import types

os.environ["SUPPORT_CHAT_TEST_MODE"] = "1"
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SESSION_JWT_SECRET", "test-secret-please-change")

# Make the project root importable when pytest runs from anywhere.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Stub `openai` if the SDK is not installed.
# ---------------------------------------------------------------------------
def _install_openai_stub() -> None:
    try:
        import openai  # noqa: F401
        return
    except Exception:
        pass

    mod = types.ModuleType("openai")

    class _Err(Exception):
        pass

    class AsyncOpenAI:  # minimal shape used by openai_client
        def __init__(self, *args, **kwargs):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        async def _create(self, *args, **kwargs):  # pragma: no cover
            raise _Err("stub: no real OpenAI in tests")

    mod.AsyncOpenAI = AsyncOpenAI
    # error classes referenced by openai_client introspection
    for name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError",
                 "RateLimitError", "APITimeoutError", "APIConnectionError",
                 "InternalServerError", "APIError"):
        setattr(mod, name, type(name, (_Err,), {}))
    sys.modules["openai"] = mod


# ---------------------------------------------------------------------------
# Stub `asyncpg` if not installed (db.py imports it at module load).
# ---------------------------------------------------------------------------
def _install_asyncpg_stub() -> None:
    try:
        import asyncpg  # noqa: F401
        return
    except Exception:
        pass

    mod = types.ModuleType("asyncpg")

    class Pool:  # pragma: no cover - not exercised; db calls are not unit-tested
        pass

    class Connection:  # pragma: no cover
        pass

    class Record(dict):  # pragma: no cover
        pass

    async def create_pool(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("stub asyncpg: no real DB in tests")

    mod.Pool = Pool
    mod.Connection = Connection
    mod.Record = Record
    mod.create_pool = create_pool
    sys.modules["asyncpg"] = mod


_install_openai_stub()
_install_asyncpg_stub()


# ---------------------------------------------------------------------------
# The shared fake pool.
#
# `db.*` is not otherwise unit-tested (the asyncpg stub above has no server
# behind it), but some db helpers are worth pinning anyway: destructive deletes,
# the atomic turn write, and the money queries whose SQL must key on the right
# column. Those tests monkeypatch `db._pool` with the fakes below.
#
# ONE implementation on purpose. Four near-identical copies used to live in the
# test files, which is how they drifted: none of them implemented `acquire()`,
# so they only worked for helpers that reached for the pool's convenience
# methods — exactly the call shape db.py has since stopped using (every query
# now goes through the bounded `_acquire`).
# ---------------------------------------------------------------------------
_UNSET = object()


class Row(dict):
    """A row that answers any unset column with 0.

    The query tests read the SQL, not the values, so they should not have to
    enumerate every column a helper happens to select.
    """

    def __missing__(self, key):
        return 0


class _Tx:
    """`conn.transaction()` — records nothing, just satisfies `async with`."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        self._conn.transactions += 1
        return None

    async def __aexit__(self, *a):
        return False


class FakeConn:
    """Records every statement; returns canned results.

    `rows` answers fetch(), `row` answers fetchrow(), `val` answers fetchval().
    Each may also be a callable taking (sql, args) when a test needs different
    answers per statement.
    """

    def __init__(self, *, rows=_UNSET, row=_UNSET, val=0,
                 execute_result="INSERT 0 1"):
        self.calls: list[tuple[str, tuple]] = []
        # Writes only. A test that pins the ORDER of a destructive purge must
        # not have the helper's own lookups shuffled into its list.
        self.executed: list[tuple[str, tuple]] = []
        self.transactions = 0
        # `row=None` is a MEANINGFUL answer ("no such row"), so it must be told
        # apart from "the test didn't care" — hence the sentinel default.
        self._rows = [] if rows is _UNSET else rows
        self._row = Row() if row is _UNSET else row
        self._val = val
        self._execute_result = execute_result

    # -- what the tests assert on ------------------------------------------
    @property
    def sql(self) -> list[str]:
        return [q for q, _ in self.calls]

    @property
    def args(self) -> list[tuple]:
        return [a for _, a in self.calls]

    def _answer(self, canned, sql, args):
        return canned(sql, args) if callable(canned) else canned

    # -- the asyncpg Connection surface db.py uses -------------------------
    def transaction(self):
        return _Tx(self)

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(self._rows, sql, args)

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(self._row, sql, args)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(self._val, sql, args)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        self.executed.append((sql, args))
        return self._answer(self._execute_result, sql, args)

    async def executemany(self, sql, args):
        self.calls.append((sql, tuple(args)))
        self.executed.append((sql, tuple(args)))
        return None


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    """Stands in for `db._pool`. Every query in db.py arrives via acquire()."""

    def __init__(self, conn=None, **conn_kwargs):
        self.conn = conn if conn is not None else FakeConn(**conn_kwargs)

    def acquire(self, timeout=None):
        return _Acquire(self.conn)

    # Convenience: assert on the connection's record without reaching through.
    @property
    def sql(self) -> list[str]:
        return self.conn.sql

    @property
    def args(self) -> list[tuple]:
        return self.conn.args

    @property
    def calls(self) -> list[tuple[str, tuple]]:
        return self.conn.calls
