"""Alpha Vantage news provider (DEVELOPMENT/TEST SOURCE ONLY).

Alpha Vantage's free tier is a temporary stand-in for a real news source, not
this project's commercial vendor: its free quota is small (25 requests/day at the
time of writing) and its terms are for personal use, so nothing it returns may be
presented as a production news feed. That is why ``source`` carries an explicit
marker (`alphavantage-free-development`) into every API response and agent prompt
instead of a neutral name.

Read-only: the adapter performs one NEWS_SENTIMENT query and never writes, never
fetches an article page (title + the vendor's own bounded excerpt only), and
never produces a recommendation.

Auth: the API key arrives from configuration (app/core/config.py). Alpha Vantage
requires the key as the ``apikey`` QUERY parameter - there is no header form -
so the adapter never logs, never formats the URL into an error message, and
never chains the original httpx exception (which can carry the request URL).
Every failure is translated into a generic RuntimeError, the same convention as
the MT5 and QuantGist providers, which the API layer maps to a generic 503.

Documented vendor contract this adapter is written against
(https://www.alphavantage.co/documentation/#news-sentiment):

- one endpoint, ``GET {base_url}?function=NEWS_SENTIMENT``;
- ``time_from``/``time_to`` in ``YYYYMMDDTHHMM`` format (the vendor's own time
  filter, always sent so the request is bounded by the caller's window);
- ``sort=LATEST`` (newest first) and ``limit`` (vendor maximum 1000);
- the response's articles live under ``feed``, each carrying ``title``, ``url``,
  ``time_published`` (``YYYYMMDDTHHMMSS``, UTC), ``summary``, ``source`` (the
  publisher), ``topics`` and sentiment fields;
- an unusable request (invalid key, quota exhausted, unsupported parameter) is
  answered with HTTP 200 and a body carrying ``Information``/``Note`` instead of
  ``feed``. That is why the adapter requires a real ``feed`` list and reports a
  non-committal "not understood" error otherwise: the vendor body is untrusted
  input and may echo the submitted key, so it is never included in an error
  message or a log line.

Verified live behaviour (2026-09-16 smoke test, ONE request, no retry): the
envelope parses exactly as documented above; every returned ``time_published``
was a UTC instant inside the requested window, which is what confirms the UTC
reading; the vendor's own excerpt exceeds our bound on real data (the observed
items were bounded to 400 characters plus the marker); and the UNFILTERED feed is
dominated by equity/position-filing copy - in an observed sample of 20 items the
existing relevance layer could tie none of them to XAUUSD. That is a truthful
general market feed rather than a defect, but it means the default feed is not
XAUUSD-focused: narrowing it by topic or ticker is a deliberate product decision
this step did not take (see CURRENT_CHECKPOINT.md known issue 16).

Only fields our NewsItem contract carries are parsed. The vendor's sentiment
scores/labels, banner images, authors and ticker-level sentiment are dropped: the
deterministic relevance layer decides relevance from the title/currency legs
(declared tags first, then the documented keyword map), never from a vendor
sentiment number.

Secret hygiene: because the vendor needs the key in the query string, httpx's own
optional INFO-level request log would otherwise record a URL containing it. This
module therefore installs (idempotently, for the configured value only) a
redaction filter on the ``httpx`` logger that removes that exact key from any
record it emits. The filter never drops a record and changes no logging level, so
observability is unaffected; it exists solely so that "the key is never logged"
holds even when a deployment turns on INFO logging.
"""
import logging
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx

from app.providers.news import NewsItem, NewsProvider

_DEFAULT_BASE_URL = "https://www.alphavantage.co/query"
_DEFAULT_TIMEOUT_SECONDS = 15.0

# One shared transport for production use. Tests inject an httpx.MockTransport
# instead, keeping the suite fully offline (the QuantGist/OpenAI-compatible
# adapter pattern, applied to news).
_DEFAULT_TRANSPORT = httpx.HTTPTransport()

# Caller-supplied cap when the caller passes none, and the vendor's documented
# maximum. Both bound the work one request can cause.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000

# Bounded excerpt: the vendor publishes a multi-sentence article summary, and the
# outbound prompt must stay bounded. This stays comfortably under the news
# service's own hard boundary (NewsService.MAX_SUMMARY_CHARS = 600), which would
# reject an over-long excerpt rather than truncate it silently; the marker makes
# the truncation visible to a reader.
_MAX_SUMMARY_CHARS = 400
_TRUNCATED = "..."

# Vendor timestamp shapes: ``YYYYMMDDTHHMMSS`` as published in the feed, and the
# ``YYYYMMDDTHHMM`` shape the vendor documents for its time_from/time_to
# parameters (accepted so a vendor change degrades into a parse rather than a
# silent misreading).
_TIME_FORMATS = ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M")

# A vendor failure must never surface as a partially populated result, so every
# unusable payload reports the same non-committal reason (never the vendor body).
_UNREADABLE_PAYLOAD = "Alpha Vantage news response was not understood"

# What the key is replaced with in a redacted log record.
_REDACTED = "[REDACTED]"

# The third-party logger whose request line would contain the key (httpx logs
# "HTTP Request: GET <full url> ..." at INFO level).
_HTTP_LOGGER_NAME = "httpx"


class _RedactApiKey(logging.Filter):
    """Removes one configured key from every record the HTTP client logs.

    A record's arguments are formatted into its message only after filters run,
    and httpx passes the request URL as an object rather than a string, so both
    the message and each argument (stringified, then re-stringified only when it
    contains the key) are redacted. Records are never dropped.
    """

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def matches(self, secret: str) -> bool:
        """True when this filter already redacts ``secret`` (install once)."""
        return self._secret == secret

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.msg)
        if record.args is not None:
            record.args = self._redact_argument(record.args)  # type: ignore[assignment]
        return True

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            return value.replace(self._secret, _REDACTED)
        return value

    def _redact_argument(self, value: object) -> object:
        if isinstance(value, tuple):
            return tuple(self._redact_argument(item) for item in value)
        if isinstance(value, dict):
            return {key: self._redact_argument(item) for key, item in value.items()}
        rendered = self._redact(value)
        if rendered is not value:
            return rendered
        # A non-string argument (for example httpx.URL) can still stringify with
        # the key in it, which is how the request line is built.
        text = str(value)
        return text.replace(self._secret, _REDACTED) if self._secret in text else value


def _install_key_redaction(secret: str) -> None:
    """Install the redaction filter for ``secret`` on the HTTP logger, once."""
    logger = logging.getLogger(_HTTP_LOGGER_NAME)
    for existing in logger.filters:
        if isinstance(existing, _RedactApiKey) and existing.matches(secret):
            return
    logger.addFilter(_RedactApiKey(secret))


def _require_text(value: object, field: str) -> str:
    """Return a required non-empty string field or fail loudly."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Alpha Vantage news article was missing its {field}")
    return value.strip()


def _parse_published_at(value: object) -> datetime:
    """Parse the vendor's naive UTC timestamp into an aware UTC datetime.

    Alpha Vantage publishes ``time_published`` in UTC without an offset, so the
    offset is attached explicitly here rather than guessed from the server's
    local timezone. Parsing is exact rather than lenient: ``strptime`` accepts
    fewer digits than a directive asks for (``%S`` happily reads "0" out of
    "0730", silently producing 07:03 from 07:30), so each candidate format is
    only accepted when re-formatting the result reproduces the input. An
    unparseable value fails closed.
    """
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError("Alpha Vantage news article was missing its publication time")
    text = value.strip()
    for time_format in _TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, time_format)
        except ValueError:
            continue
        if parsed.strftime(time_format) != text:
            # A lenient partial match is not the documented shape.
            continue
        return parsed.replace(tzinfo=UTC)
    raise RuntimeError("Alpha Vantage news publication time was not in the documented format")


def _bounded_summary(value: object) -> str:
    """The vendor's own excerpt, deterministically bounded (never fetched)."""
    if not isinstance(value, str):
        # A summary is optional in the feed; an absent one is reported as empty
        # rather than invented, and the title still identifies the item.
        return ""
    text = value.strip()
    if len(text) <= _MAX_SUMMARY_CHARS:
        return text
    return text[:_MAX_SUMMARY_CHARS] + _TRUNCATED


def _topics(value: object) -> tuple[str, ...]:
    """The vendor's own topic labels, carried verbatim as factual metadata.

    Purely informational: relevance is never decided from a topic label, and an
    unrecognised shape is dropped rather than guessed at.
    """
    if not isinstance(value, list):
        return ()
    labels: set[str] = set()
    for entry in value:
        if isinstance(entry, dict):
            topic = entry.get("topic")
            if isinstance(topic, str) and topic.strip():
                labels.add(topic.strip().lower())
    return tuple(sorted(labels))


def _item_id(url: str | None, title: str, published_at: datetime) -> str:
    """Stable per-article id (the vendor publishes none).

    Derived from the article URL when present, otherwise from its title and
    publication instant, so the same article always yields the same id and the
    id carries no vendor-specific object past this boundary.
    """
    seed = url if url else f"{title}|{published_at.isoformat()}"
    return f"av-{sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _to_news_item(row: object) -> NewsItem:
    """Translate one Alpha Vantage article into the domain contract."""
    if not isinstance(row, dict):
        raise RuntimeError(_UNREADABLE_PAYLOAD)
    payload: dict[str, Any] = row
    published_at = _parse_published_at(payload.get("time_published"))
    title = _require_text(payload.get("title"), "title")
    # ``source`` is the publisher (e.g. "Reuters"); it is required because an
    # unattributed article is not usable as sourced context.
    publisher = _require_text(payload.get("source"), "publisher")
    url_value = payload.get("url")
    url = url_value.strip() if isinstance(url_value, str) and url_value.strip() else None
    return NewsItem(
        item_id=_item_id(url, title, published_at),
        published_at=published_at,
        publisher=publisher,
        title=title,
        summary=_bounded_summary(payload.get("summary")),
        url=url,
        # Declared instrument tags stay empty: the vendor's ticker vocabulary is
        # not our symbol vocabulary, and inventing a mapping would mis-tag an
        # article. The deterministic relevance layer decides from the title.
        instruments=(),
        currencies=(),
        categories=_topics(payload.get("topics")),
    )


class AlphaVantageNewsProvider(NewsProvider):
    """Read-only Alpha Vantage NEWS_SENTIMENT adapter (development/test source).

    One request per call, bounded by the caller's half-open UTC window (sent to
    the vendor as its own time filter and re-applied locally, so a vendor that
    widens the range cannot leak an out-of-window item) and by the requested
    result cap.
    """

    source = "alphavantage-free-development"

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
            raise RuntimeError("Alpha Vantage API key is not configured")
        endpoint = base_url.strip()
        if not endpoint:
            raise RuntimeError("Alpha Vantage base URL is not configured")
        self._api_key = key
        self._base_url = endpoint
        self._timeout_seconds = timeout_seconds
        self._transport = transport if transport is not None else _DEFAULT_TRANSPORT
        # The vendor requires the key in the query string; make sure the HTTP
        # client's own request log can never carry it (idempotent, additive).
        _install_key_redaction(key)

    def get_news(
        self,
        from_time: datetime,
        to_time: datetime,
        *,
        instruments: tuple[str, ...] = (),
        limit: int | None = None,
    ) -> tuple[NewsItem, ...]:
        """Articles published inside the half-open window [from_time, to_time)."""
        window_from = self._require_aware_utc(from_time, "from_time")
        window_to = self._require_aware_utc(to_time, "to_time")
        if window_from >= window_to:
            raise ValueError("from_time must be earlier than to_time")
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")

        payload = self._request_news(window_from, window_to, instruments, limit)
        rows = payload.get("feed")
        if not isinstance(rows, list):
            # Covers the vendor's HTTP-200 "Information"/"Note" bodies (invalid
            # key, exhausted quota, unsupported parameter) without ever
            # surfacing that body, which may echo the submitted key.
            raise RuntimeError(_UNREADABLE_PAYLOAD)

        items: list[NewsItem] = []
        for row in rows:
            item = _to_news_item(row)
            # Local re-filter: the half-open window is our semantics, not the
            # vendor's, so an item outside it is dropped even if the vendor
            # returned it.
            if window_from <= item.published_at < window_to:
                items.append(item)
        return tuple(items)

    @staticmethod
    def _require_aware_utc(value: datetime, field: str) -> datetime:
        """Return ``value`` normalized to UTC, rejecting naive datetimes."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)

    def _request_news(
        self,
        window_from: datetime,
        window_to: datetime,
        instruments: tuple[str, ...],
        limit: int | None,
    ) -> dict[str, object]:
        """Perform the single bounded query, translating every failure mode."""
        params: dict[str, str | int] = {
            "function": "NEWS_SENTIMENT",
            # The vendor's own time filter (YYYYMMDDTHHMM, its documented
            # format). Sent so the vendor does the bounding where it can.
            "time_from": window_from.strftime("%Y%m%dT%H%M"),
            "time_to": window_to.strftime("%Y%m%dT%H%M"),
            "sort": "LATEST",
            "limit": self._effective_limit(limit),
            "apikey": self._api_key,
        }
        if instruments:
            # A declared-tag filter is pushed to the vendor's own ticker
            # parameter (our callers pass none today: relevance is decided
            # locally, and an untagged article must never be dropped for
            # carrying no tag).
            params["tickers"] = ",".join(instrument.strip().upper() for instrument in instruments)
        try:
            # Short-lived client per call: this provider holds no connection and
            # is constructed per request, so there is nothing to pool.
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.get(self._base_url, params=params)
        except httpx.HTTPError:
            # Timeout / transport / protocol failure. The key travels in the
            # query string, so the original exception (which can carry the
            # request URL) is deliberately not chained, and no URL is formatted
            # into the message.
            raise RuntimeError("Alpha Vantage news request failed") from None
        if response.status_code != 200:
            # Status code only: the vendor body is untrusted input and may echo
            # the submitted key.
            raise RuntimeError(f"Alpha Vantage news request failed (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Alpha Vantage news response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError(_UNREADABLE_PAYLOAD)
        return payload

    @staticmethod
    def _effective_limit(limit: int | None) -> int:
        """The vendor limit for this call: the caller's cap, clamped safely."""
        if limit is None:
            return _DEFAULT_LIMIT
        return max(1, min(limit, _MAX_LIMIT))
