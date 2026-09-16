"""Unit tests for MarketDataService using the deterministic fake providers.

Require none of: MT5, MetaTrader terminal, PostgreSQL, network, credentials, .env.
Since Step 53 the service resolves the requested symbol through the instrument
service first; the instrument fake is in-memory, so the real InstrumentService,
the real resolution rules and the recording candle provider all run offline.
"""
from datetime import datetime

import pytest

from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.fake_market_data import FakeMarketDataProvider
from app.providers.instrument import Instrument, TradeMode
from app.providers.market_data import Candle, MarketDataProvider
from app.services.instruments import InstrumentService
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


# --- Step 53: resolution through the instrument catalog -------------------------------


def instrument_row(symbol: str) -> Instrument:
    return Instrument(
        symbol=symbol,
        name=None,
        asset_class=None,
        base_currency=None,
        quote_currency=None,
        digits=5,
        trade_mode=TradeMode.FULL,
    )


def make_resolving_service(*catalog: str) -> tuple[MarketDataService, FakeMarketDataProvider, FakeInstrumentProvider]:
    candle_provider = FakeMarketDataProvider()
    instruments = FakeInstrumentProvider(instruments=tuple(instrument_row(s) for s in catalog))
    service = MarketDataService(candle_provider, instrument_service=InstrumentService(instruments))
    return service, candle_provider, instruments


@pytest.mark.parametrize("requested", ["xauusd", "XAuUsD", "XAUUSD"])
def test_case_insensitive_requests_read_the_brokers_canonical_symbol(requested: str):
    service, provider, _ = make_resolving_service("XAUUSD")

    service.get_market_data(requested)

    # The provider is asked for the broker's own spelling, never the caller's.
    assert provider.requested_symbols == ["XAUUSD"]


def test_a_suffixed_broker_catalog_resolves_the_base_symbol():
    service, provider, _ = make_resolving_service("XAUUSD.r")

    service.get_market_data("xauusd")

    assert provider.requested_symbols == ["XAUUSD.r"]


def test_several_variants_fail_closed_and_no_candle_is_read():
    service, provider, _ = make_resolving_service("XAUUSD.r", "XAUUSD.m")

    with pytest.raises(ValueError) as exc_info:
        service.get_market_data("XAUUSD")

    assert "ambiguous" in str(exc_info.value)
    assert provider.requested_symbols == []  # decided before any candle read


def test_an_unknown_symbol_is_an_error_and_no_candle_is_read():
    service, provider, _ = make_resolving_service("EURUSD")

    with pytest.raises(ValueError):
        service.get_market_data("NOSUCHSYMBOL")

    assert provider.requested_symbols == []


def test_a_catalog_failure_propagates_and_no_candle_is_read():
    class FailingInstrumentProvider(FakeInstrumentProvider):
        def get_instrument(self, symbol: str) -> Instrument:
            raise RuntimeError("MT5 instrument lookup failed")

        def list_instruments(self) -> tuple[Instrument, ...]:
            raise RuntimeError("MT5 instrument catalog request failed")

    provider = FakeMarketDataProvider()
    service = MarketDataService(provider, instrument_service=InstrumentService(FailingInstrumentProvider()))

    with pytest.raises(RuntimeError):
        service.get_market_data("XAUUSD")

    assert provider.requested_symbols == []


def test_resolution_costs_one_symbol_lookup_and_at_most_one_catalog_scan():
    # The whole point of reusing the instrument service: no duplicate catalog
    # read (and no second provider) for the same request. A name the vendor
    # matches as written costs one lookup and no scan...
    exact_service, _, exact = make_resolving_service("EURUSD", "XAUUSD")
    exact_service.get_market_data("XAUUSD")
    assert exact.get_calls == ["XAUUSD"]
    assert exact.list_calls == 0

    # ...and a spelling it does not match as written costs exactly one catalog
    # scan, never more.
    missing_service, _, missing = make_resolving_service("XAUUSD.r")
    missing_service.get_market_data("xauusd")
    assert missing.get_calls == ["xauusd"]
    assert missing.list_calls == 1


def test_without_an_instrument_service_the_symbol_is_passed_through():
    # A caller that has already verified the spelling (or a deployment with no
    # catalog) keeps the exact behaviour that existed before resolution.
    service, provider = make_service()

    service.get_market_data("xauusd")

    assert provider.requested_symbols == ["xauusd"]


def test_resolution_is_deterministic():
    service, provider, _ = make_resolving_service("XAUUSD.r", "EURUSD")

    service.get_market_data("XAUUSD")
    service.get_market_data("XAUUSD")

    assert provider.requested_symbols == ["XAUUSD.r", "XAUUSD.r"]


def test_exact_and_unrelated_symbols_are_unaffected_by_resolution():
    service, provider, _ = make_resolving_service("EURUSD", "AAPL", "COFFEE")

    for symbol in ("EURUSD", "AAPL", "COFFEE"):
        assert service.get_market_data(symbol) == EXPECTED_CANDLE

    assert provider.requested_symbols == ["EURUSD", "AAPL", "COFFEE"]
