from app.providers.market_data import Candle, MarketDataProvider
from app.services.instruments import InstrumentService


# Depends on the provider abstraction, not on MT5 directly.
class MarketDataService:
    """Read-only market data for a requested instrument.

    Since the instrument catalog exists, a requested symbol is resolved FIRST
    (``app.services.instruments.InstrumentService``) and the candle read uses
    the broker's own canonical spelling — the SAME resolution semantics
    GET /instruments and financial research use, so ``xauusd`` reads XAUUSD and
    a broker that lists only ``XAUUSD.r`` is read for ``XAUUSD.r`` instead of
    failing. No resolution logic is duplicated here: the service asks the
    instrument boundary and uses its answer.

    ``instrument_service`` is optional: ``None`` means the caller supplied a
    spelling it has already verified (or the deployment has no catalog), and the
    requested symbol is passed through unchanged — the behaviour before
    resolution existed.

    One catalog lookup per request: resolution performs a single symbol lookup
    (plus at most one catalog scan when the exact spelling is unknown), and the
    service holds no second provider.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        instrument_service: InstrumentService | None = None,
    ):
        self._provider = provider
        self._instruments = instrument_service

    def get_market_data(self, symbol: str) -> Candle:
        """Return the latest candle for ``symbol``, resolved to the broker's spelling.

        A symbol the broker does not offer raises ValueError (the existing
        client-error/404 contract) before any candle is read; an MT5/catalog
        availability failure propagates as RuntimeError (the existing 503).
        """
        resolved_symbol = symbol
        if self._instruments is not None:
            # The broker's own spelling travels to the provider: a broker with a
            # suffixed catalog must be asked for the symbol it actually lists.
            resolved_symbol = self._instruments.resolve(symbol).symbol
        return self._provider.get_market_data(resolved_symbol)
