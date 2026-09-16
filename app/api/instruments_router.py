"""Instruments API: read-only MT5 instrument discovery for authenticated users.

Two thin routes over one service: a single-symbol resolution (the common case —
"does this broker offer what I asked for?") and a bounded catalog listing
("what can I ask about?"). Both answer only from the authenticated tenant's own
MT5 terminal, so no caller can discover another tenant's broker catalog.
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from app.core.blocking import run_mt5_call
from app.core.dependencies import get_current_user, get_instrument_service
from app.db.models import User
from app.providers.instrument import Instrument
from app.services.instruments import InstrumentService

router = APIRouter()


# Maps the Instrument contract to a JSON-safe response schema; the raw MT5
# record never reaches this layer because the provider already converts it.
# from_attributes lets model_validate() read the NamedTuple contract directly
# (same convention as PositionResponse). Every optional field is nullable
# because "the broker did not provide it" is a normal outcome, not an error.
class InstrumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str | None
    asset_class: str | None
    base_currency: str | None
    quote_currency: str | None
    digits: int | None
    trade_mode: str | None


class InstrumentCatalogResponse(BaseModel):
    # Wrapped contract: instruments is always an array and never a 404, so
    # "this broker offers nothing matching" is a normal 200 response. total and
    # truncated state the bound instead of hiding it.
    instruments: list[InstrumentResponse]
    total: int
    truncated: bool


@router.get("/instruments", response_model=InstrumentCatalogResponse)
async def list_instruments(
    # Bounded literal substring search (see InstrumentService). Optional: no
    # search lists the catalog from the start, bounded by the service.
    search: str | None = Query(default=None, max_length=64),
    # Authentication boundary: any active user may list the instruments of the
    # MT5 account this process is authenticated as. Tenant identity stays with
    # the database-backed User; no account/login parameter is accepted.
    current_user: User = Depends(get_current_user),
    service: InstrumentService = Depends(get_instrument_service),
) -> InstrumentCatalogResponse:
    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop through the consolidated MT5 blocking boundary.
    # ValueError means a rejected search term (client error 422);
    # RuntimeError means an MT5 infrastructure failure (server error 503).
    try:
        catalog = await run_mt5_call(service.list_instruments, search)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError:
        raise HTTPException(
            status_code=503, detail="Instrument service temporarily unavailable"
        )
    return InstrumentCatalogResponse(
        instruments=[InstrumentResponse.model_validate(i) for i in catalog.instruments],
        total=catalog.total,
        truncated=catalog.truncated,
    )


@router.get("/instruments/{symbol}", response_model=InstrumentResponse)
async def resolve_instrument(
    # Path-bounded so an oversized symbol can never reach the provider.
    symbol: str = Path(min_length=1, max_length=64),
    current_user: User = Depends(get_current_user),
    service: InstrumentService = Depends(get_instrument_service),
) -> InstrumentResponse:
    # ValueError means the broker does not offer the requested instrument
    # (client error 404); RuntimeError means an MT5 infrastructure failure
    # (server error 503). The generic detail never leaks provider internals, and
    # no credential is ever part of the response.
    try:
        instrument: Instrument = await run_mt5_call(service.resolve, symbol)
    except ValueError:
        raise HTTPException(status_code=404, detail="Instrument unavailable for the requested symbol")
    except RuntimeError:
        raise HTTPException(
            status_code=503, detail="Instrument service temporarily unavailable"
        )
    return InstrumentResponse.model_validate(instrument)
