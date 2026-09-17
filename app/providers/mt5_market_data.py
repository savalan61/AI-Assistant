import datetime
from decimal import Decimal
from typing import Any

from app.core.mt5_session import MT5AccountCredentials, MT5ClientError, MT5SessionManager
from app.providers.market_data import Candle, MarketDataProvider

# The error code this provider has always read as the terminal's own "symbol not
# recognized" answer (invalid/unavailable symbol). Kept exactly as it was: this
# provider's client/availability split is deliberately narrower than the
# instrument provider's, which also accepts a non-negative terminal code.
_UNRECOGNISED_SYMBOL_CODE = -1


def _is_unrecognised_symbol(error: Any) -> bool:
    """Is this ``last_error()`` the terminal's own "symbol not recognized" answer?

    Only ``-1`` — the reading this provider has always used. Every other code, and
    an error that cannot be read at all, is an MT5 infrastructure problem: an
    unclassified state must never be retold as "this symbol has no data".
    """
    if not isinstance(error, tuple) or not error:
        return False
    code = error[0]
    if not isinstance(code, int) or isinstance(code, bool):
        return False
    return code == _UNRECOGNISED_SYMBOL_CODE


class MT5MarketDataProvider(MarketDataProvider):
    """Read-only MT5 market-data provider, scoped to one customer's session.

    Tenant scope: prices are read from the terminal connection, and that
    connection is authenticated as one account at a time process-wide, so the
    read is made inside ``MT5SessionManager.acquire`` for the requesting customer —
    exactly like the account/positions/history providers. Without this the feed
    could be served from whatever account another customer last authenticated.

    A normal "no data for this symbol" answer is a client error
    (``MT5ClientError``, a ``ValueError``, mapped to the existing 404) that does
    NOT invalidate the session, while a terminal/IPC failure is translated into
    ``RuntimeError`` (the existing 503) and still invalidates it.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_market_data(self, symbol: str) -> Candle:
        # Authenticate (or reuse) the requesting customer's session and read inside
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
                try:
                    error = mt5_api.last_error()
                except Exception:  # expected third-party MT5 exception at this boundary only
                    error = None
                # Error code -1 means the symbol is not recognized by MT5
                # (invalid/unavailable symbol): the terminal ANSWERED, and what it
                # reports is a client-level result, so it is raised as
                # MT5ClientError — still a ValueError, so the existing 404 is
                # unchanged — and the session boundary keeps the verified session
                # instead of re-authenticating on the next request. Other error
                # codes indicate an MT5 infrastructure issue and stay a
                # RuntimeError (the existing 503) that still invalidates it.
                if _is_unrecognised_symbol(error):
                    raise MT5ClientError(f"No candle data returned for {symbol}")
                raise RuntimeError(f"MT5 API error for {symbol}: {error}")

            if len(rates) == 0:
                # The terminal answered normally: there are simply no bars for
                # this symbol/window. Same client-level result as above.
                raise MT5ClientError(f"No candle data returned for {symbol}")

            rate = rates[0]

            # OHLC are market prices: Decimal(str()) — never Decimal(raw_float).
            # volume is MT5 tick volume (a count), so it stays float.
            return Candle(
                timestamp=datetime.datetime.fromtimestamp(int(rate[0])),
                open=Decimal(str(rate[1])),
                high=Decimal(str(rate[2])),
                low=Decimal(str(rate[3])),
                close=Decimal(str(rate[4])),
                volume=float(rate[5]),
            )
