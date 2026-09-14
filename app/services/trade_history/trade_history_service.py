from datetime import datetime

from app.providers.trade_history import TradeHistoryEntry, TradeHistoryProvider


# Depends on the provider abstraction, not on MT5 directly (mirrors
# PositionService). No database access, no business calculations: executed
# trade history is a passthrough read between the caller-supplied UTC window
# and the provider.
class TradeHistoryService:
    def __init__(self, provider: TradeHistoryProvider):
        self._provider = provider

    def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
        return self._provider.get_trade_history(from_time, to_time)
