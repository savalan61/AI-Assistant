"""Tests for MT5AccountInfoProvider (Step 16).

All tests patch the module-level MT5 seam (app.providers.mt5_account_info.mt5_api)
with a configurable fake, so none of them require a real MT5 terminal, MT5
credentials, PostgreSQL, network access, or .env. The provider's full boundary
behavior (initialization, conversion, error translation, shutdown) runs for real.
"""
from types import SimpleNamespace

import pytest

import app.providers.mt5_account_info as mt5_account_info_module
from app.providers.account_info import AccountInfo
from app.providers.mt5_account_info import MT5AccountInfoProvider

# The only MT5 functions a read-only account-info provider may ever touch.
ALLOWED_MT5_FUNCTIONS = {"initialize", "last_error", "account_info", "shutdown"}
# MT5 trading functions that must never be called by this provider.
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}


class FakeMT5:
    """Configurable fake of the MT5 C-extension surface used by the provider.

    Every attribute access is recorded so tests can prove exactly which MT5
    functions were touched (no trading calls, nothing unexpected).
    """

    def __init__(
        self,
        initialize_result: object = True,
        initialize_error: Exception | None = None,
        account_info_result: object = None,
        account_info_error: Exception | None = None,
        shutdown_error: Exception | None = None,
    ):
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.account_info_result = account_info_result
        self.account_info_error = account_info_error
        self.shutdown_error = shutdown_error
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.accessed: list[str] = []

    def _record(self, name: str) -> None:
        self.accessed.append(name)

    def __getattr__(self, name: str):  # pragma: no cover - only for unexpected probes
        self.accessed.append(name)
        raise AttributeError(f"FakeMT5 has no attribute {name!r}")

    def initialize(self):  # noqa: D102 - fake of mt5.initialize
        self._record("initialize")
        self.initialize_calls += 1
        if self.initialize_error is not None:
            raise self.initialize_error
        return self.initialize_result

    def last_error(self):  # noqa: D102 - fake of mt5.last_error
        self._record("last_error")
        return (-1, "simulated MT5 failure")

    def account_info(self):  # noqa: D102 - fake of mt5.account_info
        self._record("account_info")
        if self.account_info_error is not None:
            raise self.account_info_error
        return self.account_info_result

    def shutdown(self):  # noqa: D102 - fake of mt5.shutdown
        self._record("shutdown")
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


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


@pytest.fixture()
def patch_mt5(monkeypatch):
    """Patch the provider's MT5 seam and restore it after each test."""

    def _patch(fake: FakeMT5) -> FakeMT5:
        monkeypatch.setattr(mt5_account_info_module, "mt5_api", fake)
        return fake

    return _patch


# --- successful initialization and conversion ----------------------------------


def test_successful_initialization_and_account_info_conversion(patch_mt5):
    fake = patch_mt5(FakeMT5(initialize_result=True, account_info_result=MT5_ACCOUNT))

    provider = MT5AccountInfoProvider()
    info = provider.get_account_info()

    assert fake.initialize_calls == 1
    assert isinstance(info, AccountInfo)
    # Raw MT5 object must not leak: the public result is the app contract.
    assert not isinstance(info, SimpleNamespace)


def test_all_nine_fields_are_mapped_correctly(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=True, account_info_result=MT5_ACCOUNT))

    info = MT5AccountInfoProvider().get_account_info()

    # Values preserved exactly as returned by MT5.
    assert info == AccountInfo(
        login=10001,
        name="Demo Account",
        balance=10000.0,
        equity=10150.25,
        margin=250.0,
        free_margin=9900.25,
        margin_level=40601.0,
        currency="USD",
        server="MetaQuotes-Demo",
    )
    # Contract types hold (explicit coercions in the provider).
    assert isinstance(info.login, int)
    for float_field in ("balance", "equity", "margin", "free_margin", "margin_level"):
        assert isinstance(getattr(info, float_field), float)
    assert isinstance(info.name, str) and isinstance(info.currency, str) and isinstance(info.server, str)


# --- initialization failures -----------------------------------------------------


def test_initialize_returning_false_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=False))

    with pytest.raises(RuntimeError) as exc_info:
        MT5AccountInfoProvider()

    # Error information from last_error() is included in the message.
    assert "simulated MT5 failure" in str(exc_info.value)


def test_initialize_raising_external_exception_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_error=OSError("simulated IPC crash")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5AccountInfoProvider()

    # Generic message (no third-party detail) with the cause chained for logs.
    assert str(exc_info.value) == "MT5 terminal initialization failed"
    assert isinstance(exc_info.value.__cause__, OSError)


# --- account_info failures ---------------------------------------------------------


def test_account_info_returning_none_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=True, account_info_result=None))

    provider = MT5AccountInfoProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.get_account_info()

    assert "account information unavailable" in str(exc_info.value)


def test_account_info_raising_external_exception_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=True, account_info_error=OSError("simulated terminal disconnect")))

    provider = MT5AccountInfoProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.get_account_info()

    assert str(exc_info.value) == "MT5 account information request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


# --- shutdown behavior ----------------------------------------------------------------


def test_shutdown_calls_mt5_shutdown(patch_mt5):
    fake = patch_mt5(FakeMT5(initialize_result=True, account_info_result=MT5_ACCOUNT))

    provider = MT5AccountInfoProvider()
    provider.shutdown()

    assert fake.shutdown_calls == 1


def test_shutdown_never_raises_when_mt5_shutdown_fails(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=True, shutdown_error=OSError("terminal already gone")))

    provider = MT5AccountInfoProvider()
    provider.shutdown()  # must not raise


# --- read-only guarantee ----------------------------------------------------------------


def test_only_read_only_mt5_functions_are_called(patch_mt5):
    fake = patch_mt5(FakeMT5(initialize_result=True, account_info_result=MT5_ACCOUNT))

    provider = MT5AccountInfoProvider()
    provider.get_account_info()
    provider.shutdown()

    accessed = set(fake.accessed)
    # Nothing outside the read-only surface, and no trading function at all.
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
