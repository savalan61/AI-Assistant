from datetime import datetime

from app.providers.market_data import Candle, MarketDataProvider

# Deterministic candle returned for every symbol; fixed values keep tests repeatable.
_FAKE_CANDLE = Candle(
    timestamp=datetime(2024, 1, 15, 12, 0, 0),
    open=100.0,
    high=110.0,
    low=95.0,
    close=105.0,
    volume=1234.0,
)


# Minimal in-memory provider for tests; implements the provider contract without MT5.
class FakeMarketDataProvider(MarketDataProvider):
    def __init__(self):
        self.requested_symbols: list[str] = []

    def get_market_data(self, symbol: str) -> Candle:
        self.requested_symbols.append(symbol)
        return _FAKE_CANDLE
