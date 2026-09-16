from typing import Any

from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.instrument import Instrument, InstrumentProvider, TradeMode

# MT5's documented numeric trade modes (SYMBOL_TRADE_MODE_*), mapped explicitly
# by value rather than by attribute lookup so a value the terminal does not
# define degrades into a clear contract error instead of an AttributeError in
# the middle of a read. An unrecognised value is never guessed.
_TRADE_MODES: dict[int, TradeMode] = {
    0: TradeMode.DISABLED,  # SYMBOL_TRADE_MODE_DISABLED
    1: TradeMode.LONG_ONLY,  # SYMBOL_TRADE_MODE_LONGONLY
    2: TradeMode.SHORT_ONLY,  # SYMBOL_TRADE_MODE_SHORTONLY
    3: TradeMode.CLOSE_ONLY,  # SYMBOL_TRADE_MODE_CLOSEONLY
    4: TradeMode.FULL,  # SYMBOL_TRADE_MODE_FULL
}


def _optional_text(value: Any) -> str | None:
    """Vendor text that is missing or whitespace-only means "not provided"."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _asset_class(path: Any) -> str | None:
    """The broker's own top-level symbol-group segment.

    MT5 groups symbols by a backslash path (``Forex\\EURUSD``, ``Metals\\XAUUSD``),
    so the first non-empty segment is the broker's own asset grouping. This is
    reported as given: it is never mapped onto a taxonomy of ours.
    """
    if path is None:
        return None
    for segment in str(path).split("\\"):
        stripped = segment.strip()
        if stripped:
            return stripped
    return None


class MT5InstrumentProvider(InstrumentProvider):
    """Read-only MT5 instrument-discovery provider, scoped to one tenant's session.

    Tenant scope: every broker's terminal offers its own symbol catalog, and the
    terminal is authenticated as one account at a time process-wide, so the
    catalog is read inside ``MT5SessionManager.acquire`` for the requesting
    tenant — the same boundary as the account/positions/market-data providers.
    Expected third-party failures are translated into RuntimeError, which the API
    maps to the existing generic 503.

    Read-only by design: the only MT5 calls here are ``symbol_info`` and
    ``symbols_get``. ``symbol_select`` is deliberately NOT called — it changes
    terminal/Market Watch state, and discovery must not mutate anything.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_instrument(self, symbol: str) -> Instrument:
        # Authenticate (or reuse) the requesting tenant's session and read inside
        # that authenticated span: the lock is held until the read completes, so
        # another tenant can never re-authenticate the terminal mid-read.
        with self._session.acquire(self._credentials) as mt5_api:
            # Boundary rule: the C extension raises plain built-in Exception for
            # terminal/IPC failures instead of returning a value. This try block
            # covers only the external MT5 call; raw MT5 exceptions must never
            # escape the provider.
            try:
                raw = mt5_api.symbol_info(symbol)
            except Exception as exc:  # expected third-party MT5 exception at this boundary only
                raise RuntimeError("MT5 instrument lookup failed") from exc

            # None is MT5's answer for "this terminal does not know that symbol",
            # i.e. a client error (404). A terminal/IPC problem raises instead,
            # so the two cases cannot be confused.
            if raw is None:
                raise ValueError(f"MT5 does not offer instrument {symbol}")

            return self._to_instrument(raw)

    def list_instruments(self) -> tuple[Instrument, ...]:
        # Same tenant-scoped, lock-held read span as get_instrument().
        with self._session.acquire(self._credentials) as mt5_api:
            try:
                raw_symbols = mt5_api.symbols_get()
            except Exception as exc:  # expected third-party MT5 exception at this boundary only
                raise RuntimeError("MT5 instrument catalog request failed") from exc

            # None means the request itself failed (terminal/IPC problem), which
            # is an availability issue — unlike an empty tuple, which legitimately
            # means "this broker offers nothing matching".
            if raw_symbols is None:
                error = mt5_api.last_error()
                raise RuntimeError(f"MT5 instrument catalog unavailable: {error}")

            return tuple(self._to_instrument(raw) for raw in raw_symbols)

    @staticmethod
    def _to_instrument(raw: Any) -> Instrument:
        """Map one raw MT5 symbol record into the application contract.

        A malformed vendor record is a contract violation, not a client error:
        it is translated into RuntimeError so it can never be reported as
        "no such instrument" (which is ValueError/404).
        """
        try:
            symbol = str(raw.name).strip()
            digits = None if raw.digits is None else int(raw.digits)
            trade_mode = _TRADE_MODES[int(raw.trade_mode)]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("MT5 returned a malformed instrument record") from exc
        if not symbol:
            raise RuntimeError("MT5 returned an instrument without a symbol")

        return Instrument(
            symbol=symbol,
            # MT5's human-readable instrument name lives in ``description``
            # (``name`` is the symbol itself).
            name=_optional_text(getattr(raw, "description", None)),
            asset_class=_asset_class(getattr(raw, "path", None)),
            base_currency=_optional_text(getattr(raw, "currency_base", None)),
            quote_currency=_optional_text(getattr(raw, "currency_profit", None)),
            digits=digits,
            trade_mode=trade_mode,
        )
