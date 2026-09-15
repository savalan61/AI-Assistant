"""Symmetric encryption for secrets stored in the database.

Broker-scoped provider credentials (LLM API keys) must never be readable from
the database alone, so they are encrypted before persistence and decrypted only
inside the boundary that needs them.

Design notes:

* Fernet from the ``cryptography`` library: authenticated symmetric encryption
  (AES-128-CBC with an HMAC-SHA256 tag). It keeps the secret confidential *and*
  detects tampering/corruption instead of silently returning garbage. No crypto
  is hand-rolled here.
* The key comes from application configuration (``SECRET_ENCRYPTION_KEY``, i.e.
  the environment) and is never hard-coded or defaulted: with no usable key the
  utility fails closed, so a misconfigured deployment refuses to store or read
  credentials rather than falling back to something weaker.
* Nothing in this module ever logs, returns, or embeds the key, the plaintext,
  or the ciphertext in an error message: callers translate the generic
  EncryptionError at their own API boundary.
"""
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionError(RuntimeError):
    """Secret encryption/decryption is unavailable or failed.

    Deliberately generic: the specific cause (missing key, wrong key, tampered
    ciphertext) is never disclosed to callers that surface errors to users.
    """


def generate_encryption_key() -> str:
    """Return a fresh urlsafe-base64 Fernet key for ``SECRET_ENCRYPTION_KEY``."""
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    """Build the Fernet cipher from configuration, failing closed."""
    key = settings.SECRET_ENCRYPTION_KEY.strip()
    if not key:
        # No fallback key exists on purpose: an unconfigured deployment must
        # never store credentials in a form anyone with the database can read.
        raise EncryptionError("secret encryption key is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except ValueError as exc:
        # Fernet rejects an invalid/wrong-length base64 key with ValueError;
        # UnicodeError (a ValueError subclass) covers non-ASCII input.
        raise EncryptionError("secret encryption key is invalid") from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` for storage; returns urlsafe-base64 ciphertext."""
    token: bytes = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored secret; raises EncryptionError on any failure."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        # InvalidToken covers malformed base64, a failed signature check, and a
        # token encrypted under a different key; the message stays generic.
        raise EncryptionError("stored secret could not be decrypted") from exc
