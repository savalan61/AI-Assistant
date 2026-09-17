"""One request must not read the same MT5 data twice.

The MT5 Python API authenticates ONE account per process and serializes every
read behind the single terminal session, so a redundant read is a real cost: it
queues behind every other tenant's read and (through the session boundary) pays
its own identity-verification call. This module pins the reads that are
deliberately removed and the one that is deliberately kept.

How the count is measured: a recording fake MT5 is injected into the REAL
MT5SessionManager, and the REAL providers (account, positions, trade history,
instruments, market data) are composed exactly as app/core/dependencies.py
composes them. So every assertion counts the MT5 calls the application actually
made for one request — not a provider or service result a test double returned.
The fake also serves positions from the account it is AUTHENTICATED as, so a
snapshot can never look shared between tenants.

What is pinned here:

* POST /agent performs ONE positions read: the financial context reads the
  snapshot and the economic layer scores calendar relevance against that SAME
  snapshot (previously it read positions again).
* GET /financial-research/today resolves each requested instrument ONCE: the
  resolution the router already performed is reused for grading (previously
  build_research resolved the catalog again).
* GET /market-data/{symbol} KEEPS its resolution read: that read is not a
  duplicate of the candle read — it is how the caller's spelling becomes the
  broker's own (``xauusd`` -> ``XAUUSD.r``), so removing it would break every
  suffixed catalog. It is pinned at exactly one read so a future change cannot
  silently double it.
* Reuse is strictly request-local: consecutive requests each read for
  themselves (no cross-request or cross-tenant cache), and a failing read still
  propagates to the existing 503 boundary.

Everything here is offline: a fake terminal, no network, no real MT5, no
database, no credentials. No pytest asyncio plugin — the async handlers are
driven with asyncio.run, as the rest of the suite does.
"""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.agent_router import AgentRequest, handle_agent_message
from app.api.agent_router import AgentResponse as AgentMessageResponse
from app.api.fundamental_intelligence_router import get_todays_financial_research
from app.api.market_data_router import get_market_data
from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import MT5AccountCredentials, MT5SessionManager
from app.db.models import User, UserRole
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.mt5_account_info import MT5AccountInfoProvider
from app.providers.mt5_instruments import MT5InstrumentProvider
from app.providers.mt5_market_data import MT5MarketDataProvider
from app.providers.mt5_positions import MT5PositionProvider
from app.providers.mt5_trade_history import MT5TradeHistoryProvider
from app.services.account import AccountInfoService
from app.services.agent import AgentService, AgentUsageLimiter
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.financial_context import FinancialContextService
from app.services.fundamental_intelligence import (
    FinancialResearchService,
    FundamentalIntelligenceService,
)
from app.services.instruments import InstrumentService
from app.services.market import MarketDataService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
PASSWORD = "mt5-investor-password-under-test"
SERVER = "BrokerA-Live"


@pytest.fixture(autouse=True)
def test_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test-only encryption key: the credentials must round-trip for real."""
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


class RecordingMT5:
    """A recording stand-in for the MT5 C-extension surface.

    Every function the application may call is recorded BY NAME, so a test can
    count the real MT5 calls one request made. Positions are served from the
    account the fake is currently authenticated as — a real terminal behaves
    exactly that way — so a test can prove one tenant never observes another's
    snapshot.
    """

    # Values the real MT5 module exposes and the providers read.
    TIMEFRAME_M1 = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    DEAL_ENTRY_OUT = 1
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1

    def __init__(
        self,
        *,
        positions_by_login: dict[int, tuple[SimpleNamespace, ...]] | None = None,
        symbols: tuple[SimpleNamespace, ...] = (),
        positions_error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.positions_by_login = positions_by_login or {}
        self.symbols = symbols
        self.positions_error = positions_error
        self.authenticated: tuple[str, int] | None = None

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self.authenticated = (str(kwargs["server"]), int(kwargs["login"]))  # type: ignore[arg-type]
        return True

    def login(self, **kwargs: object) -> object:
        self._record("login")
        self.authenticated = (str(kwargs["server"]), int(kwargs["login"]))  # type: ignore[arg-type]
        return True

    def last_error(self) -> tuple[int, str]:
        self._record("last_error")
        return (-1, "simulated MT5 failure")

    def account_info(self) -> object:
        """The terminal's own answer about the account it is on.

        Used twice per acquire: once by the session boundary's identity
        verification and once as the account read itself.
        """
        self._record("account_info")
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return None
        server, login = self.authenticated
        return SimpleNamespace(
            login=login,
            name="Test Trader",
            balance=10000.0,
            equity=10050.0,
            margin=250.0,
            margin_free=9800.0,
            margin_level=4020.0,
            currency="USD",
            server=server,
        )

    def positions_get(self) -> object:
        self._record("positions_get")
        if self.positions_error is not None:
            raise self.positions_error
        if self.authenticated is None:  # pragma: no cover - every read authenticates first
            return ()
        return self.positions_by_login.get(self.authenticated[1], ())

    def history_deals_get(self, from_time: object, to_time: object) -> tuple:
        self._record("history_deals_get")
        return ()

    def history_orders_get(self, ticket: object) -> tuple:  # pragma: no cover - no deals here
        self._record("history_orders_get")
        return ()

    def symbol_info(self, symbol: str) -> object:
        self._record("symbol_info")
        return next((candidate for candidate in self.symbols if candidate.name == symbol), None)

    def symbols_get(self) -> tuple:
        self._record("symbols_get")
        return tuple(self.symbols)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> tuple:
        self._record("copy_rates_from_pos")
        return ((1767000000, 3642.5, 3648.2, 3640.0, 3646.1, 120.0),)


def mt5_symbol(name: str) -> SimpleNamespace:
    """A realistic MT5 symbol record (attribute access, not a dict)."""
    return SimpleNamespace(
        name=name,
        digits=2,
        trade_mode=4,
        description=f"{name} instrument",
        path="Metals",
        currency_base="XAU",
        currency_profit="USD",
    )


def mt5_position(ticket: int, symbol: str = "XAUUSD") -> SimpleNamespace:
    """A realistic MT5 open position row."""
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=RecordingMT5.DEAL_TYPE_BUY,
        volume=0.10,
        price_open=3642.50,
        price_current=3648.20,
        profit=57.00,
    )


def make_user(broker_id: int = 1, user_id: int = 7) -> User:
    return User(
        id=user_id,
        broker_id=broker_id,
        login="10001",
        password_hash="x-not-a-real-hash",
        is_active=True,
        role=UserRole.CUSTOMER,
    )


class Stack(SimpleNamespace):
    """The REAL provider/service composition for one tenant, over one fake terminal."""

    agent: AgentService
    economic: EconomicIntelligenceService
    research: FinancialResearchService
    market: MarketDataService


def build_stack(
    mt5: RecordingMT5,
    session: MT5SessionManager,
    *,
    login: int = 10001,
    server: str = SERVER,
) -> Stack:
    """Compose every read path exactly as app/core/dependencies.py does.

    The same real providers, the same real services and the same one
    process-wide session; only the terminal is fake.
    """
    credentials = MT5AccountCredentials(
        login=login, server=server, password_encrypted=encrypt_secret(PASSWORD)
    )
    positions = PositionService(MT5PositionProvider(session_manager=session, credentials=credentials))
    account = AccountInfoService(MT5AccountInfoProvider(session_manager=session, credentials=credentials))
    trade_history = TradeHistoryService(
        MT5TradeHistoryProvider(session_manager=session, credentials=credentials)
    )
    instruments = InstrumentService(MT5InstrumentProvider(session_manager=session, credentials=credentials))
    economic = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=positions,
    )
    research = FinancialResearchService(news_service=None, instrument_service=instruments)
    agent = AgentService(
        financial_context_service=FinancialContextService(
            account_service=account,
            position_service=positions,
            trade_history_service=trade_history,
        ),
        llm_provider=FakeLLMProvider(),
        economic_intelligence_service=economic,
        fundamental_intelligence_service=FundamentalIntelligenceService(news_service=None),
        financial_research_service=research,
    )
    return Stack(
        session=session,
        credentials=credentials,
        positions=positions,
        account=account,
        trade_history=trade_history,
        instruments=instruments,
        economic=economic,
        research=research,
        market=MarketDataService(
            MT5MarketDataProvider(session_manager=session, credentials=credentials),
            instrument_service=instruments,
        ),
        agent=agent,
    )


def submit_agent(stack: Stack, message: str = "What is my XAUUSD exposure?") -> AgentMessageResponse:
    """Run the REAL /agent router coroutine (one request) to completion."""
    return asyncio.run(
        handle_agent_message(
            AgentRequest(message=message),
            make_user(),
            stack.agent,
            AgentUsageLimiter(daily_limit=5),
        )
    )


# --- POST /agent: one positions read per request ------------------------------


def test_agent_request_reads_positions_exactly_once() -> None:
    """The financial context's snapshot is reused for calendar relevance."""
    mt5 = RecordingMT5(positions_by_login={10001: (mt5_position(1),)}, symbols=(mt5_symbol("XAUUSD"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    response = submit_agent(stack)

    assert response.answer  # the request completed end to end
    # The regression: positions used to be read twice (financial context read,
    # then the economic layer read again for per-position relevance).
    assert mt5.calls.count("positions_get") == 1
    # The whole request, counted at the terminal: one initialize(), four acquires
    # (account, positions, trade history, focus-instrument resolution) whose
    # identity verification is one account_info each, the account read itself,
    # positions_get, history_deals_get and symbol_info = 9 MT5 calls. Each removed
    # duplicate read also removed its acquire, so a request like this cost 13
    # MT5 calls before the fix (positions read twice, catalog resolved twice).
    assert mt5.calls.count("account_info") == 5
    assert len(mt5.calls) == 9


def test_the_reused_snapshot_is_the_one_the_response_reports() -> None:
    """Reuse must not change what the context says — same positions, one read."""
    mt5 = RecordingMT5(
        positions_by_login={10001: (mt5_position(1, "XAUUSD"), mt5_position(2, "XAUUSD"))},
        symbols=(mt5_symbol("XAUUSD"),),
    )
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    prepared = stack.agent.prepare("What is my XAUUSD exposure?", broker_id=1, now=AS_OF)

    assert mt5.calls.count("positions_get") == 1
    # The economic layer scored the SAME snapshot the financial context reports
    # (it orders it deterministically, so compare values, not identity).
    assert prepared.economic is not None
    assert tuple(sorted(p.ticket for p in prepared.context.positions)) == (1, 2)
    assert tuple(sorted(p.ticket for p in prepared.economic.positions)) == (1, 2)
    assert prepared.economic.position_symbols == ("XAUUSD",)
    # And the response envelope still carries that one snapshot.
    assert tuple(sorted(p.ticket for p in prepared.context.positions)) == (1, 2)


def test_the_standalone_economic_path_still_reads_its_own_positions() -> None:
    """A caller with no snapshot (GET /economic-intelligence/today) still reads.

    The reuse is opt-in and request-local: the default path is unchanged, and two
    separate calls are two separate reads — there is no cache between them.
    """
    mt5 = RecordingMT5(positions_by_login={10001: (mt5_position(1),)}, symbols=(mt5_symbol("XAUUSD"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    first = stack.economic.build_today_context(now=AS_OF)
    second = stack.economic.build_today_context(now=AS_OF)

    assert first.positions == second.positions
    assert mt5.calls.count("positions_get") == 2


# --- GET /financial-research/today: each instrument resolved once --------------


def test_research_request_resolves_each_symbol_once() -> None:
    """The router's resolution is reused for grading instead of re-resolving."""
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD"), mt5_symbol("USOIL")))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    context = asyncio.run(
        get_todays_financial_research(
            WINDOW_FROM,
            WINDOW_TO,
            "XAUUSD,USOIL",
            make_user(),
            stack.research,
        )
    )

    assert sorted(context.focus_symbols) == ["USOIL", "XAUUSD"]
    # One lookup per requested instrument. It used to be two per instrument:
    # the router resolved, then build_research resolved the same catalog again.
    assert mt5.calls.count("symbol_info") == 2
    assert mt5.calls.count("symbols_get") == 0


def test_research_request_reads_a_decorated_catalog_once() -> None:
    """A broker-suffixed catalog is scanned once, not once per resolution."""
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD.r"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    context = asyncio.run(
        get_todays_financial_research(WINDOW_FROM, WINDOW_TO, "XAUUSD", make_user(), stack.research)
    )

    assert list(context.focus_symbols) == ["XAUUSD.r"]
    # The exact spelling is unknown to the terminal, so resolution falls back to
    # the catalog; that full scan must happen ONCE for the request.
    assert mt5.calls.count("symbol_info") == 1
    assert mt5.calls.count("symbols_get") == 1


def test_an_unknown_research_symbol_fails_closed_after_one_resolution() -> None:
    """The 404 path is unchanged — and it does not resolve twice to reach it."""
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            get_todays_financial_research(
                WINDOW_FROM, WINDOW_TO, "NOTREAL", make_user(), stack.research
            )
        )

    assert excinfo.value.status_code == 404
    assert mt5.calls.count("symbol_info") == 1
    assert mt5.calls.count("symbols_get") == 1


# --- GET /market-data/{symbol}: the resolution read is INTENTIONALLY kept ------


def test_market_data_keeps_exactly_one_resolution_read() -> None:
    """The resolution read is not a duplicate of the candle read.

    It answers a different question — which spelling the broker actually lists —
    and the candle read cannot be asked for a symbol the terminal does not have
    (``copy_rates_from_pos`` on an unknown or differently-cased name returns no
    rates). Removing it would therefore break every suffixed catalog, so it is
    retained and pinned at exactly ONE read per request.
    """
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    candle = asyncio.run(get_market_data("XAUUSD", make_user(), stack.market))

    assert candle.close > 0  # the candle came back
    assert mt5.calls.count("symbol_info") == 1  # resolution: caller spelling -> broker spelling
    assert mt5.calls.count("copy_rates_from_pos") == 1  # the candle itself
    assert mt5.calls.count("symbols_get") == 0


def test_market_data_resolution_still_uses_the_broker_spelling() -> None:
    """Retaining the read is what keeps a decorated catalog working."""
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD.r"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    asyncio.run(get_market_data("xauusd", make_user(), stack.market))

    assert mt5.calls.count("symbol_info") == 1
    assert mt5.calls.count("symbols_get") == 1
    assert mt5.calls.count("copy_rates_from_pos") == 1


# --- reuse is request-local: no cross-request or cross-tenant leakage ----------


def test_consecutive_requests_each_read_for_themselves() -> None:
    """No cache: the second request performs its own read, not the first's."""
    mt5 = RecordingMT5(positions_by_login={10001: (mt5_position(1),)}, symbols=(mt5_symbol("XAUUSD"),))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    submit_agent(stack)
    submit_agent(stack)

    assert mt5.calls.count("positions_get") == 2


def test_two_tenants_never_share_a_snapshot() -> None:
    """The snapshot is an argument for one request, never process-wide state.

    Both tenants run over the SAME process-wide session (as production does), so
    the second request has to authenticate onto its own account and read its own
    positions; the fake serves positions per authenticated login, which is how a
    real terminal behaves.
    """
    mt5 = RecordingMT5(
        positions_by_login={10001: (mt5_position(11, "XAUUSD"),), 10002: (mt5_position(22, "EURUSD"),)},
        symbols=(mt5_symbol("XAUUSD"), mt5_symbol("EURUSD")),
    )
    session = MT5SessionManager(mt5_api=mt5)
    tenant_a = build_stack(mt5, session, login=10001)
    tenant_b = build_stack(mt5, session, login=10002, server="BrokerA-Live")

    prepared_a = tenant_a.agent.prepare("What is my exposure?", broker_id=1, now=AS_OF)
    prepared_b = tenant_b.agent.prepare("What is my exposure?", broker_id=2, now=AS_OF)

    assert {p.symbol for p in prepared_a.context.positions} == {"XAUUSD"}
    assert {p.symbol for p in prepared_b.context.positions} == {"EURUSD"}
    assert {p.symbol for p in prepared_a.economic.positions} == {"XAUUSD"}  # type: ignore[union-attr]
    assert {p.symbol for p in prepared_b.economic.positions} == {"EURUSD"}  # type: ignore[union-attr]
    # Each request read positions once: two requests, two reads, no sharing.
    assert mt5.calls.count("positions_get") == 2


# --- failure propagation at the same boundary ---------------------------------


def test_a_failed_positions_read_still_maps_to_the_agent_503() -> None:
    """Reusing the snapshot must not swallow a read failure."""
    mt5 = RecordingMT5(positions_error=RuntimeError("MT5 positions request failed"))
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    with pytest.raises(HTTPException) as excinfo:
        submit_agent(stack)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Agent service temporarily unavailable"
    # Attempted once, then propagated: no retry, no second read, no silent reuse.
    assert mt5.calls.count("positions_get") == 1


def test_a_failed_research_resolution_still_maps_to_the_research_503() -> None:
    """The single resolution read keeps the existing failure boundary."""
    mt5 = RecordingMT5(symbols=(mt5_symbol("XAUUSD"),))

    def failing_symbol_info(symbol: str) -> object:
        mt5.calls.append("symbol_info")
        raise RuntimeError("MT5 instrument lookup failed")

    mt5.symbol_info = failing_symbol_info  # type: ignore[method-assign]
    stack = build_stack(mt5, MT5SessionManager(mt5_api=mt5))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            get_todays_financial_research(
                WINDOW_FROM, WINDOW_TO, "XAUUSD", make_user(), stack.research
            )
        )

    assert excinfo.value.status_code == 503
    assert mt5.calls.count("symbol_info") == 1
