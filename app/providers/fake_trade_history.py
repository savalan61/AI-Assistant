from app.providers.trade_history import TradeHistoryEntry, TradeHistoryProvider, TradeType

# Deterministic executed-trade history returned for every call; fixed values
# keep tests repeatable (the FakePositionProvider pattern, applied to trade
# history).
from datetime import UTC, datetime

_FAKE_TRADE: TradeHistoryEntry = TradeHistoryEntry(
    ticket=246802468,
    order_ticket=987654321,
    symbol="XAUUSD",
    type=TradeType.BUY,
    volume=0.10,
    price=3648.20,
    profit=57.00,
    time=datetime(2026, 9, 14, 12, 30, 0, tzinfo=UTC),
    close_reason=None,
    stop_loss=3635.00,
    take_profit=3650.00,
)


# Minimal in-memory provider for tests; implements the provider contract without MT5.
class FakeTradeHistoryProvider(TradeHistoryProvider):
    def __init__(self, trades: tuple[TradeHistoryEntry, ...] = (_FAKE_TRADE,)):
        self.trades = trades
        self.call_count = 0
        self.last_from_time: datetime | None = None
        self.last_to_time: datetime | None = None

    def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
        self.call_count += 1
        self.last_from_time = from_time
        self.last_to_time = to_time
        return self.trades
