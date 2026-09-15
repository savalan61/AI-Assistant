import datetime

from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.market_data import Candle, MarketDataProvider


class MT5MarketDataProvider(MarketDataProvider):
    """Read-only MT5 market-data provider, scoped to one tenant's session.

    Tenant scope: prices are read from the terminal connection, and that
    connection is authenticated as one account at a time process-wide, so the
    read is made inside ``MT5SessionManager.acquire`` for the requesting tenant —
    exactly like the account/positions/history providers. Without this the feed
    could be served from whatever account another tenant last authenticated.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_market_data(self, symbol: str) -> Candle:
        # Authenticate (or reuse) the requesting tenant's session and read inside
        # that authenticated span: the lock is held until the read completes.
        with self._session.acquire(self._credentials) as mt5_api:
            # Boundary rule: the C extension raises plain built-in Exception for
            # terminal/IPC failures. The message carries no third-party details,
            # so nothing raw can leak into the HTTP response.
            try:
                rates = mt5_api.copy_rates_from_pos(symbol, mt5_api.TIMEFRAME_M1, 1, 1)
            except Exception as exc:  # expected third-party MT5 exception at this boundary only
                raise RuntimeError("MT5 market data request failed") from exc

            if rates is None:
                error = mt5_api.last_error()
                # Error code -1 means the symbol is not recognized by MT5
                # (invalid/unavailable symbol); other error codes indicate an MT5
                # infrastructure issue.
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
