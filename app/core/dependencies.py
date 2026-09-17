import logging
import threading
from typing import NoReturn

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import EconomicCalendarSource, NewsSource, settings
from app.core.mt5_ownership import MT5OwnershipGuard, ownership_lock_path
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.core.security import SecurityError, decode_token
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers import (
    AlphaVantageNewsProvider,
    EconomicCalendarProvider,
    FakeEconomicCalendarProvider,
    FakeNewsProvider,
    LLMProvider,
    LLMProviderPool,
    LLMRouter,
    MT5AccountInfoProvider,
    MT5InstrumentProvider,
    MT5MarketDataProvider,
    MT5PositionProvider,
    MT5TradeHistoryProvider,
    OpenAICompatibleLLMProvider,
    QuantGistEconomicCalendarProvider,
)
from app.services.account import AccountInfoService
from app.services.agent import AgentService, AgentUsageLimiter, OutboundDataPolicy
from app.services.auth import LoginThrottle
from app.services.broker_llm_config import (
    BrokerLLMConfigurationError,
    LLMConnectionTester,
    resolve_llm_provider,
)
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.financial_context import FinancialContextService
from app.services.fundamental_intelligence import (
    FinancialResearchService,
    FundamentalIntelligenceService,
)
from app.services.instruments import InstrumentService
from app.services.market import MarketDataService
from app.services.news import NewsService
from app.services.portfolio_intelligence import PortfolioIntelligenceService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

# Process-wide MT5 session: the terminal connection is process-global by
# construction (the MT5 Python API authenticates one account per process), so
# this manager — lock plus authenticated identity — is the single owner of it and
# the only process-wide MT5 object. It is lazy and lock-guarded, and a failed
# authentication is never cached (the manager forgets the identity so the next
# request retries). Providers are no longer cached: they became cheap per-request
# objects carrying the authenticated customer's credentials, so no cached provider
# can ever serve one customer's data to another.
_mt5_session_manager: MT5SessionManager | None = None
_mt5_session_manager_lock = threading.Lock()

# The single-owner guard for that session's terminal: process-global state, so it
# is a lazy lock-guarded singleton like the manager above (see
# get_mt5_ownership_guard).
_mt5_ownership_guard: MT5OwnershipGuard | None = None
_mt5_ownership_guard_lock = threading.Lock()


def get_mt5_session_manager() -> MT5SessionManager:
    global _mt5_session_manager
    if _mt5_session_manager is None:
        with _mt5_session_manager_lock:
            if _mt5_session_manager is None:
                # The deployment's terminal executable and IPC timeout enter here,
                # at the composition root - never read inside the boundary - so
                # the session manager stays a pure boundary that tests can drive
                # without configuration. The timeout is converted to MT5's own
                # unit (milliseconds) once, here.
                timeout_ms = int(settings.MT5_TIMEOUT_SECONDS * 1000)
                _mt5_session_manager = MT5SessionManager(
                    terminal_path=settings.MT5_TERMINAL_PATH,
                    timeout_ms=timeout_ms if timeout_ms > 0 else None,
                )
    return _mt5_session_manager


def get_mt5_ownership_guard() -> MT5OwnershipGuard:
    """The process's MT5 terminal ownership guard (one per process).

    Enforces the invariant the session manager cannot: the session lock is
    thread-local to this process, so "exactly one process owns this terminal" has
    to be held OUTSIDE the process - one exclusive OS file lock per terminal,
    kept for the process lifetime (app/core/mt5_ownership.py). The lock is keyed
    on MT5_TERMINAL_PATH, so a deployment that pins its own terminal is a
    different owner from one that lets the package find it, and two processes
    driving different terminals do not block each other.
    """
    global _mt5_ownership_guard
    if _mt5_ownership_guard is None:
        with _mt5_ownership_guard_lock:
            if _mt5_ownership_guard is None:
                _mt5_ownership_guard = MT5OwnershipGuard(
                    lock_path=ownership_lock_path(settings.MT5_TERMINAL_PATH)
                )
    return _mt5_ownership_guard


def reset_mt5_ownership_guard() -> None:
    """Test/development seam: drop the guard so the next caller rebuilds it.

    Any hold this process still has is released first, so a test that never
    exited its lifespan cannot leave the process owning the terminal.
    """
    global _mt5_ownership_guard
    with _mt5_ownership_guard_lock:
        guard, _mt5_ownership_guard = _mt5_ownership_guard, None
    if guard is not None:
        while guard.owned:
            guard.release()


def shutdown_mt5_session() -> None:
    # Release the single terminal session at application shutdown and clear the
    # cache so a later request (or a test) rebuilds a fresh manager. Safe when
    # nothing was ever authenticated.
    global _mt5_session_manager
    manager, _mt5_session_manager = _mt5_session_manager, None
    if manager is not None:
        manager.shutdown()


def effective_mt5_login(user: User) -> str | None:
    """The MT5 account number this user is read as (digits, or None).

    Since Step 42 there is exactly ONE identity column: ``user.login`` is both
    the application login and the MT5 account number, so there is nothing to
    prefer and no second column to fall back to. A non-numeric login is simply
    not an MT5 account number and fails closed at the session boundary — which
    is how the development role accounts (whose logins are words) keep
    behaving. Kept as a string so leading zeros survive.
    """
    login = user.login.strip()
    return login if login.isdecimal() else None


def effective_mt5_server(broker: Broker | None) -> str | None:
    """The MT5 server every customer of this deployment's broker trades on.

    There is ONE broker and therefore one MT5 server: it is the broker's own
    configuration (``brokers.mt5_server``) and nothing else can set it. A user
    record has no server field at all, and no request may supply one, so a
    customer's identity is always (the broker's server, that customer's login) —
    which is what makes a customer unable to point their own reads at an MT5
    server belonging to somebody else. A blank/unset value is simply "not
    configured" and fails closed at the session boundary.
    """
    configured = (broker.mt5_server or "").strip() if broker is not None else ""
    return configured or None


def resolve_mt5_account_credentials(user: User, broker: Broker | None) -> MT5AccountCredentials:
    """Extract one customer's MT5 identity from trusted server-side rows only.

    The identity is (this deployment's broker MT5 server, the user's own
    ``login``): both halves come from the database, never from a request body, a
    query parameter or a token claim, so no caller can select another customer's
    account or another broker's server. ``login`` is the MT5 account number as
    well as the application login; a non-numeric login is not an account number
    and fails closed. ``mt5_password_encrypted`` is the stored ciphertext of the
    customer's MT5 INVESTOR (read-only) password — the trading password is never
    accepted anywhere in this system. Nothing is decrypted here: the ciphertext
    travels to the session boundary, which is the only place the plaintext
    exists, and nothing raises — an incomplete record fails closed there with a
    message that never discloses which value was missing.
    """
    login = effective_mt5_login(user)
    return MT5AccountCredentials(
        login=int(login) if login is not None else None,
        server=effective_mt5_server(broker),
        password_encrypted=user.mt5_password_encrypted,
    )


# --- the one broker this deployment serves ----------------------------------
#
# This is a ONE-BROKER product: the deployment IS one broker's assistant, so the
# broker is not a per-request (or per-deployment-name) selector — it is the one
# row in the brokers table, and every authenticated request is bound to it here.
#
# Binding it in exactly one place is what keeps the boundary honest:
#
#   * no endpoint accepts a broker id, code, login, server or role from the client;
#   * a user row belonging to any other broker can never authenticate, so such a
#     row is inert rather than a second tenant;
#   * the broker's suspended state fails closed for every request immediately;
#   * a database that is not a one-broker database (no row, or several) fails
#     closed instead of arbitrarily picking one.
_deployment_logger = logging.getLogger(__name__)


def _deployment_unavailable(reason: str) -> None:
    """Log the precise reason server-side; the client keeps a generic answer."""
    _deployment_logger.warning("The deployment's broker is not usable (%s); refusing the request", reason)


async def load_deployment_broker(session: AsyncSession) -> Broker | None:
    """The single broker row this deployment serves, or None when unusable.

    Exactly one row is required. None means the deployment is misconfigured — no
    broker at all, or several (a leftover from the multi-broker era, which would
    make "which broker is this?" ambiguous) — and every caller fails closed.

    An INACTIVE row is still returned: suspension has its own, existing answer
    (401 for authentication, the established "unavailable" detail for the
    provisioning paths), so the reason stays precise in the logs without
    changing any status code the API already promised.
    """
    result = await session.execute(select(Broker).order_by(Broker.id.asc()))
    brokers = result.scalars().all()
    if len(brokers) != 1:
        _deployment_unavailable(f"{len(brokers)} brokers rows exist, exactly one is required")
        return None
    return brokers[0]


# Economic-calendar wiring. No MT5 terminal and no credentials are involved, so
# there is no process-wide provider or session to guard: the provider is
# stateless and cheap to construct per request.
#
# The calendar is MANDATORY for every Agent request, so the source this
# deployment serves is an explicit configuration value (ECONOMIC_CALENDAR_SOURCE)
# and an unusable one fails closed (503) instead of silently degrading to another
# source:
#
#   * "auto" (the default) keeps the historical environment-driven selection: in
#     development the QuantGist free tier when an API key is configured,
#     otherwise the deterministic fake; anywhere else there is no production
#     source configured, so it refuses.
#   * "development_fake" and "quantgist" name development/test sources
#     explicitly and are served inside development only: the fake is placeholder
#     data and QuantGist is a delayed, quota-limited stand-in, so neither may be
#     presented to a broker's customers.
#   * "production" is the deliberate production seam. No production vendor is
#     implemented (see CURRENT_CHECKPOINT.md known issue 9): a production
#     provider is registered in the single branch below when one is chosen, and
#     until then selecting it fails closed rather than serving development data.
#
# Either development source carries its own provenance marker through every
# response (and into the agent prompt), so its data can never be mistaken for
# live financial data.
_CALENDAR_PLACEHOLDER_ALLOWED_ENVS = frozenset({"development"})

# One generic client-facing detail for every unusable-source case: which setting
# or environment caused it stays server-side, like every other configuration
# failure in this project.
_CALENDAR_SOURCE_UNAVAILABLE_DETAIL = "Economic calendar data source is not configured"

_calendar_logger = logging.getLogger(__name__)


def _calendar_source_unavailable(source: str, reason: str) -> NoReturn:
    """Refuse to serve calendar data, naming the reason to the operator only.

    The precise, value-free reason is logged, because a misconfigured source
    would otherwise be indistinguishable from an outage; the client keeps
    getting the same generic 503 the endpoint has always returned.
    """
    _calendar_logger.warning(
        "Economic calendar source %r is not usable in APP_ENV %r (%s); "
        "refusing to serve calendar data",
        source,
        settings.APP_ENV,
        reason,
    )
    raise HTTPException(status_code=503, detail=_CALENDAR_SOURCE_UNAVAILABLE_DETAIL)


def _calendar_provider_for(source: EconomicCalendarSource) -> EconomicCalendarProvider:
    """Build the provider for one source — the one place a source becomes a provider.

    The PRODUCTION branch is the deliberate seam: a real production calendar
    provider is registered there when the operator chooses one. Until then the
    branch refuses, so no deployment can accidentally serve development data as
    production data.
    """
    if source == EconomicCalendarSource.DEVELOPMENT_FAKE:
        return FakeEconomicCalendarProvider()
    if source == EconomicCalendarSource.QUANTGIST:
        return QuantGistEconomicCalendarProvider(
            api_key=settings.QUANTGIST_API_KEY,
            base_url=settings.QUANTGIST_BASE_URL,
            timeout_seconds=settings.QUANTGIST_TIMEOUT_SECONDS,
        )
    return _calendar_source_unavailable(
        "production", "no production economic-calendar provider is implemented"
    )


def get_economic_calendar_service() -> EconomicCalendarService:
    """Resolve the configured calendar source for this deployment.

    Selection is explicit and fails closed: an unusable source raises the
    established generic 503 rather than falling back to another source, because
    the calendar is mandatory for every Agent request.
    """
    in_development = settings.APP_ENV in _CALENDAR_PLACEHOLDER_ALLOWED_ENVS
    source = settings.ECONOMIC_CALENDAR_SOURCE

    if source == EconomicCalendarSource.AUTO:
        if not in_development:
            _calendar_source_unavailable(
                "auto", "no production economic-calendar source is configured"
            )
        source = (
            EconomicCalendarSource.QUANTGIST
            if settings.QUANTGIST_API_KEY.strip()
            else EconomicCalendarSource.DEVELOPMENT_FAKE
        )

    if source == EconomicCalendarSource.DEVELOPMENT_FAKE and not in_development:
        _calendar_source_unavailable(
            "development_fake", "the deterministic placeholder is development-only"
        )
    if source == EconomicCalendarSource.QUANTGIST:
        if not in_development:
            _calendar_source_unavailable("quantgist", "development/test source only")
        if not settings.QUANTGIST_API_KEY.strip():
            _calendar_source_unavailable("quantgist", "QUANTGIST_API_KEY is not configured")

    return EconomicCalendarService(_calendar_provider_for(source))


# News wiring (Step 47). Same shape as the calendar seam: an explicit
# NEWS_SOURCE resolved here, development/test sources served inside development
# only, and no silent fallback. The deliberate difference is what "auto" means
# outside development: the calendar is MANDATORY for every agent request (so an
# unusable calendar source refuses), while there is simply NO news source
# configured there - which the fundamental context reports as explicitly
# unavailable, never as "no news".
_NEWS_SOURCE_UNAVAILABLE_DETAIL = "News data source is not configured"

_news_logger = logging.getLogger(__name__)


def _news_source_unavailable(source: str, reason: str) -> NoReturn:
    """Refuse to serve news, naming the reason to the operator only."""
    _news_logger.warning(
        "News source %r is not usable in APP_ENV %r (%s); refusing to serve news",
        source,
        settings.APP_ENV,
        reason,
    )
    raise HTTPException(status_code=503, detail=_NEWS_SOURCE_UNAVAILABLE_DETAIL)


def get_news_service() -> NewsService | None:
    """Resolve the configured news source (None = no news source configured).

    An explicitly selected but unusable source refuses (503) instead of falling
    back; an environment with no news source at all returns None, which the
    fundamental context reports as explicitly unavailable rather than empty.

    Selection matrix (mirrors the calendar seam, with one deliberate difference:
    no news source is a supported state rather than a refusal):

    * auto in development - Alpha Vantage when ALPHA_VANTAGE_API_KEY is
      configured, otherwise the deterministic fake;
    * auto anywhere else - no news source (None);
    * development_fake / alphavantage - served inside development only, with
      alphavantage additionally requiring a configured key;
    * production - refuses everywhere until a real vendor is registered.
    """
    in_development = settings.APP_ENV in _CALENDAR_PLACEHOLDER_ALLOWED_ENVS
    source = settings.NEWS_SOURCE

    if source == NewsSource.AUTO:
        if not in_development:
            return None
        # Development: the real development source when it is configured,
        # otherwise the deterministic fake (the historical behaviour).
        source = (
            NewsSource.ALPHAVANTAGE
            if settings.ALPHA_VANTAGE_API_KEY.strip()
            else NewsSource.DEVELOPMENT_FAKE
        )

    if source == NewsSource.DEVELOPMENT_FAKE:
        if not in_development:
            _news_source_unavailable("development_fake", "development/test source only")
        return NewsService(FakeNewsProvider(), max_items=settings.NEWS_MAX_ITEMS)

    if source == NewsSource.ALPHAVANTAGE:
        if not in_development:
            _news_source_unavailable("alphavantage", "development/test source only")
        if not settings.ALPHA_VANTAGE_API_KEY.strip():
            _news_source_unavailable("alphavantage", "ALPHA_VANTAGE_API_KEY is not configured")
        return NewsService(
            AlphaVantageNewsProvider(
                api_key=settings.ALPHA_VANTAGE_API_KEY,
                base_url=settings.ALPHA_VANTAGE_BASE_URL,
                timeout_seconds=settings.ALPHA_VANTAGE_TIMEOUT_SECONDS,
            ),
            max_items=settings.NEWS_MAX_ITEMS,
        )

    return _news_source_unavailable("production", "no production news provider is implemented")


def get_fundamental_intelligence_service() -> FundamentalIntelligenceService:
    """Compose the fundamental context from the existing calendar path and news.

    The service holds no MT5 provider: the positions arrive with the mandatory
    economic-intelligence context at request time, so news relevance and exposure
    are added without a second MT5 read.
    """
    return FundamentalIntelligenceService(news_service=get_news_service())


# Agent LLM wiring. The production seam is the deployment's own configuration:
# when this broker's LLM configuration is active it is used, and when the broker
# has none the shared free pool applies. Both are resolved per request (the
# configuration row can be changed at any time by the operator).
#
# Tests override this seam (deps.get_llm_provider) with the deterministic
# FakeLLMProvider, keeping the suite offline and AgentService unchanged.
def get_free_llm_pool() -> LLMProvider:
    """The shared system fallback pool used when the broker has no configuration.

    Providers are tried in order, falling through only on transient failures:

    * the deployment-level OpenAI-compatible endpoint from settings (the
      operator's own provider, and the seam a paid production provider plugs
      into);
    * Step 56: the pinned OpenRouter free model, added in DEVELOPMENT only and
      only when its key is configured. It is the real model the development chat
      uses, behind the SAME OpenAICompatibleLLMProvider as the endpoint above, so
      the agent still depends only on LLMProvider and the vendor stays
      replaceable. Outside development the key is ignored, so a configured
      free-tier credential can never serve a broker's customers.

    An absent or unusable provider simply leaves the pool empty, which fails
    safely (503) rather than ever inventing an answer.
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
    # Step 56: the development free tier. Development-only is the whole point of
    # the guard - the key is a stand-in, never a production vendor - so a key
    # set in any other environment is deliberately ignored here.
    if (
        settings.APP_ENV == "development"
        and settings.OPENROUTER_API_KEY.strip()
        and settings.OPENROUTER_MODEL.strip()
    ):
        try:
            providers.append(
                OpenAICompatibleLLMProvider(
                    api_key=settings.OPENROUTER_API_KEY,
                    base_url=settings.OPENROUTER_BASE_URL,
                    # The pinned model is a setting, not a per-request choice:
                    # one model, no dynamic pool.
                    model=settings.OPENROUTER_MODEL,
                    timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                )
            )
        except RuntimeError:
            # A placeholder/blank credential counts as "not configured": skip
            # just this provider rather than disturbing the endpoint already in
            # the pool (unlike the deployment endpoint above, whose own invalid
            # configuration empties the pool).
            pass
    return LLMProviderPool(providers)


async def get_llm_provider(session: AsyncSession) -> LLMProvider:
    """Resolve the production LLM seam for this deployment (composition boundary).

    There is one broker, so there is one stored LLM configuration. A
    configuration that exists but cannot be used fails safely here, so the
    broker's traffic never silently moves onto the shared free pool; a broker
    with no active configuration gets the free pool.
    """
    try:
        configured_provider = await resolve_llm_provider(session=session)
    except BrokerLLMConfigurationError:
        raise HTTPException(status_code=503, detail="Agent service temporarily unavailable")
    return LLMRouter(
        configured_provider=configured_provider,
        free_pool=get_free_llm_pool(),
    )


# Per-user daily usage limiting is process-wide state by its documented nature
# (in-process counters, reset on restart): one limiter per process, lazily
# built from settings behind the same lock-guarded pattern as the MT5 session
# manager. The daily limit comes from configuration, never hard-coded.
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
# (in-process per-IP and per-login counters, reset on restart): one throttle
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

    # The deployment is bound to exactly ONE broker (the single brokers row), and
    # only that broker's users exist. A user row belonging to any other broker is refused
    # here — it is not a second customer, it is a row this deployment does not
    # serve, and it can never authenticate. The broker must also still be
    # active: login already checks that, but a token issued before the broker was
    # suspended must stop working immediately rather than at the next login. One
    # generic detail covers user, broker and role so the response never reveals
    # which condition caused it.
    broker = await load_deployment_broker(session)
    if broker is None or not broker.is_active or user.broker_id != broker.id:
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


# --- customer-scoped MT5 composition -----------------------------------------
#
# Everything below depends on the authenticated user, so it is declared after
# the authentication boundary. The customer's MT5 credentials are resolved once
# per request (FastAPI caches a dependency's result within a request) and shared
# by every MT5-backed service, so there is one broker read and no per-provider
# duplication. Each provider is a cheap per-request object; the process-wide
# session lives in the manager above and is the only thing that is shared.
async def get_mt5_credentials(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MT5AccountCredentials:
    """Resolve the authenticated customer's MT5 credentials.

    Both halves of the identity come from trusted data: the account number is
    the authenticated user's own ``login`` row and the MT5 server is the
    deployment broker's own configuration — never a request parameter. Because
    get_current_user() has already bound the caller to the one broker this
    deployment serves, the broker loaded here IS the caller's broker. The value
    stays encrypted at this point; decryption happens only inside the session
    boundary.
    """
    broker = await load_deployment_broker(session)
    return resolve_mt5_account_credentials(current_user, broker)


def get_market_data_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> MarketDataService:
    provider = MT5MarketDataProvider(session_manager=get_mt5_session_manager(), credentials=credentials)
    # The requested symbol is resolved through the SAME instrument service
    # GET /instruments uses (one resolution architecture, one credential path),
    # so a candle read asks the broker for a spelling it actually lists. The
    # candle provider itself is unchanged and still receives a plain symbol.
    return MarketDataService(provider, instrument_service=get_instrument_service(credentials))


def get_account_info_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> AccountInfoService:
    provider = MT5AccountInfoProvider(session_manager=get_mt5_session_manager(), credentials=credentials)
    return AccountInfoService(provider)


def get_position_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> PositionService:
    provider = MT5PositionProvider(session_manager=get_mt5_session_manager(), credentials=credentials)
    return PositionService(provider)


def get_trade_history_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> TradeHistoryService:
    provider = MT5TradeHistoryProvider(session_manager=get_mt5_session_manager(), credentials=credentials)
    return TradeHistoryService(provider)


def get_instrument_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> InstrumentService:
    # Same customer-scoped MT5 composition path as the other broker-data services:
    # the provider is a cheap per-request object holding the authenticated
    # customer's credentials, and provider selection is explicit (and therefore
    # replaceable in tests) rather than hidden behind a factory.
    provider = MT5InstrumentProvider(session_manager=get_mt5_session_manager(), credentials=credentials)
    return InstrumentService(provider)


def get_financial_research_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> FinancialResearchService:
    """Compose the graded research context from the news seam and the catalog.

    Declared below the authentication boundary because it now needs this
    customer's own MT5 identity: it reuses the news seam unchanged (same
    NEWS_SOURCE selection, same development-only posture, same failure
    behaviour) AND the same instrument service GET /instruments uses, so a
    caller-named instrument is resolved against the authenticated customer's own
    broker catalog before anything is researched — one resolution architecture,
    one credential path, no duplicate lookup.

    The research service itself still reads no positions and holds no customer
    identity, so it carries nothing customer-sensitive by construction.
    """
    return FinancialResearchService(
        news_service=get_news_service(),
        instrument_service=get_instrument_service(credentials),
    )


def get_economic_intelligence_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> EconomicIntelligenceService:
    # Reuses the existing positions composition-root path (get_position_service),
    # so there is exactly one position architecture; a session/authentication
    # failure surfaces as 503 from there, exactly as for GET /positions.
    return EconomicIntelligenceService(
        calendar_service=get_economic_calendar_service(),
        position_service=get_position_service(credentials),
    )


def get_portfolio_intelligence_service(
    credentials: MT5AccountCredentials = Depends(get_mt5_credentials),
) -> PortfolioIntelligenceService:
    # Reuses the existing account-info and positions composition-root paths, so
    # there is exactly one account architecture and one position architecture
    # (no providers are duplicated here); a session/authentication failure
    # surfaces as 503 from those paths, exactly as for GET /account-info and
    # GET /positions.
    return PortfolioIntelligenceService(
        account_service=get_account_info_service(credentials),
        position_service=get_position_service(credentials),
    )


async def get_agent_service(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AgentService:
    """Composition root for POST /agent.

    Declared after the authentication boundary because it depends on the
    authenticated user: the broker's own LLM configuration is resolved from
    that user's broker (never from the request body), and a broker with no
    active configuration uses the shared free pool. The financial context reads
    the *same* customer's MT5 session as every other endpoint, so AgentService
    depends on LLMProvider alone.
    """
    credentials = await get_mt5_credentials(current_user, session)
    llm_provider = await get_llm_provider(session)
    # Built once and shared by both consumers, so one request performs exactly
    # one calendar read and one position read for its calendar context.
    economic_intelligence = get_economic_intelligence_service(credentials)
    return AgentService(
        financial_context_service=FinancialContextService(
            account_service=get_account_info_service(credentials),
            position_service=get_position_service(credentials),
            trade_history_service=get_trade_history_service(credentials),
        ),
        llm_provider=llm_provider,
        # The outbound-data policy is applied to the prompt the provider gets,
        # so what may leave the process is explicit at the composition boundary.
        data_policy=get_outbound_data_policy(),
        # Step 45: today's economic intelligence is composed into the same
        # prompt through the EXISTING calendar/intelligence composition path
        # (the one GET /economic-intelligence/today uses), so customers see one
        # calendar architecture. That path fails closed outside development
        # (no calendar source may serve a broker's customers), and a provider
        # failure surfaces as the established generic 503.
        economic_intelligence_service=economic_intelligence,
        # Step 47: fundamental intelligence (news + relevance + position
        # exposure) is composed AROUND that same calendar context, so it adds no
        # calendar request and no MT5 read of its own. An explicitly selected but
        # unusable news source still fails closed here, exactly as it does for
        # GET /fundamental-intelligence/today.
        fundamental_intelligence_service=get_fundamental_intelligence_service(),
        # Step 49: the graded research context shares the news seam above (one
        # source selection, one failure behaviour) and is composed per request
        # ONLY when the request names a focus instrument, for the look-back span
        # immediately before the calendar window — so it adds news the fundamental
        # block does not already carry without fetching any window twice. Since
        # Step 51 it also resolves that focus instrument through this customer's own
        # broker catalog before researching it.
        financial_research_service=get_financial_research_service(credentials),
    )
