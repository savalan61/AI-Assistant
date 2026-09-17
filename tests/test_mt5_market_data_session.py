"""A normal market-data miss must not tear down the verified MT5 session.

Known Issue 33: ``MT5MarketDataProvider`` raises for two perfectly normal
answers — the vendor's ``-1`` "symbol not recognized" and an empty rates tuple
("there are no bars for this symbol/window") — and it raised a PLAIN
``ValueError``. That exception escapes the ``acquire`` span, so the session
boundary classified it as an unknown failure, discarded the verified identity,
and made the next request pay initialize()/login() again for a terminal that had
just answered.

What is pinned here, at three levels:

* the provider, over a recording terminal: a client-level "no data" answer is
  raised as ``MT5ClientError`` — still a ``ValueError``, so the existing 404 and
  its exact message are unchanged — while every other outcome (a raising candle
  read, the wrapper's ``No IPC connection`` code, an unreadable or absent
  ``last_error()``) stays a ``RuntimeError`` (the existing 503);
* the session boundary, over the REAL MT5SessionManager and the REAL provider: a
  no-data answer leaves the verified identity in place and the next request
  performs no initialize()/login(), while genuine failures still invalidate the
  session and re-authenticate — with identity verification, tenant isolation and
  fail-closed behaviour untouched;
* the real API route coroutine: 404 for a no-data answer with the session kept,
  503 for an outage with it dropped.

Everything here is offline: no terminal, no network, no database, no real
credentials. No pytest asyncio plugin — the async route is driven with
asyncio.run.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.market_data_router import get_market_data as market_data_route
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import (
    MT5AccountCredentials,
    MT5ClientError,
    MT5SessionError,
    MT5SessionManager,
)
from app.db.models import User, UserRole
from app.providers.mt5_instruments import MT5InstrumentProvider
from app.providers.mt5_market_data import MT5MarketDataProvider
from app.services.instruments import InstrumentService
from app.services.market.market_data_service import MarketDataService

SERVER_A = "BrokerA-Live"
SERVER_B = "BrokerB-Live"
PASSWORD = "mt5-investor-password-under-test"

SYMBOL_WITHOUT_DATA = "XAUUSD.r"
GOOD_SYMBOL = "EURUSD.r"
ANOTHER_GOOD_SYMBOL = "GBPUSD.r"
RATE = (1767000000, 3642.5, 3648.2, 3640.0, 3646.1, 120.0)

# The vendor's own answers, reproduced from the installed package.
UNRECOGNISED_SYMBOL_ERROR = (-1, "Unknown symbol")
NO_IPC_ERROR = (-10004, "No IPC connection")


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
    """A recording stand-in for the MT5 surface the market-data path uses.

    The terminal prices each tenant's OWN symbols from the account this process
    last authenticated — a real terminal behaves exactly that way — so a test can
    prove one tenant never receives another tenant's candle.

    ``symbols_without_data`` are symbols the terminal lists but has no bars for
    (a normal vendor answer: ``None`` plus ``last_error() == -1``);
    ``symbols_with_empty_candles`` answer with an empty tuple instead.
    """

    TIMEFRAME_M1 = 1

    def __init__(
        self,
        symbols_by_login: dict[int, tuple[str, ...]] | None = None,
        *,
        symbols_without_data: tuple[str, ...] = (SYMBOL_WITHOUT_DATA,),
        symbols_with_empty_candles: tuple[str, ...] = (),
        rate_by_login: dict[int, tuple] | None = None,
        rates_error: Exception | None = None,
        last_error_value: object = UNRECOGNISED_SYMBOL_ERROR,
    ) -> None:
        self.calls: list[str] = []
        self.authentications: list[dict[str, object]] = []
        self.symbols_by_login = symbols_by_login or {10001: (SYMBOL_WITHOUT_DATA, GOOD_SYMBOL)}
        self.symbols_without_data = symbols_without_data
        self.symbols_with_empty_candles = symbols_with_empty_candles
        self.rate_by_login = rate_by_login or {}
        self.rates_error = rates_error
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
        return mt5_symbol(symbol) if symbol in self._catalog() else None

    def symbols_get(self) -> object:
        self._record("symbols_get")
        return tuple(mt5_symbol(name) for name in self._catalog())

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> object:
        self._record("copy_rates_from_pos")
        if self.rates_error is not None:
            raise self.rates_error
        if symbol in self.symbols_without_data or symbol not in self._catalog():
            return None
        if symbol in self.symbols_with_empty_candles:
            return ()
        return (self.rate_by_login.get(self.authenticated[1], RATE),)  # type: ignore[index]


def credentials(login: int = 10001, server: str = SERVER_A) -> MT5AccountCredentials:
    return MT5AccountCredentials(login=login, server=server, password_encrypted=encrypt_secret(PASSWORD))


def provider_over(terminal: RecordingTerminal, login: int = 10001, server: str = SERVER_A) -> MT5MarketDataProvider:
    """The REAL provider over the REAL session boundary, fake terminal only."""
    return MT5MarketDataProvider(
        session_manager=MT5SessionManager(mt5_api=terminal),
        credentials=credentials(login, server),
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


def market_data_service(terminal: RecordingTerminal, login: int = 10001, server: str = SERVER_A) -> MarketDataService:
    """The real route's service: candle provider plus the real instrument boundary."""
    manager = MT5SessionManager(mt5_api=terminal)
    return MarketDataService(
        MT5MarketDataProvider(session_manager=manager, credentials=credentials(login, server)),
        instrument_service=InstrumentService(
            MT5InstrumentProvider(session_manager=manager, credentials=credentials(login, server))
        ),
    )


def authentication_calls(terminal: RecordingTerminal, name: str) -> list[dict[str, object]]:
    """Which authenticate call was made with which arguments, in order."""
    return [call for call in terminal.authentications if call["_call"] == name]


# --- the provider: which vendor answer is a client-level miss? -----------------


def test_a_no_data_answer_is_still_a_value_error_with_the_same_message() -> None:
    """The 404 contract is unchanged: same type family, same wording as before."""
    provider = provider_over(RecordingTerminal())

    with pytest.raises(ValueError) as excinfo:
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    assert str(excinfo.value) == f"No candle data returned for {SYMBOL_WITHOUT_DATA}"
    assert isinstance(excinfo.value, MT5ClientError)


def test_an_empty_rates_answer_is_a_client_level_miss_too() -> None:
    """An empty tuple is the terminal saying "no bars", not a failure."""
    terminal = RecordingTerminal(
        symbols_without_data=(), symbols_with_empty_candles=(GOOD_SYMBOL,)
    )

    with pytest.raises(MT5ClientError) as excinfo:
        provider_over(terminal).get_market_data(GOOD_SYMBOL)

    assert str(excinfo.value) == f"No candle data returned for {GOOD_SYMBOL}"


def test_the_vendors_no_ipc_answer_is_an_outage_not_a_missing_candle() -> None:
    terminal = RecordingTerminal(last_error_value=NO_IPC_ERROR)

    with pytest.raises(RuntimeError) as excinfo:
        provider_over(terminal).get_market_data(SYMBOL_WITHOUT_DATA)

    assert not isinstance(excinfo.value, ValueError)
    assert "No IPC connection" in str(excinfo.value)


@pytest.mark.parametrize(
    "unreadable", [None, "not-a-tuple", (), (None, "no code"), ("-1", "not a number"), RuntimeError("gone")]
)
def test_an_unreadable_last_error_fails_closed(unreadable: object) -> None:
    """An error this provider cannot interpret is never retold as "no data"."""
    terminal = RecordingTerminal(last_error_value=unreadable)

    with pytest.raises(RuntimeError) as excinfo:
        provider_over(terminal).get_market_data(SYMBOL_WITHOUT_DATA)

    assert not isinstance(excinfo.value, ValueError)


def test_a_raising_candle_read_is_still_an_outage() -> None:
    terminal = RecordingTerminal(rates_error=OSError("simulated terminal disconnect"))

    with pytest.raises(RuntimeError) as excinfo:
        provider_over(terminal).get_market_data(GOOD_SYMBOL)

    assert str(excinfo.value) == "MT5 market data request failed"
    assert isinstance(excinfo.value.__cause__, OSError)


def test_a_served_candle_is_mapped_exactly_as_before() -> None:
    """The successful path is untouched by the reclassification."""
    candle = provider_over(RecordingTerminal()).get_market_data(GOOD_SYMBOL)

    assert candle.close == Decimal("3646.1")
    assert candle.open == Decimal("3642.5")
    assert candle.volume == 120.0


# --- the session boundary: a no-data answer is not a session failure -----------


def test_a_no_data_answer_does_not_invalidate_the_verified_session() -> None:
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    assert manager.authenticated_account == (SERVER_A, 10001)


def test_an_empty_rates_answer_does_not_invalidate_the_verified_session() -> None:
    terminal = RecordingTerminal(
        symbols_without_data=(), symbols_with_empty_candles=(GOOD_SYMBOL,)
    )
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_market_data(GOOD_SYMBOL)

    assert manager.authenticated_account == (SERVER_A, 10001)


def test_the_next_request_does_not_re_authenticate_after_a_no_data_answer() -> None:
    """The point of Issue 33: the next read costs no initialize()/login()."""
    terminal = RecordingTerminal()
    provider = provider_over(terminal)

    with pytest.raises(ValueError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    assert provider.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")
    assert terminal.calls.count("initialize") == 1
    assert terminal.calls.count("login") == 0


def test_repeated_no_data_answers_never_re_authenticate() -> None:
    terminal = RecordingTerminal(
        {10001: (GOOD_SYMBOL, ANOTHER_GOOD_SYMBOL)}, symbols_without_data=(GOOD_SYMBOL,)
    )
    provider = provider_over(terminal)

    for _ in range(3):
        with pytest.raises(ValueError):
            provider.get_market_data(GOOD_SYMBOL)

    assert provider.get_market_data(ANOTHER_GOOD_SYMBOL).close == Decimal("3646.1")
    assert terminal.calls.count("initialize") == 1
    assert terminal.calls.count("login") == 0


def test_a_genuine_transport_failure_still_invalidates_the_session() -> None:
    terminal = RecordingTerminal(rates_error=OSError("simulated terminal disconnect"))
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(RuntimeError):
        provider.get_market_data(GOOD_SYMBOL)

    assert manager.authenticated_account is None
    terminal.rates_error = None
    assert provider.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")
    assert terminal.calls.count("initialize") == 2


def test_a_no_ipc_answer_still_invalidates_the_session() -> None:
    terminal = RecordingTerminal(last_error_value=NO_IPC_ERROR)
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(RuntimeError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    assert manager.authenticated_account is None
    terminal.last_error_value = UNRECOGNISED_SYMBOL_ERROR
    assert provider.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")
    assert terminal.calls.count("initialize") == 2


def test_identity_verification_still_runs_on_every_read_after_a_miss() -> None:
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)
    provider.get_market_data(GOOD_SYMBOL)
    provider.get_market_data(GOOD_SYMBOL)

    # account_info() is the terminal's own answer, asked for on every read.
    assert terminal.calls.count("account_info") >= 3


def test_a_session_that_moved_away_is_still_detected_after_a_no_data_answer() -> None:
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    # The terminal silently moved to another account (an external re-login).
    terminal.authenticated = (SERVER_B, 99999)

    assert provider.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")
    assert [call["login"] for call in authentication_calls(terminal, "login")] == [10001]
    assert manager.authenticated_account == (SERVER_A, 10001)


def test_an_unconfirmable_terminal_is_still_refused_after_a_no_data_answer() -> None:
    terminal = RecordingTerminal()
    manager = MT5SessionManager(mt5_api=terminal)
    provider = MT5MarketDataProvider(session_manager=manager, credentials=credentials())

    with pytest.raises(ValueError):
        provider.get_market_data(SYMBOL_WITHOUT_DATA)

    # The terminal keeps reporting an account we did not ask for.
    terminal.authenticated = (SERVER_B, 99999)
    terminal.initialize = lambda **kwargs: True  # type: ignore[method-assign]
    terminal.login = lambda **kwargs: True  # type: ignore[method-assign]

    with pytest.raises(MT5SessionError):
        provider.get_market_data(GOOD_SYMBOL)

    assert manager.authenticated_account is None


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


def test_two_tenants_never_receive_each_others_candle() -> None:
    terminal = RecordingTerminal(
        {10001: (GOOD_SYMBOL,), 10002: ("XAUUSD.p",)},
        symbols_without_data=(),
        rate_by_login={10001: RATE, 10002: (1767000000, 9.0, 9.5, 8.5, 9.25, 7.0)},
    )
    manager = MT5SessionManager(mt5_api=terminal)
    tenant_a = MT5MarketDataProvider(session_manager=manager, credentials=credentials(10001, SERVER_A))
    tenant_b = MT5MarketDataProvider(session_manager=manager, credentials=credentials(10002, SERVER_B))

    assert tenant_a.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")
    assert tenant_b.get_market_data("XAUUSD.p").close == Decimal("9.25")
    assert tenant_a.get_market_data(GOOD_SYMBOL).close == Decimal("3646.1")

    logins = [call["login"] for call in authentication_calls(terminal, "login")]
    assert logins == [10002, 10001]  # B switched it, A switched it back
    assert manager.authenticated_account == (SERVER_A, 10001)


# --- the real API route --------------------------------------------------------


def test_the_endpoint_still_answers_404_and_keeps_the_session() -> None:
    """End to end through the real route coroutine: 404, no re-authentication."""
    terminal = RecordingTerminal()
    service = market_data_service(terminal)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(market_data_route(SYMBOL_WITHOUT_DATA, make_user(), service))

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Market data unavailable for the requested symbol"

    response = asyncio.run(market_data_route(GOOD_SYMBOL, make_user(), service))

    assert response.close == Decimal("3646.1")
    assert terminal.calls.count("initialize") == 1
    assert terminal.calls.count("login") == 0


def test_the_endpoint_still_answers_503_and_invalidates_the_session() -> None:
    terminal = RecordingTerminal(rates_error=OSError("simulated terminal disconnect"))
    manager = MT5SessionManager(mt5_api=terminal)
    service = MarketDataService(
        MT5MarketDataProvider(session_manager=manager, credentials=credentials())
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(market_data_route(GOOD_SYMBOL, make_user(), service))

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Market data service temporarily unavailable"
    assert manager.authenticated_account is None
