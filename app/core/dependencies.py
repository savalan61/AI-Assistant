import threading

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecurityError, decode_token
from app.db.database import get_db
from app.db.models import User, UserRole
from app.providers import (
    AccountInfoProvider,
    MT5AccountInfoProvider,
    MT5MarketDataProvider,
    MT5PositionProvider,
    MarketDataProvider,
    PositionProvider,
)
from app.services.account import AccountInfoService
from app.services.market import MarketDataService
from app.services.positions import PositionService

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


# Process-wide account-info provider cache, mirroring the market-data one:
# lazy, lock-guarded, and a failed initialization is never cached so later
# requests may retry. Both providers attach to the same MT5 terminal session.
_account_info_provider: AccountInfoProvider | None = None
_account_info_provider_lock = threading.Lock()


def get_account_info_provider() -> AccountInfoProvider:
    global _account_info_provider
    if _account_info_provider is None:
        with _account_info_provider_lock:
            if _account_info_provider is None:
                _account_info_provider = MT5AccountInfoProvider()
    return _account_info_provider


def get_account_info_service() -> AccountInfoService:
    # MT5 initialization failure at this boundary is a service availability issue (503).
    try:
        provider = get_account_info_provider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Account information service temporarily unavailable")
    return AccountInfoService(provider)


def shutdown_account_info() -> None:
    # Shut down the cached account-info provider, if any, and clear the cache
    # so the next request constructs a fresh one. Safe when nothing was built.
    global _account_info_provider
    provider, _account_info_provider = _account_info_provider, None
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


# Process-wide positions provider cache, mirroring the market-data and
# account-info ones: lazy, lock-guarded, failed initialization never cached.
_position_provider: PositionProvider | None = None
_position_provider_lock = threading.Lock()


def get_position_provider() -> PositionProvider:
    global _position_provider
    if _position_provider is None:
        with _position_provider_lock:
            if _position_provider is None:
                _position_provider = MT5PositionProvider()
    return _position_provider


def get_position_service() -> PositionService:
    # MT5 initialization failure at this boundary is a service availability issue (503).
    try:
        provider = get_position_provider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Positions service temporarily unavailable")
    return PositionService(provider)


def shutdown_positions() -> None:
    # Shut down the cached positions provider, if any, and clear the cache
    # so the next request constructs a fresh one. Safe when nothing was built.
    global _position_provider
    provider, _position_provider = _position_provider, None
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
