import threading

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecurityError, decode_token
from app.db.database import get_db
from app.db.models import User, UserRole
from app.providers import MarketDataProvider, MT5MarketDataProvider
from app.services.market import MarketDataService

# Process-wide provider cache. It stays None until a construction succeeds, so
# a failed MT5 initialization is never cached and later requests may retry.
_provider: MarketDataProvider | None = None
_provider_lock = threading.Lock()


# Composition root for market-data wiring: this is the only place that knows
# the concrete provider. The provider is injected through the service, keeping
# the HTTP layer independent of MT5.
def get_market_data_provider() -> MarketDataProvider:
    # Lazy singleton: construct MT5MarketDataProvider once per process. The lock
    # keeps "constructed once" true because dependencies run in worker threads.
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = MT5MarketDataProvider()
    return _provider


def get_market_data_service() -> MarketDataService:
    # MT5 initialization failure at this boundary is a service availability issue (503).
    try:
        provider = get_market_data_provider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market data service temporarily unavailable")
    return MarketDataService(provider)


def shutdown_market_data() -> None:
    # Shut down the cached provider, if any, and clear the cache so the next
    # request constructs a fresh one. Safe to call when nothing was initialized.
    global _provider
    provider, _provider = _provider, None
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


# Bearer scheme for HTTP authentication. auto_error=False lets this dependency
# raise its own 401 (FastAPI's built-in error would be 403) with a proper
# WWW-Authenticate challenge for every failure mode.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token (sub = User.id)")


def _unauthorized(detail: str) -> HTTPException:
    # RFC 6750: every 401 carries WWW-Authenticate: Bearer. Details stay generic
    # so JWT internals and database specifics never reach the client.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Resolve a Bearer JWT into the active User ORM object it names.

    Expected authentication failures become HTTP 401 here; database
    infrastructure failures deliberately propagate as server errors.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated")

    # JWT validation through the existing security boundary; PyJWT details
    # cannot leak because decode_token translates them into SecurityError.
    try:
        payload = decode_token(credentials.credentials)
    except SecurityError:
        raise _unauthorized("Invalid or expired token")

    # The subject must safely name a User.id (int primary key). No other token
    # claim is trusted: broker_id travels with the ORM object from the
    # database, never from the token.
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise _unauthorized("Invalid token subject")
    try:
        user_id = int(subject)
    except ValueError:
        raise _unauthorized("Invalid token subject")

    user = await session.get(User, user_id)

    # Inactive users must never authenticate.
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")

    return user


async def get_current_broker_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require the authenticated user to be a Broker Admin.

    Authorization rides on top of authentication: the database-backed
    User.role is the sole authority — no role claim exists in (or is read
    from) the JWT. Non-admins are rejected with 403 so an authenticated
    customer is distinguishable from an unauthenticated caller (401).
    """
    if current_user.role != UserRole.BROKER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Broker admin privileges required")
    return current_user
