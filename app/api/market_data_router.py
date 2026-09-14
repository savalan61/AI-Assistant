from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.dependencies import get_market_data_service
from app.services.market import MarketDataService

router = APIRouter()


# Maps the Candle contract to a JSON-safe response schema.
class CandleResponse(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@router.get("/market-data/{symbol}", response_model=CandleResponse)
async def get_market_data(symbol: str, service: MarketDataService = Depends(get_market_data_service)):
    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop into the worker threadpool.
    # ValueError means invalid/unavailable symbol (client error 404);
    # RuntimeError means MT5 infrastructure failure (server error 503).
    try:
        candle = await run_in_threadpool(service.get_market_data, symbol)
    except ValueError:
        raise HTTPException(status_code=404, detail="Market data unavailable for the requested symbol")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
    return candle
