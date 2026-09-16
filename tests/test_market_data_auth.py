"""Tests for authentication protection of the market-data endpoint.

Require none of: real PostgreSQL, real MT5 terminal, network, real credentials.
The REAL get_current_user dependency runs (real JWT decode + real async user
lookup via a per-test SQLite get_db override); only the vendor boundaries are
faked behind the existing composition-root seams. Since Step 53 the market-data
path resolves the requested symbol through the instrument service first, so the
instrument provider CLASS seam is replaced with the deterministic in-memory
catalog: the real InstrumentService and the real resolution rules run, with no
terminal and no credentials, and the recording market-data provider proves which
spelling the candle read was asked for.
"""
import asyncio
from datetime import timedelta
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.market_data_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, TradeMode
from app.providers.market_data import Candle
from decimal import Decimal

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
FAKE_CANDLE = Candle(
    timestamp=__import__("datetime").datetime(2024, 1, 15, 12, 0, 0),
    open=Decimal("100.0"),
    high=Decimal("110.0"),
    low=Decimal("95.0"),
    close=Decimal("105.0"),
    volume=1234.0,
)


def set_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


class _FakeProvider:
    """Deterministic provider standing in for MT5 at the composition seam.

    The composition root constructs providers with the authenticated tenant's
    credentials and the process-wide session manager; the fake accepts and
    ignores both, since the tenant-scoped session itself is covered by the
    MT5 session/provider tests. The symbol each candle read was asked for is
    recorded, so resolution can be asserted at the HTTP boundary.
    """

    requested_symbols: list[str] = []

    def __init__(self, behavior: str = "ok", session_manager: object = None, credentials: object = None):
        self.behavior = behavior
        self.credentials = credentials

    def get_market_data(self, symbol: str) -> Candle:
        _FakeProvider.requested_symbols.append(symbol)
        if self.behavior == "value_error":
            raise ValueError(f"No candle data returned for {symbol}")
        if self.behavior == "runtime_error":
            raise RuntimeError("MT5 infrastructure failure")
        return FAKE_CANDLE


def catalog_provider_class(*symbols: str):
    """Instrument-provider class at the composition seam over a fixed catalog."""

    class CatalogInstrumentProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            self._inner = FakeInstrumentProvider(
                instruments=tuple(
                    Instrument(
                        symbol=symbol,
                        name=None,
                        asset_class=None,
                        base_currency=None,
                        quote_currency=None,
                        digits=5,
                        trade_mode=TradeMode.FULL,
                    )
                    for symbol in symbols
                )
            )

        def get_instrument(self, symbol: str):
            return self._inner.get_instrument(symbol)

        def list_instruments(self):
            return self._inner.list_instruments()

    return CatalogInstrumentProvider


@pytest.fixture()
def protected_app(tmp_path, monkeypatch):
    """Probe app with real auth, SQLite user DB, and a faked provider."""
    set_auth_config(monkeypatch)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/protect_test.db")
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

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    _FakeProvider.requested_symbols = []

    def use_provider(behavior: str) -> None:
        # The composition root builds the provider class with keyword arguments,
        # so the fake class (not a zero-arg lambda) is installed here.
        class FakeProvider(_FakeProvider):
            def __init__(self, session_manager: object = None, credentials: object = None) -> None:
                super().__init__(behavior, session_manager, credentials)

        monkeypatch.setattr(deps, "MT5MarketDataProvider", FakeProvider)

    def use_catalog(*symbols: str, error: Exception | None = None) -> None:
        """Install the broker catalog the requested symbol is resolved against."""
        if error is None:
            monkeypatch.setattr(deps, "MT5InstrumentProvider", catalog_provider_class(*symbols))
            return

        class FailingInstrumentProvider:
            def __init__(self, session_manager: object = None, credentials: object = None) -> None:
                pass

            def get_instrument(self, symbol: str):
                raise error

            def list_instruments(self):
                raise error

        monkeypatch.setattr(deps, "MT5InstrumentProvider", FailingInstrumentProvider)

    use_provider("ok")
    use_catalog("EURUSD", "XAUUSD", "XAUUSD.r")
    yield {
        "app": app,
        "user_token": create_access_token(str(user_id)),
        "use_provider": use_provider,
        "use_catalog": use_catalog,
        "set_user_active": lambda active: _set_active(factory, User, user_id, active),
        "factory": factory,
    }
    asyncio.run(engine.dispose())


def _set_active(factory: async_sessionmaker[AsyncSession], model: type, row_id: int, active: bool) -> None:
    async def mutate() -> None:
        async with factory() as session:
            row = await session.get(model, row_id)
            assert row is not None
            row.is_active = active
            await session.commit()

    asyncio.run(mutate())


def get(app: FastAPI, token: str | None, symbol: str = "EURUSD"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app).get(f"/market-data/{symbol}", headers=headers)


# --- authentication enforcement ------------------------------------------------


def test_unauthenticated_request_returns_401(protected_app):
    response = get(protected_app["app"], token=None)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_malformed_token_returns_401(protected_app):
    response = get(protected_app["app"], token="not-a-jwt-token")

    assert response.status_code == 401


def test_expired_token_returns_401(protected_app):
    expired = create_access_token("1", expires_delta=timedelta(seconds=-10))

    response = get(protected_app["app"], token=expired)

    assert response.status_code == 401


def test_token_for_nonexistent_user_returns_401(protected_app):
    response = get(protected_app["app"], token=create_access_token("999999"))

    assert response.status_code == 401


def test_token_for_inactive_user_returns_401(protected_app):
    protected_app["set_user_active"](False)

    response = get(protected_app["app"], token=protected_app["user_token"])

    assert response.status_code == 401


# --- authenticated market-data behavior preserved -------------------------------


def test_valid_active_user_reaches_market_data_path(protected_app):
    response = get(protected_app["app"], token=protected_app["user_token"])

    assert response.status_code == 200
    assert response.json()["close"] == 105.0


def test_existing_404_behavior_unchanged_after_authentication(protected_app):
    protected_app["use_provider"]("value_error")

    response = get(protected_app["app"], token=protected_app["user_token"])

    assert response.status_code == 404
    assert response.json()["detail"] == "Market data unavailable for the requested symbol"


def test_existing_503_behavior_unchanged_after_authentication(protected_app):
    protected_app["use_provider"]("runtime_error")

    response = get(protected_app["app"], token=protected_app["user_token"])

    assert response.status_code == 503
    assert response.json()["detail"] == "Market data service temporarily unavailable"


# --- Step 53: the requested symbol is resolved through the broker catalog ---------


@pytest.mark.parametrize("requested", ["xauusd", "XAuUsD", "XAUUSD"])
def test_case_insensitive_requests_read_the_brokers_canonical_symbol(protected_app, requested):
    response = get(protected_app["app"], token=protected_app["user_token"], symbol=requested)

    assert response.status_code == 200
    # The candle provider was asked for the broker's own spelling, not the
    # caller's.
    assert _FakeProvider.requested_symbols == ["XAUUSD"]


def test_a_suffixed_broker_catalog_resolves_the_base_symbol(protected_app):
    protected_app["use_catalog"]("XAUUSD.r")

    response = get(protected_app["app"], token=protected_app["user_token"], symbol="xauusd")

    assert response.status_code == 200
    assert _FakeProvider.requested_symbols == ["XAUUSD.r"]


def test_several_broker_variants_fail_closed_as_the_resolution_contract_requires(protected_app):
    protected_app["use_catalog"]("XAUUSD.r", "XAUUSD.m")

    response = get(protected_app["app"], token=protected_app["user_token"], symbol="XAUUSD")

    # The existing market-data client-error contract, decided BEFORE any candle
    # read: two different instruments are never a coin flip.
    assert response.status_code == 404
    assert response.json()["detail"] == "Market data unavailable for the requested symbol"
    assert _FakeProvider.requested_symbols == []


def test_an_unknown_symbol_is_a_404_without_a_candle_read(protected_app):
    response = get(protected_app["app"], token=protected_app["user_token"], symbol="NOSUCHSYMBOL")

    assert response.status_code == 404
    assert response.json()["detail"] == "Market data unavailable for the requested symbol"
    assert _FakeProvider.requested_symbols == []


def test_a_catalog_failure_is_the_generic_503(protected_app):
    protected_app["use_catalog"](error=RuntimeError("MT5 instrument catalog unavailable"))

    response = get(protected_app["app"], token=protected_app["user_token"], symbol="EURUSD")

    assert response.status_code == 503
    assert response.json()["detail"] == "Market data service temporarily unavailable"
    assert _FakeProvider.requested_symbols == []


def test_no_credential_or_provider_internal_appears_in_a_resolved_response(protected_app):
    response = get(protected_app["app"], token=protected_app["user_token"], symbol="xauusd")

    assert response.status_code == 200
    assert set(response.json()) == {"timestamp", "open", "high", "low", "close", "volume"}
    rendered = response.text.lower()
    assert "password" not in rendered and "credential" not in rendered
    assert "mt5" not in rendered
