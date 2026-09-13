from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
def get_market_data(symbol: str, service: MarketDataService = Depends(get_market_data_service)):
    # ValueError means invalid/unavailable symbol (client error 404);
    # RuntimeError means MT5 infrastructure failure (server error 503).
    try:
        return service.get_market_data(symbol)
    except ValueError:
        raise HTTPException(status_code=404, detail="Market data unavailable for the requested symbol")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
