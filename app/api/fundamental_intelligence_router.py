"""Fundamental Intelligence API: read-only, deterministic fundamental context.

Returns today's economic-calendar context (the same mandatory context the Agent
receives, with its per-position relevance), the news configured for this
deployment with a deterministic relevance classification, and each open
position's factual fundamental exposure — for an optional focus instrument
(XAUUSD first) plus every instrument the caller actually holds.

Strictly read-only intelligence: no forecast, no probability, no direction, no
trading action, and no endpoint here can mutate anything. Tenant identity comes
only from the authenticated database user, so a caller can never read another
tenant's positions.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.api.economic_intelligence_router import (
    EventIntelligenceResponse,
    EventResponse,
    PositionRelevanceResponse,
)
from app.api.numeric import DecimalAsNumber
from app.core.blocking import run_mt5_call
from app.core.dependencies import (
    get_current_user,
    get_economic_intelligence_service,
    get_fundamental_intelligence_service,
)
from app.db.models import User
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.fundamental_intelligence import FundamentalContext, FundamentalIntelligenceService

router = APIRouter()


class NewsItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: str
    published_at: datetime
    publisher: str
    title: str
    # Bounded excerpt as published; the provider boundary rejects an unbounded
    # one rather than truncating silently.
    summary: str
    url: str | None
    instruments: list[str]
    currencies: list[str]
    categories: list[str]


class FundamentalNewsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item: NewsItemResponse
    # Strongest relevance across the instruments in play, from the same discrete
    # levels the calendar uses. Never a score and never a direction.
    overall_relevance: str
    matched_instruments: list[str]
    # Deterministic factual reason for the classification above.
    reason: str


class CalendarContextResponse(BaseModel):
    # Provenance marker of the calendar source (e.g. a development placeholder
    # while no production vendor is registered). Never live data in this step.
    data_source: str
    events: list[EventIntelligenceResponse]


class NewsContextResponse(BaseModel):
    # False means no news source is configured/available for this deployment.
    # ``items`` is then empty and ``unavailable_reason`` explains that news could
    # not be assessed — deliberately distinct from "there is no news".
    available: bool
    data_source: str | None
    unavailable_reason: str | None
    items: list[FundamentalNewsResponse]


class PositionExposureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    symbol: str
    type: str
    volume: DecimalAsNumber
    # strongest factual relevance across today's drivers
    relevance: str
    # KNOWN when the exposure could be established, UNKNOWN when information is
    # insufficient (the reason states why) — missing data is never "no risk".
    status: str
    reason: str
    calendar_event_ids: list[str]
    news_item_ids: list[str]


class FundamentalIntelligenceResponse(BaseModel):
    # broker_id is the authenticated user's own tenant identity, echoed for the
    # client; it is never accepted as request input.
    broker_id: int
    as_of: datetime
    window_from: datetime
    window_to: datetime
    # The instrument the request asked about (uppercased), when one was given.
    focus_symbol: str | None
    # Every instrument in play: the focus symbol plus the caller's own holdings.
    instruments: list[str]
    calendar: CalendarContextResponse
    news: NewsContextResponse
    positions: list[PositionExposureResponse]


def _calendar_context(context: FundamentalContext) -> CalendarContextResponse:
    """Render the mandatory calendar context (unchanged shape) for this response."""
    return CalendarContextResponse(
        data_source=context.calendar.data_source,
        events=[
            EventIntelligenceResponse(
                event=EventResponse.model_validate(item.event),
                overall_relevance=item.overall_relevance.value,
                positions=[
                    PositionRelevanceResponse(
                        ticket=relevance.ticket,
                        symbol=relevance.symbol,
                        type=relevance.type.value,
                        relevance=relevance.relevance.value,
                        reason=relevance.reason,
                    )
                    for relevance in item.positions
                ],
            )
            for item in context.calendar.events
        ],
    )


@router.get("/fundamental-intelligence/today", response_model=FundamentalIntelligenceResponse)
async def get_todays_fundamental_intelligence(
    # Optional focus instrument (e.g. XAUUSD). It is only a label for relevance
    # computation: tenant scope still comes solely from the authenticated user.
    symbol: str | None = Query(
        None,
        min_length=1,
        max_length=32,
        description="Optional instrument to focus on (e.g. XAUUSD). Omit to cover only your holdings.",
    ),
    # Authentication boundary: any active user may read fundamental intelligence.
    # No broker_id/user_id parameter exists, so a caller cannot widen the scope.
    current_user: User = Depends(get_current_user),
    economic_service: EconomicIntelligenceService = Depends(get_economic_intelligence_service),
    fundamental_service: FundamentalIntelligenceService = Depends(
        get_fundamental_intelligence_service
    ),
) -> FundamentalIntelligenceResponse:
    # The mandatory calendar context is built FIRST and always: every fundamental
    # answer passes through it, exactly like every Agent request. Building it
    # reads open positions (blocking MT5), so the whole composition is offloaded
    # through the consolidated MT5 blocking boundary. No extra MT5 read happens
    # for the news/exposure layer, which consumes this same context.
    def compose() -> FundamentalContext:
        calendar = economic_service.build_today_context()
        return fundamental_service.build_context(calendar, focus_symbol=symbol)

    try:
        context = await run_mt5_call(compose)
    except ValueError:
        # A symbol that normalizes to nothing is a client input problem.
        raise HTTPException(status_code=422, detail="symbol must be a non-empty instrument name")
    except RuntimeError:
        # MT5, calendar or news infrastructure failure: the established generic
        # 503, with no provider internals in the detail.
        raise HTTPException(
            status_code=503, detail="Fundamental intelligence service temporarily unavailable"
        )

    return FundamentalIntelligenceResponse(
        broker_id=current_user.broker_id,
        as_of=context.as_of,
        window_from=context.window_from,
        window_to=context.window_to,
        focus_symbol=context.focus_symbol,
        instruments=list(context.instruments),
        calendar=_calendar_context(context),
        news=NewsContextResponse(
            available=context.news_available,
            data_source=context.news_data_source,
            unavailable_reason=context.news_unavailable_reason,
            items=[
                FundamentalNewsResponse(
                    item=NewsItemResponse.model_validate(entry.item),
                    overall_relevance=entry.relevance.value,
                    matched_instruments=list(entry.matched_instruments),
                    reason=entry.reason,
                )
                for entry in context.news
            ],
        ),
        positions=[
            PositionExposureResponse(
                ticket=exposure.ticket,
                symbol=exposure.symbol,
                type=exposure.type.value,
                volume=exposure.volume,
                relevance=exposure.relevance.value,
                status=exposure.status.value,
                reason=exposure.reason,
                calendar_event_ids=list(exposure.calendar_event_ids),
                news_item_ids=list(exposure.news_item_ids),
            )
            for exposure in context.positions
        ],
    )
