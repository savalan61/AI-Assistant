"""Deterministic, conservative relevance classification for economic events.

READ-ONLY financial intelligence. This module decides whether an economic event
is *related* to a position's currency exposure. It never predicts prices, never
claims an event will move a market, and never produces trading actions
(no BUY/SELL/OPEN/CLOSE/MODIFY).

How relevance is decided (pure symbol-string analysis, no market data call):

1. The instrument's currency tokens are extracted from the MT5-style symbol
   (e.g. "XAUUSD" -> ("XAU", "USD")).
2. POTENTIALLY_RELEVANT when the event currency is one of those legs: the event
   is tied to a currency the instrument is exposed to. The wording stays
   deliberately cautious — a shared currency is a relatedness signal, not a
   claim that the event will move the instrument. Base-currency, quote-currency
   and USD-denominated-metal cases get their own factual reason.
3. POTENTIALLY_RELEVANT also covers HIGH-impact USD events against instruments
   that have a currency leg but no USD leg: USD is the dominant reserve and
   funding currency, so major US data can affect broader FX conditions. Lower
   impact USD events are deliberately not escalated.
4. NOT_OBVIOUSLY_RELEVANT otherwise (including symbols with no identifiable
   currency leg, such as index products).

RELEVANT is intentionally never asserted by this mechanism: a symbol string
alone cannot evidence a direct instrument-level link. That level exists in the
contract for a future instrument catalog that names the exact traded asset; the
current classifier caps its assertions at POTENTIALLY_RELEVANT rather than
overstating certainty.
"""
from enum import StrEnum
from typing import NamedTuple

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.position import Position, PositionType


class RelevanceLevel(StrEnum):
    """How strongly an economic event relates to a position's exposure."""

    RELEVANT = "RELEVANT"
    POTENTIALLY_RELEVANT = "POTENTIALLY_RELEVANT"
    NOT_OBVIOUSLY_RELEVANT = "NOT_OBVIOUSLY_RELEVANT"


# Explicit ordering so the strongest classification across positions is
# deterministic (StrEnum members themselves are not orderable).
_LEVEL_RANK: dict[RelevanceLevel, int] = {
    RelevanceLevel.NOT_OBVIOUSLY_RELEVANT: 0,
    RelevanceLevel.POTENTIALLY_RELEVANT: 1,
    RelevanceLevel.RELEVANT: 2,
}


def relevance_rank(level: RelevanceLevel) -> int:
    """Rank of a relevance level (higher = stronger linkage)."""
    return _LEVEL_RANK[level]


# Known ISO currency codes and traded metals/products used to tokenize
# MT5-style symbols. Index/equity product names (US30, GER40, ...) contain none
# of these tokens and are therefore conservatively treated as having no
# identifiable currency leg.
_CURRENCY_TOKENS: tuple[str, ...] = (
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "CAD",
    "AUD",
    "NZD",
    "CNY",
    "CNH",
    "SEK",
    "NOK",
    "DKK",
    "SGD",
    "HKD",
    "MXN",
    "ZAR",
    "TRY",
    "PLN",
    "CZK",
    "HUF",
    "XAU",
    "XAG",
    "XPT",
    "XPD",
    "BTC",
    "ETH",
)

# Precious metals / USD-denominated commodity products.
_METAL_TOKENS = frozenset({"XAU", "XAG", "XPT", "XPD"})


class PositionRelevance(NamedTuple):
    """Factual relatedness of one economic event to one open position."""

    ticket: int
    symbol: str
    type: PositionType
    relevance: RelevanceLevel
    reason: str


def symbol_currencies(symbol: str) -> tuple[str, ...]:
    """Currency/metal tokens found in an MT5-style symbol, in symbol order.

    Deterministic and side-effect free; only the known token set is recognized,
    so an unknown or non-currency product simply yields no tokens.
    """
    upper = symbol.upper()
    located = sorted(
        (upper.find(token), token) for token in _CURRENCY_TOKENS if upper.find(token) != -1
    )
    return tuple(token for _, token in located)


def is_metal_instrument(symbol: str) -> bool:
    """True when the instrument's base token is a traded metal (XAU/XAG/XPT/XPD).

    Shared by the calendar classifier and the fundamental/news relevance layer so
    both apply the same "USD-denominated metal" rule instead of two divergent
    ones.
    """
    currencies = symbol_currencies(symbol)
    return bool(currencies) and currencies[0] in _METAL_TOKENS


def _result(position: Position, level: RelevanceLevel, reason: str) -> PositionRelevance:
    return PositionRelevance(
        ticket=position.ticket,
        symbol=position.symbol,
        type=position.type,
        relevance=level,
        reason=reason,
    )


def classify_relevance(event: EconomicEvent, position: Position) -> PositionRelevance:
    """Classify one event against one position (deterministic, conservative)."""
    currencies = symbol_currencies(position.symbol)
    event_currency = event.currency.upper()

    if not currencies:
        return _result(
            position,
            RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
            f"No currency leg could be identified in {position.symbol}, so relevance cannot be "
            "established from the symbol alone.",
        )

    base = currencies[0]
    if event_currency in currencies:
        if event_currency == base and base in _METAL_TOKENS:
            reason = (
                f"{event_currency} is the base (traded) metal of {position.symbol}; the event is "
                "tied to that instrument's own exposure."
            )
        elif event_currency == base:
            reason = (
                f"{event_currency} is the base currency of {position.symbol}; the event is tied to "
                "the instrument's own currency exposure."
            )
        elif base in _METAL_TOKENS and event_currency == "USD":
            reason = (
                f"{event_currency} is the quote currency of {position.symbol}, a USD-denominated "
                "metal instrument; the event is tied to its pricing currency."
            )
        else:
            reason = (
                f"{event_currency} is the quote currency of {position.symbol}; the event is tied to "
                "the instrument's quote-currency exposure."
            )
        return _result(position, RelevanceLevel.POTENTIALLY_RELEVANT, reason)

    if event_currency == "USD" and event.impact is EventImpact.HIGH:
        reason = (
            "USD is the dominant reserve and funding currency; a high-impact US event can affect "
            f"broader FX conditions even though USD is not a leg of {position.symbol} "
            "(potential linkage only)."
        )
        return _result(position, RelevanceLevel.POTENTIALLY_RELEVANT, reason)

    reason = (
        f"{event_currency} is not a currency leg of {position.symbol}; no relevance could be "
        "established from the symbol alone."
    )
    return _result(position, RelevanceLevel.NOT_OBVIOUSLY_RELEVANT, reason)


def overall_relevance(relevances: tuple[PositionRelevance, ...]) -> RelevanceLevel:
    """Strongest classification across positions (no positions: not obvious)."""
    if not relevances:
        return RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    return max((relevance.relevance for relevance in relevances), key=relevance_rank)
