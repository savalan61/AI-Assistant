import abc
from datetime import datetime
from typing import NamedTuple


class Candle(NamedTuple):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataProvider(abc.ABC):
    @abc.abstractmethod
    def get_market_data(self, symbol: str) -> Candle:
        ...
