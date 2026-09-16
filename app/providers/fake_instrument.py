from app.providers.instrument import Instrument, InstrumentProvider, TradeMode

# Deterministic placeholder catalog returned for every call; fixed values keep
# tests repeatable (the FakeMarketDataProvider / FakeNewsProvider pattern,
# applied to instrument discovery). It is intentionally varied — forex, metals,
# energy, index, shares, crypto and a soft commodity, a broker suffix spelling,
# a mixed-case-in-name record, a restricted trade mode and one record whose
# optional metadata the "broker" does not provide at all — so generic handling
# is exercised without any symbol being special-cased in production logic.
#
# Terminal order (not alphabetical) on purpose: the catalog reaches the service
# in vendor order, so the service's deterministic ordering is what makes a
# response reproducible.
_FAKE_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        symbol="EURUSD",
        name="Euro vs US Dollar",
        asset_class="Forex",
        base_currency="EUR",
        quote_currency="USD",
        digits=5,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="GBPJPY",
        name="Great Britain Pound vs Japanese Yen",
        asset_class="Forex",
        base_currency="GBP",
        quote_currency="JPY",
        digits=3,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="XAUUSD",
        name="Gold vs US Dollar",
        asset_class="Metals",
        base_currency="XAU",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="XAUUSD.r",
        name="Gold vs US Dollar (retail)",
        asset_class="Metals",
        base_currency="XAU",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="USOIL",
        name="US Crude Oil",
        asset_class="Energies",
        base_currency="USOIL",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="NAS100",
        name="US 100 Index",
        asset_class="Indices",
        base_currency="NAS100",
        quote_currency="USD",
        digits=1,
        trade_mode=TradeMode.CLOSE_ONLY,
    ),
    Instrument(
        symbol="AAPL",
        name="Apple Inc.",
        asset_class="Shares",
        base_currency="AAPL",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.LONG_ONLY,
    ),
    Instrument(
        symbol="LVMH",
        name="LVMH Moët Hennessy Louis Vuitton",
        asset_class="Shares",
        base_currency="LVMH",
        quote_currency="EUR",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
    Instrument(
        symbol="BTCUSD",
        name="Bitcoin vs US Dollar",
        asset_class="Crypto",
        base_currency="BTC",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
    # A catalog entry with no optional metadata at all: "the broker did not
    # provide it" must stay a first-class outcome, not an error.
    Instrument(
        symbol="NICKEL",
        name=None,
        asset_class=None,
        base_currency=None,
        quote_currency=None,
        digits=None,
        trade_mode=TradeMode.DISABLED,
    ),
    Instrument(
        symbol="COFFEE",
        name="Coffee",
        asset_class="Commodities",
        base_currency="COFFEE",
        quote_currency="USD",
        digits=2,
        trade_mode=TradeMode.FULL,
    ),
)


# Minimal in-memory provider for tests and development; implements the provider
# contract without MT5 and without any network access.
class FakeInstrumentProvider(InstrumentProvider):
    def __init__(self, instruments: tuple[Instrument, ...] = _FAKE_INSTRUMENTS) -> None:
        self.instruments = instruments
        self.get_calls: list[str] = []
        self.list_calls = 0

    def get_instrument(self, symbol: str) -> Instrument:
        self.get_calls.append(symbol)
        for instrument in self.instruments:
            if instrument.symbol == symbol:
                return instrument
        raise ValueError(f"MT5 does not offer instrument {symbol}")

    def list_instruments(self) -> tuple[Instrument, ...]:
        self.list_calls += 1
        return self.instruments
