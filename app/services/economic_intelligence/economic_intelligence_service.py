"""AI-ready economic-intelligence context builder (read-only, no LLM).

Composes today's economic calendar events with the user's current open positions
and the deterministic relevance classification into one structured result a
future Agent/LLM can consume. No LLM call is made here and nothing is generated:
every field is derived from calendar data and position data, so the result is
reproducible and safe to reason over.

The user's open positions are read through the existing PositionService (the
single position architecture); this service makes no MT5 call of its own.
"""
from datetime import UTC, datetime
from typing import NamedTuple

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.position import Position
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence.relevance import (
    PositionRelevance,
    RelevanceLevel,
    classify_relevance,
    overall_relevance,
)
from app.services.positions import PositionService


class EventIntelligence(NamedTuple):
    """One event plus how it relates to the current open positions."""

    event: EconomicEvent
    overall_relevance: RelevanceLevel
    positions: tuple[PositionRelevance, ...]


def _event_intelligence(event: EconomicEvent, positions: tuple[Position, ...]) -> EventIntelligence:
    """Classify one event against every position currently held."""
    relevances = tuple(classify_relevance(event, position) for position in positions)
    return EventIntelligence(
        event=event,
        overall_relevance=overall_relevance(relevances),
        positions=relevances,
    )


class EconomicIntelligenceContext(NamedTuple):
    """Everything a future Agent needs to explain today's events to the user."""

    as_of: datetime
    window_from: datetime
    window_to: datetime
    data_source: str
    position_symbols: tuple[str, ...]
    events: tuple[EventIntelligence, ...]


class EconomicIntelligenceService:
    def __init__(self, calendar_service: EconomicCalendarService, position_service: PositionService):
        self._calendar = calendar_service
        self._positions = position_service

    def build_today_context(
        self,
        minimum_impact: EventImpact | None = None,
        now: datetime | None = None,
    ) -> EconomicIntelligenceContext:
        """Build today's (UTC day) economic intelligence context.

        ``minimum_impact`` optionally filters events by importance; ``now``
        injects the reference time (defaults to the current UTC time) so the
        day window and ``as_of`` stay explicit and testable. Reads positions
        through PositionService, which performs a blocking read, so callers on
        the event loop must offload this call via the MT5 blocking boundary.
        """
        reference = now if now is not None else datetime.now(UTC)
        # get_today_window validates timezone-awareness (naive input fails loudly).
        window_from, window_to = self._calendar.get_today_window(reference)
        events = self._calendar.get_events(window_from, window_to)
        if minimum_impact is not None:
            events = self._calendar.filter_by_minimum_impact(events, minimum_impact)

        positions = self._positions.get_positions()
        # Deterministic presentation: stable position order and a deduplicated,
        # sorted symbol set, independent of provider row order.
        ordered_positions = tuple(sorted(positions, key=lambda position: (position.symbol, position.ticket)))
        position_symbols = tuple(sorted({position.symbol for position in ordered_positions}))

        events_intelligence = tuple(_event_intelligence(event, ordered_positions) for event in events)

        return EconomicIntelligenceContext(
            as_of=reference.astimezone(UTC),
            window_from=window_from,
            window_to=window_to,
            data_source=self._calendar.source,
            position_symbols=position_symbols,
            events=events_intelligence,
        )
