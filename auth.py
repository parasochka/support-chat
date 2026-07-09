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
    if ttl_hours is None:
        import settings  # lazy: avoid an import cycle (settings is built atop db)
        ttl = settings.general()["session_ttl_hours"]
    else:
        ttl = ttl_hours
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


# ---------------------------------------------------------------------------
# Admin tokens (Phase 2) — separate secret, role claim, minutes-based TTL
# ---------------------------------------------------------------------------
def _sign_with(secret: str, signing_input: bytes) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def issue_admin_token(role: str = "admin",
                      ttl_min: Optional[int] = None,
                      email: Optional[str] = None) -> str:
    """Mint an admin JWT signed with ADMIN_JWT_SECRET (distinct from sessions).

    `role` drives authorization (admin may write; manager is read-only).
    `email` identifies the named user and rides in the token so the audit trail
    (`updated_by`) records who acted.
    """
    if ttl_min is None:
        import settings  # lazy: avoid an import cycle (settings is built atop db)
        ttl = settings.general()["admin_token_ttl_min"]
    else:
        ttl = ttl_min
    now = int(time.time())
    header = {"alg": _ALG, "typ": "JWT"}
    payload = {"sub": email or "admin", "role": role, "iat": now, "exp": now + ttl * 60}
    if email:
        payload["email"] = email
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig_b64 = _b64url_encode(_sign_with(config.ADMIN_JWT_SECRET, signing_input))
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def verify_admin_token(token: str) -> dict[str, Any]:
    """Verify an admin JWT (signature via ADMIN_JWT_SECRET + expiry + role)."""
    if not token or token.count(".") != 2:
        raise TokenError("malformed token")
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        expected = _sign_with(config.ADMIN_JWT_SECRET, signing_input)
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
    if not payload.get("role"):
        raise TokenError("missing role claim")
    return payload


def refresh_admin_token(payload: dict[str, Any], role: str,
                        email: Optional[str] = None) -> Optional[str]:
    """Sliding-session helper: given a still-valid admin token's payload, return
    a freshly-minted token (new `exp`, full TTL from settings) once the current
    one is past the HALFWAY point of its lifetime — else None (no refresh yet).

    This makes the admin session slide: every day of activity re-issues the
    token with another full TTL ahead, so an operator who keeps using the panel
    is never logged out mid-work, while an account left untouched for a whole
    TTL window (a week by default) simply expires. `role`/`email` come from the
    per-request DB re-check in `require_admin`, so a demotion propagates into
    the refreshed token too. Re-minting only after half-life keeps it to at most
    one new token per request-burst instead of one on every single call.
    """
    iat = payload.get("iat")
    exp = payload.get("exp")
    if iat is None or exp is None:
        return None
    try:
        iat_i, exp_i = int(iat), int(exp)
    except (TypeError, ValueError):
        return None
    now = int(time.time())
    lifetime = exp_i - iat_i
    if lifetime <= 0:
        return None
    # Past the halfway mark of this token's life → slide it forward.
    if (now - iat_i) * 2 >= lifetime:
        return issue_admin_token(role=role, email=email)
    return None


# ---------------------------------------------------------------------------
# Signed front-end handshake (Phase 2 §9) — HMAC over a base64url payload + exp
# ---------------------------------------------------------------------------
def sign_handshake(context: dict[str, Any], ttl_sec: int = 300,
                   secret: Optional[str] = None) -> str:
    """Build a signed user_context blob (host-backend helper / test aid).

    Format: b64url(payload_json).b64url(hmac). The payload carries the trusted
    user_context fields plus `iat`/`exp`. Signed with the PRODUCT's handshake
    secret when given (multi-tenancy: each casino's CMS signs with its own
    secret from the admin Structure tab), else the deploy-level
    WIDGET_HANDSHAKE_SECRET.
    """
    key = secret or config.WIDGET_HANDSHAKE_SECRET
    if not key:
        raise TokenError("WIDGET_HANDSHAKE_SECRET is not configured")
    now = int(time.time())
    payload = dict(context)
    payload.setdefault("iat", now)
    payload.setdefault("exp", now + ttl_sec)
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    sig_b64 = _b64url_encode(
        _sign_with(key, payload_b64.encode("ascii"))
    )
    return f"{payload_b64}.{sig_b64}"


def verify_handshake(blob: str, secret: Optional[str] = None) -> dict[str, Any]:
    """Verify a signed handshake blob; return the payload or raise TokenError.

    Checks: configured secret, structural validity, HMAC, `exp`, and that the
    token is not older than WIDGET_HANDSHAKE_MAX_AGE_SEC (anti-replay window).
    `secret` is the per-product handshake secret when the product has one;
    without it the deploy-level WIDGET_HANDSHAKE_SECRET applies.
    """
    key = secret or config.WIDGET_HANDSHAKE_SECRET
    if not key:
        raise TokenError("handshake secret not configured")
    if not blob or blob.count(".") != 1:
        raise TokenError("malformed handshake")
    payload_b64, sig_b64 = blob.split(".")
    try:
        expected = _sign_with(key, payload_b64.encode("ascii"))
        provided = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise TokenError("bad handshake signature encoding") from exc
    if not hmac.compare_digest(expected, provided):
        raise TokenError("handshake signature mismatch")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("undecodable handshake payload") from exc
    now = int(time.time())
    exp = payload.get("exp")
    if exp is None or now >= int(exp):
        raise TokenError("handshake expired")
    iat = payload.get("iat")
    if iat is not None and now - int(iat) > config.WIDGET_HANDSHAKE_MAX_AGE_SEC:
        raise TokenError("handshake too old")
    return payload


# ---------------------------------------------------------------------------
# Password hashing for named admin users (PBKDF2-HMAC-SHA256, stdlib only)
#
# Named admin/manager users (created from the Users tab) authenticate with an
# email + password pair; their password is stored only as a salted PBKDF2 hash,
# never in plaintext. Format mirrors Django's: "pbkdf2_sha256$iters$salt$hash"
# (salt + hash base64url, no padding). Verification is constant-time.
# ---------------------------------------------------------------------------
_PBKDF2_ALG = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS,
                  salt: Optional[bytes] = None) -> str:
    """Derive a salted PBKDF2-HMAC-SHA256 hash string for `password`."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    if salt is None:
        import os
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (f"{_PBKDF2_ALG}${iterations}$"
            f"{_b64url_encode(salt)}${_b64url_encode(dk)}")


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored hash string."""
    if not password or not stored:
        return False
    try:
        alg, iters_s, salt_b64, hash_b64 = stored.split("$")
        if alg != _PBKDF2_ALG:
            return False
        iterations = int(iters_s)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(hash_b64)
    except Exception:  # noqa: BLE001 - any malformed hash fails closed
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def extract_bearer(authorization: Optional[str]) -> str:
    """Pull the raw token out of an `Authorization: Bearer <token>` header."""
    if not authorization:
        raise TokenError("missing Authorization header")
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenError("expected 'Bearer <token>'")
    return parts[1].strip()
