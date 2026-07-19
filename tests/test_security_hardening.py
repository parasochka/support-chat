"""Small security-hardening regressions:

1. _ct_eq tolerates a non-ASCII Bearer/secret token (hmac.compare_digest raises
   TypeError on non-ASCII str -> a 500 that, on the per-product partner path, is a
   tenant-enumeration oracle). It must return False, never raise.
2. verify_handshake bounds the anti-replay window against `exp` when the signed
   payload omits `iat`, so a self-signed far-future blob isn't replayable for its
   whole lifetime.
"""
from __future__ import annotations

import json
import time

import auth
import config
import pytest
from api import retention


# --- 1. non-ASCII constant-time compare -----------------------------------
def test_ct_eq_matches_and_rejects():
    assert retention._ct_eq("sak_abc123", "sak_abc123") is True
    assert retention._ct_eq("sak_abc123", "sak_abc124") is False


def test_ct_eq_non_ascii_returns_false_not_crash():
    # Starlette decodes header bytes as latin-1, so a >0x7F byte reaches here as a
    # non-ASCII str; hmac.compare_digest would raise TypeError -> HTTP 500.
    assert retention._ct_eq("ÿ", "secret") is False
    assert retention._ct_eq("Bearerÿ", "secret") is False
    assert retention._ct_eq("секрет", "secret") is False


# --- 2. handshake anti-replay window without iat --------------------------
def _blob(payload: dict, key: str) -> str:
    pb = auth._b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = auth._b64url_encode(auth._sign_with(key, pb.encode("ascii")))
    return f"{pb}.{sig}"


def test_handshake_without_iat_rejected_when_exp_exceeds_max_age():
    key = config.WIDGET_HANDSHAKE_SECRET or "test-secret"
    now = int(time.time())
    long_exp = now + config.WIDGET_HANDSHAKE_MAX_AGE_SEC + 10_000
    blob = _blob({"id": "u1", "exp": long_exp}, key)  # no iat
    with pytest.raises(auth.TokenError):
        auth.verify_handshake(blob, secret=key)


def test_handshake_without_iat_accepted_within_max_age():
    key = config.WIDGET_HANDSHAKE_SECRET or "test-secret"
    now = int(time.time())
    short_exp = now + min(60, config.WIDGET_HANDSHAKE_MAX_AGE_SEC)
    blob = _blob({"id": "u1", "exp": short_exp}, key)  # no iat, within window
    payload = auth.verify_handshake(blob, secret=key)
    assert payload["id"] == "u1"


def test_handshake_with_iat_unaffected():
    key = config.WIDGET_HANDSHAKE_SECRET or "test-secret"
    # The normal helper always stamps iat; a far exp is fine as long as iat fresh.
    blob = auth.sign_handshake({"id": "u2"}, ttl_sec=99_999, secret=key)
    payload = auth.verify_handshake(blob, secret=key)
    assert payload["id"] == "u2"
