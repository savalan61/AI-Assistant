from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.providers import MarketDataProvider, MT5MarketDataProvider
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


# Provider is injected through the service to keep the route independent of MT5.
def get_market_data_service() -> MarketDataService:
    provider: MarketDataProvider = MT5MarketDataProvider()
    return MarketDataService(provider)


@router.get("/market-data/{symbol}", response_model=CandleResponse)
def get_market_data(symbol: str, service: MarketDataService = Depends(get_market_data_service)):
    return service.get_market_data(symbol)
