import abc
from datetime import datetime
from typing import NamedTuple


# Typed OHLCV candle contract shared by all providers.
class Candle(NamedTuple):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


# Abstraction boundary: services depend on this, never on MT5 directly.
class MarketDataProvider(abc.ABC):
    @abc.abstractmethod
    def get_market_data(self, symbol: str) -> Candle:
        ...
