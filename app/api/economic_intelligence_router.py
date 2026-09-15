"""Economic Intelligence API: read-only, AI-ready economic context.

Returns today's economic calendar events together with a deterministic
relevance classification against the authenticated user's current open
positions. Strictly read-only intelligence: no prices are predicted, no
BUY/SELL actions are produced, and no trading endpoint exists here.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_economic_intelligence_service
from app.db.models import User
from app.providers.economic_calendar import EventImpact
from app.services.economic_intelligence import EconomicIntelligenceService

router = APIRouter()


# Maps the calendar contract to a JSON-safe schema; the provider already
# converted any source-specific object, so nothing raw reaches this layer.
# from_attributes lets model_validate() read the NamedTuple contracts directly
# (same convention as PositionResponse / TradeResponse).
class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    timestamp: datetime
    currency: str
    title: str
    impact: str
    forecast: str | None
    previous: str | None
    actual: str | None


class PositionRelevanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    symbol: str
    type: str
    relevance: str
    # Deterministic factual reason (e.g. "USD is the quote currency of
    # XAUUSD, a USD-denominated metal instrument; ..."). Never a forecast.
    reason: str


class EventIntelligenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event: EventResponse
    overall_relevance: str
    positions: list[PositionRelevanceResponse]


class EconomicIntelligenceResponse(BaseModel):
    # broker_id is the authenticated user's own tenant identity, echoed for the
    # client; it is never accepted as request input.
    broker_id: int
    as_of: datetime
    window_from: datetime
    window_to: datetime
    # Provenance marker: e.g. "fake-development-placeholder" while the calendar
    # source is the deterministic development provider. Consumers must never
    # treat that data as live financial data.
    data_source: str
    position_symbols: list[str]
    events: list[EventIntelligenceResponse]


@router.get("/economic-intelligence/today", response_model=EconomicIntelligenceResponse)
async def get_todays_economic_intelligence(
    # Optional business filter: keep only events at least this important.
    # Omitted means no impact filtering (all of today's events are returned).
    min_impact: EventImpact | None = Query(
        None,
        description="Minimum event importance to include (LOW, MEDIUM or HIGH). Omit for all events.",
    ),
    # Authentication boundary: any active user (customer, admin or super_admin)
    # may read economic intelligence. Tenant identity stays with the
    # database-backed User; no broker_id/user_id parameter is accepted, so a
    # caller can never widen or redirect the scope of the response.
    current_user: User = Depends(get_current_user),
    service: EconomicIntelligenceService = Depends(get_economic_intelligence_service),
) -> EconomicIntelligenceResponse:
    # Building the context reads open positions (blocking MT5), so the whole
    # call is offloaded through the consolidated MT5 blocking boundary.
    # RuntimeError means an MT5 infrastructure failure (server error 503); the
    # generic detail never leaks provider internals.
    try:
        context = await run_mt5_call(service.build_today_context, min_impact)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Economic intelligence service temporarily unavailable")

    return EconomicIntelligenceResponse(
        # Tenant identity of the authenticated user, read from the database
        # record — never from the request or a token claim.
        broker_id=current_user.broker_id,
        as_of=context.as_of,
        window_from=context.window_from,
        window_to=context.window_to,
        data_source=context.data_source,
        position_symbols=list(context.position_symbols),
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
            for item in context.events
        ],
    )
