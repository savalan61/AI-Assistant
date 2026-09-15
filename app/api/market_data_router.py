from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.numeric import DecimalAsNumber
from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_market_data_service
from app.db.models import User
from app.services.market import MarketDataService

router = APIRouter()


# Maps the Candle contract to a JSON-safe response schema. OHLC are Decimal in
# the contract and JSON numbers on the wire; tick volume stays float.
class CandleResponse(BaseModel):
    timestamp: datetime
    open: DecimalAsNumber
    high: DecimalAsNumber
    low: DecimalAsNumber
    close: DecimalAsNumber
    volume: float


@router.get("/market-data/{symbol}", response_model=CandleResponse)
async def get_market_data(
    symbol: str,
    # Authentication boundary: a valid Bearer JWT for an active user is
    # required. get_current_user loads the User from the database, so the
    # authoritative broker_id comes from the record, never from token claims.
    # No further authorization is applied yet; the market-data path itself is
    # not user-scoped at this stage.
    current_user: User = Depends(get_current_user),
    service: MarketDataService = Depends(get_market_data_service),
):
    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop through the consolidated MT5 blocking boundary.
    # ValueError means invalid/unavailable symbol (client error 404);
    # RuntimeError means MT5 infrastructure failure (server error 503).
    try:
        candle = await run_mt5_call(service.get_market_data, symbol)
    except ValueError:
        raise HTTPException(status_code=404, detail="Market data unavailable for the requested symbol")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
    return candle
