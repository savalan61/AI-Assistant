from decimal import Decimal

from app.providers.position import Position, PositionProvider, PositionType

# Deterministic open positions returned for every call; fixed values keep tests
# repeatable (the FakeMarketDataProvider pattern, applied to positions). Money
# and volume are Decimal, exactly as the real MT5 provider produces them.
_FAKE_POSITIONS: tuple[Position, ...] = (
    Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("3642.50"),
        current_price=Decimal("3648.20"),
        profit=Decimal("57.00"),
    ),
    Position(
        ticket=987654321,
        symbol="EURUSD",
        type=PositionType.SELL,
        volume=Decimal("1.00"),
        open_price=Decimal("1.0850"),
        current_price=Decimal("1.0820"),
        profit=Decimal("-30.00"),
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
