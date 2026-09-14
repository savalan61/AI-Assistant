"""Minimal authentication security primitives.

Boundary module: translates expected bcrypt/PyJWT failures into the
application-level SecurityError so higher layers never depend on the
third-party libraries or see their low-level details.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# One clear application-level error for invalid/expired/malformed tokens.
# Deliberately broad enough to back a future authentication dependency with a
# single 401-style semantic; not used to hide programming errors.
class SecurityError(Exception):
    """A token could not be trusted: invalid, expired, or malformed."""


def hash_password(password: str) -> str:
    # bcrypt.generate_password_hash is the library's public API; salt is
    # generated internally per call, which is why two hashes of the same
    # password differ.
    try:
        hashed: bytes = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Password hashing failed") from exc
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    # bcrypt.checkpw returns False on mismatch; only library misuse raises.
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Password verification failed") from exc


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expires = expires_delta if expires_delta is not None else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,  # standard subject claim; broker_id intentionally deferred
        "exp": now + expires,
        "iat": now,
    }
    try:
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    except jwt.InvalidKeyError as exc:
        # Fail closed on misconfiguration (e.g. unconfigured SECRET_KEY):
        # refuse to sign with an empty/invalid key rather than emit a token.
        raise RuntimeError("Token signing failed: SECRET_KEY is not configured") from exc


def decode_token(token: str) -> dict[str, Any]:
    try:
        # PyJWT verifies both signature and exp when present (required here).
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except jwt.InvalidTokenError as exc:
        # Base class of every expected PyJWT failure: ExpiredSignatureError,
        # InvalidSignatureError, DecodeError, etc. Translated at this boundary
        # so no third-party details leak to callers.
        raise SecurityError("Invalid or expired token") from exc
    return payload
