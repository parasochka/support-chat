"""main.body_size_cap — the request-body DoS guard.

Rejects an over-large declared Content-Length (413) and a chunked body that
declares no length (411, so it can't sail past the cap and get buffered whole).
The middleware is driven directly with a fake request + call_next, avoiding the
app lifespan (which needs a DB).
"""
from __future__ import annotations

import json
import types

import config
import main
import settings


def _request(headers: dict, *, method="POST", path="/api/chat/message"):
    return types.SimpleNamespace(
        method=method,
        url=types.SimpleNamespace(path=path),
        headers=headers,
    )


async def _passthrough(request):
    return "OK"


def _status_and_body(resp):
    return resp.status_code, json.loads(bytes(resp.body).decode())


async def test_oversized_content_length_rejected_413():
    cap = settings.general()["body_max_bytes"]
    resp = await main.body_size_cap(
        _request({"content-length": str(cap + 1)}), _passthrough)
    status, body = _status_and_body(resp)
    assert status == 413
    assert body["error"] == "body_too_large"


async def test_within_cap_passes_through():
    cap = settings.general()["body_max_bytes"]
    out = await main.body_size_cap(
        _request({"content-length": str(cap - 1)}), _passthrough)
    assert out == "OK"


async def test_no_headers_passes_through():
    out = await main.body_size_cap(_request({}), _passthrough)
    assert out == "OK"


async def test_chunked_without_length_rejected_411():
    resp = await main.body_size_cap(
        _request({"transfer-encoding": "chunked"}), _passthrough)
    status, body = _status_and_body(resp)
    assert status == 411
    assert body["error"] == "length_required"


async def test_invalid_content_length_is_ignored():
    # A non-numeric Content-Length can't be enforced; the request passes to the
    # parser rather than 500-ing in the middleware.
    out = await main.body_size_cap(
        _request({"content-length": "not-a-number"}), _passthrough)
    assert out == "OK"


async def test_media_upload_gets_the_larger_cap():
    # A body between the JSON cap and the upload cap is rejected on the JSON path
    # but accepted on the media-upload path.
    json_cap = settings.general()["body_max_bytes"]
    mid = json_cap + 1
    assert mid < config.RETENTION_MAX_UPLOAD_BYTES

    rejected = await main.body_size_cap(
        _request({"content-length": str(mid)}, path="/api/chat/message"),
        _passthrough)
    assert rejected.status_code == 413

    allowed = await main.body_size_cap(
        _request({"content-length": str(mid)}, path="/admin/retention/photos"),
        _passthrough)
    assert allowed == "OK"
