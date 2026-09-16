"""Tests for the fundamental-intelligence context builder (READ-ONLY, no LLM).

Covers the deterministic composition the Agent and GET
/fundamental-intelligence/today both depend on: the mandatory calendar context is
carried through unchanged (including its provenance), news is combined with it
over the SAME window, relevance is classified per instrument in play, each open
position gets a factual exposure (with UNKNOWN whenever it cannot be established),
and nothing anywhere in the produced context is a forecast, a probability or a
recommendation.

Require none of: real MT5, PostgreSQL, network, credentials, an external LLM or a
news vendor. The calendar context is either built by the real
EconomicIntelligenceService over the deterministic placeholder calendar and fake
position provider, or a canned context; news comes from the deterministic
placeholder feed or an explicit in-memory stub.
"""
from datetime import UTC, datetime
from decimal import Decimal
from inspect import signature

import pytest

from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.news import NewsItem, NewsProvider
from app.providers.position import Position, PositionType
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    EconomicIntelligenceService,
    EventIntelligence,
    PositionRelevance,
    RelevanceLevel,
)
from app.services.fundamental_intelligence import (
    ExposureStatus,
    FundamentalContext,
    FundamentalIntelligenceService,
    detect_focus_symbol,
    detect_focus_symbols,
)
from app.services.news import NewsService
from app.services.positions import PositionService

AS_OF = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

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

US30 = Position(
    ticket=555555555,
    symbol="US30",
    type=PositionType.BUY,
    volume=Decimal("0.50"),
    open_price=Decimal("45000.00"),
    current_price=Decimal("45120.00"),
    profit=Decimal("60.00"),
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

EUR_EVENT = EconomicEvent(
    event_id="evt-eur-ip",
    timestamp=datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
    currency="EUR",
    title="Euro Area Industrial Production MoM",
    impact=EventImpact.MEDIUM,
    forecast="0.3%",
    previous="-0.2%",
    actual="0.1%",
)

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
)


def news_item(
    *,
    item_id: str = "news-usd-cpi",
    title: str = "US CPI release due later today (placeholder)",
    instruments: tuple[str, ...] = (),
    currencies: tuple[str, ...] = ("USD",),
) -> NewsItem:
    return NewsItem(
        item_id=item_id,
        published_at=datetime(2026, 9, 16, 7, 5, tzinfo=UTC),
        publisher="Example Newswire (placeholder)",
        title=title,
        summary="Placeholder excerpt.",
        url=None,
        instruments=tuple(instruments),
        currencies=currencies,
        categories=(),
    )


class _StubNewsProvider(NewsProvider):
    """Provider returning fixed items (no network, no clock)."""

    source = "test-news-source"

    def __init__(self, items: tuple[NewsItem, ...] = ()) -> None:
        self.items = items

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        selected = tuple(item for item in self.items if from_time <= item.published_at < to_time)
        return selected if limit is None else selected[:limit]


class _RecordingNewsService(NewsService):
    """Real NewsService over a stub provider, recording the requested window."""

    def __init__(self, items: tuple[NewsItem, ...] = ()) -> None:
        super().__init__(_StubNewsProvider(items), max_items=20)
        self.calls: list[tuple[datetime, datetime]] = []

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        instruments: tuple[str, ...] = (),
    ) -> tuple[NewsItem, ...]:
        self.calls.append((from_time, to_time))
        return super().get_news(from_time, to_time, instruments)


def position_relevance(
    position: Position, relevance: RelevanceLevel = RelevanceLevel.POTENTIALLY_RELEVANT
) -> PositionRelevance:
    """The calendar layer's per-position relevance record for one position."""
    return PositionRelevance(
        ticket=position.ticket,
        symbol=position.symbol,
        type=position.type,
        relevance=relevance,
        reason="canned relevance",
    )


def calendar_context(
    *,
    positions: tuple[Position, ...] = (XAUUSD,),
    events: tuple[EconomicEvent, ...] = (CPI_EVENT,),
    data_source: str = "test-calendar-source",
    event_relevance: RelevanceLevel = RelevanceLevel.POTENTIALLY_RELEVANT,
) -> EconomicIntelligenceContext:
    """A canned calendar context (no MT5, no calendar provider call).

    Each event carries the per-position relevance records the calendar layer
    would have produced, so the fundamental layer links drivers back to the
    caller's own positions exactly as it does in production.
    """
    ordered = tuple(sorted(positions, key=lambda position: (position.symbol, position.ticket)))
    intelligence = tuple(
        EventIntelligence(
            event=event,
            overall_relevance=event_relevance,
            positions=tuple(position_relevance(position, event_relevance) for position in ordered),
        )
        for event in events
    )
    return EconomicIntelligenceContext(
        as_of=AS_OF,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source=data_source,
        position_symbols=tuple(sorted({position.symbol for position in ordered})),
        events=intelligence,
        positions=ordered,
    )


def real_calendar_context(*, positions: tuple[Position, ...] = (XAUUSD,)) -> EconomicIntelligenceContext:
    """The real economic-intelligence context over deterministic offline sources."""
    service = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider(positions=positions)),
    )
    return service.build_today_context(now=AS_OF)


def build(
    *,
    calendar: EconomicIntelligenceContext | None = None,
    news: NewsService | None = None,
    focus_symbol: str | None = None,
) -> FundamentalContext:
    service = FundamentalIntelligenceService(news_service=news)
    return service.build_context(calendar if calendar is not None else calendar_context(), focus_symbol)


# --- the service holds nothing but news --------------------------------------------------


def test_the_fundamental_service_holds_no_mt5_or_calendar_provider() -> None:
    # Positions and calendar data always arrive with the caller's mandatory
    # calendar context, so one request performs no extra MT5 read.
    parameters = tuple(signature(FundamentalIntelligenceService.__init__).parameters)

    assert parameters == ("self", "news_service")


def test_the_news_source_provenance_is_exposed() -> None:
    service = FundamentalIntelligenceService(news_service=_RecordingNewsService())

    assert service.news_source == "test-news-source"


def test_no_news_source_is_exposed_as_none() -> None:
    assert FundamentalIntelligenceService(news_service=None).news_source is None


# --- the mandatory calendar context is carried through unchanged --------------------------


def test_the_calendar_context_is_preserved_in_the_fundamental_context() -> None:
    calendar = calendar_context(events=(CPI_EVENT, EUR_EVENT), data_source="quantgist-free-development")

    context = build(calendar=calendar, news=_RecordingNewsService())

    assert context.calendar is calendar
    assert context.calendar.data_source == "quantgist-free-development"
    assert [item.event.event_id for item in context.calendar.events] == [
        "evt-usd-cpi",
        "evt-eur-ip",
    ]


def test_the_reference_time_and_window_come_from_the_calendar_context() -> None:
    calendar = calendar_context()

    context = build(calendar=calendar, news=_RecordingNewsService())

    assert context.as_of == calendar.as_of
    assert context.window_from == calendar.window_from
    assert context.window_to == calendar.window_to


def test_news_is_requested_for_exactly_the_calendar_window() -> None:
    news = _RecordingNewsService((news_item(),))

    build(news=news)

    assert news.calls == [(WINDOW_FROM, WINDOW_TO)]


def test_the_real_calendar_context_flows_through_the_fundamental_layer() -> None:
    context = build(calendar=real_calendar_context(), news=_RecordingNewsService())

    # Deterministic placeholder events for today, with their provenance intact.
    assert context.calendar.data_source == "fake-development-placeholder"
    assert context.calendar.events
    assert context.instruments == ("XAUUSD",)


# --- instruments in play ------------------------------------------------------------------


def test_instruments_are_the_focus_symbol_plus_the_callers_holdings() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD, EURUSD)),
        news=_RecordingNewsService(),
        focus_symbol="xauusd",
    )

    assert context.focus_symbol == "XAUUSD"
    assert context.instruments == ("EURUSD", "XAUUSD")


def test_without_a_focus_symbol_only_the_holdings_are_in_play() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD,)),
        news=_RecordingNewsService(),
    )

    assert context.focus_symbol is None
    assert context.instruments == ("XAUUSD",)


def test_a_blank_focus_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="focus_symbol"):
        build(news=_RecordingNewsService(), focus_symbol="   ")


# --- news combination and relevance --------------------------------------------------------


def test_news_items_are_returned_with_their_relevance_and_reason() -> None:
    context = build(
        news=_RecordingNewsService((news_item(),)),
        focus_symbol="XAUUSD",
    )

    assert len(context.news) == 1
    entry = context.news[0]
    assert entry.item.item_id == "news-usd-cpi"
    assert entry.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert entry.reason
    assert entry.matched_instruments == ("XAUUSD",)


def test_news_relevance_is_classified_against_every_instrument_in_play() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD, EURUSD)),
        news=_RecordingNewsService((news_item(currencies=("USD",)),)),
        focus_symbol="XAUUSD",
    )

    entry = context.news[0]
    assert entry.matched_instruments == ("EURUSD", "XAUUSD")
    assert entry.relevance is RelevanceLevel.POTENTIALLY_RELEVANT


def test_an_unreferenced_item_stays_not_obviously_relevant_for_every_instrument() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD, EURUSD)),
        news=_RecordingNewsService(
            (news_item(title="Market wrap: energy and shipping costs in focus", currencies=()),)
        ),
        focus_symbol="XAUUSD",
    )

    entry = context.news[0]
    assert entry.relevance is RelevanceLevel.NOT_OBVIOUSLY_RELEVANT
    assert entry.matched_instruments == ()


def test_a_source_that_publishes_nothing_is_not_the_unavailable_state() -> None:
    context = build(news=_RecordingNewsService(()))

    assert context.news_available is True
    assert context.news_data_source == "test-news-source"
    assert context.news_unavailable_reason is None
    assert context.news == ()


def test_without_a_news_source_the_absence_is_stated_explicitly() -> None:
    context = build(news=None)

    # Deliberately not "no news": an unconfigured feed is missing information.
    assert context.news_available is False
    assert context.news_data_source is None
    assert context.news_unavailable_reason is not None
    assert "No news source is configured" in context.news_unavailable_reason
    assert context.news == ()


def test_the_placeholder_feed_is_combined_with_the_calendar_context() -> None:
    context = build(
        calendar=real_calendar_context(),
        news=NewsService(FakeNewsProvider(), max_items=20),
        focus_symbol="XAUUSD",
    )

    assert context.news_available is True
    assert context.news_data_source == "fake-development-placeholder"
    assert context.news, "the deterministic feed publishes items for today"
    # The calendar block is untouched beside it.
    assert context.calendar.data_source == "fake-development-placeholder"


# --- position fundamental exposure ---------------------------------------------------------


def test_a_position_with_calendar_and_news_drivers_is_known_exposure() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD,), events=(CPI_EVENT,)),
        news=_RecordingNewsService((news_item(),)),
        focus_symbol="XAUUSD",
    )

    assert len(context.positions) == 1
    exposure = context.positions[0]
    assert exposure.symbol == "XAUUSD"
    assert exposure.status is ExposureStatus.KNOWN
    assert exposure.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "XAUUSD" in exposure.reason


def test_calendar_drivers_of_a_position_are_the_positions_own_relevance_hits() -> None:
    calendar = EconomicIntelligenceContext(
        as_of=AS_OF,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source="test-calendar-source",
        position_symbols=("XAUUSD",),
        events=(
            EventIntelligence(
                event=CPI_EVENT,
                overall_relevance=RelevanceLevel.POTENTIALLY_RELEVANT,
                positions=(
                    # The position-relevance records the calendar layer produced
                    # for this ticket are what the fundamental layer links back.
                    position_relevance(XAUUSD, RelevanceLevel.POTENTIALLY_RELEVANT),
                ),
            ),
            EventIntelligence(
                event=EUR_EVENT,
                overall_relevance=RelevanceLevel.NOT_OBVIOUSLY_RELEVANT,
                positions=(position_relevance(XAUUSD, RelevanceLevel.NOT_OBVIOUSLY_RELEVANT),),
            ),
        ),
        positions=(XAUUSD,),
    )

    context = build(calendar=calendar, news=_RecordingNewsService())

    exposure = context.positions[0]
    assert exposure.calendar_event_ids == ("evt-usd-cpi",)
    assert exposure.relevance is RelevanceLevel.POTENTIALLY_RELEVANT


def test_news_drivers_of_a_position_are_the_items_matched_to_its_symbol() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD, EURUSD), events=()),
        news=_RecordingNewsService((news_item(currencies=("USD",)),)),
        focus_symbol="XAUUSD",
    )

    by_symbol = {exposure.symbol: exposure for exposure in context.positions}
    assert by_symbol["XAUUSD"].news_item_ids == ("news-usd-cpi",)
    assert by_symbol["EURUSD"].news_item_ids == ("news-usd-cpi",)


def test_a_position_without_identifiable_drivers_and_without_news_is_unknown() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD,), events=()),
        news=None,
    )

    exposure = context.positions[0]
    # Missing information is never presented as an absence of risk.
    assert exposure.status is ExposureStatus.UNKNOWN
    assert "could not be assessed" in exposure.reason
    assert exposure.calendar_event_ids == ()
    assert exposure.news_item_ids == ()


def test_a_symbol_without_a_currency_leg_is_unknown_exposure() -> None:
    context = build(
        calendar=calendar_context(positions=(US30,), events=(CPI_EVENT,)),
        news=_RecordingNewsService((news_item(),)),
        focus_symbol="US30",
    )

    exposure = context.positions[0]
    assert exposure.symbol == "US30"
    assert exposure.status is ExposureStatus.UNKNOWN
    assert "No currency leg could be identified" in exposure.reason


def test_known_exposure_states_that_news_could_not_be_assessed() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD,), events=(CPI_EVENT,)),
        news=None,
    )

    exposure = context.positions[0]
    assert exposure.status is ExposureStatus.KNOWN
    assert "No news source is configured" in exposure.reason


def test_no_positions_produces_no_exposures() -> None:
    context = build(
        calendar=calendar_context(positions=(), events=(CPI_EVENT,)),
        news=_RecordingNewsService(),
    )

    assert context.positions == ()
    assert context.instruments == ()


def test_exposure_carries_the_position_identity_and_volume() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD,), events=(CPI_EVENT,)),
        news=_RecordingNewsService(),
    )

    exposure = context.positions[0]
    assert exposure.ticket == XAUUSD.ticket
    assert exposure.type is PositionType.BUY
    assert exposure.volume == Decimal("0.10")


# --- fact only: no advice, no prediction ---------------------------------------------------


def test_no_reason_in_the_context_is_directional_or_predictive() -> None:
    context = build(
        calendar=calendar_context(positions=(XAUUSD, EURUSD, US30), events=(CPI_EVENT, EUR_EVENT)),
        news=_RecordingNewsService(
            (
                news_item(currencies=("USD",)),
                news_item(item_id="news-eur", title="ECB officials speak (placeholder)", currencies=("EUR",)),
                news_item(item_id="news-none", title="Market wrap: energy in focus", currencies=()),
            )
        ),
        focus_symbol="XAUUSD",
    )

    reasons = [entry.reason for entry in context.news] + [
        exposure.reason for exposure in context.positions
    ]
    assert reasons
    for reason in reasons:
        lowered = reason.lower()
        for forbidden in _FORBIDDEN_WORDS:
            assert forbidden not in lowered, (forbidden, reason)


def test_the_context_offers_no_forecast_or_target_fields() -> None:
    context = build(news=_RecordingNewsService((news_item(),)), focus_symbol="XAUUSD")

    # The whole vocabulary is factual: no direction, no probability, no price.
    assert set(FundamentalContext._fields) == {
        "as_of",
        "window_from",
        "window_to",
        "focus_symbol",
        "instruments",
        "calendar",
        "news_available",
        "news_data_source",
        "news_unavailable_reason",
        "news",
        "positions",
    }


def test_the_context_is_deterministic() -> None:
    def once() -> FundamentalContext:
        return build(
            calendar=calendar_context(events=(CPI_EVENT,)),
            news=_RecordingNewsService((news_item(),)),
            focus_symbol="XAUUSD",
        )

    assert once() == once()


# --- deterministic focus detection ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is happening with XAUUSD today?", "XAUUSD"),
        ("what fundamental factors matter for xauusd today?", "XAUUSD"),
        ("How is EURUSD trading?", "EURUSD"),
        ("Is GBPJPY affected?", "GBPJPY"),
        ("What is threatening my USDCAD position?", "USDCAD"),
        ("How is XAUUSD.r doing?", "XAUUSD"),
        # A bare currency is not an instrument, so it is never added to the
        # instruments in play.
        ("What is my USD exposure?", None),
        ("any balance questions", None),
        # A commodity name is not an MT5 symbol: mapping it would be a guess.
        ("What is happening with gold today?", None),
        # A suffix variant that is not a clean token concatenation is refused.
        ("How is XAUUSD1 doing?", None),
        ("How is XAUUSDS doing?", None),
        ("", None),
    ],
)
def test_focus_symbol_detection(text: str, expected: str | None) -> None:
    assert detect_focus_symbol(text) == expected


def test_focus_detection_is_case_and_typo_tolerant_within_its_rule() -> None:
    assert detect_focus_symbol("xauusd") == "XAUUSD"
    assert detect_focus_symbol("XaUuSd") == "XAUUSD"


def test_focus_detection_returns_symbols_in_first_appearance_order() -> None:
    assert detect_focus_symbols("EURUSD and XAUUSD today", limit=2) == ("EURUSD", "XAUUSD")


def test_focus_detection_bounds_its_result() -> None:
    assert detect_focus_symbols("EURUSD and XAUUSD today") == ("EURUSD",)
    with pytest.raises(ValueError, match="limit"):
        detect_focus_symbols("EURUSD", limit=0)


def test_focus_detection_is_deterministic() -> None:
    text = "What is happening with XAUUSD and EURUSD today?"

    assert detect_focus_symbols(text, limit=5) == detect_focus_symbols(text, limit=5)
