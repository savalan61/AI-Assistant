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


# --- broker-suffixed resolution ------------------------------------------------------


def suffixed_catalog(*symbols: str) -> InstrumentService:
    """A broker catalog holding exactly ``symbols`` (the real service runs)."""
    return InstrumentService(
        FakeInstrumentProvider(instruments=tuple(instrument(symbol) for symbol in symbols))
    )


def test_a_unique_suffixed_spelling_of_the_requested_base_resolves():
    assert suffixed_catalog("XAUUSD.r").resolve("XAUUSD").symbol == "XAUUSD.r"


def test_a_unique_suffixed_spelling_resolves_regardless_of_case():
    # The requested name is not the broker's spelling in any sense, but the
    # catalog offers exactly one suffixed variant of it.
    assert suffixed_catalog("XAUUSD.r").resolve("xauusd").symbol == "XAUUSD.r"
    assert suffixed_catalog("eurusd.m").resolve("EURUSD").symbol == "eurusd.m"


def test_an_exact_spelling_always_wins_over_a_suffixed_variant():
    svc = suffixed_catalog("XAUUSD.r", "XAUUSD")

    assert svc.resolve("XAUUSD").symbol == "XAUUSD"


def test_an_exact_spelling_wins_over_a_lowercase_tag_variant():
    # "goldm" is in every sense a decoration of "GOLD" (GOLD + the lowercase
    # tag "m"), but the exact entry still wins first.
    svc = suffixed_catalog("GOLD", "goldm")

    assert svc.resolve("GOLD").symbol == "GOLD"


def test_a_case_insensitive_exact_match_wins_over_a_suffixed_variant():
    svc = suffixed_catalog("XAUUSD.r", "xauusd")

    assert svc.resolve("XAUUSD").symbol == "xauusd"


def test_several_suffixed_variants_are_ambiguous_and_never_guessed():
    svc = suffixed_catalog("XAUUSD.r", "XAUUSD.m")

    with pytest.raises(ValueError) as exc_info:
        svc.resolve("XAUUSD")

    assert "ambiguous" in str(exc_info.value)


def test_several_suffixed_variants_are_ambiguous_in_any_catalog_order():
    # Determinism: the outcome depends on the catalog's contents, never on the
    # order the terminal happened to return them in.
    for catalog in (("XAUUSD.m", "XAUUSD.r"), ("XAUUSD.cash", "XAUUSD.r", "XAUUSD.m")):
        with pytest.raises(ValueError, match="ambiguous"):
            suffixed_catalog(*catalog).resolve("XAUUSD")


def test_no_suffixed_variant_stays_unresolved():
    with pytest.raises(ValueError) as exc_info:
        suffixed_catalog("EURUSD", "USOIL").resolve("XAUUSD")

    assert "does not offer" in str(exc_info.value)


def test_a_variant_of_a_different_base_is_not_matched():
    # The requested name must be the symbol's BASE, not merely a part of it:
    # XAUUSDT.r is a variant of XAUUSDT, not of XAUUSD, and GOLDMINI is a base
    # symbol of its own — neither is a decoration of the requested name.
    for catalog_symbol in ("XAUUSDT.r", "GOLDMINI"):
        svc = suffixed_catalog(catalog_symbol)

        with pytest.raises(ValueError):
            svc.resolve("XAUUSD" if catalog_symbol == "XAUUSDT.r" else "GO")


@pytest.mark.parametrize(
    "symbol",
    [
        "XAUUSDX",  # uppercase undelimited tail: a different base symbol's shape
        "XAUUSD1",  # digit-only undelimited tail: marks nothing
        "XAUUSDXAUUSD",  # long uppercase tail
        "XAUUSD.verylongsuffix",  # beyond the bounded suffix length
        "XAUUSD.r.x",  # compound tail
        "XAUUSDT.r",  # a variant of a DIFFERENT base
    ],
)
def test_a_tail_that_is_not_a_broker_decoration_is_never_matched(symbol: str):
    with pytest.raises(ValueError):
        suffixed_catalog(symbol).resolve("XAUUSD")


@pytest.mark.parametrize(
    "symbol",
    [
        "XAUUSD.r",  # separator + short token
        "XAUUSD.p",  # another single-letter broker variant
        "XAUUSD.",  # trailing separator with no token (UKOIL. / US100. style)
        "XAUUSD_m",
        "XAUUSD-m",
        "XAUUSD#1",
        "XAUUSD.cash",
        "XAUUSD.ECN",  # case of the tail is the broker's business
        "XAUUSDm",  # delimiter-free lowercase tag
        "XAUUSDpro",  # longer lowercase tag
    ],
)
def test_the_documented_broker_decoration_forms_are_recognised(symbol: str):
    assert suffixed_catalog(symbol).resolve("XAUUSD").symbol == symbol


def test_a_delimiter_free_tag_is_lowercase_only():
    # Lowercase letters are the tag convention; an uppercase tail is how a
    # different base symbol would be spelled, and digits mark nothing.
    with pytest.raises(ValueError):
        suffixed_catalog("XAUUSDX").resolve("XAUUSD")
    with pytest.raises(ValueError):
        suffixed_catalog("XAUUSD1").resolve("XAUUSD")


def test_a_partial_name_never_resolves_even_when_the_tail_shape_fits():
    # "US" is a prefix of US500.cash and its remainder ("500.cash") contains a
    # separator, but the partial-name bound rejects digits in an undelimited
    # tail, so the longer instrument is never read as a decoration of "US".
    svc = suffixed_catalog("US500.cash", "AAPL")

    with pytest.raises(ValueError):
        svc.resolve("US")
    with pytest.raises(ValueError):
        svc.resolve("US5")  # even a longer slice of the same symbol


def test_a_lowercase_tag_is_never_read_as_a_partial_name_match():
    # A partial name of the right shape ("goldm" for "goldm" + nothing) is an
    # exact entry, but "go" + "ldm" is a lowercase-alphabetic tail — the tag
    # form must only fire when the tail is a real decoration, not a slice of a
    # longer instrument name. The prefix bound does that: "GO" + "LDM" is
    # uppercase, so it never matches.
    svc = suffixed_catalog("GOLDMINI")

    with pytest.raises(ValueError):
        svc.resolve("GO")


def test_a_suffix_match_is_never_a_substring_or_prefix_match():
    # None of these is an exact catalog entry, and each is a substring/prefix of
    # one: a partial name must never resolve to the longer instrument.
    svc = suffixed_catalog("USOIL.cash", "XAUUSD.r", "GOLD", "AAPL")

    for requested in ("US", "USO", "USD", "XA", "AU", "GOL", "AA"):
        with pytest.raises(ValueError):
            svc.resolve(requested)


def test_an_unrelated_symbol_in_the_catalog_does_not_interfere():
    svc = suffixed_catalog("XAUUSD.r", "EURUSD.m", "USOIL.cash", "AAPL")

    assert svc.resolve("XAUUSD").symbol == "XAUUSD.r"
    assert svc.resolve("EURUSD").symbol == "EURUSD.m"
    assert svc.resolve("USOIL").symbol == "USOIL.cash"
    assert svc.resolve("AAPL").symbol == "AAPL"  # exact


def test_suffixed_resolution_is_deterministic():
    svc = suffixed_catalog("XAUUSD.r", "AAPL")

    assert svc.resolve("XAUUSD") == svc.resolve("XAUUSD")
    assert svc.resolve("xauusd").symbol == svc.resolve("XAUUSD").symbol


def test_a_suffix_ambiguity_does_not_fall_through_to_a_looser_rule():
    # Two variants AND a third, unrelated symbol: still ambiguous, never the
    # unrelated one.
    svc = suffixed_catalog("XAUUSD.r", "XAUUSD.m", "COFFEE")

    with pytest.raises(ValueError, match="ambiguous"):
        svc.resolve("XAUUSD")


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
