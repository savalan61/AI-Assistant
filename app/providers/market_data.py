import abc
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple


# Typed OHLCV candle contract shared by all providers.
#
# OHLC are Decimal (Step 37): they are market prices, converted at the provider
# boundary with Decimal(str(raw_value)). ``volume`` is MT5 tick volume — a
# counting measure, not money or a price — and stays float.
class Candle(NamedTuple):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: float


# Abstraction boundary: services depend on this, never on MT5 directly.
class MarketDataProvider(abc.ABC):
    @abc.abstractmethod
    def get_market_data(self, symbol: str) -> Candle:
        ...
