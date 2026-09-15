"""Trade History API: read-only MT5 executed trades for authenticated users."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.api.numeric import DecimalAsNumber

from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_trade_history_service
from app.db.models import User
from app.providers.trade_history import TradeHistoryEntry
from app.services.trade_history import TradeHistoryService

router = APIRouter()

# A read-only window guard: UTC-awareness and from < to are validated by the
# endpoint (client errors, 422/400) before any MT5 call is made.


class TradeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    order_ticket: int
    symbol: str
    type: str
    # Decimal in the contract, JSON numbers on the wire (unchanged format);
    # SL/TP stay nullable exactly as in the contract (None = MT5 supplied no
    # protective level — never a fabricated 0).
    volume: DecimalAsNumber
    price: DecimalAsNumber
    profit: DecimalAsNumber
    time: datetime
    close_reason: str | None
    stop_loss: DecimalAsNumber | None
    take_profit: DecimalAsNumber | None


class TradeHistoryResponse(BaseModel):
    # Wrapped contract: trades is always an array, never a bare list and
    # never a 404, so "no executed trades in the window" is a normal 200.
    trades: list[TradeResponse]


def _require_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise HTTPException(status_code=400, detail=f"'{field}' must be a UTC-aware ISO 8601 datetime")
    return value.astimezone(UTC)


@router.get("/trade-history", response_model=TradeHistoryResponse)
async def get_trade_history(
    # Window boundaries are required query parameters. FastAPI validates
    # presence and ISO 8601 syntax (422); UTC-awareness and from < to are
    # enforced explicitly below.
    from_time: datetime = Query(..., alias="from", description="Window start (UTC-aware ISO 8601)"),
    to_time: datetime = Query(..., alias="to", description="Window end (UTC-aware ISO 8601)"),
    # Authentication boundary: any active user (customer or broker admin) may
    # read the executed trades of the MT5 account this process is attached to.
    # Tenant identity stays with the database-backed User; no account/login
    # parameter is accepted from the client.
    current_user: User = Depends(get_current_user),
    service: TradeHistoryService = Depends(get_trade_history_service),
) -> TradeHistoryResponse:
    from_time = _require_utc(from_time, "from")
    to_time = _require_utc(to_time, "to")
    if from_time >= to_time:
        raise HTTPException(status_code=400, detail="'from' must be earlier than 'to'")

    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop through the consolidated MT5 blocking boundary.
    # RuntimeError means an MT5 infrastructure failure (server error 503);
    # the generic detail never leaks provider internals.
    try:
        trades = await run_mt5_call(service.get_trade_history, from_time, to_time)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Trade history service temporarily unavailable")
    return TradeHistoryResponse(trades=[TradeResponse.model_validate(t) for t in trades])
