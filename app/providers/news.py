"""Vendor-neutral news contract (READ-ONLY).

A news provider is a read-only external-data integration, exactly like the
economic-calendar provider: services depend on this abstraction, never on a
concrete source, so a real vendor can be added later without touching the
service, the API or the agent layer.

Deliberate properties of this boundary:

* every field is factual and bounded — an item carries a publisher name, a
  title and a bounded excerpt, never an analysis, a sentiment score, a price
  forecast or a trading suggestion;
* ``published_at`` MUST be timezone-aware (UTC). The service layer rejects a
  naive timestamp instead of guessing a local timezone;
* ``instruments``/``currencies``/``categories`` are *declared* tags. They are
  optional: when a source publishes none, the deterministic relevance layer
  infers currency references from the title with an explicit, testable keyword
  map (see app/services/fundamental_intelligence/relevance.py). Declared tags
  always win;
* retrieval is window-bounded and the caller passes a result cap, so a provider
  can never decide on its own how much of a request's budget it consumes;
* ``source`` is a provenance marker surfaced in every API response and prompt,
  so a development/test source can never be mistaken for live financial data.

No scraping, no scheduler and no caching live at this boundary: a provider is a
bounded read for one caller-supplied window.
"""
import abc
from datetime import datetime
from typing import NamedTuple


# Typed read-only news snapshot shared by all news providers. Explicit
# NamedTuple like EconomicEvent/Candle/Position so no vendor-specific object
# (HTTP payload, feed entry, vendor model) travels past the provider boundary.
#
# ``summary`` is a bounded excerpt (the service rejects an over-long one rather
# than silently inventing a truncation point), and ``url`` is optional because
# not every source exposes one.
class NewsItem(NamedTuple):
    item_id: str
    published_at: datetime
    publisher: str
    title: str
    summary: str
    url: str | None
    instruments: tuple[str, ...]
    currencies: tuple[str, ...]
    categories: tuple[str, ...]


# Abstraction boundary: services depend on this, never on a concrete source
# (mirrors EconomicCalendarProvider / PositionProvider).
class NewsProvider(abc.ABC):
    # Data provenance marker surfaced in API responses and in the agent prompt
    # so a development or test source can never be mistaken for live data.
    source: str

    @abc.abstractmethod
    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        """Return items published inside the half-open window [from_time, to_time).

        ``instruments`` is an optional declared-tag filter; an empty tuple means
        "no instrument filter" (the relevance layer decides relevance, so an
        untagged item is never dropped merely because it carries no tag).
        ``limit`` bounds the work a provider does; the service layer still
        enforces its own hard cap on what a response may contain.
        """
        ...
