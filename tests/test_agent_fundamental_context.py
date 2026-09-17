"""Step 47 tests: fundamental intelligence composed into the Agent pipeline.

Covers the agent ↔ fundamental-intelligence composition only: the facts that
reach the prompt (news, relevance, provenance, position exposure), the mandatory
calendar block beside them, the single reference time shared by every context,
the UNKNOWN/unavailable states, the file's bounded size discipline, failure
propagation through the existing error boundary, and the unchanged behaviour when
no fundamental service is wired.

Require none of: real MT5, PostgreSQL, network, credentials, a news vendor or an
external LLM. The fundamental context is either a canned ``FundamentalContext``
built from deterministic offline sources, or the real service over the
deterministic placeholder news feed. No pytest asyncio plugin.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import settings as app_settings
from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.fake_news import FakeNewsProvider
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
    PositionRelevance,
    RelevanceLevel,
)
from app.services.financial_context import (
    DEFAULT_TRADE_HISTORY_DAYS,
    FinancialContext,
    FinancialContextService,
)
from app.services.fundamental_intelligence import (
    FundamentalContext,
    FundamentalIntelligenceService,
)
from app.services.news import NewsService
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
        self,
        minimum_impact: EventImpact | None = None,
        now: datetime | None = None,
        positions: tuple[Position, ...] | None = None,
    ) -> EconomicIntelligenceContext:
        self.calls.append(now)
        if self.error is not None:
            raise self.error
        assert self.context is not None
        return self.context


class _RecordingFundamentalService(FundamentalIntelligenceService):
    """Test double recording build_context() calls; returns or raises."""

    def __init__(
        self,
        context: FundamentalContext | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(news_service=None)
        self.context = context
        self.error = error
        self.calls: list[tuple[EconomicIntelligenceContext, str | None]] = []

    def build_context(
        self, calendar: EconomicIntelligenceContext, focus_symbol: str | None = None
    ) -> FundamentalContext:
        self.calls.append((calendar, focus_symbol))
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
    positions: tuple[Position, ...] = (XAUUSD,),
) -> EconomicIntelligenceContext:
    return EconomicIntelligenceContext(
        as_of=AS_OF,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source=data_source,
        position_symbols=tuple(sorted({position.symbol for position in positions})),
        events=events,
        positions=positions,
    )


def event_intelligence(
    event: EconomicEvent = CPI_EVENT,
    relevance: RelevanceLevel = RelevanceLevel.POTENTIALLY_RELEVANT,
) -> EventIntelligence:
    return EventIntelligence(
        event=event,
        overall_relevance=relevance,
        positions=(
            PositionRelevance(
                ticket=XAUUSD.ticket,
                symbol=XAUUSD.symbol,
                type=XAUUSD.type,
                relevance=relevance,
                reason="canned relevance",
            ),
        ),
    )


def real_fundamental_context(
    *, focus_symbol: str | None = "XAUUSD", calendar: EconomicIntelligenceContext | None = None
) -> FundamentalContext:
    """The real fundamental service over the deterministic placeholder feed."""
    service = FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    )
    return service.build_context(
        calendar if calendar is not None else make_economic_context(events=(event_intelligence(),)),
        focus_symbol=focus_symbol,
    )


def make_agent(
    *,
    economic: EconomicIntelligenceService | None = None,
    fundamental: FundamentalIntelligenceService | None = None,
    llm: LLMProvider | None = None,
    context: FinancialContext | None = None,
) -> tuple[AgentService, _RecordingFinancialContextService, LLMProvider]:
    financial = _RecordingFinancialContextService(
        context if context is not None else make_financial_context()
    )
    provider: LLMProvider = llm if llm is not None else FakeLLMProvider()
    agent = AgentService(
        financial,
        provider,
        economic_intelligence_service=economic,
        fundamental_intelligence_service=fundamental,
    )
    return agent, financial, provider


# --- the fundamental facts reach the prompt ---------------------------------------------


def test_news_facts_appear_in_the_agent_prompt() -> None:
    context = real_fundamental_context()
    fundamental = _RecordingFundamentalService(context)
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=_RecordingEconomicService(make_economic_context()), fundamental=fundamental, llm=provider)

    agent.handle("What news matters for XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Fundamental intelligence" in content
    entry = context.news[0]
    assert entry.item.title in content
    assert entry.item.publisher in content
    assert entry.item.published_at.isoformat() in content
    assert f"relevance {entry.relevance.value}" in content


def test_the_prompt_labels_facts_as_source_material() -> None:
    context = real_fundamental_context()
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # The model is told where published material ends and its own interpretation
    # begins, so it cannot present its own reasoning as a published fact.
    assert "published source facts, not analysis" in content


def test_fundamental_provenance_is_preserved_in_the_prompt() -> None:
    context = real_fundamental_context()
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(data_source="quantgist-free-development")),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # Both the calendar provenance and the news provenance travel with the data.
    assert "quantgist-free-development" in content
    assert "fake-development-placeholder" in content


def test_the_instruments_in_play_are_rendered() -> None:
    context = real_fundamental_context(focus_symbol="XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert "instruments in play: XAUUSD" in provider.prompts[0].content


def test_position_exposure_is_rendered_with_its_status_and_relevance() -> None:
    context = real_fundamental_context()
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    exposure = context.positions[0]
    assert "Position fundamental exposure" in content
    assert f"{exposure.symbol} {exposure.type.value} 0.10" in content
    assert exposure.status.value in content


def test_the_calendar_block_still_reaches_the_prompt_beside_it() -> None:
    context = real_fundamental_context()
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # The calendar remains mandatory context on every request.
    assert "Economic calendar for today" in content
    assert CPI_EVENT.title in content


# --- the same reference time and the same calendar context -------------------------------


def test_the_fundamental_context_is_built_from_the_requests_own_calendar_context() -> None:
    economic_context = make_economic_context(events=(event_intelligence(),))
    economic = _RecordingEconomicService(economic_context)
    fundamental = _RecordingFundamentalService(real_fundamental_context(calendar=economic_context))
    agent, _, _ = make_agent(economic=economic, fundamental=fundamental)

    agent.handle("hello", broker_id=1, now=AS_OF)

    # Exactly one build, from the very context the calendar step produced: no
    # second calendar read and no second position read.
    assert len(fundamental.calls) == 1
    assert fundamental.calls[0][0] is economic_context
    assert len(economic.calls) == 1


def test_the_same_explicit_now_drives_every_context() -> None:
    economic = _RecordingEconomicService(make_economic_context())
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    agent, financial, _ = make_agent(economic=economic, fundamental=fundamental)

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert financial.calls == [AS_OF]
    assert economic.calls == [AS_OF]


def test_the_focus_symbol_is_detected_from_the_untrusted_request() -> None:
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()), fundamental=fundamental
    )

    agent.handle("What is threatening my EURUSD position today?", broker_id=1, now=AS_OF)

    assert fundamental.calls[0][1] == "EURUSD"


def test_a_request_without_an_instrument_passes_no_focus_symbol() -> None:
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()), fundamental=fundamental
    )

    agent.handle("How am I doing today?", broker_id=1, now=AS_OF)

    assert fundamental.calls[0][1] is None


def test_without_a_calendar_context_the_fundamental_layer_is_not_asked() -> None:
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    agent, _, _ = make_agent(fundamental=fundamental)

    agent.handle("hello", broker_id=1, now=AS_OF)

    # Fundamental intelligence is composed around the mandatory calendar, so it
    # is never built without one.
    assert fundamental.calls == []


# --- explicit states: unknown, unavailable, empty ----------------------------------------


def test_unknown_exposure_is_rendered_as_unknown_not_as_no_risk() -> None:
    calendar = make_economic_context(events=())
    context = FundamentalIntelligenceService(news_service=None).build_context(calendar, "XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(calendar),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("What is threatening my position?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "UNKNOWN" in content
    assert "could not be assessed" in content


def test_an_unconfigured_news_source_is_rendered_as_unavailable_not_as_no_news() -> None:
    calendar = make_economic_context(events=(event_intelligence(),))
    context = FundamentalIntelligenceService(news_service=None).build_context(calendar, "XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(calendar),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("Any news today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "news unavailable" in content
    assert "No news source is configured" in content
    assert "published no news items for today" not in content


def test_an_empty_feed_is_stated_as_this_source_publishing_nothing() -> None:
    calendar = make_economic_context(events=(event_intelligence(),))
    context = FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(items=()), max_items=20)
    ).build_context(calendar, "XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(calendar),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("Any news today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "this source published no news items for today" in content


def test_no_open_positions_is_rendered_explicitly() -> None:
    calendar = make_economic_context(events=(event_intelligence(),), positions=())
    context = FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    ).build_context(calendar, "XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(calendar),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert "no open positions" in provider.prompts[0].content


# --- bounded prompt discipline ------------------------------------------------------------


def test_a_long_news_excerpt_is_bounded_in_the_prompt() -> None:
    from app.providers.news import NewsItem

    long_summary = "y" * 500
    item = NewsItem(
        item_id="news-long",
        published_at=datetime(2026, 9, 16, 7, 5, tzinfo=UTC),
        publisher="Example Newswire (placeholder)",
        title="Long placeholder item",
        summary=long_summary,
        url=None,
        instruments=(),
        currencies=("USD",),
        categories=(),
    )
    calendar = make_economic_context(events=())
    context = FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(items=(item,)), max_items=20)
    ).build_context(calendar, "XAUUSD")
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(calendar),
        fundamental=_RecordingFundamentalService(context),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert long_summary not in content
    assert "y" * 200 + "..." in content


def test_build_prompt_drops_the_trade_block_first(monkeypatch: pytest.MonkeyPatch) -> None:
    economic_context = make_economic_context(events=(event_intelligence(),))
    fundamental_context = real_fundamental_context(
        focus_symbol="XAUUSD", calendar=economic_context
    )
    context = make_financial_context()
    full = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    )
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", len(full.content) - 1, raising=True)

    reduced = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    ).content

    assert "Recent executed trades: omitted" in reduced
    assert CPI_EVENT.title in reduced  # the calendar survives
    assert "Fundamental intelligence" in reduced  # and so does the fundamental block
    assert len(reduced) <= len(full.content) - 1


def test_build_prompt_then_drops_the_fundamental_block_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    economic_context = make_economic_context(events=(event_intelligence(),))
    fundamental_context = real_fundamental_context(
        focus_symbol="XAUUSD", calendar=economic_context
    )
    context = make_financial_context()
    # First rung: the trade block goes (the established behaviour).
    monkeypatch.setattr(
        app_settings,
        "AGENT_MAX_PROMPT_CHARS",
        len(build_prompt("hello", context, OutboundDataPolicy(), economic_context, fundamental_context).content) - 1,
        raising=True,
    )
    trades_dropped = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    )
    assert "Recent executed trades: omitted" in trades_dropped.content
    # Second rung: just below what the trade-dropped rendering needs.
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(trades_dropped.content) - 1, raising=True
    )

    reduced = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    ).content

    assert "Recent executed trades: omitted" in reduced
    # The bounded public block goes before the mandatory calendar context...
    assert "Fundamental intelligence: omitted" in reduced
    assert CPI_EVENT.title in reduced
    assert len(reduced) <= len(trades_dropped.content) - 1


def test_build_prompt_then_drops_the_calendar_block_too(monkeypatch: pytest.MonkeyPatch) -> None:
    economic_context = make_economic_context(events=(event_intelligence(),))
    fundamental_context = real_fundamental_context(
        focus_symbol="XAUUSD", calendar=economic_context
    )
    context = make_financial_context()
    # Walk the ladder down to the rendering that has both public blocks dropped.
    monkeypatch.setattr(
        app_settings,
        "AGENT_MAX_PROMPT_CHARS",
        len(build_prompt("hello", context, OutboundDataPolicy(), economic_context, fundamental_context).content) - 1,
        raising=True,
    )
    trades_dropped = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    )
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(trades_dropped.content) - 1, raising=True
    )
    fundamental_dropped = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    )
    assert "Fundamental intelligence: omitted" in fundamental_dropped.content
    # Final rung before the context itself is too large: the calendar goes too.
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(fundamental_dropped.content) - 1, raising=True
    )

    reduced = build_prompt(
        "hello", context, OutboundDataPolicy(), economic_context, fundamental_context
    ).content

    assert "Fundamental intelligence: omitted" in reduced
    assert "Economic calendar: omitted" in reduced
    assert CPI_EVENT.title not in reduced
    assert "Balance: 10000.00" in reduced  # the financial core survives
    assert len(reduced) <= len(fundamental_dropped.content) - 1


def test_prompt_still_fails_closed_when_the_financial_core_alone_is_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    with pytest.raises(PromptTooLargeError):
        build_prompt(
            "hello",
            make_financial_context(),
            OutboundDataPolicy(),
            make_economic_context(events=(event_intelligence(),)),
            real_fundamental_context(),
        )


def test_the_agent_propagates_the_prompt_too_large_error_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=fundamental,
        llm=provider,
    )
    monkeypatch.setattr(app_settings, "AGENT_MAX_PROMPT_CHARS", 50, raising=True)

    with pytest.raises(PromptTooLargeError):
        agent.handle("hello", broker_id=1, now=AS_OF)

    assert provider.call_count == 0


# --- failure boundary ---------------------------------------------------------------------


def test_calendar_failure_propagates_and_the_fundamental_layer_is_never_asked() -> None:
    fundamental = _RecordingFundamentalService(real_fundamental_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(error=RuntimeError("calendar source unavailable")),
        fundamental=fundamental,
        llm=provider,
    )

    with pytest.raises(RuntimeError, match="calendar source unavailable"):
        agent.handle("hello", broker_id=1, now=AS_OF)

    assert fundamental.calls == []
    assert provider.call_count == 0


def test_fundamental_failure_propagates_and_the_llm_is_never_asked() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(error=RuntimeError("news source unavailable")),
        llm=provider,
    )

    with pytest.raises(RuntimeError, match="news source unavailable"):
        agent.handle("hello", broker_id=1, now=AS_OF)

    # The existing boundary: no partial answer is fabricated from a failed read.
    assert provider.call_count == 0


def test_the_financial_context_is_read_before_the_calendar_and_the_calendar_before_news() -> None:
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
            self,
            minimum_impact: EventImpact | None = None,
            now: datetime | None = None,
            positions: tuple[Position, ...] | None = None,
        ) -> EconomicIntelligenceContext:
            order.append("economic")
            return super().build_today_context(minimum_impact, now, positions)

    class _OrderedFundamental(_RecordingFundamentalService):
        def build_context(
            self, calendar: EconomicIntelligenceContext, focus_symbol: str | None = None
        ) -> FundamentalContext:
            order.append("fundamental")
            return super().build_context(calendar, focus_symbol)

    agent = AgentService(
        _OrderedFinancial(make_financial_context()),
        FakeLLMProvider(),
        economic_intelligence_service=_OrderedEconomic(make_economic_context()),
        fundamental_intelligence_service=_OrderedFundamental(real_fundamental_context()),
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert order == ["financial", "economic", "fundamental"]


# --- existing behaviour unchanged ----------------------------------------------------------


def test_without_a_fundamental_service_the_prompt_is_unchanged() -> None:
    economic = _RecordingEconomicService(make_economic_context(events=(event_intelligence(),)))
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(economic=economic, llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Fundamental intelligence" not in content
    assert provider.prompts[0] == build_prompt(
        "hello",
        make_financial_context(),
        OutboundDataPolicy.from_settings(),
        make_economic_context(events=(event_intelligence(),)),
    )


def test_the_fundamental_block_leaves_the_financial_sections_intact() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        llm=provider,
    )

    agent.handle("What is threatening my positions?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Balance: 10000.00" in content
    assert "Risk classification" in content
    assert "What is threatening my positions?" in content
    assert "Recent executed trades" in content


def test_the_fundamental_block_carries_no_account_identity() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        llm=provider,
    )

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert str(ACCOUNT.login) not in content
    assert ACCOUNT.name not in content
    assert ACCOUNT.server not in content


def test_the_fundamental_rendering_is_deterministic() -> None:
    def content_once() -> str:
        provider = FakeLLMProvider()
        agent, _, _ = make_agent(
            economic=_RecordingEconomicService(
                make_economic_context(events=(event_intelligence(),))
            ),
            fundamental=_RecordingFundamentalService(real_fundamental_context()),
            llm=provider,
        )
        agent.handle("hello", broker_id=1, now=AS_OF)
        return provider.prompts[0].content

    assert content_once() == content_once()


def test_the_agent_envelope_is_unchanged_by_the_fundamental_composition() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context(events=(event_intelligence(),))),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        llm=provider,
    )

    response = agent.handle("hello", broker_id=42, now=AS_OF)

    assert response.broker_id == 42
    assert response.request == "hello"
    assert isinstance(response.context, FinancialContext)
    assert response.context.as_of == AS_OF


def test_the_real_sources_flow_through_the_whole_agent_pipeline() -> None:
    # The composition under test: real AgentService + real
    # EconomicIntelligenceService + real FundamentalIntelligenceService over the
    # deterministic placeholder calendar, position and news sources.
    provider = FakeLLMProvider()
    agent = AgentService(
        FinancialContextService(
            account_service=AccountInfoService(_FakeAccountInfoProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
            trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=(TRADE,))),
        ),
        provider,
        economic_intelligence_service=EconomicIntelligenceService(
            calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
        ),
        fundamental_intelligence_service=FundamentalIntelligenceService(
            news_service=NewsService(FakeNewsProvider(), max_items=20)
        ),
    )

    agent.handle("What news and economic events are relevant to XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Economic calendar for today" in content
    assert "Fundamental intelligence for XAUUSD" in content
    assert "fake-development-placeholder" in content
    assert "US CPI release due later today (placeholder)" in content
    assert "Position fundamental exposure" in content
