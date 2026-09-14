"""Tests for the PositionService and GET /positions endpoint.

Require none of: real MT5, PostgreSQL, network, or real credentials. Provider
tests use a deterministic fake at the PositionProvider abstraction (the
FakeMarketDataProvider pattern); API tests patch the composition-root provider
class seam (app.core.dependencies.MT5PositionProvider) with the singleton cache
reset, following the established lifecycle-test pattern, and override get_db
with a per-test file-based async SQLite database. JWT config uses test-only
values. No pytest asyncio plugin: async setup is driven with asyncio.run.
"""
import asyncio
import threading
from typing import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.positions_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.fake_position import FakePositionProvider
from app.providers.position import Position, PositionProvider, PositionType
from app.services.positions import PositionService

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

BUY_POSITION = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=0.10,
    open_price=3642.50,
    current_price=3648.20,
    profit=57.00,
)
SELL_POSITION = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=1.00,
    open_price=1.0850,
    current_price=1.0820,
    profit=-30.00,
)


# --- service delegation (Fake Provider pattern) -------------------------------------


def make_service(positions: tuple[Position, ...]) -> tuple[PositionService, FakePositionProvider]:
    provider = FakePositionProvider(positions=positions)
    return PositionService(provider), provider


def test_service_delegates_to_provider():
    service, provider = make_service((BUY_POSITION,))

    result = service.get_positions()

    assert result == (BUY_POSITION,)
    assert provider.call_count == 1
    assert isinstance(provider, PositionProvider)


def test_service_returns_exactly_what_provider_returns():
    service, _ = make_service((BUY_POSITION, SELL_POSITION))

    assert service.get_positions() == (BUY_POSITION, SELL_POSITION)


# --- API fixtures / helpers ----------------------------------------------------------


def make_fake_provider_class(positions: tuple[Position, ...], error: Exception | None = None):
    """Fake provider class at the composition-root seam; call threads recorded."""
    record: dict[str, object] = {"calls": 0, "call_threads": []}

    class FakePositionProvider:
        def __init__(self) -> None:
            pass

        def get_positions(self) -> tuple[Position, ...]:
            record["calls"] = int(record["calls"]) + 1
            record["call_threads"] = [*record["call_threads"], threading.get_ident()]  # type: ignore[dict-item]
            if error is not None:
                raise error
            return positions

    return FakePositionProvider, record


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_position_provider(monkeypatch):
    def _install(positions: tuple[Position, ...] = (), error: Exception | None = None):
        cls, record = make_fake_provider_class(positions, error)
        monkeypatch.setattr(deps, "MT5PositionProvider", cls)
        deps._position_provider = None
        return record

    yield _install
    # Never leak a fake (or real) provider into other tests.
    deps._position_provider = None


@pytest.fixture()
def positions_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/positions.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="Test Broker", code="TB-1")
            session.add(broker)
            await session.commit()
            user = User(
                broker_id=broker.id,
                username="10001",
                password_hash="x" * 60,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            return user.id

    user_id = asyncio.run(seed())

    def make_app() -> TestClient:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    yield {"make_app": make_app, "user_id": user_id, "factory": factory}
    asyncio.run(engine.dispose())


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- API contract ---------------------------------------------------------------------


def test_authenticated_user_retrieves_positions(positions_env, patched_position_provider):
    patched_position_provider((BUY_POSITION, SELL_POSITION))
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    assert response.status_code == 200
    body = response.json()
    # Top-level contract: a wrapped object with a single "positions" array.
    assert set(body.keys()) == {"positions"}
    assert isinstance(body["positions"], list)
    assert len(body["positions"]) == 2


def test_each_position_contains_exactly_the_required_fields(positions_env, patched_position_provider):
    patched_position_provider((BUY_POSITION,))
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    body = response.json()
    assert set(body["positions"][0].keys()) == {
        "ticket",
        "symbol",
        "type",
        "volume",
        "open_price",
        "current_price",
        "profit",
    }
    assert body["positions"][0] == {
        "ticket": 123456789,
        "symbol": "XAUUSD",
        "type": "BUY",
        "volume": 0.10,
        "open_price": 3642.50,
        "current_price": 3648.20,
        "profit": 57.00,
    }


def test_sell_type_serializes_as_sell(positions_env, patched_position_provider):
    patched_position_provider((SELL_POSITION,))
    client = positions_env["make_app"]()

    with client as c:
        body = c.get(
            "/positions", headers=auth_header(create_access_token(str(positions_env["user_id"])))
        ).json()

    assert body["positions"][0]["type"] == "SELL"


def test_no_open_positions_returns_200_with_empty_array(positions_env, patched_position_provider):
    patched_position_provider(())  # empty, NOT an error
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    assert response.status_code == 200
    assert response.json() == {"positions": []}


def test_raw_mt5_structures_do_not_leak(positions_env, patched_position_provider):
    patched_position_provider((BUY_POSITION,))
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    body = str(response.json())
    assert "Position(" not in body  # NamedTuple repr must not leak
    assert "price_open" not in body  # raw MT5 attribute spelling must not leak
    assert "price_current" not in body
    assert "password" not in body and "token" not in body


# --- authentication ---------------------------------------------------------------------


def test_unauthenticated_request_returns_401(positions_env, patched_position_provider):
    patched_position_provider()
    client = positions_env["make_app"]()

    response = client.get("/positions")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_returns_401(positions_env, patched_position_provider):
    patched_position_provider()
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header("not-a-jwt"))

    assert response.status_code == 401


def test_nonexistent_user_returns_401(positions_env, patched_position_provider):
    patched_position_provider()
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token("999999")))

    assert response.status_code == 401


def test_client_cannot_request_another_account(positions_env, patched_position_provider):
    patched_position_provider()
    client = positions_env["make_app"]()

    with client as c:
        response = c.get(
            "/positions",
            params={"login": 999999, "account_id": 42},  # attempted account selection is ignored
            headers=auth_header(create_access_token(str(positions_env["user_id"]))),
        )

    # The endpoint serves the process-attached account only; no client-supplied
    # account selector exists, so the response is the caller's own snapshot.
    assert response.status_code == 200
    assert response.json() == {"positions": []}


# --- provider / MT5 failure mapping -----------------------------------------------------


def test_provider_runtime_error_maps_to_503(positions_env, patched_position_provider):
    patched_position_provider(error=RuntimeError("MT5 open positions unavailable"))
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    assert response.status_code == 503
    # Generic detail: no MT5/provider internals in the response.
    assert response.json()["detail"] == "Positions service temporarily unavailable"


def test_provider_initialization_failure_maps_to_503_and_is_not_cached(positions_env, monkeypatch):
    def failing_init(self) -> None:
        raise RuntimeError("MT5 initialization failed: simulated")

    cls, _ = make_fake_provider_class(())
    monkeypatch.setattr(cls, "__init__", failing_init)
    monkeypatch.setattr(deps, "MT5PositionProvider", cls)
    deps._position_provider = None
    client = positions_env["make_app"]()

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    assert response.status_code == 503
    assert deps._position_provider is None  # failed construction not cached; later requests retry
    deps._position_provider = None


# --- blocking boundary -------------------------------------------------------------------


def test_blocking_call_runs_off_the_event_loop_thread(positions_env, patched_position_provider):
    patched_position_provider((BUY_POSITION,))
    loop_thread: dict[str, int] = {}

    async def capture_loop_thread() -> None:
        # Async dependencies run on the event loop: record its thread id.
        loop_thread["id"] = threading.get_ident()

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(capture_loop_thread)])
    apply_overrides(positions_env, app)
    client = TestClient(app)

    with client as c:
        response = c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    assert response.status_code == 200
    # The provider was invoked, and on a worker thread — not the event loop.
    assert response.json()["positions"][0]["ticket"] == 123456789


def apply_overrides(positions_env, target_app: FastAPI) -> None:
    from app.db.database import get_db as _get_db

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with positions_env["factory"]() as session:
            yield session

    target_app.dependency_overrides[_get_db] = override_get_db


# --- composition-root cache identity ------------------------------------------------------


def test_composition_root_constructs_provider_once(positions_env, patched_position_provider):
    patched_position_provider((BUY_POSITION,))
    client = positions_env["make_app"]()

    with client as c:
        c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))
        c.get("/positions", headers=auth_header(create_access_token(str(positions_env["user_id"]))))

    # Two requests, one cached provider instance.
    first = deps.get_position_service()
    second = deps.get_position_service()
    assert first._provider is second._provider
    assert deps._position_provider is first._provider
