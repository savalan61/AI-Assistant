"""Step 57 tests: the deterministic relevance boundary on LLM-facing evidence.

Covers the boundary only — the rule that the calendar, fundamental and research
blocks present ONLY the items the deterministic relevance layer related to an
instrument in play, that the withheld items are counted rather than silently
dropped, that the boundary never touches what was not assessed (no instrument in
play), that retained items keep their provenance and factor notes, and that the
empty / unavailable / UNKNOWN states are exactly as they were.

The leak these tests pin: the offline QuantGist/placeholder calendar grades the
Japan BoJ decision NOT_OBVIOUSLY_RELEVANT for a XAUUSD position, and the model
still discussed it as a gold driver because the item was handed over as evidence.
The boundary makes that impossible by withholding the item, not by asking the
model to ignore it.

Require none of: real MT5, PostgreSQL, network, credentials, a news vendor or an
external LLM. Everything runs over the deterministic placeholder sources.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.news import NewsItem
from app.providers.position import Position, PositionType
from app.services.account import AccountInfoService
from app.services.agent import AgentService, build_prompt
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    EconomicIntelligenceService,
    RelevanceLevel,
)
from app.services.financial_context import FinancialContext, FinancialContextService
from app.services.fundamental_intelligence import (
    FinancialResearchService,
    FundamentalContext,
    FundamentalIntelligenceService,
)
from app.services.news import NewsService
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
LOOKBACK_DAYS = 3

NOT_RELEVANT = RelevanceLevel.NOT_OBVIOUSLY_RELEVANT

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


def position(symbol: str, ticket: int) -> Position:
    return Position(
        ticket=ticket,
        symbol=symbol,
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("100.00"),
        current_price=Decimal("101.00"),
        profit=Decimal("10.00"),
    )


XAUUSD = position("XAUUSD", 1)
USOIL = position("USOIL", 2)
US100 = position("US100", 3)

# The placeholder catalog's own titles, so a test can name the item it expects
# withheld without depending on an item_id format.
BOJ_TITLE = "Japan BoJ Interest Rate Decision (placeholder)"
CPI_TITLE = "US Consumer Price Index (CPI) YoY (placeholder)"
CRUDE_TITLE = "US API Crude Oil Stock Change (placeholder)"
ECB_TITLE = "ECB officials speak on the euro-area outlook (placeholder)"


class _FakeAccountInfoProvider(AccountInfoProvider):
    def __init__(self, account: AccountInfo = ACCOUNT) -> None:
        self.account = account

    def get_account_info(self) -> AccountInfo:
        return self.account


def economic_context(*positions: Position) -> EconomicIntelligenceContext:
    """The real economic-intelligence service over deterministic offline sources."""
    return EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider(positions=positions)),
    ).build_today_context(now=AS_OF)


# Distinguishes "no news source configured" (None) from "use the placeholder
# feed" (the default) without making the caller spell the fake out.
_DEFAULT_NEWS = object()


def fundamental_context(
    calendar: EconomicIntelligenceContext,
    focus_symbol: str | None = None,
    *,
    news_service: NewsService | None | object = _DEFAULT_NEWS,
) -> FundamentalContext:
    resolved: NewsService | None = (
        NewsService(FakeNewsProvider(), max_items=20)
        if news_service is _DEFAULT_NEWS
        else news_service  # type: ignore[assignment]
    )
    return FundamentalIntelligenceService(news_service=resolved).build_context(
        calendar, focus_symbol=focus_symbol
    )


def financial_context(*positions: Position) -> FinancialContext:
    return FinancialContext(
        broker_id=1,
        as_of=AS_OF,
        account=ACCOUNT,
        positions=positions,
        trade_history=(),
        portfolio_intelligence=build_portfolio_intelligence(ACCOUNT, positions, AS_OF),
    )


def news_item(title: str, item_id: str = "news-item") -> NewsItem:
    return NewsItem(
        item_id=item_id,
        published_at=datetime(2026, 9, 16, 7, 5, tzinfo=UTC),
        publisher="Example Newswire (placeholder)",
        title=title,
        summary="Placeholder excerpt.",
        url=None,
        instruments=(),
        currencies=(),
        categories=(),
    )


def calendar_body(content: str) -> str:
    return content.split("Economic calendar for today", 1)[1].split(
        "Fundamental intelligence", 1
    )[0]


def fundamental_body(content: str) -> str:
    body = content.split("Fundamental intelligence", 1)[1]
    return body.split("Financial research", 1)[0] if "Financial research" in body else body


def research_body(content: str) -> str:
    return content.split("Financial research", 1)[1]


def calendar_evidence(
    calendar: EconomicIntelligenceContext,
) -> tuple[set[str], set[str], int]:
    """What the deterministic layer says about the calendar: (related titles,

    withheld titles, number of withheld events). The count is per event and the
    titles are per title, so a multi-day window cannot make the two disagree.
    """
    related = [i for i in calendar.events if i.overall_relevance is not NOT_RELEVANT]
    withheld = [i for i in calendar.events if i.overall_relevance is NOT_RELEVANT]
    return (
        {i.event.title for i in related},
        {i.event.title for i in withheld},
        len(withheld),
    )


def news_evidence(context: FundamentalContext) -> tuple[set[str], set[str], int]:
    """What the deterministic layer says about the news items: (related titles,

    withheld titles, number of withheld items).
    """
    related = [e for e in context.news if e.relevance is not NOT_RELEVANT]
    withheld = [e for e in context.news if e.relevance is NOT_RELEVANT]
    return (
        {e.item.title for e in related},
        {e.item.title for e in withheld},
        len(withheld),
    )


# --- the calendar block ------------------------------------------------------------------


def test_a_boj_decision_unrelated_to_the_held_gold_position_is_withheld() -> None:
    # The exact case from the Step 56 quality evaluation: a JPY BoJ decision,
    # NOT_OBVIOUSLY_RELEVANT for a XAUUSD position, was handed to the model and
    # discussed as a gold driver.
    calendar = economic_context(XAUUSD)
    prompt = build_prompt(
        "What is happening with XAUUSD right now?",
        financial_context(XAUUSD),
        economic=calendar,
        fundamental=fundamental_context(calendar, "XAUUSD"),
    )

    body = calendar_body(prompt.content)
    assert BOJ_TITLE not in body
    # The releases the layer DID relate to gold's USD leg survive.
    assert CPI_TITLE in body
    assert "US Initial Jobless Claims (placeholder)" in body
    assert CRUDE_TITLE in body
    assert "- (3 economic event(s) omitted" in body
    # Nothing was invented and nothing was reclassified: the block still states
    # the source and the window it covers.
    assert "fake-development-placeholder" in body
    assert calendar.window_from.isoformat() in body


@pytest.mark.parametrize(
    "holdings",
    [
        (XAUUSD,),
        (USOIL,),
        (US100,),
        (XAUUSD, USOIL, US100),
    ],
    ids=["gold", "crude", "index", "all-three"],
)
def test_the_calendar_block_is_exactly_the_relevance_layers_own_verdict(
    holdings: tuple[Position, ...],
) -> None:
    calendar = economic_context(*holdings)
    related_titles, withheld_titles, withheld_count = calendar_evidence(calendar)
    assert related_titles, "the placeholder catalog must relate something to these instruments"

    prompt = build_prompt(
        "What is happening today?",
        financial_context(*holdings),
        economic=calendar,
        fundamental=fundamental_context(calendar),
    )

    body = calendar_body(prompt.content)
    # Exact set equality: every related event is evidence, no withheld event is.
    rendered = {item.event.title for item in calendar.events if item.event.title in body}
    assert rendered == related_titles
    assert all(title not in body for title in withheld_titles)
    if withheld_count:
        assert f"- ({withheld_count} economic event(s) omitted" in body
    else:
        assert "economic event(s) omitted" not in body


def test_crude_and_index_relevant_events_are_kept_for_their_own_instruments() -> None:
    calendar = economic_context(USOIL, US100)
    prompt = build_prompt(
        "What is happening with USOIL today?",
        financial_context(USOIL, US100),
        economic=calendar,
        fundamental=fundamental_context(calendar, "USOIL"),
    )

    body = calendar_body(prompt.content)
    # A crude-oil event is relevant to USOIL through its documented profile, and
    # a US inflation release is a macro factor for US100, so neither is withheld
    # just because gold is not held.
    assert CRUDE_TITLE in body
    assert CPI_TITLE in body


def test_the_mandatory_calendar_is_rendered_whole_when_nothing_was_assessed() -> None:
    # With no instrument in play every event is trivially NOT_OBVIOUSLY_RELEVANT,
    # which is not a verdict about relevance: the published calendar is kept.
    calendar = economic_context()
    prompt = build_prompt(
        "What is on the economic calendar today?",
        financial_context(),
        economic=calendar,
        fundamental=fundamental_context(calendar),
    )

    body = calendar_body(prompt.content)
    for item in calendar.events:
        assert item.event.title in body
    assert "omitted" not in body


# --- the fundamental block ---------------------------------------------------------------


def test_news_unrelated_to_the_instruments_in_play_is_withheld() -> None:
    calendar = economic_context(XAUUSD)
    context = fundamental_context(calendar, "XAUUSD")
    prompt = build_prompt(
        "What news matters for XAUUSD today?",
        financial_context(XAUUSD),
        economic=calendar,
        fundamental=context,
    )

    body = fundamental_body(prompt.content)
    related_titles, withheld_titles, withheld_count = news_evidence(context)
    assert related_titles and withheld_count
    assert all(title in body for title in related_titles)
    assert all(title not in body for title in withheld_titles)
    # The euro-area item (EUR is no leg of a gold instrument) and the untagged
    # shipping wrap are the withheld ones; the untagged Fed item is kept because
    # the keyword map relates it to gold through USD.
    assert ECB_TITLE not in body
    assert "Federal Reserve minutes due later today (placeholder)" in body
    assert "Gold ETF flows reported steady ahead of US data (placeholder)" in body
    assert f"- ({withheld_count} news item(s) omitted" in body


def test_an_item_relevant_to_another_instrument_is_kept_for_that_instrument() -> None:
    # The same published item is evidence for one instrument and not for another,
    # and the boundary applies per instrument in play: it is never a global
    # "this item is irrelevant" verdict about the item itself.
    gold_news = news_item(
        "Gold demand rises as central banks increase purchases", "news-gold"
    )
    apple_news = news_item("Apple announces a new consumer device", "news-apple")
    items = (gold_news, apple_news)

    def body_for(holding: Position, focus: str) -> str:
        calendar = economic_context(holding)
        context = fundamental_context(
            calendar,
            focus,
            news_service=NewsService(FakeNewsProvider(items=items), max_items=20),
        )
        return fundamental_body(
            build_prompt(
                f"What is happening with {focus} today?",
                financial_context(holding),
                economic=calendar,
                fundamental=context,
            ).content
        )

    gold_body = body_for(XAUUSD, "XAUUSD")
    assert gold_news.title in gold_body
    assert apple_news.title not in gold_body

    index_body = body_for(US100, "US100")
    assert apple_news.title in index_body
    assert gold_news.title not in index_body


def test_retained_news_keeps_its_provenance_and_factor_note() -> None:
    calendar = economic_context(XAUUSD)
    context = fundamental_context(calendar, "XAUUSD")
    body = fundamental_body(
        build_prompt(
            "What news matters for XAUUSD today?",
            financial_context(XAUUSD),
            economic=calendar,
            fundamental=context,
        ).content
    )

    # The boundary removes items only: everything it retains is rendered exactly
    # as before, with its source, its publication time and its matched factor.
    for entry in context.news:
        if entry.relevance is NOT_RELEVANT:
            continue
        assert entry.item.title in body
        assert entry.item.publisher in body
        assert entry.item.published_at.isoformat() in body
        assert f"relevance {entry.relevance.value}" in body
    assert "news source: fake-development-placeholder" in body
    assert "(direct factor for XAUUSD: precious metals)" in body


def test_the_published_items_are_rendered_when_no_instrument_is_in_play() -> None:
    # No focus and no positions: the news layer classified against nothing, so
    # nothing is withheld on the strength of a vacuous verdict.
    calendar = economic_context()
    context = fundamental_context(calendar)
    assert context.instruments == ()
    body = fundamental_body(
        build_prompt(
            "Any news today?",
            financial_context(),
            economic=calendar,
            fundamental=context,
        ).content
    )

    assert context.news
    for entry in context.news:
        assert entry.item.title in body
    assert "omitted" not in body


# --- the research block ------------------------------------------------------------------


def test_research_evidence_is_bounded_by_the_focus_instruments_relevance() -> None:
    calendar = economic_context(XAUUSD)
    research = FinancialResearchService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    ).build_research(
        calendar.window_from - timedelta(days=LOOKBACK_DAYS),
        calendar.window_from,
        ("XAUUSD",),
    )
    prompt = build_prompt(
        "What is happening with XAUUSD today?",
        financial_context(XAUUSD),
        economic=calendar,
        fundamental=fundamental_context(calendar, "XAUUSD"),
        research=research,
    )

    body = research_body(prompt.content)
    related = [e for e in research.news if e.relevance is not NOT_RELEVANT]
    withheld_count = len(research.news) - len(related)
    assert related and withheld_count
    for entry in related:
        assert entry.item.title in body
    for entry in research.news:
        if entry.relevance is NOT_RELEVANT:
            assert entry.item.title not in body
    assert f"- ({withheld_count} research item(s) omitted" in body
    assert "news source: fake-development-placeholder" in body


# --- the empty, unavailable and UNKNOWN states stay distinct ------------------------------


def test_an_unavailable_news_source_is_still_unavailable() -> None:
    calendar = economic_context(XAUUSD)
    context = fundamental_context(calendar, "XAUUSD", news_service=None)
    body = fundamental_body(
        build_prompt(
            "Any news today?",
            financial_context(XAUUSD),
            economic=calendar,
            fundamental=context,
        ).content
    )

    assert "news unavailable" in body
    assert "No news source is configured" in body
    assert "published no news items for today" not in body
    assert "is relevant to the instruments in play" not in body


def test_an_empty_feed_is_still_stated_as_the_source_publishing_nothing() -> None:
    calendar = economic_context(XAUUSD)
    context = fundamental_context(
        calendar, "XAUUSD", news_service=NewsService(FakeNewsProvider(items=()), max_items=20)
    )
    body = fundamental_body(
        build_prompt(
            "Any news today?",
            financial_context(XAUUSD),
            economic=calendar,
            fundamental=context,
        ).content
    )

    assert "this source published no news items for today" in body
    assert "is relevant to the instruments in play" not in body


def test_an_all_withheld_feed_is_not_reported_as_an_empty_feed() -> None:
    unrelated = news_item("Market wrap: energy and shipping costs in focus", "news-none")
    calendar = economic_context(XAUUSD)
    context = fundamental_context(
        calendar,
        "XAUUSD",
        news_service=NewsService(FakeNewsProvider(items=(unrelated,)), max_items=20),
    )
    assert context.news and context.news[0].relevance is NOT_RELEVANT
    body = fundamental_body(
        build_prompt(
            "Any news today?",
            financial_context(XAUUSD),
            economic=calendar,
            fundamental=context,
        ).content
    )

    assert "no news item this source published for today is relevant" in body
    assert "this source published no news items for today" not in body


def test_an_empty_calendar_is_not_reported_as_an_all_withheld_calendar() -> None:
    calendar = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider(events=())),
        position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
    ).build_today_context(now=AS_OF)
    body = calendar_body(
        build_prompt(
            "hello",
            financial_context(XAUUSD),
            economic=calendar,
            fundamental=fundamental_context(calendar, "XAUUSD"),
        ).content
    )

    assert "no economic events are published for today" in body
    assert "is relevant to the instruments in play" not in body


def test_the_unbounded_unknown_exposure_state_is_unchanged() -> None:
    calendar = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider(events=())),
        position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
    ).build_today_context(now=AS_OF)
    context = fundamental_context(calendar, "XAUUSD", news_service=None)
    content = build_prompt(
        "What is threatening my position?",
        financial_context(XAUUSD),
        economic=calendar,
        fundamental=context,
    ).content

    assert "UNKNOWN" in content
    assert "could not be assessed" in content


# --- end to end through the real Agent ----------------------------------------------------


def test_the_agent_sends_the_boundary_checked_evidence_to_the_llm() -> None:
    positions = (XAUUSD,)
    financial = FinancialContextService(
        account_service=AccountInfoService(_FakeAccountInfoProvider()),
        position_service=PositionService(FakePositionProvider(positions=positions)),
        trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=())),
    )
    economic = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider(positions=positions)),
    )
    fundamental = FundamentalIntelligenceService(
        news_service=NewsService(FakeNewsProvider(), max_items=20)
    )
    provider = FakeLLMProvider()
    agent = AgentService(
        financial,
        provider,
        economic_intelligence_service=economic,
        fundamental_intelligence_service=fundamental,
    )

    agent.handle(
        "What is happening with XAUUSD right now? Separate the published facts from "
        "your interpretation.",
        broker_id=1,
        now=AS_OF,
    )

    content = provider.prompts[0].content
    # The mandatory calendar still reaches the model, with its provenance, and
    # the BoJ decision it graded unrelated to gold is no longer handed over as
    # evidence it could promote into the analysis.
    assert "Economic calendar for today" in content
    assert "fake-development-placeholder" in content
    assert BOJ_TITLE not in content
    assert CPI_TITLE in content
    assert "economic event(s) omitted" in content
    # The boundary only withholds: the exposure and the labelled facts the model
    # reasons over are still there, unchanged and uninterpreted.
    assert "Position fundamental exposure" in content
    assert "published source facts, not analysis" in content
    assert "relevance is a discrete relatedness level" in content
