import abc
from enum import StrEnum
from typing import NamedTuple


# Broker-side availability of an instrument, normalised from MT5's numeric
# trade mode. StrEnum so the JSON value is exactly the documented word and a new
# mode can only be introduced deliberately (mirrors PositionType / UserRole).
class TradeMode(StrEnum):
    DISABLED = "DISABLED"
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    CLOSE_ONLY = "CLOSE_ONLY"
    FULL = "FULL"


# Typed, read-only instrument-identity snapshot shared by all instrument
# providers. Explicit NamedTuple like Candle/Position/NewsItem so the raw MT5
# object never travels past the provider boundary.
#
# Scope rule: identity and metadata only — what the instrument is called, how
# the broker classifies it, and whether it can be traded. There is deliberately
# no price, no position, no relevance and no fundamental field: discovery
# answers "what does this broker offer", and nothing else.
#
# ``symbol`` is the broker's own spelling, exactly as the broker reports it. It
# is never re-cased or rewritten: broker symbol names are case-sensitive and
# carry broker-specific suffixes (``XAUUSD.r``, ``US500.cash``), and a
# normalised spelling that the broker does not offer cannot be resolved at all.
#
# Every other field is optional because brokers genuinely omit them.
# ``asset_class`` is the broker's own top-level symbol-group path segment (the
# broker's taxonomy, never ours), ``base_currency``/``quote_currency`` are MT5's
# own currency_base/currency_profit values passed through verbatim (for
# exchange-traded symbols MT5 uses them for the security ticker and the
# settlement currency, so they are NOT parsed into an FX pair), ``digits`` is
# the broker's price precision, and ``trade_mode`` is the availability above.
# None means "the broker did not provide it" — it never means "unknown because
# it was guessed".
class Instrument(NamedTuple):
    symbol: str
    name: str | None
    asset_class: str | None
    base_currency: str | None
    quote_currency: str | None
    digits: int | None
    trade_mode: TradeMode | None


# Abstraction boundary: services depend on this, never on MT5 directly (mirrors
# MarketDataProvider / PositionProvider). Read-only by design: the contract has
# no order, trade, subscription or state-changing operation in it.
class InstrumentProvider(abc.ABC):
    @abc.abstractmethod
    def get_instrument(self, symbol: str) -> Instrument:
        """Return one instrument, looked up by the broker's own spelling.

        Raises ValueError when the broker does not offer the symbol (a client
        error) and RuntimeError when the broker/terminal cannot answer at all
        (an availability error); callers map the two differently.

        The MT5 implementation raises ``MT5ClientError`` (a ``ValueError``
        subclass, see ``app.core.mt5_session``) for the client case, so the
        session boundary can tell a normal "no such symbol" answer from a
        transport failure. Callers that only care about the client/availability
        split keep catching ``ValueError``/``RuntimeError`` unchanged.
        """
        ...

    @abc.abstractmethod
    def list_instruments(self) -> tuple[Instrument, ...]:
        """Return every instrument the broker offers, in vendor order.

        Deterministic presentation (resolution fallback, searching, ordering,
        capping) belongs to the service, so providers stay thin adapters.
        """
        ...
