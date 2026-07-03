"""secretbox: encryption-at-rest for per-product secrets (OpenAI keys,
handshake secrets). Stdlib HMAC-CTR + encrypt-then-MAC; a DB dump alone must
never reveal a plaintext, and any tampering must fail closed."""
from __future__ import annotations

import pytest

import config
import secretbox


def test_roundtrip():
    token = secretbox.encrypt("sk-test-1234567890")
    assert token.startswith("v1.")
    assert "sk-test" not in token          # ciphertext, not plaintext
    assert secretbox.decrypt(token) == "sk-test-1234567890"


def test_unicode_and_empty_roundtrip():
    assert secretbox.decrypt(secretbox.encrypt("секрет-ключ-🔑")) == "секрет-ключ-🔑"
    assert secretbox.decrypt(secretbox.encrypt("")) == ""


def test_unique_nonce_per_encryption():
    a = secretbox.encrypt("same-secret")
    b = secretbox.encrypt("same-secret")
    assert a != b                          # fresh nonce every time
    assert secretbox.decrypt(a) == secretbox.decrypt(b) == "same-secret"


def test_tampered_token_rejected():
    token = secretbox.encrypt("sk-live-abcdef")
    body = token[3:]
    flipped = ("A" if body[10] != "A" else "B")
    tampered = "v1." + body[:10] + flipped + body[11:]
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.decrypt(tampered)


def test_unknown_format_rejected():
    for bad in ("", "v2.abc", "plaintext", "v1.!!!not-base64!!!", "v1."):
        with pytest.raises(secretbox.SecretBoxError):
            secretbox.decrypt(bad)


def test_master_key_rotation_invalidates(monkeypatch):
    token = secretbox.encrypt("sk-old-master")
    monkeypatch.setattr(config, "SECRETS_MASTER_KEY", "a-brand-new-master-key")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.decrypt(token)
