import datetime
from typing import Any

import MetaTrader5 as mt5

from app.providers.market_data import Candle, MarketDataProvider

# MT5 is a C extension without Python type stubs; Any bypasses Pylance type checking.
mt5_api: Any = mt5


class MT5MarketDataProvider(MarketDataProvider):
    def __init__(self):
        # Boundary rule: the MT5 C extension raises plain built-in Exception for
        # terminal/IPC failures instead of returning False. This try block covers
        # only the external MT5 call and translates it into the application's
        # RuntimeError (mapped to HTTP 503 by the API layer), with exception
        # chaining preserved for logs; raw MT5 exceptions must never reach FastAPI.
        try:
            initialized = mt5_api.initialize()
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 terminal initialization failed") from exc
        if not initialized:
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 initialization failed: {error}")

    def get_market_data(self, symbol: str) -> Candle:
        # Same boundary rule as __init__: translate an expected plain Exception
        # raised by the MT5 C extension into RuntimeError. The message carries no
        # third-party details, so nothing raw can leak into the HTTP response.
        try:
            rates = mt5_api.copy_rates_from_pos(symbol, mt5_api.TIMEFRAME_M1, 1, 1)
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 market data request failed") from exc

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