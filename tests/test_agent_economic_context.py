"""Step 45 tests: economic intelligence composed into the Agent pipeline.

Covers the agent ↔ economic-calendar composition only: the calendar data that
reaches the LLM prompt, its relevance/provenance, the single reference time
shared by both contexts, the empty-calendar case, failure propagation through
the existing error boundary, and the unchanged behaviour when no economic
service is wired.

Require none of: real MT5, PostgreSQL, network, credentials, or an external LLM.
The economic context is either a canned ``EconomicIntelligenceContext`` or the
real ``EconomicIntelligenceService`` over the deterministic placeholder calendar
provider and fake position/trade providers. No pytest asyncio plugin.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import settings as app_settings
from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.llm import LLMProvider
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeCloseReason, TradeHistoryEntry, TradeType
from app.services.account import AccountInfoService
from app.services.agent import AgentService, OutboundDataPolicy, build_prompt
from app.services.agent.prompt import PromptTooLargeError
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    EconomicIntelligenceService,
    EventIntelligence,
    RelevanceLevel,
)
from app.services.financial_context import (
    DEFAULT_TRADE_HISTORY_DAYS,
    FinancialContext,
    FinancialContextService,
)
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

ACCOUNT = AccountInfo(
    login=10001,
    name="Test Trader",
    balance=Decimal("10000.00"),
    equity=Decimal("10050.00"),
    margin=Decimal("250.00"),
    free_margin=Decimal("9800.00"),
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

XAUUSD = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=Decimal("0.10"),
    open_price=Decimal("3642.50"),
    current_price=Decimal("3648.20"),
    profit=Decimal("57.00"),
)

TRADE = TradeHistoryEntry(
    ticket=246802468,
    order_ticket=987654321,
    symbol="XAUUSD",
    type=TradeType.BUY,
    volume=Decimal("0.10"),
    price=Decimal("3648.20"),
    profit=Decimal("57.00"),
    time=datetime(2026, 9, 15, 12, 30, 0, tzinfo=UTC),
    close_reason=TradeCloseReason.TP,
    stop_loss=Decimal("3635.00"),
    take_profit=Decimal("3650.00"),
)

CPI_EVENT = EconomicEvent(
    event_id="evt-usd-cpi",
    timestamp=datetime(2026, 9, 16, 12, 30, tzinfo=UTC),
    currency="USD",
    title="US Consumer Price Index (CPI) YoY",
    impact=EventImpact.HIGH,
    forecast="3.1%",
    previous="3.2%",
    actual=None,
)

IP_EVENT = EconomicEvent(
    event_id="evt-eur-ip",
    timestamp=datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
    currency="EUR",
    title="Euro Area Industrial Production MoM",
    impact=EventImpact.MEDIUM,
    forecast="0.3%",
    previous="-0.2%",
    actual="0.1%",
)


class _FakeAccountInfoProvider(AccountInfoProvider):
    def __init__(self, account: AccountInfo = ACCOUNT) -> None:
        self.account = account
        self.call_count = 0

    def get_account_info(self) -> AccountInfo:
        self.call_count += 1
        return self.account


class _RecordingFinancialContextService(FinancialContextService):
    """Test double recording build() calls (no MT5, no database)."""

    def __init__(self, context: FinancialContext) -> None:
        self.context = context
        self.calls: list[datetime | None] = []

    def build(
        self,
        broker_id: int,
        trade_history_days: int = DEFAULT_TRADE_HISTORY_DAYS,
        now: datetime | None = None,
    ) -> FinancialContext:
        self.calls.append(now)
        return self.context


class _RecordingEconomicService(EconomicIntelligenceService):
    """Test double recording build_today_context() calls; returns or raises."""

    def __init__(
        self,
        context: EconomicIntelligenceContext | None = None,
        error: Exception | None = None,
    ) -> None:
        self.context = context
        self.error = error
        self.calls: list[datetime | None] = []

    def build_today_context(
        self, minimum_impact: EventImpact | None = None, now: datetime | None = None
    ) -> EconomicIntelligenceContext:
        self.calls.append(now)
        if self.error is not None:
            raise self.error
        assert self.context is not None
        return self.context


def make_financial_context(broker_id: int = 1) -> FinancialContext:
    return FinancialContext(
        broker_id=broker_id,
        as_of=AS_OF,
        account=ACCOUNT,
        positions=(XAUUSD,),
        trade_history=(TRADE,),
        portfolio_intelligence=build_portfolio_intelligence(ACCOUNT, (XAUUSD,), AS_OF),
    )


def make_economic_context(
    *,
    events: tuple[EventIntelligence, ...] = (),
    data_source: str = "test-calendar-source",
) -> EconomicIntelligenceContext:
    return EconomicIntelligenceContext(
        as_of=AS_OF,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source=data_source,
        position_symbols=("XAUUSD",),
        events=events,
    )


def event_intelligence(
    event: EconomicEvent, relevance: RelevanceLevel = RelevanceLevel.POTENTIALLY_RELEVANT
) -> EventIntelligence:
    return EventIntelligence(event=event, overall_relevance=relevance, positions=())


def make_agent(
    *,
    economic: EconomicIntelligenceService | None = None,
    llm: LLMProvider | None = None,
    context: FinancialContext | None = None,
) -> tuple[AgentService, _RecordingFinancialContextService, LLMProvider]:
    financial = _RecordingFinancialContextService(
        context if context is not None else make_financial_context()
    )
    provider: LLMProvider = llm if llm is not None else FakeLLMProvider()
    agent = AgentService(financial, provider, economic_intelligence_service=economic)
    return agent, financial, provider


def real_economic_service() -> EconomicIntelligenceService:
    """The real economic-intelligence service over deterministic offline sources."""
    return EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
    )


def make_real_financial_service() -> FinancialContextService:
    return FinancialContextService(
        account_service=AccountInfoService(_FakeAccountInfoProvider()),
        position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
        trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=(TRADE,))),
    )


# --- calendar data reaches the agent prompt ---------------------------------------------


def test_calendar_events_appear_in_the_agent_prompt() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("What economic events are today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert CPI_EVENT.title in content
    assert CPI_EVENT.timestamp.isoformat() in content
    assert CPI_EVENT.currency in content
    assert "impact HIGH" in content
    assert "forecast 3.1%" in content
    assert "previous 3.2%" in content


def test_unreported_release_values_render_as_absent() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    # CPI's actual is None: the model must see "not reported", never a guess.
    assert "actual -" in provider.prompts[0].content


def test_a_reported_value_is_rendered_verbatim() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(IP_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert "actual 0.1%" in provider.prompts[0].content


def test_calendar_provenance_is_preserved_in_the_prompt() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(
            events=(event_intelligence(CPI_EVENT),), data_source="quantgist-free-development"
        )
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    # The delayed/placeholder provenance marker travels with the data, exactly
    # as it does in the API response, so the model cannot present it as live.
    assert "quantgist-free-development" in provider.prompts[0].content


def test_each_event_carries_its_own_relevance_level() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(
            events=(
                event_intelligence(CPI_EVENT, RelevanceLevel.POTENTIALLY_RELEVANT),
                event_intelligence(IP_EVENT, RelevanceLevel.NOT_OBVIOUSLY_RELEVANT),
            )
        )
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    cpi_line = next(line for line in content.splitlines() if CPI_EVENT.title in line)
    ip_line = next(line for line in content.splitlines() if IP_EVENT.title in line)
    assert "relevance POTENTIALLY_RELEVANT" in cpi_line
    assert "relevance NOT_OBVIOUSLY_RELEVANT" in ip_line


def test_the_window_bounds_are_rendered_for_the_model() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert WINDOW_FROM.isoformat() in content
    assert WINDOW_TO.isoformat() in content


def test_the_real_economic_service_flows_through_the_agent_prompt() -> None:
    # The composition under test: real AgentService + real EconomicIntelligenceService
    # over the deterministic placeholder calendar and fake position provider.
    provider = FakeLLMProvider()
    agent = AgentService(
        make_real_financial_service(),
        provider,
        economic_intelligence_service=real_economic_service(),
    )

    agent.handle("Which of today's events matter for my positions?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # Deterministic placeholder events for today's UTC window.
    assert "US Consumer Price Index (CPI) YoY (placeholder)" in content
    assert "Japan BoJ Interest Rate Decision (placeholder)" in content
    # Provenance of the placeholder source is carried into the prompt.
    assert "fake-development-placeholder" in content
    # XAUUSD holds USD, so a USD event is classified relevant to the position.
    assert "relevance POTENTIALLY_RELEVANT" in content


def test_the_real_economic_service_uses_the_reference_time_it_is_given() -> None:
    calendar = FakeEconomicCalendarProvider()
    service = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(calendar),
        position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
    )
    agent = AgentService(
        make_real_financial_service(), FakeLLMProvider(), economic_intelligence_service=service
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    # The calendar was asked for AS_OF's UTC day, not the wall clock's.
    assert calendar.windows == [(WINDOW_FROM, WINDOW_TO)]


# --- one reference time for the whole request -------------------------------------------


def test_the_same_explicit_now_drives_both_contexts() -> None:
    economic = _RecordingEconomicService(make_economic_context())
    agent, financial, _ = make_agent(economic=economic)

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert financial.calls == [AS_OF]
    assert economic.calls == [AS_OF]


def test_an_absent_now_is_resolved_once_and_shared() -> None:
    economic = _RecordingEconomicService(make_economic_context())
    agent, financial, _ = make_agent(economic=economic)

    agent.handle("hello", broker_id=1)

    # The agent resolves one UTC reference and uses it for both windows.
    resolved = financial.calls[0]
    assert resolved is not None
    assert resolved == economic.calls[0]
    assert resolved.tzinfo is not None


def test_one_calendar_read_per_request() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    agent, _, _ = make_agent(economic=economic)

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert len(economic.calls) == 1


# --- empty calendar ----------------------------------------------------------------------


def test_an_empty_calendar_is_rendered_explicitly() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(), data_source="test-calendar-source")
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("Any events today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Economic calendar for today" in content
    assert "test-calendar-source" in content
    assert "no economic events are published for today" in content


# --- failure boundary --------------------------------------------------------------------


def test_calendar_failure_propagates_and_the_llm_is_never_asked() -> None:
    economic = _RecordingEconomicService(error=RuntimeError("calendar source unavailable"))
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    with pytest.raises(RuntimeError, match="calendar source unavailable"):
        agent.handle("hello", broker_id=1, now=AS_OF)

    # The existing boundary: the failure is neither swallowed nor turned into a
    # partial answer, and the model is never called.
    assert provider.call_count == 0


def test_the_financial_context_is_read_before_the_calendar() -> None:
    order: list[str] = []

    class _OrderedFinancial(_RecordingFinancialContextService):
        def build(
            self,
            broker_id: int,
            trade_history_days: int = DEFAULT_TRADE_HISTORY_DAYS,
            now: datetime | None = None,
        ) -> FinancialContext:
            order.append("financial")
            return super().build(broker_id, trade_history_days, now)

    class _OrderedEconomic(_RecordingEconomicService):
        def build_today_context(
            self, minimum_impact: EventImpact | None = None, now: datetime | None = None
        ) -> EconomicIntelligenceContext:
            order.append("economic")
            return super().build_today_context(minimum_impact, now)

    agent = AgentService(
        _OrderedFinancial(make_financial_context()),
        FakeLLMProvider(),
        economic_intelligence_service=_OrderedEconomic(make_economic_context()),
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert order == ["financial", "economic"]


# --- existing behaviour unchanged --------------------------------------------------------


def test_without_an_economic_service_the_prompt_has_no_calendar_block() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Economic calendar" not in content
    # Byte-identical to the pre-Step-45 rendering.
    assert provider.prompts[0] == build_prompt(
        "hello", make_financial_context(), OutboundDataPolicy.from_settings()
    )


def test_the_calendar_block_leaves_the_financial_sections_intact() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("How exposed am I?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Balance: 10000.00" in content
    assert "XAUUSD" in content
    assert "Risk classification" in content
    assert "How exposed am I?" in content
    # The trade block and its guarantee survive the addition.
    assert "Recent executed trades" in content


def test_calendar_rendering_is_deterministic() -> None:
    def content_once() -> str:
        economic = _RecordingEconomicService(
            make_economic_context(events=(event_intelligence(CPI_EVENT),))
        )
        provider = FakeLLMProvider()
        make_agent(economic=economic, llm=provider)[0].handle("hello", broker_id=1, now=AS_OF)
        return provider.prompts[0].content

    assert content_once() == content_once()


def test_the_calendar_block_carries_no_account_identity() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert str(ACCOUNT.login) not in content
    assert ACCOUNT.name not in content
    assert ACCOUNT.server not in content


def test_the_agent_envelope_is_unchanged() -> None:
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    response = agent.handle("hello", broker_id=42, now=AS_OF)

    assert response.broker_id == 42
    assert response.request == "hello"
    assert isinstance(response.context, FinancialContext)
    assert response.context.as_of == AS_OF


# --- prompt size limits and staged reduction -------------------------------------------


def test_oversized_prompt_drops_the_trade_block_before_the_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    economic_context = make_economic_context(events=(event_intelligence(CPI_EVENT),))
    context = make_financial_context()
    full = build_prompt("hello", context, OutboundDataPolicy(), economic_context)
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", len(full.content) - 1, raising=True)

    reduced = build_prompt("hello", context, OutboundDataPolicy(), economic_context).content

    # The existing first reduction still happens first...
    assert "Recent executed trades: omitted" in reduced
    # ...and the calendar block survives it.
    assert CPI_EVENT.title in reduced
    assert len(reduced) <= len(full.content) - 1


def test_oversized_prompt_then_drops_the_calendar_block_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    economic_context = make_economic_context(events=(event_intelligence(CPI_EVENT),))
    context = make_financial_context()
    full = build_prompt("hello", context, OutboundDataPolicy(), economic_context)
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", len(full.content) - 1, raising=True)
    trades_dropped = build_prompt("hello", context, OutboundDataPolicy(), economic_context)
    # Just below what the trade-dropped rendering needs: the calendar block goes too.
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(trades_dropped.content) - 1, raising=True
    )

    reduced = build_prompt("hello", context, OutboundDataPolicy(), economic_context).content

    assert len(reduced) <= len(trades_dropped.content) - 1
    assert "Recent executed trades: omitted" in reduced  # trades, as before
    assert CPI_EVENT.title not in reduced  # calendar data dropped
    assert "Economic calendar: omitted" in reduced  # ...and stated, never silent
    assert "Balance: 10000.00" in reduced  # financial core survives


def test_prompt_still_fails_closed_when_the_financial_core_alone_is_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    with pytest.raises(PromptTooLargeError):
        build_prompt(
            "hello",
            make_financial_context(),
            OutboundDataPolicy(),
            make_economic_context(events=(event_intelligence(CPI_EVENT),)),
        )


def test_the_agent_propagates_the_prompt_too_large_error_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The agent raises the existing PromptTooLargeError untouched (the API layer
    # already translates it to 422), and the model is never called.
    economic = _RecordingEconomicService(
        make_economic_context(events=(event_intelligence(CPI_EVENT),))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    with pytest.raises(PromptTooLargeError):
        agent.handle("hello", broker_id=1, now=AS_OF)

    assert provider.call_count == 0
