"""HS256 JWT on pure stdlib (hmac/hashlib/base64) — no PyJWT.

Issues and verifies short-lived session tokens bound to a `session_id`. The
token's `sub` claim is the session UUID; `POST /api/chat/message` requires a
valid token whose `sub` matches the path/body `session_id`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

import config

_ALG = "HS256"


class TokenError(Exception):
    """Raised when a token is malformed, tampered, expired, or mismatched."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(signing_input: bytes) -> bytes:
    return hmac.new(
        config.SESSION_JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()


def issue_session_token(session_id: str, ttl_hours: Optional[int] = None,
                        extra: Optional[dict[str, Any]] = None) -> str:
    """Mint an HS256 JWT for the given session id."""
    ttl = config.SESSION_TTL_HOURS if ttl_hours is None else ttl_hours
    now = int(time.time())
    header = {"alg": _ALG, "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": session_id,
        "iat": now,
        "exp": now + ttl * 3600,
    }
    if extra:
        payload.update(extra)

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(_sign(signing_input))
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def verify_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry; return the decoded payload or raise TokenError."""
    if not token or token.count(".") != 2:
        raise TokenError("malformed token")
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    try:
        expected = _sign(signing_input)
        provided = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise TokenError("bad signature encoding") from exc

    if not hmac.compare_digest(expected, provided):
        raise TokenError("signature mismatch")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("undecodable token body") from exc

    if header.get("alg") != _ALG:
        raise TokenError("unexpected alg")

    exp = payload.get("exp")
    if exp is None or int(time.time()) >= int(exp):
        raise TokenError("token expired")

    return payload


def verify_session_token(token: str, session_id: str) -> dict[str, Any]:
    """Verify a token AND that it is bound to `session_id`."""
    payload = verify_token(token)
    if payload.get("sub") != session_id:
        raise TokenError("token not bound to this session")
    return payload


def extract_bearer(authorization: Optional[str]) -> str:
    """Pull the raw token out of an `Authorization: Bearer <token>` header."""
    if not authorization:
        raise TokenError("missing Authorization header")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenError("expected 'Bearer <token>'")
    return parts[1].strip()
