"""Tests for the economic calendar contract, fake provider and service.

Require none of: real MT5, PostgreSQL, network, credentials, or a real calendar
source. Everything runs against the deterministic FakeEconomicCalendarProvider
and a tiny in-test provider used to prove contract guards. No pytest asyncio
plugin is needed: this slice is fully synchronous.
"""
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.providers.economic_calendar import (
    EconomicCalendarProvider,
    EconomicEvent,
    EventImpact,
    impact_meets_minimum,
)
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.services.economic_calendar import EconomicCalendarService

DAY = datetime(2026, 9, 15, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)


def make_event(
    event_id: str = "e1",
    timestamp: datetime = DAY,
    currency: str = "USD",
    impact: EventImpact = EventImpact.MEDIUM,
    forecast: str | None = None,
    previous: str | None = None,
    actual: str | None = None,
) -> EconomicEvent:
    return EconomicEvent(
        event_id=event_id,
        timestamp=timestamp,
        currency=currency,
        title=f"Test event {event_id}",
        impact=impact,
        forecast=forecast,
        previous=previous,
        actual=actual,
    )


class NaiveTimestampProvider(EconomicCalendarProvider):
    """Contract-violating provider: returns a naive timestamp on purpose."""

    source = "test-naive-timestamps"

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        return (make_event(timestamp=datetime(2026, 9, 15, 10, 0)),)


class FailingProvider(EconomicCalendarProvider):
    """Provider that fails the way an infrastructure-backed source would."""

    source = "test-failing"

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        raise RuntimeError("calendar source unavailable")


# --- contract / fake provider ---------------------------------------------------


def test_fake_provider_returns_typed_events() -> None:
    provider = FakeEconomicCalendarProvider()

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert events, "the deterministic catalog must produce events for a full day"
    assert all(isinstance(event, EconomicEvent) for event in events)
    assert all(isinstance(event.impact, EventImpact) for event in events)


def test_fake_provider_covers_varied_currencies_and_impacts() -> None:
    events = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, WINDOW_TO)

    assert {event.currency for event in events} >= {"USD", "EUR", "GBP", "JPY"}
    assert {event.impact for event in events} == {EventImpact.LOW, EventImpact.MEDIUM, EventImpact.HIGH}


def test_fake_provider_event_timestamps_are_timezone_aware() -> None:
    events = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, WINDOW_TO)

    for event in events:
        assert event.timestamp.tzinfo is not None
        # UTC-sourced data: the offset is exactly zero, never a local offset.
        assert event.timestamp.utcoffset() == timedelta(0)


def test_fake_provider_values_are_nullable_and_varied() -> None:
    events = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, WINDOW_TO)

    # Some release values are present and some are legitimately unavailable.
    assert any(event.forecast is not None for event in events)
    assert any(event.previous is not None for event in events)
    assert any(event.actual is None for event in events)
    assert any(event.actual is not None for event in events)


def test_fake_provider_is_deterministic_for_a_given_window() -> None:
    first = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, WINDOW_TO)
    second = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, WINDOW_TO)

    assert first == second


def test_fake_provider_hardcodes_no_today() -> None:
    # A different requested date yields that date's events, with date-scoped ids.
    other_from = datetime(2031, 3, 4, 0, 0, tzinfo=UTC)
    other_to = datetime(2031, 3, 5, 0, 0, tzinfo=UTC)

    events = FakeEconomicCalendarProvider().get_events(other_from, other_to)

    assert events
    assert all(event.timestamp.date() == datetime(2031, 3, 4).date() for event in events)
    assert all(event.event_id.startswith("fake-2031-03-04") for event in events)


def test_fake_provider_filters_to_the_half_open_window() -> None:
    # A window covering only the first hours of the day keeps only those events.
    events = FakeEconomicCalendarProvider().get_events(WINDOW_FROM, datetime(2026, 9, 15, 9, 0, tzinfo=UTC))

    assert events
    assert all(WINDOW_FROM <= event.timestamp < datetime(2026, 9, 15, 9, 0, tzinfo=UTC) for event in events)


def test_fake_provider_explicit_events_are_window_filtered() -> None:
    inside = make_event("inside", datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    boundary = make_event("boundary", WINDOW_TO)
    outside = make_event("outside", datetime(2026, 9, 14, 10, 0, tzinfo=UTC))
    provider = FakeEconomicCalendarProvider(events=(outside, inside, boundary))

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    # Half-open window: the event exactly at "to" and the one before "from" are out.
    assert events == (inside,)


def test_fake_provider_records_requested_windows() -> None:
    provider = FakeEconomicCalendarProvider()

    provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert provider.windows == [(WINDOW_FROM, WINDOW_TO)]


# --- service ----------------------------------------------------------------------


def test_service_orders_events_chronologically() -> None:
    late = make_event("late", datetime(2026, 9, 15, 18, 0, tzinfo=UTC))
    early = make_event("early", datetime(2026, 9, 15, 2, 0, tzinfo=UTC))
    middle = make_event("middle", datetime(2026, 9, 15, 10, 0, tzinfo=UTC))
    service = EconomicCalendarService(FakeEconomicCalendarProvider(events=(late, early, middle)))

    events = service.get_events(WINDOW_FROM, WINDOW_TO)

    assert [event.event_id for event in events] == ["early", "middle", "late"]


def test_service_orders_equal_timestamps_deterministically() -> None:
    same_time = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
    service = EconomicCalendarService(
        FakeEconomicCalendarProvider(events=(make_event("bbb", same_time), make_event("aaa", same_time)))
    )

    events = service.get_events(WINDOW_FROM, WINDOW_TO)

    assert [event.event_id for event in events] == ["aaa", "bbb"]


def test_service_filters_by_minimum_impact() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())
    all_events = service.get_events(WINDOW_FROM, WINDOW_TO)

    medium_or_higher = service.filter_by_minimum_impact(all_events, EventImpact.MEDIUM)
    high_only = service.filter_by_minimum_impact(all_events, EventImpact.HIGH)

    assert all(event.impact in {EventImpact.MEDIUM, EventImpact.HIGH} for event in medium_or_higher)
    assert all(event.impact is EventImpact.HIGH for event in high_only)
    assert len(high_only) < len(medium_or_higher) <= len(all_events)
    # LOW is the minimum: nothing is filtered out.
    assert service.filter_by_minimum_impact(all_events, EventImpact.LOW) == all_events


def test_impact_ranking_is_explicit_not_alphabetical() -> None:
    # HIGH ranks above MEDIUM/LOW even though it sorts first alphabetically.
    assert impact_meets_minimum(EventImpact.HIGH, EventImpact.MEDIUM) is True
    assert impact_meets_minimum(EventImpact.MEDIUM, EventImpact.HIGH) is False
    assert impact_meets_minimum(EventImpact.LOW, EventImpact.LOW) is True
    assert impact_meets_minimum(EventImpact.LOW, EventImpact.MEDIUM) is False


def test_todays_events_use_the_utc_day_containing_now() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())

    window_from, window_to = service.get_today_window(datetime(2026, 9, 15, 23, 59, tzinfo=UTC))
    events = service.get_todays_events(datetime(2026, 9, 15, 10, 0, tzinfo=UTC))

    assert window_from == datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    assert window_to == datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
    assert events
    assert all(window_from <= event.timestamp < window_to for event in events)


def test_non_utc_offset_now_is_converted_before_selecting_the_day() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())
    # 2026-09-16 00:30 at +02:00 is 2026-09-15 22:30 UTC: still the 15th in UTC.
    plus_two = timezone(timedelta(hours=2))

    window_from, _ = service.get_today_window(datetime(2026, 9, 16, 0, 30, tzinfo=plus_two))

    assert window_from == datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


def test_service_rejects_naive_window_times() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())

    with pytest.raises(ValueError):
        service.get_events(datetime(2026, 9, 15, 0, 0), WINDOW_TO)
    with pytest.raises(ValueError):
        service.get_events(WINDOW_FROM, datetime(2026, 9, 16, 0, 0))


def test_service_rejects_naive_now() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())

    with pytest.raises(ValueError):
        service.get_today_window(datetime(2026, 9, 15, 10, 0))


def test_service_rejects_inverted_window() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())

    with pytest.raises(ValueError):
        service.get_events(WINDOW_TO, WINDOW_FROM)


def test_service_rejects_naive_provider_timestamps() -> None:
    # A provider that violates the aware-timestamp contract must fail loudly
    # rather than letting a naive datetime reach the response.
    service = EconomicCalendarService(NaiveTimestampProvider())

    with pytest.raises(ValueError):
        service.get_events(WINDOW_FROM, WINDOW_TO)


def test_provider_failure_propagates_as_runtime_error() -> None:
    service = EconomicCalendarService(FailingProvider())

    with pytest.raises(RuntimeError):
        service.get_events(WINDOW_FROM, WINDOW_TO)


def test_service_exposes_the_provider_provenance_marker() -> None:
    service = EconomicCalendarService(FakeEconomicCalendarProvider())

    assert service.source == "fake-development-placeholder"
    assert "fake" in service.source
