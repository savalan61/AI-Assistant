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
* **Focus instruments are explicit parameters, resolved against the broker.**
  The caller names the instruments (for example ``XAUUSD``); when an
  ``InstrumentService`` is wired (Step 51), every name is resolved through the
  tenant's own MT5 catalog first and grading uses the broker's canonical
  spelling — a requested spelling the broker does not offer is reported as
  unresolved and never used as if it were a real instrument. This service holds
  no MT5 provider, no credentials, no positions and no broker/user identity: it
  asks the instrument layer for a symbol, which is the same read-only boundary
  every other resolver uses.
* **Generic by construction.** A profile is never required to research a
  symbol: AAPL, LVMH, BTCUSD, NICKEL, COFFEE, XAUUSD.r, USOIL, NAS100 and any
  other broker symbol follow the identical path. The Step 48 instrument profiles
  only decide relevance strength once a symbol is known.
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
from app.services.instruments import InstrumentService
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


class FocusResolution(NamedTuple):
    """The outcome of resolving requested instrument names to broker symbols.

    ``requested`` is the normalized caller input (upper-cased, sorted,
    deduplicated). ``resolved`` holds the broker's OWN canonical spelling for
    each name it confirmed — never a spelling this service invented — and
    ``unresolved`` holds the requested names the broker's catalog did not offer
    (or offered ambiguously). ``requested`` is always the disjoint union of the
    other two.
    """

    requested: tuple[str, ...]
    resolved: tuple[str, ...]
    unresolved: tuple[str, ...]


class FinancialResearchContext(NamedTuple):
    """Graded published-source news for one explicit window and instrument set.

    ``focus_symbols`` are the instruments actually researched — the broker's
    canonical spellings when resolution is wired (Step 51), otherwise the
    normalized caller input. ``unresolved_symbols`` names the requested
    instruments the broker's catalog did not confirm: they are deliberately
    absent from ``focus_symbols`` rather than graded as if they existed.
    ``instruments`` is the set everything was graded against — the focus symbols
    themselves, because this surface holds no positions to add. When
    ``news_available`` is False the deployment has no news source configured
    and ``news_unavailable_reason`` says so; that is a different statement from
    an available source that published nothing in the window.
    """

    as_of: datetime
    window_from: datetime
    window_to: datetime
    focus_symbols: tuple[str, ...]
    unresolved_symbols: tuple[str, ...]
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
    empty feed.

    ``instrument_service`` is the optional broker-catalog boundary (Step 51).
    When it is wired, caller-supplied instrument names are resolved through the
    authenticated tenant's own MT5 catalog before they are graded, and the
    broker's canonical spelling is what travels into the context. When it is
    not wired, names are used as given — a deployment with no broker catalog
    cannot verify them — which is exactly the Step 49 behaviour.

    The service still holds no MT5 provider, no credentials, no positions and
    no tenant identity: it depends on the instrument *service*, which owns the
    tenant-scoped session, the provider and the deterministic presentation
    rules. Resolution is reached only through that boundary, so no catalog or
    provider logic is duplicated here.
    """

    def __init__(
        self,
        news_service: NewsService | None,
        instrument_service: InstrumentService | None = None,
    ):
        self._news = news_service
        self._instruments = instrument_service

    @property
    def news_source(self) -> str | None:
        """Provenance marker of the configured news source, or None when absent."""
        return self._news.source if self._news is not None else None

    def resolve_focus_symbols(
        self,
        focus_symbols: tuple[str, ...] | list[str],
    ) -> FocusResolution:
        """Resolve requested instrument names to the broker's canonical symbols.

        A blank name is a caller input error (``ValueError``). A name the
        broker's catalog cannot resolve to exactly one instrument — unknown,
        ambiguous or unusable as a symbol — is reported in ``unresolved`` rather
        than guessed at or silently accepted, so a caller decides what an
        unresolvable instrument means for it (the research API fails closed,
        the agent simply researches nothing for it).

        An MT5/catalog availability failure is NOT an unresolved instrument: it
        propagates as ``RuntimeError``, which every caller already maps to the
        established generic 503.
        """
        requested = _normalize_focus_symbols(focus_symbols)
        if self._instruments is None:
            # No broker catalog is wired: the names cannot be verified, so they
            # are reported as requested rather than as unresolved ("unverified"
            # and "known to be absent" are different statements).
            return FocusResolution(requested=requested, resolved=requested, unresolved=())

        resolved: set[str] = set()
        unresolved: list[str] = []
        for symbol in requested:
            try:
                instrument = self._instruments.resolve(symbol)
            except ValueError:
                # resolve() raises ValueError exactly when the broker's catalog
                # cannot identify one instrument for this name; the requested
                # names are already normalized above, so nothing else lands here.
                unresolved.append(symbol)
                continue
            resolved.add(instrument.symbol)
        return FocusResolution(
            requested=requested,
            resolved=tuple(sorted(resolved)),
            unresolved=tuple(unresolved),
        )

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

        Each requested instrument is resolved through the broker catalog first
        when one is wired (Step 51), and only the resolved canonical spellings
        are graded; the rest are reported in ``unresolved_symbols``.

        ``as_of`` echoes the caller's (already validated) window start so the
        context is reproducible; no clock is read here.
        """
        window_from = _require_aware(from_time, "from_time")
        window_to = _require_aware(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")

        resolution = self.resolve_focus_symbols(focus_symbols)
        focus = resolution.resolved
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
            unresolved_symbols=resolution.unresolved,
            instruments=instruments,
            news_available=news_available,
            news_data_source=news_data_source,
            news_unavailable_reason=unavailable_reason,
            news=news,
        )
