"""The security-header middleware (app/main.py).

The admin SPA performs destructive, state-changing actions from ordinary buttons
(delete a product, rotate a widget key, mint a service API key) and nothing
stopped a third-party page from FRAMING it and clickjacking a logged-in operator
into those clicks. /admin/* must therefore refuse to be framed — while the
widget, the test page and the integration docs stay embeddable, since partner
sites are meant to open them (a blanket deny would break the integration this
service exists for).
"""
from __future__ import annotations

import types

import pytest

from app.main import security_headers


class _Resp:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})


def _req(path: str):
    return types.SimpleNamespace(url=types.SimpleNamespace(path=path))


async def _run(path: str, headers=None) -> _Resp:
    resp = _Resp(headers)

    async def _call_next(_request):
        return resp

    return await security_headers(_req(path), _call_next)


@pytest.mark.parametrize("path", ["/admin", "/admin/", "/admin/login",
                                  "/admin/products/1", "/admin/assets/x.js",
                                  "/admin/retention/photos",
                                  "/admin/quality/reviews"])
async def test_admin_paths_refuse_framing(path):
    resp = await _run(path)
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("path", ["/", "/widget.js", "/widget.css",
                                  "/test.html", "/integration",
                                  "/integration-widget", "/api/chat/message",
                                  "/healthz"])
async def test_public_surfaces_stay_embeddable(path):
    """A frame deny here would break partner embeds — never set it."""
    resp = await _run(path)
    assert "X-Frame-Options" not in resp.headers
    assert "Content-Security-Policy" not in resp.headers


@pytest.mark.parametrize("path", ["/", "/widget.js", "/admin", "/healthz",
                                  "/api/chat/session"])
async def test_nosniff_everywhere(path):
    resp = await _run(path)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


async def test_handler_supplied_headers_win():
    """setdefault, not assignment: a route that set its own value keeps it."""
    resp = await _run("/admin/x", {"X-Frame-Options": "SAMEORIGIN",
                                   "X-Content-Type-Options": "custom"})
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["X-Content-Type-Options"] == "custom"
