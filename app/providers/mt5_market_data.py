import datetime
from typing import Any

import MetaTrader5 as mt5

from app.providers.market_data import Candle, MarketDataProvider

# MT5 is a C extension without Python type stubs; Any bypasses Pylance type checking.
mt5_api: Any = mt5


class MT5MarketDataProvider(MarketDataProvider):
    def __init__(self):
        if not mt5_api.initialize():
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 initialization failed: {error}")

    def get_market_data(self, symbol: str) -> Candle:
        rates = mt5_api.copy_rates_from_pos(symbol, mt5_api.TIMEFRAME_M1, 1, 1)

        if rates is None:
            error = mt5_api.last_error()
            # Error code -1 means the symbol is not recognized by MT5
            # (invalid/unavailable symbol); other error codes indicate an MT5 infrastructure issue.
            if error[0] == -1:
                raise ValueError(f"No candle data returned for {symbol}")
            raise RuntimeError(f"MT5 API error for {symbol}: {error}")

        if len(rates) == 0:
            raise ValueError(f"No candle data returned for {symbol}")

        rate = rates[0]

        return Candle(
            timestamp=datetime.datetime.fromtimestamp(int(rate[0])),
            open=float(rate[1]),
            high=float(rate[2]),
            low=float(rate[3]),
            close=float(rate[4]),
            volume=float(rate[5]),
        )