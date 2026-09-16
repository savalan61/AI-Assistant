"""Tests for InstrumentService (deterministic discovery and resolution).

Offline and deterministic: every test drives the service through a fake
provider, so no MT5 terminal, credentials, network or database is involved. The
suite pins the rules the service owns — normalisation, exact-then-unique
resolution, ordering, searching, and the response bound — plus the generic
guarantee that arbitrary broker symbols (with or without a suffix, with or
without any metadata) resolve through the same path.
"""
import pytest

from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, InstrumentProvider, TradeMode
from app.services.instruments import InstrumentCatalog, InstrumentService, normalize_symbol


def instrument(symbol: str, name: str | None = None, asset_class: str | None = "Forex") -> Instrument:
    return Instrument(
        symbol=symbol,
        name=name,
        asset_class=asset_class,
        base_currency=None,
        quote_currency=None,
        digits=2,
        trade_mode=TradeMode.FULL,
    )


@pytest.fixture()
def catalog() -> tuple[Instrument, ...]:
    """A small, deliberately unsorted catalog spanning several asset classes."""
    return (
        instrument("XAUUSD", "Gold vs US Dollar", "Metals"),
        instrument("AAPL", "Apple Inc.", "Shares"),
        instrument("EURUSD", "Euro vs US Dollar"),
        instrument("XAUUSD.r", "Gold vs US Dollar (retail)", "Metals"),
        instrument("NICKEL", None, None),
    )


@pytest.fixture()
def service(catalog) -> tuple[InstrumentService, FakeInstrumentProvider]:
    provider = FakeInstrumentProvider(instruments=catalog)
    return InstrumentService(provider), provider


# --- normalisation ------------------------------------------------------------------


def test_symbol_is_trimmed_but_never_re_cased():
    assert normalize_symbol("  XAUUSD.r  ") == "XAUUSD.r"
    assert normalize_symbol("xauusd") == "xauusd"  # case belongs to resolution, not normalisation


@pytest.mark.parametrize("symbol", ["", "   ", "\t", "BAD\x00SYMBOL", "SYM\nBOL"])
def test_unusable_symbol_input_is_rejected(symbol):
    with pytest.raises(ValueError):
        normalize_symbol(symbol)


def test_oversized_symbol_input_is_rejected():
    with pytest.raises(ValueError):
        normalize_symbol("X" * 65)


# --- resolution ---------------------------------------------------------------------


def test_exact_symbol_resolves_to_the_broker_record(service):
    svc, provider = service

    resolved = svc.resolve("XAUUSD")

    assert resolved.symbol == "XAUUSD"
    assert resolved.name == "Gold vs US Dollar"
    assert provider.get_calls == ["XAUUSD"]
    assert provider.list_calls == 0  # one read, no catalog scan on a direct hit


def test_resolution_preserves_the_brokers_own_spelling(service):
    svc, _ = service

    # A caller typing lower case still receives the broker's spelling, and a
    # suffix spelling resolves too.
    assert svc.resolve("xauusd.r").symbol == "XAUUSD.r"
    assert svc.resolve("  eurusd  ").symbol == "EURUSD"


def test_unknown_symbol_raises_value_error(service):
    svc, _ = service

    with pytest.raises(ValueError):
        svc.resolve("NOTLISTED")


def test_ambiguous_case_only_symbols_are_not_guessed():
    provider = FakeInstrumentProvider(
        instruments=(instrument("GOLD"), instrument("gold"), instrument("Gold.x"))
    )
    svc = InstrumentService(provider)

    with pytest.raises(ValueError) as exc_info:
        svc.resolve("Gold")

    assert "ambiguous" in str(exc_info.value)


def test_symbol_without_any_metadata_still_resolves(service):
    svc, _ = service

    resolved = svc.resolve("NICKEL")

    assert resolved.name is None
    assert resolved.asset_class is None
    assert resolved.digits == 2  # whatever the broker provided is passed through


def test_provider_availability_failure_is_not_treated_as_unknown_symbol():
    class FailingProvider(InstrumentProvider):
        def get_instrument(self, symbol: str) -> Instrument:
            raise RuntimeError("MT5 instrument lookup failed")

        def list_instruments(self) -> tuple[Instrument, ...]:
            raise AssertionError("the resolver must not fall back to the catalog on an outage")

    svc = InstrumentService(FailingProvider())

    with pytest.raises(RuntimeError):
        svc.resolve("XAUUSD")


# --- listing ------------------------------------------------------------------------


def test_listing_is_deterministically_ordered_by_symbol(service):
    svc, _ = service

    catalog = svc.list_instruments()

    assert isinstance(catalog, InstrumentCatalog)
    assert [item.symbol for item in catalog.instruments] == ["AAPL", "EURUSD", "NICKEL", "XAUUSD", "XAUUSD.r"]
    assert catalog.total == 5
    assert catalog.truncated is False


def test_listing_is_reproducible_across_calls(service):
    svc, _ = service

    assert svc.list_instruments() == svc.list_instruments()


def test_search_matches_symbol_case_insensitively(service):
    svc, _ = service

    catalog = svc.list_instruments("xau")

    assert [item.symbol for item in catalog.instruments] == ["XAUUSD", "XAUUSD.r"]
    assert catalog.total == 2


def test_search_matches_the_brokers_description(service):
    svc, _ = service

    catalog = svc.list_instruments("gold")

    assert [item.symbol for item in catalog.instruments] == ["XAUUSD", "XAUUSD.r"]


def test_search_with_no_match_is_an_empty_success_not_an_error(service):
    svc, _ = service

    catalog = svc.list_instruments("zzz")

    assert catalog.instruments == ()
    assert catalog.total == 0
    assert catalog.truncated is False


def test_blank_search_means_no_search(service):
    svc, _ = service

    assert svc.list_instruments("   ").total == 5


def test_oversized_search_is_rejected(service):
    svc, _ = service

    with pytest.raises(ValueError):
        svc.list_instruments("x" * 65)


# --- bounds -------------------------------------------------------------------------


def test_catalog_cap_truncates_deterministically_and_says_so():
    provider = FakeInstrumentProvider(
        instruments=tuple(instrument(f"SYMBOL{index:03d}") for index in range(InstrumentService.MAX_INSTRUMENTS + 7))
    )
    svc = InstrumentService(provider)

    catalog = svc.list_instruments()

    assert len(catalog.instruments) == InstrumentService.MAX_INSTRUMENTS
    assert catalog.total == InstrumentService.MAX_INSTRUMENTS + 7
    assert catalog.truncated is True
    # The first page is the head of the deterministic order, not arbitrary rows.
    assert catalog.instruments[0].symbol == "SYMBOL000"
    assert catalog.instruments[-1].symbol == f"SYMBOL{InstrumentService.MAX_INSTRUMENTS - 1:03d}"


def test_catalog_exactly_at_the_cap_is_not_reported_as_truncated():
    provider = FakeInstrumentProvider(
        instruments=tuple(instrument(f"SYM{index:03d}") for index in range(InstrumentService.MAX_INSTRUMENTS))
    )
    svc = InstrumentService(provider)

    catalog = svc.list_instruments()

    assert len(catalog.instruments) == InstrumentService.MAX_INSTRUMENTS
    assert catalog.truncated is False


# --- generic symbol coverage --------------------------------------------------------

@pytest.mark.parametrize(
    "symbol",
    ["AAPL", "LVMH", "BTCUSD", "NICKEL", "COFFEE", "XAUUSD.r", "USOIL", "NAS100"],
)
def test_arbitrary_broker_symbols_resolve_without_special_casing(symbol):
    """The placeholder catalog is the only place these names appear; the service
    resolves them generically, exactly as it would a symbol it has never seen."""
    svc = InstrumentService(FakeInstrumentProvider())

    assert svc.resolve(symbol).symbol == symbol


def test_service_delegates_to_the_provider_contract():
    provider = FakeInstrumentProvider()

    assert isinstance(provider, InstrumentProvider)
    InstrumentService(provider).list_instruments()

    assert provider.list_calls == 1
