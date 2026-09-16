"""Step 48 tests: instrument-aware fundamental intelligence, end to end (offline).

Covers the capability the product needs: several instruments in play (gold, crude
and an equity index) with mixed calendar events and news items, where each
instrument gets its OWN deterministic relevance, the same item can be relevant to
more than one instrument at different levels, unrelated instruments are left
alone, position exposure links back to the exact drivers it found, provenance is
preserved, UNKNOWN is preserved when evidence is insufficient, and nothing
anywhere is a direction, a forecast or a recommendation.

Also covers the realistic user questions the roadmap names, and the agent prompt
block that renders the matched factor.

Require none of: real MT5, PostgreSQL, network, credentials, a news vendor or an
external LLM. Everything runs over the deterministic placeholder sources.
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.account_info import AccountInfo
from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.news import NewsItem
from app.providers.position import Position, PositionType
from app.services.agent import build_prompt
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    EconomicIntelligenceService,
    RelevanceLevel,
)
from app.services.financial_context import FinancialContext
from app.services.fundamental_intelligence import (
    ExposureStatus,
    FundamentalContext,
    FundamentalIntelligenceService,
    detect_focus_symbol,
)
from app.services.instrument_intelligence import FundamentalDomain, RelevanceKind
from app.services.news import NewsService
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService

AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

NEWS_TAG = "fake-development-placeholder"
CALENDAR_TAG = "fake-development-placeholder"

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
NAS100 = position("NAS100", 3)
US30 = position("US30", 4)

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

FOMC_EVENT = EconomicEvent(
    event_id="evt-usd-fomc",
    timestamp=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    currency="USD",
    title="FOMC Rate Decision",
    impact=EventImpact.HIGH,
    forecast="4.25%",
    previous="4.50%",
    actual=None,
)

BOJ_EVENT = EconomicEvent(
    event_id="evt-jpy-boj",
    timestamp=datetime(2026, 9, 16, 3, 0, tzinfo=UTC),
    currency="JPY",
    title="Japan BoJ Interest Rate Decision",
    impact=EventImpact.HIGH,
    forecast="0.50%",
    previous="0.50%",
    actual=None,
)

CLAIMS_EVENT = EconomicEvent(
    event_id="evt-usd-claims",
    timestamp=datetime(2026, 9, 16, 13, 30, tzinfo=UTC),
    currency="USD",
    title="US Initial Jobless Claims",
    impact=EventImpact.MEDIUM,
    forecast="220K",
    previous="215K",
    actual="218K",
)

CASES = (CPI_EVENT, FOMC_EVENT, BOJ_EVENT, CLAIMS_EVENT)
CASE_IDS = tuple(event.event_id for event in CASES)


def news_item(item_id: str, title: str, *, instruments: tuple[str, ...] = (), currencies: tuple[str, ...] = ()) -> NewsItem:
    return NewsItem(
        item_id=item_id,
        published_at=datetime(2026, 9, 16, 7, 5, tzinfo=UTC),
        publisher="Example Newswire (placeholder)",
        title=title,
        summary="Placeholder excerpt.",
        url=None,
        instruments=instruments,
        currencies=currencies,
        categories=(),
    )


GOLD_ITEM = news_item("news-gold", "Gold demand rises as central banks increase purchases")
OIL_ITEM = news_item("news-oil", "Persian Gulf tensions disrupt crude oil shipments")
CHIP_ITEM = news_item("news-chip", "Major semiconductor export restrictions announced")
APPLE_ITEM = news_item("news-apple", "Apple announces a major product launch")
UNRELATED_ITEM = news_item("news-none", "Market wrap: energy and shipping costs in focus")

ITEMS = (GOLD_ITEM, OIL_ITEM, CHIP_ITEM, APPLE_ITEM, UNRELATED_ITEM)

# Wording that would turn factual exposure reporting into advice or a prediction.
_FORBIDDEN_WORDS = (
    "buy",
    "sell",
    "expect",
    "forecast",
    "predict",
    "target",
    "probability",
    "recommend",
    "should",
    "bullish",
    "bearish",
    "will rise",
    "will fall",
)


def build_context(
    *,
    focus_symbol: str | None = "XAUUSD",
    positions: tuple[Position, ...] = (XAUUSD, USOIL, NAS100),
    events: tuple[EconomicEvent, ...] | None = CASES,
    items: tuple[NewsItem, ...] | None = ITEMS,
    news_service: NewsService | None = None,
    with_news: bool = True,
) -> tuple[EconomicIntelligenceContext, FundamentalContext]:
    """Real services over the deterministic placeholder providers (no network).

    ``with_news=False`` models a deployment with no news source configured at all
    (the explicit "could not be assessed" state rather than "no news").
    """
    calendar_service = EconomicIntelligenceService(
        EconomicCalendarService(FakeEconomicCalendarProvider(events=events)),
        PositionService(FakePositionProvider(positions=positions)),
    )
    calendar = calendar_service.build_today_context(now=AS_OF)
    if not with_news:
        resolved_news: NewsService | None = None
    elif news_service is not None:
        resolved_news = news_service
    else:
        resolved_news = NewsService(FakeNewsProvider(items=items), max_items=20)
    service = FundamentalIntelligenceService(news_service=resolved_news)
    return calendar, service.build_context(calendar, focus_symbol=focus_symbol)


def entries(context: FundamentalContext, item_id: str):
    return next(entry for entry in context.news if entry.item.item_id == item_id)


def level_map(entry) -> dict[str, RelevanceLevel]:
    return {match.symbol: match.level for match in entry.matches}


# --- several instruments in play --------------------------------------------------------


def test_every_instrument_in_play_is_covered_with_its_own_relevance() -> None:
    _, context = build_context()

    assert context.instruments == ("NAS100", "USOIL", "XAUUSD")
    assert len(context.news) == len(ITEMS)
    # Every item is classified against every instrument in play.
    for entry in context.news:
        assert tuple(match.symbol for match in entry.matches) == context.instruments


def test_the_same_item_is_relevant_to_two_instruments_at_different_levels() -> None:
    _, context = build_context()

    entry = entries(context, OIL_ITEM.item_id)
    levels = level_map(entry)

    # Direct for the commodity the item is about, indirect for the others: the
    # graded levels differ per instrument instead of being forced equal.
    assert levels["USOIL"] is RelevanceLevel.RELEVANT
    assert levels["XAUUSD"] is RelevanceLevel.POTENTIALLY_RELEVANT
    assert levels["NAS100"] is RelevanceLevel.POTENTIALLY_RELEVANT
    assert entry.relevance is RelevanceLevel.RELEVANT
    assert entry.matched_instruments == ("NAS100", "USOIL", "XAUUSD")
    assert entry.kind is RelevanceKind.DIRECT
    assert entry.domains == (FundamentalDomain.CRUDE_OIL, FundamentalDomain.GEOPOLITICAL_RISK)


def test_an_unrelated_instrument_is_not_marked_relevant() -> None:
    _, context = build_context()

    gold = entries(context, GOLD_ITEM.item_id)
    apple = entries(context, APPLE_ITEM.item_id)
    unrelated = entries(context, UNRELATED_ITEM.item_id)

    # A metal story concerns only the metal instrument...
    assert gold.matched_instruments == ("XAUUSD",)
    assert level_map(gold)["NAS100"] is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    # ...a single-company story concerns only the equity index...
    assert apple.matched_instruments == ("NAS100",)
    assert level_map(apple)["XAUUSD"] is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert level_map(apple)["USOIL"] is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    # ...and an item that matches nothing is not guessed at. The aggregate reason
    # says so once, and each instrument's own view is explicit too.
    assert unrelated.matched_instruments == ()
    assert unrelated.kind is None
    assert unrelated.domains == ()
    assert "No documented fundamental factor or instrument reference" in unrelated.reason
    own = {match.symbol: match.reason for match in unrelated.matches}
    assert "No currency or instrument reference" in own["XAUUSD"]
    assert "No currency leg" in own["NAS100"]


def test_the_strongest_match_explains_itself_with_kind_and_reason() -> None:
    _, context = build_context()

    chip = entries(context, CHIP_ITEM.item_id)

    assert chip.relevance is RelevanceLevel.RELEVANT
    assert chip.kind is RelevanceKind.DIRECT
    assert chip.domains == (
        FundamentalDomain.TECHNOLOGY_SECTOR,
        FundamentalDomain.TRADE_AND_TARIFFS,
    )
    assert "technology sector" in chip.reason
    assert "direct fundamental factor for NAS100" in chip.reason


# --- position exposure -------------------------------------------------------------------


def test_each_position_links_back_to_the_drivers_it_found() -> None:
    _, context = build_context()
    exposures = {exposure.symbol: exposure for exposure in context.positions}

    gold = exposures["XAUUSD"]
    calendar_ids = {item.event.event_id for item in context.calendar.events}
    news_ids = {entry.item.item_id for entry in context.news}

    assert gold.status is ExposureStatus.KNOWN
    assert gold.relevance is RelevanceLevel.RELEVANT
    assert set(gold.calendar_event_ids) <= calendar_ids
    assert set(gold.news_item_ids) <= news_ids
    # The drivers named are exactly the ones classified as related at some level:
    # the three USD events (JPY is not a leg of a USD-quoted metal) and the three
    # items that reach gold (directly or through a documented transmission).
    assert gold.calendar_event_ids == ("evt-usd-claims", "evt-usd-cpi", "evt-usd-fomc")
    assert gold.news_item_ids == (CHIP_ITEM.item_id, GOLD_ITEM.item_id, OIL_ITEM.item_id)
    # Every named driver is related to THIS position's instrument.
    for entry in context.news:
        if entry.item.item_id in gold.news_item_ids:
            own = {match.symbol: match.level for match in entry.matches}["XAUUSD"]
            assert own is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT


def test_an_index_exposure_uses_its_profile_for_calendar_drivers() -> None:
    _, context = build_context()
    exposures = {exposure.symbol: exposure for exposure in context.positions}

    index = exposures["NAS100"]

    # An index CFD has no currency leg, so its documented profile decides.
    assert index.status is ExposureStatus.KNOWN
    assert "evt-usd-cpi" in index.calendar_event_ids
    assert "evt-usd-fomc" in index.calendar_event_ids
    assert index.relevance is RelevanceLevel.RELEVANT
    assert "documented fundamental profile was used" in index.reason
    assert index.news_item_ids == (APPLE_ITEM.item_id, CHIP_ITEM.item_id, OIL_ITEM.item_id)


def test_exposure_names_the_fundamental_factors_it_matched() -> None:
    _, context = build_context()
    exposures = {exposure.symbol: exposure for exposure in context.positions}

    gold_factors = exposures["XAUUSD"].factors
    index_factors = exposures["NAS100"].factors

    assert "precious metals" in gold_factors
    assert "geopolitical risk" in gold_factors
    assert "inflation and price data" in gold_factors
    assert "the technology sector" in index_factors
    # A factor tied to another market is not listed for an instrument that has no
    # documented exposure to it.
    assert "major technology companies" not in gold_factors
    assert "the technology sector" not in gold_factors
    # The factors are also stated in the human-readable reason.
    assert "precious metals" in exposures["XAUUSD"].reason
    # ...and nowhere is a factor described as a direction or an expectation.
    for exposure in context.positions:
        for forbidden in ("buy", "sell", "expect", "forecast", "predict", "target"):
            assert forbidden not in exposure.reason.lower(), exposure.reason


def test_a_symbol_with_no_profile_and_no_drivers_stays_unknown() -> None:
    # US30 has neither a currency leg nor an instrument profile, and the calendar
    # holds no event for it: missing information, never "no risk".
    _, context = build_context(
        positions=(US30,), focus_symbol=None, events=(CPI_EVENT,), with_news=False
    )

    exposure = context.positions[0]

    assert exposure.symbol == "US30"
    assert exposure.status is ExposureStatus.UNKNOWN
    assert exposure.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert "No currency leg could be identified" in exposure.reason
    assert exposure.factors == ()
    assert context.news_available is False
    assert context.news_unavailable_reason is not None


def test_a_position_with_no_drivers_and_no_news_source_stays_unknown() -> None:
    _, context = build_context(positions=(XAUUSD,), events=(), with_news=False)

    exposure = context.positions[0]

    assert exposure.status is ExposureStatus.UNKNOWN
    # Missing information is never presented as an absence of risk.
    assert "could not be assessed" in exposure.reason
    assert context.news_available is False


# --- provenance, windows and fact-only vocabulary ------------------------------------------


def test_both_sources_keep_their_provenance_and_the_same_window() -> None:
    calendar, context = build_context()

    assert context.calendar.data_source == CALENDAR_TAG
    assert context.news_data_source == NEWS_TAG
    assert context.as_of == AS_OF
    assert (context.window_from, context.window_to) == (WINDOW_FROM, WINDOW_TO)
    assert (context.window_from, context.window_to) == (calendar.window_from, calendar.window_to)


def test_no_reason_or_factor_is_directional_or_predictive() -> None:
    _, context = build_context()
    reasons = [entry.reason for entry in context.news] + [
        exposure.reason for exposure in context.positions
    ]
    factors = " ".join(
        factor for exposure in context.positions for factor in exposure.factors
    ).lower()

    assert reasons
    for reason in reasons:
        lowered = reason.lower()
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in lowered, (forbidden, reason)
    for forbidden in _FORBIDDEN_WORDS:
        assert forbidden not in factors, (forbidden, factors)


def test_the_context_is_deterministic() -> None:
    _, first = build_context()
    _, second = build_context()

    assert first == second


# --- realistic user questions -------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected_focus"),
    [
        ("What is happening with XAUUSD today?", "XAUUSD"),
        ("What fundamental factors matter for XAUUSD?", "XAUUSD"),
        ("What is happening with USOIL today?", "USOIL"),
        ("What fundamental factors matter for USOIL?", "USOIL"),
        ("What is happening with WTI today?", "USOIL"),
        ("What is affecting NASDAQ today from a fundamental perspective?", "NAS100"),
        ("What news and economic events are relevant to NAS100?", "NAS100"),
        # A commodity word is not an instrument name: the documented rule holds,
        # and the answer then covers the instruments actually in play.
        ("What is happening with oil today?", None),
        ("What news is relevant to my current positions?", None),
    ],
)
def test_realistic_questions_resolve_to_an_instrument(
    question: str, expected_focus: str | None
) -> None:
    assert detect_focus_symbol(question) == expected_focus


@pytest.mark.parametrize(
    ("question", "focus", "expected_source"),
    [
        ("What is happening with XAUUSD today?", "XAUUSD", "US Consumer Price Index (CPI) YoY"),
        ("What is happening with USOIL today?", "USOIL", "US Initial Jobless Claims"),
        (
            "What is affecting NASDAQ today from a fundamental perspective?",
            "NAS100",
            "US Consumer Price Index (CPI) YoY",
        ),
    ],
)
def test_a_question_gets_the_mandatory_calendar_and_instrument_aware_news(
    question: str, focus: str, expected_source: str
) -> None:
    # The default deterministic development catalog is used here (no fixtures).
    calendar, context = build_context(focus_symbol=focus, events=None, items=None)

    assert context.focus_symbol == focus
    assert focus in context.instruments
    # The calendar is mandatory: an answer always passes through it.
    assert context.calendar.events, "today's calendar must always be present"
    assert any(expected_source in item.event.title for item in context.calendar.events)
    assert context.calendar.data_source == CALENDAR_TAG
    # News is graded for the instrument the question is about.
    assert context.news, "the deterministic feed publishes items for today"
    focus_levels = {
        match.level
        for entry in context.news
        for match in entry.matches
        if match.symbol == focus
    }
    assert focus_levels, "every item is classified against the focus instrument"
    assert focus_levels != {RelevanceLevel.NOT_OBVIOUSLY_RELEVANT}
    assert calendar.position_symbols == ("NAS100", "USOIL", "XAUUSD")


def test_a_positions_question_covers_every_holding_without_a_focus() -> None:
    _, context = build_context(
        focus_symbol=detect_focus_symbol("What news is relevant to my current positions?"),
        events=None,
        items=None,
    )

    assert context.focus_symbol is None
    assert context.instruments == ("NAS100", "USOIL", "XAUUSD")
    assert {exposure.symbol for exposure in context.positions} == {"NAS100", "USOIL", "XAUUSD"}
    for exposure in context.positions:
        assert exposure.status is ExposureStatus.KNOWN
        assert exposure.news_item_ids or exposure.calendar_event_ids


# --- the agent prompt renders the matched factor ------------------------------------------


def financial_context() -> FinancialContext:
    return FinancialContext(
        broker_id=1,
        as_of=AS_OF,
        account=ACCOUNT,
        positions=(XAUUSD, USOIL, NAS100),
        trade_history=(),
        portfolio_intelligence=build_portfolio_intelligence(ACCOUNT, (XAUUSD, USOIL, NAS100), AS_OF),
    )


def test_the_prompt_states_the_matched_relationship_and_factor() -> None:
    calendar, context = build_context()

    prompt = build_prompt(
        "What is happening with XAUUSD today?",
        financial_context(),
        economic=calendar,
        fundamental=context,
    )

    assert "Fundamental intelligence for XAUUSD" in prompt.content
    # The factor is scoped to the instrument whose profile matched it, so the
    # model cannot read it as a claim about every instrument on the line.
    assert "(direct factor for XAUUSD: precious metals)" in prompt.content
    assert "(direct factor for USOIL: crude oil and refined product prices, geopolitical risk)" in prompt.content
    assert "Relevant fundamental factors:" in prompt.content


def test_an_item_classified_by_the_symbol_view_renders_without_a_factor() -> None:
    tagged = NewsService(
        FakeNewsProvider(
            items=(news_item("news-tagged", "Quarterly statement", instruments=("XAUUSD",)),)
        ),
        max_items=20,
    )
    calendar, context = build_context(news_service=tagged)

    prompt = build_prompt(
        "What is happening with XAUUSD today?",
        financial_context(),
        economic=calendar,
        fundamental=context,
    )

    # No profile factor was involved, so no relationship is claimed.
    assert "relevance POTENTIALLY_RELEVANT for XAUUSD |" in prompt.content
    assert "factor:" not in prompt.content
