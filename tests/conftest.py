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
