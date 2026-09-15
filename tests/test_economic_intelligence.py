"""Tests for the deterministic relevance layer and the intelligence service.

Require none of: real MT5, PostgreSQL, network, credentials, LLM, or a real
calendar source. The relevance classifier is pure, and the service is exercised
with the deterministic fake calendar provider and FakePositionProvider through
the existing PositionService. Fully synchronous: no async plugin needed.
"""
from datetime import UTC, datetime

import pytest

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.position import Position, PositionType
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import (
    EconomicIntelligenceService,
    RelevanceLevel,
    classify_relevance,
    overall_relevance,
    symbol_currencies,
)
from app.services.economic_intelligence.relevance import PositionRelevance
from app.services.positions import PositionService

DAY_FROM = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
DAY_TO = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)

XAUUSD = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=0.10,
    open_price=3642.50,
    current_price=3648.20,
    profit=57.00,
)
EURUSD = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=1.00,
    open_price=1.0850,
    current_price=1.0820,
    profit=-30.00,
)


def make_event(
    currency: str = "USD",
    impact: EventImpact = EventImpact.HIGH,
    event_id: str = "e1",
    timestamp: datetime = datetime(2026, 9, 15, 12, 30, tzinfo=UTC),
) -> EconomicEvent:
    return EconomicEvent(
        event_id=event_id,
        timestamp=timestamp,
        currency=currency,
        title=f"Test {currency} event",
        impact=impact,
        forecast="3.1%",
        previous="3.2%",
        actual=None,
    )


def make_position(symbol: str, ticket: int = 1, position_type: PositionType = PositionType.BUY) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        type=position_type,
        volume=0.10,
        open_price=1.0,
        current_price=1.0,
        profit=0.0,
    )


def make_service(positions: tuple[Position, ...]) -> EconomicIntelligenceService:
    calendar = EconomicCalendarService(FakeEconomicCalendarProvider())
    return EconomicIntelligenceService(calendar, PositionService(FakePositionProvider(positions=positions)))


# --- symbol tokenization ---------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("XAUUSD", ("XAU", "USD")),
        ("EURUSD", ("EUR", "USD")),
        ("GBPJPY", ("GBP", "JPY")),
        ("AUDNZD", ("AUD", "NZD")),
        ("eurusd", ("EUR", "USD")),
        ("US30", ()),
        ("GER40", ()),
    ],
)
def test_symbol_currencies_extracts_known_tokens_only(symbol: str, expected: tuple[str, ...]) -> None:
    assert symbol_currencies(symbol) == expected


# --- relevance classification ------------------------------------------------------


def test_usd_event_is_potentially_relevant_to_xauusd_exposure() -> None:
    relevance = classify_relevance(make_event(currency="USD"), XAUUSD)

    # The spec's core case: USD data against gold exposure.
    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "USD" in relevance.reason
    assert "quote currency" in relevance.reason
    assert relevance.symbol == "XAUUSD"
    assert relevance.ticket == XAUUSD.ticket
    assert relevance.type is PositionType.BUY


def test_currency_specific_relevance_for_matching_instrument() -> None:
    eur_usd_relevance = classify_relevance(make_event(currency="EUR", impact=EventImpact.MEDIUM), EURUSD)
    gbp_usd_relevance = classify_relevance(
        make_event(currency="GBP", impact=EventImpact.MEDIUM), make_position("GBPUSD")
    )

    assert eur_usd_relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "base currency" in eur_usd_relevance.reason
    assert gbp_usd_relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "GBP" in gbp_usd_relevance.reason


def test_unrelated_currency_is_not_marked_relevant() -> None:
    gbp_on_eurusd = classify_relevance(make_event(currency="GBP", impact=EventImpact.MEDIUM), EURUSD)
    jpy_on_xauusd = classify_relevance(make_event(currency="JPY"), XAUUSD)

    assert gbp_on_eurusd.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert jpy_on_xauusd.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "not a currency leg" in gbp_on_eurusd.reason


def test_high_impact_usd_event_escalates_only_via_the_reserve_currency_rule() -> None:
    # EURGBP has no USD leg: only a HIGH-impact USD event is treated as a
    # potential (reserve-currency) linkage; a MEDIUM-impact one is not.
    high = classify_relevance(make_event(currency="USD", impact=EventImpact.HIGH), make_position("EURGBP"))
    medium = classify_relevance(make_event(currency="USD", impact=EventImpact.MEDIUM), make_position("EURGBP"))

    assert high.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "reserve" in high.reason
    assert medium.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT


def test_symbol_without_currency_leg_cannot_be_classified_as_relevant() -> None:
    relevance = classify_relevance(make_event(currency="USD"), make_position("US30"))

    assert relevance.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "No currency leg" in relevance.reason


def test_metal_base_currency_branch_is_reachable() -> None:
    # A metal-denominated event (not published by real calendars) still maps
    # deterministically through the base-metal branch.
    relevance = classify_relevance(make_event(currency="XAU"), XAUUSD)

    assert relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "base (traded) metal" in relevance.reason


def test_classifier_never_asserts_the_strongest_level() -> None:
    # Documented limitation: a symbol string alone cannot evidence a direct
    # instrument-level link, so RELEVANT is never emitted by this mechanism.
    symbols = ("XAUUSD", "EURUSD", "GBPJPY", "AUDNZD", "US30")
    currencies = ("USD", "EUR", "GBP", "JPY", "AUD", "XAU")
    impacts = (EventImpact.LOW, EventImpact.MEDIUM, EventImpact.HIGH)

    levels = {
        classify_relevance(make_event(currency=currency, impact=impact), make_position(symbol)).relevance
        for symbol in symbols
        for currency in currencies
        for impact in impacts
    }

    assert RelevanceLevel.RELEVANT not in levels
    assert levels <= {
        RelevanceLevel.POTENTIALLY_RELEVANT,
        RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
    }


def test_classification_is_deterministic() -> None:
    event = make_event(currency="USD", impact=EventImpact.HIGH)

    first = classify_relevance(event, XAUUSD)
    second = classify_relevance(event, XAUUSD)

    assert first == second


def test_overall_relevance_is_the_strongest_position_classification() -> None:
    relevant = PositionRelevance(
        ticket=1, symbol="XAUUSD", type=PositionType.BUY, relevance=RelevanceLevel.POTENTIALLY_RELEVANT, reason="r"
    )
    unrelated = PositionRelevance(
        ticket=2, symbol="US30", type=PositionType.BUY, relevance=RelevanceLevel.NOT_OBVIOUSLY_RELEVANT, reason="r"
    )

    assert overall_relevance((unrelated, relevant)) is RelevanceLevel.POTENTIALLY_RELEVANT
    assert overall_relevance((unrelated,)) is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    # No positions at all: nothing can be asserted.
    assert overall_relevance(()) is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT


# --- intelligence service ---------------------------------------------------------


def test_context_describes_todays_window_and_provenance() -> None:
    service = make_service((XAUUSD,))

    context = service.build_today_context(now=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))

    assert context.as_of == datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    assert context.window_from == DAY_FROM
    assert context.window_to == DAY_TO
    # Provenance marker: never live financial data in this step.
    assert context.data_source == "fake-development-placeholder"
    assert context.position_symbols == ("XAUUSD",)


def test_context_events_are_chronological_and_fully_classified() -> None:
    service = make_service((XAUUSD, EURUSD))

    context = service.build_today_context(now=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))

    timestamps = [item.event.timestamp for item in context.events]
    assert timestamps == sorted(timestamps)
    assert context.events, "the fake catalog must produce today's events"
    for item in context.events:
        # One classification per open position, with deterministic ordering.
        assert [entry.symbol for entry in item.positions] == ["EURUSD", "XAUUSD"]
        assert all(entry.reason for entry in item.positions)
        assert item.overall_relevance in {
            RelevanceLevel.POTENTIALLY_RELEVANT,
            RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
        }


def test_context_filters_events_by_minimum_impact() -> None:
    service = make_service((XAUUSD,))

    high_only = service.build_today_context(minimum_impact=EventImpact.HIGH, now=datetime(2026, 9, 15, tzinfo=UTC))
    unfiltered = service.build_today_context(now=datetime(2026, 9, 15, tzinfo=UTC))

    assert high_only.events
    assert all(item.event.impact is EventImpact.HIGH for item in high_only.events)
    assert len(high_only.events) < len(unfiltered.events)


def test_position_symbols_are_deduplicated_and_sorted() -> None:
    service = make_service(
        (
            make_position("XAUUSD", ticket=2),
            make_position("EURUSD", ticket=3),
            make_position("XAUUSD", ticket=1),
        )
    )

    context = service.build_today_context(now=datetime(2026, 9, 15, tzinfo=UTC))

    assert context.position_symbols == ("EURUSD", "XAUUSD")
    # Per-event relevance entries: one per position, ordered by (symbol, ticket).
    first_event = context.events[0]
    assert [(entry.symbol, entry.ticket) for entry in first_event.positions] == [
        ("EURUSD", 3),
        ("XAUUSD", 1),
        ("XAUUSD", 2),
    ]


def test_no_positions_yields_no_relevance_assertions() -> None:
    service = make_service(())

    context = service.build_today_context(now=datetime(2026, 9, 15, tzinfo=UTC))

    assert context.position_symbols == ()
    assert context.events
    for item in context.events:
        assert item.positions == ()
        assert item.overall_relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT


def test_context_never_contains_trading_actions_or_price_predictions() -> None:
    service = make_service((XAUUSD, EURUSD))

    context = service.build_today_context(now=datetime(2026, 9, 15, tzinfo=UTC))

    reasons = [entry.reason for item in context.events for entry in item.positions]
    assert reasons
    banned = ("buy", "sell", "should", "will rise", "will fall", "target price", "open a", "close the")
    for reason in reasons:
        lowered = reason.lower()
        for token in banned:
            assert token not in lowered, f"trading/predictive language leaked into reason: {reason!r}"

    # The only classifications are the three allowed relevance levels.
    levels = {item.overall_relevance for item in context.events}
    assert levels <= {
        RelevanceLevel.POTENTIALLY_RELEVANT,
        RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
    }


def test_context_is_deterministic_for_the_same_reference_time() -> None:
    service = make_service((XAUUSD,))

    first = service.build_today_context(now=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    second = service.build_today_context(now=datetime(2026, 9, 15, 10, 0, tzinfo=UTC))

    assert first == second


def test_service_rejects_naive_reference_time() -> None:
    service = make_service((XAUUSD,))

    with pytest.raises(ValueError):
        service.build_today_context(now=datetime(2026, 9, 15, 10, 0))


def test_high_impact_catalog_events_are_scoped_by_currency() -> None:
    service = make_service((XAUUSD,))

    context = service.build_today_context(minimum_impact=EventImpact.HIGH, now=datetime(2026, 9, 15, tzinfo=UTC))
    by_currency = {item.event.currency: item for item in context.events}

    # The same impact level is not enough: relevance follows the currency leg.
    assert by_currency["USD"].overall_relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert by_currency["JPY"].overall_relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert all(entry.reason for entry in by_currency["JPY"].positions)
