"""Step 49 follow-up tests: financial research composed into the Agent pipeline.

Covers the agent ↔ research composition only: when the research context is
built (focus instrument named, look-back enabled), the window it covers (the
half-open span immediately BEFORE the calendar window, so it never overlaps the
fundamental news), the graded facts that reach the prompt (Step 48 levels and
factors, provenance, facts-not-analysis labelling), the explicit empty and
unavailable states, the reduction ladder rung, failure propagation through the
existing error boundary, and the unchanged behaviour when no research service
is wired.

Require none of: real MT5, PostgreSQL, network, credentials, a news vendor or
an external LLM. Every context is a canned/real-service construction over the
deterministic placeholder sources, exactly like test_agent_fundamental_context.
No pytest asyncio plugin.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import settings as app_settings
from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.llm import LLMProvider
from app.providers.instrument import Instrument, InstrumentProvider, TradeMode
from app.providers.news import NewsItem
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeCloseReason, TradeHistoryEntry, TradeType
from app.services.account import AccountInfoService
from app.services.agent import AgentService, OutboundDataPolicy, build_prompt
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
    FinancialResearchContext,
    FinancialResearchService,
    FocusResolution,
    FundamentalContext,
    FundamentalIntelligenceService,
)
from app.services.instruments import InstrumentService
from app.services.news import NewsService
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
DEFAULT_LOOKBACK = app_settings.AGENT_RESEARCH_LOOKBACK_DAYS
RESEARCH_FROM = WINDOW_FROM - timedelta(days=DEFAULT_LOOKBACK)

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
    def __init__(
        self,
        context: EconomicIntelligenceContext | None = None,
        error: Exception | None = None,
    ) -> None:
        # No super().__init__: the double overrides build_today_context
        # completely, so no calendar or position source is ever constructed.
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


class _RecordingResearchService(FinancialResearchService):
    """Test double recording build_research() calls; returns or raises."""

    def __init__(
        self,
        context: FinancialResearchContext | None = None,
        error: Exception | None = None,
        instrument_service: InstrumentService | None = None,
    ) -> None:
        super().__init__(news_service=None, instrument_service=instrument_service)
        self.context = context
        self.error = error
        self.calls: list[tuple[datetime, datetime, tuple[str, ...]]] = []
        # Resolution is the REAL implementation (Step 51), so what the agent
        # asked the catalog for can be asserted.
        self.resolutions: list[FocusResolution] = []

    def resolve_focus_symbols(
        self, focus_symbols: tuple[str, ...] | list[str]
    ) -> FocusResolution:
        resolution = super().resolve_focus_symbols(focus_symbols)
        self.resolutions.append(resolution)
        return resolution

    def build_research(
        self,
        from_time: datetime,
        to_time: datetime,
        focus_symbols: tuple[str, ...] | list[str] = (),
    ) -> FinancialResearchContext:
        self.calls.append((from_time, to_time, tuple(focus_symbols)))
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


def make_economic_context() -> EconomicIntelligenceContext:
    return EconomicIntelligenceContext(
        as_of=AS_OF,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source="test-calendar-source",
        position_symbols=(XAUUSD.symbol,),
        events=(
            EventIntelligence(
                event=CPI_EVENT,
                overall_relevance=RelevanceLevel.POTENTIALLY_RELEVANT,
                positions=(
                    PositionRelevance(
                        ticket=XAUUSD.ticket,
                        symbol=XAUUSD.symbol,
                        type=XAUUSD.type,
                        relevance=RelevanceLevel.POTENTIALLY_RELEVANT,
                        reason="canned relevance",
                    ),
                ),
            ),
        ),
        positions=(XAUUSD,),
    )


def real_fundamental_context() -> FundamentalContext:
    """The real fundamental service over the deterministic placeholder feed."""
    return FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    ).build_context(make_economic_context(), focus_symbol="XAUUSD")


class _RecordingFundamentalService(FundamentalIntelligenceService):
    """Test double recording build_context() calls; returns the canned context."""

    def __init__(self, context: FundamentalContext) -> None:
        super().__init__(news_service=None)
        self.context = context
        self.calls: list[tuple[EconomicIntelligenceContext, str | None]] = []

    def build_context(
        self, calendar: EconomicIntelligenceContext, focus_symbol: str | None = None
    ) -> FundamentalContext:
        self.calls.append((calendar, focus_symbol))
        return self.context


def broker_catalog(*symbols: str) -> InstrumentService:
    """An instrument service over a broker catalog holding exactly ``symbols``.

    The real InstrumentService runs (real resolution rules); only the vendor
    side is a deterministic in-memory catalog, so the agent's resolution step is
    exercised without a terminal, credentials, database or network.
    """
    return InstrumentService(
        FakeInstrumentProvider(
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
    )


class _FailingInstrumentProvider(InstrumentProvider):
    """Catalog whose terminal is unavailable (infrastructure failure)."""

    def get_instrument(self, symbol: str) -> Instrument:
        raise RuntimeError("MT5 instrument lookup failed")

    def list_instruments(self) -> tuple[Instrument, ...]:
        raise RuntimeError("MT5 instrument catalog request failed")


def real_research_context(
    *,
    items: tuple[NewsItem, ...] | None = None,
    news_service: NewsService | None = None,
    focus_symbols: tuple[str, ...] = ("XAUUSD",),
    instrument_service: InstrumentService | None = None,
) -> FinancialResearchContext:
    """The real research service over the deterministic placeholder feed."""
    service = news_service if news_service is not None else NewsService(
        FakeNewsProvider(items=items) if items is not None else FakeNewsProvider(),
        max_items=20,
    )
    return FinancialResearchService(service, instrument_service).build_research(
        RESEARCH_FROM, WINDOW_FROM, focus_symbols=focus_symbols
    )


def make_agent(
    *,
    economic: EconomicIntelligenceService | None = None,
    fundamental: FundamentalIntelligenceService | None = None,
    research: FinancialResearchService | None = None,
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
        financial_research_service=research,
    )
    return agent, financial, provider


# --- the research facts reach the prompt --------------------------------------------------


def test_research_facts_appear_in_the_agent_prompt() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Financial research (published source facts, not analysis" in content
    # The graded look-back items are rendered with the same line format.
    assert "Gold ETF flows reported steady ahead of US data (placeholder)" in content


def test_research_is_labelled_as_facts_not_analysis() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # The research block carries the same source-facts-vs-analysis label the
    # fundamental block does, so the model cannot present look-back items as
    # its own interpretation (or vice versa).
    research_body = content.split("Financial research (")[1]
    assert "published source facts, not analysis" in content
    assert "relevance " in research_body


def test_research_provenance_is_preserved_in_the_prompt() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    # The development feed's provenance marker travels into the research block,
    # so placeholder look-back data can never be read as live market data.
    content = provider.prompts[0].content
    research_body = content.split("Financial research (")[1]
    assert "news source: fake-development-placeholder" in research_body


def test_research_preserves_the_step_48_grading() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    research_body = provider.prompts[0].content.split("Financial research (")[1]
    # A DIRECT profile match is graded RELEVANT; a MACRO match stays
    # POTENTIALLY_RELEVANT — the same discrete levels the fundamental block uses.
    assert "relevance RELEVANT (direct factor for XAUUSD: precious metals)" in research_body
    assert (
        "relevance POTENTIALLY_RELEVANT (macro factor for XAUUSD: "
        "central-bank monetary policy)" in research_body
    )
    # An item with no reference in play is stated, never guessed.
    assert "relevance NOT_OBVIOUSLY_RELEVANT" in research_body


def test_research_window_is_stated_and_precedes_the_calendar_window() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert f"look-back window {RESEARCH_FROM.isoformat()} .. {WINDOW_FROM.isoformat()}" in content
    assert "BEFORE the calendar window above" in content


def test_research_holds_no_account_position_or_tenant_data() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    research_body = content.split("Financial research (")[1]
    assert str(ACCOUNT.login) not in research_body
    assert ACCOUNT.name not in research_body
    assert ACCOUNT.server not in research_body
    assert "broker" not in research_body.split("news source")[0]
    # The research block renders no exposure line at all (that is the
    # fundamental block's job).
    assert "Position fundamental exposure" not in research_body


def test_the_research_block_leaves_the_financial_sections_intact() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Balance: 10000.00" in content
    assert "Risk classification" in content
    assert "Economic calendar for today" in content
    assert "Fundamental intelligence" in content
    assert "What is happening with XAUUSD today?" in content


# --- when the research context is (not) built ---------------------------------------------


def test_research_is_built_only_for_the_requests_own_focus_instruments() -> None:
    research = _RecordingResearchService(real_research_context())
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert len(research.calls) == 1
    assert research.calls[0][2] == ("XAUUSD",)


def test_research_is_not_built_without_a_focus_instrument() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("How am I doing today?", broker_id=1, now=AS_OF)

    # Without a named instrument the fundamental block already covers the
    # window; the research block would add nothing.
    assert research.calls == []
    assert "Financial research" not in provider.prompts[0].content


def test_research_is_not_built_when_the_lookback_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "AGENT_RESEARCH_LOOKBACK_DAYS", 0, raising=True)
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.calls == []
    assert "Financial research" not in provider.prompts[0].content


def test_research_covers_exactly_the_lookback_span_before_the_calendar_window() -> None:
    class _RecordingNews(FakeNewsProvider):
        pass

    news = _RecordingNews()
    research = _RecordingResearchService(
        real_research_context(news_service=NewsService(news, max_items=20))
    )
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    from_time, to_time, _ = research.calls[0]
    # The half-open span immediately BEFORE the calendar window.
    assert from_time == WINDOW_FROM - timedelta(days=DEFAULT_LOOKBACK)
    assert to_time == WINDOW_FROM
    # No overlap with the fundamental block's window is structural: [a, b) and
    # [b, c) share no instant, so no item is fetched twice.
    assert from_time < to_time <= WINDOW_FROM


def test_multiple_focus_instruments_are_graded_together() -> None:
    research = _RecordingResearchService(
        real_research_context(focus_symbols=("XAUUSD", "USOIL"))
    )
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle("What is happening with XAUUSD and USOIL today?", broker_id=1, now=AS_OF)

    # Step 51 changed WHERE the focus set comes from, not how it is normalized:
    # the agent now hands over the resolved symbols, which the resolution step
    # reports deterministically (upper-cased, sorted, deduplicated) exactly as
    # the research context's own focus list always was. The order the request
    # happens to name instruments in was never part of the contract.
    assert research.calls[0][2] == ("USOIL", "XAUUSD")


def test_focus_symbols_are_bounded_per_request() -> None:
    research = _RecordingResearchService(real_research_context())
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle(
        "What is happening with XAUUSD, USOIL, NAS100 and WTI today?",
        broker_id=1,
        now=AS_OF,
    )

    assert len(research.calls[0][2]) == 3


# --- explicit states: empty, unavailable ---------------------------------------------------


def test_an_empty_research_window_is_stated_not_invented() -> None:
    empty = real_research_context(items=())
    research = _RecordingResearchService(empty)
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    research_body = provider.prompts[0].content.split("Financial research (")[1]
    assert "no research items in the look-back window" in research_body


def test_an_unavailable_research_source_is_stated_not_empty() -> None:
    unavailable = FinancialResearchService(None).build_research(
        RESEARCH_FROM, WINDOW_FROM, focus_symbols=("XAUUSD",)
    )
    research = _RecordingResearchService(unavailable)
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    research_body = provider.prompts[0].content.split("Financial research (")[1]
    assert "news unavailable" in research_body
    assert "No news source is configured" in research_body


# --- bounded prompt discipline ---------------------------------------------------------------


def test_a_long_research_excerpt_is_bounded_in_the_prompt() -> None:
    long_summary = "z" * 500
    item = NewsItem(
        item_id="research-long",
        published_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
        publisher="Example Newswire (placeholder)",
        title="Long look-back placeholder item",
        summary=long_summary,
        url=None,
        instruments=(),
        currencies=("USD",),
        categories=(),
    )
    research = _RecordingResearchService(real_research_context(items=(item,)))
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert long_summary not in content
    assert "z" * 200 + "..." in content


def test_the_research_block_is_dropped_before_the_mandatory_calendar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fundamental = real_fundamental_context()
    research = real_research_context()
    context = make_financial_context()
    full = build_prompt(
        "hello",
        context,
        OutboundDataPolicy(),
        make_economic_context(),
        fundamental,  # type: ignore[arg-type]
        research,
    )
    # First rung: the trade block goes (established behaviour).
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(full.content) - 1, raising=True
    )
    trades_dropped = build_prompt(
        "hello",
        context,
        OutboundDataPolicy(),
        make_economic_context(),
        fundamental,  # type: ignore[arg-type]
        research,
    )
    assert "Recent executed trades: omitted" in trades_dropped.content
    # Second rung: just below what the trade-dropped rendering needs.
    monkeypatch.setattr(
        app_settings, "AGENT_MAX_PROMPT_CHARS", len(trades_dropped.content) - 1, raising=True
    )
    fundamental_dropped = build_prompt(
        "hello",
        context,
        OutboundDataPolicy(),
        make_economic_context(),
        fundamental,  # type: ignore[arg-type]
        research,
    )
    assert "Fundamental intelligence: omitted" in fundamental_dropped.content
    # Third rung: just below what THAT rendering needs — the research block goes
    # before the mandatory calendar context does.
    monkeypatch.setattr(
        app_settings,
        "AGENT_MAX_PROMPT_CHARS",
        len(fundamental_dropped.content) - 1,
        raising=True,
    )

    reduced = build_prompt(
        "hello",
        context,
        OutboundDataPolicy(),
        make_economic_context(),
        fundamental,  # type: ignore[arg-type]
        research,
    ).content

    assert "Financial research: omitted" in reduced
    assert "Economic calendar: omitted" not in reduced
    assert CPI_EVENT.title in reduced  # the mandatory calendar survives
    assert len(reduced) <= len(fundamental_dropped.content) - 1


# --- failure boundary ------------------------------------------------------------------------


def test_research_failure_propagates_and_the_llm_is_never_asked() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=_RecordingResearchService(error=RuntimeError("research source unavailable")),
        llm=provider,
    )

    with pytest.raises(RuntimeError, match="research source unavailable"):
        agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    # The existing boundary: no partial answer is fabricated from a failed read.
    assert provider.call_count == 0


def test_the_research_context_is_built_after_the_fundamental_context() -> None:
    order: list[str] = []

    class _OrderedFundamental(FundamentalIntelligenceService):
        def build_context(self, calendar, focus_symbol=None):  # type: ignore[no-untyped-def]
            order.append("fundamental")
            return super().build_context(calendar, focus_symbol)

    class _OrderedResearch(_RecordingResearchService):
        def build_research(self, from_time, to_time, focus_symbols=()):  # type: ignore[no-untyped-def]
            order.append("research")
            return super().build_research(from_time, to_time, focus_symbols)

    agent = AgentService(
        _RecordingFinancialContextService(make_financial_context()),
        FakeLLMProvider(),
        economic_intelligence_service=_RecordingEconomicService(make_economic_context()),
        fundamental_intelligence_service=_OrderedFundamental(
            news_service=NewsService(FakeNewsProvider(), max_items=20)
        ),
        financial_research_service=_OrderedResearch(real_research_context()),
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert order == ["fundamental", "research"]


# --- existing behaviour unchanged --------------------------------------------------------------


def test_without_a_research_service_the_prompt_is_unchanged() -> None:
    economic_context = make_economic_context()
    fundamental_service = _RecordingFundamentalService(real_fundamental_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(economic_context),
        fundamental=fundamental_service,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Financial research" not in content
    # Byte-identical to the prompt the pre-research builder produces.
    assert content == build_prompt(
        "What is happening with XAUUSD today?",
        make_financial_context(),
        OutboundDataPolicy.from_settings(),
        economic_context,
        fundamental_service.context,  # type: ignore[arg-type]
    ).content


def test_the_agent_envelope_is_unchanged_by_the_research_composition() -> None:
    research = _RecordingResearchService(real_research_context())
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    response = agent.handle("hello", broker_id=42, now=AS_OF)

    assert response.broker_id == 42
    assert response.request == "hello"
    assert response.answer == FakeLLMProvider().response


def test_the_research_rendering_is_deterministic() -> None:
    def content_once() -> str:
        provider = FakeLLMProvider()
        agent, _, _ = make_agent(
            economic=_RecordingEconomicService(make_economic_context()),
            fundamental=_RecordingFundamentalService(real_fundamental_context()),
            research=_RecordingResearchService(real_research_context()),
            llm=provider,
        )
        agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)
        return provider.prompts[0].content

    assert content_once() == content_once()


# --- Step 51: the focus instrument is resolved through the tenant's catalog --------------------


def test_the_detected_focus_instrument_is_resolved_before_it_is_researched() -> None:
    # The broker lists the instrument under its own spelling, so research is
    # built for the BROKER's symbol rather than for the label the request text
    # produced. The agent owns no catalog logic: it asks the research service.
    # The double is built WITH the same catalog, so it resolves for real.
    research = _RecordingResearchService(
        real_research_context(), instrument_service=broker_catalog("xauusd")
    )
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.resolutions[0].requested == ("XAUUSD",)
    assert research.resolutions[0].resolved == ("xauusd",)
    assert research.calls[0][2] == ("xauusd",)


def test_a_focus_instrument_the_broker_does_not_offer_builds_no_research() -> None:
    # The request names XAUUSD and this broker offers no XAUUSD in any spelling.
    # The label is never treated as a real instrument: nothing is researched for
    # it, and the question is still answered from the mandatory context rather
    # than failing.
    research = _RecordingResearchService(
        real_research_context(), instrument_service=broker_catalog("EURUSD", "EURUSD.m")
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.resolutions[0].unresolved == ("XAUUSD",)
    assert research.calls == []  # no research built, so no look-back news fetched
    content = provider.prompts[0].content
    assert "Financial research" not in content
    assert "Economic calendar for today" in content
    assert "Fundamental intelligence" in content


def test_an_unconfirmed_focus_instrument_never_reaches_the_prompt() -> None:
    # Structural: the prompt for an unresolvable focus instrument is byte-identical
    # to the prompt of the same request with no research service wired at all.
    request = "What is happening with XAUUSD today?"
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=_RecordingResearchService(
            real_research_context(), instrument_service=broker_catalog("EURUSD")
        ),
        llm=provider,
    )
    agent.handle(request, broker_id=1, now=AS_OF)
    unconfirmed = provider.prompts[0].content

    baseline_provider = FakeLLMProvider()
    baseline_agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        llm=baseline_provider,
    )
    baseline_agent.handle(request, broker_id=1, now=AS_OF)

    assert unconfirmed == baseline_provider.prompts[0].content


def test_a_suffixed_broker_still_produces_the_research_block() -> None:
    # The regression this fix exists for: detection yields XAUUSD, the broker's
    # catalog only offers XAUUSD.r, and the research block is built in the
    # broker's own spelling instead of being dropped.
    research = _RecordingResearchService(
        real_research_context(), instrument_service=broker_catalog("XAUUSD.r")
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.resolutions[0].unresolved == ()
    assert research.resolutions[0].resolved == ("XAUUSD.r",)
    assert research.calls[0][2] == ("XAUUSD.r",)


def test_the_suffixed_brokers_spelling_reaches_the_prompt() -> None:
    # The whole real pipeline over a suffixed broker: real research service, real
    # resolution, real grading, real prompt rendering.
    provider = FakeLLMProvider()
    agent = AgentService(
        FinancialContextService(
            account_service=AccountInfoService(_FakeAccountInfoProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
            trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=(TRADE,))),
        ),
        provider,
        economic_intelligence_service=_RecordingEconomicService(make_economic_context()),
        fundamental_intelligence_service=FundamentalIntelligenceService(
            news_service=NewsService(FakeNewsProvider(), max_items=20)
        ),
        financial_research_service=FinancialResearchService(
            NewsService(FakeNewsProvider(), max_items=20), broker_catalog("XAUUSD.r")
        ),
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Financial research (published source facts" in content
    # The broker's spelling, never the detected label.
    assert "focus: XAUUSD.r" in content
    assert "focus: XAUUSD)" not in content


def test_several_suffixed_variants_build_no_research_instead_of_guessing() -> None:
    research = _RecordingResearchService(
        real_research_context(), instrument_service=broker_catalog("XAUUSD.r", "XAUUSD.m")
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.resolutions[0].unresolved == ("XAUUSD",)
    assert research.calls == []  # different instruments: never a coin flip
    assert "Financial research" not in provider.prompts[0].content
    assert "Economic calendar for today" in provider.prompts[0].content


def test_a_catalog_failure_propagates_and_the_llm_is_never_asked() -> None:
    research = _RecordingResearchService(
        real_research_context(),
        instrument_service=InstrumentService(_FailingInstrumentProvider()),
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    with pytest.raises(RuntimeError):
        agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert provider.call_count == 0  # the existing error boundary: no answer is invented
    assert research.calls == []


def test_a_request_without_a_focus_instrument_never_touches_the_catalog() -> None:
    research = _RecordingResearchService(
        real_research_context(instrument_service=broker_catalog("XAUUSD"))
    )
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
        llm=provider,
    )

    agent.handle("What is happening today?", broker_id=1, now=AS_OF)

    assert research.resolutions == []  # no catalog read, no research fetch
    assert research.calls == []
    assert provider.call_count == 1


def test_research_without_a_broker_catalog_keeps_the_step_49_behaviour() -> None:
    # A deployment with no instrument service cannot verify a name, so the names
    # are used as given: unverified, which is exactly what Step 49 did.
    research = _RecordingResearchService(real_research_context())
    agent, _, _ = make_agent(
        economic=_RecordingEconomicService(make_economic_context()),
        fundamental=_RecordingFundamentalService(real_fundamental_context()),
        research=research,
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    assert research.resolutions[0].resolved == ("XAUUSD",)
    assert research.resolutions[0].unresolved == ()
    assert research.calls[0][2] == ("XAUUSD",)


def test_the_profile_grading_still_works_on_a_resolved_broker_spelling() -> None:
    # Step 48 profiles remain optional enhancements applied AFTER resolution: the
    # resolved spelling still grades a direct gold item as the strongest level,
    # and an unprofiled symbol stays researchable beside it.
    context = real_research_context(
        focus_symbols=("XAUUSD.r", "COFFEE"),
        instrument_service=broker_catalog("XAUUSD.r", "COFFEE"),
    )

    assert context.focus_symbols == ("COFFEE", "XAUUSD.r")
    assert context.unresolved_symbols == ()
    gold = next(entry for entry in context.news if "Gold ETF flows" in entry.item.title)
    assert gold.relevance.value == "RELEVANT"
    assert gold.matched_instruments == ("XAUUSD.R",)
    assert context.instruments == ("COFFEE", "XAUUSD.r")


def test_the_real_sources_flow_through_the_whole_agent_pipeline() -> None:
    # Real AgentService + real services over the deterministic placeholder
    # sources, including the research slice over its own feed.
    provider = FakeLLMProvider()
    agent = AgentService(
        FinancialContextService(
            account_service=AccountInfoService(_FakeAccountInfoProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
            trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=(TRADE,))),
        ),
        provider,
        economic_intelligence_service=_RecordingEconomicService(make_economic_context()),
        fundamental_intelligence_service=FundamentalIntelligenceService(
            news_service=NewsService(FakeNewsProvider(), max_items=20)
        ),
        financial_research_service=FinancialResearchService(
            NewsService(FakeNewsProvider(), max_items=20)
        ),
    )

    agent.handle("What is happening with XAUUSD today?", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert "Financial research (published source facts" in content
    assert "fake-development-placeholder" in content
    assert "BEFORE the calendar window above" in content
