"""Deterministic development/test news provider.

NOT LIVE DATA. This provider materializes a fixed catalog of placeholder news
items on the UTC dates covered by the requested window, so results are
reproducible for a given window and no "today" is hard-coded anywhere. Every
response built from it carries ``FakeNewsProvider.source`` as the provenance
marker, so development data can never be presented as live news. A real news
vendor is selected separately, behind the same contract.

The catalog deliberately mixes tag shapes so the deterministic relevance layer
is exercised honestly:

* items with declared instrument/currency tags (the shape a real vendor
  usually publishes);
* an item that declares no tags but names the Federal Reserve in its title, so
  the keyword map has to detect the USD reference;
* an item with no tags and no identifiable currency reference at all, which must
  stay NOT_OBVIOUSLY_RELEVANT for every instrument rather than being guessed at.

No network, no files, no clock: the provider is pure and its only input is the
requested window.
"""
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple

from app.providers.news import NewsItem, NewsProvider


class _NewsTemplate(NamedTuple):
    """Placeholder item definition; ``at`` is a UTC time of day."""

    suffix: str
    publisher: str
    title: str
    summary: str
    at: time
    url: str | None
    instruments: tuple[str, ...]
    currencies: tuple[str, ...]
    categories: tuple[str, ...]


# Fixed catalog. Titles and excerpts are obviously illustrative placeholders and
# stay strictly factual: they describe that a release or an event is scheduled
# or on record, and never an outcome, a direction or an expectation.
_NEWS_TEMPLATES: tuple[_NewsTemplate, ...] = (
    _NewsTemplate(
        suffix="usd-cpi-preview",
        publisher="Example Newswire (placeholder)",
        title="US CPI release due later today (placeholder)",
        summary=(
            "Placeholder excerpt: the US consumer price index release is scheduled for today. "
            "Figures are placeholder values, not live data."
        ),
        at=time(7, 5),
        url="https://example.invalid/placeholder/us-cpi",
        instruments=("XAUUSD",),
        currencies=("USD",),
        categories=("economic_data",),
    ),
    _NewsTemplate(
        suffix="gold-flows",
        publisher="Example Markets Desk (placeholder)",
        title="Gold ETF flows reported steady ahead of US data (placeholder)",
        summary=(
            "Placeholder excerpt: reported gold-backed fund flows are described as steady in a "
            "placeholder bulletin. No live data is represented here."
        ),
        at=time(8, 20),
        url=None,
        instruments=("XAUUSD",),
        currencies=("XAU",),
        categories=("commodities",),
    ),
    _NewsTemplate(
        suffix="fed-minutes-untagged",
        publisher="Example Wire Service (placeholder)",
        title="Federal Reserve minutes due later today (placeholder)",
        summary=(
            "Placeholder excerpt: a central-bank minutes release is on today's placeholder "
            "schedule. No live data is represented here."
        ),
        at=time(10, 45),
        url=None,
        # Deliberately untagged: the deterministic keyword map must detect the
        # USD reference from the title.
        instruments=(),
        currencies=(),
        categories=("central_bank",),
    ),
    _NewsTemplate(
        suffix="ecb-outlook",
        publisher="Example European Desk (placeholder)",
        title="ECB officials speak on the euro-area outlook (placeholder)",
        summary=(
            "Placeholder excerpt: euro-area policy comments are on today's placeholder schedule. "
            "No live data is represented here."
        ),
        at=time(12, 10),
        url=None,
        instruments=(),
        currencies=("EUR",),
        categories=("central_bank",),
    ),
    _NewsTemplate(
        suffix="energy-shipping-wrap",
        publisher="Example Commodities Desk (placeholder)",
        title="Market wrap: energy and shipping costs in focus (placeholder)",
        summary=(
            "Placeholder excerpt: a placeholder wrap-up of energy and shipping cost commentary. "
            "No live data is represented here."
        ),
        at=time(14, 30),
        url=None,
        # No tags and no currency reference anywhere: this item must never be
        # presented as relevant to an instrument.
        instruments=(),
        currencies=(),
        categories=("commodities",),
    ),
)


def _utc_dates_between(from_time: datetime, to_time: datetime) -> tuple[date, ...]:
    """UTC dates touched by the half-open window [from_time, to_time)."""
    start = from_time.astimezone(UTC).date()
    end = to_time.astimezone(UTC).date()
    if end < start:
        return ()
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return tuple(days)


class FakeNewsProvider(NewsProvider):
    """In-memory news provider with fully deterministic placeholder items.

    By default the fixed catalog is materialized on every UTC date the requested
    window touches (item_id is date-scoped, so ids stay unique per day). Tests
    may pass an explicit item tuple to pin exact data instead.
    """

    source = "fake-development-placeholder"

    def __init__(self, items: tuple[NewsItem, ...] | None = None):
        # Explicit items (when given) are returned by plain window filtering.
        self._items = items
        # Recorded requested windows so tests can assert delegation and
        # boundedness without touching the provider's internals.
        self.windows: list[tuple[datetime, datetime]] = []
        self.requested_instruments: list[tuple[str, ...]] = []
        self.requested_limits: list[int | None] = []

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        self.windows.append((from_time, to_time))
        self.requested_instruments.append(instruments)
        self.requested_limits.append(limit)

        materialized = self._materialize(from_time, to_time)
        wanted = {instrument.upper() for instrument in instruments}
        selected = tuple(
            item
            for item in materialized
            if from_time <= item.published_at < to_time
            and (not wanted or wanted.intersection(item.instruments))
        )
        if limit is not None and limit >= 0:
            return selected[:limit]
        return selected

    def _materialize(self, from_time: datetime, to_time: datetime) -> tuple[NewsItem, ...]:
        if self._items is not None:
            return self._items
        materialized: list[NewsItem] = []
        for day in _utc_dates_between(from_time, to_time):
            materialized.extend(_items_for_day(day))
        return tuple(materialized)


def _items_for_day(day: date) -> tuple[NewsItem, ...]:
    """The fixed catalog as materialized on one UTC date."""
    return tuple(
        NewsItem(
            item_id=f"fake-news-{day.isoformat()}-{template.suffix}",
            # combine() with tzinfo=UTC keeps every timestamp timezone-aware;
            # no local-time assumption is ever made.
            published_at=datetime.combine(day, template.at, tzinfo=UTC),
            publisher=template.publisher,
            title=template.title,
            summary=template.summary,
            url=template.url,
            instruments=template.instruments,
            currencies=template.currencies,
            categories=template.categories,
        )
        for template in _NEWS_TEMPLATES
    )
