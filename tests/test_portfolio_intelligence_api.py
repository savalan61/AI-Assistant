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
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.portfolio_intelligence_router import router
from app.core.config import settings as app_settings
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
    balance=10000.0,
    equity=10050.0,
    margin=250.0,
    free_margin=9800.0,
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

XAUUSD_BUY = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=0.10,
    open_price=3642.50,
    current_price=3648.20,
    profit=57.00,
)
EURUSD_SELL = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=1.00,
    open_price=1.0850,
    current_price=1.0820,
    profit=-30.00,
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


def make_fake_account_provider_class(account: AccountInfo, error: Exception | None = None):
    """Fake MT5AccountInfoProvider installed at the composition-root seam."""
    call_threads: list[int] = []

    class FakeMT5AccountInfoProvider:
        def __init__(self) -> None:
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
        def __init__(self) -> None:
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
        deps._account_info_provider = None
        return record

    yield _install
    # Never leak a fake (or real) provider into other tests.
    deps._account_info_provider = None


@pytest.fixture()
def patched_positions(monkeypatch):
    def _install(positions: tuple[Position, ...] = (), error: Exception | None = None):
        cls, record = make_fake_position_provider_class(positions, error)
        monkeypatch.setattr(deps, "MT5PositionProvider", cls)
        deps._position_provider = None
        return record

    yield _install
    deps._position_provider = None


@pytest.fixture()
def portfolio_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/portfolio.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TP-A")
            broker_b = Broker(name="Broker B", code="TP-B")
            session.add_all([broker_a, broker_b])
            await session.commit()
            customer_a = User(
                broker_id=broker_a.id,
                username="10001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            customer_b = User(
                broker_id=broker_b.id,
                username="10002",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            super_b = User(
                broker_id=broker_b.id,
                username="super-b",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            session.add_all([customer_a, customer_b, super_b])
            await session.commit()
            return {
                "broker_a_id": broker_a.id,
                "broker_b_id": broker_b.id,
                "customer_a_id": customer_a.id,
                "customer_b_id": customer_b.id,
                "super_b_id": super_b.id,
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


# --- tenant scope ---------------------------------------------------------------------


def test_tenant_identity_comes_from_the_authenticated_user(
    portfolio_env, patched_account, patched_positions
) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY,))

    _, body_a = get_portfolio(portfolio_env, portfolio_env["customer_a_id"])
    _, body_b = get_portfolio(portfolio_env, portfolio_env["customer_b_id"])

    assert body_a["broker_id"] == portfolio_env["broker_a_id"]
    assert body_b["broker_id"] == portfolio_env["broker_b_id"]
    assert body_a["broker_id"] != body_b["broker_id"]


def test_admin_roles_do_not_bypass_tenant_identity(portfolio_env, patched_account, patched_positions) -> None:
    patched_account()
    patched_positions((XAUUSD_BUY,))

    # A super_admin authenticated against broker B is still scoped to broker B.
    _, body = get_portfolio(portfolio_env, portfolio_env["super_b_id"])

    assert body["broker_id"] == portfolio_env["broker_b_id"]


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
    assert with_params["broker_id"] == portfolio_env["broker_a_id"]


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


def test_provider_initialization_failure_returns_503_and_is_not_cached(
    portfolio_env, patched_positions, monkeypatch
) -> None:
    def failing_init(self) -> None:
        raise RuntimeError("MT5 initialization failed: simulated")

    cls, _ = make_fake_account_provider_class(ACCOUNT)
    monkeypatch.setattr(cls, "__init__", failing_init)
    monkeypatch.setattr(deps, "MT5AccountInfoProvider", cls)
    deps._account_info_provider = None
    patched_positions(())

    with portfolio_env["make_app"]() as client:
        response = client.get(
            "/portfolio-intelligence", headers=auth_header(token_for(portfolio_env["customer_a_id"]))
        )

    assert response.status_code == 503
    assert deps._account_info_provider is None  # failed construction not cached; later requests retry
    deps._account_info_provider = None


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
