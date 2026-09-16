"""Tests for GET /financial-research/today (auth, window validation, contract).

Require none of: real MT5, PostgreSQL, network, credentials, a real calendar
source, a news vendor or an LLM. The get_db dependency is overridden with a
per-test file-based async SQLite database; the REAL authentication dependency
runs (real JWT decode + real database lookup). Both public-data sources are
pinned to the deterministic development sources (calendar AUTO with the
QuantGist key cleared, news AUTO with the Alpha Vantage key cleared), so no
request leaves the process.

Step 51 gives the research endpoint its instrument-resolution step, so the suite
replaces the composition-root MT5 provider CLASS seam with the deterministic
instrument fake: the real InstrumentService, the real resolution rules and the
real research service run, with no terminal, no credentials and no network.

The suite pins the HTTP boundary guarantees: authentication, the explicit UTC
window contract (naive 400, inverted 400), focus-symbol validation (422), the
broker-canonical spelling echoed for a resolved instrument, the deterministic
404 for an instrument this broker does not offer, the generic fail-closed 503
on a failing catalog or news source, the response contract with provenance, the
explicit unavailable state when a deployment has no news source, tenant
isolation, and the fact that the only tenant fact in a response is the caller's
own broker_id echo.
"""
import asyncio
from datetime import UTC, datetime
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.fundamental_intelligence_router import router
from app.core.config import NewsSource, settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.instrument import Instrument, TradeMode
from app.providers.news import NewsItem, NewsProvider

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
# Obvious marker instead of a credential: it must never reach a response.
FAKE_API_KEY = "av_test_unit-only-not-a-real-key"

WINDOW_FROM = "2026-09-16T00:00:00Z"
WINDOW_TO = "2026-09-17T00:00:00Z"

RESEARCH_TOP_LEVEL_KEYS = {
    "broker_id",
    "as_of",
    "window_from",
    "window_to",
    "focus_symbols",
    "instruments",
    "news",
}
RESEARCH_NEWS_KEYS = {"available", "data_source", "unavailable_reason", "items"}
RESEARCH_ITEM_KEYS = {"item", "overall_relevance", "matched_instruments", "reason"}
RELEVANCE_LEVELS = {"RELEVANT", "POTENTIALLY_RELEVANT", "NOT_OBVIOUSLY_RELEVANT"}


class EmptyNewsProvider(NewsProvider):
    """Development-shaped provider that always publishes nothing (offline)."""

    source = "test-empty-news"

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        return ()


class FailingNewsProvider(NewsProvider):
    """News provider whose source is unavailable (infrastructure failure)."""

    source = "test-failing-news"

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        raise RuntimeError("news source unavailable")


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)
    # Pin the news source to the deterministic development fake (offline,
    # reproducible) regardless of what a developer's local .env holds.
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.AUTO, raising=True)
    monkeypatch.setattr(app_settings, "ALPHA_VANTAGE_API_KEY", "", raising=True)


@pytest.fixture(autouse=True)
def patched_trade_catalog(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Resolve research instruments over the deterministic fake catalog.

    The composition root builds the instrument provider from the authenticated
    tenant's credentials; the class seam is replaced here so the SAME resolution
    architecture runs without a terminal. The tenant credentials each provider
    was built with are recorded, so tenant binding can be asserted.
    """
    constructions: list[Any] = []

    class FakeMT5InstrumentProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            constructions.append(credentials)
            self._inner = FakeInstrumentProvider()

        def get_instrument(self, symbol: str):
            return self._inner.get_instrument(symbol)

        def list_instruments(self):
            return self._inner.list_instruments()

    monkeypatch.setattr(deps, "MT5InstrumentProvider", FakeMT5InstrumentProvider)
    return constructions


@pytest.fixture()
def research_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/research.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="FI-A")
            broker_b = Broker(name="Broker B", code="FI-B")
            session.add_all([broker_a, broker_b])
            await session.commit()
            customer_a = User(
                broker_id=broker_a.id,
                login="20001",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            customer_b = User(
                broker_id=broker_b.id,
                login="20002",
                password_hash="x" * 60,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            session.add_all([customer_a, customer_b])
            await session.commit()
            return {
                "broker_a_id": broker_a.id,
                "broker_b_id": broker_b.id,
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

    yield {"make_app": make_app, **ids}
    asyncio.run(engine.dispose())


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(user_id: int) -> str:
    return create_access_token(str(user_id))


def get_research(env, user_id: int, query: str) -> tuple[int, dict[str, Any]]:
    client = env["make_app"]()
    with client as c:
        response = c.get(f"/financial-research/today{query}", headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


# --- authentication ---------------------------------------------------------------------


def test_an_unauthenticated_request_is_rejected(research_env) -> None:
    client = research_env["make_app"]()
    with client as c:
        response = c.get(f"/financial-research/today?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD")
    assert response.status_code == 401


def test_an_invalid_token_is_rejected(research_env) -> None:
    client = research_env["make_app"]()
    with client as c:
        response = c.get(
            f"/financial-research/today?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
            headers=auth_header("not-a-token"),
        )
    assert response.status_code == 401


# --- window validation --------------------------------------------------------------------


def test_a_naive_window_boundary_is_a_400(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        "?from=2026-09-16T00:00:00&to=2026-09-17T00:00:00Z&symbol=XAUUSD",
    )

    assert status == 400


def test_an_inverted_window_is_a_400(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_TO}&to={WINDOW_FROM}&symbol=XAUUSD",
    )

    assert status == 400


# --- focus symbol validation ---------------------------------------------------------------


def test_a_missing_symbol_is_a_422(research_env) -> None:
    client = research_env["make_app"]()
    with client as c:
        response = c.get(
            f"/financial-research/today?from={WINDOW_FROM}&to={WINDOW_TO}",
            headers=auth_header(token_for(research_env["customer_a_id"])),
        )

    assert response.status_code == 422


def test_a_symbol_that_names_no_instrument_is_a_422(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=%20,%20",
    )

    assert status == 422


# --- the 200 contract -----------------------------------------------------------------------


def test_a_valid_request_returns_the_graded_research_contract(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 200
    assert set(body) == RESEARCH_TOP_LEVEL_KEYS
    assert set(body["news"]) == RESEARCH_NEWS_KEYS
    assert body["focus_symbols"] == ["XAUUSD"]
    assert body["instruments"] == ["XAUUSD"]
    # The requested window is echoed exactly (UTC).
    assert body["window_from"] == "2026-09-16T00:00:00Z"
    assert body["window_to"] == "2026-09-17T00:00:00Z"
    # The development source is available and named by its provenance marker.
    assert body["news"]["available"] is True
    assert body["news"]["data_source"] == "fake-development-placeholder"
    assert body["news"]["unavailable_reason"] is None
    # The fake catalog materializes on the requested dates, so items exist and
    # every one is graded with a discrete relevance level and a reason.
    assert body["news"]["items"]
    for wrapper in body["news"]["items"]:
        assert set(wrapper) == RESEARCH_ITEM_KEYS
        assert wrapper["overall_relevance"] in RELEVANCE_LEVELS
        assert wrapper["reason"]
        assert set(wrapper["item"]) == {
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


def test_a_direct_gold_item_is_relevant_for_xauusd(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    gold_items = [
        wrapper
        for wrapper in body["news"]["items"]
        if "Gold ETF flows" in wrapper["item"]["title"]
    ]
    assert gold_items, "the deterministic development catalog contains a gold item"
    assert gold_items[0]["overall_relevance"] == "RELEVANT"
    assert gold_items[0]["matched_instruments"] == ["XAUUSD"]


def test_multiple_focus_symbols_are_normalized_and_sorted(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=usoil,XAUUSD,USOIL",
    )

    assert status == 200
    assert body["focus_symbols"] == ["USOIL", "XAUUSD"]
    assert body["instruments"] == ["USOIL", "XAUUSD"]


def test_the_same_item_can_be_graded_differently_per_focus_instrument(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD,NAS100",
    )

    assert status == 200
    gold = next(
        (
            wrapper
            for wrapper in body["news"]["items"]
            if "Gold ETF flows" in wrapper["item"]["title"]
        ),
        None,
    )
    assert gold is not None
    assert gold["matched_instruments"] == ["XAUUSD"]
    assert gold["overall_relevance"] == "RELEVANT"


def test_an_empty_feed_is_a_normal_200_with_available_true(research_env, monkeypatch) -> None:
    monkeypatch.setattr(deps, "FakeNewsProvider", EmptyNewsProvider)

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 200
    assert body["news"]["available"] is True
    assert body["news"]["data_source"] == "test-empty-news"
    assert body["news"]["items"] == []


# --- unavailable / failing source ------------------------------------------------------------


def test_a_deployment_without_a_news_source_reports_unavailable(
    research_env, monkeypatch
) -> None:
    monkeypatch.setattr(deps, "get_news_service", lambda: None)

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 200
    assert body["news"]["available"] is False
    assert body["news"]["data_source"] is None
    assert body["news"]["unavailable_reason"] is not None
    assert "No news source is configured" in body["news"]["unavailable_reason"]
    assert body["news"]["items"] == []


def test_a_failing_news_source_fails_closed_with_the_generic_503(
    research_env, monkeypatch
) -> None:
    monkeypatch.setattr(deps, "FakeNewsProvider", FailingNewsProvider)

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 503
    assert body["detail"] == "Financial research service temporarily unavailable"


# --- Step 51: instrument resolution against the tenant's own broker catalog -------------------


def test_a_requested_spelling_is_answered_with_the_brokers_canonical_symbol(
    research_env,
) -> None:
    # The caller's case is not the broker's: the broker lists XAUUSD.r, so that
    # is the spelling research is graded against and the spelling echoed back.
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=xauusd.r",
    )

    assert status == 200
    assert body["focus_symbols"] == ["XAUUSD.r"]
    assert body["instruments"] == ["XAUUSD.r"]


def test_the_brokers_canonical_spelling_still_reaches_the_relevance_profiles(
    research_env,
) -> None:
    # Step 48 profiles stay optional enhancements ON TOP of a resolved symbol:
    # XAUUSD.r resolves the XAUUSD profile, so a direct gold item is still the
    # strongest level, now graded against the broker's own spelling.
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=xauusd.r",
    )

    gold = next(
        (
            wrapper
            for wrapper in body["news"]["items"]
            if "Gold ETF flows" in wrapper["item"]["title"]
        ),
        None,
    )
    assert status == 200
    assert gold is not None
    assert gold["overall_relevance"] == "RELEVANT"
    # The context reports the broker's own spelling, while the classification
    # labels an item with the upper-cased instrument name it matched — the
    # labelling contract the relevance layer has always used (it upper-cases the
    # labels it compares, which is why a broker-spelled position already matched
    # under upper case). The match itself is case-insensitive; only the echoed
    # label differs (CURRENT_CHECKPOINT.md known issue 21).
    assert gold["matched_instruments"] == ["XAUUSD.R"]
    assert body["focus_symbols"] == ["XAUUSD.r"]


@pytest.mark.parametrize("symbol", ["AAPL", "LVMH", "BTCUSD", "COFFEE", "NICKEL"])
def test_an_arbitrary_broker_symbol_is_researchable_without_a_profile(
    research_env, symbol: str
) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol={symbol}",
    )

    assert status == 200
    assert body["focus_symbols"] == [symbol]
    # Researchable does not mean "relevant": every item is still graded against
    # this symbol and may legitimately be NOT_OBVIOUSLY_RELEVANT.
    for wrapper in body["news"]["items"]:
        assert wrapper["overall_relevance"] in RELEVANCE_LEVELS
        assert symbol in wrapper["matched_instruments"] or not wrapper["matched_instruments"]


def suffix_only_broker(monkeypatch: pytest.MonkeyPatch, *symbols: str) -> None:
    """Patch the catalog seam with a broker that lists exactly ``symbols``.

    Used for the brokers whose whole catalog is suffixed (XAUUSD.r), where the
    base symbol a user or the focus detector names does not exist verbatim.
    """

    class SuffixOnlyInstrumentProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            self._inner = FakeInstrumentProvider(
                instruments=tuple(
                    Instrument(
                        symbol=symbol,
                        name=None,
                        asset_class=None,
                        base_currency=None,
                        quote_currency=None,
                        digits=2,
                        trade_mode=TradeMode.FULL,
                    )
                    for symbol in symbols
                )
            )

        def get_instrument(self, symbol: str):
            return self._inner.get_instrument(symbol)

        def list_instruments(self):
            return self._inner.list_instruments()

    monkeypatch.setattr(deps, "MT5InstrumentProvider", SuffixOnlyInstrumentProvider)


def test_a_broker_that_only_lists_a_suffixed_spelling_still_answers(
    research_env, monkeypatch
) -> None:
    # The regression this fix exists for: the caller (or the agent's focus
    # detector) names XAUUSD and the broker's whole catalog is suffixed.
    suffix_only_broker(monkeypatch, "XAUUSD.r")

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 200
    assert body["focus_symbols"] == ["XAUUSD.r"]
    assert body["instruments"] == ["XAUUSD.r"]
    gold = next(
        (
            wrapper
            for wrapper in body["news"]["items"]
            if "Gold ETF flows" in wrapper["item"]["title"]
        ),
        None,
    )
    assert gold is not None and gold["overall_relevance"] == "RELEVANT"


def test_two_suffixed_variants_of_the_requested_base_are_a_404(
    research_env, monkeypatch
) -> None:
    # XAUUSD.r and XAUUSD.m are different instruments: the request is refused
    # rather than silently researched as one of them.
    suffix_only_broker(monkeypatch, "XAUUSD.r", "XAUUSD.m")

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 404
    assert body == {"detail": "Instrument unavailable for the requested symbol"}


def test_an_instrument_the_broker_does_not_offer_is_a_404(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=NOSUCHSYMBOL",
    )

    assert status == 404
    # The same deterministic client error the instrument endpoints use: no
    # provider internals, no broker identity, nothing about the catalog.
    assert body == {"detail": "Instrument unavailable for the requested symbol"}


def test_one_unknown_instrument_fails_the_whole_request(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD,NOSUCHSYMBOL",
    )

    assert status == 404
    # Fail closed: no partial result, and the known symbol is never echoed as if
    # the request had succeeded.
    assert body == {"detail": "Instrument unavailable for the requested symbol"}
    assert "focus_symbols" not in body


def test_a_catalog_failure_is_the_generic_503(research_env, monkeypatch) -> None:
    class FailingInstrumentProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

        def get_instrument(self, symbol: str):
            raise RuntimeError("MT5 instrument lookup failed")

        def list_instruments(self):
            raise RuntimeError("MT5 instrument catalog request failed")

    monkeypatch.setattr(deps, "MT5InstrumentProvider", FailingInstrumentProvider)

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 503
    assert body == {"detail": "Financial research service temporarily unavailable"}


def test_a_tenant_without_a_usable_mt5_session_gets_the_generic_503(research_env, monkeypatch) -> None:
    """The real provider runs here: no credentials means the session refuses.

    Resolution is broker infrastructure, so an unavailable MT5 catalog is a
    503 (never an empty or invented instrument set), exactly as for every other
    MT5-backed endpoint.
    """
    monkeypatch.undo()  # drop the catalog fake for this test: the real seam runs

    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 503
    assert body == {"detail": "Financial research service temporarily unavailable"}


def test_each_request_resolves_against_the_authenticated_tenants_own_catalog(
    research_env, patched_trade_catalog
) -> None:
    get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )
    get_research(
        research_env,
        research_env["customer_b_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    # Each request composed its own provider from the authenticated user's own
    # MT5 identity: a caller cannot choose whose broker catalog is read.
    assert [getattr(credentials, "login", None) for credentials in patched_trade_catalog] == [
        20001,
        20002,
    ]


# --- tenant isolation / secret safety -------------------------------------------------------


def test_the_only_tenant_fact_in_a_response_is_the_callers_own_broker_id(
    research_env,
) -> None:
    status_a, body_a = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )
    status_b, body_b = get_research(
        research_env,
        research_env["customer_b_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status_a == status_b == 200
    assert body_a["broker_id"] == research_env["broker_a_id"]
    assert body_b["broker_id"] == research_env["broker_b_id"]
    # The graded items are public market data: identical for both tenants.
    assert body_a["news"]["items"] == body_b["news"]["items"]


def test_no_api_key_or_credential_appears_in_any_response(research_env) -> None:
    status, body = get_research(
        research_env,
        research_env["customer_a_id"],
        f"?from={WINDOW_FROM}&to={WINDOW_TO}&symbol=XAUUSD",
    )

    assert status == 200
    rendered = str(body)
    assert FAKE_API_KEY not in rendered
    assert "password" not in rendered.lower()
    assert "mt5_password" not in rendered.lower()
