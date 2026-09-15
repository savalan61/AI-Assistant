import abc
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple


# Trade direction. StrEnum so the JSON value is exactly "BUY"/"SELL" and new
# directions can only be introduced deliberately (mirrors PositionType).
class TradeType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


# Why a deal was closed. StrEnum so the JSON value is exactly the contract
# value. Values are mapped from the real MT5 deal reason field (DEAL_REASON_*),
# never guessed.
class TradeCloseReason(StrEnum):
    TP = "TP"
    SL = "SL"
    MANUAL = "MANUAL"
    OTHER = "OTHER"


# Typed read-only executed-trade record shared by all trade-history providers.
# Explicit NamedTuple like Candle/AccountInfo/Position so the raw MT5 object
# never travels past the provider boundary.
#
# close_reason: DealCloseReason | None — None means MT5 supplied no reason for
# this deal; the application must not guess one.
#
# stop_loss / take_profit: Decimal | None. MT5's historical deal object does
# not reliably carry the protective levels of the closing order, so they are
# only filled when the related closing order can actually be retrieved
# (history_orders_get(ticket=...)); otherwise they are None rather than
# invented values.
#
# volume/price/profit and the protective levels are Decimal (Step 37),
# converted at the provider boundary with Decimal(str(raw_value)).
class TradeHistoryEntry(NamedTuple):
    ticket: int
    order_ticket: int
    symbol: str
    type: TradeType
    volume: Decimal
    price: Decimal
    profit: Decimal
    time: datetime
    close_reason: TradeCloseReason | None
    stop_loss: Decimal | None
    take_profit: Decimal | None


# Abstraction boundary: services depend on this, never on MT5 directly
# (mirrors MarketDataProvider / AccountInfoProvider / PositionProvider).
class TradeHistoryProvider(abc.ABC):
    @abc.abstractmethod
    def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
        ...
