"""Tests for the secret encryption utility.

Pure crypto tests: no database, no network, no MT5. The encryption key is a
test-only value generated in-process and restored by monkeypatch.
"""
import pytest

from app.core.config import settings as app_settings
from app.core.encryption import (
    EncryptionError,
    decrypt_secret,
    encrypt_secret,
    generate_encryption_key,
)

SECRET = "sk-encryption-unit-test-secret-value"


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Give every test a usable, test-only encryption key."""
    key = generate_encryption_key()
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", key, raising=True)
    return key


# --- roundtrip ------------------------------------------------------------------------


def test_roundtrip_returns_the_original_secret() -> None:
    assert decrypt_secret(encrypt_secret(SECRET)) == SECRET


def test_roundtrip_handles_arbitrary_text() -> None:
    for value in ("", "short", "sk-" + "x" * 400, "unicode-\u00e9\u4e2d\u6587"):
        assert decrypt_secret(encrypt_secret(value)) == value


def test_ciphertext_does_not_contain_the_plaintext() -> None:
    ciphertext = encrypt_secret(SECRET)

    assert ciphertext != SECRET
    assert SECRET not in ciphertext
    # Fernet v1 tokens are urlsafe base64 and begin with the version byte.
    assert ciphertext.startswith("gAAAAA")


def test_encryption_is_non_deterministic_and_both_ciphertexts_decrypt() -> None:
    first = encrypt_secret(SECRET)
    second = encrypt_secret(SECRET)

    # A fresh random IV per call: identical plaintexts produce different
    # ciphertexts, so the store does not leak equality between brokers.
    assert first != second
    assert decrypt_secret(first) == SECRET
    assert decrypt_secret(second) == SECRET


# --- integrity ------------------------------------------------------------------------


def test_tampered_ciphertext_is_rejected() -> None:
    ciphertext = encrypt_secret(SECRET)
    tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

    # Fernet authenticates: tampering is detected instead of decrypting to
    # garbage.
    with pytest.raises(EncryptionError):
        decrypt_secret(tampered)


def test_other_key_cannot_decrypt() -> None:
    ciphertext = encrypt_secret(SECRET)
    # Simulate key rotation: the old ciphertext must fail closed, not silently
    # yield a wrong or partial secret.
    app_settings.SECRET_ENCRYPTION_KEY = generate_encryption_key()

    with pytest.raises(EncryptionError):
        decrypt_secret(ciphertext)


def test_non_token_ciphertext_is_rejected() -> None:
    with pytest.raises(EncryptionError):
        decrypt_secret("not-a-fernet-token")


# --- configuration handling --------------------------------------------------------------


def test_missing_key_fails_closed_on_encrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", "", raising=True)

    with pytest.raises(EncryptionError) as excinfo:
        encrypt_secret(SECRET)

    message = str(excinfo.value)
    assert "not configured" in message
    assert SECRET not in message


def test_missing_key_fails_closed_on_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    ciphertext = encrypt_secret(SECRET)
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", "", raising=True)

    with pytest.raises(EncryptionError):
        decrypt_secret(ciphertext)


def test_invalid_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", "definitely-not-a-fernet-key", raising=True)

    with pytest.raises(EncryptionError) as excinfo:
        encrypt_secret(SECRET)

    assert "invalid" in str(excinfo.value)


def test_generated_keys_differ_and_are_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    first = generate_encryption_key()
    second = generate_encryption_key()

    assert isinstance(first, str)
    assert first != second
    for key in (first, second):
        monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", key, raising=True)
        assert decrypt_secret(encrypt_secret(SECRET)) == SECRET


# --- no secret leakage ------------------------------------------------------------------


def test_error_messages_never_contain_the_secret_or_ciphertext() -> None:
    ciphertext = encrypt_secret(SECRET)
    tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

    with pytest.raises(EncryptionError) as excinfo:
        decrypt_secret(tampered)

    message = str(excinfo.value)
    assert SECRET not in message
    assert ciphertext not in message
    assert "gAAAAA" not in message
