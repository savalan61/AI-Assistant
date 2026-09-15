from datetime import datetime
from decimal import Decimal

from app.providers.market_data import Candle, MarketDataProvider

# Deterministic candle returned for every symbol; fixed values keep tests
# repeatable. OHLC are Decimal market prices; tick volume stays float — exactly
# as the real MT5 provider produces them.
_FAKE_CANDLE = Candle(
    timestamp=datetime(2024, 1, 15, 12, 0, 0),
    open=Decimal("100.00"),
    high=Decimal("110.00"),
    low=Decimal("95.00"),
    close=Decimal("105.00"),
    volume=1234.0,
)


# Minimal in-memory provider for tests; implements the provider contract without MT5.
class FakeMarketDataProvider(MarketDataProvider):
    def __init__(self):
        self.requested_symbols: list[str] = []

    def get_market_data(self, symbol: str) -> Candle:
        self.requested_symbols.append(symbol)
        return _FAKE_CANDLE
