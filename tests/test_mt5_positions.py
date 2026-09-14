"""Tests for MT5PositionProvider (read-only open positions).

All tests patch the module-level MT5 seam (app.providers.mt5_positions.mt5_api)
with a configurable fake, so none of them require a real MT5 terminal,
credentials, PostgreSQL, network access, or .env.
"""
from types import SimpleNamespace

import pytest

import app.providers.mt5_positions as mt5_positions_module
from app.providers.position import Position, PositionType
from app.providers.mt5_positions import MT5PositionProvider

# The only MT5 functions a read-only positions provider may ever touch.
ALLOWED_MT5_FUNCTIONS = {"initialize", "last_error", "positions_get", "shutdown",
                         "ORDER_TYPE_BUY", "ORDER_TYPE_SELL"}
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}


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
        shutdown_error: Exception | None = None,
    ):
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.positions_result = positions_result
        self.positions_error = positions_error
        self.shutdown_error = shutdown_error
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.accessed: list[str] = []

    def _record(self, name: str) -> None:
        self.accessed.append(name)

    def initialize(self) -> object:
        self._record("initialize")
        self.initialize_calls += 1
        if self.initialize_error is not None:
            raise self.initialize_error
        return self.initialize_result

    def last_error(self) -> tuple[int, str]:
        self._record("last_error")
        return (-1, "simulated MT5 failure")

    def positions_get(self) -> object:
        self._record("positions_get")
        if self.positions_error is not None:
            raise self.positions_error
        return self.positions_result

    def shutdown(self) -> None:
        self._record("shutdown")
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


def mt5_position(ticket: int = 123456789, symbol: str = "XAUUSD", direction: int = FakeMT5.ORDER_TYPE_BUY) -> SimpleNamespace:
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


@pytest.fixture()
def patch_mt5(monkeypatch):
    def _patch(fake: FakeMT5) -> FakeMT5:
        monkeypatch.setattr(mt5_positions_module, "mt5_api", fake)
        return fake

    return _patch


# --- provider mapping -------------------------------------------------------------


def test_successful_retrieval_and_mapping(patch_mt5):
    patch_mt5(FakeMT5(positions_result=(mt5_position(),)))

    provider = MT5PositionProvider()
    positions = provider.get_positions()

    assert isinstance(positions, tuple)
    assert isinstance(positions[0], Position)
    assert not isinstance(positions[0], SimpleNamespace)  # raw MT5 object must not leak


def test_all_seven_fields_are_mapped_correctly(patch_mt5):
    patch_mt5(FakeMT5(positions_result=(mt5_position(),)))

    info = MT5PositionProvider().get_positions()[0]

    assert info == Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=0.10,
        open_price=3642.50,
        current_price=3648.20,
        profit=57.00,
    )
    assert isinstance(info.ticket, int)
    for float_field in ("volume", "open_price", "current_price", "profit"):
        assert isinstance(getattr(info, float_field), float)
    assert isinstance(info.symbol, str)


def test_mt5_buy_maps_to_buy(patch_mt5):
    patch_mt5(FakeMT5(positions_result=(mt5_position(direction=FakeMT5.ORDER_TYPE_BUY),)))

    assert MT5PositionProvider().get_positions()[0].type is PositionType.BUY


def test_mt5_sell_maps_to_sell(patch_mt5):
    patch_mt5(FakeMT5(positions_result=(mt5_position(ticket=987654321, direction=FakeMT5.ORDER_TYPE_SELL),)))

    position = MT5PositionProvider().get_positions()[0]
    assert position.type is PositionType.SELL
    assert position.type.value == "SELL"  # JSON-facing value is exactly "SELL"


def test_unknown_direction_fails_loudly(patch_mt5):
    patch_mt5(FakeMT5(positions_result=(mt5_position(direction=7),)))

    with pytest.raises(RuntimeError):
        MT5PositionProvider().get_positions()


def test_empty_tuple_means_no_positions_not_failure(patch_mt5):
    patch_mt5(FakeMT5(positions_result=()))

    assert MT5PositionProvider().get_positions() == ()


def test_positions_get_none_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(positions_result=None))

    with pytest.raises(RuntimeError) as exc_info:
        MT5PositionProvider().get_positions()

    assert "open positions unavailable" in str(exc_info.value)


def test_positions_get_raising_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(positions_error=OSError("simulated terminal disconnect")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5PositionProvider().get_positions()

    assert str(exc_info.value) == "MT5 open positions request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_initialize_returning_false_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=False))

    with pytest.raises(RuntimeError):
        MT5PositionProvider()


def test_initialize_raising_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_error=OSError("simulated IPC crash")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5PositionProvider()

    assert str(exc_info.value) == "MT5 terminal initialization failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_shutdown_calls_mt5_shutdown(patch_mt5):
    fake = patch_mt5(FakeMT5(positions_result=()))

    provider = MT5PositionProvider()
    provider.shutdown()

    assert fake.shutdown_calls == 1


def test_shutdown_never_raises_when_mt5_shutdown_fails(patch_mt5):
    patch_mt5(FakeMT5(shutdown_error=OSError("terminal already gone")))

    provider = MT5PositionProvider()
    provider.shutdown()  # must not raise


def test_only_read_only_mt5_functions_are_called(patch_mt5):
    fake = patch_mt5(FakeMT5(positions_result=(mt5_position(),)))

    provider = MT5PositionProvider()
    provider.get_positions()
    provider.shutdown()

    accessed = set(fake.accessed)
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
