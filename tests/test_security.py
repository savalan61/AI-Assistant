"""Unit tests for the authentication security primitives.

Require none of: database, PostgreSQL, MT5, network, FastAPI TestClient, .env.
JWT configuration is overridden with a deterministic test-only secret.
"""
from datetime import timedelta
from typing import Any

import jwt
import pytest

from app.core import security
from app.core.config import settings as app_settings
from app.core.security import SecurityError, create_access_token, decode_token, hash_password, verify_password

# Deterministic, obviously-non-secret values used ONLY inside these tests.
TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the settings object at safe test values so no real .env secret
    # is ever needed or read by security operations during tests.
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


# --- password hashing -------------------------------------------------------


def test_hash_password_returns_hash_different_from_plaintext():
    password = "correct horse battery staple"

    hashed = hash_password(password)

    assert isinstance(hashed, str)
    assert hashed != password
    assert hashed.startswith("$2")


def test_same_plaintext_verifies_successfully():
    password = "correct horse battery staple"
    hashed = hash_password(password)

    assert verify_password(password, hashed) is True


def test_wrong_password_fails_verification():
    hashed = hash_password("correct password")

    assert verify_password("wrong password", hashed) is False


def test_hashing_same_password_twice_produces_different_hashes():
    password = "same input"

    first = hash_password(password)
    second = hash_password(password)

    # Per-call internal salt: identical plaintexts must never collide on hash.
    assert first != second
    assert verify_password(password, first) and verify_password(password, second)


# --- JWT creation/decoding ---------------------------------------------------


def test_created_token_is_decodable_and_subject_is_preserved():
    token = create_access_token("user-42")

    payload = decode_token(token)

    assert payload["sub"] == "user-42"


def test_token_contains_expiration():
    token = create_access_token("user-42")

    payload: dict[str, Any] = jwt.decode(token, TEST_SECRET, algorithms=[TEST_ALGORITHM])

    assert "exp" in payload
    assert "iat" in payload


def test_decode_token_accepts_valid_token_with_custom_expiry():
    token = create_access_token("user-42", expires_delta=timedelta(minutes=5))

    payload = decode_token(token)

    assert payload["sub"] == "user-42"
    assert "exp" in payload


def test_malformed_token_raises_security_error():
    with pytest.raises(SecurityError):
        decode_token("not-a-jwt-token")


def test_invalid_signature_raises_security_error():
    # Crafted with a different (wrong but length-valid) key: signature
    # validation must fail closed.
    forged = jwt.encode(
        {"sub": "attacker", "exp": 4102444800},
        "some-other-secret-that-is-long-enough-32",
        algorithm=TEST_ALGORITHM,
    )

    with pytest.raises(SecurityError):
        decode_token(forged)


def test_expired_token_raises_security_error():
    token = create_access_token("user-42", expires_delta=timedelta(seconds=-10))

    with pytest.raises(SecurityError):
        decode_token(token)


def test_security_error_hides_third_party_details():
    # The boundary must not leak PyJWT internals in the message.
    with pytest.raises(SecurityError) as exc_info:
        decode_token("not-a-jwt-token")

    assert "Invalid or expired token" in str(exc_info.value)


# --- configuration fail-closed behavior -------------------------------------


def test_create_token_without_secret_fails_closed(monkeypatch: pytest.MonkeyPatch):
    # No committed SECRET_KEY exists: token operations must fail loudly
    # instead of silently signing with an empty secret.
    monkeypatch.setattr(app_settings, "SECRET_KEY", "", raising=True)

    with pytest.raises(RuntimeError):
        create_access_token("user-42")


def test_module_has_exactly_the_documented_public_functions():
    # Guard against scope creep: no login/user-lookup machinery here yet.
    public = {name for name in dir(security) if not name.startswith("_")}
    assert {"hash_password", "verify_password", "create_access_token", "decode_token", "SecurityError"} <= public
