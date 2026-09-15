"""Tenant-scoped MT5 session boundary.

Why this module exists
----------------------

MT5 is not a multi-session API. The MetaTrader5 Python package authenticates
**one account per process**: ``initialize()`` and ``login()`` act on the single
global terminal connection, and any further ``login()`` switches that one
connection to another account. There is therefore no way to hold two tenants'
sessions at the same time inside one Python process.

Left unmanaged, that produces a real cross-tenant leak: request A authenticates
tenant A, request B authenticates tenant B, and A's read then silently returns
B's data. This module is the smallest boundary that makes that impossible.

Design
------

``MT5SessionManager`` owns every piece of process-global MT5 state:

* one re-entrant lock, held for a whole ``acquire`` -> raw-read span;
* the identity currently authenticated (``server``, ``login``);
* the MT5 API seam (``MetaTrader5`` by default, injectable for tests).

A read for tenant A either finds A already authenticated — reuse, no login — or
switches the terminal to A *while holding the lock*, runs its raw MT5 calls
inside the ``with`` block, and releases. Because the key is asserted before
every call and the lock covers the call itself, a session belonging to tenant B
can never serve A's read, and an account switch can never land in the middle of
another tenant's read.

The unavoidable cost is serialization: every MT5 read in the process waits for
the one terminal. That is a property of the MT5 Python API, not a choice made
here — it is documented rather than hidden, and it is why providers stay cheap
per-request objects while only this manager is process-wide.

Credentials
-----------

The plaintext password never lives outside this boundary. The composition root
resolves the tenant's *encrypted* password straight from the database (see
``app.core.dependencies``) and hands it over here; decryption happens inside
``acquire``, immediately before authenticating, and the plaintext is not
retained. ``MT5AccountCredentials`` deliberately keeps the ciphertext out of
its ``repr`` so a stray log line or traceback cannot carry it, and no message in
this module ever embeds a password, a login or a server name.
"""
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import MetaTrader5 as mt5

from app.core.encryption import EncryptionError, decrypt_secret


class MT5SessionError(RuntimeError):
    """The tenant's MT5 session could not be established.

    A ``RuntimeError`` subclass on purpose: MT5 availability failures already
    map to HTTP 503 at the API boundary, so no API code needs to know about
    this type, and the message stays generic — which credential was missing,
    undecryptable or rejected is never disclosed.
    """


@dataclass(frozen=True)
class MT5AccountCredentials:
    """A tenant's MT5 identity, resolved from the authenticated database rows.

    - ``login``: the MT5 account number (the user's ``login``, when numeric);
    - ``server``: the user's MT5 server, else the broker's;
    - ``password_encrypted``: the stored Fernet ciphertext of the MT5 password.

    Any of them may be ``None`` — a record that is not fully configured is not
    an error at resolution time; it fails closed here, inside the session
    boundary, where the reason stays generic. ``password_encrypted`` is excluded
    from the dataclass ``repr`` so it cannot leak through logs or tracebacks.
    """

    login: int | None
    server: str | None
    password_encrypted: str | None = field(default=None, repr=False)


def _resolve_credentials(credentials: MT5AccountCredentials) -> tuple[int, str, str]:
    """Validate one tenant's credentials, returning (login, server, password).

    Fails closed (``MT5SessionError``) when the identity is incomplete or the
    stored password cannot be decrypted; the message never names the field, and
    the encryption error is chained for logs only.
    """
    server = (credentials.server or "").strip()
    ciphertext = (credentials.password_encrypted or "").strip()
    if credentials.login is None or not server or not ciphertext:
        raise MT5SessionError("MT5 account credentials are not configured")
    try:
        password = decrypt_secret(ciphertext)
    except EncryptionError as exc:
        raise MT5SessionError("MT5 account credentials could not be used") from exc
    if not password:
        raise MT5SessionError("MT5 account credentials could not be used")
    return credentials.login, server, password


class MT5SessionManager:
    """Process-wide owner of the single MT5 terminal session (see module docs).

    One instance per process. The lock is held for an entire acquire -> read
    span, so concurrent tenants cannot interleave, and an account is only
    switched when the requested identity differs from the one already
    authenticated, so repeated requests for the same tenant cost no extra login.
    """

    def __init__(self, mt5_api: Any | None = None) -> None:
        # The MT5 C extension ships no type stubs, so it is held as Any. It is
        # injectable so tests can drive the whole boundary with a fake instead
        # of patching module globals.
        self._mt5_api: Any = mt5 if mt5_api is None else mt5_api
        # Re-entrant: a read never nests another acquire, but teardown and
        # diagnostics may run where the lock is already held.
        self._lock = threading.RLock()
        self._active_key: tuple[str, int] | None = None

    @property
    def authenticated_account(self) -> tuple[str, int] | None:
        """The (server, login) currently authenticated, or None.

        Exposed for tests and diagnostics only; it carries no credential.
        """
        with self._lock:
            return self._active_key

    @contextmanager
    def acquire(self, credentials: MT5AccountCredentials) -> Iterator[Any]:
        """Yield the MT5 API authenticated as ``credentials``' account.

        The lock is released only after the caller's raw MT5 work finishes, so
        the terminal cannot be re-authenticated underneath a read. Fails closed
        (``MT5SessionError``) when the identity is incomplete, the stored
        password cannot be decrypted, or MT5 rejects the authentication.
        """
        login, server, password = _resolve_credentials(credentials)
        with self._lock:
            key = (server, login)
            if self._active_key != key:
                # A live connection means another account is authenticated: switch
                # it. Otherwise authenticate the terminal straight onto this
                # tenant, so a fresh process never reads someone else's account.
                already_connected = self._active_key is not None
                try:
                    self._authenticate(login, password, server, already_connected=already_connected)
                except MT5SessionError:
                    # The terminal's account state is unknown after a failed
                    # login: forget the identity so the next request starts from
                    # a clean initialize() instead of trusting a stale session.
                    self._active_key = None
                    raise
                self._active_key = key
            try:
                yield self._mt5_api
            except Exception:
                # The connection is in an unknown state after a failed call;
                # drop the cached identity so the next request re-authenticates.
                self._active_key = None
                raise

    def shutdown(self) -> None:
        """Release the terminal session. Never raises: teardown must not fail."""
        with self._lock:
            self._active_key = None
            api = self._mt5_api
        try:
            api.shutdown()
        except Exception:  # expected third-party MT5 failure during teardown only
            pass

    def _authenticate(
        self,
        login: int,
        password: str,
        server: str,
        *,
        already_connected: bool,
    ) -> None:
        """Authenticate the terminal (or switch accounts) onto one tenant.

        Expected third-party failures are translated into ``MT5SessionError``
        with the cause chained for logs; MT5's own ``last_error()`` text is
        included because it carries no credential and is what an operator needs
        to diagnose a rejected login.
        """
        api = self._mt5_api
        try:
            if already_connected:
                logged_in = api.login(login=login, password=password, server=server)
            else:
                logged_in = api.initialize(login=login, password=password, server=server)
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise MT5SessionError("MT5 terminal initialization failed") from exc
        if not logged_in:
            raise MT5SessionError(f"MT5 account authentication failed: {api.last_error()}")
