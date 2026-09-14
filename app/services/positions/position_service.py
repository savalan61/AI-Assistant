from app.providers.position import Position, PositionProvider


# Depends on the provider abstraction, not on MT5 directly (mirrors
# AccountInfoService). No database access, no business calculations: open
# positions are a passthrough read.
class PositionService:
    def __init__(self, provider: PositionProvider):
        self._provider = provider

    def get_positions(self) -> tuple[Position, ...]:
        return self._provider.get_positions()
