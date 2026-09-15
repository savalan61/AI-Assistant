"""Deterministic portfolio / exposure analysis (read-only, no LLM).

Pure calculations over the existing AccountInfo and Position contracts. This
module makes no MT5 call, reads no database, and produces no trading action:
every value is derived directly from the account snapshot and the open
positions, so the result is reproducible for a given input.

Two deliberate constraints:

1. Only data that actually exists is computed. The Position contract carries
   ticket/symbol/type/volume/open_price/current_price/profit, so this module
   aggregates *volume by direction and by symbol*. It deliberately does NOT
   invent monetary exposure, market value, leverage or percentage exposure:
   those need instrument specifications (contract size, tick value) and a
   valuation currency that the current contracts do not reliably carry.
2. Risk is classified only from a field that exists (the account's margin
   level) and is reported as UNKNOWN whenever that field cannot support a
   meaningful classification. No price is predicted, no liquidation is
   forecast, and no BUY/SELL/OPEN/CLOSE/MODIFY action is produced anywhere.
"""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from app.providers.account_info import AccountInfo
from app.providers.position import Position, PositionType


class PortfolioRiskLevel(StrEnum):
    """Margin-based account risk band (an exposure observation, not a forecast).

    FLAT       no open positions, so there is no market exposure to assess.
    LOW        margin level comfortably above the used-margin reference bands.
    ELEVATED   a moderate share of equity is committed as margin.
    HIGH       a large share of equity is committed as margin.
    UNKNOWN    positions exist but the account reports no usable margin level,
               so no honest classification is possible.
    """

    FLAT = "FLAT"
    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


# Margin-level bands, expressed as the account's margin level percentage
# (equity / used margin * 100). These are coarse, deterministic observation
# bands — not broker stop-out levels, which this service does not know.
_LOW_MARGIN_LEVEL = 500.0
_ELEVATED_MARGIN_LEVEL = 200.0


class RiskAssessment(NamedTuple):
    """A risk band plus the factual, deterministic reason for it."""

    level: PortfolioRiskLevel
    basis: str


class SymbolExposure(NamedTuple):
    """Aggregated open volume for one symbol, split by direction."""

    symbol: str
    buy_volume: Decimal
    sell_volume: Decimal
    net_volume: Decimal
    position_count: int


class PortfolioIntelligence(NamedTuple):
    """The combined account + exposure snapshot handed to the API/AI layer.

    Money and volume fields are Decimal (Step 37); margin_level stays float
    (a ratio, not money) exactly as in the AccountInfo contract.
    """

    as_of: datetime
    account_currency: str
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    margin_level: float
    open_positions: int
    buy_positions: int
    sell_positions: int
    symbols: tuple[str, ...]
    total_volume: Decimal
    buy_volume: Decimal
    sell_volume: Decimal
    directional_balance: Decimal
    exposure: tuple[SymbolExposure, ...]
    risk: RiskAssessment


def aggregate_exposure(positions: tuple[Position, ...]) -> tuple[SymbolExposure, ...]:
    """Aggregate open positions by symbol, ordered by symbol.

    One entry per distinct symbol; ``net_volume`` is buy volume minus sell
    volume for that symbol. Ordering is by symbol (ascending) so the result is
    deterministic and independent of provider row order.
    """
    # All aggregation is Decimal over Decimal: the zero start value is a
    # Decimal, so no float ever enters the arithmetic.
    buy: dict[str, Decimal] = {}
    sell: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    for position in positions:
        symbol = position.symbol
        counts[symbol] = counts.get(symbol, 0) + 1
        if position.type is PositionType.BUY:
            buy[symbol] = buy.get(symbol, Decimal(0)) + position.volume
        else:
            sell[symbol] = sell.get(symbol, Decimal(0)) + position.volume

    return tuple(
        SymbolExposure(
            symbol=symbol,
            buy_volume=buy.get(symbol, Decimal(0)),
            sell_volume=sell.get(symbol, Decimal(0)),
            net_volume=buy.get(symbol, Decimal(0)) - sell.get(symbol, Decimal(0)),
            position_count=counts[symbol],
        )
        for symbol in sorted(counts)
    )


def classify_portfolio_risk(positions: tuple[Position, ...], margin_level: float) -> RiskAssessment:
    """Classify exposure risk from data that actually exists.

    The only risk-relevant field the contracts reliably carry is the account's
    margin level, so classification is based on it alone: no positions is FLAT,
    a non-positive margin level alongside open positions is UNKNOWN (the field
    cannot support a claim), and otherwise the margin level is banded. This is a
    descriptive observation of committed margin — never a prediction, and never
    a recommendation to trade.
    """
    if not positions:
        return RiskAssessment(
            PortfolioRiskLevel.FLAT,
            "No open positions, so there is no market exposure to classify.",
        )
    if margin_level <= 0:
        return RiskAssessment(
            PortfolioRiskLevel.UNKNOWN,
            "Open positions exist but the account reports no usable margin level, "
            "so the exposure cannot be classified.",
        )
    if margin_level >= _LOW_MARGIN_LEVEL:
        return RiskAssessment(
            PortfolioRiskLevel.LOW,
            f"Margin level is {margin_level:.2f}%; used margin is small relative to equity.",
        )
    if margin_level >= _ELEVATED_MARGIN_LEVEL:
        return RiskAssessment(
            PortfolioRiskLevel.ELEVATED,
            f"Margin level is {margin_level:.2f}%; a moderate share of equity is committed as margin.",
        )
    return RiskAssessment(
        PortfolioRiskLevel.HIGH,
        f"Margin level is {margin_level:.2f}%; a large share of equity is committed as margin.",
    )


def build_portfolio_intelligence(
    account: AccountInfo, positions: tuple[Position, ...], as_of: datetime
) -> PortfolioIntelligence:
    """Combine an account snapshot and open positions into one analysis.

    Pure and deterministic: the only inputs are the two contracts and the
    reference timestamp, and every aggregate is a direct sum over the positions.
    """
    ordered = tuple(sorted(positions, key=lambda position: (position.symbol, position.ticket)))

    # Decimal sums over Decimal volumes: sum() starts from int 0, which Decimal
    # accepts without losing exactness — no float is ever involved.
    buy_volume = sum((position.volume for position in ordered if position.type is PositionType.BUY), Decimal(0))
    sell_volume = sum((position.volume for position in ordered if position.type is PositionType.SELL), Decimal(0))
    buy_positions = sum(1 for position in ordered if position.type is PositionType.BUY)

    return PortfolioIntelligence(
        as_of=as_of,
        account_currency=account.currency,
        balance=account.balance,
        equity=account.equity,
        margin=account.margin,
        free_margin=account.free_margin,
        margin_level=account.margin_level,
        open_positions=len(ordered),
        buy_positions=buy_positions,
        sell_positions=len(ordered) - buy_positions,
        symbols=tuple(sorted({position.symbol for position in ordered})),
        total_volume=buy_volume + sell_volume,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        directional_balance=buy_volume - sell_volume,
        exposure=aggregate_exposure(ordered),
        risk=classify_portfolio_risk(ordered, account.margin_level),
    )
