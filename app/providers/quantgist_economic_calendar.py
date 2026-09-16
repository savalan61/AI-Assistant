"""QuantGist economic calendar provider (DEVELOPMENT/TEST SOURCE ONLY).

QuantGist's free tier is a temporary stand-in for a real calendar source, not
this project's final commercial vendor: it is quota-limited and serves delayed
data, so nothing it returns may be presented as live market data. That is why
``source`` carries an explicit marker into every API response (``data_source``)
instead of a neutral name.

Read-only: the adapter performs GET /calendar requests only. It never writes,
never trades, and never produces a recommendation.

Auth: the API key arrives from configuration (app/core/config.py) and is sent in
the ``X-API-Key`` header. It is never logged, never embedded in an error message
and never committed.

Verified live API behaviour (2026-09-16 smoke test) this adapter is written
against:

- The response is a single paginated envelope
  ``{"data": [...], "page", "per_page", "total", "total_pages", "has_more"}``;
  events live under ``data``, not ``events``.
- The ``date`` query parameter is IGNORED by the live endpoint: every date
  filter variant returned the identical first page (release dates spread
  2026-09-16 .. 2026-10-08). Windowing is therefore done locally, and no
  request is spent per UTC day.
- The feed is ordered by ``release_time`` ascending, which is what makes the
  early stop below safe.
- ``release_time`` arrives in two observed ISO 8601 shapes for the same event
  ids ("2026-09-16 13:15:00+00:00" and "2026-09-16T13:15:00Z"); both parse.

Error model: every expected external failure (missing configuration, timeout,
transport error, non-2xx response, malformed or unusable payload) is translated
into RuntimeError at this boundary - the same convention as the MT5 providers -
which the API layer maps to a generic 503. No retry and no caching are
implemented: the existing calendar design has neither, and the endpoint's
provider call is offloaded through the existing blocking boundary.

Sync by design: like MT5MarketDataProvider / MT5PositionProvider, this adapter is
synchronous; callers on the event loop offload it.
"""
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.economic_calendar import (
    EconomicCalendarProvider,
    EconomicEvent,
    EventImpact,
)

_DEFAULT_BASE_URL = "https://api.quantgist.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 15.0

# One shared transport for production use. Tests inject an httpx.MockTransport
# instead, keeping the suite fully offline (the OpenAI-compatible LLM adapter's
# pattern, applied to the calendar).
_DEFAULT_TRANSPORT = httpx.HTTPTransport()

# The live endpoint ignores ``date`` and serves a global feed of ~88 upcoming
# events (per_page=20, 5 pages). Page size must be a positive int or the
# envelope is unusable - there is no safe default to invent, so it fails closed.
_MIN_PER_PAGE = 1

# Vendor impact vocabulary -> domain impact. The vendor documents
# low | medium | high; any other value is a contract violation and fails loudly
# rather than being mapped to a guessed level.
_IMPACT_BY_NAME: dict[str, EventImpact] = {
    "low": EventImpact.LOW,
    "medium": EventImpact.MEDIUM,
    "high": EventImpact.HIGH,
}

# A vendor failure must never surface as a partially populated result, so every
# unusable payload reports the same non-committal reason.
_UNREADABLE_PAYLOAD = "QuantGist economic calendar response was not understood"


def _require_aware_utc(value: datetime, field: str) -> datetime:
    """Return ``value`` normalized to UTC, rejecting naive datetimes.

    The service layer validates this first; the check is repeated here so a
    naive datetime can never be silently interpreted as the server's local time
    while the window is filtered.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _require_text(value: object, field: str) -> str:
    """Return a required non-empty string field or fail loudly."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"QuantGist event was missing its {field}")
    return value


def _published_value(value: object) -> str | None:
    """Render a release value the way the vendor published it.

    The domain contract keeps forecast/previous/actual as strings because
    calendars publish formatted values ("3.1%", "220K") and coercing them
    would invent precision that was never reported. The live feed publishes
    JSON numbers or null (and at least one formatted string, "-0.4M"), so an
    integer stays integral, a float keeps its shortest round-trip form, a
    string is passed through unchanged, and null stays None.
    """
    if value is None:
        return None
    # bool is an int subclass; a boolean is never a release value.
    if isinstance(value, bool):
        raise RuntimeError("QuantGist event value had an unexpected type")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value
    raise RuntimeError("QuantGist event value had an unexpected type")


def _parse_release_time(value: object) -> datetime:
    """Parse the vendor's ISO 8601 release time into an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("QuantGist event was missing its release time")
    try:
        # fromisoformat accepts both live shapes: the trailing "Z" form
        # (Python 3.11+) and the space-separated "+00:00" form.
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise RuntimeError("QuantGist event release time was not ISO 8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # A naive vendor timestamp must not be guessed into a timezone.
        raise RuntimeError("QuantGist event release time was not timezone-aware")
    return parsed.astimezone(UTC)


def _to_economic_event(row: object) -> EconomicEvent:
    """Translate one QuantGist calendar row into the domain contract."""
    if not isinstance(row, dict):
        raise RuntimeError(_UNREADABLE_PAYLOAD)
    impact_name = row.get("impact")
    if not isinstance(impact_name, str):
        raise RuntimeError(_UNREADABLE_PAYLOAD)
    impact = _IMPACT_BY_NAME.get(impact_name.strip().lower())
    if impact is None:
        # Guessing a level would silently mis-rank the event in the
        # intelligence classifier, so an unmapped value fails the request. The
        # vendor value is included truncated (it is untrusted input and must not
        # be able to fill a log line), and never reaches an API response.
        raise RuntimeError(f"QuantGist returned an unknown event impact: {impact_name[:32]!r}")
    return EconomicEvent(
        # The vendor id (UUID) is globally unique, so it satisfies the
        # contract's per-window uniqueness requirement as-is.
        event_id=_require_text(row.get("id"), "event id"),
        timestamp=_parse_release_time(row.get("release_time")),
        currency=_require_text(row.get("currency"), "event currency"),
        title=_require_text(row.get("title"), "event title"),
        impact=impact,
        forecast=_published_value(row.get("forecast")),
        previous=_published_value(row.get("previous")),
        actual=_published_value(row.get("actual")),
    )


class QuantGistEconomicCalendarProvider(EconomicCalendarProvider):
    """Read-only QuantGist calendar adapter (development/test source).

    The live endpoint serves one global paginated feed, ordered by release time
    ascending, and ignores the ``date`` parameter. The requested half-open
    window is therefore covered by walking that feed with an early stop instead
    of spending one request per UTC day (verified live: per-day fan-out would
    fetch the same page repeatedly and still miss events).

    Extra vendor fields (event_type, session, risk_score, affected_symbols,
    ...) are dropped: the domain contract is the boundary, and nothing
    source-specific travels past it.
    """

    source = "quantgist-free-development"

    def __init__(
        self,
        api_key: str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        # Fail fast on missing configuration, before any network call: an empty
        # key means the deployment did not select this source.
        key = api_key.strip()
        if not key:
            raise RuntimeError("QuantGist API key is not configured")
        endpoint = base_url.strip()
        if not endpoint:
            raise RuntimeError("QuantGist base URL is not configured")
        self._api_key = key
        self._base_url = endpoint.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport if transport is not None else _DEFAULT_TRANSPORT

    def get_events(self, from_time: datetime, to_time: datetime) -> tuple[EconomicEvent, ...]:
        """Events inside the half-open window [from_time, to_time)."""
        window_from = _require_aware_utc(from_time, "from_time")
        window_to = _require_aware_utc(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")

        events: list[EconomicEvent] = []
        # The feed is release_time-ascending (verified live), so the first event
        # at or beyond the window's exclusive end proves every later row - on
        # this page and on all later pages - is outside the window too. Paging
        # stops there and no unnecessary page is fetched. The local half-open
        # filter remains the only thing that decides what is returned.
        for page in self._pages():
            for row in page:
                event = _to_economic_event(row)
                if event.timestamp >= window_to:
                    # Only in-window rows were appended, so what we hold is the
                    # complete answer.
                    return tuple(events)
                if window_from <= event.timestamp:
                    events.append(event)
        # The feed ended (last page) before the window end was reached: every
        # row the feed holds for this window has been seen.
        return tuple(events)

    def _pages(self) -> Iterator[list[object]]:
        """Yield each page's ``data`` rows until the feed's last page.

        The envelope is the verified live shape
        ``{"data": [...], "page", "per_page", "total", "total_pages", "has_more"}``.
        The pagination metadata is checked only when the consumer has read a
        whole page without reaching its window end - i.e. only when knowing
        whether another page exists matters for completeness. An unusable
        envelope then fails closed: with no trustworthy page count a partially
        populated result could not be distinguished from a complete one. The
        loop bound is ``total_pages``, so a vendor regression cannot loop
        forever; ``has_more`` is the second stop signal the live API provides.
        """
        page_number = 1
        while True:
            payload = self._request_calendar(page_number)
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise RuntimeError(_UNREADABLE_PAYLOAD)
            yield rows
            # Every metadata field is validated explicitly (bool excluded from
            # int) so pyright narrows the types for the termination check below.
            page_field = payload.get("page")
            per_page = payload.get("per_page")
            total = payload.get("total")
            total_pages = payload.get("total_pages")
            has_more = payload.get("has_more")
            if (
                not isinstance(page_field, int)
                or isinstance(page_field, bool)
                or page_field != page_number
                or not isinstance(per_page, int)
                or isinstance(per_page, bool)
                or per_page < _MIN_PER_PAGE
                or not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
                or not isinstance(total_pages, int)
                or isinstance(total_pages, bool)
                or total_pages < 1
                or not isinstance(has_more, bool)
            ):
                raise RuntimeError(_UNREADABLE_PAYLOAD)
            if not rows or page_number >= total_pages or not has_more:
                return
            page_number += 1

    def _request_calendar(self, page_number: int) -> dict[str, object]:
        """Fetch one feed page, translating every failure mode."""
        try:
            # Short-lived client per call: this provider holds no connection and
            # is constructed per request, so there is nothing to pool.
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.get(
                    f"{self._base_url}/calendar",
                    # The live API ignores every date filter and serves one
                    # global feed; ``page`` is the only parameter it honours.
                    params={"page": page_number},
                    headers={"X-API-Key": self._api_key, "Accept": "application/json"},
                )
        except httpx.HTTPError:
            # Timeout / transport / protocol failure. The key travels in a
            # header and the URL is not needed by the caller, so the message
            # stays generic and the original exception is not chained.
            raise RuntimeError("QuantGist economic calendar request failed") from None
        if response.status_code != 200:
            # Status code only: the vendor body is untrusted input, and the API
            # answers a generic 503 for every provider failure anyway. The
            # status is kept because it separates a bad key/quota problem from
            # an outage in logs without disclosing anything secret.
            raise RuntimeError(
                f"QuantGist economic calendar request failed (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("QuantGist economic calendar response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError(_UNREADABLE_PAYLOAD)
        return payload
