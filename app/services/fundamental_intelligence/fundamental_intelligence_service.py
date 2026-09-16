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

Relevance is decided per instrument through that instrument's documented
fundamental profile (Step 48, app/services/instrument_intelligence), so an item
that never names the instrument can still be related to it — and can be related
at different levels to different instruments in play. Each item therefore carries
its per-instrument classification, and each exposure names the factors today's
drivers matched for it.

Everything it produces is a FACT: timestamps, source, publisher, provenance,
discrete relevance levels, matched subject areas and factual exposure. It never
produces a forecast, a probability, a direction, a target or a recommendation,
and it never interprets missing data as an absence of risk — an exposure that
cannot be established is reported as ``UNKNOWN`` with the reason. Interpretation
is the LLM answer layer's job, on top of this labelled context (see the agent
prompt builder).

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
    InstrumentRelevance,
    classify_instrument_relevance,
    strongest_level,
)
from app.services.instrument_intelligence import (
    FundamentalDomain,
    RelevanceKind,
    domain_labels,
    order_domains,
    profile_for,
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
    """One news item plus its deterministic relevance to the instruments in play.

    ``relevance``/``reason``/``kind``/``domains`` describe the STRONGEST match
    across the instruments in play; ``matches`` carries the per-instrument
    classification, so the same item can be RELEVANT to one instrument and only
    POTENTIALLY_RELEVANT (or not obviously relevant) to another. The last three
    fields are defaulted so existing constructions keep working.
    """

    item: NewsItem
    relevance: RelevanceLevel
    reason: str
    matched_instruments: tuple[str, ...]
    kind: RelevanceKind | None = None
    domains: tuple[FundamentalDomain, ...] = ()
    matches: tuple[InstrumentRelevance, ...] = ()


class PositionFundamentalExposure(NamedTuple):
    """Factual fundamental exposure of one open position (never advice).

    ``factors`` (Step 48) names the documented fundamental factors today's
    calendar events and news items matched for this position, in the shared
    vocabulary's order. It is a list of subject areas, never a direction, and it
    is defaulted so existing constructions keep working; the HTTP contract of
    GET /fundamental-intelligence/today is unchanged (the factors are stated in
    ``reason``).
    """

    ticket: int
    symbol: str
    type: PositionType
    volume: Decimal
    relevance: RelevanceLevel
    status: ExposureStatus
    reason: str
    calendar_event_ids: tuple[str, ...]
    news_item_ids: tuple[str, ...]
    factors: tuple[str, ...] = ()


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


def news_intelligence(item: NewsItem, instruments: tuple[str, ...]) -> FundamentalNewsItem:
    """Classify one item against every instrument in play.

    Each instrument is classified through its own documented fundamental profile,
    so an item that never names an instrument can still be relevant - and can be
    relevant at different levels to different instruments. Shared by the
    fundamental context and the financial-research context (Step 49): one
    classification, so both surfaces can never disagree about an item.
    """
    matches = tuple(classify_instrument_relevance(item, symbol) for symbol in instruments)
    level = strongest_level(tuple(match.level for match in matches))
    # Every instrument an item is related to at all (the strongest match may be
    # "not obviously relevant", in which case nothing matched).
    matched = tuple(
        match.symbol for match in matches if match.level is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    )
    strongest = next((match for match in matches if match.level is level), None)
    if not matched:
        # Nothing anywhere: state that once, instead of reporting one arbitrary
        # instrument's per-instrument verdict as if it were the item's summary.
        reason = (
            "No documented fundamental factor or instrument reference was found for any "
            "instrument in play."
        )
    elif strongest is not None:
        reason = strongest.reason
    else:  # pragma: no cover - defensive: a related item always has a strongest match
        reason = "No instrument in play is referenced by this item."
    return FundamentalNewsItem(
        item=item,
        relevance=level,
        reason=reason,
        matched_instruments=matched,
        kind=strongest.kind if strongest is not None else None,
        domains=strongest.domains if strongest is not None else (),
        matches=matches,
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


def _symbol_match(entry: FundamentalNewsItem, symbol: str) -> InstrumentRelevance | None:
    """This instrument's own classification of one item, if it has one."""
    return next((match for match in entry.matches if match.symbol == symbol), None)


def _news_driver_ids(news: tuple[FundamentalNewsItem, ...], symbol: str) -> tuple[str, ...]:
    """News item ids this instrument's symbol is related to."""
    return tuple(
        sorted(
            entry.item.item_id
            for entry in news
            if (match := _symbol_match(entry, symbol)) is not None
            and match.level is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
        )
    )


def _news_level(news: tuple[FundamentalNewsItem, ...], symbol: str) -> RelevanceLevel:
    """Strongest news relevance for this symbol (nothing matched: not obvious)."""
    return strongest_level(
        tuple(
            match.level
            for entry in news
            if (match := _symbol_match(entry, symbol)) is not None
        )
    )


def _exposure_factors(
    calendar: EconomicIntelligenceContext,
    news: tuple[FundamentalNewsItem, ...],
    position: Position,
) -> tuple[str, ...]:
    """The documented fundamental factors today's drivers matched for a position.

    Calendar attribution comes from the position's own relevance records (which
    carry the domains the shared vocabulary matched), news attribution from the
    instrument's own per-instrument match. Only factors from drivers that were
    actually related are reported, and they are returned in the vocabulary's
    order, so the same inputs always produce the same list.
    """
    domains: tuple[FundamentalDomain, ...] = ()
    for item in calendar.events:
        for relevance in item.positions:
            if relevance.ticket == position.ticket and (
                relevance.relevance is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
            ):
                domains += relevance.domains
    for entry in news:
        match = _symbol_match(entry, position.symbol)
        if match is not None and match.level is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT:
            domains += match.domains
    return domain_labels(order_domains(domains))


def _exposure(
    position: Position,
    calendar: EconomicIntelligenceContext,
    news: tuple[FundamentalNewsItem, ...],
    news_available: bool,
) -> PositionFundamentalExposure:
    """Factual exposure of one position; UNKNOWN whenever it cannot be established."""
    calendar_ids = _calendar_driver_ids(calendar, position.ticket)
    news_ids = _news_driver_ids(news, position.symbol)
    factors = _exposure_factors(calendar, news, position)
    relevance = strongest_level(
        (_calendar_level(calendar, position.ticket), _news_level(news, position.symbol))
    )

    if not symbol_currencies(position.symbol) and profile_for(position.symbol) is None:
        # No currency leg AND no documented fundamental profile: nothing this
        # layer can relate the symbol to at all. That is missing information, not
        # an absence of risk. (A symbol whose profile is known is assessed
        # through it instead - see the reason below.)
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
    if not symbol_currencies(position.symbol):
        reason += (
            " The symbol has no currency leg, so its documented fundamental profile was used "
            "to identify them."
        )
    if factors:
        # Names the subject areas the drivers matched, never a direction: this is
        # what makes the classification explainable to a reader and to the model.
        reason += f" Relevant fundamental factors: {', '.join(factors)}."
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
        factors=factors,
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

        news = tuple(news_intelligence(item, instruments) for item in items)
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
