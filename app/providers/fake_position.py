from app.providers.position import Position, PositionProvider, PositionType

# Deterministic open positions returned for every call; fixed values keep tests
# repeatable (the FakeMarketDataProvider pattern, applied to positions).
_FAKE_POSITIONS: tuple[Position, ...] = (
    Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=0.10,
        open_price=3642.50,
        current_price=3648.20,
        profit=57.00,
    ),
    Position(
        ticket=987654321,
        symbol="EURUSD",
        type=PositionType.SELL,
        volume=1.00,
        open_price=1.0850,
        current_price=1.0820,
        profit=-30.00,
    ),
)


# Minimal in-memory provider for tests; implements the provider contract without MT5.
class FakePositionProvider(PositionProvider):
    def __init__(self, positions: tuple[Position, ...] = _FAKE_POSITIONS):
        self.positions = positions
        self.call_count = 0

    def get_positions(self) -> tuple[Position, ...]:
        self.call_count += 1
        return self.positions
