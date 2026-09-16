"""Tests for the instrument discovery/resolution API.

Require none of: real MT5, PostgreSQL, network, or real credentials. Provider
tests patch the composition-root provider class seam
(app.core.dependencies.MT5InstrumentProvider), which receives the authenticated
tenant's MT5 credentials and the process-wide session manager, and override
get_db with a per-test file-based async SQLite database. JWT config uses
test-only values. No pytest asyncio plugin: async setup is driven with
asyncio.run.
"""
import asyncio
import threading
from typing import Any, AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.instruments_router import router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, InstrumentProvider, TradeMode
from app.services.instruments import InstrumentService

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"


# --- service delegation (fake provider pattern) --------------------------------------


def test_service_uses_the_injected_provider_contract():
    provider = FakeInstrumentProvider()

    assert isinstance(provider, InstrumentProvider)
    InstrumentService(provider).resolve("XAUUSD")

    assert provider.get_calls == ["XAUUSD"]


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


def make_fake_provider_class(
    error: Exception | None = None,
    instruments: tuple[Instrument, ...] | None = None,
):
    """Fake provider class at the composition-root seam; construction recorded."""
    inner = FakeInstrumentProvider() if instruments is None else FakeInstrumentProvider(instruments)
    record: dict[str, Any] = {"instances": [], "credentials": [], "calls": [], "call_threads": []}

    class FakeMT5InstrumentProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            # The composition root passes the tenant's credentials and the
            # process-wide session manager to every provider it builds.
            record["instances"].append(self)
            record["credentials"].append(credentials)

        def get_instrument(self, symbol: str) -> Instrument:
            record["calls"].append(("get", symbol))
            record["call_threads"].append(threading.get_ident())
            if error is not None:
                raise error
            return inner.get_instrument(symbol)

        def list_instruments(self) -> tuple[Instrument, ...]:
            record["calls"].append(("list", None))
            record["call_threads"].append(threading.get_ident())
            if error is not None:
                raise error
            return inner.list_instruments()

    return FakeMT5InstrumentProvider, record


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def patched_instrument_provider(monkeypatch):
    def _install(error: Exception | None = None, instruments: tuple[Instrument, ...] | None = None):
        cls, record = make_fake_provider_class(error, instruments)
        monkeypatch.setattr(deps, "MT5InstrumentProvider", cls)
        return record

    yield _install
    # monkeypatch restores the real provider class after each test.


@pytest.fixture()
def instruments_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/instruments.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TA-1")
            broker_b = Broker(name="Broker B", code="TB-1")
            session.add_all([broker_a, broker_b])
            await session.commit()
            # Two tenants in two different brokers: cross-tenant access is what
            # the isolation tests below pin.
            user_a = User(broker_id=broker_a.id, login="10001", password_hash="x" * 60, is_active=True)
            user_b = User(broker_id=broker_b.id, login="20002", password_hash="x" * 60, is_active=True)
            session.add_all([user_a, user_b])
            await session.commit()
            return {"user_a": user_a.id, "user_b": user_b.id}

    user_ids = asyncio.run(seed())

    def make_app() -> TestClient:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    yield {"make_app": make_app, "user_ids": user_ids, "factory": factory}
    asyncio.run(engine.dispose())


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(env, who: str = "user_a") -> str:
    return create_access_token(str(env["user_ids"][who]))


# --- API wiring ------------------------------------------------------------------------


def test_new_and_existing_endpoints_remain_registered() -> None:
    from app.main import app

    # The generated OpenAPI schema is the authoritative list of mounted routes.
    paths = set(app.openapi()["paths"])

    assert "/instruments" in paths
    assert "/instruments/{symbol}" in paths
    # Regression guard: the pre-existing feature endpoints are still mounted.
    assert {
        "/positions",
        "/account-info",
        "/trade-history",
        "/market-data/{symbol}",
        "/agent",
        "/economic-intelligence/today",
        "/fundamental-intelligence/today",
        "/financial-research/today",
        "/portfolio-intelligence",
        "/health",
    } <= paths


# --- resolution contract --------------------------------------------------------------


def test_authenticated_user_resolves_a_symbol(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/XAUUSD.r", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 200
    assert response.json() == {
        "symbol": "XAUUSD.r",
        "name": "Gold vs US Dollar (retail)",
        "asset_class": "Metals",
        "base_currency": "XAU",
        "quote_currency": "USD",
        "digits": 2,
        "trade_mode": "FULL",
    }


def test_response_contains_exactly_the_seven_contract_fields(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        body = c.get("/instruments/EURUSD", headers=auth_header(token_for(instruments_env))).json()

    assert set(body.keys()) == {
        "symbol",
        "name",
        "asset_class",
        "base_currency",
        "quote_currency",
        "digits",
        "trade_mode",
    }


@pytest.mark.parametrize(
    ("symbol", "expected_mode"),
    [("USOIL", "FULL"), ("NAS100", "CLOSE_ONLY"), ("AAPL", "LONG_ONLY"), ("NICKEL", "DISABLED")],
)
def test_trade_mode_serializes_as_the_documented_word(
    instruments_env, patched_instrument_provider, symbol, expected_mode
):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        body = c.get(f"/instruments/{symbol}", headers=auth_header(token_for(instruments_env))).json()

    assert body["trade_mode"] == expected_mode


def test_missing_optional_metadata_serializes_as_null(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        body = c.get("/instruments/NICKEL", headers=auth_header(token_for(instruments_env))).json()

    assert body["symbol"] == "NICKEL"
    assert body["name"] is None
    assert body["asset_class"] is None
    assert body["digits"] is None


def test_resolution_is_case_insensitive_and_returns_the_brokers_spelling(
    instruments_env, patched_instrument_provider
):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        body = c.get("/instruments/xauusd.r", headers=auth_header(token_for(instruments_env))).json()

    assert body["symbol"] == "XAUUSD.r"


def test_any_broker_symbol_resolves_generically(instruments_env, patched_instrument_provider):
    """Equities, crypto, soft commodities and index symbols all work the same way."""
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        headers = auth_header(token_for(instruments_env))
        for symbol in ("AAPL", "LVMH", "BTCUSD", "COFFEE", "NICKEL", "USOIL", "NAS100"):
            response = c.get(f"/instruments/{symbol}", headers=headers)
            assert response.status_code == 200
            assert response.json()["symbol"] == symbol


def test_no_raw_mt5_or_provider_internals_leak(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        body = str(c.get("/instruments/XAUUSD.r", headers=auth_header(token_for(instruments_env))).json())

    assert "Instrument(" not in body  # NamedTuple repr must not leak
    assert "currency_base" not in body  # raw MT5 attribute spelling must not leak
    assert "currency_profit" not in body
    assert "trade_mode_" not in body
    assert "password" not in body and "token" not in body


# --- catalog listing ------------------------------------------------------------------


def test_authenticated_user_lists_the_catalog(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"instruments", "total", "truncated"}
    symbols = [item["symbol"] for item in body["instruments"]]
    assert symbols == sorted(symbols)  # deterministic ordering, not terminal order
    assert body["total"] == len(symbols)
    assert body["truncated"] is False


def test_catalog_search_filters_by_symbol_and_description(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        headers = auth_header(token_for(instruments_env))
        by_symbol = c.get("/instruments", params={"search": "xau"}, headers=headers).json()
        by_name = c.get("/instruments", params={"search": "Gold"}, headers=headers).json()

    assert [item["symbol"] for item in by_symbol["instruments"]] == ["XAUUSD", "XAUUSD.r"]
    assert [item["symbol"] for item in by_name["instruments"]] == ["XAUUSD", "XAUUSD.r"]


def test_catalog_with_no_match_is_a_200_with_an_empty_array(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments", params={"search": "zzz"}, headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 200
    assert response.json() == {"instruments": [], "total": 0, "truncated": False}


def test_catalog_is_bounded_and_states_the_omission(instruments_env, patched_instrument_provider):
    big = tuple(
        Instrument(
            symbol=f"SYM{index:03d}",
            name=f"Symbol {index}",
            asset_class="Forex",
            base_currency=None,
            quote_currency=None,
            digits=2,
            trade_mode=TradeMode.FULL,
        )
        for index in range(InstrumentService.MAX_INSTRUMENTS + 5)
    )
    patched_instrument_provider(instruments=big)
    client = instruments_env["make_app"]()

    with client as c:
        body = c.get("/instruments", headers=auth_header(token_for(instruments_env))).json()

    assert len(body["instruments"]) == InstrumentService.MAX_INSTRUMENTS
    assert body["total"] == InstrumentService.MAX_INSTRUMENTS + 5
    assert body["truncated"] is True


def test_oversized_search_is_rejected(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get(
            "/instruments", params={"search": "x" * 65}, headers=auth_header(token_for(instruments_env))
        )

    assert response.status_code == 422  # bounded before it can reach the provider


# --- unknown symbols / client errors ---------------------------------------------------


def test_unknown_symbol_returns_404_with_a_generic_detail(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/NOSUCHSYMBOL", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 404
    assert response.json() == {"detail": "Instrument unavailable for the requested symbol"}


def test_blank_symbol_returns_404(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/%20", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 404


def test_oversized_symbol_is_rejected_before_the_provider(instruments_env, patched_instrument_provider):
    record = patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get(f"/instruments/{'X' * 65}", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 422
    # Composition is inert (a provider object holds no connection), but no read
    # may ever be attempted for input the boundary already rejected.
    assert record["calls"] == []


# --- authentication --------------------------------------------------------------------


def test_unauthenticated_request_returns_401(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    response = client.get("/instruments/XAUUSD")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_returns_401(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/XAUUSD", headers=auth_header("not-a-jwt"))

    assert response.status_code == 401


def test_nonexistent_user_returns_401(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/XAUUSD", headers=auth_header(create_access_token("999999")))

    assert response.status_code == 401


def test_unauthenticated_catalog_request_returns_401(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    assert client.get("/instruments").status_code == 401


# --- provider / MT5 failure mapping -----------------------------------------------------


def test_provider_runtime_error_maps_to_503(instruments_env, patched_instrument_provider):
    patched_instrument_provider(error=RuntimeError("MT5 instrument catalog unavailable"))
    client = instruments_env["make_app"]()

    with client as c:
        headers = auth_header(token_for(instruments_env))
        resolved = c.get("/instruments/XAUUSD", headers=headers)
        listed = c.get("/instruments", headers=headers)

    for response in (resolved, listed):
        assert response.status_code == 503
        # Generic detail: no MT5/provider internals in the response.
        assert response.json()["detail"] == "Instrument service temporarily unavailable"


def test_mt5_session_failure_maps_to_503_and_is_not_cached(instruments_env, monkeypatch):
    """A tenant whose MT5 session cannot be established gets a generic 503.

    The real provider runs here (no provider-class fake): the seeded user has no
    stored MT5 password, so the session boundary refuses and the failure surfaces
    as a service-availability error — and no partial authentication is cached.
    """
    monkeypatch.setattr(deps, "_mt5_session_manager", MT5SessionManager(mt5_api=_FakeMT5()), raising=True)
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get("/instruments/XAUUSD", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 503
    assert response.json()["detail"] == "Instrument service temporarily unavailable"
    assert deps.get_mt5_session_manager().authenticated_account is None  # nothing cached


def test_no_secret_material_appears_in_any_response(instruments_env, patched_instrument_provider):
    patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        headers = auth_header(token_for(instruments_env))
        bodies = [c.get("/instruments", headers=headers).text, c.get("/instruments/XAUUSD", headers=headers).text]

    for body in bodies:
        assert TEST_SECRET not in body
        assert "password" not in body
        assert "TA-1" not in body  # broker code is not part of the instrument contract


# --- tenant isolation -------------------------------------------------------------------


def test_each_request_composes_a_provider_for_the_authenticated_tenant(
    instruments_env, patched_instrument_provider
):
    record = patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        headers = auth_header(token_for(instruments_env))
        c.get("/instruments/XAUUSD", headers=headers)
        c.get("/instruments", headers=headers)

    # Providers are cheap per-request objects (the process-wide state is the MT5
    # session), so nothing about one request's tenant is reused by the next.
    assert len(record["instances"]) == 2
    for credentials in record["credentials"]:
        assert isinstance(credentials, MT5AccountCredentials)
        assert credentials.login == 10001  # the authenticated user's own MT5 identity


def test_two_tenants_compose_their_own_mt5_identity(instruments_env, patched_instrument_provider):
    record = patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        c.get("/instruments/XAUUSD", headers=auth_header(token_for(instruments_env, "user_a")))
        c.get("/instruments/XAUUSD", headers=auth_header(token_for(instruments_env, "user_b")))

    logins = [getattr(credentials, "login", None) for credentials in record["credentials"]]
    assert logins == [10001, 20002]


def test_client_cannot_select_another_tenants_broker_or_account(
    instruments_env, patched_instrument_provider
):
    record = patched_instrument_provider()
    client = instruments_env["make_app"]()

    with client as c:
        response = c.get(
            "/instruments/XAUUSD",
            # Attempted account/broker selection is ignored: the tenant comes from
            # the authenticated database user, never from the request.
            params={"login": 20002, "broker_id": 2, "account_id": 42},
            headers=auth_header(token_for(instruments_env, "user_a")),
        )

    assert response.status_code == 200
    assert [getattr(credentials, "login", None) for credentials in record["credentials"]] == [10001]


# --- blocking boundary -------------------------------------------------------------------


def test_blocking_provider_call_runs_off_the_event_loop_thread(instruments_env, patched_instrument_provider):
    record = patched_instrument_provider()
    loop_thread: dict[str, int] = {}

    async def capture_loop_thread() -> None:
        # Async dependencies run on the event loop: record its thread id.
        loop_thread["id"] = threading.get_ident()

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with instruments_env["factory"]() as session:
            yield session

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(capture_loop_thread)])
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    with client as c:
        response = c.get("/instruments/XAUUSD", headers=auth_header(token_for(instruments_env)))

    assert response.status_code == 200
    assert record["calls"] == [("get", "XAUUSD")]
    assert record["call_threads"][0] != loop_thread["id"]
