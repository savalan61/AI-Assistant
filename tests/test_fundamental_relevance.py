"""Tests for deterministic news relevance (fundamental intelligence).

Pins the properties the fundamental layer depends on: relevance is a DISCRETE
category from the existing calendar vocabulary, XAUUSD is handled through the
same currency-leg rules as the calendar classifier, declared source tags win over
inferred references, an item with no identifiable reference stays explicitly
NOT_OBVIOUSLY_RELEVANT (never guessed), and no reason ever contains a direction,
an expectation, a probability or a recommendation.

Offline and deterministic: no MT5, no database, no network, no LLM.
"""
from datetime import UTC, datetime

import pytest

from app.providers.news import NewsItem
from app.services.economic_intelligence import (
    RelevanceLevel,
    is_metal_instrument,
    symbol_currencies,
)
from app.services.fundamental_intelligence import (
    classify_news_relevance,
    detected_currencies,
    news_currencies,
    news_instruments,
    strongest_level,
)

PUBLISHED = datetime(2026, 9, 16, 7, 5, tzinfo=UTC)

# Wording that would turn a factual relatedness statement into advice or a
# prediction. No reason this layer produces may contain any of it.
_FORBIDDEN_WORDS = (
    "buy",
    "sell",
    "long",
    "short",
    "expect",
    "forecast",
    "predict",
    "target",
    "probability",
    "recommend",
    "should",
    "bullish",
    "bearish",
)


def item(
    *,
    title: str = "US CPI release due later today (placeholder)",
    summary: str = "Placeholder excerpt.",
    instruments: tuple[str, ...] = (),
    currencies: tuple[str, ...] = (),
) -> NewsItem:
    return NewsItem(
        item_id="news-1",
        published_at=PUBLISHED,
        publisher="Example Newswire (placeholder)",
        title=title,
        summary=summary,
        url=None,
        instruments=instruments,
        currencies=currencies,
        categories=(),
    )


# --- currency reference detection --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Federal Reserve minutes due later today", ("USD",)),
        ("FOMC statement scheduled", ("USD",)),
        ("US inflation report due", ("USD",)),
        ("Consumer price index release scheduled", ("USD",)),
        ("Gold ETF flows reported steady", ("XAU",)),
        ("Bullion dealers report steady demand", ("XAU",)),
        ("ECB officials speak on the outlook", ("EUR",)),
        ("Euro area industrial production due", ("EUR",)),
        ("Bank of England comments scheduled", ("GBP",)),
        ("Bank of Japan policy summary due", ("JPY",)),
        ("Market wrap: energy and shipping costs in focus", ()),
    ],
)
def test_keyword_map_detects_currency_references(text: str, expected: tuple[str, ...]) -> None:
    assert detected_currencies(text) == expected


def test_detection_is_case_insensitive() -> None:
    assert detected_currencies("FEDERAL RESERVE MINUTES DUE") == ("USD",)


def test_detection_is_whole_word_only() -> None:
    # "inflationary" is a different word from "inflation": no reference is
    # inferred from a partial match.
    assert detected_currencies("inflationary pressures mentioned") == ()


def test_detection_is_deterministic() -> None:
    text = "Federal Reserve and ECB and gold in focus"

    assert detected_currencies(text) == detected_currencies(text)
    assert detected_currencies(text) == ("EUR", "USD", "XAU")


def test_item_currencies_combine_declared_and_detected_references() -> None:
    tagged = item(title="Quarterly statement scheduled", currencies=("eur",))

    # Declared tags (normalized) plus anything the title references.
    assert news_currencies(tagged) == ("EUR",)


def test_item_currencies_include_a_detected_reference_alongside_declared_ones() -> None:
    mixed = item(title="Federal Reserve minutes due", currencies=("GBP",))

    assert news_currencies(mixed) == ("GBP", "USD")


def test_instrument_tags_are_normalized_and_deduplicated() -> None:
    tagged = item(instruments=("xauusd", " XAUUSD", "eurusd", ""))

    assert news_instruments(tagged) == ("EURUSD", "XAUUSD")


# --- classification: declared tags win ---------------------------------------------------


def test_an_item_naming_the_instrument_directly_is_potentially_relevant() -> None:
    relevance = classify_news_relevance(item(instruments=("XAUUSD",)), "XAUUSD")

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "named directly" in relevance.reason


def test_relevance_never_exceeds_potentially_relevant() -> None:
    # The strongest level this layer may assert: no instrument catalog exists to
    # evidence a direct instrument-level link yet.
    relevance = classify_news_relevance(item(instruments=("XAUUSD",)), "XAUUSD")

    assert relevance.relevance is not RelevanceLevel.RELEVANT


def test_declared_tags_win_over_a_different_inferred_reference() -> None:
    # The source declares USD; the title happens to mention the ECB. The
    # declared tag is the source's own statement and decides the classification.
    mixed = item(title="ECB officials comment on broad conditions", currencies=("USD",))

    relevance = classify_news_relevance(mixed, "XAUUSD")

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "USD" in relevance.reason
    assert "quote currency" in relevance.reason


# --- classification: XAUUSD (the first important use case) -------------------------------


def test_xauusd_is_tokenized_as_a_metal_pair() -> None:
    assert symbol_currencies("XAUUSD") == ("XAU", "USD")
    assert is_metal_instrument("XAUUSD") is True
    assert is_metal_instrument("EURUSD") is False


def test_a_usd_item_is_relevant_to_xauusd_through_its_quote_currency() -> None:
    relevance = classify_news_relevance(item(currencies=("USD",)), "XAUUSD")

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "USD is the quote currency of XAUUSD" in relevance.reason
    assert "USD-denominated metal" in relevance.reason


def test_a_xau_item_is_relevant_to_xauusd_through_its_base_metal() -> None:
    relevance = classify_news_relevance(item(currencies=("XAU",)), "XAUUSD")

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "base (traded) metal" in relevance.reason


def test_an_untagged_fed_item_is_relevant_to_xauusd_through_the_keyword_map() -> None:
    untagged = item(title="Federal Reserve minutes due later today (placeholder)")

    relevance = classify_news_relevance(untagged, "XAUUSD")

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "USD" in relevance.reason


def test_a_gold_item_is_relevant_to_xauusd_even_without_declared_currencies() -> None:
    gold = item(title="Gold ETF flows reported steady ahead of US data (placeholder)")

    assert classify_news_relevance(gold, "XAUUSD").relevance is RelevanceLevel.POTENTIALLY_RELEVANT


def test_an_ecb_item_is_not_obviously_relevant_to_xauusd() -> None:
    ecb = item(title="ECB officials speak on the euro-area outlook (placeholder)")

    relevance = classify_news_relevance(ecb, "XAUUSD")

    assert relevance.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "EUR" in relevance.reason


def test_eurusd_base_and_quote_legs_are_both_recognized() -> None:
    eur_item = classify_news_relevance(item(currencies=("EUR",)), "EURUSD")
    usd_item = classify_news_relevance(item(currencies=("USD",)), "EURUSD")

    assert eur_item.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "EUR is the base currency of EURUSD" in eur_item.reason
    assert usd_item.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "USD is the quote currency of EURUSD" in usd_item.reason


# --- classification: explicit "cannot be established" ------------------------------------


def test_an_item_with_no_identifiable_reference_stays_not_obviously_relevant() -> None:
    unrelated = item(title="Market wrap: energy and shipping costs in focus (placeholder)")

    relevance = classify_news_relevance(unrelated, "XAUUSD")

    assert relevance.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "cannot be established" in relevance.reason


def test_a_symbol_without_a_currency_leg_is_reported_as_such() -> None:
    relevance = classify_news_relevance(item(currencies=("USD",)), "US30")

    assert relevance.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "No currency leg could be identified" in relevance.reason


def test_a_foreign_currency_item_is_not_obviously_relevant_to_a_usd_pair() -> None:
    relevance = classify_news_relevance(item(currencies=("JPY",)), "EURUSD")

    assert relevance.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "JPY" in relevance.reason


# --- classification: no advice, no prediction -------------------------------------------


def test_no_reason_ever_contains_directional_or_predictive_wording() -> None:
    pairs = (
        (item(instruments=("XAUUSD",)), "XAUUSD"),
        (item(currencies=("USD",)), "XAUUSD"),
        (item(currencies=("XAU",)), "XAUUSD"),
        (item(title="Federal Reserve minutes due"), "XAUUSD"),
        (item(title="ECB officials speak on the euro-area outlook"), "XAUUSD"),
        (item(title="Market wrap: energy and shipping costs in focus"), "XAUUSD"),
        (item(currencies=("USD",)), "US30"),
    )

    for news_item, symbol in pairs:
        reason = classify_news_relevance(news_item, symbol).reason.lower()
        assert reason
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in reason, (symbol, forbidden, reason)


def test_classification_is_deterministic() -> None:
    news_item = item(currencies=("USD",))

    first = classify_news_relevance(news_item, "XAUUSD")
    second = classify_news_relevance(news_item, "XAUUSD")

    assert first == second


# --- strongest level ---------------------------------------------------------------------


def test_strongest_level_picks_the_most_relevant_category() -> None:
    levels = (
        RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
        RelevanceLevel.POTENTIALLY_RELEVANT,
        RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
    )

    assert strongest_level(levels) is RelevanceLevel.POTENTIALLY_RELEVANT


def test_strongest_level_of_nothing_is_not_obviously_relevant() -> None:
    assert strongest_level(()) is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT


def test_strongest_level_of_only_unrelated_items_stays_conservative() -> None:
    levels = (RelevanceLevel.NOT_OBVIOUSLY_RELEVANT, RelevanceLevel.NOT_OBVIOUSLY_RELEVANT)

    assert strongest_level(levels) is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
