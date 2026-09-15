"""Deterministic development/test economic-calendar provider.

NOT LIVE DATA. This provider materializes a fixed catalog of placeholder events
on the UTC dates covered by the requested window, so results are reproducible
for a given window and no "today" is hard-coded anywhere in the provider. Every
response built from it carries ``FakeEconomicCalendarProvider.source`` as the
provenance marker, so development data can never be presented as live market
data. A real calendar source will be selected separately.
"""
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple

from app.providers.economic_calendar import (
    EconomicCalendarProvider,
    EconomicEvent,
    EventImpact,
)


class _EventTemplate(NamedTuple):
    """Placeholder event definition; ``at`` is a UTC time of day."""

    suffix: str
    currency: str
    title: str
    impact: EventImpact
    at: time
    forecast: str | None
    previous: str | None
    actual: str | None


# Fixed catalog: several currencies, all three impact levels, different times of
# day, and a deliberate mix of forecast/previous/actual availability (some
# values present, some unreported, some not yet released). Values are
# illustrative placeholders, never market data.
_EVENT_TEMPLATES: tuple[_EventTemplate, ...] = (
    _EventTemplate(
        suffix="api-crude",
        currency="USD",
        title="US API Crude Oil Stock Change (placeholder)",
        impact=EventImpact.LOW,
        at=time(2, 30),
        forecast="-1.2M",
        previous="-0.8M",
        actual=None,
    ),
    _EventTemplate(
        suffix="eur-ip",
        currency="EUR",
        title="Euro Area Industrial Production MoM (placeholder)",
        impact=EventImpact.MEDIUM,
        at=time(8, 0),
        forecast="0.3%",
        previous="-0.2%",
        actual="0.1%",
    ),
    _EventTemplate(
        suffix="gbp-gdp",
        currency="GBP",
        title="UK GDP Growth Rate MoM (placeholder)",
        impact=EventImpact.MEDIUM,
        at=time(10, 0),
        forecast="0.2%",
        previous="0.1%",
        actual="0.2%",
    ),
    _EventTemplate(
        suffix="usd-cpi",
        currency="USD",
        title="US Consumer Price Index (CPI) YoY (placeholder)",
        impact=EventImpact.HIGH,
        at=time(12, 30),
        forecast="3.1%",
        previous="3.2%",
        actual=None,
    ),
    _EventTemplate(
        suffix="usd-claims",
        currency="USD",
        title="US Initial Jobless Claims (placeholder)",
        impact=EventImpact.MEDIUM,
        at=time(13, 30),
        forecast="220K",
        previous="215K",
        actual="218K",
    ),
    _EventTemplate(
        suffix="jpy-boj",
        currency="JPY",
        title="Japan BoJ Interest Rate Decision (placeholder)",
        impact=EventImpact.HIGH,
        at=time(18, 0),
        forecast="0.50%",
        previous="0.50%",
        actual=None,
    ),
)


def _utc_dates_between(from_time: datetime, to_time: datetime) -> tuple[date, ...]:
    """UTC dates touched by the half-open window [from_time, to_time)."""
    start = from_time.astimezone(UTC).date()
    end = to_time.astimezone(UTC).date()
    if end < start:
        return ()
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return tuple(days)


class FakeEconomicCalendarProvider(EconomicCalendarProvider):
    """In-memory calendar provider with fully deterministic placeholder events.

    By default the fixed catalog is materialized on every UTC date the requested
    window touches (event_id is date-scoped, so ids stay unique per day). Tests
    may pass an explicit event tuple to pin exact data instead.
    """

    source = "fake-development-placeholder"

    def __init__(self, events: tuple[EconomicEvent, ...] | None = None):
        # Explicit events (when given) are returned by plain window filtering.
        self._events = events
        # Recorded requested windows so tests can assert delegation.
        self.windows: list[tuple[datetime, datetime]] = []

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        self.windows.append((from_time, to_time))
        if self._events is not None:
            return tuple(event for event in self._events if from_time <= event.timestamp < to_time)
        materialized: list[EconomicEvent] = []
        for day in _utc_dates_between(from_time, to_time):
            materialized.extend(self._events_for_day(day))
        return tuple(event for event in materialized if from_time <= event.timestamp < to_time)

    @staticmethod
    def _events_for_day(day: date) -> tuple[EconomicEvent, ...]:
        return tuple(
            EconomicEvent(
                event_id=f"fake-{day.isoformat()}-{template.suffix}",
                # combine() with tzinfo=UTC keeps every timestamp timezone-aware;
                # no local-time assumption is ever made.
                timestamp=datetime.combine(day, template.at, tzinfo=UTC),
                currency=template.currency,
                title=template.title,
                impact=template.impact,
                forecast=template.forecast,
                previous=template.previous,
                actual=template.actual,
            )
            for template in _EVENT_TEMPLATES
        )
