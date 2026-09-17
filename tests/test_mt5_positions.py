"""Tests for MT5PositionProvider (read-only open positions).

Every test injects a fake MT5 into the real MT5SessionManager, so none of them
require a real MT5 terminal, credentials, PostgreSQL, network access, or .env.
The provider reads through the authenticated session, so provider-level mapping
and tenant scoping are both exercised for real.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.mt5_positions import MT5PositionProvider
from app.providers.position import Position, PositionType

# The only MT5 functions a read-only positions provider may ever touch (the
# session boundary authenticates; the provider itself only reads).
ALLOWED_MT5_FUNCTIONS = {"initialize", "login", "last_error", "positions_get", "account_info"}
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}

SERVER = "BrokerA-Live"
MT5_PASSWORD = "mt5-account-password-under-test"


class FakeMT5:
    """Configurable fake of the MT5 C-extension surface used by the provider."""

    # Real MT5 numeric direction encoding.
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(
        self,
        initialize_result: object = True,
        initialize_error: Exception | None = None,
        positions_result: object = None,
        positions_error: Exception | None = None,
    ):
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.positions_result = positions_result
        self.positions_error = positions_error
        self.authenticate_calls: list[dict[str, object]] = []
        self.accessed: list[str] = []

    def _record(self, name: str) -> None:
        self.accessed.append(name)

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self.authenticate_calls.append(dict(kwargs))
        if self.initialize_error is not None:
            raise self.initialize_error
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

    def positions_get(self) -> object:
        self._record("positions_get")
        if self.positions_error is not None:
            raise self.positions_error
        return self.positions_result


def mt5_position(
    ticket: int = 123456789,
    symbol: str = "XAUUSD",
    direction: int = FakeMT5.ORDER_TYPE_BUY,
) -> SimpleNamespace:
    """A realistic MT5 position row (attribute access, not a dict)."""
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=direction,
        volume=0.10,
        price_open=3642.50,
        price_current=3648.20,
        profit=57.00,
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

    def _provider(fake: FakeMT5, credentials: MT5AccountCredentials | None = None) -> MT5PositionProvider:
        return MT5PositionProvider(
            session_manager=MT5SessionManager(mt5_api=fake),
            credentials=credentials if credentials is not None else make_credentials(),
        )

    return _provider


# --- provider mapping -------------------------------------------------------------


def test_successful_retrieval_and_mapping(provider):
    positions = provider(FakeMT5(positions_result=(mt5_position(),))).get_positions()

    assert isinstance(positions, tuple)
    assert isinstance(positions[0], Position)
    assert not isinstance(positions[0], SimpleNamespace)  # raw MT5 object must not leak


def test_all_seven_fields_are_mapped_correctly(provider):
    info = provider(FakeMT5(positions_result=(mt5_position(),))).get_positions()[0]

    assert info == Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("3642.50"),
        current_price=Decimal("3648.20"),
        profit=Decimal("57.00"),
    )
    assert isinstance(info.ticket, int)
    for money_field in ("volume", "open_price", "current_price", "profit"):
        assert isinstance(getattr(info, money_field), Decimal)
    assert isinstance(info.symbol, str)


def test_mt5_buy_maps_to_buy(provider):
    fake = FakeMT5(positions_result=(mt5_position(direction=FakeMT5.ORDER_TYPE_BUY),))

    assert provider(fake).get_positions()[0].type is PositionType.BUY


def test_mt5_sell_maps_to_sell(provider):
    fake = FakeMT5(positions_result=(mt5_position(ticket=987654321, direction=FakeMT5.ORDER_TYPE_SELL),))

    position = provider(fake).get_positions()[0]
    assert position.type is PositionType.SELL
    assert position.type.value == "SELL"  # JSON-facing value is exactly "SELL"


def test_unknown_direction_fails_loudly(provider):
    fake = FakeMT5(positions_result=(mt5_position(direction=7),))

    with pytest.raises(RuntimeError):
        provider(fake).get_positions()


def test_empty_tuple_means_no_positions_not_failure(provider):
    assert provider(FakeMT5(positions_result=())).get_positions() == ()


# --- tenant-scoped session ---------------------------------------------------------


def test_reads_are_authenticated_as_the_tenant(provider):
    fake = FakeMT5(positions_result=(mt5_position(),))

    provider(fake).get_positions()

    # The decrypted password is handed to MT5 for this tenant's own login/server.
    assert fake.authenticate_calls == [{"login": 10001, "password": MT5_PASSWORD, "server": SERVER}]


def test_two_tenants_read_their_own_sessions(provider):
    fake = FakeMT5(positions_result=(mt5_position(),))

    provider(fake, make_credentials(login=10001, server="BrokerA-Live")).get_positions()
    provider(fake, make_credentials(login=20002, server="BrokerB-Live")).get_positions()

    assert [call["login"] for call in fake.authenticate_calls] == [10001, 20002]
    assert [call["server"] for call in fake.authenticate_calls] == ["BrokerA-Live", "BrokerB-Live"]


def test_incomplete_credentials_fail_closed_before_reading(provider):
    fake = FakeMT5(positions_result=(mt5_position(),))
    incomplete = MT5AccountCredentials(login=None, server=SERVER, password_encrypted="x")

    with pytest.raises(RuntimeError):
        provider(fake, incomplete).get_positions()

    assert fake.accessed == []  # nothing was ever read


# --- failure translation -----------------------------------------------------------


def test_positions_get_none_raises_runtime_error(provider):
    with pytest.raises(RuntimeError) as exc_info:
        provider(FakeMT5(positions_result=None)).get_positions()

    assert "open positions unavailable" in str(exc_info.value)


def test_positions_get_raising_translated_to_runtime_error(provider):
    fake = FakeMT5(positions_error=OSError("simulated terminal disconnect"))

    with pytest.raises(RuntimeError) as exc_info:
        provider(fake).get_positions()

    assert str(exc_info.value) == "MT5 open positions request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_initialize_failure_surfaces_as_runtime_error(provider):
    fake = FakeMT5(initialize_result=False)

    with pytest.raises(RuntimeError):
        provider(fake).get_positions()


# --- read-only guarantee ----------------------------------------------------------------


def test_only_read_only_mt5_functions_are_called(provider):
    fake = FakeMT5(positions_result=(mt5_position(),))

    provider(fake).get_positions()

    accessed = set(fake.accessed)
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
