import abc
from datetime import datetime
from enum import StrEnum
from typing import NamedTuple


# Importance of an economic event. StrEnum so the JSON value is exactly
# "LOW"/"MEDIUM"/"HIGH" and new levels can only be introduced deliberately
# (mirrors UserRole / PositionType).
class EventImpact(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# Explicit importance ordering. StrEnum members have no meaningful comparison
# order (alphabetical order would place HIGH below LOW), so every minimum-impact
# decision goes through this single table instead of scattered comparisons.
_IMPACT_RANK: dict[EventImpact, int] = {
    EventImpact.LOW: 0,
    EventImpact.MEDIUM: 1,
    EventImpact.HIGH: 2,
}


def impact_meets_minimum(impact: EventImpact, minimum: EventImpact) -> bool:
    """True when ``impact`` is at least as important as ``minimum``."""
    return _IMPACT_RANK[impact] >= _IMPACT_RANK[minimum]


# Typed read-only economic-event snapshot shared by all calendar providers.
# Explicit NamedTuple like Candle/Position/AccountInfo so no provider-specific
# object (HTTP payload, scraper result, vendor model) travels past the provider
# boundary.
#
# Timestamp MUST be timezone-aware (UTC); the service layer rejects naive
# timestamps rather than guessing a local timezone. Release values are kept as
# published strings (e.g. "3.1%", "220K") because calendars publish formatted
# values and coercion would invent precision that was never reported. Any of
# forecast/previous/actual may legitimately be None (not reported / not yet even
# released), and all three stay nullable all the way to the API response.
class EconomicEvent(NamedTuple):
    event_id: str
    timestamp: datetime
    currency: str
    title: str
    impact: EventImpact
    forecast: str | None
    previous: str | None
    actual: str | None


# Abstraction boundary: services depend on this, never on a concrete source
# (mirrors PositionProvider / TradeHistoryProvider). A real economic-calendar
# source can be added later without touching the service or API layers.
class EconomicCalendarProvider(abc.ABC):
    # Data provenance marker surfaced in API responses so a development or test
    # source can never be mistaken for live financial data.
    source: str

    @abc.abstractmethod
    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        """Return the events inside the half-open window [from_time, to_time)."""
        ...
