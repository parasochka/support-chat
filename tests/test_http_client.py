"""app/core/http.py — the ONE outbound client per process.

What these pin down is easy to break by accident and silent when broken: a
client built at import time (the fakes stop intercepting), a construction-time
timeout (the three sites' distinct deadlines quietly flatten to one), a close
that leaves the global
set (the process can never open another one), and the keep-alive escape hatch.
"""
from __future__ import annotations

import asyncio

import httpx

from app.core import config
from app.core import http


def _teardown():
    http.reset()


def test_client_is_built_lazily_and_cached():
    async def _go():
        first = http.client()
        second = http.client()
        # Same object: the whole point is one connection pool + one SSL context
        # per process, not one per call.
        assert first is second
        await http.aclose()

    asyncio.run(_go())
    _teardown()


def test_the_class_is_resolved_at_call_time_not_import_time(monkeypatch):
    """A module-level `_CLIENT = httpx.AsyncClient(...)` would resolve the class
    before any test could patch it — and would silently un-hook every transport
    fake in the suite."""
    built = []

    class _Fake:
        def __init__(self, *a, **k):
            built.append(k)

    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    http.reset()

    async def _go():
        assert isinstance(http.client(), _Fake)

    asyncio.run(_go())
    assert built, "the patched class was never constructed"
    _teardown()


def test_no_base_url_no_default_headers(monkeypatch):
    """Both would be cross-tenant leaks: the Customer.io host is per-product,
    the Telegram path embeds the per-product bot token, and every bearer is a
    per-product decrypted secret."""
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    http.reset()

    async def _go():
        http.client()

    asyncio.run(_go())
    assert "base_url" not in seen
    assert "headers" not in seen
    assert "follow_redirects" not in seen  # a redirect would leave the host
    _teardown()


def test_keepalive_expiry_zero_disables_reuse(monkeypatch):
    """The documented escape hatch: HTTP_KEEPALIVE_EXPIRY_SEC=0 must drop the
    keepalive pool to zero, leaving only the shared SSL context."""
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    monkeypatch.setattr(config, "HTTP_KEEPALIVE_EXPIRY_SEC", 0)
    http.reset()

    async def _go():
        http.client()

    asyncio.run(_go())
    assert seen["limits"].max_keepalive_connections == 0
    _teardown()


def test_close_resets_the_global_so_it_can_reopen():
    async def _go():
        first = http.client()
        await http.aclose()
        assert http._client is None
        second = http.client()
        assert second is not first
        await http.aclose()

    asyncio.run(_go())
    _teardown()


def test_close_is_a_no_op_when_nothing_was_built():
    """Both entry points call this from their shutdown `finally`, and one test
    runs the whole lifespan with no DB and no client."""
    http.reset()
    asyncio.run(http.aclose())
    assert http._client is None


def test_close_never_raises(monkeypatch):
    """It sits next to `await db.close()` in an unguarded `finally` — a teardown
    error here must not skip the DB close or the shutdown log line."""
    class _Fake:
        is_closed = False

        def __init__(self, *a, **k):
            pass

        async def aclose(self):
            raise RuntimeError("transport exploded")

    monkeypatch.setattr(httpx, "AsyncClient", _Fake)
    http.reset()

    async def _go():
        http.client()
        await http.aclose()          # must swallow

    asyncio.run(_go())
    assert http._client is None
    _teardown()


def test_a_client_from_a_dead_loop_is_rebuilt():
    """Each async test gets its own event loop; a pool bound to a closed one
    raises 'Event loop is closed' on first use."""
    http.reset()

    async def _first():
        return http.client()

    first = asyncio.run(_first())

    async def _second():
        return http.client()

    second = asyncio.run(_second())
    assert second is not first
    asyncio.run(http.aclose())
    _teardown()
