"""Tests for the TradeHistoryService and GET /trade-history endpoint.

Require none of: real MT5, PostgreSQL, network, or real credentials. Provider
tests use a deterministic fake at the TradeHistoryProvider abstraction (the
FakePositionProvider pattern); API tests patch the composition-root provider
class seam (app.core.dependencies.MT5TradeHistoryProvider), which now receives
the authenticated tenant's MT5 credentials and the process-wide session
manager, and override get_db with a per-test file-based async SQLite database.
JWT config uses test-only values. No pytest asyncio plugin: async setup is
driven with asyncio.run.
"""
import asyncio
import threading
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.trade_history_router import router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.trade_history import TradeHistoryEntry, TradeHistoryProvider, TradeType
from app.services.trade_history import TradeHistoryService
from decimal import Decimal

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

WINDOW_FROM = datetime(2026, 9, 1, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 14, tzinfo=UTC)

BUY_TRADE = TradeHistoryEntry(
    ticket=246802468,
    order_ticket=987654321,
    symbol="XAUUSD",
    type=TradeType.BUY,
    volume=Decimal("0.10"),
    price=Decimal("3648.20"),
    profit=Decimal("57.00"),
    time=datetime(2026, 9, 14, 12, 30, 0, tzinfo=UTC),
    close_reason="TP",
    stop_loss=Decimal("3635.00"),
    take_profit=Decimal("3650.00"),
)
SELL_TRADE = TradeHistoryEntry(
    ticket=135791357,
    order_ticket=246813578,
    symbol="EURUSD",
    type=TradeType.SELL,
    volume=Decimal("1.00"),
    price=Decimal("1.0820"),
    profit=Decimal("-30.00"),
    time=datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC),
    close_reason=None,
    stop_loss=None,
    take_profit=None,
)


# --- service delegation (Fake Provider pattern) -------------------------------------


def make_service(
    trades: tuple[TradeHistoryEntry, ...],
) -> tuple[TradeHistoryService, FakeTradeHistoryProvider]:
    provider = FakeTradeHistoryProvider(trades=trades)
    return TradeHistoryService(provider), provider


def test_service_delegates_to_provider():
    service, provider = make_service((BUY_TRADE,))

    result = service.get_trade_history(WINDOW_FROM, WINDOW_TO)

    assert result == (BUY_TRADE,)
    assert provider.call_count == 1
    assert provider.last_from_time == WINDOW_FROM
    assert provider.last_to_time == WINDOW_TO
    assert isinstance(provider, TradeHistoryProvider)


def test_service_returns_exactly_what_provider_returns():
    service, _ = make_service((BUY_TRADE, SELL_TRADE))

    assert service.get_trade_history(WINDOW_FROM, WINDOW_TO) == (BUY_TRADE, SELL_TRADE)


# --- API fixtures / helpers ----------------------------------------------------------


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


def make_fake_provider_class(trades: tuple[TradeHistoryEntry, ...], error: Exception | None = None):
    """Fake provider class at the composition-root seam; calls and threads recorded."""
    record: dict[str, Any] = {
        "calls": 0,
        "call_threads": [],
        "from_time": None,
        "to_time": None,
        "credentials": [],
    }

    class FakeTradeHistoryProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            record["credentials"].append(credentials)

        def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
            record["calls"] = int(record["calls"]) + 1
            record["call_threads"].append(threading.get_ident())
            record["from_time"] = from_time
            record["to_time"] = to_time
            if error is not None:
                raise error
            return trades

    return FakeTradeHistoryProvider, record


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_trade_history_provider(monkeypatch):
    def _install(trades: tuple[TradeHistoryEntry, ...] = (), error: Exception | None = None):
        cls, record = make_fake_provider_class(trades, error)
        monkeypatch.setattr(deps, "MT5TradeHistoryProvider", cls)
        return record

    yield _install
    # monkeypatch restores the real provider class after each test.


@pytest.fixture()
def trade_history_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/trade_history.db")
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
                login="10001",
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


def trade_history_url(**params: str) -> str:
    # urlencode properly escapes '+' in the UTC offset ('+00:00' → '%2B00:00');
    # a raw '+' in a query string would arrive at the server as a space.
    return f"/trade-history?{urlencode(params)}"


VALID_WINDOW = {"from": "2026-09-01T00:00:00+00:00", "to": "2026-09-14T00:00:00+00:00"}


# --- API contract ---------------------------------------------------------------------


def test_authenticated_user_retrieves_trade_history(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((BUY_TRADE, SELL_TRADE))
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 200
    body = response.json()
    # Top-level contract: a wrapped object with a single "trades" array.
    assert set(body.keys()) == {"trades"}
    assert isinstance(body["trades"], list)
    assert len(body["trades"]) == 2


def test_each_trade_contains_exactly_the_required_fields(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((BUY_TRADE,))
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    body = response.json()
    assert set(body["trades"][0].keys()) == {
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
    assert body["trades"][0] == {
        "ticket": 246802468,
        "order_ticket": 987654321,
        "symbol": "XAUUSD",
        "type": "BUY",
        "volume": 0.10,
        "price": 3648.20,
        "profit": 57.00,
        "time": "2026-09-14T12:30:00Z",
        "close_reason": "TP",
        "stop_loss": 3635.00,
        "take_profit": 3650.00,
    }


def test_time_serializes_as_utc_iso(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((BUY_TRADE,))
    client = trade_history_env["make_app"]()

    with client as c:
        body = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        ).json()

    assert body["trades"][0]["time"] == "2026-09-14T12:30:00Z"


def test_sell_type_serializes_as_sell(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((SELL_TRADE,))
    client = trade_history_env["make_app"]()

    with client as c:
        body = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        ).json()

    assert body["trades"][0]["type"] == "SELL"


def test_unknown_close_reason_and_levels_serialize_as_null(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((SELL_TRADE,))  # close_reason=None, SL/TP=None
    client = trade_history_env["make_app"]()

    with client as c:
        body = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        ).json()

    assert body["trades"][0]["close_reason"] is None
    assert body["trades"][0]["stop_loss"] is None
    assert body["trades"][0]["take_profit"] is None


def test_no_trades_in_window_returns_200_with_empty_array(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider(())  # empty, NOT an error
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 200
    assert response.json() == {"trades": []}


def test_raw_mt5_structures_do_not_leak(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider((BUY_TRADE,))
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    body = str(response.json())
    assert "TradeHistoryEntry(" not in body  # NamedTuple repr must not leak
    assert "entry=" not in body  # raw MT5 attribute spelling must not leak
    # Raw MT5 historical-order attribute names must never reach the API: the
    # contract exposes stop_loss/take_profit, never the MT5 order fields.
    for raw_field in ("price_open", "price_stoplimit", "volume_initial", "position_id"):
        assert raw_field not in body
    assert "password" not in body and "token" not in body


# --- window validation -------------------------------------------------------------------


def test_missing_from_returns_422(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(to="2026-09-14T00:00:00+00:00"),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 422


def test_missing_to_returns_422(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**{"from": "2026-09-01T00:00:00+00:00"}),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 422


def test_malformed_datetime_returns_422(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**{"from": "not-a-date", "to": "2026-09-14T00:00:00+00:00"}),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 422


def test_naive_datetimes_return_400(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**{"from": "2026-09-01T00:00:00", "to": "2026-09-14T00:00:00"}),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 400
    assert "UTC-aware" in response.json()["detail"]


def test_from_after_to_returns_400(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(
                **{"from": "2026-09-14T00:00:00+00:00", "to": "2026-09-01T00:00:00+00:00"}
            ),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 400


def test_from_equal_to_returns_400(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(
                **{"from": "2026-09-14T00:00:00+00:00", "to": "2026-09-14T00:00:00+00:00"}
            ),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 400


def test_offset_datetimes_are_accepted_and_converted_to_utc(trade_history_env, patched_trade_history_provider):
    record = patched_trade_history_provider(())
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(
                **{"from": "2026-09-01T02:00:00+02:00", "to": "2026-09-14T02:00:00+02:00"}
            ),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 200
    assert record["from_time"] == datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    assert record["to_time"] == datetime(2026, 9, 14, 0, 0, tzinfo=UTC)


def test_window_is_forwarded_to_provider(trade_history_env, patched_trade_history_provider):
    record = patched_trade_history_provider(())
    client = trade_history_env["make_app"]()

    with client as c:
        c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert record["calls"] == 1
    assert record["from_time"] == WINDOW_FROM
    assert record["to_time"] == WINDOW_TO


# --- authentication ---------------------------------------------------------------------


def test_unauthenticated_request_returns_401(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    response = client.get(trade_history_url(**VALID_WINDOW))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_returns_401(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(trade_history_url(**VALID_WINDOW), headers=auth_header("not-a-jwt"))

    assert response.status_code == 401


def test_nonexistent_user_returns_401(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token("999999")),
        )

    assert response.status_code == 401


def test_client_cannot_request_another_account(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider()
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(login="999999", account_id="42", **VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    # The endpoint serves the process-attached account only; no client-supplied
    # account selector exists, so the response is the caller's own window.
    assert response.status_code == 200
    assert response.json() == {"trades": []}


# --- provider / MT5 failure mapping -----------------------------------------------------


def test_provider_runtime_error_maps_to_503(trade_history_env, patched_trade_history_provider):
    patched_trade_history_provider(error=RuntimeError("MT5 trade history unavailable"))
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 503
    # Generic detail: no MT5/provider internals in the response.
    assert response.json()["detail"] == "Trade history service temporarily unavailable"


def test_mt5_session_failure_maps_to_503_and_is_not_cached(trade_history_env, monkeypatch):
    # The real provider runs (no provider-class fake): the seeded user has no
    # stored MT5 password, so the session boundary refuses and the failure maps
    # to a service-availability error — with no credentials disclosed.
    monkeypatch.setattr(deps, "_mt5_session_manager", MT5SessionManager(mt5_api=_FakeMT5()), raising=True)
    client = trade_history_env["make_app"]()

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Trade history service temporarily unavailable"
    assert deps.get_mt5_session_manager().authenticated_account is None  # nothing cached


# --- blocking boundary -------------------------------------------------------------------


class DifferentThreadError(RuntimeError):
    """Raised when the provider runs on the event-loop thread."""


def make_thread_checking_provider_class(trades: tuple[TradeHistoryEntry, ...]):
    """Fake provider that fails if invoked on the event-loop thread."""
    loop_thread: dict[str, int] = {}

    async def capture_loop_thread() -> None:
        # Async dependencies run on the event loop: record its thread id.
        loop_thread["id"] = threading.get_ident()

    class ThreadCheckingTradeHistoryProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_trade_history(self, from_time: datetime, to_time: datetime) -> tuple[TradeHistoryEntry, ...]:
            if threading.get_ident() == loop_thread.get("id"):
                raise DifferentThreadError("MT5 call executed on the event loop thread")
            return trades

    return ThreadCheckingTradeHistoryProvider, capture_loop_thread


def test_blocking_call_runs_off_the_event_loop_thread(trade_history_env, monkeypatch):
    provider_cls, capture_loop_thread = make_thread_checking_provider_class((BUY_TRADE,))
    monkeypatch.setattr(deps, "MT5TradeHistoryProvider", provider_cls)
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(capture_loop_thread)])
    apply_overrides(trade_history_env, app)
    client = TestClient(app)

    with client as c:
        response = c.get(
            trade_history_url(**VALID_WINDOW),
            headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
        )

    assert response.status_code == 200
    # The provider was invoked, and on a worker thread — not the event loop.
    assert response.json()["trades"][0]["ticket"] == 246802468


def apply_overrides(trade_history_env, target_app: FastAPI) -> None:
    from app.db.database import get_db as _get_db

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with trade_history_env["factory"]() as session:
            yield session

    target_app.dependency_overrides[_get_db] = override_get_db


# --- composition-root tenant binding -----------------------------------------------------


def test_composition_root_binds_the_provider_to_the_authenticated_tenant(
    trade_history_env, patched_trade_history_provider
):
    record = patched_trade_history_provider((BUY_TRADE,))
    client = trade_history_env["make_app"]()

    with client as c:
        for _ in range(2):
            c.get(
                trade_history_url(**VALID_WINDOW),
                headers=auth_header(create_access_token(str(trade_history_env["user_id"]))),
            )

    # Providers are cheap per-request objects now (the process-wide state is the
    # MT5 session), each built with the caller's own resolved MT5 identity.
    assert len(record["credentials"]) == 2
    for credentials in record["credentials"]:
        assert isinstance(credentials, MT5AccountCredentials)
        assert credentials.login == 10001
