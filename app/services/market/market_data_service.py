from app.providers.market_data import Candle, MarketDataProvider


class MarketDataService:
    def __init__(self, provider: MarketDataProvider):
        self._provider = provider

    def get_market_data(self, symbol: str) -> Candle:
        return self._provider.get_market_data(symbol)
