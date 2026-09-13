from fastapi import HTTPException

from app.providers import MarketDataProvider, MT5MarketDataProvider
from app.services.market import MarketDataService


# Composition root for market-data wiring: this is the only place that knows
# the concrete provider. The provider is injected through the service, keeping
# the HTTP layer independent of MT5.
# MT5 initialization failure at this boundary is a service availability issue (503).
def get_market_data_service() -> MarketDataService:
    try:
        provider: MarketDataProvider = MT5MarketDataProvider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
    return MarketDataService(provider)
