"""Signed front-end handshake: valid HMAC accepted; tampered/expired rejected;
secret required."""
from __future__ import annotations

import time

import pytest

import auth
import config


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", "hs-secret")
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_MAX_AGE_SEC", 300)


def test_valid_handshake_roundtrip():
    blob = auth.sign_handshake({"id": "p1", "full_name": "Alice"}, ttl_sec=120)
    payload = auth.verify_handshake(blob)
    assert payload["id"] == "p1"
    assert payload["full_name"] == "Alice"
    assert "exp" in payload and "iat" in payload


def test_tampered_payload_rejected():
    blob = auth.sign_handshake({"id": "p1"})
    payload_b64, sig = blob.split(".")
    tampered = payload_b64[:-2] + ("AA" if not payload_b64.endswith("AA") else "BB")
    with pytest.raises(auth.TokenError):
        auth.verify_handshake(f"{tampered}.{sig}")


def test_tampered_signature_rejected():
    blob = auth.sign_handshake({"id": "p1"})
    payload_b64, sig = blob.split(".")
    bad = sig[:-2] + ("AA" if not sig.endswith("AA") else "BB")
    with pytest.raises(auth.TokenError):
        auth.verify_handshake(f"{payload_b64}.{bad}")


def test_expired_handshake_rejected():
    blob = auth.sign_handshake({"id": "p1"}, ttl_sec=-1)
    with pytest.raises(auth.TokenError):
        auth.verify_handshake(blob)


def test_too_old_handshake_rejected(monkeypatch):
    # exp far in the future but iat older than the max-age window.
    old = int(time.time()) - 10_000
    blob = auth.sign_handshake({"id": "p1", "iat": old, "exp": int(time.time()) + 9999})
    with pytest.raises(auth.TokenError):
        auth.verify_handshake(blob)


def test_malformed_rejected():
    with pytest.raises(auth.TokenError):
        auth.verify_handshake("no-dot-here")


def test_secret_required(monkeypatch):
    monkeypatch.setattr(config, "WIDGET_HANDSHAKE_SECRET", None)
    with pytest.raises(auth.TokenError):
        auth.verify_handshake("a.b")
    with pytest.raises(auth.TokenError):
        auth.sign_handshake({"id": "x"})
