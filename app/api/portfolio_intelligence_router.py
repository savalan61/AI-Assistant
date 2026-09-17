"""Portfolio Intelligence API: read-only account + exposure summary.

Combines the authenticated user's MT5 account snapshot and open positions into
a deterministic portfolio/exposure result. Strictly read-only: no price is
predicted, no BUY/SELL action is produced, and no trading endpoint exists here.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_portfolio_intelligence_service
from app.db.models import User
from app.api.numeric import DecimalAsNumber
from app.services.portfolio_intelligence import PortfolioIntelligenceService

router = APIRouter()


# Maps the SymbolExposure contract to a JSON-safe schema; from_attributes lets
# model_validate() read the NamedTuple directly (same convention as
# PositionResponse / TradeResponse).
class SymbolExposureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    # Decimal in the contract, JSON numbers on the wire (unchanged format).
    buy_volume: DecimalAsNumber
    sell_volume: DecimalAsNumber
    net_volume: DecimalAsNumber
    position_count: int


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Margin-based risk band (FLAT / LOW / ELEVATED / HIGH / UNKNOWN).
    level: str
    # Deterministic factual reason for the band. Never a forecast.
    basis: str


class PortfolioIntelligenceResponse(BaseModel):
    # broker_id is the authenticated user's own broker (this deployment's single broker), echoed for the
    # client; it is never accepted as request input.
    broker_id: int
    as_of: datetime
    account_currency: str
    # Money and volume are Decimal in the contract, JSON numbers on the wire.
    balance: DecimalAsNumber
    equity: DecimalAsNumber
    margin: DecimalAsNumber
    free_margin: DecimalAsNumber
    # A ratio, not money: float in the contract and float on the wire.
    margin_level: float
    open_positions: int
    buy_positions: int
    sell_positions: int
    symbols: list[str]
    total_volume: DecimalAsNumber
    buy_volume: DecimalAsNumber
    sell_volume: DecimalAsNumber
    # BUY volume minus SELL volume across all open positions.
    directional_balance: DecimalAsNumber
    exposure: list[SymbolExposureResponse]
    risk: RiskAssessmentResponse


@router.get("/portfolio-intelligence", response_model=PortfolioIntelligenceResponse)
async def get_portfolio_intelligence(
    # Authentication boundary: any active user (customer, admin or super_admin)
    # may read the portfolio intelligence of the MT5 account this process is
    # attached to. Tenant identity stays with the database-backed User; no
    # broker_id/user_id parameter is accepted, so a caller can never widen or
    # redirect the scope of the response.
    current_user: User = Depends(get_current_user),
    service: PortfolioIntelligenceService = Depends(get_portfolio_intelligence_service),
) -> PortfolioIntelligenceResponse:
    # The service reads the account and the open positions, both blocking MT5
    # calls, so the whole call is offloaded through the consolidated MT5
    # blocking boundary. RuntimeError means an MT5 infrastructure failure
    # (server error 503); the generic detail never leaks provider internals.
    try:
        portfolio = await run_mt5_call(service.build)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Portfolio intelligence service temporarily unavailable")

    return PortfolioIntelligenceResponse(
        # Tenant identity of the authenticated user, read from the database
        # record — never from the request or a token claim.
        broker_id=current_user.broker_id,
        as_of=portfolio.as_of,
        account_currency=portfolio.account_currency,
        balance=portfolio.balance,
        equity=portfolio.equity,
        margin=portfolio.margin,
        free_margin=portfolio.free_margin,
        margin_level=portfolio.margin_level,
        open_positions=portfolio.open_positions,
        buy_positions=portfolio.buy_positions,
        sell_positions=portfolio.sell_positions,
        symbols=list(portfolio.symbols),
        total_volume=portfolio.total_volume,
        buy_volume=portfolio.buy_volume,
        sell_volume=portfolio.sell_volume,
        directional_balance=portfolio.directional_balance,
        exposure=[SymbolExposureResponse.model_validate(item) for item in portfolio.exposure],
        risk=RiskAssessmentResponse(level=portfolio.risk.level.value, basis=portfolio.risk.basis),
    )
