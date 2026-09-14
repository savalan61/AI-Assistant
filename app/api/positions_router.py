"""Positions API: read-only MT5 open positions for authenticated users."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_position_service
from app.db.models import User
from app.providers.position import Position
from app.services.positions import PositionService

router = APIRouter()


# Maps the Position contract to a JSON-safe response schema; the raw MT5
# object never reaches this layer because the provider already converts it.
# from_attributes lets model_validate() read the NamedTuple contract directly
# (same convention as UserResponse for ORM objects).
class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    symbol: str
    type: str
    volume: float
    open_price: float
    current_price: float
    profit: float


class PositionsResponse(BaseModel):
    # Wrapped contract: positions is always an array, never a bare list and
    # never a 404, so "no open positions" is a normal 200 response.
    positions: list[PositionResponse]


@router.get("/positions", response_model=PositionsResponse)
async def get_positions(
    # Authentication boundary: any active user (customer or broker admin) may
    # read the open positions of the MT5 account this process is attached to.
    # Tenant identity stays with the database-backed User; no account/login
    # parameter is accepted from the client.
    current_user: User = Depends(get_current_user),
    service: PositionService = Depends(get_position_service),
) -> PositionsResponse:
    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop through the consolidated MT5 blocking boundary.
    # RuntimeError means an MT5 infrastructure failure (server error 503);
    # the generic detail never leaks provider internals.
    try:
        positions = await run_mt5_call(service.get_positions)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Positions service temporarily unavailable")
    return PositionsResponse(positions=[PositionResponse.model_validate(p) for p in positions])
