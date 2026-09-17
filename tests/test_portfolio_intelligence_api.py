"""Tests for GET /portfolio-intelligence (auth, tenant scope, contract, errors).

Require none of: real MT5, PostgreSQL, network, or real credentials. The get_db
dependency is overridden with a per-test file-based async SQLite database; the
REAL authentication dependency runs (real JWT decode + real database lookup).
The account-info and positions composition-root seams are patched with
deterministic fakes (the established lifecycle-test pattern), so no MT5 terminal
is needed. JWT config uses test-only values; async setup is driven with
asyncio.run.
"""
import asyncio
import threading
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.portfolio_intelligence_router import router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.account_info import AccountInfo
from app.providers.position import Position, PositionType

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
EURUSD_SELL = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=Decimal("1.00"),
    open_price=Decimal("1.0850"),
    current_price=Decimal("1.0820"),
    profit=Decimal("-30.00"),
)

PORTFOLIO_KEYS = {
    "broker_id",
    "as_of",
    "account_currency",
    "balance",
    "equity",
    "margin",
    "free_margin",
    "margin_level",
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
    """Fake MT5AccountInfoProvider installed at the composition-root seam."""
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
    """Fake MT5PositionProvider installed at the composition-root seam."""
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


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_account(monkeypatch):
    def _install(account: AccountInfo = ACCOUNT, error: Exception | None = None):
        cls, record = make_fake_account_provider_class(account, error)
        monkeypatch.setattr(deps, "MT5AccountInfoProvider", cls)
        return record

    yield _install
    # monkeypatch restores the real provider class after each test.


@pytest.fixture()
def patched_positions(monkeypatch):
    def _install(positions: tuple[Position, ...] = (), error: Exception | None = None):
        cls, record = make_fake_position_provider_class(positions, error)
        monkeypatch.setattr(deps, "MT5PositionProvider", cls)
        return record

    yield _install


@pytest.fixture()
def portfolio_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/portfolio.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="TP-ONE", mt5_server="TheBroker-Live")
            session.add(broker)
            await session.commit()
            # Two CUSTOMERS of the one broker (two MT5 accounts) and the
            # deployment's single super_admin.
            customer_a = User(
                broker_id=broker.id,
                login="10001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            customer_b = User(
                broker_id=broker.id,
                login="10002",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            super_admin = User(
                broker_id=broker.id,
                login="9001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            session.add_all([customer_a, customer_b, super_admin])
            await session.commit()
            return {
                "broker_id": broker.id,
                "customer_a_id": customer_a.id,
                "customer_b_id": customer_b.id,
                "super_admin_id": super_admin.id,
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


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def is_utc_iso(value: object) -> bool:
    """True for a timezone-aware UTC ISO 8601 value ("...Z" or "...+00:00")."""
    return str(value).endswith(("Z", "+00:00"))


def token_for(user_id: int) -> str:
    return create_access_token(str(user_id))


def get_portfolio(env, user_id: int, query: str = "") -> tuple[int, dict[str, object]]:
    client = env["make_app"]()
    with client as c:
        response = c.get(f"/portfolio-intelligence{query}", headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


# --- authentication / authorization ---------------------------------------------------


def test_unauthenticated_request_returns_401(portfolio_env, patched_account, patched_positions) -> None:
    patched_account()
    patched_positions(())

    with portfolio_env["make_app"]() as client:
        response = client.get("/portfolio-intelligence")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_authenticated_customer_receives_portfolio_intelligence(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY, EURUSD_SELL))

    status, body = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])

    assert status == 200
    assert set(body.keys()) == PORTFOLIO_KEYS
    assert isinstance(body["exposure"], list)


def test_response_structure_is_complete(portfolio_env, patched_account, patched_positions) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY, EURUSD_SELL))

    _, body = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])

    assert body["account_currency"] == "USD"
    assert body["balance"] == 10000.0
    assert body["equity"] == 10050.0
    assert body["margin"] == 250.0
    assert body["free_margin"] == 9800.0
    assert body["margin_level"] == 4020.0
    assert body["open_positions"] == 2
    assert body["buy_positions"] == 1
    assert body["sell_positions"] == 1
    assert body["symbols"] == ["EURUSD", "XAUUSD"]
    assert body["directional_balance"] == pytest.approx(-0.90)
    assert is_utc_iso(body["as_of"])

    for entry in body["exposure"]:
        assert set(entry.keys()) == EXPOSURE_KEYS
        assert entry["symbol"] in {"EURUSD", "XAUUSD"}

    assert set(body["risk"].keys()) == RISK_KEYS
    assert body["risk"]["level"] == "LOW"
    assert body["risk"]["basis"]


def test_no_positions_returns_flat_risk_and_empty_exposure(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions(())

    status, body = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])

    assert status == 200
    assert body["open_positions"] == 0
    assert body["exposure"] == []
    assert body["symbols"] == []
    assert body["directional_balance"] == 0
    assert body["risk"]["level"] == "FLAT"


# --- identity scope -------------------------------------------------------------------


def test_the_reported_broker_is_the_deployments_only_broker(
    portfolio_env, patched_account, patched_positions
) -> None:
    """Every customer reports the same broker — and their own account's numbers."""
    patched_account()
    patched_positions((XAUUSD_BUY,))

    _, body_a = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])
    _, body_b = get_portfolio(portfolio_env, portfolio_env["customer_b_id"])

    assert body_a["broker_id"] == portfolio_env["broker_id"]
    assert body_b["broker_id"] == portfolio_env["broker_id"]


def test_admin_roles_do_not_bypass_the_authenticated_identity(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY,))

    # A super_admin is still just an authenticated user here: the response is
    # composed for the deployment's broker, never for a broker it could name.
    _, body = get_portfolio(portfolio_env, portfolio_env["super_admin_id"])

    assert body["broker_id"] == portfolio_env["broker_id"]


def test_broker_id_and_user_id_query_parameters_cannot_change_scope(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY,))
    user_id = portfolio_env["customer_a_id"]

    _, plain = get_portfolio(portfolio_env, user_id)
    _, with_params = get_portfolio(portfolio_env, user_id, query="?broker_id=2&user_id=999")

    # No such parameters exist: only the per-request as_of stamp (legitimately
    # the current time) differs.
    def without_as_of(body: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in body.items() if key != "as_of"}

    assert without_as_of(with_params) == without_as_of(plain)
    assert with_params["broker_id"] == portfolio_env["broker_id"]


# --- error handling ---------------------------------------------------------------------


def test_account_provider_failure_returns_generic_503(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account(error=RuntimeError("terminal unavailable"))
    patched_positions((XAUUSD_BUY,))

    with portfolio_env["make_app"]() as client:
        response = client.get(
            "/portfolio-intelligence", headers=auth_header(token_for(portfolio_env["customer_a_id"]))
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Portfolio intelligence service temporarily unavailable"}


def test_position_provider_failure_returns_generic_503(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions(error=RuntimeError("open positions unavailable"))

    with portfolio_env["make_app"]() as client:
        response = client.get(
            "/portfolio-intelligence", headers=auth_header(token_for(portfolio_env["customer_a_id"]))
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Portfolio intelligence service temporarily unavailable"}


def test_mt5_session_failure_returns_503_and_is_not_cached(
    portfolio_env, patched_positions, monkeypatch
) -> None:
    # The real account provider runs (no provider-class fake): the seeded user
    # has no stored MT5 password, so the session boundary refuses and the failure
    # maps to a service-availability error — with no credentials disclosed.
    monkeypatch.setattr(deps, "_mt5_session_manager", MT5SessionManager(mt5_api=_FakeMT5()), raising=True)
    patched_positions(())

    with portfolio_env["make_app"]() as client:
        response = client.get(
            "/portfolio-intelligence", headers=auth_header(token_for(portfolio_env["customer_a_id"]))
        )

    assert response.status_code == 503
    assert deps.get_mt5_session_manager().authenticated_account is None  # nothing cached


# --- credential / secret safety ---------------------------------------------------------


def test_response_exposes_no_credentials(portfolio_env, patched_account, patched_positions) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY,))

    _, body = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])
    text = str(body)

    assert "password_hash" not in text
    assert "mt5_password_encrypted" not in text
    assert "$2b$" not in text
    assert "token" not in text
    assert "AccountInfo(" not in text  # NamedTuple repr must not leak


# --- blocking boundary -------------------------------------------------------------------


def test_blocking_reads_run_off_the_event_loop(portfolio_env, patched_account, patched_positions) -> None:
    account_threads = patched_account()
    position_threads = patched_positions((XAUUSD_BUY,))
    main_thread = threading.get_ident()

    status, _ = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])

    assert status == 200
    # Both blocking reads must leave the request (event-loop) thread.
    assert account_threads
    assert position_threads
    assert all(thread_id != main_thread for thread_id in account_threads)
    assert all(thread_id != main_thread for thread_id in position_threads)


# --- existing API wiring stays intact -------------------------------------------------------


def test_existing_endpoints_remain_registered() -> None:
    from app.main import app

    # The generated OpenAPI schema is the authoritative list of mounted routes.
    paths = set(app.openapi()["paths"])

    assert "/portfolio-intelligence" in paths
    # Regression guard: the pre-existing feature endpoints are still mounted.
    assert {
        "/positions",
        "/account-info",
        "/trade-history",
        "/market-data/{symbol}",
        "/users",
        "/economic-intelligence/today",
    } <= paths


def test_reference_timestamp_is_timezone_aware(portfolio_env, patched_account, patched_positions) -> None:
    patched_account()
    patched_positions(())

    _, body = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])

    # Pydantic serializes the UTC as_of; it must carry an explicit offset.
    assert is_utc_iso(body["as_of"])
    assert datetime.fromisoformat(str(body["as_of"]).replace("Z", "+00:00")).tzinfo is not None
