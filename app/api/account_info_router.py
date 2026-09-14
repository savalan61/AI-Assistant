"""Account Information API: read-only MT5 account snapshot for authenticated users."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import get_account_info_service, get_current_user
from app.db.models import User
from app.providers.account_info import AccountInfo
from app.services.account import AccountInfoService

router = APIRouter()


# Maps the AccountInfo contract to a JSON-safe response schema; the raw MT5
# object never reaches this layer because the provider already converts it.
class AccountInfoResponse(BaseModel):
    login: int
    name: str
    balance: float
    equity: float
    margin: float
    free_margin: float
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
    # RuntimeError means an MT5 infrastructure failure (server error 503);
    # the generic detail never leaks provider internals.
    try:
        info = service.get_account_info()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Account information service temporarily unavailable")
    return info
