"""Financial context: one typed, read-only snapshot for future AI consumption.

Composes the existing financial reads — the account snapshot, the open
positions, the executed trade history, and the portfolio/exposure analysis —
into a single immutable context object. This is an internal service/domain
capability: it exposes no HTTP route of its own and calls no LLM.

Design notes:

* It reuses the existing AccountInfoService, PositionService and
  TradeHistoryService and never touches MT5 (or the database) directly.
* The account and the positions are read exactly once and the portfolio
  analysis is derived from that *same* snapshot via the existing
  ``build_portfolio_intelligence`` analysis, so the context can never contain a
  portfolio view that disagrees with the raw account/positions it reports (and
  no duplicate MT5 read is made). ``PortfolioIntelligenceService`` is this very
  analysis plus the two reads; composing the analysis directly is the same
  computation without re-reading the terminal.
* ``broker_id`` is supplied by the caller from the authenticated database user;
  nothing here derives tenant identity from a request.
* Strictly read-only: no order, position or account mutation exists on any of
  the services used, and no trading action is produced.
"""
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from app.providers.account_info import AccountInfo
from app.providers.position import Position
from app.providers.trade_history import TradeHistoryEntry
from app.services.account import AccountInfoService
from app.services.portfolio_intelligence import PortfolioIntelligence, build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

# Default look-back for the trade-history component. The window is a parameter,
# not a constant of the design: callers may request any positive number of days.
DEFAULT_TRADE_HISTORY_DAYS = 30


def _require_aware_utc(value: datetime, field: str) -> datetime:
    """Return ``value`` normalized to UTC, rejecting naive datetimes.

    Timezone handling is explicit at this boundary: a naive datetime is a caller
    contract violation, so it fails loudly instead of silently assuming the
    server's local timezone (the EconomicCalendarService convention).
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class FinancialContext(NamedTuple):
    """Immutable read-only snapshot of everything a future Agent needs."""

    # Tenant identity of the authenticated user the context was built for.
    broker_id: int
    # Reference time of the whole snapshot (UTC); the trade-history window ends
    # here and the portfolio analysis is stamped with it.
    as_of: datetime
    account: AccountInfo
    positions: tuple[Position, ...]
    trade_history: tuple[TradeHistoryEntry, ...]
    portfolio_intelligence: PortfolioIntelligence


class FinancialContextService:
    def __init__(
        self,
        account_service: AccountInfoService,
        position_service: PositionService,
        trade_history_service: TradeHistoryService,
    ):
        self._account = account_service
        self._positions = position_service
        self._trade_history = trade_history_service

    def build(
        self,
        broker_id: int,
        trade_history_days: int = DEFAULT_TRADE_HISTORY_DAYS,
        now: datetime | None = None,
    ) -> FinancialContext:
        """Build the financial context for one tenant.

        ``broker_id`` must come from the authenticated database user, never from
        request input. ``trade_history_days`` is the trade-history look-back in
        days (default 30) and must be a positive number of days. ``now`` injects
        the single reference time (defaults to the current UTC time) so the
        window and every timestamp stay explicit and testable; a naive ``now``
        is rejected rather than silently read as local time.

        Every underlying read blocks (MT5), so callers on the event loop must
        offload this call through the consolidated MT5 blocking boundary.
        Provider failures propagate unchanged (RuntimeError) for the API layer
        to translate at its own boundary.
        """
        if trade_history_days < 1:
            raise ValueError("trade_history_days must be at least 1")

        reference = _require_aware_utc(now, "now") if now is not None else datetime.now(UTC)
        window_from = reference - timedelta(days=trade_history_days)

        account: AccountInfo = self._account.get_account_info()
        positions: tuple[Position, ...] = self._positions.get_positions()
        trade_history: tuple[TradeHistoryEntry, ...] = self._trade_history.get_trade_history(
            window_from, reference
        )

        return FinancialContext(
            broker_id=broker_id,
            as_of=reference,
            account=account,
            positions=positions,
            trade_history=trade_history,
            # Derived from the same snapshot read above, so the portfolio view
            # always agrees with the account/positions in this context.
            portfolio_intelligence=build_portfolio_intelligence(account, positions, reference),
        )
