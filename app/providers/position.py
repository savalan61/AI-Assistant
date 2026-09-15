import abc
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple


# Position direction. StrEnum so the JSON value is exactly "BUY"/"SELL" and new
# directions can only be introduced deliberately (mirrors UserRole).
class PositionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


# Typed read-only open-position snapshot shared by all position providers.
# Explicit NamedTuple like Candle/AccountInfo so the raw MT5 object never
# travels past the provider boundary.
#
# volume and the price/profit fields are Decimal (Step 37): volume is a
# fixed-0.01-step quantity and open/current price and profit are financial
# values, all converted at the provider boundary with Decimal(str(raw_value)).
class Position(NamedTuple):
    ticket: int
    symbol: str
    type: PositionType
    volume: Decimal
    open_price: Decimal
    current_price: Decimal
    profit: Decimal


# Abstraction boundary: services depend on this, never on MT5 directly
# (mirrors MarketDataProvider / AccountInfoProvider).
class PositionProvider(abc.ABC):
    @abc.abstractmethod
    def get_positions(self) -> tuple[Position, ...]:
        ...
