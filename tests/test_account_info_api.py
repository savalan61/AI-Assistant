"""Tests for the GET /account-info endpoint (Step 17).

Require none of: real MT5, PostgreSQL, network, or real credentials. Two seams
are used, following the established Stage-6 pattern: the provider class is
patched at the composition root (app.core.dependencies.MT5AccountInfoProvider)
with the singleton cache reset, and get_db is overridden with a per-test
file-based async SQLite database. The real service, router, and authentication
dependency all run. JWT config uses test-only values. No pytest asyncio
plugin: async setup is driven with asyncio.run.
"""
import asyncio
import threading
from typing import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.account_info_router import router
from app.core.blocking import run_mt5_call
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.account_info import AccountInfo

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

ACCOUNT_INFO = AccountInfo(
    login=10001,
    name="Demo Account",
    balance=10000.0,
    equity=10150.25,
    margin=250.0,
    free_margin=9900.25,
    margin_level=40601.0,
    currency="USD",
    server="MetaQuotes-Demo",
)


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


def make_fake_provider_class(info: AccountInfo = ACCOUNT_INFO, error: Exception | None = None):
    """Build a fake provider class at the composition-root seam.

    The composition root constructs the class with no arguments, so the
    configured result/error travel via closure; call counts and the thread ids
    the provider ran on are recorded (thread offload is asserted in tests).
    """
    record: dict[str, object] = {"calls": 0, "call_threads": []}

    class FakeAccountInfoProvider:
        def __init__(self) -> None:
            pass

        def get_account_info(self) -> AccountInfo:
            record["calls"] = int(record["calls"]) + 1  # type: ignore[call-overload]
            record["call_threads"] = [*record["call_threads"], threading.get_ident()]  # type: ignore[dict-item]
            if error is not None:
                raise error
            return info

    return FakeAccountInfoProvider, record


@pytest.fixture()
def patched_account_provider(monkeypatch):
    """Install a fake provider class into the composition root per test."""

    def _install(info: AccountInfo = ACCOUNT_INFO, error: Exception | None = None):
        cls, record = make_fake_provider_class(info, error)
        monkeypatch.setattr(deps, "MT5AccountInfoProvider", cls)
        deps._account_info_provider = None
        return record

    yield _install
    # Never leak a fake (or real) provider into other tests.
    deps._account_info_provider = None


@pytest.fixture()
def account_env(tmp_path):
    """Seed a customer and a broker admin; yield a client factory."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/account_info.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="Test Broker", code="TB-1")
            session.add(broker)
            await session.commit()
            customer = User(
                broker_id=broker.id,
                username="10001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            admin = User(
                broker_id=broker.id,
                username="20001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            session.add_all([customer, admin])
            await session.commit()
            return {"customer_id": customer.id, "admin_id": admin.id}

    ids = asyncio.run(seed())

    def make_app() -> TestClient:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    yield {"make_app": make_app, "ids": ids, "factory": factory}
    asyncio.run(engine.dispose())


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- authentication / protection -------------------------------------------------


def test_unauthenticated_request_returns_401(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    response = client.get("/account-info")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_for_nonexistent_user_returns_401(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get("/account-info", headers=auth_header(create_access_token("999999")))

    assert response.status_code == 401


def test_token_for_inactive_user_returns_401(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    # Flip the customer to inactive directly in the DB.
    async def deactivate() -> None:
        async with account_env["factory"]() as session:
            user = (
                await session.execute(select(User).where(User.id == account_env["ids"]["customer_id"]))
            ).scalar_one()
            user.is_active = False
            await session.commit()

    asyncio.run(deactivate())

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 401


def test_authenticated_customer_can_access_account_info(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 200
    assert response.json()["login"] == 10001


def test_authenticated_super_admin_can_access_account_info(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["admin_id"])))
        )

    assert response.status_code == 200
    assert response.json()["login"] == 10001


# --- response contract -------------------------------------------------------------


def test_response_contains_exactly_the_account_info_fields(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 200
    assert set(response.json().keys()) == {
        "login",
        "name",
        "balance",
        "equity",
        "margin",
        "free_margin",
        "margin_level",
        "currency",
        "server",
    }


def test_values_are_correctly_mapped_from_the_fake_provider(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        body = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        ).json()

    assert body == {
        "login": 10001,
        "name": "Demo Account",
        "balance": 10000.0,
        "equity": 10150.25,
        "margin": 250.0,
        "free_margin": 9900.25,
        "margin_level": 40601.0,
        "currency": "USD",
        "server": "MetaQuotes-Demo",
    }


def test_raw_provider_object_is_not_exposed(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    body = str(response.json())
    # No NamedTuple repr fragments, no MT5 attribute spellings beyond the contract.
    assert "AccountInfo(" not in body
    assert "margin_free" not in body  # raw MT5 field spelling must not leak
    assert "password" not in body and "token" not in body


# --- error mapping -----------------------------------------------------------------


def test_provider_runtime_error_maps_to_503(account_env, patched_account_provider):
    patched_account_provider(error=RuntimeError("MT5 account information unavailable"))
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 503
    # Generic detail: no MT5/provider internals in the response.
    assert response.json()["detail"] == "Account information service temporarily unavailable"


def test_provider_initialization_failure_maps_to_503(account_env, monkeypatch):
    # Initialization failure happens inside the composition root; simulate it
    # with a provider class whose constructor raises.
    def failing_init(self) -> None:
        raise RuntimeError("MT5 initialization failed: simulated")

    cls, _ = make_fake_provider_class()
    monkeypatch.setattr(cls, "__init__", failing_init)
    monkeypatch.setattr(deps, "MT5AccountInfoProvider", cls)
    deps._account_info_provider = None
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 503
    # The failed construction must not be cached: a later request retries.
    assert deps._account_info_provider is None
    deps._account_info_provider = None


def test_client_cannot_supply_broker_id_to_select_tenant(account_env, patched_account_provider):
    patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        response = c.get(
            "/account-info",
            params={"broker_id": 999},  # attempt tenant selection via query string
            headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"]))),
        )

    # Unknown query parameters are ignored by the handler: the response is
    # still the caller's own account snapshot, not another tenant's.
    assert response.status_code == 200
    assert response.json()["login"] == 10001


def test_service_delegates_to_provider_exactly_once(account_env, patched_account_provider):
    record = patched_account_provider()
    client = account_env["make_app"]()

    with client as c:
        c.get("/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"]))))

    assert record["calls"] == 1


# --- blocking boundary (Step 19: consolidation) --------------------------------------


def test_blocking_call_runs_off_the_event_loop_thread(account_env, patched_account_provider):
    record = patched_account_provider()
    loop_thread: dict[str, int] = {}

    async def capture_loop_thread() -> None:
        # Async dependencies run on the event loop: record its thread id
        # (same mechanism as the positions boundary test).
        loop_thread["id"] = threading.get_ident()

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(capture_loop_thread)])
    apply_overrides(account_env, app)
    client = TestClient(app)

    with client as c:
        response = c.get(
            "/account-info", headers=auth_header(create_access_token(str(account_env["ids"]["customer_id"])))
        )

    assert response.status_code == 200
    assert response.json()["login"] == 10001
    assert record["call_threads"], "provider was never called"
    # The provider must have run on a worker thread, never the event loop.
    assert all(t != loop_thread["id"] for t in record["call_threads"])  # type: ignore[union-attr]


def test_consolidated_boundary_is_the_single_mt5_execution_path():
    # Every MT5-touching router must go through run_mt5_call; a direct
    # starlette run_in_threadpool import in a router would signal a second,
    # unconsolidated boundary.
    import inspect

    from app.api import account_info_router, market_data_router, positions_router

    for module in (account_info_router, market_data_router, positions_router):
        source = inspect.getsource(module)
        assert "run_mt5_call" in source, f"{module.__name__} bypasses the consolidated boundary"
        assert "starlette.concurrency" not in source, f"{module.__name__} imports its own boundary"


def apply_overrides(account_env, target_app: FastAPI) -> None:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with account_env["factory"]() as session:
            yield session

    target_app.dependency_overrides[get_db] = override_get_db
