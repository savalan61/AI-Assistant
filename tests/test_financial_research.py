"""Tests for FinancialResearchService (Step 49 graded research context).

Fully offline and deterministic: news comes from stub/fake providers, every
timestamp is a fixed aware UTC instant, and no clock is read anywhere. The
suite pins the vertical slice's boundary guarantees:

* the explicit half-open UTC window is honoured (items outside it are dropped,
  boundaries are half-open);
* focus instruments are explicit parameters (upper-cased, sorted, deduplicated,
  a blank one is a caller error) and are labels for grading only;
* grading goes through the SAME classification the fundamental context uses
  (one relevance mechanism — an item graded here matches what
  FundamentalIntelligenceService would say about the same item);
* empty news is distinct from an unavailable news source, which is itself
  distinct from a failing news source (fail closed, no fallback to fake data);
* ordering is deterministic, output stays bounded by the service's cap, and
  provenance travels on every response;
* the surface carries no tenant/account/credential data and emits no direction.
"""
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.providers.news import NewsItem, NewsProvider
from app.services.fundamental_intelligence import (
    FinancialResearchService,
    FundamentalIntelligenceService,
)
from app.services.news import NewsService

WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)
# A second, different window proves the context uses the caller's window and
# never a clock.
LATER_FROM = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
LATER_TO = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)


def item(
    item_id: str,
    published_at: datetime,
    title: str,
    *,
    publisher: str = "Example Newswire",
    instruments: tuple[str, ...] = (),
    currencies: tuple[str, ...] = (),
) -> NewsItem:
    return NewsItem(
        item_id=item_id,
        published_at=published_at,
        publisher=publisher,
        title=title,
        summary=f"Bounded factual excerpt for {item_id}.",
        url=f"https://example.invalid/{item_id}",
        instruments=instruments,
        currencies=currencies,
        categories=("markets",),
    )


class StubNewsProvider(NewsProvider):
    """Deterministic provider returning a fixed item tuple (offline)."""

    source = "test-research-news"

    def __init__(self, items: tuple[NewsItem, ...], error: Exception | None = None):
        self._items = items
        self._error = error

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        if self._error is not None:
            raise self._error
        return self._items


def make_service(items: tuple[NewsItem, ...] = (), max_items: int = 20) -> FinancialResearchService:
    return FinancialResearchService(
        news_service=NewsService(StubNewsProvider(items), max_items=max_items)
    )


# --- window validation ------------------------------------------------------------------


def test_a_naive_window_boundary_is_rejected() -> None:
    service = make_service()

    with pytest.raises(ValueError, match="timezone-aware"):
        service.build_research(datetime(2026, 9, 16), WINDOW_TO, focus_symbols=("XAUUSD",))


def test_an_inverted_window_is_rejected() -> None:
    service = make_service()

    with pytest.raises(ValueError, match="earlier than"):
        service.build_research(WINDOW_TO, WINDOW_FROM, focus_symbols=("XAUUSD",))


def test_a_window_boundary_equal_to_the_other_is_rejected() -> None:
    service = make_service()

    with pytest.raises(ValueError, match="earlier than"):
        service.build_research(WINDOW_FROM, WINDOW_FROM, focus_symbols=("XAUUSD",))


def test_a_naive_boundary_inside_a_valid_pair_is_still_rejected() -> None:
    service = make_service()

    with pytest.raises(ValueError, match="timezone-aware"):
        service.build_research(WINDOW_FROM, datetime(2026, 9, 17), focus_symbols=("XAUUSD",))


# --- focus symbols ------------------------------------------------------------------------


def test_a_blank_focus_symbol_is_rejected() -> None:
    service = make_service()

    with pytest.raises(ValueError, match="empty instrument name"):
        service.build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("  ",))


def test_focus_symbols_are_normalized_deduplicated_and_sorted() -> None:
    context = make_service().build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("usoil", "XAUUSD", "USOIL ")
    )

    assert context.focus_symbols == ("USOIL", "XAUUSD")
    assert context.instruments == ("USOIL", "XAUUSD")


def test_focus_symbols_may_be_passed_as_a_list() -> None:
    context = make_service().build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=["XAUUSD"])

    assert context.focus_symbols == ("XAUUSD",)


# --- window filtering -------------------------------------------------------------------


def test_items_outside_the_window_are_dropped() -> None:
    inside = item("in-1", datetime(2026, 9, 16, 12, 0, tzinfo=UTC), "Federal Reserve holds rates")
    before = item("before", datetime(2026, 9, 15, 23, 59, tzinfo=UTC), "Gold demand rises")
    after = item("after", datetime(2026, 9, 17, 0, 1, tzinfo=UTC), "Gold demand rises")

    context = make_service((before, inside, after)).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",)
    )

    assert [entry.item.item_id for entry in context.news] == ["in-1"]


def test_the_window_is_half_open_at_both_boundaries() -> None:
    at_start = item("at-start", WINDOW_FROM, "Federal Reserve holds rates")
    at_end = item("at-end", WINDOW_TO, "Federal Reserve holds rates")

    context = make_service((at_start, at_end)).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",)
    )

    assert [entry.item.item_id for entry in context.news] == ["at-start"]


def test_the_context_uses_the_callers_window_not_a_clock() -> None:
    later = item("later", LATER_FROM, "Gold demand rises")

    context = make_service((later,)).build_research(
        LATER_FROM, LATER_TO, focus_symbols=("XAUUSD",)
    )

    assert context.window_from == LATER_FROM
    assert context.window_to == LATER_TO
    assert context.as_of == LATER_FROM
    assert [entry.item.item_id for entry in context.news] == ["later"]


def test_a_non_utc_window_is_normalized_to_utc() -> None:
    # 02:00 +02:00 == 00:00 UTC: same instant, so the item at 00:00 UTC is in.
    # A fixed offset keeps the test independent of the tzdata database.
    offset_from = datetime(2026, 9, 16, 2, 0, tzinfo=timezone(timedelta(hours=2)))
    context = make_service((item("z", WINDOW_FROM, "Fed minutes released"),)).build_research(
        offset_from, WINDOW_TO, focus_symbols=("XAUUSD",)
    )

    assert context.window_from == WINDOW_FROM
    assert [entry.item.item_id for entry in context.news] == ["z"]


# --- grading through the shared mechanism --------------------------------------------------


def test_a_direct_factor_item_is_relevant_with_kind_and_domains() -> None:
    context = make_service(
        (item("gold-1", WINDOW_FROM, "Gold demand rises as central banks increase purchases"),)
    ).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    entry = context.news[0]
    assert entry.relevance.value == "RELEVANT"
    assert entry.kind is not None and entry.kind.value == "DIRECT"
    assert "precious metals" in entry.reason
    assert entry.matched_instruments == ("XAUUSD",)


def test_a_macro_factor_item_is_potentially_relevant() -> None:
    context = make_service(
        (item("fed-1", WINDOW_FROM, "Federal Reserve officials discuss interest rates"),)
    ).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    entry = context.news[0]
    assert entry.relevance.value == "POTENTIALLY_RELEVANT"
    assert entry.kind is not None and entry.kind.value == "MACRO"


def test_an_unrelated_item_stays_not_obviously_relevant() -> None:
    context = make_service(
        (item("apple-1", WINDOW_FROM, "Apple launches a new consumer device"),)
    ).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    entry = context.news[0]
    assert entry.relevance.value == "NOT_OBVIOUSLY_RELEVANT"
    assert entry.matched_instruments == ()
    assert "No documented fundamental factor" in entry.reason


def test_grading_matches_the_fundamental_context_for_the_same_item() -> None:
    """One relevance mechanism: research and fundamental agree on the same item."""
    single = item("shared", WINDOW_FROM, "OPEC announces a production cut")
    research = make_service((single,)).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("USOIL",)
    )

    from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
    from app.services.economic_calendar import EconomicCalendarService
    from app.services.economic_intelligence import EconomicIntelligenceContext

    calendar = EconomicIntelligenceContext(
        as_of=WINDOW_FROM,
        window_from=WINDOW_FROM,
        window_to=WINDOW_TO,
        data_source="test-calendar",
        position_symbols=(),
        events=(),
        positions=(),
    )
    fundamental = FundamentalIntelligenceService(
        news_service=NewsService(StubNewsProvider((single,)), max_items=20)
    ).build_context(calendar, focus_symbol="USOIL")

    assert research.news[0].relevance == fundamental.news[0].relevance
    assert research.news[0].kind == fundamental.news[0].kind
    assert research.news[0].domains == fundamental.news[0].domains


def test_multi_instrument_items_are_graded_per_instrument() -> None:
    context = make_service(
        (item("gulf-1", WINDOW_FROM, "Persian Gulf tensions disrupt crude shipments"),)
    ).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("USOIL", "XAUUSD", "NAS100"))

    entry = context.news[0]
    assert entry.matched_instruments == ("NAS100", "USOIL", "XAUUSD")
    levels = {match.symbol: match.level for match in entry.matches}
    assert levels["USOIL"].value == "RELEVANT"
    assert levels["XAUUSD"].value == "POTENTIALLY_RELEVANT"
    assert levels["NAS100"].value == "POTENTIALLY_RELEVANT"


# --- empty / unavailable / failing source -------------------------------------------------


def test_empty_news_from_an_available_source_is_reported_as_empty() -> None:
    context = make_service(()).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    assert context.news_available is True
    assert context.news_data_source == "test-research-news"
    assert context.news_unavailable_reason is None
    assert context.news == ()


def test_no_news_source_is_reported_as_unavailable_not_empty() -> None:
    context = FinancialResearchService(news_service=None).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",)
    )

    assert context.news_available is False
    assert context.news_data_source is None
    assert context.news_unavailable_reason is not None
    assert "No news source is configured" in context.news_unavailable_reason
    assert context.news == ()


def test_a_failing_news_source_fails_closed_without_fallback() -> None:
    service = FinancialResearchService(
        news_service=NewsService(
            StubNewsProvider((), error=RuntimeError("news source unavailable")),
            max_items=20,
        )
    )

    with pytest.raises(RuntimeError, match="news source unavailable"):
        service.build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))


def test_news_source_property_reports_provenance_or_none() -> None:
    assert make_service().news_source == "test-research-news"
    assert FinancialResearchService(news_service=None).news_source is None


# --- determinism / bounds -------------------------------------------------------------------


def test_ordering_is_deterministic_publication_time_then_item_id() -> None:
    b = item("b-later-id", datetime(2026, 9, 16, 8, 0, tzinfo=UTC), "Fed holds rates")
    a = item("a-same-time", datetime(2026, 9, 16, 8, 0, tzinfo=UTC), "Fed holds rates")
    c = item("c-earlier", datetime(2026, 9, 16, 7, 0, tzinfo=UTC), "Fed holds rates")

    first = make_service((b, a, c)).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))
    second = make_service((c, b, a)).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    assert [e.item.item_id for e in first.news] == ["c-earlier", "a-same-time", "b-later-id"]
    assert first.news == second.news


def test_output_is_bounded_by_the_news_service_cap() -> None:
    items = tuple(
        item(f"n-{i}", datetime(2026, 9, 16, 7, i % 60, tzinfo=UTC), "Fed holds rates")
        for i in range(30)
    )
    service = FinancialResearchService(
        news_service=NewsService(StubNewsProvider(items), max_items=5)
    )

    context = service.build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    assert len(context.news) == 5
    assert service.news_source == "test-research-news"


# --- safety --------------------------------------------------------------------------------


def test_research_items_are_public_data_with_no_account_identity() -> None:
    # A position is deliberately NOT part of this surface: the context is graded
    # news only, so no exposure record, ticket, balance or equity can appear.
    context = make_service(
        (item("gold-1", WINDOW_FROM, "Gold demand rises"),)
    ).build_research(WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",))

    assert not hasattr(context, "positions")
    for entry in context.news:
        payload = repr(entry.item)
        assert "balance" not in payload.lower()
        assert "equity" not in payload.lower()
        assert "password" not in payload.lower()


def test_no_directional_vocabulary_is_produced_by_the_service() -> None:
    items = (
        item("gold-1", WINDOW_FROM, "Gold demand rises"),
        item("opec-1", WINDOW_FROM, "OPEC announces production cuts"),
        item("fed-1", WINDOW_FROM, "Federal Reserve signals rate path"),
    )
    context = make_service(items).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD", "USOIL")
    )

    rendered = "\n".join(entry.reason for entry in context.news).lower()
    for forbidden in ("buy", "sell", "bullish", "bearish", "should", "recommend"):
        assert forbidden not in rendered


def test_volume_and_decimal_fields_never_enter_the_research_context() -> None:
    # The contract has no numeric market field at all: nothing here can carry an
    # account figure or a fabricated price.
    context = make_service((item("gold-1", WINDOW_FROM, "Gold demand rises"),)).build_research(
        WINDOW_FROM, WINDOW_TO, focus_symbols=("XAUUSD",)
    )

    assert not any(
        isinstance(getattr(context, field), (int, float, Decimal))
        for field in context._fields
        if field not in ("news_available",)
    )
