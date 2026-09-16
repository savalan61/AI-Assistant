"""Tests for the news provider contract, the deterministic fake and NewsService.

Require none of: real MT5, PostgreSQL, network, credentials or a news vendor.
Everything here uses the deterministic placeholder provider or explicit
in-memory items, so no external news service is ever contacted.

The suite pins the boundary guarantees the fundamental layer relies on: a
half-open UTC window, bounded factual fields, explicit provenance, a hard result
cap, deterministic ordering, and fail-closed handling of a provider that returns
a naive timestamp or an unbounded excerpt.
"""
import inspect
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.providers.fake_news import FakeNewsProvider
from app.providers.news import NewsItem, NewsProvider
from app.services.news import MAX_SUMMARY_CHARS, NewsService

WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)


def item(
    *,
    item_id: str = "item-1",
    published_at: datetime = datetime(2026, 9, 16, 7, 5, tzinfo=UTC),
    publisher: str = "Example Newswire (placeholder)",
    title: str = "US CPI release due later today (placeholder)",
    summary: str = "Placeholder excerpt.",
    url: str | None = None,
    instruments: tuple[str, ...] = ("XAUUSD",),
    currencies: tuple[str, ...] = ("USD",),
    categories: tuple[str, ...] = ("economic_data",),
) -> NewsItem:
    return NewsItem(
        item_id=item_id,
        published_at=published_at,
        publisher=publisher,
        title=title,
        summary=summary,
        url=url,
        instruments=instruments,
        currencies=currencies,
        categories=categories,
    )


class _StubProvider(NewsProvider):
    """Provider returning fixed items, recording how it was called."""

    source = "test-provider"

    def __init__(self, items: tuple[NewsItem, ...]) -> None:
        self.items = items
        self.calls: list[tuple[datetime, datetime, tuple[str, ...], int | None]] = []

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        self.calls.append((from_time, to_time, instruments, limit))
        return self.items


# --- the contract -----------------------------------------------------------------------


def test_the_news_provider_contract_cannot_be_instantiated_directly() -> None:
    # Services depend on the abstraction, so it must not be a usable source on
    # its own (the EconomicCalendarProvider/PositionProvider pattern).
    with pytest.raises(TypeError):
        NewsProvider()  # type: ignore[abstract]


def test_the_news_contract_carries_only_bounded_factual_fields() -> None:
    # The typed snapshot is the whole vocabulary a provider may hand to a
    # service: no sentiment, no score, no forecast, no vendor object.
    assert set(NewsItem._fields) == {
        "item_id",
        "published_at",
        "publisher",
        "title",
        "summary",
        "url",
        "instruments",
        "currencies",
        "categories",
    }


def test_the_fake_provider_declares_explicit_development_provenance() -> None:
    assert FakeNewsProvider.source == "fake-development-placeholder"


def test_no_news_provider_module_uses_the_network() -> None:
    # Structural guard: a fake (or the contract) must never be able to reach a
    # network, so no HTTP/socket client may be imported at this boundary.
    for module in (NewsProvider, FakeNewsProvider):
        source = inspect.getsource(inspect.getmodule(module))  # type: ignore[arg-type]
        for forbidden in ("httpx", "requests", "urllib", "socket", "aiohttp"):
            assert forbidden not in source


# --- the deterministic fake -------------------------------------------------------------


def test_the_fake_materializes_deterministic_items_for_the_window() -> None:
    provider = FakeNewsProvider()

    first = provider.get_news(WINDOW_FROM, WINDOW_TO)
    second = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert first
    assert first == second  # fully deterministic: no clock, no randomness
    assert all(WINDOW_FROM <= entry.published_at < WINDOW_TO for entry in first)
    assert all(entry.item_id.startswith("fake-news-2026-09-16-") for entry in first)
    assert all(entry.published_at.tzinfo is not None for entry in first)


def test_the_fake_marks_its_items_as_placeholder_text() -> None:
    provider = FakeNewsProvider()

    items = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert all("placeholder" in entry.publisher.lower() for entry in items)
    assert all("placeholder" in entry.summary.lower() for entry in items)


def test_the_fake_ids_are_scoped_to_the_utc_date() -> None:
    provider = FakeNewsProvider()

    today = provider.get_news(WINDOW_FROM, WINDOW_TO)
    two_days = provider.get_news(WINDOW_FROM, WINDOW_TO + timedelta(days=1))

    assert len(two_days) == len(today) * 2
    assert {entry.item_id for entry in today} < {entry.item_id for entry in two_days}


def test_the_fake_window_is_half_open() -> None:
    provider = FakeNewsProvider()
    # 14:30 UTC is the last catalog item of the day: [00:00, 14:30) excludes it.
    cutoff = datetime(2026, 9, 16, 14, 30, tzinfo=UTC)

    items = provider.get_news(WINDOW_FROM, cutoff)

    assert items
    assert all(entry.published_at < cutoff for entry in items)


def test_the_fake_honours_a_declared_instrument_filter() -> None:
    provider = FakeNewsProvider()

    items = provider.get_news(WINDOW_FROM, WINDOW_TO, instruments=("EURUSD",))

    # Only items that declare the requested tag survive the provider filter; the
    # relevance layer (not the provider) decides what an untagged item means.
    assert items == ()


def test_the_fake_honours_the_result_cap() -> None:
    provider = FakeNewsProvider()

    items = provider.get_news(WINDOW_FROM, WINDOW_TO, limit=2)

    assert len(items) == 2


def test_the_fake_can_be_pinned_to_explicit_items() -> None:
    pinned = (item(item_id="pinned-1"),)
    provider = FakeNewsProvider(items=pinned)

    assert provider.get_news(WINDOW_FROM, WINDOW_TO) == pinned


def test_the_fake_records_requested_windows() -> None:
    provider = FakeNewsProvider()

    provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert provider.windows == [(WINDOW_FROM, WINDOW_TO)]


# --- the service: window and delegation -------------------------------------------------


def test_the_service_normalizes_the_window_to_utc() -> None:
    provider = _StubProvider(())
    service = NewsService(provider, max_items=5)
    # The same two instants, written with a +02:00 offset instead of UTC.
    plus_two = timezone(timedelta(hours=2))
    shifted_from = datetime(2026, 9, 16, 2, 0, tzinfo=plus_two)
    shifted_to = datetime(2026, 9, 16, 5, 0, tzinfo=plus_two)

    service.get_news(shifted_from, shifted_to)

    # The provider is always asked in UTC, so no local-time interpretation can
    # leak into a vendor's day window.
    called_from, called_to, _, _ = provider.calls[0]
    assert called_from == WINDOW_FROM
    assert called_to == datetime(2026, 9, 16, 3, 0, tzinfo=UTC)
    assert called_from.tzinfo == UTC
    assert called_to.tzinfo == UTC


def test_the_service_rejects_a_naive_window() -> None:
    service = NewsService(_StubProvider(()), max_items=5)

    with pytest.raises(ValueError):
        service.get_news(datetime(2026, 9, 16), WINDOW_TO)
    with pytest.raises(ValueError):
        service.get_news(WINDOW_FROM, datetime(2026, 9, 17))


def test_the_service_rejects_an_empty_or_inverted_window() -> None:
    service = NewsService(_StubProvider(()), max_items=5)

    with pytest.raises(ValueError):
        service.get_news(WINDOW_FROM, WINDOW_FROM)
    with pytest.raises(ValueError):
        service.get_news(WINDOW_TO, WINDOW_FROM)


def test_the_service_requires_a_positive_cap() -> None:
    with pytest.raises(ValueError):
        NewsService(_StubProvider(()), max_items=0)


def test_the_service_delegates_the_window_instruments_and_cap() -> None:
    provider = _StubProvider(())
    service = NewsService(provider, max_items=7)

    service.get_news(WINDOW_FROM, WINDOW_TO, instruments=("XAUUSD",))

    assert provider.calls == [(WINDOW_FROM, WINDOW_TO, ("XAUUSD",), 7)]


def test_the_service_exposes_its_source_and_cap() -> None:
    service = NewsService(FakeNewsProvider(), max_items=20)

    assert service.source == "fake-development-placeholder"
    assert service.max_items == 20


# --- the service: guarantees added for the callers ---------------------------------------


def test_the_service_rejects_a_naive_provider_timestamp() -> None:
    naive = item(published_at=datetime(2026, 9, 16, 7, 5))
    service = NewsService(_StubProvider((naive,)), max_items=5)

    # A naive timestamp would silently shift the item across the UTC day
    # boundary, so it fails closed instead of being guessed at.
    with pytest.raises(RuntimeError, match="naive timestamp"):
        service.get_news(WINDOW_FROM, WINDOW_TO)


def test_the_service_rejects_an_oversized_excerpt() -> None:
    oversized = item(summary="x" * (MAX_SUMMARY_CHARS + 1))
    service = NewsService(_StubProvider((oversized,)), max_items=5)

    with pytest.raises(RuntimeError, match="oversized excerpt"):
        service.get_news(WINDOW_FROM, WINDOW_TO)


def test_the_boundary_accepts_an_excerpt_exactly_at_the_limit() -> None:
    at_limit = item(summary="x" * MAX_SUMMARY_CHARS)
    service = NewsService(_StubProvider((at_limit,)), max_items=5)

    assert service.get_news(WINDOW_FROM, WINDOW_TO) == (at_limit,)


def test_the_service_drops_out_of_window_items_even_when_a_provider_returns_them() -> None:
    inside = item(item_id="inside")
    before = item(item_id="before", published_at=WINDOW_FROM - timedelta(minutes=1))
    at_end = item(item_id="at-end", published_at=WINDOW_TO)
    service = NewsService(_StubProvider((inside, before, at_end)), max_items=5)

    assert service.get_news(WINDOW_FROM, WINDOW_TO) == (inside,)


def test_the_service_orders_items_deterministically() -> None:
    later = item(item_id="b", published_at=datetime(2026, 9, 16, 9, 0, tzinfo=UTC))
    earlier = item(item_id="a", published_at=datetime(2026, 9, 16, 8, 0, tzinfo=UTC))
    tie = item(item_id="aa", published_at=datetime(2026, 9, 16, 8, 0, tzinfo=UTC))
    service = NewsService(_StubProvider((later, earlier, tie)), max_items=5)

    ordered = service.get_news(WINDOW_FROM, WINDOW_TO)

    assert [entry.item_id for entry in ordered] == ["a", "aa", "b"]


def test_the_service_applies_its_own_cap_regardless_of_the_provider() -> None:
    items = tuple(
        item(item_id=f"item-{index}", published_at=datetime(2026, 9, 16, index, 0, tzinfo=UTC))
        for index in range(1, 6)
    )
    service = NewsService(_StubProvider(items), max_items=2)

    assert len(service.get_news(WINDOW_FROM, WINDOW_TO)) == 2


def test_the_service_returns_nothing_when_the_source_publishes_nothing() -> None:
    # An empty feed is a normal result: the caller decides what "no items" means
    # (the fundamental context labels it as this source publishing nothing
    # today, never as "nothing happened").
    service = NewsService(_StubProvider(()), max_items=5)

    assert service.get_news(WINDOW_FROM, WINDOW_TO) == ()
