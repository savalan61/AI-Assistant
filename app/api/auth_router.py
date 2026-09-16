"""Authentication API: tenant-scoped login endpoint issuing JWT access tokens.

The caller names the broker it is signing in to, because the MT5 login number
is unique only per broker: the same number may legitimately exist at several
brokers, so an unscoped lookup would be ambiguous. Broker identity is used to
scope credential lookup and nothing else — authorization still reads the
authenticated User/Broker rows, and the token carries no tenant claim.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_login_throttle
from app.core.security import create_access_token, dummy_password_verification, verify_password
from app.db.database import get_db
from app.db.models import Broker, User
from app.services.auth import LoginThrottle, LoginThrottleExceededError

router = APIRouter(prefix="/auth", tags=["auth"])


# Application/Agent credentials: `login` is the user's single identity — the
# MT5 account/login number — and the password is the application password. The
# MT5 password is never used here. `broker` is the tenant selector: since the
# login is unique only per broker, the client must say which broker it is
# signing in to. It scopes the credential lookup and is validated against the
# database before any credential work; it never becomes an authorization fact.
class LoginRequest(BaseModel):
    broker: str
    login: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


def _login_failed() -> HTTPException:
    # One generic failure for every rejection path: an unknown broker, an
    # ambiguous broker code, an unknown login inside a known broker, a wrong
    # password, an inactive user, or an inactive broker. The response must not
    # reveal which condition caused it, so it cannot be used to probe which
    # brokers or accounts exist. WWW-Authenticate stays consistent with
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

    The broker is mandatory on every request — never merely a tie-breaker for
    duplicated logins: an optional tenant field would make the *shape* of the
    request reveal whether a login exists at more than one broker.

    Brute-force protection runs *before* any credential lookup, so a throttled
    attempt costs no database work. Failures are counted per client IP and per
    (broker, login); the counters are cleared by a successful login.
    """
    client_ip = request.client.host if request.client is not None else "unknown"
    now = datetime.now(UTC)

    try:
        throttle.check(client_ip, credentials.broker, credentials.login, now)
    except LoginThrottleExceededError:
        raise _too_many_attempts()

    def _rejected() -> HTTPException:
        # Every authentication rejection counts against both keys before the
        # generic 401 is returned, so repeats trigger the throttle.
        throttle.record_failure(client_ip, credentials.broker, credentials.login, now)
        return _login_failed()

    # Resolve the tenant from the supplied broker code, case-insensitively.
    # func.lower() gives up the plain unique index on Broker.code, but the table
    # holds one row per broker, so this remains a cheap lookup. A code is unique
    # per broker, so zero matches (unknown broker) and several matches (a
    # data-integrity violation: codes differing only by case) both fail closed
    # instead of silently choosing a tenant.
    broker_code = credentials.broker.strip().lower()
    brokers = (
        await session.execute(select(Broker).where(func.lower(Broker.code) == broker_code))
    ).scalars().all()
    if len(brokers) != 1:
        # No stored hash was available to check, so spend a dummy verification
        # of equal cost rather than returning early: an early return is measurably
        # faster and would reveal which broker codes exist.
        dummy_password_verification(credentials.password)
        raise _rejected()
    broker = brokers[0]

    # Tenant-scoped lookup: exactly this broker's row for this login. The login
    # is unique inside its broker, so the query can match at most one user; the
    # defensive length check keeps the refusal to act on an ambiguous match.
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

    # The tenant must be active. The broker is the row resolved above — from the
    # database, never taken on the request's word — and the user belongs to it
    # by construction of the scoped query.
    if not broker.is_active:
        raise _rejected()

    # A valid login clears this client's and this broker/login's counters.
    throttle.record_success(client_ip, credentials.broker, credentials.login)

    # Existing security contract: sub = str(User.id); no broker_id and no MT5
    # credentials are placed into the JWT, and post-authentication identity and
    # authorization keep coming from the database rows.
    return TokenResponse(access_token=create_access_token(subject=str(user.id)), token_type="bearer")
