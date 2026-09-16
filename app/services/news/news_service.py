"""Business-level access to news (READ-ONLY).

Depends on the NewsProvider abstraction, never on a concrete source (mirrors
EconomicCalendarService / PositionService). All date handling is explicit UTC
with no hidden local-time assumptions, and the service adds the guarantees the
API and the agent rely on:

* the requested window must be timezone-aware and half-open ``[from, to)``, and
  an item outside it is dropped here even if a provider returned it, so a
  vendor's inclusive or ignored filtering can never leak an out-of-window item;
* a provider timestamp that is naive, or an excerpt longer than
  ``MAX_SUMMARY_CHARS``, is a provider contract violation and fails closed
  (RuntimeError) rather than being silently coerced or truncated — the API maps
  it to the established generic 503. Adapters are responsible for bounding an
  excerpt before it crosses this boundary;
* results are deterministically ordered (publication time, then item id) and
  hard-capped by the deployment's ``max_items``, so one request can never pull
  an unbounded amount of outbound context.

No caching, no retry, no scheduler: one bounded read per call.
"""
from datetime import UTC, datetime

from app.providers.news import NewsItem, NewsProvider

# A news excerpt is context, not an article body: the boundary accepts a bounded
# excerpt and rejects anything longer (fail closed) so an unbounded vendor
# payload can never reach the prompt.
MAX_SUMMARY_CHARS = 600


def _require_aware(value: datetime, field: str) -> datetime:
    """Return ``value`` normalized to UTC, rejecting naive datetimes."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class NewsService:
    def __init__(self, provider: NewsProvider, max_items: int):
        if max_items < 1:
            raise ValueError("max_items must be at least 1")
        self._provider = provider
        self._max_items = max_items

    @property
    def source(self) -> str:
        """Provenance marker of the underlying provider (never live data)."""
        return self._provider.source

    @property
    def max_items(self) -> int:
        """The deployment's hard cap on items per response."""
        return self._max_items

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        instruments: tuple[str, ...] = (),
    ) -> tuple[NewsItem, ...]:
        """News published inside the half-open window [from_time, to_time).

        Deterministically ordered and capped at ``max_items``; provider payloads
        are validated (aware timestamps, bounded excerpt) and re-filtered against
        the window before anything is returned.
        """
        window_from = _require_aware(from_time, "from_time")
        window_to = _require_aware(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")

        items = self._provider.get_news(
            window_from,
            window_to,
            instruments=instruments,
            limit=self._max_items,
        )
        for item in items:
            # Provider contract violations fail closed: a naive timestamp would
            # silently shift the item across the day boundary, and an unbounded
            # excerpt would blow up the outbound prompt.
            if item.published_at.tzinfo is None or item.published_at.tzinfo.utcoffset(item.published_at) is None:
                raise RuntimeError(f"news provider returned a naive timestamp (item {item.item_id})")
            if len(item.summary) > MAX_SUMMARY_CHARS:
                raise RuntimeError(f"news provider returned an oversized excerpt (item {item.item_id})")

        in_window = tuple(item for item in items if window_from <= item.published_at < window_to)
        # Chronological order is a business guarantee of this service; item_id
        # breaks ties so equal timestamps stay deterministic.
        ordered = tuple(sorted(in_window, key=lambda item: (item.published_at, item.item_id)))
        return ordered[: self._max_items]
