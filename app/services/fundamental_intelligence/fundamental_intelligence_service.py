"""Deterministic fundamental-intelligence context builder (READ-ONLY, no LLM).

Composes three read-only inputs into one structured result a user or an agent
can reason over:

* the **mandatory** economic-intelligence context (today's UTC calendar window,
  its provenance and its per-position relevance) passed in by the caller — this
  service never re-fetches the calendar and never reads MT5, so one agent request
  still performs exactly the position reads it performed before this step;
* news for the same window from the configured news source (or the explicit
  "unavailable" state when a deployment has no news source);
* the caller's own open positions, taken from that same economic context.

Everything it produces is a FACT: timestamps, source, publisher, provenance,
discrete relevance levels and factual exposure. It never produces a forecast, a
probability, a direction, a target or a recommendation, and it never interprets
missing data as an absence of risk — an exposure that cannot be established is
reported as ``UNKNOWN`` with the reason. Interpretation is the LLM answer
layer's job, on top of this labelled context (see the agent prompt builder).

Window semantics: the context reuses the calendar context's ``as_of`` and
half-open ``[window_from, window_to)`` window, so "today" can never differ
between the calendar and the news it is combined with.
"""
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import NamedTuple

from app.providers.news import NewsItem
from app.providers.position import Position, PositionType
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    RelevanceLevel,
    symbol_currencies,
)
from app.services.fundamental_intelligence.relevance import (
    classify_news_relevance,
    strongest_level,
)
from app.services.news import NewsService

# Stated reason when a deployment has no news source configured at all. This is
# deliberately not "no news": the absence of a source is not evidence that
# nothing happened.
_NEWS_UNAVAILABLE_REASON = (
    "No news source is configured for this deployment, so today's news could not be assessed."
)


class ExposureStatus(StrEnum):
    """Whether a position's fundamental exposure could be established at all."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"


class FundamentalNewsItem(NamedTuple):
    """One news item plus its deterministic relevance to the instruments in play."""

    item: NewsItem
    relevance: RelevanceLevel
    reason: str
    matched_instruments: tuple[str, ...]


class PositionFundamentalExposure(NamedTuple):
    """Factual fundamental exposure of one open position (never advice)."""

    ticket: int
    symbol: str
    type: PositionType
    volume: Decimal
    relevance: RelevanceLevel
    status: ExposureStatus
    reason: str
    calendar_event_ids: tuple[str, ...]
    news_item_ids: tuple[str, ...]


class FundamentalContext(NamedTuple):
    """Today's deterministic fundamental context, with provenance per source."""

    as_of: datetime
    window_from: datetime
    window_to: datetime
    focus_symbol: str | None
    instruments: tuple[str, ...]
    calendar: EconomicIntelligenceContext
    news_available: bool
    news_data_source: str | None
    news_unavailable_reason: str | None
    news: tuple[FundamentalNewsItem, ...]
    positions: tuple[PositionFundamentalExposure, ...]


def _normalize_symbol(symbol: str | None) -> str | None:
    """Upper-case/strip a requested instrument, rejecting a blank one."""
    if symbol is None:
        return None
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("focus_symbol must be a non-empty instrument name")
    return normalized


def _news_intelligence(item: NewsItem, instruments: tuple[str, ...]) -> FundamentalNewsItem:
    """Classify one item against every instrument in play."""
    relevances = tuple(classify_news_relevance(item, symbol) for symbol in instruments)
    levels = tuple(relevance.relevance for relevance in relevances)
    level = strongest_level(levels)
    matched = tuple(
        symbol
        for symbol, relevance in zip(instruments, relevances, strict=True)
        if relevance.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    )
    reason = next(
        (relevance.reason for relevance in relevances if relevance.relevance is level),
        "No instrument in play is referenced by this item.",
    )
    return FundamentalNewsItem(
        item=item,
        relevance=level,
        reason=reason,
        matched_instruments=matched,
    )


def _calendar_driver_ids(calendar: EconomicIntelligenceContext, ticket: int) -> tuple[str, ...]:
    """Event ids whose per-position relevance points at this ticket."""
    return tuple(
        sorted(
            item.event.event_id
            for item in calendar.events
            for position in item.positions
            if position.ticket == ticket
            and position.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
        )
    )


def _calendar_level(calendar: EconomicIntelligenceContext, ticket: int) -> RelevanceLevel:
    """Strongest calendar relevance for this ticket (nothing: not obvious)."""
    return strongest_level(
        tuple(
            position.relevance
            for item in calendar.events
            for position in item.positions
            if position.ticket == ticket
        )
    )


def _news_driver_ids(news: tuple[FundamentalNewsItem, ...], symbol: str) -> tuple[str, ...]:
    """News item ids this instrument's symbol is matched to."""
    return tuple(sorted(entry.item.item_id for entry in news if symbol in entry.matched_instruments))


def _news_level(news: tuple[FundamentalNewsItem, ...], symbol: str) -> RelevanceLevel:
    """Strongest news relevance for this symbol (nothing matched: not obvious)."""
    return strongest_level(
        tuple(entry.relevance for entry in news if symbol in entry.matched_instruments)
    )


def _exposure(
    position: Position,
    calendar: EconomicIntelligenceContext,
    news: tuple[FundamentalNewsItem, ...],
    news_available: bool,
) -> PositionFundamentalExposure:
    """Factual exposure of one position; UNKNOWN whenever it cannot be established."""
    calendar_ids = _calendar_driver_ids(calendar, position.ticket)
    news_ids = _news_driver_ids(news, position.symbol)
    relevance = strongest_level(
        (_calendar_level(calendar, position.ticket), _news_level(news, position.symbol))
    )

    if not symbol_currencies(position.symbol):
        # A symbol with no identifiable currency leg cannot be related to
        # currency/commodity drivers at all: that is missing information, not
        # an absence of risk.
        return PositionFundamentalExposure(
            ticket=position.ticket,
            symbol=position.symbol,
            type=position.type,
            volume=position.volume,
            relevance=relevance,
            status=ExposureStatus.UNKNOWN,
            reason=(
                f"No currency leg could be identified in {position.symbol}, so its fundamental "
                "exposure cannot be established from the symbol alone."
            ),
            calendar_event_ids=calendar_ids,
            news_item_ids=news_ids,
        )

    if not news_available and not calendar_ids:
        # Nothing on the calendar side and no news source configured: today's
        # drivers could not be assessed, so this must not read as "nothing is
        # relevant".
        return PositionFundamentalExposure(
            ticket=position.ticket,
            symbol=position.symbol,
            type=position.type,
            volume=position.volume,
            relevance=relevance,
            status=ExposureStatus.UNKNOWN,
            reason=(
                "No relevant calendar event was identified today and no news source is "
                "configured, so today's fundamental drivers could not be assessed."
            ),
            calendar_event_ids=calendar_ids,
            news_item_ids=news_ids,
        )

    reason = (
        f"{len(calendar_ids)} relevant calendar event(s) and {len(news_ids)} relevant news "
        f"item(s) were identified for {position.symbol} today."
    )
    if not news_available:
        reason += " No news source is configured, so news drivers could not be assessed."
    return PositionFundamentalExposure(
        ticket=position.ticket,
        symbol=position.symbol,
        type=position.type,
        volume=position.volume,
        relevance=relevance,
        status=ExposureStatus.KNOWN,
        reason=reason,
        calendar_event_ids=calendar_ids,
        news_item_ids=news_ids,
    )


class FundamentalIntelligenceService:
    """Builds today's fundamental context from the mandatory calendar context.

    ``news_service`` is optional: ``None`` means this deployment has no news
    source configured, which is reported explicitly rather than treated as an
    empty feed. The service performs no MT5 read of its own.
    """

    def __init__(self, news_service: NewsService | None):
        self._news = news_service

    @property
    def news_source(self) -> str | None:
        """Provenance marker of the configured news source, or None when absent."""
        return self._news.source if self._news is not None else None

    def build_context(
        self,
        calendar: EconomicIntelligenceContext,
        focus_symbol: str | None = None,
    ) -> FundamentalContext:
        """Build today's fundamental context from ``calendar`` (and news).

        ``focus_symbol`` is the instrument the request is about (for example
        XAUUSD); the instruments in play are that symbol plus every symbol the
        caller currently holds. The window and reference instant come from the
        calendar context, so the calendar and the news always describe the same
        UTC day.
        """
        normalized_focus = _normalize_symbol(focus_symbol)
        instruments = tuple(
            sorted(
                {*calendar.position_symbols, *((normalized_focus,) if normalized_focus else ())}
            )
        )

        if self._news is None:
            news_available = False
            news_data_source: str | None = None
            unavailable_reason: str | None = _NEWS_UNAVAILABLE_REASON
            items: tuple[NewsItem, ...] = ()
        else:
            news_available = True
            news_data_source = self._news.source
            unavailable_reason = None
            # Retrieved bounded by NewsService (window + provider + hard cap);
            # relevance is decided here, so an untagged item is still considered.
            items = self._news.get_news(calendar.window_from, calendar.window_to)

        news = tuple(_news_intelligence(item, instruments) for item in items)
        positions = tuple(
            _exposure(position, calendar, news, news_available) for position in calendar.positions
        )

        return FundamentalContext(
            as_of=calendar.as_of,
            window_from=calendar.window_from,
            window_to=calendar.window_to,
            focus_symbol=normalized_focus,
            instruments=instruments,
            calendar=calendar,
            news_available=news_available,
            news_data_source=news_data_source,
            news_unavailable_reason=unavailable_reason,
            news=news,
            positions=positions,
        )
