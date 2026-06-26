"""Password hashing for named admin users: salted PBKDF2, constant-time verify."""
from __future__ import annotations

import auth


def test_hash_roundtrips_and_verifies():
    stored = auth.hash_password("correct horse battery staple")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse battery staple", stored)


def test_wrong_password_rejected():
    stored = auth.hash_password("s3cret-pass")
    assert not auth.verify_password("nope", stored)


def test_salt_makes_hashes_unique():
    a = auth.hash_password("same-pass")
    b = auth.hash_password("same-pass")
    assert a != b  # random per-user salt
    assert auth.verify_password("same-pass", a)
    assert auth.verify_password("same-pass", b)


def test_malformed_stored_hash_fails_closed():
    assert not auth.verify_password("x", "")
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("x", "md5$1$a$b")  # unknown alg


def test_empty_password_rejected():
    stored = auth.hash_password("realpass")
    assert not auth.verify_password("", stored)


def test_admin_token_carries_email_and_role():
    token = auth.issue_admin_token(role="manager", email="m@example.com")
    payload = auth.verify_admin_token(token)
    assert payload["role"] == "manager"
    assert payload["email"] == "m@example.com"
    assert payload["sub"] == "m@example.com"
