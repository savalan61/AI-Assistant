"""Stage 6 lifecycle tests: singleton provider, shutdown, lifespan, threadpool boundary.

All tests patch the composition-root seam (app.core.dependencies.MT5MarketDataProvider),
so none of them require MT5, PostgreSQL, network, credentials, or .env.
"""
import asyncio
import threading
from datetime import datetime
from typing import AsyncIterator

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
import app.main
from app.api.market_data_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.market_data import Candle, MarketDataProvider

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

CANDLE = Candle(
    timestamp=datetime(2024, 1, 15, 12, 0, 0),
    open=100.0,
    high=110.0,
    low=95.0,
    close=105.0,
    volume=1234.0,
)


class _Recording:
    """Shared record of provider construction, calls, and shutdowns."""

    def __init__(self):
        self.instances = []
        self.shutdown_calls = 0
        self.call_threads = []
        self.symbols = []


def make_recording_provider_class():
    record = _Recording()

    class RecordingProvider(MarketDataProvider):
        def __init__(self):
            record.instances.append(self)

        def get_market_data(self, symbol: str) -> Candle:
            record.symbols.append(symbol)
            record.call_threads.append(threading.get_ident())
            return CANDLE

        def shutdown(self) -> None:
            record.shutdown_calls += 1

    return RecordingProvider, record


@pytest.fixture()
def patched_provider(monkeypatch):
    cls, record = make_recording_provider_class()
    monkeypatch.setattr(deps, "MT5MarketDataProvider", cls)
    deps._provider = None
    yield cls, record
    deps._provider = None  # never leak a recording provider into other tests


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    """Test-only JWT config plus a seeded user for authenticated HTTP probes.

    The market-data route requires authentication since Step 11; these probes
    override get_db with a per-test SQLite database and mint a real token.
    """
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/lifecycle_auth.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="Test Broker", code="TB-1")
            session.add(broker)
            await session.commit()
            user = User(broker_id=broker.id, username="10001", password_hash="x" * 60, is_active=True)
            session.add(user)
            await session.commit()
            return user.id

    user_id = asyncio.run(seed())

    def apply_overrides(target_app) -> None:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        target_app.dependency_overrides[get_db] = override_get_db

    # app.main.app is global: its override must be cleaned up after the test.
    apply_overrides(app.main.app)
    token = create_access_token(str(user_id))
    yield {"token": token, "apply_overrides": apply_overrides}
    app.main.app.dependency_overrides.pop(get_db, None)
    asyncio.run(engine.dispose())


# --- singleton provider identity / construction once -----------------------


def test_provider_constructed_once_and_service_reuses_instance(patched_provider):
    _, record = patched_provider

    first = deps.get_market_data_service()
    second = deps.get_market_data_service()

    assert len(record.instances) == 1
    assert first is not second
    assert first._provider is second._provider


def test_provider_constructed_exactly_once_under_concurrency(patched_provider):
    from concurrent.futures import ThreadPoolExecutor

    _, record = patched_provider

    with ThreadPoolExecutor(max_workers=8) as pool:
        services = list(pool.map(lambda _: deps.get_market_data_service(), range(16)))

    assert len(record.instances) == 1
    assert all(s._provider is record.instances[0] for s in services)


# --- failed initialization is not cached -----------------------------------


def test_failed_initialization_is_not_cached(patched_provider, monkeypatch):
    cls, record = patched_provider

    def failing_init(self):
        raise RuntimeError("MT5 initialization failed: simulated")

    monkeypatch.setattr(cls, "__init__", failing_init)
    with pytest.raises(HTTPException) as exc_info:
        deps.get_market_data_service()
    assert exc_info.value.status_code == 503
    assert record.instances == []  # construction aborted, nothing recorded
    assert deps._provider is None  # failure was not cached

    # Retry with a succeeding init that still records the instance.
    monkeypatch.setattr(cls, "__init__", lambda self: record.instances.append(self))
    service = deps.get_market_data_service()
    assert len(record.instances) == 1  # retried, then cached
    assert deps._provider is service._provider


# --- lifespan startup/shutdown ----------------------------------------------


def test_lifespan_warms_provider_and_shuts_it_down_once(patched_provider):
    _, record = patched_provider

    with TestClient(app.main.app) as client:
        assert client.get("/health").status_code == 200
        assert len(record.instances) == 1  # warmed at startup, not per request
        assert client.get("/health").status_code == 200
        assert len(record.instances) == 1  # still the same warm instance

    assert record.shutdown_calls == 1  # exactly once at shutdown
    assert deps._provider is None


def test_startup_failure_does_not_prevent_boot(patched_provider, monkeypatch):
    cls, record = patched_provider

    def failing_init(self):
        raise RuntimeError("MT5 initialization failed: simulated")

    monkeypatch.setattr(cls, "__init__", failing_init)
    with TestClient(app.main.app) as client:
        assert client.get("/health").status_code == 200  # boot survived

    assert record.instances == []
    assert record.shutdown_calls == 0  # nothing was cached, shutdown is a no-op
    assert deps._provider is None


# --- post-shutdown behavior --------------------------------------------------


def test_shutdown_clears_cache_and_next_request_gets_fresh_provider(patched_provider):
    _, record = patched_provider

    first = deps.get_market_data_service()
    deps.shutdown_market_data()

    assert record.shutdown_calls == 1
    assert deps._provider is None

    second = deps.get_market_data_service()
    assert second._provider is not first._provider
    assert len(record.instances) == 2


def test_request_after_shutdown_maps_to_503(patched_provider, monkeypatch, auth_env):
    cls, record = patched_provider
    deps.get_market_data_service()
    deps.shutdown_market_data()
    fresh = deps.get_market_data_service()  # retry constructs a fresh instance

    def dead(self, symbol):
        raise RuntimeError("MT5 market data request failed: terminal released")

    monkeypatch.setattr(cls, "get_market_data", dead)
    client = TestClient(app.main.app)
    # The route requires authentication: send a valid token so the request
    # reaches the (dead) provider and still maps to 503.
    response = client.get("/market-data/EURUSD", headers={"Authorization": f"Bearer {auth_env['token']}"})

    assert response.status_code == 503


# --- blocking call is explicitly offloaded to the threadpool -----------------


def test_blocking_call_runs_off_the_event_loop_thread(patched_provider, auth_env):
    _, record = patched_provider
    loop_thread = {}

    async def capture_loop_thread():
        # Async dependencies run on the event loop: record its thread id.
        loop_thread["id"] = threading.get_ident()

    probe_app = FastAPI()
    probe_app.include_router(router, dependencies=[Depends(capture_loop_thread)])
    auth_env["apply_overrides"](probe_app)  # authenticated route needs a DB-backed user

    client = TestClient(probe_app)
    response = client.get("/market-data/EURUSD", headers={"Authorization": f"Bearer {auth_env['token']}"})

    assert response.status_code == 200
    assert response.json()["close"] == 105.0
    assert record.call_threads, "provider was never called"
    assert record.call_threads[0] != loop_thread["id"]  # ran on a worker thread
