from decimal import Decimal

from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.providers.position import Position, PositionProvider, PositionType


class MT5PositionProvider(PositionProvider):
    """Read-only MT5 open-positions provider, scoped to one tenant's session.

    Tenant scope: the provider is a cheap per-request object carrying the
    authenticated tenant's credentials; the raw MT5 call is made inside
    ``MT5SessionManager.acquire`` so the terminal is authenticated as *that*
    tenant, under the process-wide session lock, for the whole read. Expected
    third-party failures are translated into RuntimeError. Read-only by design:
    no trading operation exists here.
    """

    def __init__(self, session_manager: MT5SessionManager, credentials: MT5AccountCredentials) -> None:
        self._session = session_manager
        self._credentials = credentials

    def get_positions(self) -> tuple[Position, ...]:
        # Authenticate (or reuse) the requesting tenant's session and read inside
        # that authenticated span: the lock is held until the read completes, so
        # another tenant can never re-authenticate the terminal mid-read.
        with self._session.acquire(self._credentials) as mt5_api:
            # Boundary rule: the C extension raises plain built-in Exception for
            # terminal/IPC failures instead of returning False. This try block
            # covers only the external MT5 call; raw MT5 exceptions must never
            # escape the provider.
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
                # Money/quantity conversion is Decimal(str()) — never
                # Decimal(raw_float), which would bake in the binary-float
                # artifact the Decimal conversion exists to remove.
                positions.append(
                    Position(
                        ticket=int(raw.ticket),
                        symbol=str(raw.symbol),
                        type=position_type,
                        volume=Decimal(str(raw.volume)),
                        open_price=Decimal(str(raw.price_open)),
                        current_price=Decimal(str(raw.price_current)),
                        profit=Decimal(str(raw.profit)),
                    )
                )
            return tuple(positions)
