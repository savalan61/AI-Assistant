import threading

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import SecurityError, decode_token
from app.db.database import get_db
from app.db.models import User, UserRole
from app.providers import (
    AccountInfoProvider,
    EconomicCalendarProvider,
    FakeEconomicCalendarProvider,
    FakeLLMProvider,
    MT5AccountInfoProvider,
    MT5MarketDataProvider,
    MT5PositionProvider,
    MT5TradeHistoryProvider,
    MarketDataProvider,
    PositionProvider,
    TradeHistoryProvider,
)
from app.services.account import AccountInfoService
from app.services.agent import AgentService
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.financial_context import FinancialContextService
from app.services.market import MarketDataService
from app.services.portfolio_intelligence import PortfolioIntelligenceService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

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


# Process-wide trade-history provider cache, mirroring the market-data,
# account-info, and positions ones: lazy, lock-guarded, failed initialization
# never cached.
_trade_history_provider: TradeHistoryProvider | None = None
_trade_history_provider_lock = threading.Lock()


def get_trade_history_provider() -> TradeHistoryProvider:
    global _trade_history_provider
    if _trade_history_provider is None:
        with _trade_history_provider_lock:
            if _trade_history_provider is None:
                _trade_history_provider = MT5TradeHistoryProvider()
    return _trade_history_provider


def get_trade_history_service() -> TradeHistoryService:
    # MT5 initialization failure at this boundary is a service availability issue (503).
    try:
        provider = get_trade_history_provider()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Trade history service temporarily unavailable")
    return TradeHistoryService(provider)


def shutdown_trade_history() -> None:
    # Shut down the cached trade-history provider, if any, and clear the cache
    # so the next request constructs a fresh one. Safe when nothing was built.
    global _trade_history_provider
    provider, _trade_history_provider = _trade_history_provider, None
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


# Economic-calendar wiring. No MT5 terminal and no credentials are involved, so
# there is no process-wide provider cache to guard (unlike the MT5 providers):
# the provider is stateless and cheap to construct per request. The deterministic
# fake is wired here as the development placeholder until a real calendar source
# is selected; its provenance marker is carried through every response so the
# data can never be mistaken for live financial data.
def get_economic_calendar_service() -> EconomicCalendarService:
    provider: EconomicCalendarProvider = FakeEconomicCalendarProvider()
    return EconomicCalendarService(provider)


def get_economic_intelligence_service() -> EconomicIntelligenceService:
    # Reuses the existing positions composition-root path (get_position_service),
    # so there is exactly one position architecture; an MT5 initialization
    # failure surfaces as 503 from there, exactly as for GET /positions.
    return EconomicIntelligenceService(
        calendar_service=get_economic_calendar_service(),
        position_service=get_position_service(),
    )


def get_portfolio_intelligence_service() -> PortfolioIntelligenceService:
    # Reuses the existing account-info and positions composition-root paths, so
    # there is exactly one account architecture and one position architecture
    # (no providers are duplicated here); an MT5 initialization failure surfaces
    # as 503 from those paths, exactly as for GET /account-info and GET /positions.
    return PortfolioIntelligenceService(
        account_service=get_account_info_service(),
        position_service=get_position_service(),
    )


# Agent wiring. The LLM provider is the deterministic FakeLLMProvider: the
# placeholder implementation until a real model adapter is selected, so no API
# key or vendor configuration exists and tests stay offline. The financial
# context flows through the single existing architecture below.
def get_agent_service() -> AgentService:
    return AgentService(
        financial_context_service=FinancialContextService(
            account_service=get_account_info_service(),
            position_service=get_position_service(),
            trade_history_service=get_trade_history_service(),
        ),
        llm_provider=FakeLLMProvider(),
    )


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


async def get_current_broker_manager(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require the authenticated user to manage users: super_admin or admin.

    Authorization rides on top of authentication: the database-backed
    User.role is the sole authority — no role claim exists in (or is read
    from) the JWT. Customers are rejected with 403 so an authenticated
    customer is distinguishable from an unauthenticated caller (401).
    Both manager roles may create customer users; only the super_admin may
    additionally manage admins and broker-level settings (see
    get_current_super_admin).
    """
    if current_user.role not in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Broker manager privileges required")
    return current_user


async def get_current_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Require the authenticated user to be the broker's super_admin.

    The database-backed User.role is the sole authority; at most one
    super_admin exists per broker (enforced by a partial unique index in the
    database). Admins and customers are rejected with 403.
    """
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    return current_user
