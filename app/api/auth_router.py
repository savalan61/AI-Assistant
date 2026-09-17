"""Authentication API: the login endpoint issuing JWT access tokens.

This is a ONE-BROKER deployment: the broker it serves is the single brokers row
(resolved server-side by app/core/dependencies.load_deployment_broker), so the
client sends only its own credentials — there is no broker field, path segment
or query parameter to select a tenant with, and no lookup can be ambiguous.
Authorization still reads the authenticated User/Broker rows, and the token
carries no customer claim.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_login_throttle, load_deployment_broker
from app.core.security import create_access_token, dummy_password_verification, verify_password
from app.db.database import get_db
from app.db.models import User
from app.services.auth import LoginThrottle, LoginThrottleExceededError

router = APIRouter(prefix="/auth", tags=["auth"])


# Application/Agent credentials: `login` is the customer's single identity — the
# MT5 account/login number — and the password is the application password. The
# MT5 password is never used here. There is deliberately no broker field: this
# deployment serves exactly one broker, which the server resolves from its own
# configuration, so nothing the client sends can select or influence a customer.
class LoginRequest(BaseModel):
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


def _login_failed() -> HTTPException:
    # One generic failure for every rejection path: an unknown login, a wrong
    # password, an inactive user, or an unusable/inactive deployment broker. The
    # response must not reveal which condition caused it, so it cannot be used to
    # probe which accounts exist. WWW-Authenticate stays consistent with
    # get_current_user().
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

    The broker is resolved from this deployment's own configuration, never from
    the request, and the credential lookup is scoped to it. A login belonging to
    any other broker therefore cannot authenticate even if the password matches.

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

    # The one broker this deployment serves (the single brokers row), resolved
    # server-side.
    # Missing or inactive: spend a dummy password verification instead of
    # returning early, so the timing and the answer stay identical to a wrong
    # password and the deployment's state cannot be probed.
    broker = await load_deployment_broker(session)
    if broker is None or not broker.is_active:
        dummy_password_verification(credentials.password)
        raise _rejected()

    # Lookup scoped to that broker: the user must belong to it, or the login is
    # simply not this deployment's. The login is unique inside the broker, so the
    # query can match at most one user; the defensive length check keeps the
    # refusal to act on an ambiguous match.
    users = (
        await session.execute(
            select(User).where(User.broker_id == broker.id, User.login == credentials.login)
        )
    ).scalars().all()
    if len(users) != 1:
        dummy_password_verification(credentials.password)
        raise _rejected()
    user = users[0]

    if not verify_password(credentials.password, user.password_hash):
        raise _rejected()

    # Inactive users must never authenticate.
    if not user.is_active:
        raise _rejected()

    # A valid login clears this client's and this login's counters.
    throttle.record_success(client_ip, credentials.login)

    # Existing security contract: sub = str(User.id); no broker_id and no MT5
    # credentials are placed into the JWT, and post-authentication identity and
    # authorization keep coming from the database rows.
    return TokenResponse(access_token=create_access_token(subject=str(user.id)), token_type="bearer")
