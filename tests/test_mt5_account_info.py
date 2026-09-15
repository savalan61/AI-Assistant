"""Tests for MT5AccountInfoProvider (read-only account information).

Every test injects a fake MT5 into the real MT5SessionManager, so none of them
require a real MT5 terminal, MT5 credentials, PostgreSQL, network access, or
.env. The provider reads through the authenticated session, so conversion,
tenant scoping and error translation all run for real.
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.account_info import AccountInfo
from app.providers.mt5_account_info import MT5AccountInfoProvider

# The only MT5 functions a read-only account-info provider may ever touch (the
# session boundary authenticates; the provider itself only reads).
ALLOWED_MT5_FUNCTIONS = {"initialize", "login", "last_error", "account_info"}
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}

SERVER = "MetaQuotes-Demo"
MT5_PASSWORD = "mt5-account-password-under-test"


class FakeMT5:
    """Configurable fake of the MT5 C-extension surface used by the provider."""

    def __init__(
        self,
        initialize_result: object = True,
        initialize_error: Exception | None = None,
        account_info_result: object = None,
        account_info_error: Exception | None = None,
    ):
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.account_info_result = account_info_result
        self.account_info_error = account_info_error
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
        self._record("account_info")
        if self.account_info_error is not None:
            raise self.account_info_error
        return self.account_info_result


# A realistic MT5 account_info() payload (attribute access, not a dict).
# Field names mirror the REAL MetaTrader5 surface — note margin_free, which
# the provider maps onto the contract's free_margin (verified on a terminal).
MT5_ACCOUNT = SimpleNamespace(
    login=10001,
    name="Demo Account",
    balance=10000.0,
    equity=10150.25,
    margin=250.0,
    margin_free=9900.25,
    margin_level=40601.0,
    currency="USD",
    server="MetaQuotes-Demo",
)

# The contract's money fields are Decimal(str(raw_float)) conversions of the
# same MT5 payload — exactness through str(), never Decimal(raw_float).
MT5_ACCOUNT_EXPECTED = AccountInfo(
    login=10001,
    name="Demo Account",
    balance=Decimal("10000.0"),
    equity=Decimal("10150.25"),
    margin=Decimal("250.0"),
    free_margin=Decimal("9900.25"),
    margin_level=40601.0,
    currency="USD",
    server="MetaQuotes-Demo",
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

    def _provider(fake: FakeMT5, credentials: MT5AccountCredentials | None = None) -> MT5AccountInfoProvider:
        return MT5AccountInfoProvider(
            session_manager=MT5SessionManager(mt5_api=fake),
            credentials=credentials if credentials is not None else make_credentials(),
        )

    return _provider


# --- successful conversion --------------------------------------------------------


def test_successful_retrieval_and_conversion(provider):
    info = provider(FakeMT5(account_info_result=MT5_ACCOUNT)).get_account_info()

    assert isinstance(info, AccountInfo)
    # Raw MT5 object must not leak: the public result is the app contract.
    assert not isinstance(info, SimpleNamespace)


def test_all_nine_fields_are_mapped_correctly(provider):
    info = provider(FakeMT5(account_info_result=MT5_ACCOUNT)).get_account_info()

    # Values preserved exactly as returned by MT5 (money fields via Decimal(str(...))).
    assert info == MT5_ACCOUNT_EXPECTED
    # Contract types hold (explicit coercions in the provider).
    assert isinstance(info.login, int)
    for money_field in ("balance", "equity", "margin", "free_margin"):
        assert isinstance(getattr(info, money_field), Decimal)
    assert isinstance(info.margin_level, float)
    assert isinstance(info.name, str) and isinstance(info.currency, str) and isinstance(info.server, str)


# --- tenant-scoped session ---------------------------------------------------------


def test_reads_are_authenticated_as_the_tenant(provider):
    fake = FakeMT5(account_info_result=MT5_ACCOUNT)

    provider(fake).get_account_info()

    # The decrypted password is handed to MT5 for this tenant's own login/server.
    assert fake.authenticate_calls == [{"login": 10001, "password": MT5_PASSWORD, "server": SERVER}]


def test_two_tenants_read_their_own_sessions(provider):
    fake = FakeMT5(account_info_result=MT5_ACCOUNT)

    provider(fake, make_credentials(login=10001, server="BrokerA-Live")).get_account_info()
    provider(fake, make_credentials(login=20002, server="BrokerB-Live")).get_account_info()

    assert [call["login"] for call in fake.authenticate_calls] == [10001, 20002]
    assert [call["server"] for call in fake.authenticate_calls] == ["BrokerA-Live", "BrokerB-Live"]


def test_incomplete_credentials_fail_closed_before_reading(provider):
    fake = FakeMT5(account_info_result=MT5_ACCOUNT)
    incomplete = MT5AccountCredentials(login=10001, server=None, password_encrypted="x")

    with pytest.raises(RuntimeError):
        provider(fake, incomplete).get_account_info()

    assert fake.accessed == []  # nothing was ever read


# --- failures ----------------------------------------------------------------------


def test_initialize_failure_surfaces_as_runtime_error(provider):
    fake = FakeMT5(initialize_result=False)

    with pytest.raises(RuntimeError):
        provider(fake).get_account_info()


def test_initialize_raising_external_exception_translated_to_runtime_error(provider):
    fake = FakeMT5(initialize_error=OSError("simulated IPC crash"))

    with pytest.raises(RuntimeError) as exc_info:
        provider(fake).get_account_info()

    # Generic message (no third-party detail) with the cause chained for logs.
    assert "MT5 terminal initialization failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, OSError)


def test_account_info_returning_none_raises_runtime_error(provider):
    with pytest.raises(RuntimeError) as exc_info:
        provider(FakeMT5(account_info_result=None)).get_account_info()

    assert "account information unavailable" in str(exc_info.value)


def test_account_info_raising_external_exception_translated_to_runtime_error(provider):
    fake = FakeMT5(account_info_error=OSError("simulated terminal disconnect"))

    with pytest.raises(RuntimeError) as exc_info:
        provider(fake).get_account_info()

    assert str(exc_info.value) == "MT5 account information request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


# --- read-only guarantee ----------------------------------------------------------------


def test_only_read_only_mt5_functions_are_called(provider):
    fake = FakeMT5(account_info_result=MT5_ACCOUNT)

    provider(fake).get_account_info()

    accessed = set(fake.accessed)
    # Nothing outside the read-only surface, and no trading function at all.
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
