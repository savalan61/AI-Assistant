"""Portfolio intelligence service: account snapshot + open exposure, combined.

Composes the existing AccountInfoService and PositionService (the single
account and position architectures) and delegates the arithmetic to the pure
analysis module. This service never touches MT5 directly and never reads the
database.
"""
from datetime import UTC, datetime

from app.providers.account_info import AccountInfo
from app.providers.position import Position
from app.services.account import AccountInfoService
from app.services.portfolio_intelligence.portfolio import (
    PortfolioIntelligence,
    build_portfolio_intelligence,
)
from app.services.positions import PositionService


class PortfolioIntelligenceService:
    def __init__(self, account_service: AccountInfoService, position_service: PositionService):
        self._account = account_service
        self._positions = position_service

    def build(self, now: datetime | None = None) -> PortfolioIntelligence:
        """Build the current portfolio/exposure snapshot.

        Reads the account snapshot and the open positions through the existing
        services and combines them deterministically. Both reads block (MT5), so
        callers on the event loop must offload this call through the consolidated
        MT5 blocking boundary. ``now`` injects the ``as_of`` reference time
        (defaults to the current UTC time) so the timestamp stays explicit and
        testable; a naive ``now`` is rejected rather than silently read as local
        time.
        """
        if now is None:
            reference = datetime.now(UTC)
        elif now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("now must be timezone-aware")
        else:
            reference = now.astimezone(UTC)

        account: AccountInfo = self._account.get_account_info()
        positions: tuple[Position, ...] = self._positions.get_positions()
        return build_portfolio_intelligence(account, positions, reference)
