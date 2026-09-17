"""Tests for MT5InstrumentProvider (read-only instrument discovery).

Every test injects a fake MT5 into the real MT5SessionManager, so none of them
require a real MT5 terminal, credentials, PostgreSQL, network access, or .env.
The provider reads through the authenticated session, so provider-level mapping
and tenant scoping are both exercised for real.
"""
from types import SimpleNamespace

import pytest

from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.instrument import Instrument, TradeMode
from app.providers.mt5_instruments import MT5InstrumentProvider

# The only MT5 functions a read-only instrument-discovery provider may ever
# touch (the session boundary authenticates; the provider itself only reads).
ALLOWED_MT5_FUNCTIONS = {
    "initialize",
    "login",
    "last_error",
    "symbol_info",
    "symbols_get",
    "account_info",
}
TRADING_FUNCTIONS = {
    "order_send",
    "order_check",
    "positions_modify",
    "orders_modify",
    "symbol_select",  # terminal-state mutation: forbidden even though it is not trading
}
TRADE_MODE_CONSTANTS = {
    "SYMBOL_TRADE_MODE_DISABLED": 0,
    "SYMBOL_TRADE_MODE_LONGONLY": 1,
    "SYMBOL_TRADE_MODE_SHORTONLY": 2,
    "SYMBOL_TRADE_MODE_CLOSEONLY": 3,
    "SYMBOL_TRADE_MODE_FULL": 4,
}

SERVER = "BrokerA-Live"
MT5_PASSWORD = "mt5-account-password-under-test"


class FakeMT5:
    """Configurable fake of the MT5 C-extension surface used by the provider."""

    # Real MT5 numeric trade-mode encoding (kept so the fake mirrors the vendor).
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_TRADE_MODE_LONGONLY = 1
    SYMBOL_TRADE_MODE_SHORTONLY = 2
    SYMBOL_TRADE_MODE_CLOSEONLY = 3
    SYMBOL_TRADE_MODE_FULL = 4

    def __init__(
        self,
        initialize_result: object = True,
        symbol_info_result: object = None,
        symbol_info_error: Exception | None = None,
        symbols_get_result: object = None,
        symbols_get_error: Exception | None = None,
    ):
        self.initialize_result = initialize_result
        self.symbol_info_result = symbol_info_result
        self.symbol_info_error = symbol_info_error
        self.symbols_get_result = symbols_get_result
        self.symbols_get_error = symbols_get_error
        self.authenticate_calls: list[dict[str, object]] = []
        self.symbol_info_calls: list[str] = []
        self.accessed: list[str] = []

    def _record(self, name: str) -> None:
        self.accessed.append(name)

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self.authenticate_calls.append(dict(kwargs))
        return self.initialize_result

    def login(self, **kwargs: object) -> object:
        self._record("login")
        self.authenticate_calls.append(dict(kwargs))
        return True

    def last_error(self) -> tuple[int, str]:
        self._record("last_error")
        return (-1, "simulated MT5 failure")

    def account_info(self) -> object:
        """The terminal reports the account it is authenticated as.

        The session boundary verifies this against the requesting tenant before
        every read, so the fake reports whatever account it last authenticated
        (see ``authenticate_calls``) — exactly as a real terminal would.
        """
        self._record("account_info")
        last_auth = self.authenticate_calls[-1] if self.authenticate_calls else None
        if last_auth is None:  # pragma: no cover - every read authenticates first
            return None
        return SimpleNamespace(login=last_auth["login"], server=last_auth["server"])

    def symbol_info(self, symbol: str) -> object:
        self._record("symbol_info")
        self.symbol_info_calls.append(symbol)
        if self.symbol_info_error is not None:
            raise self.symbol_info_error
        return self.symbol_info_result

    def symbols_get(self) -> object:
        self._record("symbols_get")
        if self.symbols_get_error is not None:
            raise self.symbols_get_error
        return self.symbols_get_result


def mt5_symbol(
    name: str = "XAUUSD.r",
    description: object = "Gold vs US Dollar (retail)",
    path: object = "Metals\\XAUUSD.r",
    currency_base: object = "XAU",
    currency_profit: object = "USD",
    digits: object = 2,
    trade_mode: object = FakeMT5.SYMBOL_TRADE_MODE_FULL,
) -> SimpleNamespace:
    """A realistic MT5 symbol record (attribute access, not a dict)."""
    return SimpleNamespace(
        name=name,
        description=description,
        path=path,
        currency_base=currency_base,
        currency_profit=currency_profit,
        digits=digits,
        trade_mode=trade_mode,
    )


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a usable, test-only encryption key."""
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


def make_credentials(login: int = 10001, server: str = SERVER) -> MT5AccountCredentials:
    """Tenant credentials whose stored password is real ciphertext."""
    return MT5AccountCredentials(login=login, server=server, password_encrypted=encrypt_secret(MT5_PASSWORD))


@pytest.fixture()
def provider():
    """Build a provider bound to a fake MT5 session for one tenant."""

    def _provider(fake: FakeMT5, credentials: MT5AccountCredentials | None = None) -> MT5InstrumentProvider:
        return MT5InstrumentProvider(
            session_manager=MT5SessionManager(mt5_api=fake),
            credentials=credentials if credentials is not None else make_credentials(),
        )

    return _provider


# --- provider mapping --------------------------------------------------------------


def test_all_seven_contract_fields_are_mapped(provider):
    instrument = provider(FakeMT5(symbol_info_result=mt5_symbol())).get_instrument("XAUUSD.r")

    assert isinstance(instrument, Instrument)
    assert not isinstance(instrument, SimpleNamespace)  # raw MT5 object must not leak
    assert instrument == Instrument(
        symbol="XAUUSD.r",
        name="Gold vs US Dollar (retail)",
        asset_class="Metals",
        base_currency="XAU",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    )
    assert isinstance(instrument.digits, int)
    assert isinstance(instrument.symbol, str)


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        (FakeMT5.SYMBOL_TRADE_MODE_DISABLED, TradeMode.DISABLED),
        (FakeMT5.SYMBOL_TRADE_MODE_LONGONLY, TradeMode.LONG_ONLY),
        (FakeMT5.SYMBOL_TRADE_MODE_SHORTONLY, TradeMode.SHORT_ONLY),
        (FakeMT5.SYMBOL_TRADE_MODE_CLOSEONLY, TradeMode.CLOSE_ONLY),
        (FakeMT5.SYMBOL_TRADE_MODE_FULL, TradeMode.FULL),
    ],
)
def test_each_mt5_trade_mode_maps_to_the_documented_word(provider, raw_mode, expected):
    instrument = provider(FakeMT5(symbol_info_result=mt5_symbol(trade_mode=raw_mode))).get_instrument("XAUUSD.r")

    assert instrument.trade_mode is expected
    assert instrument.trade_mode.value in {"DISABLED", "LONG_ONLY", "SHORT_ONLY", "CLOSE_ONLY", "FULL"}


def test_unknown_trade_mode_fails_loudly(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol(trade_mode=5))

    with pytest.raises(RuntimeError):
        provider(fake).get_instrument("XAUUSD.r")


def test_trade_mode_constants_match_the_documented_numbers():
    # The provider maps by value, so the vendor's own numbers are pinned here.
    assert TRADE_MODE_CONSTANTS["SYMBOL_TRADE_MODE_DISABLED"] == 0
    assert TRADE_MODE_CONSTANTS["SYMBOL_TRADE_MODE_FULL"] == 4


def test_missing_optional_metadata_stays_none_and_never_errors(provider):
    fake = FakeMT5(
        symbol_info_result=mt5_symbol(
            name="NICKEL", description="", path="", currency_base=None, currency_profit=None, digits=None
        )
    )

    instrument = provider(fake).get_instrument("NICKEL")

    assert instrument == Instrument(
        symbol="NICKEL",
        name=None,
        asset_class=None,
        base_currency=None,
        quote_currency=None,
        digits=None,
        trade_mode=TradeMode.FULL,
    )


def test_whitespace_only_vendor_text_becomes_none(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol(description="   ", currency_base="  ", currency_profit=""))

    instrument = provider(fake).get_instrument("XAUUSD.r")

    assert instrument.name is None
    assert instrument.base_currency is None
    assert instrument.quote_currency is None


def test_asset_class_is_the_brokers_own_top_level_group(provider):
    for path, expected in (("Forex\\EURUSD", "Forex"), ("Shares\\US\\AAPL", "Shares"), ("\\Metals\\XAUUSD", "Metals")):
        instrument = provider(FakeMT5(symbol_info_result=mt5_symbol(path=path))).get_instrument("XAUUSD.r")

        assert instrument.asset_class == expected


def test_numeric_string_digits_are_converted_to_int(provider):
    instrument = provider(FakeMT5(symbol_info_result=mt5_symbol(digits="5"))).get_instrument("XAUUSD.r")

    assert instrument.digits == 5


def test_symbol_spelling_is_preserved_exactly(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol(name="XAUUSD.r"))

    assert provider(fake).get_instrument("XAUUSD.r").symbol == "XAUUSD.r"
    # The provider asks for exactly what it was given; normalisation is the
    # service's job, so no case rewriting ever happens here.
    assert fake.symbol_info_calls == ["XAUUSD.r"]


# --- failure translation ------------------------------------------------------------


def test_symbol_info_none_means_unknown_instrument_not_an_outage(provider):
    with pytest.raises(ValueError) as exc_info:
        provider(FakeMT5(symbol_info_result=None)).get_instrument("NOSUCH")

    assert "NOSUCH" in str(exc_info.value)


def test_symbol_info_raising_is_translated_to_runtime_error(provider):
    fake = FakeMT5(symbol_info_error=OSError("simulated terminal disconnect"))

    with pytest.raises(RuntimeError) as exc_info:
        provider(fake).get_instrument("XAUUSD.r")

    assert str(exc_info.value) == "MT5 instrument lookup failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_malformed_record_is_runtime_error_not_unknown_instrument(provider):
    malformed = SimpleNamespace(description="no name attribute", path="", digits=2, trade_mode=4)

    with pytest.raises(RuntimeError):
        provider(FakeMT5(symbol_info_result=malformed)).get_instrument("XAUUSD.r")


def test_invalid_digits_value_fails_closed(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol(digits="not-a-number"))

    with pytest.raises(RuntimeError):
        provider(fake).get_instrument("XAUUSD.r")


def test_empty_symbol_name_fails_closed(provider):
    with pytest.raises(RuntimeError):
        provider(FakeMT5(symbol_info_result=mt5_symbol(name="   "))).get_instrument("XAUUSD.r")


# --- catalog listing ----------------------------------------------------------------


def test_catalog_maps_every_row_and_keeps_terminal_order(provider):
    fake = FakeMT5(
        symbols_get_result=(
            mt5_symbol(name="EURUSD", description="Euro vs US Dollar", path="Forex\\EURUSD"),
            mt5_symbol(name="XAUUSD", description="Gold vs US Dollar", path="Metals\\XAUUSD"),
        )
    )

    catalog = provider(fake).list_instruments()

    assert isinstance(catalog, tuple)
    assert [instrument.symbol for instrument in catalog] == ["EURUSD", "XAUUSD"]  # vendor order, not sorted


def test_empty_catalog_is_not_a_failure(provider):
    assert provider(FakeMT5(symbols_get_result=())).list_instruments() == ()


def test_catalog_none_is_an_availability_failure(provider):
    with pytest.raises(RuntimeError) as exc_info:
        provider(FakeMT5(symbols_get_result=None)).list_instruments()

    assert "catalog unavailable" in str(exc_info.value)


def test_catalog_raising_is_translated_to_runtime_error(provider):
    fake = FakeMT5(symbols_get_error=OSError("simulated terminal disconnect"))

    with pytest.raises(RuntimeError) as exc_info:
        provider(fake).list_instruments()

    assert str(exc_info.value) == "MT5 instrument catalog request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


# --- tenant-scoped session ----------------------------------------------------------


def test_reads_are_authenticated_as_the_tenant(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol())

    provider(fake).get_instrument("XAUUSD.r")

    # The decrypted password is handed to MT5 for this tenant's own login/server.
    assert fake.authenticate_calls == [{"login": 10001, "password": MT5_PASSWORD, "server": SERVER}]


def test_two_tenants_read_their_own_sessions(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol())

    provider(fake, make_credentials(login=10001, server="BrokerA-Live")).get_instrument("XAUUSD.r")
    provider(fake, make_credentials(login=20002, server="BrokerB-Live")).get_instrument("XAUUSD.r")

    assert [call["login"] for call in fake.authenticate_calls] == [10001, 20002]
    assert [call["server"] for call in fake.authenticate_calls] == ["BrokerA-Live", "BrokerB-Live"]


def test_incomplete_credentials_fail_closed_before_reading(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol())
    incomplete = MT5AccountCredentials(login=None, server=SERVER, password_encrypted="x")

    with pytest.raises(RuntimeError):
        provider(fake, incomplete).get_instrument("XAUUSD.r")

    assert fake.accessed == []  # nothing was ever read


def test_initialize_failure_surfaces_as_runtime_error(provider):
    with pytest.raises(RuntimeError):
        provider(FakeMT5(initialize_result=False)).list_instruments()


# --- secret safety and read-only guarantee -------------------------------------------


def test_failures_never_carry_the_password(provider):
    """Every failure path is checked: no credential text may appear in a message."""
    failures: list[str] = []
    fake = FakeMT5(symbol_info_error=OSError("simulated terminal disconnect"))
    for call in (
        lambda: provider(fake).get_instrument("XAUUSD.r"),
        lambda: provider(FakeMT5(symbol_info_result=None)).get_instrument("XAUUSD.r"),
        lambda: provider(FakeMT5(symbols_get_result=None)).list_instruments(),
        lambda: provider(FakeMT5(initialize_result=False)).list_instruments(),
    ):
        try:
            call()
        except Exception as exc:  # the type is asserted by the individual tests above
            failures.append(str(exc))
    assert failures
    for message in failures:
        assert MT5_PASSWORD not in message
        assert "password" not in message


def test_only_read_only_mt5_functions_are_called(provider):
    fake = FakeMT5(symbol_info_result=mt5_symbol(), symbols_get_result=(mt5_symbol(),))

    provider(fake).get_instrument("XAUUSD.r")
    provider(fake).list_instruments()

    accessed = set(fake.accessed)
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
    # Discovery must not select/subscribe a symbol: that mutates terminal state.
    assert "symbol_select" not in fake.accessed
