from datetime import UTC, datetime, timedelta

from app.providers.economic_calendar import (
    EconomicCalendarProvider,
    EconomicEvent,
    EventImpact,
    impact_meets_minimum,
)


def _require_aware(value: datetime, field: str) -> datetime:
    """Return ``value`` normalized to UTC, rejecting naive datetimes.

    Timezone handling is explicit at this boundary: a naive datetime is a caller
    or provider contract violation, so it fails loudly instead of silently
    assuming the server's local timezone.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


# Business-level access to the economic calendar. Depends on the provider
# abstraction, not on a concrete source (mirrors PositionService /
# TradeHistoryService); all date handling is explicit UTC with no hidden
# local-time assumptions.
class EconomicCalendarService:
    def __init__(self, provider: EconomicCalendarProvider):
        self._provider = provider

    @property
    def source(self) -> str:
        """Provenance marker of the underlying provider (never live data)."""
        return self._provider.source

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        """Events inside the half-open window [from_time, to_time), chronological.

        Provider timestamps are validated to be timezone-aware here, so a
        non-conforming provider cannot leak naive datetimes into the response.
        """
        window_from = _require_aware(from_time, "from_time")
        window_to = _require_aware(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")

        events = self._provider.get_events(window_from, window_to)
        for event in events:
            _require_aware(event.timestamp, f"event timestamp ({event.event_id})")
        # Chronological order is a business guarantee of this service; event_id
        # breaks ties so equal timestamps stay deterministic.
        return tuple(sorted(events, key=lambda event: (event.timestamp, event.event_id)))

    def get_today_window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        """UTC day window [00:00, next 00:00) containing ``now`` (default: now)."""
        reference = _require_aware(now, "now") if now is not None else datetime.now(UTC)
        day_start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start, day_start + timedelta(days=1)

    def get_todays_events(self, now: datetime | None = None) -> tuple[EconomicEvent, ...]:
        """Today's events (UTC day), chronologically ordered."""
        window_from, window_to = self.get_today_window(now)
        return self.get_events(window_from, window_to)

    def filter_by_minimum_impact(
        self, events: tuple[EconomicEvent, ...], minimum: EventImpact
    ) -> tuple[EconomicEvent, ...]:
        """Keep events whose importance is at least ``minimum`` (order preserved)."""
        return tuple(event for event in events if impact_meets_minimum(event.impact, minimum))
