"""The Fundamental LLM Quality Benchmark — scenarios and checks (offline).

Nine scenarios grade the prompt the real pipeline sends to the LLM, plus one
end-to-end run and response-grading demonstrations. Every check is an
observable property of the captured prompt; nothing is a subjective score.
Run: python -m pytest benchmarks/ -q
"""
from datetime import UTC, datetime

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.services.account import AccountInfoService
from app.services.agent import AgentService
from app.services.economic_intelligence import RelevanceLevel
from app.services.financial_context import FinancialContextService
from app.services.fundamental_intelligence import FundamentalIntelligenceService
from app.services.news import NewsService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

from benchmarks.fundamental import (
    BOJ_TITLE,
    CAL_NEWS_TITLE,
    CPI_NEWS_TITLE,
    CPI_TITLE,
    CRUDE_TITLE,
    ECB_NEWS_TITLE,
    ETF_NEWS_TITLE,
    EXPOSURE_HEADER,
    FACTS_LABEL,
    FED_NEWS_TITLE,
    GDP_TITLE,
    IP_TITLE,
    NEWS_EMPTY,
    NEWS_UNAVAILABLE,
    SAFETY_FRAME,
    SHIP_NEWS_TITLE,
    SUPPLIED_NUMBERS,
    UNKNOWN_EXPOSURE,
    WINDOW,
    Scenario,
    ScenarioCheck,
    grade_response,
    run_scenario,
    withheld_titles_for,
)

from benchmarks.providers import (
    ACCOUNT,
    BENCHMARK_AS_OF,
    POSITION_NICKEL,
    POSITION_XAUUSD,
    QUESTION_NASDAQ,
    QUESTION_NICKEL,
    QUESTION_POSITIONS,
    QUESTION_USOIL,
    QUESTION_XAUUSD,
    RecordingLLMProvider,
    _BenchmarkAccountInfoProvider,
    build_agent,
    build_economic_service,
    build_research_service,
)

FAKE_ANSWER = (
    "Deterministic development placeholder: no external model was called and no "
    "financial advice is provided."
)
PROVENANCE = "fake-development-placeholder"
XAUUSD_POSITION = POSITION_XAUUSD
USOIL_POSITION = POSITION_XAUUSD.__class__(
    ticket=2, symbol="USOIL", type=POSITION_XAUUSD.type,
    volume=POSITION_XAUUSD.volume, open_price=POSITION_XAUUSD.open_price,
    current_price=POSITION_XAUUSD.current_price, profit=POSITION_XAUUSD.profit,
)
US100_POSITION = POSITION_XAUUSD.__class__(
    ticket=3, symbol="US100", type=POSITION_XAUUSD.type,
    volume=POSITION_XAUUSD.volume, open_price=POSITION_XAUUSD.open_price,
    current_price=POSITION_XAUUSD.current_price, profit=POSITION_XAUUSD.profit,
)
NICKEL_POSITION = POSITION_NICKEL

CLAIMS_TITLE = "US Initial Jobless Claims (placeholder)"

# The pinned event catalog (no publication dates: the scheduled-events view the
# real vendors expose — see _FixedCalendarProvider). Relevance is asserted
# against exactly these events, per scenario instrument.


def _event(event_id: str, hour: int, currency: str, title: str, impact: EventImpact,
           forecast: str | None, previous: str | None, actual: str | None = None) -> EconomicEvent:
    return EconomicEvent(
        event_id=event_id,
        timestamp=datetime(2026, 9, 16, hour, 0, tzinfo=UTC),
        currency=currency,
        title=title,
        impact=impact,
        forecast=forecast,
        previous=previous,
        actual=actual,
    )


FULL_CATALOG = (
    _event("evt-crude", 2, "USD", CRUDE_TITLE, EventImpact.LOW, "-1.2M", "-0.8M"),
    _event("evt-ip", 8, "EUR", IP_TITLE, EventImpact.MEDIUM, "0.3%", "-0.2%", "0.1%"),
    _event("evt-gdp", 10, "GBP", GDP_TITLE, EventImpact.MEDIUM, "0.2%", "0.1%", "0.2%"),
    _event("evt-cpi", 12, "USD", CPI_TITLE, EventImpact.HIGH, "3.1%", "3.2%"),
    _event("evt-claims", 13, "USD", CLAIMS_TITLE, EventImpact.MEDIUM, "220K", "215K", "218K"),
    _event("evt-boj", 18, "JPY", BOJ_TITLE, EventImpact.HIGH, "0.50%", "0.50%"),
)


def _plain_agent(llm: RecordingLLMProvider, *, positions, events, fundamental) -> AgentService:
    """The real agent boundary with an explicitly supplied fundamental service."""
    return AgentService(
        financial_context_service=FinancialContextService(
            account_service=AccountInfoService(_BenchmarkAccountInfoProvider(ACCOUNT)),
            position_service=PositionService(FakePositionProvider(positions=positions)),
            trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=())),
        ),
        llm_provider=llm,
        economic_intelligence_service=build_economic_service(*positions, events=events),
        fundamental_intelligence_service=fundamental,
    )


# --- A. XAUUSD fundamental analysis -------------------------------------------------------


def test_a_xauusd_fundamental_analysis_supplies_grounded_relevant_facts() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(
        llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG,
        research=build_research_service(),
    )
    scenario = Scenario(
        name="A. XAUUSD fundamental analysis",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="calendar facts",
                must_contain=(
                    CPI_TITLE, CLAIMS_TITLE, CRUDE_TITLE,
                    "impact HIGH", "forecast 3.1%", "previous 3.2%", "actual 218K",
                ),
            ),
            ScenarioCheck(
                name="news facts",
                must_contain=(
                    CPI_NEWS_TITLE, ETF_NEWS_TITLE, FED_NEWS_TITLE,
                    "relevance RELEVANT", "(direct factor for XAUUSD: precious metals)",
                ),
            ),
            ScenarioCheck(
                name="provenance and window",
                must_contain=(PROVENANCE, WINDOW, FACTS_LABEL, SAFETY_FRAME),
            ),
            ScenarioCheck(
                name="look-back research block accompanies a focused question",
                must_contain=("Financial research (published source facts, not analysis",),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- B. XAUUSD relevance boundary ----------------------------------------------------------


def test_b_xauusd_boundary_withholds_unrelated_events_and_states_the_omission() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="B. XAUUSD relevance boundary",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="irrelevant calendar absent",
                must_not_contain=(BOJ_TITLE, IP_TITLE, GDP_TITLE),
            ),
            ScenarioCheck(
                name="irrelevant news absent",
                must_not_contain=(ECB_NEWS_TITLE, SHIP_NEWS_TITLE),
            ),
            ScenarioCheck(
                name="omission stated",
                must_contain=("3 economic event(s) omitted", "2 news item(s) omitted"),
            ),
            ScenarioCheck(
                name="retained evidence survives",
                must_contain=(CPI_TITLE, CLAIMS_TITLE, CRUDE_TITLE,
                              CPI_NEWS_TITLE, ETF_NEWS_TITLE, FED_NEWS_TITLE),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures
    # The withheld set is the relevance layer's own verdict, verified again here
    # (catalog order: crude, euro IP, UK GDP, CPI, claims, BoJ).
    economic = agent._economic
    assert economic is not None
    context = economic.build_today_context(now=BENCHMARK_AS_OF)
    assert withheld_titles_for(context) == (IP_TITLE, GDP_TITLE, BOJ_TITLE)
    # Every event the layer DID relate to XAUUSD is exactly what the prompt carries.
    related = tuple(
        item.event.title for item in context.events
        if item.overall_relevance is not RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    )
    assert related == (CRUDE_TITLE, CPI_TITLE, CLAIMS_TITLE)


# --- C. USOIL -----------------------------------------------------------------------------


def test_c_usoil_supplies_energy_factors_and_excludes_gold_specific_items() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(USOIL_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="C. USOIL",
        question=QUESTION_USOIL,
        events=FULL_CATALOG,
        positions=(USOIL_POSITION,),
        checks=(
            ScenarioCheck(
                name="crude factor present, gold-ETF item absent",
                # The CPI and Fed-minutes items are US-macro factors the crude
                # profile itself documents (inflation, monetary policy), so they
                # are legitimately retained; the gold-ETF item is not.
                must_contain=(CRUDE_TITLE, CLAIMS_TITLE, CPI_TITLE,
                              "(macro factor for USOIL: inflation and price data)"),
                must_not_contain=(ETF_NEWS_TITLE,),
            ),
            ScenarioCheck(
                name="provenance and window",
                must_contain=(PROVENANCE, WINDOW, FACTS_LABEL),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- D. US100 -----------------------------------------------------------------------------


def test_d_us100_supplies_policy_factors_and_excludes_gold_specific_items() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(US100_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="D. US100",
        question=QUESTION_NASDAQ,
        events=FULL_CATALOG,
        positions=(US100_POSITION,),
        checks=(
            ScenarioCheck(
                name="macro/policy factors present, gold-specific news absent",
                must_contain=(CPI_TITLE, CLAIMS_TITLE, FED_NEWS_TITLE,
                              "relevance POTENTIALLY_RELEVANT"),
                must_not_contain=(ETF_NEWS_TITLE, SHIP_NEWS_TITLE, ECB_NEWS_TITLE),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- E. Position context --------------------------------------------------------------------


def test_e_position_context_is_reported_factually_with_no_advice_framing() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="E. Position context",
        question=QUESTION_POSITIONS,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="position facts",
                must_contain=("XAUUSD BUY 0.10", EXPOSURE_HEADER, "KNOWN",
                              "Relevant fundamental factors"),
            ),
            ScenarioCheck(
                name="no advice framing anywhere in the prompt",
                must_contain=("not advice", SAFETY_FRAME),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- F. Empty / unavailable / UNKNOWN states -------------------------------------------------


def _fundamental_service(news_service: NewsService | None) -> FundamentalIntelligenceService:
    return FundamentalIntelligenceService(news_service=news_service)


def test_f1_unavailable_news_is_distinct_from_empty_and_from_all_unrelated() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = _plain_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG,
                         fundamental=_fundamental_service(None))
    scenario = Scenario(
        name="F1. news unavailable",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="unavailable, not empty and not unrelated",
                must_contain=("news unavailable", NEWS_UNAVAILABLE),
                must_not_contain=(NEWS_EMPTY, CAL_NEWS_TITLE),
            ),
            ScenarioCheck(
                name="calendar evidence unaffected",
                must_contain=(CPI_TITLE,),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


def test_f2_empty_feed_is_distinct_from_unavailable_and_from_all_unrelated() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = _plain_agent(
        llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG,
        fundamental=_fundamental_service(NewsService(FakeNewsProvider(items=()), max_items=20)),
    )
    scenario = Scenario(
        name="F2. empty feed",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="empty, not unavailable and not all-unrelated",
                must_contain=(NEWS_EMPTY,),
                must_not_contain=("news unavailable", CAL_NEWS_TITLE),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


def test_f3_all_unrelated_feed_is_distinct_from_empty_and_unavailable() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    unrelated = FakeNewsProvider(items=(
        FakeNewsProvider().get_news  # placeholder never used; replaced below
        if False else _unrelated_item(),
    ))
    agent = _plain_agent(
        llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG,
        fundamental=_fundamental_service(NewsService(unrelated, max_items=20)),
    )
    scenario = Scenario(
        name="F3. all items unrelated",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="all-unrelated, not empty and not unavailable",
                must_contain=(CAL_NEWS_TITLE,),
                must_not_contain=(NEWS_EMPTY, "news unavailable"),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


def _unrelated_item():
    from app.providers.news import NewsItem
    return NewsItem(
        item_id="bench-unrelated",
        published_at=datetime(2026, 9, 16, 14, 30, tzinfo=UTC),
        publisher="Example Commodities Desk (placeholder)",
        title="Market wrap: energy and shipping costs in focus (placeholder)",
        summary="Placeholder excerpt: a placeholder wrap-up.",
        url=None,
        instruments=(),
        currencies=(),
        categories=(),
    )


def test_f4_unknown_exposure_renders_as_unknown_never_as_no_risk() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    # No events and no news source: today's drivers could not be assessed.
    agent = _plain_agent(llm, positions=(XAUUSD_POSITION,), events=(),
                         fundamental=_fundamental_service(None))
    scenario = Scenario(
        name="F4. UNKNOWN exposure",
        question=QUESTION_XAUUSD,
        events=(),
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="UNKNOWN stated with its reason",
                must_contain=(UNKNOWN_EXPOSURE, "could not be assessed"),
                must_not_contain=("no risk",),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- G. Unprofiled instrument -----------------------------------------------------------------


def test_g_unprofiled_instrument_fails_closed_without_inventing_relevance() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(NICKEL_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="G. unprofiled instrument (NICKEL)",
        question=QUESTION_NICKEL,
        events=FULL_CATALOG,
        positions=(NICKEL_POSITION,),
        checks=(
            ScenarioCheck(
                name="every event withheld (no currency leg, no profile)",
                must_not_contain=(CPI_TITLE, CLAIMS_TITLE, CRUDE_TITLE,
                                  BOJ_TITLE, IP_TITLE, GDP_TITLE),
            ),
            ScenarioCheck(
                name="all events withheld with the none-state stated, never an invented link",
                must_contain=(
                    "no economic event published for today is relevant to the instruments in play",
                ),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures


# --- H. Fact vs interpretation boundary ---------------------------------------------------------


def test_h_prompt_labels_facts_and_keeps_the_request_untrusted() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="H. fact/interpretation boundary",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="facts labelled, request untrusted",
                must_contain=(
                    FACTS_LABEL,
                    "relevance is a discrete relatedness level",
                    "USER_REQUEST",
                    "untrusted user input",
                ),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures
    # The user request travels verbatim inside the untrusted block.
    assert QUESTION_XAUUSD in (result.prompt or "")


# --- I. Safety ---------------------------------------------------------------------------------


def test_i_safety_framing_is_present_and_no_instruction_vocabulary_leaks_in() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="I. safety",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(
            ScenarioCheck(
                name="instruction framing",
                must_contain=(
                    SAFETY_FRAME,
                    "do not give investment advice",
                    "never suggest opening, closing or modifying a trade",
                ),
            ),
        ),
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures
    # The body outside the user's own quoted request must carry no instruction
    # vocabulary (the question itself legitimately names the prohibitions).
    body = (result.prompt or "").split("USER_REQUEST>>>", 1)[-1].lower()
    for phrase in ("you should buy", "you should sell", "price target", "open a long position"):
        assert phrase not in body


# --- End-to-end: the recorded answer grades against the same rules ---------------------------


def test_end_to_end_scenario_passes_and_the_recorded_answer_is_clean() -> None:
    llm = RecordingLLMProvider(FAKE_ANSWER)
    agent = build_agent(llm, positions=(XAUUSD_POSITION,), events=FULL_CATALOG)
    scenario = Scenario(
        name="E2E. XAUUSD request and recorded answer",
        question=QUESTION_XAUUSD,
        events=FULL_CATALOG,
        positions=(XAUUSD_POSITION,),
        checks=(ScenarioCheck(name="grounded", must_contain=(CPI_TITLE,))),  # type: ignore[arg-type]
    )
    result = run_scenario(agent, scenario, reference=BENCHMARK_AS_OF)
    assert result.passed, result.failures
    grade = grade_response(
        result.response or "",
        supplied_numbers=SUPPLIED_NUMBERS,
        withheld_titles=(BOJ_TITLE, IP_TITLE, GDP_TITLE),
    )
    assert grade.passed, grade.failures


def test_response_grader_rejects_invented_values_withheld_items_and_advice() -> None:
    # The grader is provider-independent: feed it a realistic-looking answer and
    # it must fail exactly on its three failure classes, and pass a grounded one.
    grounded = grade_response(
        "The US CPI YoY forecast is 3.1% versus 3.2% previous. Jobless claims 220K.",
        supplied_numbers=SUPPLIED_NUMBERS,
        withheld_titles=(BOJ_TITLE,),
    )
    assert grounded.passed, grounded.failures

    invented = grade_response(
        "Gold may move 2.5% after the release; the probability of a rise is 70%.",
        supplied_numbers=SUPPLIED_NUMBERS,
        withheld_titles=(),
    )
    assert not invented.passed
    assert any("not supplied" in failure for failure in invented.failures)
    assert any("prohibited pattern" in failure for failure in invented.failures)

    promoted = grade_response(
        "The Japan BoJ Interest Rate Decision (placeholder) is a key driver today.",
        supplied_numbers=SUPPLIED_NUMBERS,
        withheld_titles=(BOJ_TITLE,),
    )
    assert not promoted.passed
    assert any("withheld item" in failure for failure in promoted.failures)

    advice = grade_response(
        "You should buy gold now; open a long position.",
        supplied_numbers=SUPPLIED_NUMBERS,
        withheld_titles=(),
    )
    assert not advice.passed
    assert any("prohibited pattern" in failure for failure in advice.failures)
