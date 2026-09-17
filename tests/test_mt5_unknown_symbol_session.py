"""A normal missing-symbol answer must not tear down the verified MT5 session.

Known Issue 32: the session boundary cleared its verified identity for *any*
exception escaping a read span, so a client-level "this terminal does not offer
that symbol" (a ValueError on its way to the API's 404) dropped the identity and
made the next request pay initialize()/login() again — for a session that had
just answered normally.

What is pinned here, at three levels:

* the provider, over a recording terminal: the vendor's two outcomes are told
  apart. Against the installed package (MetaTrader5 5.0.6180) a symbol read that
  cannot reach the terminal does NOT raise — it returns None and reports its own
  transport failure through last_error(), e.g. (-10004, 'No IPC connection'),
  while the terminal's answer about the symbol is -1 or a non-negative code such
  as 4301 (ERR_MARKET_UNKNOWN_SYMBOL). Only the latter is a client miss, raised
  as MT5ClientError (still a ValueError, so the 404 contract is unchanged);
  anything else stays a RuntimeError (the existing 503);
* the session boundary, over the REAL MT5SessionManager and the REAL provider:
  a miss leaves the verified identity in place and the next request performs no
  initialize()/login(), while a transport failure still invalidates the session
  and re-authenticates — and identity verification, tenant isolation and
  fail-closed behaviour are unchanged;
* the real API route coroutine: 404 for a miss, 503 for an outage, with the
  session surviving the first and being dropped for the second.

Everything here is offline: no terminal, no network, no database, no real
credentials. No pytest asyncio plugin — the async routes are driven with
asyncio.run.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.instruments_router import resolve_instrument
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import (
    MT5AccountCredentials,
    MT5ClientError,
    MT5SessionError,
    MT5SessionManager,
)
from app.db.models import User, UserRole
from app.providers.mt5_instruments import MT5InstrumentProvider
from app.services.instruments import InstrumentService

SERVER_A = "BrokerA-Live"
SERVER_B = "BrokerB-Live"
PASSWORD = "mt5-investor-password-under-test"

# The vendor's own answers: reproduced offline from the installed package and
# verified against the live terminal (see CURRENT_CHECKPOINT.md item 32).
UNKNOWN_SYMBOL_ERROR = (-1, "Unknown symbol")
NO_IPC_ERROR = (-10004, "No IPC connection")
TERMINAL_UNKNOWN_SYMBOL_CODE = 4301  # ERR_MARKET_UNKNOWN_SYMBOL
# "Terminal: Not found" — what the LIVE terminal actually reported for a
# symbol_info() miss (a code below the client floor, hence the explicit rule).
TERMINAL_NOT_FOUND_ERROR = (-4, "Terminal: Not found")


@pytest.fixture(autouse=True)
def test_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test-only encryption key, so the tenant credentials round-trip for real."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


def mt5_symbol(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        digits=2,
        trade_mode=4,
        description=f"{name} instrument",
        path="Metals",
        currency_base="XAU",
        currency_profit="USD",
    )


class RecordingTerminal:
    """A recording stand-in for the MT5 surface the instrument path uses.

    Serves each tenant its OWN catalog from the account this process last
    authenticated — a real terminal behaves exactly that way — so a test can
    prove one tenant never resolves against another tenant's symbols.

    ``symbol_error`` reproduces the vendor raising (an IPC/terminal failure that
    does raise); otherwise an unknown symbol reproduces the vendor's documented
    answer of ``None`` plus ``last_error()``.
    """

    def __init__(
        self,
        symbols_by_login: dict[int, tuple[str, ...]] | None = None,
        *,
        symbol_error: Exception | None = None,
        last_error_value: object = UNKNOWN_SYMBOL_ERROR,
    ) -> None:
        self.calls: list[str] = []
        self.authentications: list[dict[str, object]] = []
        self.symbols_by_login = symbols_by_login or {10001: ("XAUUSD.r",)}
        self.symbol_error = symbol_error
        self.last_error_value = last_error_value
        self.authenticated: tuple[str, int] | None = None

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def _authenticate(self, name: str, kwargs: dict[str, object]) -> None:
        self.authentications.append({"_call": name, **kwargs})
        self.authenticated = (str(kwargs["server"]), int(kwargs["login"]))  # type: ignore[arg-type]

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self._authenticate("initialize", kwargs)
        return True

    def login(self, **kwargs: object) -> object:
        self._record("login")
        self._authenticate("login", kwargs)
        return True

    def shutdown(self) -> None:
        self._record("shutdown")

    def last_error(self) -> object:
        self._record("last_error")
        if isinstance(self.last_error_value, Exception):
            raise self.last_error_value
        return self.last_error_value

    def account_info(self) -> object:
        self._record("account_info")
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return None
        server, login = self.authenticated
        return SimpleNamespace(login=login, server=server)

    def _catalog(self) -> tuple[str, ...]:
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return ()
        return self.symbols_by_login.get(self.authenticated[1], ())

    def symbol_info(self, symbol: str) -> object:
        self._record("symbol_info")
        if self.symbol_error is not None:
            raise self.symbol_error
        return mt5_symbol(symbol) if symbol in self._catalog() else None

    def symbols_get(self) -> object:
        self._record("symbols_get")
        return tuple(mt5_symbol(name) for name in self._catalog())


def authentication_calls(terminal: RecordingTerminal, name: str) -> list[dict[str, object]]:
    """Which authenticate call was made with which arguments, in order.

    Tenant isolation is about *which login the terminal was switched to*, so the
    assertions below read the recorded arguments rather than a call count.
    """
    return [call for call in terminal.authentications if call["_call"] == name]


def credentials(login: int = 10001, server: str = SERVER_A) -> MT5AccountCredentials:
    return MT5AccountCredentials(login=login, server=server, password_encrypted=encrypt_secret(PASSWORD))


def provider_over(terminal: RecordingTerminal, login: int = 10001, server: str = SERVER_A) -> MT5InstrumentProvider:
    """The REAL provider over the REAL session boundary, fake terminal only."""
    return MT5InstrumentProvider(
        session_manager=MT5SessionManager(mt5_api=terminal),
        credentials=credentials(login=login, server=server),
    )


def make_user(login: str = "10001", user_id: int = 7) -> User:
    return User(
        id=user_id,
        broker_id=1,
        login=login,
        password_hash="x-not-a-real-hash",
        is_active=True,
        role=UserRole.CUSTOMER,
    )


# --- the provider: which vendor answer is a client miss? -----------------------


def test_a_missing_symbol_is_still_a_value_error_with_the_same_message() -> None:
    """The 404 contract is unchanged: same type family, same wording as before."""
    provider = provider_over(RecordingTerminal())

    with pytest.raises(ValueError) as excinfo:
        provider.get_instrument("NOSUCH")

    assert str(excinfo.value) == "MT5 does not offer instrument NOSUCH"
    assert isinstance(excinfo.value, MT5ClientError)


def test_the_unknown_symbol_answer_the_terminal_reports_is_a_client_miss() -> None:
    """Every spelling the terminal uses for "no such symbol" is a client error."""
    for error in (
        UNKNOWN_SYMBOL_ERROR,
        (TERMINAL_UNKNOWN_SYMBOL_CODE, "unknown symbol"),
        TERMINAL_NOT_FOUND_ERROR,  # the live terminal's own answer, code -4
    ):
        terminal = RecordingTerminal(last_error_value=error)
        with pytest.raises(MT5ClientError):
            provider_over(terminal).get_instrument("NOSUCH")


def test_the_vendors_no_ipc_answer_is_an_outage_not_a_missing_instrument() -> None:
    """The regression the vendor probe found: an unreachable terminal returns
    None too, so the answer must be read from last_error() rather than assumed."""
    terminal = RecordingTerminal(last_error_value=NO_IPC_ERROR)

    with pytest.raises(RuntimeError) as excinfo:
        provider_over(terminal).get_instrument("XAUUSD")

    assert not isinstance(excinfo.value, ValueError)
    assert "No IPC connection" in str(excinfo.value)


def test_terminal_range_failures_below_the_not_found_code_stay_outages() -> None:
    """Only the specific not-found code is exempted below the floor, nothing else."""
    for error in ((-3, "Terminal: some other failure"), (-5, "Terminal: gone"), (-2, "IPC refused")):
        terminal = RecordingTerminal(last_error_value=error)
        with pytest.raises(RuntimeError) as excinfo:
            provider_over(terminal).get_instrument("NOSUCH")
        assert not isinstance(excinfo.value, ValueError)


@pytest.mark.parametrize("unreadable", [None, "not-a-tuple", (-10004,), (None, "no code"), RuntimeError("gone")])
def test_an_unreadable_last_error_fails_closed(unreadable: object) -> None:
    """An error this module cannot interpret is never reported as "not offered"."""
    terminal = RecordingTerminal(last_error_value=unreadable)

    with pytest.raises(RuntimeError) as excinfo:
        provider_over(terminal).get_instrument("NOSUCH")

    assert not isinstance(excinfo.value, ValueError)


def test_a_catalog_request_that_cannot_reach_the_terminal_is_still_an_outage() -> None:
    """The listing path is untouched: an unavailable catalog stays a RuntimeError."""
    terminal = RecordingTerminal()
    terminal.symbols_get = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        provider_over(terminal).list_instruments()


# --- the session boundary: a client miss is not a session failure --------------


def test_a_missing_symbol_does_not_invalidate_the_verified_session() -> None:
    """The identity the session verified is still there after a client miss."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5InstrumentProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_instrument("NOSUCH")

    assert manager.authenticated_account == (SERVER_A, 10001)


def test_the_next_request_does_not_re_authenticate_after_a_missing_symbol() -> None:
    """The whole point of Issue 32: the next read costs no initialize()/login()."""
    terminal = RecordingTerminal()
    provider = provider_over(terminal)

    with pytest.raises(ValueError):
        provider.get_instrument("NOSUCH")

    assert provider.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert terminal.calls.count("initialize") == 1  # one authentication, ever
    assert terminal.calls.count("login") == 0


def test_repeated_misses_never_re_authenticate() -> None:
    """N misses in a row still cost exactly one authentication."""
    terminal = RecordingTerminal()
    provider = provider_over(terminal)

    for _ in range(3):
        with pytest.raises(ValueError):
            provider.get_instrument("NOSUCH")

    assert provider.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert terminal.calls.count("initialize") == 1
    assert terminal.calls.count("login") == 0


def test_a_missing_symbol_does_not_bypass_identity_verification() -> None:
    """Keeping the cache does not mean trusting it: every acquire still verifies."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5InstrumentProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_instrument("NOSUCH")
    provider.get_instrument("XAUUSD.r")
    provider.get_instrument("XAUUSD.r")

    # account_info() is the terminal's own answer, asked for on every read.
    assert terminal.calls.count("account_info") >= 3


def test_a_session_that_moved_away_is_still_detected_after_a_miss() -> None:
    """A client miss must not make a stale cache look fresh."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5InstrumentProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_instrument("NOSUCH")

    # The terminal silently moved to another account (an external re-login).
    terminal.authenticated = (SERVER_B, 99999)

    assert provider.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert [call["login"] for call in authentication_calls(terminal, "login")] == [10001]
    assert manager.authenticated_account == (SERVER_A, 10001)


def test_an_unconfirmable_terminal_is_still_refused_after_a_miss() -> None:
    """Fail-closed behaviour is unchanged by the client-miss exemption."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5InstrumentProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_instrument("NOSUCH")

    # The terminal keeps reporting an account we did not ask for.
    terminal.authenticated = (SERVER_B, 99999)
    terminal.initialize = lambda **kwargs: True  # type: ignore[method-assign]  # login never takes effect
    terminal.login = lambda **kwargs: True  # type: ignore[method-assign]

    with pytest.raises(MT5SessionError):
        provider.get_instrument("XAUUSD.r")

    assert manager.authenticated_account is None


def test_a_genuine_transport_failure_still_invalidates_the_session() -> None:
    """An outage that raises keeps the old contract: identity dropped, next read re-auths."""
    terminal = RecordingTerminal(symbol_error=OSError("simulated terminal disconnect"))
    provider = provider_over(terminal)

    with pytest.raises(RuntimeError):
        provider.get_instrument("XAUUSD.r")

    terminal.symbol_error = None
    assert provider.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert terminal.calls.count("initialize") == 2


def test_a_no_ipc_failure_still_invalidates_the_session() -> None:
    """The vendor's None-plus-no-IPC answer is also treated as a session failure."""
    terminal = RecordingTerminal(last_error_value=NO_IPC_ERROR)
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5InstrumentProvider(session_manager=manager, credentials=credentials())

    # The terminal is connected to nobody: the read answers None and explains
    # itself as an IPC failure rather than a missing symbol.
    with pytest.raises(RuntimeError):
        provider.get_instrument("EURUSD")

    assert manager.authenticated_account is None
    terminal.last_error_value = UNKNOWN_SYMBOL_ERROR
    assert provider.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert terminal.calls.count("initialize") == 2


def test_an_unrelated_value_error_still_invalidates_the_session() -> None:
    """The exemption is narrow: only the classified client error keeps the session."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    creds = credentials()

    with manager.acquire(creds):
        pass

    with pytest.raises(ValueError):
        with manager.acquire(creds):
            raise ValueError("some other client-side failure")

    assert manager.authenticated_account is None


# --- tenant isolation ----------------------------------------------------------


def test_a_miss_does_not_let_another_tenant_see_this_tenants_catalog() -> None:
    """A kept identity is still one tenant's identity: the next tenant switches it."""
    terminal = RecordingTerminal({10001: ("XAUUSD.r",), 10002: ("XAUUSD.p",)})
    manager = MT5SessionManager(mt5_api=terminal)
    tenant_a = MT5InstrumentProvider(session_manager=manager, credentials=credentials(10001, SERVER_A))
    tenant_b = MT5InstrumentProvider(session_manager=manager, credentials=credentials(10002, SERVER_B))

    with pytest.raises(ValueError):
        tenant_a.get_instrument("NOSUCH")
    # Each tenant reads its OWN broker's spelling, never the other's catalog.
    assert tenant_a.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    assert tenant_b.get_instrument("XAUUSD.p").symbol == "XAUUSD.p"
    assert tenant_a.get_instrument("XAUUSD.r").symbol == "XAUUSD.r"

    logins = [call["login"] for call in authentication_calls(terminal, "login")]
    assert logins == [10002, 10001]  # B switched it, A switched it back
    assert manager.authenticated_account == (SERVER_A, 10001)


def test_a_fresh_process_starts_with_no_identity() -> None:
    """A kept identity is request-local state in one manager, never global."""
    terminal = RecordingTerminal()
    provider_over(terminal)
    with pytest.raises(ValueError):
        provider_over(terminal).get_instrument("NOSUCH")

    fresh = MT5SessionManager(mt5_api=terminal)
    assert fresh.authenticated_account is None


# --- the real API route --------------------------------------------------------


def test_the_endpoint_still_answers_404_and_keeps_the_session() -> None:
    """End to end through the real route coroutine: 404, no re-authentication."""
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    service = InstrumentService(
        MT5InstrumentProvider(session_manager=manager, credentials=credentials())
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(resolve_instrument("NOSUCH", make_user(), service))

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Instrument unavailable for the requested symbol"
    assert manager.authenticated_account == (SERVER_A, 10001)

    response = asyncio.run(resolve_instrument("XAUUSD", make_user(), service))

    assert response.symbol == "XAUUSD.r"  # the broker's own spelling
    assert terminal.calls.count("initialize") == 1
    assert terminal.calls.count("login") == 0


def test_the_endpoint_still_answers_503_and_invalidates_the_session() -> None:
    terminal = RecordingTerminal(symbol_error=OSError("simulated terminal disconnect"))
    manager = MT5SessionManager(mt5_api=terminal)
    service = InstrumentService(
        MT5InstrumentProvider(session_manager=manager, credentials=credentials())
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(resolve_instrument("XAUUSD", make_user(), service))

    assert excinfo.value.status_code == 503
    assert manager.authenticated_account is None


def test_two_users_do_not_share_one_session_identity() -> None:
    """Each authenticated user reads its own account's catalog, in any order."""
    terminal = RecordingTerminal({10001: ("XAUUSD.r",), 10002: ("XAUUSD.p",)})
    manager = MT5SessionManager(mt5_api=terminal)
    user_a = make_user("10001", 7)
    user_b = make_user("10002", 8)
    service_a = InstrumentService(
        MT5InstrumentProvider(session_manager=manager, credentials=credentials(10001, SERVER_A))
    )
    service_b = InstrumentService(
        MT5InstrumentProvider(session_manager=manager, credentials=credentials(10002, SERVER_B))
    )

    first = asyncio.run(resolve_instrument("XAUUSD", user_a, service_a))
    second = asyncio.run(resolve_instrument("XAUUSD", user_b, service_b))
    third = asyncio.run(resolve_instrument("XAUUSD", user_a, service_a))

    assert (first.symbol, second.symbol, third.symbol) == ("XAUUSD.r", "XAUUSD.p", "XAUUSD.r")
