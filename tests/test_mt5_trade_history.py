"""Tests for MT5TradeHistoryProvider (read-only executed trade history).

All tests patch the module-level MT5 seam (app.providers.mt5_trade_history.mt5_api)
with a configurable fake, so none of them require a real MT5 terminal,
credentials, PostgreSQL, network access, or .env.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.providers.mt5_trade_history as mt5_trade_history_module
from app.providers.mt5_trade_history import MT5TradeHistoryProvider
from app.providers.trade_history import TradeCloseReason, TradeHistoryEntry, TradeType

# The only MT5 functions a read-only trade-history provider may ever touch.
ALLOWED_MT5_FUNCTIONS = {"initialize", "last_error", "history_deals_get", "history_orders_get",
                         "shutdown", "DEAL_TYPE_BUY", "DEAL_TYPE_SELL", "DEAL_ENTRY_OUT"}
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}

# Real MT5 numeric encodings.
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_REASON_CLIENT = 0
DEAL_REASON_MOBILE = 1
DEAL_REASON_WEB = 2
DEAL_REASON_EXPERT = 3
DEAL_REASON_AGENT = 4
DEAL_REASON_TP = 5
DEAL_REASON_SO = 6
DEAL_REASON_DEALER = 7
DEAL_REASON_SOFTWARE = 8

DEAL_TIME = 1789468200  # 2026-09-14T12:30:00Z (Unix seconds)


class FakeMT5:
    """Configurable fake of the MT5 C-extension surface used by the provider."""

    DEAL_TYPE_BUY = DEAL_TYPE_BUY
    DEAL_TYPE_SELL = DEAL_TYPE_SELL
    DEAL_ENTRY_OUT = DEAL_ENTRY_OUT

    def __init__(
        self,
        deals_result: object = None,
        deals_error: Exception | None = None,
        orders_result: object = None,
        orders_error: Exception | None = None,
        initialize_result: object = True,
        initialize_error: Exception | None = None,
        shutdown_error: Exception | None = None,
    ):
        self.deals_result = deals_result
        self.deals_error = deals_error
        self.orders_result = orders_result
        self.orders_error = orders_error
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.shutdown_error = shutdown_error
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.deals_calls: list[tuple[object, object]] = []
        self.order_tickets_requested: list[int] = []
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

    def history_deals_get(self, *args: object) -> object:
        self._record("history_deals_get")
        self.deals_calls.append(args)
        if self.deals_error is not None:
            raise self.deals_error
        return self.deals_result

    def history_orders_get(self, *, ticket: int = 0) -> object:
        self._record("history_orders_get")
        self.order_tickets_requested.append(ticket)
        if self.orders_error is not None:
            raise self.orders_error
        return self.orders_result

    def shutdown(self) -> None:
        self._record("shutdown")
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


def mt5_deal(
    ticket: int = 246802468,
    order: int = 987654321,
    symbol: str = "XAUUSD",
    deal_type: int = DEAL_TYPE_BUY,
    entry: int = DEAL_ENTRY_OUT,
    reason: int = DEAL_REASON_TP,
    with_reason: bool = True,
) -> SimpleNamespace:
    """A realistic MT5 deal row (attribute access, not a dict)."""
    values: dict[str, object] = {
        "ticket": ticket,
        "order": order,
        "symbol": symbol,
        "type": deal_type,
        "entry": entry,
        "volume": 0.10,
        "price": 3648.20,
        "profit": 57.00,
        "time": DEAL_TIME,
    }
    if with_reason:
        values["reason"] = reason
    return SimpleNamespace(**values)


@pytest.fixture()
def patch_mt5(monkeypatch):
    def _patch(fake: FakeMT5) -> FakeMT5:
        monkeypatch.setattr(mt5_trade_history_module, "mt5_api", fake)
        return fake

    return _patch


# --- entry filtering --------------------------------------------------------------------


def test_entry_in_deals_are_excluded(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(
        mt5_deal(entry=DEAL_ENTRY_IN, order=111, reason=DEAL_REASON_CLIENT),
        mt5_deal(entry=DEAL_ENTRY_OUT),
    )))

    entries = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))

    assert len(entries) == 1
    assert entries[0].ticket == 246802468


# --- mapping -----------------------------------------------------------------------------


def test_successful_retrieval_and_mapping(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert isinstance(entry, TradeHistoryEntry)
    assert not isinstance(entry, SimpleNamespace)  # raw MT5 object must not leak
    assert entry == TradeHistoryEntry(
        ticket=246802468,
        order_ticket=987654321,
        symbol="XAUUSD",
        type=TradeType.BUY,
        volume=0.10,
        price=3648.20,
        profit=57.00,
        time=datetime.fromtimestamp(DEAL_TIME, tz=UTC),
        close_reason=TradeCloseReason.TP,
        stop_loss=None,
        take_profit=None,
    )
    assert isinstance(entry.ticket, int)
    assert isinstance(entry.order_ticket, int)
    for float_field in ("volume", "price", "profit"):
        assert isinstance(getattr(entry, float_field), float)
    assert entry.time.tzinfo is not None  # UTC-aware datetime, never naive


def test_mt5_buy_maps_to_buy(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(deal_type=DEAL_TYPE_BUY),), orders_result=()))

    assert MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0].type is TradeType.BUY


def test_mt5_sell_maps_to_sell(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(deal_type=DEAL_TYPE_SELL),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]
    assert entry.type is TradeType.SELL
    assert entry.type.value == "SELL"  # JSON-facing value is exactly "SELL"


def test_unknown_deal_type_fails_loudly(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(deal_type=7),), orders_result=()))

    with pytest.raises(RuntimeError):
        MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))


# --- close-reason mapping -------------------------------------------------------------------


@pytest.mark.parametrize("reason,expected", [
    (DEAL_REASON_TP, TradeCloseReason.TP),
    (DEAL_REASON_SO, TradeCloseReason.SL),
    (DEAL_REASON_CLIENT, TradeCloseReason.MANUAL),
    (DEAL_REASON_MOBILE, TradeCloseReason.MANUAL),
    (DEAL_REASON_WEB, TradeCloseReason.MANUAL),
    (DEAL_REASON_EXPERT, TradeCloseReason.OTHER),
    (DEAL_REASON_AGENT, TradeCloseReason.OTHER),
    (DEAL_REASON_DEALER, TradeCloseReason.OTHER),
    (DEAL_REASON_SOFTWARE, TradeCloseReason.OTHER),
])
def test_close_reason_mapping_table(patch_mt5, reason, expected):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(reason=reason),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.close_reason is expected


def test_unmapped_reason_classified_as_other_not_guessed(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(reason=99),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.close_reason is TradeCloseReason.OTHER


def test_missing_reason_field_maps_to_none(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(with_reason=False),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.close_reason is None


# --- SL/TP from related closing order ---------------------------------------------------------


def test_sl_tp_taken_from_related_closing_order(patch_mt5):
    order = SimpleNamespace(price_sl=3635.00, price_tp=3650.00)
    fake = patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_result=(order,)))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.stop_loss == 3635.00
    assert entry.take_profit == 3650.00
    assert fake.order_tickets_requested == [987654321]


def test_sl_tp_none_when_no_related_order_found(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_result=()))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.stop_loss is None
    assert entry.take_profit is None


def test_sl_tp_none_when_levels_are_zero_sentinels(patch_mt5):
    order = SimpleNamespace(price_sl=0.0, price_tp=0.0)
    patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_result=(order,)))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.stop_loss is None
    assert entry.take_profit is None


def test_sl_tp_none_when_deal_has_no_order_ticket(patch_mt5):
    order = SimpleNamespace(price_sl=3635.00, price_tp=3650.00)
    fake = patch_mt5(FakeMT5(deals_result=(mt5_deal(order=0),), orders_result=(order,)))

    entry = MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[0]

    assert entry.stop_loss is None
    assert entry.take_profit is None
    assert fake.order_tickets_requested == []  # no MT5 lookup attempted for order 0


def test_related_order_failure_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_error=OSError("simulated IPC crash")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))

    assert "trade history request failed" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, OSError)


# --- empty history and failures -----------------------------------------------------------------


def test_empty_tuple_means_no_trades_not_failure(patch_mt5):
    patch_mt5(FakeMT5(deals_result=()))

    assert MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC)) == ()


def test_deals_get_none_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(deals_result=None))

    with pytest.raises(RuntimeError) as exc_info:
        MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))

    assert "trade history unavailable" in str(exc_info.value)


def test_deals_get_raising_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(deals_error=OSError("simulated terminal disconnect")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5TradeHistoryProvider().get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))

    assert str(exc_info.value) == "MT5 trade history request failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_provider_passes_window_to_mt5(patch_mt5):
    fake = patch_mt5(FakeMT5(deals_result=()))
    from_time = datetime(2026, 9, 1, tzinfo=UTC)
    to_time = datetime(2026, 9, 14, tzinfo=UTC)

    MT5TradeHistoryProvider().get_trade_history(from_time, to_time)

    assert fake.deals_calls == [(from_time, to_time)]


# --- lifecycle -----------------------------------------------------------------------------------


def test_initialize_returning_false_raises_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_result=False))

    with pytest.raises(RuntimeError):
        MT5TradeHistoryProvider()


def test_initialize_raising_translated_to_runtime_error(patch_mt5):
    patch_mt5(FakeMT5(initialize_error=OSError("simulated IPC crash")))

    with pytest.raises(RuntimeError) as exc_info:
        MT5TradeHistoryProvider()

    assert str(exc_info.value) == "MT5 terminal initialization failed"
    assert isinstance(exc_info.value.__cause__, OSError)


def test_shutdown_calls_mt5_shutdown(patch_mt5):
    fake = patch_mt5(FakeMT5(deals_result=()))

    provider = MT5TradeHistoryProvider()
    provider.shutdown()

    assert fake.shutdown_calls == 1


def test_shutdown_never_raises_when_mt5_shutdown_fails(patch_mt5):
    patch_mt5(FakeMT5(shutdown_error=OSError("terminal already gone")))

    provider = MT5TradeHistoryProvider()
    provider.shutdown()  # must not raise


def test_only_read_only_mt5_functions_are_called(patch_mt5):
    fake = patch_mt5(FakeMT5(deals_result=(mt5_deal(),), orders_result=()))

    provider = MT5TradeHistoryProvider()
    provider.get_trade_history(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
    provider.shutdown()

    accessed = set(fake.accessed)
    assert accessed <= ALLOWED_MT5_FUNCTIONS
    assert accessed.isdisjoint(TRADING_FUNCTIONS)
