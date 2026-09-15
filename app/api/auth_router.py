"""Authentication API: login endpoint issuing JWT access tokens."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_login_throttle
from app.core.security import create_access_token, verify_password
from app.db.database import get_db
from app.db.models import Broker, User
from app.services.auth import LoginThrottle, LoginThrottleExceededError

router = APIRouter(prefix="/auth", tags=["auth"])


# Application/Agent credentials: `login` is the user's single identity — the
# MT5 account/login number — and the password is the application password. The
# MT5 password is never used here.
class LoginRequest(BaseModel):
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


def _login_failed() -> HTTPException:
    # One generic failure for every rejection path: the response must not
    # reveal whether the login, password, user state, or broker state caused
    # it. WWW-Authenticate stays consistent with get_current_user().
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect login or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _too_many_attempts() -> HTTPException:
    # Deliberately identical for a throttled IP and a throttled login, and
    # independent of whether the account exists, so it cannot be used to probe
    # which logins are real.
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed login attempts; try again later",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> TokenResponse:
    """Verify application credentials and issue an access token.

    The endpoint stays thin: hashing/verification and token creation are
    delegated to the existing security primitives. Expected authentication
    failures become a generic 401; database/infrastructure errors propagate.

    Brute-force protection runs *before* any credential lookup, so a throttled
    attempt costs no database work. Failures are counted per client IP and per
    submitted login; the counters are cleared by a successful login.
    """
    client_ip = request.client.host if request.client is not None else "unknown"
    now = datetime.now(UTC)

    try:
        throttle.check(client_ip, credentials.login, now)
    except LoginThrottleExceededError:
        raise _too_many_attempts()

    def _rejected() -> HTTPException:
        # Every authentication rejection counts against both keys before the
        # generic 401 is returned, so repeats trigger the throttle.
        throttle.record_failure(client_ip, credentials.login, now)
        return _login_failed()

    # Login is unique per broker, not globally: an ambiguous match across
    # tenants is refused rather than silently picking one tenant.
    users = (await session.execute(select(User).where(User.login == credentials.login))).scalars().all()
    if len(users) != 1:
        raise _rejected()
    user = users[0]

    if not verify_password(credentials.password, user.password_hash):
        raise _rejected()

    # Inactive users must never authenticate.
    if not user.is_active:
        raise _rejected()

    # The tenant must exist and be active; broker_id comes from the database
    # relationship, never from the request.
    broker = await session.get(Broker, user.broker_id)
    if broker is None or not broker.is_active:
        raise _rejected()

    # A valid login clears this client's and this login's counters.
    throttle.record_success(client_ip, credentials.login)

    # Existing security contract: sub = str(User.id); no broker_id and no MT5
    # credentials are placed into the JWT.
    return TokenResponse(access_token=create_access_token(subject=str(user.id)), token_type="bearer")
