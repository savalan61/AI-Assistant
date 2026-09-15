"""Unit tests for MarketDataService using the deterministic fake provider.

Require none of: MT5, MetaTrader terminal, PostgreSQL, network, credentials, .env.
"""
from datetime import datetime

from app.providers.fake_market_data import FakeMarketDataProvider
from app.providers.market_data import Candle, MarketDataProvider
from app.services.market.market_data_service import MarketDataService
from decimal import Decimal

EXPECTED_CANDLE = Candle(
    timestamp=datetime(2024, 1, 15, 12, 0, 0),
    open=Decimal("100.0"),
    high=Decimal("110.0"),
    low=Decimal("95.0"),
    close=Decimal("105.0"),
    volume=1234.0,
)


def make_service() -> tuple[MarketDataService, FakeMarketDataProvider]:
    provider = FakeMarketDataProvider()
    return MarketDataService(provider), provider


def test_service_returns_expected_deterministic_candle():
    service, _ = make_service()

    candle = service.get_market_data("EURUSD")

    assert isinstance(candle, Candle)
    assert candle == EXPECTED_CANDLE


def test_fake_provider_satisfies_provider_contract():
    _, provider = make_service()

    assert isinstance(provider, MarketDataProvider)


def test_service_delegates_get_market_data_to_provider():
    service, provider = make_service()

    service.get_market_data("EURUSD")

    assert provider.requested_symbols == ["EURUSD"]


def test_service_passes_requested_symbol_to_provider():
    service, provider = make_service()

    service.get_market_data("EURUSD")
    service.get_market_data("XAUUSD")
    service.get_market_data("XAUUSD")

    assert provider.requested_symbols == ["EURUSD", "XAUUSD", "XAUUSD"]
