"""Tests for GET /economic-intelligence/today (auth, tenant scope, contract).

Require none of: real MT5, PostgreSQL, network, credentials, or a real calendar
source. The get_db dependency is overridden with a per-test file-based async
SQLite database; the REAL authentication dependency runs (real JWT decode + real
database lookup). The positions composition-root seam is patched with a
deterministic fake (the established positions-test pattern) so no MT5 terminal
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
from app.api.economic_intelligence_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.economic_calendar import EconomicEvent
from app.providers.position import Position, PositionType

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

XAUUSD = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=0.10,
    open_price=3642.50,
    current_price=3648.20,
    profit=57.00,
)
EURUSD = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=1.00,
    open_price=1.0850,
    current_price=1.0820,
    profit=-30.00,
)

TOP_LEVEL_KEYS = {
    "broker_id",
    "as_of",
    "window_from",
    "window_to",
    "data_source",
    "position_symbols",
    "events",
}
EVENT_KEYS = {"event_id", "timestamp", "currency", "title", "impact", "forecast", "previous", "actual"}
RELEVANCE_KEYS = {"ticket", "symbol", "type", "relevance", "reason"}


# --- composition-root seams (established pattern) ------------------------------------


def make_fake_position_provider_class(positions: tuple[Position, ...], error: Exception | None = None):
    """Fake MT5PositionProvider installed at the composition-root seam.

    Returns the class plus the list of calling thread ids, so tests can assert
    both delegation and that the blocking read left the event-loop thread.
    """
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


class FailingCalendarProvider:
    """Calendar provider whose source is unavailable (infrastructure failure)."""

    source = "test-failing-calendar"

    def __init__(self) -> None:
        pass

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        raise RuntimeError("calendar source unavailable")


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_positions(monkeypatch):
    def _install(positions: tuple[Position, ...] = (), error: Exception | None = None):
        cls, record = make_fake_position_provider_class(positions, error)
        monkeypatch.setattr(deps, "MT5PositionProvider", cls)
        deps._position_provider = None
        return record

    yield _install
    # Never leak a fake (or real) provider into other tests.
    deps._position_provider = None


@pytest.fixture()
def failing_calendar(monkeypatch):
    monkeypatch.setattr(deps, "FakeEconomicCalendarProvider", FailingCalendarProvider)


@pytest.fixture()
def intelligence_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/intelligence.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TI-A")
            broker_b = Broker(name="Broker B", code="TI-B")
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


def get_intelligence(env, user_id: int, query: str = "") -> tuple[int, dict[str, object]]:
    client = env["make_app"]()
    with client as c:
        response = c.get(f"/economic-intelligence/today{query}", headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


# --- authentication / authorization ---------------------------------------------------


def test_unauthenticated_request_returns_401(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    with intelligence_env["make_app"]() as client:
        response = client.get("/economic-intelligence/today")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_authenticated_customer_receives_intelligence(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD, EURUSD))

    status, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    assert status == 200
    assert set(body.keys()) == TOP_LEVEL_KEYS
    assert isinstance(body["events"], list)
    assert body["events"], "today's deterministic catalog must produce events"


def test_response_contract_is_ai_ready(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    assert body["position_symbols"] == ["XAUUSD"]
    assert body["data_source"] == "fake-development-placeholder"
    # Timestamps arrive as timezone-aware ISO 8601 UTC values.
    assert is_utc_iso(body["as_of"])
    assert is_utc_iso(body["window_from"])
    assert is_utc_iso(body["window_to"])

    for item in body["events"]:
        assert set(item.keys()) == {"event", "overall_relevance", "positions"}
        assert set(item["event"].keys()) == EVENT_KEYS
        assert is_utc_iso(item["event"]["timestamp"])
        assert item["event"]["impact"] in {"LOW", "MEDIUM", "HIGH"}
        for relevance in item["positions"]:
            assert set(relevance.keys()) == RELEVANCE_KEYS
            assert relevance["relevance"] in {"RELEVANT", "POTENTIALLY_RELEVANT", "NOT_OBVIOUSLY_RELEVANT"}
            assert relevance["reason"]


def test_customer_receives_only_their_own_position_context(intelligence_env, patched_positions) -> None:
    call_threads = patched_positions((XAUUSD,))

    _, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    # The response reflects this caller's account positions and nothing else.
    assert body["position_symbols"] == ["XAUUSD"]
    assert all(entry["symbol"] == "XAUUSD" for item in body["events"] for entry in item["positions"])
    assert len(call_threads) == 1


# --- tenant scope ---------------------------------------------------------------------


def test_each_user_gets_their_own_tenant_identity(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body_a = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])
    _, body_b = get_intelligence(intelligence_env, intelligence_env["customer_b_id"])

    assert body_a["broker_id"] == intelligence_env["broker_a_id"]
    assert body_b["broker_id"] == intelligence_env["broker_b_id"]
    assert body_a["broker_id"] != body_b["broker_id"]


def test_admin_roles_do_not_bypass_tenant_identity(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    # A super_admin authenticated against broker B is still scoped to broker B.
    _, body = get_intelligence(intelligence_env, intelligence_env["super_b_id"])

    assert body["broker_id"] == intelligence_env["broker_b_id"]


def test_broker_id_and_user_id_query_parameters_cannot_change_scope(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))
    user_id = intelligence_env["customer_a_id"]

    _, plain = get_intelligence(intelligence_env, user_id)
    _, with_params = get_intelligence(intelligence_env, user_id, query="?broker_id=2&user_id=999")

    # No such parameters exist: everything except the per-request as_of stamp
    # (which is legitimately the current time) is identical.
    def without_as_of(body: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in body.items() if key != "as_of"}

    assert without_as_of(with_params) == without_as_of(plain)
    assert with_params["broker_id"] == intelligence_env["broker_a_id"]


# --- filtering --------------------------------------------------------------------------


def test_min_impact_filter_is_applied(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"], query="?min_impact=HIGH")

    assert body["events"]
    assert all(item["event"]["impact"] == "HIGH" for item in body["events"])


def test_invalid_min_impact_is_rejected_with_422(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    client = intelligence_env["make_app"]()
    with client as c:
        response = c.get(
            "/economic-intelligence/today?min_impact=EXTREME",
            headers=auth_header(token_for(intelligence_env["customer_a_id"])),
        )

    assert response.status_code == 422


# --- credential / secret safety ---------------------------------------------------------


def test_response_exposes_no_credentials(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])
    text = str(body)

    assert "password_hash" not in text
    assert "mt5_password_encrypted" not in text
    assert "$2b$" not in text


# --- error handling ---------------------------------------------------------------------


def test_mt5_position_failure_returns_generic_503(intelligence_env, patched_positions) -> None:
    patched_positions(error=RuntimeError("terminal unavailable"))

    with intelligence_env["make_app"]() as client:
        response = client.get(
            "/economic-intelligence/today",
            headers=auth_header(token_for(intelligence_env["customer_a_id"])),
        )

    assert response.status_code == 503
    # Generic detail only: no provider internals leak.
    assert response.json() == {"detail": "Economic intelligence service temporarily unavailable"}


def test_calendar_provider_failure_returns_generic_503(intelligence_env, patched_positions, failing_calendar) -> None:
    patched_positions((XAUUSD,))

    with intelligence_env["make_app"]() as client:
        response = client.get(
            "/economic-intelligence/today",
            headers=auth_header(token_for(intelligence_env["customer_a_id"])),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Economic intelligence service temporarily unavailable"}


def test_reading_positions_is_offloaded_off_the_event_loop(intelligence_env, patched_positions) -> None:
    call_threads = patched_positions((XAUUSD,))
    main_thread = threading.get_ident()

    status, _ = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    assert status == 200
    # The blocking position read must not run on the request (event-loop) thread.
    assert call_threads
    assert all(thread_id != main_thread for thread_id in call_threads)


# --- existing API wiring stays intact ------------------------------------------------------


def test_existing_endpoints_remain_registered() -> None:
    from app.main import app

    # The generated OpenAPI schema is the authoritative list of mounted routes.
    paths = set(app.openapi()["paths"])

    assert "/economic-intelligence/today" in paths
    # Regression guard: the pre-existing feature endpoints are still mounted.
    assert {"/positions", "/account-info", "/trade-history", "/market-data/{symbol}", "/users"} <= paths


def test_placeholder_data_is_never_labelled_as_live(intelligence_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    # Provenance is explicit and non-live in every response of this step.
    assert body["data_source"] == "fake-development-placeholder"


# --- production safety: the placeholder calendar must not be served ----------------------


def test_placeholder_calendar_fails_closed_outside_development(
    intelligence_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    status, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    # No production calendar source exists yet, so the endpoint refuses rather
    # than returning fabricated events to a broker's customers.
    assert status == 503
    assert body == {"detail": "Economic calendar data source is not configured"}
    # It failed before doing any work.
    assert record == []


@pytest.mark.parametrize("environment", ["staging", "production", ""])
def test_any_non_development_environment_fails_closed(
    intelligence_env, patched_positions, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", environment, raising=True)

    status, _ = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    assert status == 503


def test_placeholder_calendar_still_works_in_development(
    intelligence_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Explicitly pin the development case: the guard must not change dev/test
    # behaviour.
    patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)

    status, body = get_intelligence(intelligence_env, intelligence_env["customer_a_id"])

    assert status == 200
    assert body["data_source"] == "fake-development-placeholder"
