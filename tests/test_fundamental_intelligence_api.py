"""Tests for GET /fundamental-intelligence/today (auth, tenant scope, contract).

Require none of: real MT5, PostgreSQL, network, credentials, a real calendar
source, a news vendor or an LLM. The get_db dependency is overridden with a
per-test file-based async SQLite database; the REAL authentication dependency
runs (real JWT decode + real database lookup). The positions composition-root
seam is patched with a deterministic fake (the established positions-test
pattern) so no MT5 terminal is needed, and the calendar/news sources are pinned
to the deterministic development sources so no request leaves the process.

The suite pins the boundary guarantees: the mandatory calendar context is always
part of a fundamental answer, tenant scope comes only from the authenticated
user, a deployment without a news source says so (rather than reporting no
news), an unusable configured source fails closed with the established generic
503, and no credential or API key ever appears in a response.
"""
import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.fundamental_intelligence_router import router
from app.core.config import EconomicCalendarSource, NewsSource, settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.position import Position, PositionType

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
# Obvious marker instead of a credential: it must never reach a response.
FAKE_API_KEY = "qg_test_unit-only-not-a-real-key"

XAUUSD = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=Decimal("0.10"),
    open_price=Decimal("3642.50"),
    current_price=Decimal("3648.20"),
    profit=Decimal("57.00"),
)
EURUSD = Position(
    ticket=987654321,
    symbol="EURUSD",
    type=PositionType.SELL,
    volume=Decimal("1.00"),
    open_price=Decimal("1.0850"),
    current_price=Decimal("1.0820"),
    profit=Decimal("-30.00"),
)

TOP_LEVEL_KEYS = {
    "broker_id",
    "as_of",
    "window_from",
    "window_to",
    "focus_symbol",
    "instruments",
    "calendar",
    "news",
    "positions",
}
CALENDAR_KEYS = {"data_source", "events"}
EVENT_KEYS = {"event_id", "timestamp", "currency", "title", "impact", "forecast", "previous", "actual"}
NEWS_KEYS = {"available", "data_source", "unavailable_reason", "items"}
NEWS_ITEM_WRAPPER_KEYS = {"item", "overall_relevance", "matched_instruments", "reason"}
NEWS_ITEM_KEYS = {
    "item_id",
    "published_at",
    "publisher",
    "title",
    "summary",
    "url",
    "instruments",
    "currencies",
    "categories",
}
EXPOSURE_KEYS = {
    "ticket",
    "symbol",
    "type",
    "volume",
    "relevance",
    "status",
    "reason",
    "calendar_event_ids",
    "news_item_ids",
}
RELEVANCE_LEVELS = {"RELEVANT", "POTENTIALLY_RELEVANT", "NOT_OBVIOUSLY_RELEVANT"}


# --- composition-root seams (established pattern) ------------------------------------


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


class FailingCalendarProvider:
    """Calendar provider whose source is unavailable (infrastructure failure)."""

    source = "test-failing-calendar"

    def __init__(self) -> None:
        pass

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[object, ...]:
        raise RuntimeError("calendar source unavailable")


class FailingNewsProvider:
    """News provider whose source is unavailable (infrastructure failure)."""

    source = "test-failing-news"

    def __init__(self) -> None:
        pass

    def get_news(
        self, from_time: datetime, to_time: datetime, *, instruments: tuple[str, ...] = (), limit: int | None = None
    ) -> tuple[object, ...]:
        raise RuntimeError("news source unavailable")


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)
    # Pin both source selections: a developer's local .env may hold a real
    # QuantGist key, and these tests describe the deterministic development
    # sources (offline, reproducible) rather than whatever a machine has.
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", "", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.AUTO, raising=True
    )
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.AUTO, raising=True)
    # NEWS_SOURCE=auto selects Alpha Vantage in development when a key is
    # configured, so the key is cleared: this suite is fully offline.
    monkeypatch.setattr(app_settings, "ALPHA_VANTAGE_API_KEY", "", raising=True)


@pytest.fixture()
def patched_positions(monkeypatch):
    def _install(positions: tuple[Position, ...] = (), error: Exception | None = None):
        cls, record = make_fake_position_provider_class(positions, error)
        monkeypatch.setattr(deps, "MT5PositionProvider", cls)
        return record

    yield _install
    # monkeypatch restores the real provider class after each test.


@pytest.fixture()
def failing_calendar(monkeypatch):
    monkeypatch.setattr(deps, "FakeEconomicCalendarProvider", FailingCalendarProvider)


@pytest.fixture()
def failing_news(monkeypatch):
    monkeypatch.setattr(deps, "FakeNewsProvider", FailingNewsProvider)


@pytest.fixture()
def fundamental_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/fundamental.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="FI-ONE", mt5_server="TheBroker-Live")
            session.add(broker)
            await session.commit()
            # Two CUSTOMERS of the one broker, each with its own MT5 account.
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
            session.add_all([customer_a, customer_b])
            await session.commit()
            return {
                "broker_id": broker.id,
                "customer_a_id": customer_a.id,
                "customer_b_id": customer_b.id,
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


def token_for(user_id: int) -> str:
    return create_access_token(str(user_id))


def get_fundamental(env, user_id: int, query: str = "") -> tuple[int, dict[str, Any]]:
    client = env["make_app"]()
    with client as c:
        response = c.get(
            f"/fundamental-intelligence/today{query}", headers=auth_header(token_for(user_id))
        )
    return response.status_code, response.json()


def events_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body["calendar"]["events"]


def items_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    return body["news"]["items"]


def is_utc_iso(value: object) -> bool:
    return str(value).endswith(("Z", "+00:00"))


# --- authentication / authorization ---------------------------------------------------


def test_unauthenticated_request_returns_401(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    with fundamental_env["make_app"]() as client:
        response = client.get("/fundamental-intelligence/today")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_authenticated_customer_receives_fundamental_intelligence(
    fundamental_env, patched_positions
) -> None:
    patched_positions((XAUUSD,))

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 200
    assert set(body.keys()) == TOP_LEVEL_KEYS
    assert set(body["calendar"].keys()) == CALENDAR_KEYS
    assert set(body["news"].keys()) == NEWS_KEYS
    # The mandatory calendar context is always part of a fundamental answer.
    assert events_of(body), "today's deterministic catalog must produce events"
    assert items_of(body), "today's deterministic news feed must produce items"


def test_response_contract_is_complete_and_factual(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"], query="?symbol=xauusd")

    assert body["focus_symbol"] == "XAUUSD"
    assert body["instruments"] == ["XAUUSD"]
    assert is_utc_iso(body["as_of"])
    assert is_utc_iso(body["window_from"])
    assert is_utc_iso(body["window_to"])

    for item in events_of(body):
        assert set(item["event"].keys()) == EVENT_KEYS
        assert is_utc_iso(item["event"]["timestamp"])
        assert item["event"]["impact"] in {"LOW", "MEDIUM", "HIGH"}
        assert item["overall_relevance"] in RELEVANCE_LEVELS

    for entry in items_of(body):
        assert set(entry.keys()) == NEWS_ITEM_WRAPPER_KEYS
        assert set(entry["item"].keys()) == NEWS_ITEM_KEYS
        assert is_utc_iso(entry["item"]["published_at"])
        assert entry["overall_relevance"] in RELEVANCE_LEVELS
        assert entry["reason"]
        assert isinstance(entry["item"]["instruments"], list)

    for exposure in body["positions"]:
        assert set(exposure.keys()) == EXPOSURE_KEYS
        assert exposure["status"] in {"KNOWN", "UNKNOWN"}
        assert exposure["relevance"] in RELEVANCE_LEVELS
        assert exposure["reason"]
        assert isinstance(exposure["calendar_event_ids"], list)
        assert isinstance(exposure["news_item_ids"], list)


def test_provenance_is_explicit_for_both_sources(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    # Neither source is ever presented as live market data in this step.
    assert body["calendar"]["data_source"] == "fake-development-placeholder"
    assert body["news"]["data_source"] == "fake-development-placeholder"
    assert body["news"]["available"] is True
    assert body["news"]["unavailable_reason"] is None


def test_position_exposure_links_back_to_the_drivers_it_found(
    fundamental_env, patched_positions
) -> None:
    patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    exposure = body["positions"][0]
    assert exposure["symbol"] == "XAUUSD"
    assert exposure["status"] == "KNOWN"
    assert exposure["calendar_event_ids"], "USD calendar events are relevant to XAUUSD"
    assert exposure["news_item_ids"], "USD news is relevant to XAUUSD"


def test_news_relevance_matches_the_instruments_in_play(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"], query="?symbol=eurusd")

    assert body["instruments"] == ["EURUSD", "XAUUSD"]
    for entry in items_of(body):
        # Every matched instrument must be one of the instruments in play, so a
        # relevance label can never reference something the caller did not ask
        # about and does not hold.
        assert set(entry["matched_instruments"]) <= set(body["instruments"])


# --- identity / customer scope -------------------------------------------------------


def test_customer_receives_only_their_own_position_context(fundamental_env, patched_positions) -> None:
    call_threads = patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert [exposure["symbol"] for exposure in body["positions"]] == ["XAUUSD"]
    assert len(call_threads) == 1


def test_each_customer_gets_the_deployments_broker_and_its_own_account(
    fundamental_env, patched_positions
) -> None:
    patched_positions((XAUUSD,))

    _, body_a = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])
    _, body_b = get_fundamental(fundamental_env, fundamental_env["customer_b_id"])

    # Both answers name the same (only) broker — the identity that separates them
    # is the account behind each request, established at authentication.
    assert body_a["broker_id"] == fundamental_env["broker_id"]
    assert body_b["broker_id"] == fundamental_env["broker_id"]


def test_identity_parameters_cannot_change_scope(fundamental_env, patched_positions) -> None:
    patched_positions((EURUSD,))
    user_id = fundamental_env["customer_a_id"]

    _, plain = get_fundamental(fundamental_env, user_id)
    _, with_params = get_fundamental(fundamental_env, user_id, query="?broker_id=2&user_id=999")

    def without_as_of(body: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in body.items() if key != "as_of"}

    # No such parameters exist: scope stays with the authenticated user.
    assert without_as_of(with_params) == without_as_of(plain)
    assert with_params["broker_id"] == fundamental_env["broker_id"]
    assert [exposure["symbol"] for exposure in with_params["positions"]] == ["EURUSD"]


def test_a_focus_symbol_never_widens_the_position_scope(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"], query="?symbol=eurusd")

    # The focus symbol labels the context; it does not read anything the caller
    # does not hold.
    assert body["instruments"] == ["EURUSD", "XAUUSD"]
    assert [exposure["symbol"] for exposure in body["positions"]] == ["XAUUSD"]


# --- request validation ---------------------------------------------------------------


def test_a_blank_focus_symbol_is_rejected_with_422(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    client = fundamental_env["make_app"]()
    with client as c:
        response = c.get(
            "/fundamental-intelligence/today?symbol=%20",
            headers=auth_header(token_for(fundamental_env["customer_a_id"])),
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "symbol must be a non-empty instrument name"}


def test_an_absent_focus_symbol_covers_only_the_holdings(fundamental_env, patched_positions) -> None:
    patched_positions((XAUUSD,))

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 200
    assert body["focus_symbol"] is None
    assert body["instruments"] == ["XAUUSD"]


# --- no news source is a supported state ---------------------------------------------


def test_a_deployment_without_a_news_source_says_so(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_positions((XAUUSD,))
    monkeypatch.setattr(deps, "get_news_service", lambda: None)

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 200
    assert body["news"]["available"] is False
    assert body["news"]["data_source"] is None
    assert body["news"]["unavailable_reason"]
    assert body["news"]["items"] == []
    # Missing news is missing information, never "no risk" and never "no news".
    for exposure in body["positions"]:
        assert exposure["status"] in {"KNOWN", "UNKNOWN"}
    # The mandatory calendar is still served.
    assert events_of(body)


# --- news volume is bounded ------------------------------------------------------------


def test_news_items_are_capped_by_the_configured_limit(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "NEWS_MAX_ITEMS", 2, raising=True)

    _, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert len(items_of(body)) == 2


# --- credential / secret safety ---------------------------------------------------------


def test_response_exposes_no_credentials_or_api_keys(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_positions((XAUUSD,))
    # A configured-looking key must never appear in a response body. The calendar
    # source is pinned to the deterministic development one so the test stays
    # offline while the key is present in the configuration.
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", FAKE_API_KEY, raising=True)
    monkeypatch.setattr(
        app_settings,
        "ECONOMIC_CALENDAR_SOURCE",
        EconomicCalendarSource.DEVELOPMENT_FAKE,
        raising=True,
    )

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])
    text = str(body)

    assert status == 200
    assert FAKE_API_KEY not in text
    assert "password_hash" not in text
    assert "mt5_password_encrypted" not in text
    assert "$2b$" not in text


# --- error handling --------------------------------------------------------------------


def test_mt5_position_failure_returns_generic_503(fundamental_env, patched_positions) -> None:
    patched_positions(error=RuntimeError("terminal unavailable"))

    with fundamental_env["make_app"]() as client:
        response = client.get(
            "/fundamental-intelligence/today",
            headers=auth_header(token_for(fundamental_env["customer_a_id"])),
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Fundamental intelligence service temporarily unavailable"
    }


def test_calendar_failure_returns_generic_503(
    fundamental_env, patched_positions, failing_calendar
) -> None:
    patched_positions((XAUUSD,))

    with fundamental_env["make_app"]() as client:
        response = client.get(
            "/fundamental-intelligence/today",
            headers=auth_header(token_for(fundamental_env["customer_a_id"])),
        )

    # The calendar is mandatory: a fundamental answer cannot exist without it.
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Fundamental intelligence service temporarily unavailable"
    }


def test_news_failure_returns_generic_503(fundamental_env, patched_positions, failing_news) -> None:
    patched_positions((XAUUSD,))

    with fundamental_env["make_app"]() as client:
        response = client.get(
            "/fundamental-intelligence/today",
            headers=auth_header(token_for(fundamental_env["customer_a_id"])),
        )

    # A configured news source that fails is an outage, not "no news".
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Fundamental intelligence service temporarily unavailable"
    }


def test_reading_positions_is_offloaded_off_the_event_loop(fundamental_env, patched_positions) -> None:
    call_threads = patched_positions((XAUUSD,))
    main_thread = threading.get_ident()

    status, _ = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 200
    assert call_threads
    assert all(thread_id != main_thread for thread_id in call_threads)


# --- fail-closed source configuration ---------------------------------------------------


def test_the_production_news_slot_fails_closed_until_a_vendor_exists(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.PRODUCTION, raising=True)

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 503
    assert body == {"detail": "News data source is not configured"}
    # It refused before doing any work.
    assert record == []


def test_the_development_news_source_is_refused_outside_development(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    # "auto" outside development means no news source, so the calendar fails
    # closed first (it is mandatory and has no production source either).
    assert status == 503
    assert body == {"detail": "Economic calendar data source is not configured"}
    assert record == []


def test_an_explicit_development_news_source_is_refused_outside_development(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", "staging", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.DEVELOPMENT_FAKE, raising=True
    )
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.DEVELOPMENT_FAKE, raising=True)

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 503
    assert body == {"detail": "Economic calendar data source is not configured"}


def test_the_development_sources_still_work_in_development(
    fundamental_env, patched_positions, monkeypatch: pytest.MonkeyPatch
) -> None:
    patched_positions((XAUUSD,))
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)

    status, body = get_fundamental(fundamental_env, fundamental_env["customer_a_id"])

    assert status == 200
    assert body["calendar"]["data_source"] == "fake-development-placeholder"
    assert body["news"]["available"] is True


# --- existing API wiring stays intact ----------------------------------------------------


def test_the_endpoint_is_registered_beside_the_existing_ones() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])

    assert "/fundamental-intelligence/today" in paths
    # Regression guard: the pre-existing feature endpoints are still mounted.
    assert {
        "/economic-intelligence/today",
        "/positions",
        "/account-info",
        "/trade-history",
        "/market-data/{symbol}",
        "/agent",
        "/users",
    } <= paths
