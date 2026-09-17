"""Deterministic context fixtures and the recording LLM for the benchmark.

Everything here is the SAME production pipeline the application runs — real
AgentService, real economic/fundamental/research services — over the existing
deterministic development providers (fake calendar, fake news, fake positions,
fake instrument catalog) and an injected RecordingLLMProvider. No network, no
MT5, no database, no credentials, no OpenRouter.
"""
from datetime import UTC, datetime
from decimal import Decimal

from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.economic_calendar import EconomicCalendarProvider
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_instrument import FakeInstrumentProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.llm import LLMPrompt, LLMProvider
from app.providers.position import Position, PositionType
from app.services.account import AccountInfoService
from app.services.agent import AgentService, OutboundDataPolicy
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.financial_context import FinancialContextService
from app.services.fundamental_intelligence import FundamentalIntelligenceService
from app.services.fundamental_intelligence.research import FinancialResearchService
from app.services.instruments import InstrumentService
from app.services.news import NewsService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

# One pinned reference instant for every scenario, so every window, timestamp
# and relevance verdict in the benchmark is reproducible forever.
BENCHMARK_AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
BENCHMARK_WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
BENCHMARK_WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

ACCOUNT = AccountInfo(
    login=10001,
    name="Benchmark Trader",
    balance=Decimal("10000.00"),
    equity=Decimal("10050.00"),
    margin=Decimal("250.00"),
    free_margin=Decimal("9800.00"),
    margin_level=4020.0,
    currency="USD",
    server="Benchmark-Server",
)


def _position(symbol: str, ticket: int) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("3642.50"),
        current_price=Decimal("3648.20"),
        profit=Decimal("57.00"),
    )


POSITION_XAUUSD = _position("XAUUSD", 123456789)
POSITION_NICKEL = _position("NICKEL", 987654321)

# The questions the benchmark asks. Wording mirrors the real Step 56 quality
# evaluation so the baseline stays comparable to it.
QUESTION_XAUUSD = (
    "What is happening with XAUUSD right now? Analyze the fundamental factors "
    "affecting gold based only on the available economic calendar, financial "
    "news, and fundamental context. Clearly separate published facts from your "
    "interpretation. Do not provide trading advice, buy/sell signals, price "
    "targets, or probabilities."
)
QUESTION_USOIL = (
    "What is happening with USOIL today? Analyze the fundamental factors "
    "affecting crude oil based only on the available context."
)
QUESTION_NASDAQ = (
    "What is affecting NASDAQ today from a fundamental perspective?"
)
QUESTION_NICKEL = (
    "What is happening with NICKEL today from a fundamental perspective?"
)
QUESTION_POSITIONS = "How exposed am I today?"
QUESTION_EMPTY = "Any news today?"


class RecordingLLMProvider(LLMProvider):
    """Capture the exact prompt and answer for every benchmark request.

    The recording replaces the model: the captured prompt IS the thing the
    prompt checks grade, and the fixed answer is the thing the response grader
    is demonstrated against. A future live runner substitutes a real provider
    here without touching any check.
    """

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[LLMPrompt] = []

    def complete(self, prompt: LLMPrompt) -> str:
        self.prompts.append(prompt)
        return self.response


class _BenchmarkAccountInfoProvider(AccountInfoProvider):
    def __init__(self, account: AccountInfo) -> None:
        self.account = account

    def get_account_info(self) -> AccountInfo:
        return self.account


class _FixedCalendarProvider(EconomicCalendarProvider):
    """Serve ONE pinned event tuple regardless of the requested window.

    The catalog carries no publication dates (it is the scheduled-events view
    the real vendors expose), so a fixed day's roster is served verbatim and the
    benchmark asserts relevance against exactly those events. The provenance
    marker is the deterministic development one, so provenance checks exercise
    the same "placeholder can never read as live" rule production applies.
    """

    source = "fake-development-placeholder"

    def __init__(self, events: tuple) -> None:
        self._events = events

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple:
        return self._events


def build_economic_service(*positions: Position, events: tuple) -> EconomicIntelligenceService:
    """The real economic-intelligence service over the pinned calendar."""
    return EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(_FixedCalendarProvider(events)),
        position_service=PositionService(FakePositionProvider(positions=positions)),
    )


def build_fundamental_service() -> FundamentalIntelligenceService:
    """The real fundamental service over the deterministic development feed."""
    return FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    )


def build_research_service(catalog: tuple[str, ...] = ("XAUUSD", "USOIL", "NAS100")) -> FinancialResearchService:
    """The real research service, resolving through a pinned broker catalog."""
    provider = FakeInstrumentProvider()
    wanted = tuple(instrument for instrument in provider.instruments if instrument.symbol in catalog)
    return FinancialResearchService(
        news_service=NewsService(FakeNewsProvider(), max_items=20),
        instrument_service=InstrumentService(FakeInstrumentProvider(wanted)),
    )


def build_agent(
    llm: LLMProvider,
    *,
    positions: tuple[Position, ...] = (POSITION_XAUUSD,),
    events: tuple = (),
    research: FinancialResearchService | None = None,
    with_news: bool = True,
    policy: OutboundDataPolicy | None = None,
) -> AgentService:
    """Assemble the real agent boundary exactly as the composition root does."""
    financial = FinancialContextService(
        account_service=AccountInfoService(_BenchmarkAccountInfoProvider(ACCOUNT)),
        position_service=PositionService(FakePositionProvider(positions=positions)),
        trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=())),
    )
    economic = build_economic_service(*positions, events=events)
    fundamental = (
        build_fundamental_service()
        if with_news
        else FundamentalIntelligenceService(news_service=None)
    )
    return AgentService(
        financial_context_service=financial,
        llm_provider=llm,
        data_policy=policy,
        economic_intelligence_service=economic,
        fundamental_intelligence_service=fundamental,
        financial_research_service=research,
    )
