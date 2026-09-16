"""Deterministic relevance classification for news items (READ-ONLY).

This extends the EXISTING calendar relevance approach rather than introducing a
parallel classification mechanism: it reuses ``RelevanceLevel``, the symbol
tokenizer (``symbol_currencies``), the metal rule (``is_metal_instrument``) and
the level ranking from app/services/economic_intelligence/relevance.py, and it
keeps the same deliberate conservatism:

* the strongest level this layer may assert is POTENTIALLY_RELEVANT. A symbol
  string cannot evidence a direct instrument-level link, so RELEVANT stays
  reserved for a future instrument catalog;
* no currency or instrument reference at all is NOT_OBVIOUSLY_RELEVANT with an
  explicit factual reason — never a guess and never a directional claim;
* relevance is a discrete category. There is no score, no probability, no
  direction and no recommendation anywhere in this module.

A news item may declare its own currency/instrument tags (the shape a real
vendor usually publishes). When it does not, this module infers currency
references from the title with a small, documented, whole-word keyword map, so an
untagged item about the Federal Reserve or gold is still considered instead of
silently ignored. Declared tags always win, and the inference is deterministic:
the same item text always produces the same detected currencies.
"""
import re
from typing import NamedTuple

from app.providers.news import NewsItem
from app.services.economic_intelligence import (
    RelevanceLevel,
    is_metal_instrument,
    relevance_rank,
    symbol_currencies,
)

# Documented keyword -> currency map used only when an item declares no
# currencies of its own. Whole-word matches only, and deliberately small: a
# keyword that is not clearly tied to one currency is not in this table.
_KEYWORD_CURRENCIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("federal reserve", ("USD",)),
    ("fed", ("USD",)),
    ("fomc", ("USD",)),
    ("inflation", ("USD",)),
    ("consumer price index", ("USD",)),
    ("nonfarm", ("USD",)),
    ("payrolls", ("USD",)),
    ("treasury", ("USD",)),
    ("dollar", ("USD",)),
    ("unemployment", ("USD",)),
    ("gold", ("XAU",)),
    ("bullion", ("XAU",)),
    ("precious metal", ("XAU",)),
    ("ecb", ("EUR",)),
    ("euro area", ("EUR",)),
    ("eurozone", ("EUR",)),
    ("euro", ("EUR",)),
    ("bank of england", ("GBP",)),
    ("pound", ("GBP",)),
    ("bank of japan", ("JPY",)),
    ("yen", ("JPY",)),
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def detected_currencies(text: str) -> tuple[str, ...]:
    """Currencies referenced by ``text`` according to the keyword map.

    Deterministic and side-effect free: whole-word matches for single keywords,
    word-bounded phrase matches for multi-word ones, and a sorted, deduplicated
    result so callers never depend on dict or table order.
    """
    lowered = text.lower()
    words = set(_WORD_RE.findall(lowered))
    detected: set[str] = set()
    for keyword, currencies in _KEYWORD_CURRENCIES:
        if " " in keyword:
            hit = re.search(rf"\b{re.escape(keyword)}\b", lowered) is not None
        else:
            hit = keyword in words
        if hit:
            detected.update(currencies)
    return tuple(sorted(detected))


def news_currencies(item: NewsItem) -> tuple[str, ...]:
    """Currency references of an item: declared tags plus detected ones."""
    declared = {currency.strip().upper() for currency in item.currencies if currency.strip()}
    return tuple(sorted(declared | set(detected_currencies(item.title))))


def news_instruments(item: NewsItem) -> tuple[str, ...]:
    """Declared instrument tags of an item (upper-cased, sorted, deduplicated)."""
    return tuple(sorted({instrument.strip().upper() for instrument in item.instruments if instrument.strip()}))


class NewsRelevance(NamedTuple):
    """Factual relatedness of one news item to one instrument."""

    item_id: str
    relevance: RelevanceLevel
    reason: str


def classify_news_relevance(item: NewsItem, symbol: str) -> NewsRelevance:
    """Classify one news item against one instrument (deterministic, conservative)."""
    upper_symbol = symbol.strip().upper()

    # A source that names the instrument directly is the strongest signal this
    # layer accepts (still POTENTIALLY_RELEVANT, never RELEVANT).
    if upper_symbol in news_instruments(item):
        return NewsRelevance(
            item_id=item.item_id,
            relevance=RelevanceLevel.POTENTIALLY_RELEVANT,
            reason=(
                f"{upper_symbol} is named directly by the item's instrument tag; the item is tied "
                "to that instrument's own exposure."
            ),
        )

    currencies = symbol_currencies(upper_symbol)
    if not currencies:
        return NewsRelevance(
            item_id=item.item_id,
            relevance=RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
            reason=(
                f"No currency leg could be identified in {upper_symbol}, so relevance cannot be "
                "established from the symbol alone."
            ),
        )

    item_currencies = news_currencies(item)
    if not item_currencies:
        return NewsRelevance(
            item_id=item.item_id,
            relevance=RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
            reason=(
                "No currency or instrument reference could be identified in the item, so relevance "
                f"to {upper_symbol} cannot be established."
            ),
        )

    base = currencies[0]
    metal = is_metal_instrument(upper_symbol)
    for leg in currencies:
        if leg not in item_currencies:
            continue
        if leg == base and metal:
            reason = (
                f"{leg} is the base (traded) metal of {upper_symbol}; the item is tied to that "
                "instrument's own exposure."
            )
        elif leg == base:
            reason = (
                f"{leg} is the base currency of {upper_symbol}; the item is tied to the "
                "instrument's own currency exposure."
            )
        elif metal and leg == "USD":
            reason = (
                f"{leg} is the quote currency of {upper_symbol}, a USD-denominated metal "
                "instrument; the item is tied to its pricing currency."
            )
        else:
            reason = (
                f"{leg} is the quote currency of {upper_symbol}; the item is tied to the "
                "instrument's quote-currency exposure."
            )
        return NewsRelevance(
            item_id=item.item_id,
            relevance=RelevanceLevel.POTENTIALLY_RELEVANT,
            reason=reason,
        )

    return NewsRelevance(
        item_id=item.item_id,
        relevance=RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
        reason=(
            f"{', '.join(item_currencies)} is not a currency leg of {upper_symbol}; no relevance "
            "could be established from the item's references."
        ),
    )


def strongest_level(levels: tuple[RelevanceLevel, ...]) -> RelevanceLevel:
    """Strongest level in ``levels`` (nothing to classify: not obviously relevant)."""
    if not levels:
        return RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    return max(levels, key=relevance_rank)
