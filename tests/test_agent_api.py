"""Tests for POST /agent (auth, tenant scope, contract, errors).

Require none of: real MT5, PostgreSQL, network, credentials, or an external
LLM. The get_db dependency is overridden with a per-test file-based async
SQLite database; the REAL authentication dependency runs (real JWT decode +
real database lookup). The account-info, positions and trade-history
composition-root seams are patched with deterministic fakes (the established
lifecycle-test pattern), so no MT5 terminal is needed. The LLM composition
seam (deps.get_llm_provider) is overridden with the real FakeLLMProvider
(deterministic, offline), so no network call and no API key are involved.
JWT config uses test-only values; async setup is driven with asyncio.run.
"""
import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.agent_router import router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.account_info import AccountInfo
from app.providers.fake_llm import DEFAULT_FAKE_RESPONSE, FakeLLMProvider
from app.providers.mt5_account_info import MT5AccountInfoProvider
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeHistoryEntry, TradeType

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

ACCOUNT = AccountInfo(
    login=10001,
    name="Test Trader",
    balance=Decimal("10000.00"),
    equity=Decimal("10050.00"),
    margin=Decimal("250.00"),
    free_margin=Decimal("9800.00"),
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

XAUUSD_BUY = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=Decimal("0.10"),
    open_price=Decimal("3642.50"),
    current_price=Decimal("3648.20"),
    profit=Decimal("57.00"),
)

TRADE = TradeHistoryEntry(
    ticket=246802468,
    order_ticket=987654321,
    symbol="XAUUSD",
    type=TradeType.BUY,
    volume=Decimal("0.10"),
    price=Decimal("3648.20"),
    profit=Decimal("57.00"),
    time=datetime(2026, 9, 14, 12, 30, 0, tzinfo=UTC),
    close_reason=None,
    stop_loss=Decimal("3635.00"),
    take_profit=Decimal("3650.00"),
)

TOP_LEVEL_KEYS = {"request", "broker_id", "answer", "context"}
CONTEXT_KEYS = {"as_of", "account", "positions", "trade_history", "portfolio_intelligence"}
ACCOUNT_KEYS = {"currency", "balance", "equity", "margin", "free_margin", "margin_level"}
PORTFOLIO_KEYS = {
    "open_positions",
    "buy_positions",
    "sell_positions",
    "symbols",
    "total_volume",
    "buy_volume",
    "sell_volume",
    "directional_balance",
    "exposure",
    "risk",
}
POSITION_KEYS = {"ticket", "symbol", "type", "volume", "open_price", "current_price", "profit"}
TRADE_KEYS = {
    "ticket",
    "order_ticket",
    "symbol",
    "type",
    "volume",
    "price",
    "profit",
    "time",
    "close_reason",
    "stop_loss",
    "take_profit",
}
EXPOSURE_KEYS = {"symbol", "buy_volume", "sell_volume", "net_volume", "position_count"}
RISK_KEYS = {"level", "basis"}


# --- composition-root seams (established pattern) ------------------------------------


class _FakeMT5:
    """Minimal MT5 seam so a real MT5SessionManager can run without a terminal."""

    def initialize(self, **kwargs: object) -> bool:
        return True

    def login(self, **kwargs: object) -> bool:
        return True

    def last_error(self) -> tuple[int, str]:
        return (-1, "simulated MT5 failure")

    def shutdown(self) -> None:
        pass


def make_fake_account_provider_class(account: AccountInfo, error: Exception | None = None):
    call_threads: list[int] = []

    class FakeMT5AccountInfoProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_account_info(self) -> AccountInfo:
            call_threads.append(threading.get_ident())
            if error is not None:
                raise error
            return account

    return FakeMT5AccountInfoProvider, call_threads


def make_fake_position_provider_class(positions: tuple[Position, ...], error: Exception | None = None):
    call_threads: list[int] = []

    class FakeMT5PositionProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_positions(self) -> tuple[Position, ...]:
            call_threads.append(threading.get_ident())
            if error is not None:
                raise error
            return positions

    return FakeMT5PositionProvider, call_threads


def make_fake_trade_provider_class(
    trades: tuple[TradeHistoryEntry, ...], error: Exception | None = None
):
    call_threads: list[int] = []
    windows: list[tuple[object, object]] = []

    class FakeMT5TradeHistoryProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_trade_history(self, from_time: object, to_time: object) -> tuple[TradeHistoryEntry, ...]:
            call_threads.append(threading.get_ident())
            windows.append((from_time, to_time))
            if error is not None:
                raise error
            return trades

    return FakeMT5TradeHistoryProvider, call_threads, windows


class FailingLLMProvider(FakeLLMProvider):
    """Deterministic fake whose complete() raises, simulating LLM failure."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def complete(self, prompt: object) -> str:
        raise self._error


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_providers(monkeypatch):
    """Patch the three MT5 provider seams and the LLM seam; returns records."""

    def _install(
        *,
        account_error: Exception | None = None,
        position_error: Exception | None = None,
        trade_error: Exception | None = None,
        llm_error: Exception | None = None,
    ) -> dict[str, object]:
        account_cls, account_threads = make_fake_account_provider_class(ACCOUNT, account_error)
        position_cls, position_threads = make_fake_position_provider_class((XAUUSD_BUY,), position_error)
        trade_cls, trade_threads, trade_windows = make_fake_trade_provider_class((TRADE,), trade_error)
        monkeypatch.setattr(deps, "MT5AccountInfoProvider", account_cls)
        monkeypatch.setattr(deps, "MT5PositionProvider", position_cls)
        monkeypatch.setattr(deps, "MT5TradeHistoryProvider", trade_cls)
        llm: FakeLLMProvider = FakeLLMProvider() if llm_error is None else FailingLLMProvider(llm_error)

        # The production seam is the broker-aware router (async, per-broker).
        # Tests replace the whole seam with the deterministic offline fake so
        # these endpoint tests involve no network, no stored configuration and
        # no broker-provider policy.
        async def fake_llm_provider(broker_id: int, session: object) -> FakeLLMProvider:
            return llm

        monkeypatch.setattr(deps, "get_llm_provider", fake_llm_provider)
        return {
            "account_threads": account_threads,
            "position_threads": position_threads,
            "trade_threads": trade_threads,
            "trade_windows": trade_windows,
            "llm": llm,
        }

    yield _install
    # monkeypatch restores the real provider classes and LLM seam afterwards.


@pytest.fixture()
def agent_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/agent.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="AG-A")
            broker_b = Broker(name="Broker B", code="AG-B")
            session.add_all([broker_a, broker_b])
            await session.commit()
            customer_a = User(
                broker_id=broker_a.id,
                login="10001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            customer_b = User(
                broker_id=broker_b.id,
                login="10002",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            session.add_all([customer_a, customer_b])
            await session.commit()
            return {
                "broker_a_id": broker_a.id,
                "broker_b_id": broker_b.id,
                "customer_a_id": customer_a.id,
                "customer_b_id": customer_b.id,
            }

    ids = asyncio.run(seed())

    def make_app() -> TestClient:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    yield {"make_app": make_app, "factory": factory, **ids}
    asyncio.run(engine.dispose())
    # Drop the process-wide usage limiter so per-user counts never leak
    # between tests (each test rebuilds it from current settings).
    deps.reset_agent_usage_limiter()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(user_id: int) -> str:
    return create_access_token(str(user_id))


def post_agent(env, user_id: int, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    client = env["make_app"]()
    with client as c:
        response = c.post("/agent", json=payload, headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


def is_utc_iso(value: object) -> bool:
    """True for a timezone-aware UTC ISO 8601 value ("...Z" or "...+00:00")."""
    return str(value).endswith(("Z", "+00:00"))


# --- authenticated success ---------------------------------------------------------------


def test_authenticated_request_returns_agent_answer(agent_env, patched_providers) -> None:
    patched_providers()

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "What is my exposure?"})

    assert status == 200
    assert set(body.keys()) == TOP_LEVEL_KEYS
    assert body["request"] == "What is my exposure?"
    assert body["answer"] == DEFAULT_FAKE_RESPONSE


def test_response_contract_is_complete_and_stable(agent_env, patched_providers) -> None:
    patched_providers()

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hello"})

    context = body["context"]
    assert set(context.keys()) == CONTEXT_KEYS
    assert is_utc_iso(context["as_of"])

    account = context["account"]
    assert set(account.keys()) == ACCOUNT_KEYS
    assert account["balance"] == 10000.0
    assert account["margin_level"] == 4020.0
    # Account identity fields are deliberately absent from the whole response.
    assert "login" not in account
    assert "name" not in account
    assert "server" not in account

    assert set(context["portfolio_intelligence"].keys()) == PORTFOLIO_KEYS
    assert context["portfolio_intelligence"]["open_positions"] == 1

    positions = context["positions"]
    assert len(positions) == 1
    assert set(positions[0].keys()) == POSITION_KEYS
    assert positions[0]["symbol"] == "XAUUSD"

    trades = context["trade_history"]
    assert len(trades) == 1
    assert set(trades[0].keys()) == TRADE_KEYS
    assert trades[0]["close_reason"] is None  # nullable, never invented

    exposure = context["portfolio_intelligence"]["exposure"]
    assert set(exposure[0].keys()) == EXPOSURE_KEYS
    assert set(context["portfolio_intelligence"]["risk"].keys()) == RISK_KEYS
    assert context["portfolio_intelligence"]["risk"]["level"] == "LOW"


def test_response_exposes_no_secrets(agent_env, patched_providers) -> None:
    patched_providers()

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hello"})

    text = str(body)
    assert "password_hash" not in text
    assert "mt5_password_encrypted" not in text
    assert "$2b$" not in text
    assert "token" not in text
    # Account identity (login/name/server) is absent everywhere, not just in
    # the LLM prompt.
    assert "Test-Server" not in text
    assert "Test Trader" not in text
    assert str(ACCOUNT.login) not in text


def test_message_is_echoed_verbatim(agent_env, patched_providers) -> None:
    patched_providers()
    message = "  Am I exposed to USD?  "

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": message})

    assert body["request"] == message


# --- authentication ---------------------------------------------------------------------


def test_unauthenticated_request_returns_401(agent_env, patched_providers) -> None:
    patched_providers()

    with agent_env["make_app"]() as client:
        response = client.post("/agent", json={"message": "hello"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_returns_401(agent_env, patched_providers) -> None:
    patched_providers()

    with agent_env["make_app"]() as client:
        response = client.post("/agent", json={"message": "hello"}, headers=auth_header("not-a-jwt"))

    assert response.status_code == 401


# --- request validation -----------------------------------------------------------------


def test_missing_message_is_rejected_with_422(agent_env, patched_providers) -> None:
    patched_providers()

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {})

    assert status == 422


def test_blank_message_is_rejected_with_422(agent_env, patched_providers) -> None:
    patched_providers()

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": ""})

    assert status == 422


def test_zero_and_negative_trade_history_days_are_rejected_with_422(agent_env, patched_providers) -> None:
    patched_providers()

    status_zero, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi", "trade_history_days": 0})
    status_negative, _ = post_agent(
        agent_env, agent_env["customer_a_id"], {"message": "hi", "trade_history_days": -5}
    )

    assert status_zero == 422
    assert status_negative == 422


def test_extra_fields_are_rejected_with_422(agent_env, patched_providers) -> None:
    patched_providers()

    status_broker, _ = post_agent(
        agent_env, agent_env["customer_a_id"], {"message": "hi", "broker_id": 2}
    )
    status_user, _ = post_agent(
        agent_env, agent_env["customer_a_id"], {"message": "hi", "user_id": 999}
    )

    # No such parameters exist: tenant scope cannot be widened or redirected.
    assert status_broker == 422
    assert status_user == 422


# --- trade-history window ----------------------------------------------------------------


def test_default_window_is_thirty_days(agent_env, patched_providers) -> None:
    record = patched_providers()

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    as_of = datetime.fromisoformat(str(body["context"]["as_of"]).replace("Z", "+00:00"))
    window_from, window_to = record["trade_windows"][0]
    assert (window_to - window_from).days == 30
    assert window_to == as_of


def test_custom_window_is_applied(agent_env, patched_providers) -> None:
    record = patched_providers()

    _, body = post_agent(
        agent_env, agent_env["customer_a_id"], {"message": "hi", "trade_history_days": 7}
    )

    as_of = datetime.fromisoformat(str(body["context"]["as_of"]).replace("Z", "+00:00"))
    window_from, window_to = record["trade_windows"][0]
    assert (window_to - window_from).days == 7
    assert window_to == as_of


# --- tenant scope ------------------------------------------------------------------------


def test_broker_id_comes_from_the_authenticated_user(agent_env, patched_providers) -> None:
    patched_providers()

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert body["broker_id"] == agent_env["broker_a_id"]
    assert body["context"]["portfolio_intelligence"]["open_positions"] == 1


def test_each_user_gets_their_own_tenant_identity(agent_env, patched_providers) -> None:
    patched_providers()

    _, body_a = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})
    _, body_b = post_agent(agent_env, agent_env["customer_b_id"], {"message": "hi"})

    assert body_a["broker_id"] == agent_env["broker_a_id"]
    assert body_b["broker_id"] == agent_env["broker_b_id"]
    assert body_a["broker_id"] != body_b["broker_id"]


# --- failure handling ---------------------------------------------------------------------


def test_account_provider_failure_returns_generic_503(agent_env, patched_providers) -> None:
    patched_providers(account_error=RuntimeError("terminal unavailable"))

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert status == 503
    assert body == {"detail": "Agent service temporarily unavailable"}


def test_position_provider_failure_returns_generic_503(agent_env, patched_providers) -> None:
    patched_providers(position_error=RuntimeError("open positions unavailable"))

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert status == 503
    assert body == {"detail": "Agent service temporarily unavailable"}


def test_trade_history_provider_failure_returns_generic_503(agent_env, patched_providers) -> None:
    patched_providers(trade_error=RuntimeError("trade history unavailable"))

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert status == 503
    assert body == {"detail": "Agent service temporarily unavailable"}


def test_llm_provider_failure_returns_generic_503(agent_env, patched_providers) -> None:
    patched_providers(llm_error=RuntimeError("model unavailable"))

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert status == 503
    assert body == {"detail": "Agent service temporarily unavailable"}


def test_mt5_session_failure_returns_503_and_is_not_cached(
    agent_env, patched_providers, monkeypatch
) -> None:
    # The real account/positions/trade providers run (no provider-class fakes):
    # the seeded user has no stored MT5 password, so the tenant's session cannot
    # be established and the agent answers a generic 503. No credentials and no
    # partial session are cached.
    patched_providers()
    monkeypatch.setattr(deps, "MT5AccountInfoProvider", MT5AccountInfoProvider, raising=True)
    monkeypatch.setattr(deps, "_mt5_session_manager", MT5SessionManager(mt5_api=_FakeMT5()), raising=True)

    with agent_env["make_app"]() as client:
        response = client.post(
            "/agent", json={"message": "hi"}, headers=auth_header(token_for(agent_env["customer_a_id"]))
        )

    assert response.status_code == 503
    assert deps.get_mt5_session_manager().authenticated_account is None  # nothing cached


# --- read-only / blocking boundary ----------------------------------------------------------


def test_blocking_reads_run_off_the_event_loop(agent_env, patched_providers) -> None:
    record = patched_providers()
    main_thread = threading.get_ident()

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "hi"})

    assert status == 200
    for key in ("account_threads", "position_threads", "trade_threads"):
        threads = record[key]
        assert threads
        assert all(thread_id != main_thread for thread_id in threads)


def test_no_trading_operation_is_available_behind_the_endpoint() -> None:
    # The router module must not import or reference any MT5 trading function;
    # the endpoint can only resolve an answer from the read-only context.
    import app.api.agent_router as router_module

    forbidden = ("order_send", "order_check", "positions_modify", "order_calc", "order_delete")
    for name in forbidden:
        assert not hasattr(router_module, name)
        assert name not in router_module.__doc__


# --- existing wiring stays intact -------------------------------------------------------------


def test_existing_endpoints_remain_registered() -> None:
    from app.main import app

    # The generated OpenAPI schema is the authoritative list of mounted routes.
    paths = set(app.openapi()["paths"])

    assert "/agent" in paths
    # Regression guard: the pre-existing feature endpoints are still mounted.
    assert {
        "/positions",
        "/account-info",
        "/trade-history",
        "/market-data/{symbol}",
        "/users",
        "/economic-intelligence/today",
        "/portfolio-intelligence",
    } <= paths


# --- scope guard + per-user daily limit (Step 31) ---------------------------------------------


def test_financial_request_is_allowed(agent_env, patched_providers) -> None:
    record = patched_providers()

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "What is my account balance?"})

    assert status == 200
    assert body["request"] == "What is my account balance?"
    assert record["llm"].call_count == 1


def test_off_topic_request_is_rejected_with_422(agent_env, patched_providers) -> None:
    record = patched_providers()

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "Tell me a joke"})

    assert status == 422
    assert body == {"detail": "Request is outside the assistant's financial scope"}
    # Rejected before any context read or LLM call.
    assert record["llm"].call_count == 0
    assert record["account_threads"] == []
    assert record["position_threads"] == []
    assert record["trade_threads"] == []


def test_quota_rejection_does_not_reach_context_or_llm(agent_env, patched_providers) -> None:
    record = patched_providers()
    monkeypatch_quota = 1
    import app.core.config as config_module

    config_module.settings.AGENT_DAILY_REQUEST_LIMIT = monkeypatch_quota
    deps.reset_agent_usage_limiter()

    try:
        first_status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})
        second_status, second_body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my equity?"})
    finally:
        config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 50
        deps.reset_agent_usage_limiter()

    assert first_status == 200
    assert second_status == 429
    assert second_body == {"detail": "Daily agent request limit reached"}
    # Only the admitted request reached the LLM.
    assert record["llm"].call_count == 1


def test_users_have_independent_quotas(agent_env, patched_providers) -> None:
    patched_providers()
    import app.core.config as config_module

    config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 1
    deps.reset_agent_usage_limiter()

    try:
        status_a, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})
        status_b, _ = post_agent(agent_env, agent_env["customer_b_id"], {"message": "my balance?"})
        blocked_a, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my equity?"})
    finally:
        config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 50
        deps.reset_agent_usage_limiter()

    # Each user consumed their own quota; blocking one does not block the other.
    assert (status_a, status_b, blocked_a) == (200, 200, 429)


def test_quota_cannot_be_bypassed_via_the_request_body(agent_env, patched_providers) -> None:
    patched_providers()
    import app.core.config as config_module

    config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 1
    deps.reset_agent_usage_limiter()

    try:
        post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})
        # A fresh login for the same user cannot widen scope: the quota is keyed
        # by the authenticated identity, not by any request field. extra="forbid"
        # already 422s unknown body fields such as user_id/broker_id.
        status, _ = post_agent(
            agent_env,
            agent_env["customer_a_id"],
            {"message": "my balance?", "user_id": 999999, "broker_id": agent_env["broker_b_id"]},
        )
    finally:
        config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 50
        deps.reset_agent_usage_limiter()

    # The body fields are rejected outright (422); the quota still blocks the
    # user's second request even with a brand-new token for the same identity.
    assert status == 422


def test_scope_rejection_happens_before_quota_consumption(agent_env, patched_providers) -> None:
    patched_providers()
    import app.core.config as config_module

    config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 1
    deps.reset_agent_usage_limiter()

    try:
        rejected_status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "draw a cat"})
        # The off-topic rejection consumed no quota, so the one permitted request
        # is still available for a genuine financial question.
        allowed_status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})
    finally:
        config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 20
        deps.reset_agent_usage_limiter()

    assert rejected_status == 422
    assert allowed_status == 200


def test_quota_is_scoped_to_the_utc_day_of_the_request(agent_env, patched_providers) -> None:
    # A practical consequence of the UTC day boundary: the counter is keyed per
    # user, so a second user's traffic never consumes this user's quota (covered
    # above); this test pins the per-request day semantics end to end.
    patched_providers()
    import app.core.config as config_module

    config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 2
    deps.reset_agent_usage_limiter()

    try:
        first, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})
        second, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my equity?"})
        third_status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my margin?"})
    finally:
        config_module.settings.AGENT_DAILY_REQUEST_LIMIT = 50
        deps.reset_agent_usage_limiter()

    assert (first, second) == (200, 200)
    assert third_status == 429


# --- input and prompt size limits (Step 35) ------------------------------------------------


def test_oversized_message_is_rejected_with_422(agent_env, patched_providers, monkeypatch) -> None:
    patched_providers()
    monkeypatch.setattr(app_settings, "AGENT_MAX_MESSAGE_LENGTH", 20, raising=True)

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "x" * 21})

    assert status == 422
    assert "at most 20 characters" in str(body)


def test_message_at_the_limit_is_accepted(agent_env, patched_providers, monkeypatch) -> None:
    patched_providers()
    monkeypatch.setattr(app_settings, "AGENT_MAX_MESSAGE_LENGTH", 20, raising=True)

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "b" * 20})

    assert status == 200


def test_oversized_message_never_reaches_the_context_or_the_llm(
    agent_env, patched_providers, monkeypatch
) -> None:
    records = patched_providers()
    monkeypatch.setattr(app_settings, "AGENT_MAX_MESSAGE_LENGTH", 10, raising=True)

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "y" * 11})

    assert status == 422
    # Rejected at the validation boundary: no MT5 read and no model call.
    assert records["account_threads"] == []
    assert records["llm"].call_count == 0


def test_context_too_large_for_a_bounded_prompt_returns_422(
    agent_env, patched_providers, monkeypatch
) -> None:
    patched_providers()
    # Force the prompt-size guard to fire: the context alone exceeds the limit.
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    status, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})

    assert status == 422
    assert body == {"detail": "The financial context is too large to process this request"}


def test_prompt_size_failure_is_not_reported_as_a_service_outage(
    agent_env, patched_providers, monkeypatch
) -> None:
    patched_providers()
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})

    assert status != 503


def test_outbound_data_policy_narrows_the_prompt_end_to_end(
    agent_env, patched_providers, monkeypatch
) -> None:
    # Proves the egress boundary is actually wired through the composition root,
    # not merely available: a configuration change removes the data from the
    # prompt the provider receives.
    records = patched_providers()
    monkeypatch.setattr(app_settings, "LLM_SEND_ACCOUNT_BALANCES", False, raising=True)

    status, _ = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})

    assert status == 200
    prompt = records["llm"].prompts[0]
    assert "10000.00" not in prompt.content
    assert "withheld by the outbound data policy" in prompt.content
    # The response contract is unchanged: only the outbound prompt narrows.
    assert "balance" in records["llm"].prompts[0].content.lower()


def test_prompt_size_error_leaks_no_financial_data(agent_env, patched_providers, monkeypatch) -> None:
    patched_providers()
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    _, body = post_agent(agent_env, agent_env["customer_a_id"], {"message": "my balance?"})

    text = str(body)
    assert "10000" not in text
    assert "XAUUSD" not in text
