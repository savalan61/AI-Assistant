from typing import Any

import MetaTrader5 as mt5

from app.providers.position import Position, PositionType

# MT5 is a C extension without Python type stubs; Any bypasses Pylance type checking.
mt5_api: Any = mt5


class MT5PositionProvider:
    """Read-only MT5 open-positions provider.

    Mirrors the established MT5 boundary (see MT5AccountInfoProvider): the
    constructor attaches to the terminal's session, expected third-party
    failures are translated into RuntimeError, and shutdown() never raises
    during teardown. Read-only by design: no trading operation exists here.
    """

    def __init__(self):
        # Boundary rule (same as the other MT5 providers): the C extension
        # raises plain built-in Exception for terminal/IPC failures instead of
        # returning False; raw MT5 exceptions must never escape the provider.
        try:
            initialized = mt5_api.initialize()
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 terminal initialization failed") from exc
        if not initialized:
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 initialization failed: {error}")

    def get_positions(self) -> tuple[Position, ...]:
        # Same boundary rule: translate an expected plain Exception from the
        # MT5 C extension into RuntimeError, chaining kept for logs.
        try:
            raw_positions = mt5_api.positions_get()
        except Exception as exc:  # expected third-party MT5 exception at this boundary only
            raise RuntimeError("MT5 open positions request failed") from exc

        # None means the request itself failed (terminal/IPC problem), which
        # is an availability issue — unlike an empty tuple, which legitimately
        # means "no open positions" and must return 200 with positions: [].
        if raw_positions is None:
            error = mt5_api.last_error()
            raise RuntimeError(f"MT5 open positions unavailable: {error}")

        # Explicit construction into the application contract; the raw MT5
        # objects never leave the provider. MT5 encodes direction numerically
        # (0 = ORDER_TYPE_BUY, 1 = ORDER_TYPE_SELL) — unmapped values are a
        # contract violation and fail loudly rather than being guessed.
        positions: list[Position] = []
        for raw in raw_positions:
            if int(raw.type) == mt5_api.ORDER_TYPE_BUY:
                position_type = PositionType.BUY
            elif int(raw.type) == mt5_api.ORDER_TYPE_SELL:
                position_type = PositionType.SELL
            else:
                raise RuntimeError(f"MT5 returned an unknown position type: {raw.type}")
            positions.append(
                Position(
                    ticket=int(raw.ticket),
                    symbol=str(raw.symbol),
                    type=position_type,
                    volume=float(raw.volume),
                    open_price=float(raw.price_open),
                    current_price=float(raw.price_current),
                    profit=float(raw.profit),
                )
            )
        return tuple(positions)

    def shutdown(self) -> None:
        # Release the terminal connection. Runs during application shutdown, so
        # any failure (e.g. terminal already gone) is deliberately swallowed:
        # shutdown must never raise at process exit.
        try:
            mt5_api.shutdown()
        except Exception:  # expected third-party MT5 failure during teardown only
            pass
