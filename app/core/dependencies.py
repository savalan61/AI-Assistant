import threading

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import SecurityError, decode_token
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers import (
    AccountInfoProvider,
    EconomicCalendarProvider,
    FakeEconomicCalendarProvider,
    LLMProvider,
    LLMProviderPool,
    LLMRouter,
    MT5AccountInfoProvider,
    MT5MarketDataProvider,
    MT5PositionProvider,
    MT5TradeHistoryProvider,
    MarketDataProvider,
    OpenAICompatibleLLMProvider,
    PositionProvider,
    TradeHistoryProvider,
)
from app.services.account import AccountInfoService
from app.services.agent import AgentService, AgentUsageLimiter, OutboundDataPolicy
from app.services.auth import LoginThrottle
from app.services.broker_llm_config import (
    BrokerLLMConfigurationError,
    LLMConnectionTester,
    resolve_broker_llm_provider,
)
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
#
# Fail-closed guard: the placeholder must never be served to a broker's
# customers. Outside development there is no production calendar source yet, so
# the dependency refuses (503) instead of quietly returning fabricated events.
_CALENDAR_PLACEHOLDER_ALLOWED_ENVS = frozenset({"development"})


def get_economic_calendar_service() -> EconomicCalendarService:
    if settings.APP_ENV not in _CALENDAR_PLACEHOLDER_ALLOWED_ENVS:
        raise HTTPException(
            status_code=503,
            detail="Economic calendar data source is not configured",
        )
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


# Agent LLM wiring. The production seam is the broker-aware router: a broker
# with an active configuration uses its own provider, and a broker without one
# uses the shared free pool. The router is built per request because the tenant
# — and therefore the provider — differs per authenticated broker.
#
# Tests override this seam (deps.get_llm_provider) with the deterministic
# FakeLLMProvider, keeping the suite offline and AgentService unchanged.
def get_free_llm_pool() -> LLMProvider:
    """The shared system fallback pool used when a broker has no active provider.

    Providers are tried in order, falling through only on transient failures.
    Today the pool holds at most the deployment-level OpenAI-compatible
    endpoint from settings (the operator's own provider); real free-tier
    providers are appended here later. An absent or unusable deployment
    endpoint simply leaves the pool empty, which fails safely (503) rather than
    ever inventing an answer.
    """
    providers: list[LLMProvider] = []
    if settings.LLM_API_KEY.strip() and settings.LLM_MODEL.strip():
        try:
            providers.append(
                OpenAICompatibleLLMProvider(
                    api_key=settings.LLM_API_KEY,
                    base_url=settings.LLM_BASE_URL,
                    model=settings.LLM_MODEL,
                    timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                )
            )
        except RuntimeError:
            # A placeholder/blank credential counts as "not configured": the
            # pool stays empty and the request fails safely with 503.
            providers = []
    return LLMProviderPool(providers)


async def get_llm_provider(broker_id: int, session: AsyncSession) -> LLMProvider:
    """Resolve the production LLM seam for one broker (composition boundary).

    ``broker_id`` comes from the authenticated database user, never from a
    request body. A broker whose active configuration exists but cannot be used
    fails safely here, so its traffic never silently moves onto the shared free
    pool; a broker with no active configuration gets the free pool.
    """
    try:
        broker_provider = await resolve_broker_llm_provider(session=session, broker_id=broker_id)
    except BrokerLLMConfigurationError:
        raise HTTPException(status_code=503, detail="Agent service temporarily unavailable")
    return LLMRouter(
        broker_id=broker_id,
        broker_provider=broker_provider,
        free_pool=get_free_llm_pool(),
    )


# Per-user daily usage limiting is process-wide state by its documented nature
# (in-process counters, reset on restart): one limiter per process, lazily
# built from settings behind the same lock-guarded pattern as the MT5 provider
# caches. The daily limit comes from configuration, never hard-coded.
_agent_usage_limiter: AgentUsageLimiter | None = None
_agent_usage_limiter_lock = threading.Lock()


def get_agent_usage_limiter() -> AgentUsageLimiter:
    global _agent_usage_limiter
    if _agent_usage_limiter is None:
        with _agent_usage_limiter_lock:
            if _agent_usage_limiter is None:
                _agent_usage_limiter = AgentUsageLimiter(settings.AGENT_DAILY_REQUEST_LIMIT)
    return _agent_usage_limiter


def reset_agent_usage_limiter() -> None:
    # Test/development seam: drop the process-wide limiter so the next request
    # rebuilds it from current settings. Never called in production flow.
    global _agent_usage_limiter
    with _agent_usage_limiter_lock:
        _agent_usage_limiter = None


def get_llm_connection_tester() -> LLMConnectionTester:
    # Stateless and cheap: no process-wide cache, because it holds no
    # connection and no credentials — each check builds a short-lived provider
    # around the broker's own (already decrypted) configuration.
    return LLMConnectionTester()


def get_outbound_data_policy() -> OutboundDataPolicy:
    """The policy for what may leave the process in an external LLM prompt.

    Resolved from configuration today. This is deliberately the seam a future
    per-broker consent / data-processing agreement plugs into: it only has to
    return a different OutboundDataPolicy instance, and neither the prompt
    builder nor AgentService changes.
    """
    return OutboundDataPolicy.from_settings()


# Login brute-force protection is process-wide state by its documented nature
# (in-process per-IP and per-username counters, reset on restart): one throttle
# per process, lazily built from settings behind the same lock-guarded pattern
# as the other in-process limiters. The thresholds come from configuration.
_login_throttle: LoginThrottle | None = None
_login_throttle_lock = threading.Lock()


def get_login_throttle() -> LoginThrottle:
    global _login_throttle
    if _login_throttle is None:
        with _login_throttle_lock:
            if _login_throttle is None:
                _login_throttle = LoginThrottle(
                    settings.LOGIN_MAX_FAILURES, settings.LOGIN_FAILURE_WINDOW_SECONDS
                )
    return _login_throttle


def reset_login_throttle() -> None:
    # Test/development seam: drop the process-wide throttle so the next request
    # rebuilds it from current settings. Never called in production flow.
    global _login_throttle
    with _login_throttle_lock:
        _login_throttle = None




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

    # The tenant must still be active. Login already checks this, but a token
    # issued before a broker was suspended must stop working immediately rather
    # than at the next login. The same generic detail is reused so the response
    # never reveals whether the user, the broker, or the role caused it.
    broker = await session.get(Broker, user.broker_id)
    if broker is None or not broker.is_active:
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


async def get_agent_service(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AgentService:
    """Composition root for POST /agent.

    Declared after the authentication boundary because it depends on the
    authenticated user: the broker's own LLM configuration is resolved from
    that user's broker (never from the request body), and a broker with no
    active configuration uses the shared free pool. The financial context keeps
    flowing through the single existing MT5 composition architecture below, so
    AgentService depends on LLMProvider alone.
    """
    llm_provider = await get_llm_provider(current_user.broker_id, session)
    return AgentService(
        financial_context_service=FinancialContextService(
            account_service=get_account_info_service(),
            position_service=get_position_service(),
            trade_history_service=get_trade_history_service(),
        ),
        llm_provider=llm_provider,
        # The outbound-data policy is applied to the prompt the provider gets,
        # so what may leave the process is explicit at the composition boundary.
        data_policy=get_outbound_data_policy(),
    )
