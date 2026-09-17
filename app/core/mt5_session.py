"""Tenant-scoped MT5 session boundary.

Why this module exists
----------------------

MT5 is not a multi-session API. The MetaTrader5 Python package authenticates
**one account per process**: ``initialize()`` and ``login()`` act on the single
global terminal connection, and any further ``login()`` switches that one
connection to another account. There is therefore no way to hold two customers'
sessions at the same time inside one Python process.

Left unmanaged, that produces a real cross-customer leak: request A authenticates
customer A, request B authenticates customer B, and A's read then silently returns
B's data. This module is the smallest boundary that makes that impossible.

Design
------

``MT5SessionManager`` owns every piece of process-global MT5 state:

* one re-entrant lock, held for a whole ``acquire`` -> raw-read span;
* the identity currently authenticated (``server``, ``login``);
* the MT5 API seam (``MetaTrader5`` by default, injectable for tests).

A read for customer A either finds A already authenticated — reuse, no login — or
switches the terminal to A *while holding the lock*, runs its raw MT5 calls
inside the ``with`` block, and releases. Because the key is asserted before
every call and the lock covers the call itself, a session belonging to customer B
can never serve A's read, and an account switch can never land in the middle of
another customer's read.

The unavoidable cost is serialization: every MT5 read in the process waits for
the one terminal. That is a property of the MT5 Python API, not a choice made
here — it is documented rather than hidden, and it is why providers stay cheap
per-request objects while only this manager is process-wide.

Verification
------------

The cached identity is *verified*, not trusted. ``account_info()`` is the
terminal's own answer about the account it is currently on, and it is the only
way to observe a session that changed outside this manager: a broker-side
re-login, a manual login at the terminal, or another OS process sharing the same
terminal. Before a read is served, the terminal must report the requested
``(server, login)``; a mismatch re-authenticates under the lock, and a session
whose identity still cannot be confirmed fails closed rather than ever serving a
read the customer may not own. Unreadable account data counts as "not confirmed",
never as a match. Verification costs one extra local ``account_info()`` call per
acquire, which is the deliberate price of not trusting a cache inside a
customer-isolation boundary.

A failure reported *about the request* is not a session failure. When the
terminal answers normally and the answer is a client-level result (for example
"this terminal has no such symbol"), the provider raises ``MT5ClientError`` —
still a ``ValueError``, so the application's 404 behaviour is unchanged — and
the verified identity is deliberately KEPT, because the session that produced
the answer is healthy and discarding it would force an unnecessary
``initialize()``/``login()`` on the next request. Anything else escaping the
span (a transport error, an unreadable terminal, a malformed vendor record)
still drops the identity and fails closed.

Terminal and timeout
--------------------

The authenticated call shape is explicit, not implicit: an optional terminal
executable pins WHICH terminal is driven (it matters as soon as a machine has
more than one installed), and an optional IPC timeout bounds how long one
``initialize``/``login`` may block — a login that hangs holds the process-wide
lock, so every customer waits behind it. Unset, both fall back to the package's
own behaviour (find the terminal itself, apply its 60 s timeout), which is the
development default; a deployment sets them (see ``MT5_TERMINAL_PATH`` and
``MT5_TIMEOUT_SECONDS``).

Credentials
-----------

The plaintext password never lives outside this boundary. The composition root
resolves the customer's *encrypted* password straight from the database (see
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


class MT5ClientError(ValueError):
    """MT5 answered normally and the answer itself is a client-level result.

    The terminal was reachable and answered the request; what it reported is
    about the *request* — most commonly "this terminal has no such symbol" —
    and says nothing about the health of the session. It stays a ``ValueError``
    because that is the provider contract for a client error (the API layer
    still maps it to the existing 404), and the session boundary reads the type
    only to tell this apart from a transport failure: a session that answered a
    client-level result is healthy, so its verified identity is kept instead of
    being discarded and re-authenticated by the next request.

    Raised by a provider that can distinguish the two (see
    ``MT5InstrumentProvider.get_instrument``), inside an ``acquire`` span; never
    raised by the session boundary itself. Every other exception escaping that
    span keeps the previous meaning: the connection is in an unknown state and
    the cached identity is dropped.
    """


class MT5SessionError(RuntimeError):
    """The customer's MT5 session could not be established.

    A ``RuntimeError`` subclass on purpose: MT5 availability failures already
    map to HTTP 503 at the API boundary, so no API code needs to know about
    this type, and the message stays generic — which credential was missing,
    undecryptable or rejected is never disclosed.
    """


@dataclass(frozen=True)
class MT5AccountCredentials:
    """A customer's MT5 identity, resolved from the authenticated database rows.

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
    """Validate one customer's credentials, returning (login, server, password).

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
    span, so concurrent customers cannot interleave, and an account is only
    switched when the requested identity differs from the one already
    authenticated, so repeated requests for the same customer cost no extra login.
    The authenticated identity is verified against the terminal before a read is
    served, so a session that changed outside this manager can never be trusted.
    """

    def __init__(
        self,
        mt5_api: Any | None = None,
        *,
        terminal_path: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        # The MT5 C extension ships no type stubs, so it is held as Any. It is
        # injectable so tests can drive the whole boundary with a fake instead
        # of patching module globals.
        self._mt5_api: Any = mt5 if mt5_api is None else mt5_api
        # Re-entrant: a read never nests another acquire, but teardown and
        # diagnostics may run where the lock is already held.
        self._lock = threading.RLock()
        self._active_key: tuple[str, int] | None = None
        # An explicit terminal executable and a bounded IPC timeout, both
        # optional (see the module docs). A blank path and a non-positive
        # timeout both mean "use the package's own default".
        self._terminal_path = (terminal_path or "").strip() or None
        self._timeout_ms = timeout_ms if (timeout_ms or 0) > 0 else None

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
        password cannot be decrypted, MT5 rejects the authentication, or the
        terminal cannot be confirmed to be on the requested account.
        """
        login, server, password = _resolve_credentials(credentials)
        with self._lock:
            key = (server, login)
            # One verification in the happy path, because a cache hit is only
            # accepted after the terminal itself confirms it. Either no account is
            # authenticated for this customer (no cache entry) or the terminal moved
            # away from it (stale entry): both mean "authenticate now", under the
            # lock, so an account switch can never land inside another customer's
            # read.
            if self._active_key != key or not self._identity_confirmed(key):
                # A live connection means another account is authenticated: switch
                # it. Otherwise authenticate the terminal straight onto this
                # customer, so a fresh process never reads someone else's account.
                already_connected = self._active_key is not None
                self._authenticate_for(key, login, password, server, already_connected=already_connected)
                # What the terminal reports must now be the customer this read is
                # for. An identity that still cannot be confirmed is refused (fail
                # closed) and forgotten, so the next request authenticates from a
                # clean initialize() instead of trusting an unknown session.
                if not self._identity_confirmed(key):
                    self._active_key = None
                    raise MT5SessionError("MT5 session identity could not be confirmed")

            try:
                yield self._mt5_api
            except MT5ClientError:
                # The terminal answered the request; the answer is a client-level
                # result ("no such symbol"), not a sign that the session is
                # unusable. Keeping the verified identity means the next request
                # costs no initialize()/login(). Tenant isolation is unaffected:
                # every acquire still confirms the identity with the terminal
                # before a read is served, and an identity that cannot be
                # confirmed is still refused (and forgotten) below.
                raise
            except Exception:
                # Any other failure — a transport error, an unreadable terminal,
                # a malformed vendor record — leaves the connection in an
                # unknown state; drop the cached identity so the next request
                # re-authenticates.
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

    def _authenticate_for(
        self,
        key: tuple[str, int],
        login: int,
        password: str,
        server: str,
        *,
        already_connected: bool,
    ) -> None:
        """Authenticate onto one customer and cache the identity on success.

        The terminal's account state is unknown after a failed authentication, so
        the identity is forgotten rather than cached: the next request starts from
        a clean initialize() instead of trusting a stale session.
        """
        try:
            self._authenticate(login, password, server, already_connected=already_connected)
        except MT5SessionError:
            self._active_key = None
            raise
        self._active_key = key

    def _identity_confirmed(self, key: tuple[str, int]) -> bool:
        """Is the terminal actually authenticated on ``key``?

        ``account_info()`` is the terminal's own answer about the account it is
        currently on, so it is the only way to notice a session that changed
        outside this manager. Unreadable, absent or malformed account data counts
        as "not confirmed" — never as a match, because a false match would serve
        one customer's read from another customer's account.

        The login number must match exactly. The server is part of the identity
        too, and is compared case-insensitively so a cosmetic difference cannot
        lock a working customer out; a terminal that reports no server name at all
        leaves only the login to compare.
        """
        server, login = key
        info = self._terminal_account_info()
        if info is None:
            return False
        try:
            actual_login = int(info.login)
        except (AttributeError, TypeError, ValueError):
            return False
        if actual_login != login:
            return False
        actual_server = str(getattr(info, "server", "") or "").strip()
        return not actual_server or actual_server.casefold() == server.casefold()

    def _terminal_account_info(self) -> Any | None:
        """The terminal's own account record, or None when it cannot be read."""
        try:
            return self._mt5_api.account_info()
        except Exception:  # expected third-party MT5 failure at this boundary only
            return None

    def _authenticate(
        self,
        login: int,
        password: str,
        server: str,
        *,
        already_connected: bool,
    ) -> None:
        """Authenticate the terminal (or switch accounts) onto one customer.

        Expected third-party failures are translated into ``MT5SessionError``
        with the cause chained for logs; MT5's own ``last_error()`` text is
        included because it carries no credential and is what an operator needs
        to diagnose a rejected login.
        """
        api = self._mt5_api
        try:
            if already_connected:
                logged_in = api.login(
                    login=login, password=password, server=server, **self._timeout_kwargs()
                )
            else:
                logged_in = api.initialize(**self._initialize_kwargs(login, password, server))
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise MT5SessionError("MT5 terminal initialization failed") from exc
        if not logged_in:
            raise MT5SessionError(f"MT5 account authentication failed: {api.last_error()}")

    def _initialize_kwargs(self, login: int, password: str, server: str) -> dict[str, Any]:
        """The initialize() call, carrying the deployment's terminal/timeout when set.

        ``path`` is the documented way to bind this process to ONE terminal
        executable; ``timeout`` is in milliseconds, which is MT5's own unit.
        Both are omitted when unconfigured, so the package's defaults apply.
        """
        kwargs: dict[str, Any] = {"login": login, "password": password, "server": server}
        if self._terminal_path is not None:
            kwargs["path"] = self._terminal_path
        kwargs.update(self._timeout_kwargs())
        return kwargs

    def _timeout_kwargs(self) -> dict[str, int]:
        """The millisecond IPC timeout for login()/initialize(), when configured."""
        if self._timeout_ms is None:
            return {}
        return {"timeout": self._timeout_ms}
