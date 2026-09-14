"""Authentication API: login endpoint issuing JWT access tokens."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, verify_password
from app.db.database import get_db
from app.db.models import Broker, User

router = APIRouter(prefix="/auth", tags=["auth"])


# Application/Agent credentials: username is the MT5 login number and the
# password is the application password. The MT5 password is never used here.
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


def _login_failed() -> HTTPException:
    # One generic failure for every rejection path: the response must not
    # reveal whether the username, password, user state, or broker state
    # caused it. WWW-Authenticate stays consistent with get_current_user().
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Verify application credentials and issue an access token.

    The endpoint stays thin: hashing/verification and token creation are
    delegated to the existing security primitives. Expected authentication
    failures become a generic 401; database/infrastructure errors propagate.
    """
    # Username is unique per broker, not globally: an ambiguous match across
    # tenants is refused rather than silently picking one tenant.
    users = (await session.execute(select(User).where(User.username == credentials.username))).scalars().all()
    if len(users) != 1:
        raise _login_failed()
    user = users[0]

    if not verify_password(credentials.password, user.password_hash):
        raise _login_failed()

    # Inactive users must never authenticate.
    if not user.is_active:
        raise _login_failed()

    # The tenant must exist and be active; broker_id comes from the database
    # relationship, never from the request.
    broker = await session.get(Broker, user.broker_id)
    if broker is None or not broker.is_active:
        raise _login_failed()

    # Existing security contract: sub = str(User.id); no broker_id and no MT5
    # credentials are placed into the JWT.
    return TokenResponse(access_token=create_access_token(subject=str(user.id)), token_type="bearer")
