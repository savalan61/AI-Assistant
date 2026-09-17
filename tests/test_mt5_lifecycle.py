"""MT5 session lifecycle tests: process-wide session manager, lifespan, threadpool.

The process-wide object is now the MT5 *session* (MT5 authenticates one account
per process), not a cached provider. All tests patch the composition-root seams
(app.core.dependencies.MT5SessionManager / MT5MarketDataProvider), so none of
them require MT5, PostgreSQL, network, credentials, or .env.
"""
import asyncio
import threading
from datetime import datetime
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
import app.main
from app.api.market_data_router import router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, TradeMode
from app.providers.market_data import Candle, MarketDataProvider
from decimal import Decimal

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

CANDLE = Candle(
    timestamp=datetime(2024, 1, 15, 12, 0, 0),
    open=Decimal("100.0"),
    high=Decimal("110.0"),
    low=Decimal("95.0"),
    close=Decimal("105.0"),
    volume=1234.0,
)


class FakeMT5:
    """Minimal MT5 seam for the session manager (no terminal, no account)."""

    def __init__(self, initialize_result: object = True, initialize_error: Exception | None = None):
        self.initialize_result = initialize_result
        self.initialize_error = initialize_error
        self.authenticate_calls: list[dict[str, object]] = []
        self.shutdown_calls = 0

    def initialize(self, **kwargs: object) -> object:
        self.authenticate_calls.append(dict(kwargs))
        if self.initialize_error is not None:
            raise self.initialize_error
        return self.initialize_result

    def login(self, **kwargs: object) -> object:
        self.authenticate_calls.append(dict(kwargs))
        return True

    def last_error(self) -> tuple[int, str]:
        return (-6, "simulated authorization failure")

    def account_info(self) -> object:
        """The terminal reports the account it is authenticated as.

        The session boundary verifies this against the requesting tenant before
        serving a read, so the fake reports the account it last authenticated.
        """
        last_auth = self.authenticate_calls[-1] if self.authenticate_calls else None
        if last_auth is None:  # pragma: no cover - every read authenticates first
            return None
        return SimpleNamespace(login=last_auth["login"], server=last_auth["server"])

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _Recording:
    """Shared record of session-manager construction/shutdown and provider calls."""

    def __init__(self):
        self.session_managers: list[MT5SessionManager] = []
        self.session_shutdowns = 0
        self.providers: list[object] = []
        self.provider_shutdowns = 0
        self.call_threads: list[int] = []
        self.symbols: list[str] = []
        self.credentials: list[object] = []
        # The market-data path now resolves the requested symbol first (Step 53),
        # so the instrument boundary is built per request too — with the same
        # authenticated tenant's credentials.
        self.instrument_providers: list[object] = []
        self.instrument_credentials: list[object] = []


def make_recording_provider_class(record: _Recording):
    class RecordingProvider(MarketDataProvider):
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            record.providers.append(self)
            record.credentials.append(credentials)

        def get_market_data(self, symbol: str) -> Candle:
            record.symbols.append(symbol)
            record.call_threads.append(threading.get_ident())
            return CANDLE

        def shutdown(self) -> None:
            record.provider_shutdowns += 1

    return RecordingProvider


@pytest.fixture()
def recording_session(monkeypatch):
    """Patch the session-manager construction with a fake-MT5 recording class."""
    record = _Recording()
    fake_mt5 = FakeMT5()

    class RecordingSessionManager(MT5SessionManager):
        # The composition root passes the deployment's terminal path and IPC
        # timeout, so the wrapper forwards whatever it is given.
        def __init__(
            self,
            mt5_api: object = None,
            *,
            terminal_path: str | None = None,
            timeout_ms: int | None = None,
        ) -> None:
            super().__init__(
                mt5_api=fake_mt5 if mt5_api is None else mt5_api,
                terminal_path=terminal_path,
                timeout_ms=timeout_ms,
            )
            record.session_managers.append(self)

        def shutdown(self) -> None:
            record.session_shutdowns += 1
            super().shutdown()

    monkeypatch.setattr(deps, "MT5SessionManager", RecordingSessionManager)
    monkeypatch.setattr(deps, "_mt5_session_manager", None, raising=True)
    yield {"record": record, "fake_mt5": fake_mt5}
    monkeypatch.setattr(deps, "_mt5_session_manager", None, raising=True)


@pytest.fixture()
def patched_provider(monkeypatch, recording_session):
    """Install recording market-data and instrument providers at the seams."""
    record: _Recording = recording_session["record"]

    class RecordingInstrumentProvider:
        """In-memory broker catalog: EURUSD, exactly as the broker lists it."""

        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            record.instrument_providers.append(self)
            record.instrument_credentials.append(credentials)
            self._inner = FakeInstrumentProvider(
                instruments=(
                    Instrument(
                        symbol="EURUSD",
                        name="Euro vs US Dollar",
                        asset_class="Forex",
                        base_currency="EUR",
                        quote_currency="USD",
                        digits=5,
                        trade_mode=TradeMode.FULL,
                    ),
                )
            )

        def get_instrument(self, symbol: str):
            return self._inner.get_instrument(symbol)

        def list_instruments(self):
            return self._inner.list_instruments()

    monkeypatch.setattr(deps, "MT5MarketDataProvider", make_recording_provider_class(record))
    monkeypatch.setattr(deps, "MT5InstrumentProvider", RecordingInstrumentProvider)
    yield record


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    """Test-only JWT config plus a seeded user for authenticated HTTP probes.

    The market-data route requires authentication, and the composition root now
    also resolves the tenant's MT5 credentials from the database, so these probes
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
            user = User(broker_id=broker.id, login="10001", password_hash="x" * 60, is_active=True)
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


# --- process-wide session manager identity ---------------------------------


def test_session_manager_is_constructed_once_and_reused(recording_session):
    record: _Recording = recording_session["record"]

    first = deps.get_mt5_session_manager()
    second = deps.get_mt5_session_manager()

    assert first is second
    assert len(record.session_managers) == 1


def test_session_manager_is_constructed_exactly_once_under_concurrency(recording_session):
    from concurrent.futures import ThreadPoolExecutor

    record: _Recording = recording_session["record"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        managers = list(pool.map(lambda _: deps.get_mt5_session_manager(), range(16)))

    assert len(record.session_managers) == 1
    assert all(manager is record.session_managers[0] for manager in managers)


# --- lifespan: no tenant exists at boot ------------------------------------


def test_lifespan_never_authenticates_at_startup_and_boots_cleanly(recording_session):
    record: _Recording = recording_session["record"]

    with TestClient(app.main.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").status_code == 200
        # No tenant is authenticated at boot: there is no session to warm.
        assert record.session_managers == []
        assert recording_session["fake_mt5"].authenticate_calls == []


def test_lifespan_releases_the_process_wide_session_once(recording_session):
    record: _Recording = recording_session["record"]
    manager = deps.get_mt5_session_manager()  # as the first authenticated read would

    with TestClient(app.main.app) as client:
        assert client.get("/health").status_code == 200

    assert record.session_shutdowns == 1
    assert manager.authenticated_account is None
    assert deps._mt5_session_manager is None


# --- shutdown behavior ------------------------------------------------------


def test_shutdown_clears_the_cache_and_the_next_request_rebuilds_it(recording_session):
    record: _Recording = recording_session["record"]

    first = deps.get_mt5_session_manager()
    deps.shutdown_mt5_session()

    assert record.session_shutdowns == 1
    assert deps._mt5_session_manager is None

    second = deps.get_mt5_session_manager()
    assert second is not first
    assert len(record.session_managers) == 2


def test_shutdown_is_safe_when_nothing_was_ever_built(recording_session):
    record: _Recording = recording_session["record"]

    deps.shutdown_mt5_session()  # must not raise

    assert record.session_shutdowns == 0
    assert record.session_managers == []


# --- composition and blocking boundary --------------------------------------


def test_market_data_provider_is_built_per_request_for_the_tenant(
    patched_provider, auth_env, monkeypatch
):
    record: _Recording = patched_provider
    client = TestClient(app.main.app)

    for _ in range(2):
        response = client.get(
            "/market-data/EURUSD", headers={"Authorization": f"Bearer {auth_env['token']}"}
        )
        assert response.status_code == 200

    # Two requests, two per-request providers (no cached provider can serve one
    # tenant's credentials to another), each bound to the caller's own identity.
    assert len(record.providers) == 2
    for credentials in record.credentials:
        assert getattr(credentials, "login", None) == 10001

    # Step 53: the symbol is resolved through the instrument boundary first, so
    # that provider is per-request too — and bound to the SAME tenant identity.
    assert len(record.instrument_providers) == 2
    for credentials in record.instrument_credentials:
        assert getattr(credentials, "login", None) == 10001
    # The candle read was asked for the broker's own spelling.
    assert record.symbols == ["EURUSD", "EURUSD"]


def test_blocking_call_runs_off_the_event_loop_thread(patched_provider, auth_env):
    record: _Recording = patched_provider
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


def test_unusable_tenant_session_maps_to_503(recording_session, auth_env, monkeypatch):
    # The real provider runs here: the seeded user has no stored MT5 password, so
    # the tenant's session cannot be established and the request fails safely.
    monkeypatch.setattr(deps, "MT5MarketDataProvider", deps.MT5MarketDataProvider, raising=True)

    client = TestClient(app.main.app)
    response = client.get("/market-data/EURUSD", headers={"Authorization": f"Bearer {auth_env['token']}"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Market data service temporarily unavailable"
    # Nothing was authenticated and no partial session was cached.
    assert deps.get_mt5_session_manager().authenticated_account is None
