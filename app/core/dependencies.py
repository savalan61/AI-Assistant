import threading

from fastapi import HTTPException

from app.providers import MarketDataProvider, MT5MarketDataProvider
from app.services.market import MarketDataService

# Process-wide provider cache. It stays None until a construction succeeds, so
# a failed MT5 initialization is never cached and later requests may retry.
_provider: MarketDataProvider | None = None
_provider_lock = threading.Lock()


# Composition root for market-data wiring: this is the only place that knows
# the concrete provider. The provider is injected through the service, keeping
# the HTTP layer independent of MT5.
def get_market_data_provider() -> MarketDataProvider:
    # Lazy singleton: construct MT5MarketDataProvider once per process. The lock
    # keeps "constructed once" true because dependencies run in worker threads.
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = MT5MarketDataProvider()
    return _provider


def get_market_data_service() -> MarketDataService:
    # MT5 initialization failure at this boundary is a service availability issue (503).
    try:
        provider = get_market_data_provider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
    return MarketDataService(provider)


def shutdown_market_data() -> None:
    # Shut down the cached provider, if any, and clear the cache so the next
    # request constructs a fresh one. Safe to call when nothing was initialized.
    global _provider
    provider, _provider = _provider, None
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()
