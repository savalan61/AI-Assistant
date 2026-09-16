"""Deterministic financial-research context (READ-ONLY, no LLM, no tenant data).

A reusable slice of the fundamental-intelligence layer: graded published-source
news for an EXPLICIT window and EXPLICIT focus instruments, without any account
or position data. It answers "what published facts matter for these instruments
in this window" — the research question — while
``FundamentalIntelligenceService.build_context`` continues to answer the
portfolio question ("what matters for what I hold today") around the mandatory
calendar context.

Deliberate properties:

* **One relevance mechanism.** Classification is the same
  ``news_intelligence`` grading the fundamental context uses (Step 48
  instrument profiles over the shared domain vocabulary) — there is no second
  classifier here, so the two surfaces can never disagree about an item.
* **Explicit window, no clock.** ``build_research`` takes the half-open UTC
  window ``[from_time, to_time)`` from its caller; nothing in this module reads
  the current time, so the result is reproducible and testable. A naive
  boundary or an inverted window is a caller error and fails loudly.
* **Focus instruments are explicit parameters.** The caller names the
  instruments (for example ``XAUUSD``); they are labels for relevance grading
  only. This service reads no positions, accepts no broker/user identity and
  performs no MT5 read, so its output carries no tenant-sensitive data by
  construction.
* **Provenance and boundedness travel.** Items come from the configured
  ``NewsService`` (window-filtered, validated, deterministically ordered and
  hard-capped), and the context reports the source's provenance marker — or the
  explicit ``news_available=False`` state when the deployment has no news
  source, which is never reported as "no news".
* **Facts only.** Timestamps, publisher, title, bounded excerpt, provenance,
  discrete relevance levels, transmission kind, matched domains and factual
  reasons. No forecast, probability, direction or recommendation, and no
  credentials anywhere — the surface has no credential to hold.
"""
from datetime import UTC, datetime
from typing import NamedTuple

from app.services.fundamental_intelligence.fundamental_intelligence_service import (
    FundamentalNewsItem,
    news_intelligence,
)
from app.services.news import NewsService


def _require_aware(value: datetime, field: str) -> datetime:
    """Return ``value`` normalized to UTC, rejecting naive datetimes.

    The same explicit-UTC boundary convention the calendar and news services
    use: a naive datetime is a caller contract violation and fails loudly
    instead of being silently read as local time.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_focus_symbols(
    focus_symbols: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """Upper-case/strip focus instruments; reject a blank one; dedupe and sort.

    Sorting keeps ``instruments`` independent of caller order (the same set of
    symbols always grades the same way), and a blank entry is a caller input
    error rather than an empty label that would silently grade nothing.
    """
    normalized: set[str] = set()
    for symbol in focus_symbols:
        candidate = symbol.strip().upper()
        if not candidate:
            raise ValueError("focus_symbols must not contain an empty instrument name")
        normalized.add(candidate)
    return tuple(sorted(normalized))


class FinancialResearchContext(NamedTuple):
    """Graded published-source news for one explicit window and instrument set.

    ``focus_symbols`` are the instruments the caller asked about (normalized);
    ``instruments`` is the set everything was graded against — the focus
    symbols themselves, because this surface holds no positions to add. When
    ``news_available`` is False the deployment has no news source configured
    and ``news_unavailable_reason`` says so; that is a different statement from
    an available source that published nothing in the window.
    """

    as_of: datetime
    window_from: datetime
    window_to: datetime
    focus_symbols: tuple[str, ...]
    instruments: tuple[str, ...]
    news_available: bool
    news_data_source: str | None
    news_unavailable_reason: str | None
    news: tuple[FundamentalNewsItem, ...]


# Stated reason when a deployment has no news source configured at all. The
# absence of a source is not evidence that nothing happened.
_RESEARCH_UNAVAILABLE_REASON = (
    "No news source is configured for this deployment, so published news could not be assessed."
)


class FinancialResearchService:
    """Builds the graded research context from the configured news source.

    ``news_service`` is optional: ``None`` means this deployment has no news
    source configured, which is reported explicitly rather than treated as an
    empty feed. The service holds no MT5 provider, reads no positions and
    accepts no tenant identity.
    """

    def __init__(self, news_service: NewsService | None):
        self._news = news_service

    @property
    def news_source(self) -> str | None:
        """Provenance marker of the configured news source, or None when absent."""
        return self._news.source if self._news is not None else None

    def build_research(
        self,
        from_time: datetime,
        to_time: datetime,
        focus_symbols: tuple[str, ...] | list[str] = (),
    ) -> FinancialResearchContext:
        """Graded news inside the half-open UTC window [from_time, to_time).

        ``focus_symbols`` names the instruments the research is about (for
        example ``("XAUUSD",)``); every item is graded against each of them
        through the shared instrument profiles, exactly as the fundamental
        context grades its items. With no focus symbol every item is still
        returned — graded against no instrument, which the classification
        states explicitly — so a caller that asks for the window's published
        facts gets them, with an honest "no instrument in play" reason rather
        than a discarded feed.

        ``as_of`` echoes the caller's (already validated) window start so the
        context is reproducible; no clock is read here.
        """
        window_from = _require_aware(from_time, "from_time")
        window_to = _require_aware(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")

        focus = _normalize_focus_symbols(focus_symbols)
        instruments = focus

        if self._news is None:
            news_available = False
            news_data_source: str | None = None
            unavailable_reason: str | None = _RESEARCH_UNAVAILABLE_REASON
            items: tuple = ()
        else:
            news_available = True
            news_data_source = self._news.source
            unavailable_reason = None
            # Bounded by NewsService: window re-filter, validated timestamps,
            # bounded excerpts, deterministic order and the hard item cap.
            items = self._news.get_news(window_from, window_to)

        # Graded with the SAME classification the fundamental context uses, so
        # the two surfaces can never disagree about an item.
        news = tuple(news_intelligence(item, instruments) for item in items)

        return FinancialResearchContext(
            as_of=window_from,
            window_from=window_from,
            window_to=window_to,
            focus_symbols=focus,
            instruments=instruments,
            news_available=news_available,
            news_data_source=news_data_source,
            news_unavailable_reason=unavailable_reason,
            news=news,
        )
