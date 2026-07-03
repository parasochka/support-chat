"""Encryption-at-rest for per-product secrets (OpenAI keys, handshake secret).

Product secrets live in the `products` table so partners manage them from the
admin panel — but a DB dump must never reveal a client's OpenAI key, so every
secret column stores only an encrypted token from this module. The master key
stays in the environment (`SECRETS_MASTER_KEY`), following the same rule as the
JWT secrets: a compromised database (or admin account) alone cannot recover the
plaintext.

Stdlib-only authenticated encryption, mirroring the project's stdlib-JWT
convention (no `cryptography` dependency):

  - keystream: HMAC-SHA256 in counter mode — ``HMAC(k_enc, nonce || counter)``
    is a PRF, so XOR-ing its output blocks over the plaintext is a standard
    PRF-based stream cipher (unique random 16-byte nonce per encryption);
  - integrity: encrypt-then-MAC — ``HMAC(k_mac, version || nonce || ct)``,
    verified in constant time before any decryption is attempted;
  - k_enc / k_mac are derived from the master key with domain separation, so
    the keystream and the tag never share a key.

Token format: ``v1.<b64url(nonce || ciphertext || tag)>``. If a dependency on
`cryptography` ever lands, AES-GCM/Fernet is a drop-in replacement behind the
same two functions (add a ``v2.`` prefix and keep reading ``v1.``).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

import config

_VERSION = b"v1"
_NONCE_LEN = 16
_TAG_LEN = 32  # full HMAC-SHA256 tag


class SecretBoxError(Exception):
    """Raised when a stored token is malformed or fails authentication."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _derived_keys() -> tuple[bytes, bytes]:
    """Derive the encryption and MAC keys from the master key (domain-separated).

    Read at call time (not import time) so tests that monkeypatch `config` work
    and a rotated env var applies on restart without stale module state.
    """
    master = config.SECRETS_MASTER_KEY.encode("utf-8")
    k_enc = hmac.new(master, b"secretbox-enc", hashlib.sha256).digest()
    k_mac = hmac.new(master, b"secretbox-mac", hashlib.sha256).digest()
    return k_enc, k_mac


def _keystream(k_enc: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(k_enc, nonce + counter.to_bytes(4, "big"),
                         hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    """Encrypt a secret string into a self-contained `v1.` token."""
    if not isinstance(plaintext, str):
        raise SecretBoxError("plaintext must be a string")
    k_enc, k_mac = _derived_keys()
    nonce = os.urandom(_NONCE_LEN)
    data = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(k_enc, nonce, len(data))))
    tag = hmac.new(k_mac, _VERSION + nonce + ct, hashlib.sha256).digest()
    return "v1." + _b64url_encode(nonce + ct + tag)


def decrypt(token: str) -> str:
    """Decrypt a `v1.` token; raises SecretBoxError on tampering/corruption."""
    if not isinstance(token, str) or not token.startswith("v1."):
        raise SecretBoxError("unknown secret token format")
    try:
        raw = _b64url_decode(token[3:])
    except Exception as exc:  # noqa: BLE001
        raise SecretBoxError("undecodable secret token") from exc
    if len(raw) < _NONCE_LEN + _TAG_LEN:
        raise SecretBoxError("secret token too short")
    nonce = raw[:_NONCE_LEN]
    ct = raw[_NONCE_LEN:-_TAG_LEN]
    tag = raw[-_TAG_LEN:]
    k_enc, k_mac = _derived_keys()
    expected = hmac.new(k_mac, _VERSION + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise SecretBoxError("secret token failed authentication")
    data = bytes(a ^ b for a, b in zip(ct, _keystream(k_enc, nonce, len(ct))))
    return data.decode("utf-8")
