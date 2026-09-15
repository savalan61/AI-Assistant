"""Account Information API: read-only MT5 account snapshot for authenticated users."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.numeric import DecimalAsNumber
from app.core.blocking import run_mt5_call
from app.core.dependencies import get_account_info_service, get_current_user
from app.db.models import User
from app.providers.account_info import AccountInfo
from app.services.account import AccountInfoService

router = APIRouter()


# Maps the AccountInfo contract to a JSON-safe response schema; the raw MT5
# object never reaches this layer because the provider already converts it.
# Money fields are Decimal in the contract and serialize as JSON numbers via
# DecimalAsNumber (the wire format is unchanged from the float era).
class AccountInfoResponse(BaseModel):
    login: int
    name: str
    balance: DecimalAsNumber
    equity: DecimalAsNumber
    margin: DecimalAsNumber
    free_margin: DecimalAsNumber
    # A ratio, not money: float in the contract and float on the wire.
    margin_level: float
    currency: str
    server: str


@router.get("/account-info", response_model=AccountInfoResponse)
async def get_account_info(
    # Authentication boundary: any active user (customer or broker admin) may
    # read account information. Tenant identity stays with the database-backed
    # User; no broker_id is accepted from the request.
    current_user: User = Depends(get_current_user),
    service: AccountInfoService = Depends(get_account_info_service),
) -> AccountInfoResponse:
    # The provider call blocks (MT5), so it is explicitly offloaded from the
    # event loop through the consolidated MT5 blocking boundary.
    # RuntimeError means an MT5 infrastructure failure (server error 503);
    # the generic detail never leaks provider internals.
    try:
        info = await run_mt5_call(service.get_account_info)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Account information service temporarily unavailable")
    return info
