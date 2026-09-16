"""QuantGist economic-calendar provider tests (fully offline).

Nothing here touches the network: the provider's HTTP transport is injected as
an httpx.MockTransport and the API key is a test-only string. QuantGist is a
temporary development/test source, so these tests pin our adapter's contract
mapping, window semantics, provenance and failure translation - and the
verified live response shape (2026-09-16 smoke test):

    {"data": [...], "page", "per_page", "total", "total_pages", "has_more"}

with a global release_time-ascending feed that IGNORES the ``date`` parameter.
No pytest asyncio plugin is used; nothing in this file is async.
"""
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

import app.core.dependencies as deps
from app.core.config import EconomicCalendarSource, Settings, settings as app_settings
from app.providers.economic_calendar import EconomicEvent, EventImpact
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.quantgist_economic_calendar import QuantGistEconomicCalendarProvider
from app.services.economic_calendar import EconomicCalendarService

TEST_API_KEY = "qg_test_unit-only-key-not-a-real-credential"
BASE_URL = "https://api.quantgist.com/v1"
PER_PAGE = 20  # the live free tier's page size

# Feed instants in release_time-ascending order (what the live feed guarantees
# and what the early stop below relies on).
T16_08 = "2026-09-16 08:00:00+00:00"  # live shape 1: space separator + offset
T16_18 = "2026-09-16T18:00:00Z"  # live shape 2: T separator + Z
T17_09 = "2026-09-17T09:00:00Z"
T18_10 = "2026-09-18T10:00:00Z"
T19_11 = "2026-09-19T11:00:00Z"
T20_12 = "2026-09-20T12:00:00Z"

DAY = datetime(2026, 9, 16, tzinfo=UTC).date()
NEXT_DAY = datetime(2026, 9, 17, tzinfo=UTC).date()
WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


# --- helpers ---------------------------------------------------------------------------


def event_row(**overrides: Any) -> dict[str, Any]:
    """One QuantGist calendar row in the verified live shape."""
    row: dict[str, Any] = {
        "id": "4f3beb79-c23b-4866-b53d-19e694a503bb",
        "source": "fed_calendar",
        "event_type": "economic_release",
        "title": "US Consumer Price Index (CPI) YoY",
        "currency": "USD",
        "country": "US",
        "impact": "high",
        "release_time": T16_08,
        "forecast": None,
        "previous": "-0.4M",
        "actual": None,
        "is_released": False,
        "risk_score": 5.6,
        "affected_symbols": ["EURUSD", "USD"],
    }
    row.update(overrides)
    return row


def envelope(
    rows: list[dict[str, Any]],
    *,
    page: int = 1,
    per_page: int = PER_PAGE,
    total: int | None = None,
    total_pages: int | None = None,
    has_more: bool | None = None,
) -> dict[str, Any]:
    """The verified live paginated envelope with consistent default metadata."""
    offset = (page - 1) * per_page
    if total is None:
        total = offset + len(rows)
    if total_pages is None:
        total_pages = max(1, (total + per_page - 1) // per_page)
    if has_more is None:
        has_more = page < total_pages
    return {
        "data": list(rows),
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_more": has_more,
    }


def build(handler: Handler) -> tuple[QuantGistEconomicCalendarProvider, list[httpx.Request]]:
    """A provider wired to an offline transport, plus the requests it received."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider = QuantGistEconomicCalendarProvider(
        api_key=TEST_API_KEY, base_url=BASE_URL, transport=httpx.MockTransport(record)
    )
    return provider, requests


def returning(payload: object) -> Handler:
    """Handler that answers every request with the same JSON payload."""
    return lambda request: httpx.Response(200, json=payload)


def raising(error: Exception) -> Handler:
    """Handler that fails the way a broken transport does."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def paginated(
    rows_by_page: dict[int, list[dict[str, Any]]], per_page: int = PER_PAGE
) -> Handler:
    """Handler serving a multi-page feed the way the live endpoint does."""
    total = sum(len(rows) for rows in rows_by_page.values())
    total_pages = max(1, max(rows_by_page)) if rows_by_page else 1

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        rows = rows_by_page.get(page, [])
        return httpx.Response(
            200,
            json=envelope(
                rows, page=page, per_page=per_page, total=total, total_pages=total_pages
            ),
        )

    return handler


def by_page(payloads: dict[int, object]) -> Handler:
    """Handler serving an explicit per-page envelope (for inconsistent metadata)."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=payloads.get(page, payloads[min(payloads)]))

    return handler


def single_row_provider(**overrides: Any) -> tuple[QuantGistEconomicCalendarProvider, list[httpx.Request]]:
    return build(returning(envelope([event_row(**overrides)])))


def pages_requested(requests: list[httpx.Request]) -> list[int]:
    return [int(r.url.params["page"]) for r in requests]


# --- contract mapping ------------------------------------------------------------------


def test_calendar_rows_are_mapped_into_the_domain_contract() -> None:
    provider, _ = single_row_provider(
        id="evt_01HX9M3K4R2BGCZ8JQNVP7YW5E",
        release_time=T16_18,
        forecast=3.1,
        previous=3.2,
    )

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, EconomicEvent)
    assert event == EconomicEvent(
        event_id="evt_01HX9M3K4R2BGCZ8JQNVP7YW5E",
        timestamp=datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
        currency="USD",
        title="US Consumer Price Index (CPI) YoY",
        impact=EventImpact.HIGH,
        forecast="3.1",
        previous="3.2",
        actual=None,
    )


def test_both_verified_live_timestamp_shapes_parse_to_the_same_instant() -> None:
    # The live API alternates between the space/offset and T/Z forms for the
    # same event ids (smoke test finding); neither may be misread.
    provider, _ = build(
        returning(
            envelope(
                [
                    event_row(id="evt_space_form", release_time=T16_08),
                    event_row(id="evt_zulu_form", release_time="2026-09-16T08:00:00Z"),
                ]
            )
        )
    )

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert [event.event_id for event in events] == ["evt_space_form", "evt_zulu_form"]
    assert events[0].timestamp == events[1].timestamp == datetime(2026, 9, 16, 8, 0, tzinfo=UTC)
    assert all(event.timestamp.utcoffset() == UTC.utcoffset(None) for event in events)


@pytest.mark.parametrize(
    ("vendor_value", "expected"),
    [
        ("low", EventImpact.LOW),
        ("medium", EventImpact.MEDIUM),
        ("high", EventImpact.HIGH),
        # Case and surrounding whitespace are tolerated; the domain value is exact.
        ("High", EventImpact.HIGH),
        ("  medium  ", EventImpact.MEDIUM),
    ],
)
def test_vendor_impact_vocabulary_maps_to_domain_impacts(
    vendor_value: str, expected: EventImpact
) -> None:
    provider, _ = single_row_provider(impact=vendor_value)

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert events[0].impact is expected


def test_release_values_are_rendered_the_way_the_vendor_published_them() -> None:
    # Vendor values arrive as JSON numbers/null (and formatted strings); the
    # contract keeps them as strings without inventing precision.
    provider, _ = single_row_provider(forecast=303000, previous=5.5, actual="218K")

    event = provider.get_events(WINDOW_FROM, WINDOW_TO)[0]

    assert (event.forecast, event.previous, event.actual) == ("303000", "5.5", "218K")


def test_unreleased_values_stay_null() -> None:
    provider, _ = single_row_provider(forecast=None, previous=None, actual=None)

    event = provider.get_events(WINDOW_FROM, WINDOW_TO)[0]

    assert (event.forecast, event.previous, event.actual) == (None, None, None)


def test_non_utc_release_time_is_normalized_to_utc() -> None:
    provider, _ = single_row_provider(release_time="2026-09-16T14:30:00+02:00")

    event = provider.get_events(WINDOW_FROM, WINDOW_TO)[0]

    assert event.timestamp == datetime(2026, 9, 16, 12, 30, tzinfo=UTC)
    assert event.timestamp.utcoffset() == WINDOW_FROM.utcoffset()


def test_naive_release_time_is_rejected_rather_than_guessed() -> None:
    provider, _ = single_row_provider(release_time="2026-09-16T12:30:00")

    with pytest.raises(RuntimeError, match="timezone-aware"):
        provider.get_events(WINDOW_FROM, WINDOW_TO)


def test_vendor_only_fields_do_not_leak_past_the_contract() -> None:
    # source / event_type / risk_score / affected_symbols are vendor
    # extensions; the provider boundary is the domain contract.
    provider, _ = single_row_provider()

    event = provider.get_events(WINDOW_FROM, WINDOW_TO)[0]

    assert event._fields == (
        "event_id",
        "timestamp",
        "currency",
        "title",
        "impact",
        "forecast",
        "previous",
        "actual",
    )


def test_source_marker_is_explicit_and_distinct_from_the_development_fake() -> None:
    provider, _ = single_row_provider()

    # A delayed, quota-limited source must never be mistakable for the
    # deterministic development fake or for live data.
    assert provider.source == "quantgist-free-development"
    assert provider.source != FakeEconomicCalendarProvider.source
    assert EconomicCalendarService(provider).source == "quantgist-free-development"


# --- request shape and window semantics ------------------------------------------------


def test_request_targets_the_documented_calendar_endpoint_with_the_key_header() -> None:
    provider, requests = single_row_provider()

    provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url.copy_with(query=None)) == f"{BASE_URL}/calendar"
    # The live API ignores every date filter and serves one global feed; the
    # only parameter it honours is the page number.
    assert dict(request.url.params) == {"page": "1"}
    assert request.headers["X-API-Key"] == TEST_API_KEY
    # The key travels in a header, never in the URL (so it cannot end up in a
    # URL log line).
    assert TEST_API_KEY not in str(request.url)


def test_a_window_that_ends_inside_the_first_page_costs_exactly_one_request() -> None:
    # Feed rows run past the window end, so the first at-or-after event proves
    # the rest of the feed is outside the window: no second page is fetched.
    provider, requests = build(
        returning(
            envelope([event_row(id="e1", release_time=T16_08),
                      event_row(id="e2", release_time=T16_18),
                      event_row(id="e3", release_time=T17_09),
                      event_row(id="e4", release_time=T18_10)])
        )
    )

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert [event.event_id for event in events] == ["e1", "e2"]
    assert pages_requested(requests) == [1]


def test_a_multi_day_window_is_not_fanned_out_per_utc_day() -> None:
    # The live endpoint ignores ``date``: one request per UTC day would fetch
    # the identical page repeatedly. The feed is walked once instead.
    provider, requests = build(
        returning(
            envelope([event_row(id="e1", release_time="2026-09-15T18:00:00Z"),
                      event_row(id="e2", release_time=T16_08),
                      event_row(id="e3", release_time=T16_18),
                      event_row(id="e4", release_time=T17_09),
                      event_row(id="e5", release_time=T18_10)])
        )
    )

    events = provider.get_events(
        datetime(2026, 9, 15, 12, 0, tzinfo=UTC), datetime(2026, 9, 17, 6, 0, tzinfo=UTC)
    )

    assert [event.event_id for event in events] == ["e1", "e2", "e3"]
    assert pages_requested(requests) == [1]


def test_window_is_half_open_at_both_ends() -> None:
    inside = event_row(id="evt_inside", release_time="2026-09-16T23:59:59Z")
    outside = event_row(id="evt_outside", release_time="2026-09-17T00:00:00Z")
    provider, requests = build(returning(envelope([inside, outside])))

    events = provider.get_events(WINDOW_FROM, WINDOW_TO)

    assert [event.event_id for event in events] == ["evt_inside"]
    # The walk stopped at the first at-or-after event: no second request.
    assert pages_requested(requests) == [1]


def test_rows_timestamped_inside_the_window_start_are_excluded() -> None:
    before = event_row(id="evt_before", release_time="2026-09-16T00:00:00Z")
    after = event_row(id="evt_after", release_time="2026-09-16T00:00:01Z")
    provider, _ = build(returning(envelope([before, after])))

    events = provider.get_events(datetime(2026, 9, 16, 0, 0, 1, tzinfo=UTC), WINDOW_TO)

    assert [event.event_id for event in events] == ["evt_after"]


def test_a_row_from_another_day_is_filtered_out() -> None:
    other_day = event_row(id="evt_yesterday", release_time="2026-09-15T23:30:00Z")
    provider, _ = build(returning(envelope([other_day])))

    assert provider.get_events(WINDOW_FROM, WINDOW_TO) == ()


def test_a_window_entirely_before_the_feed_stops_at_the_first_event() -> None:
    # Every feed row lies beyond the window end: the answer is empty and known
    # complete after the first at-or-after row - no second request is spent.
    provider, requests = build(
        returning(
            envelope([event_row(id="far", release_time=T19_11),
                      event_row(id="farther", release_time=T20_12)])
        )
    )

    assert provider.get_events(WINDOW_FROM, WINDOW_TO) == ()
    assert pages_requested(requests) == [1]


def test_a_window_entirely_after_the_feed_reads_the_whole_feed_once() -> None:
    # Every feed row lies before the window start: the local filter keeps
    # nothing, the feed end is reached, and the answer is empty but complete.
    provider, requests = build(
        paginated({1: [event_row(id="e1", release_time=T16_08)],
                   2: [event_row(id="e2", release_time=T16_18)]})
    )

    events = provider.get_events(datetime(2026, 9, 25, tzinfo=UTC), datetime(2026, 9, 26, tzinfo=UTC))

    assert events == ()
    assert pages_requested(requests) == [1, 2]


def test_inverted_or_empty_windows_are_rejected() -> None:
    provider, _ = single_row_provider()

    with pytest.raises(ValueError):
        provider.get_events(WINDOW_TO, WINDOW_FROM)
    with pytest.raises(ValueError):
        provider.get_events(WINDOW_FROM, WINDOW_FROM)


def test_naive_window_arguments_are_rejected() -> None:
    provider, _ = single_row_provider()

    with pytest.raises(ValueError, match="timezone-aware"):
        provider.get_events(datetime(2026, 9, 16, 0, 0), WINDOW_TO)


# --- pagination ------------------------------------------------------------------------


def test_the_last_page_is_fetched_when_the_window_extends_beyond_it() -> None:
    # The window ends after the feed's last row, so the walk must reach the
    # last page to be complete.
    provider, requests = build(
        paginated({1: [event_row(id="e1", release_time=T16_08),
                       event_row(id="e2", release_time=T16_18)],
                   2: [event_row(id="e3", release_time=T17_09),
                       event_row(id="e4", release_time=T18_10)]})
    )

    events = provider.get_events(WINDOW_FROM, datetime(2026, 9, 19, tzinfo=UTC))

    assert [event.event_id for event in events] == ["e1", "e2", "e3", "e4"]
    assert pages_requested(requests) == [1, 2]


def test_no_page_is_fetched_beyond_the_one_that_reaches_the_window_end() -> None:
    # The window ends inside page 2 (its first row is at/after the window end):
    # page 3 is never requested even though the feed has more pages.
    provider, requests = build(
        paginated({1: [event_row(id="e1", release_time=T16_08),
                       event_row(id="e2", release_time=T16_18)],
                   2: [event_row(id="e3", release_time=T17_09),
                       event_row(id="e4", release_time=T18_10)],
                   3: [event_row(id="e5", release_time=T19_11),
                       event_row(id="e6", release_time=T20_12)]})
    )

    events = provider.get_events(WINDOW_FROM, datetime(2026, 9, 18, tzinfo=UTC))

    assert [event.event_id for event in events] == ["e1", "e2", "e3"]
    assert pages_requested(requests) == [1, 2]


def test_pagination_terminates_at_total_pages_even_when_has_more_is_true() -> None:
    # A last page that still claims has_more=true (vendor regression) must not
    # loop: total_pages bounds the walk.
    provider, requests = build(
        by_page(
            {
                1: envelope([event_row(id="e0", release_time="2026-09-15T00:00:00Z")],
                            page=1, total=4, total_pages=2, has_more=True),
                2: envelope([event_row(id="e1", release_time=T16_08),
                             event_row(id="e2", release_time=T16_18)],
                            page=2, total=4, total_pages=2, has_more=True),
            }
        )
    )

    events = provider.get_events(WINDOW_FROM, datetime(2026, 9, 19, tzinfo=UTC))

    assert [event.event_id for event in events] == ["e1", "e2"]
    assert pages_requested(requests) == [1, 2]


def test_an_empty_feed_is_an_empty_result_not_an_error() -> None:
    provider, _ = build(returning(envelope([])))

    assert provider.get_events(WINDOW_FROM, WINDOW_TO) == ()


def test_a_row_after_the_window_end_stops_the_walk_before_page_metadata_matters() -> None:
    # Completeness only depends on the pagination metadata when a whole page
    # was read without reaching the window end; when the walk stops early the
    # envelope metadata is not even examined (this payload carries none).
    provider, _ = build(returning({"data": [event_row(id="e1", release_time=T16_08),
                                            event_row(id="e2", release_time=T16_18),
                                            event_row(id="e3", release_time=T17_09)]}))

    assert [event.event_id for event in provider.get_events(WINDOW_FROM, WINDOW_TO)] == ["e1", "e2"]


# --- configuration and failure translation ---------------------------------------------


def test_a_missing_api_key_fails_before_any_network_call() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be attempted without an API key")

    with pytest.raises(RuntimeError, match="API key is not configured"):
        QuantGistEconomicCalendarProvider(
            api_key="   ", base_url=BASE_URL, transport=httpx.MockTransport(unexpected)
        )


def test_a_blank_base_url_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="base URL is not configured"):
        QuantGistEconomicCalendarProvider(api_key=TEST_API_KEY, base_url="  ")


@pytest.mark.parametrize(
    "transport_error",
    [httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout],
)
def test_transport_failures_become_runtime_errors(transport_error: type[httpx.HTTPError]) -> None:
    provider, _ = build(raising(transport_error("simulated transport failure")))

    with pytest.raises(RuntimeError, match="request failed"):
        provider.get_events(WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize("status_code", [400, 401, 403, 429, 500, 503])
def test_non_200_responses_become_runtime_errors_naming_the_status(status_code: int) -> None:
    provider, _ = build(lambda request: httpx.Response(status_code, json={"error": "x"}))

    with pytest.raises(RuntimeError, match=f"HTTP {status_code}"):
        provider.get_events(WINDOW_FROM, WINDOW_TO)


def test_an_unparseable_body_is_rejected() -> None:
    provider, _ = build(lambda request: httpx.Response(200, content=b"<html>not json</html>"))

    with pytest.raises(RuntimeError, match="not valid JSON"):
        provider.get_events(WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "a", "mapping"],  # top level is not an object
        {},  # no data key at all (the live container is 'data')
        {"data": "not a list"},  # wrong type for data
        {"data": ["not a row"]},  # row is not an object
        {"data": [event_row()]},  # missing pagination metadata after a full page
        envelope([event_row()], page=2),  # wrong page number in the envelope
        # unusable page size (explicit dict: the helper would divide by zero)
        {"data": [event_row()], "page": 1, "per_page": 0, "total": 1, "total_pages": 1, "has_more": False},
        envelope([event_row()], total_pages=0),  # unusable page count
        {**envelope([event_row()]), "has_more": "true"},  # has_more is not a bool
        {**envelope([event_row()]), "total": -1},  # negative total
        {**envelope([event_row()]), "total_pages": True},  # a bool is not a page count
    ],
)
def test_structurally_wrong_payloads_are_rejected(payload: object) -> None:
    # The window runs past the single row, so the whole page is read and the
    # pagination metadata must hold.
    provider, _ = build(returning(payload))

    with pytest.raises(RuntimeError):
        provider.get_events(WINDOW_FROM, datetime(2026, 9, 25, tzinfo=UTC))


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},  # missing id
        {"id": "   "},  # blank id
        {"title": None},  # missing title
        {"currency": ""},  # blank currency
        {"impact": None},  # missing impact
        {"impact": "extreme"},  # unmapped impact level
        {"release_time": None},  # missing release time
        {"release_time": "yesterday"},  # not ISO 8601
        {"forecast": True},  # a boolean is never a release value
        {"actual": {"value": 1}},  # unexpected value type
    ],
)
def test_unusable_rows_are_rejected(overrides: dict[str, Any]) -> None:
    provider, _ = single_row_provider(**overrides)

    with pytest.raises(RuntimeError):
        provider.get_events(WINDOW_FROM, WINDOW_TO)


def test_error_messages_never_contain_the_api_key() -> None:
    failures: list[Handler] = [
        raising(httpx.ConnectError("boom")),
        lambda request: httpx.Response(401, json={"error": "unauthorized"}),
        lambda request: httpx.Response(200, content=b"not json"),
        returning(envelope([event_row(impact="extreme")])),
    ]

    for handler in failures:
        provider, _ = build(handler)
        with pytest.raises(RuntimeError) as excinfo:
            provider.get_events(WINDOW_FROM, WINDOW_TO)
        assert TEST_API_KEY not in str(excinfo.value)


# --- service integration ---------------------------------------------------------------


def test_the_service_adds_ordering_and_impact_filtering_to_vendor_rows() -> None:
    late = event_row(id="evt_late", release_time=T16_18, impact="high")
    early = event_row(id="evt_early", release_time=T16_08, impact="low")
    provider, _ = build(
        returning(envelope([late, early, event_row(id="evt_beyond", release_time=T17_09)]))
    )
    service = EconomicCalendarService(provider)

    events = service.get_events(WINDOW_FROM, WINDOW_TO)

    # The provider does not sort; chronological order is the service's guarantee.
    assert [event.event_id for event in events] == ["evt_early", "evt_late"]
    assert [event.event_id for event in service.filter_by_minimum_impact(events, EventImpact.HIGH)] == [
        "evt_late"
    ]


def test_todays_events_read_the_utc_day_through_the_provider() -> None:
    provider, requests = single_row_provider(release_time=T16_18)
    service = EconomicCalendarService(provider)

    events = service.get_todays_events(datetime(2026, 9, 16, 9, 0, tzinfo=UTC))

    assert [event.event_id for event in events] == ["4f3beb79-c23b-4866-b53d-19e694a503bb"]
    assert pages_requested(requests) == [1]


# --- composition-root source selection -------------------------------------------------


def test_development_without_a_key_keeps_the_deterministic_fake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.AUTO, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", "", raising=True)

    service = deps.get_economic_calendar_service()

    assert service.source == FakeEconomicCalendarProvider.source


def test_development_with_a_key_selects_the_quantgist_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default source (auto) is the historical behaviour: in development a
    # configured key selects the QuantGist development/test tier.
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.AUTO, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    service = deps.get_economic_calendar_service()

    # Constructing the provider performs no network call, so this stays offline.
    assert service.source == "quantgist-free-development"


@pytest.mark.parametrize("environment", ["staging", "production", ""])
def test_non_development_environments_fail_closed_even_with_a_key(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", environment, raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.AUTO, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    # A delayed, quota-limited development source must never be served to a
    # broker's customers.
    with pytest.raises(HTTPException) as excinfo:
        deps.get_economic_calendar_service()

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Economic calendar data source is not configured"


# --- configured source selection (Step 46) ---------------------------------------------
#
# The economic calendar is MANDATORY for every Agent request, so the source a
# deployment serves is an explicit selection that fails closed (503) instead of
# degrading to a different source. These cases are the whole matrix
# (source x environment); none of them touches the network.

GENERIC_SOURCE_DETAIL = "Economic calendar data source is not configured"


def test_auto_remains_the_committed_default() -> None:
    # An existing .env (which carries no ECONOMIC_CALENDAR_SOURCE) must keep the
    # historical behaviour, so the default is the environment-driven selection
    # rather than a hard-coded source.
    default = Settings.model_fields["ECONOMIC_CALENDAR_SOURCE"].default

    assert default is EconomicCalendarSource.AUTO


def test_development_with_an_explicit_placeholder_source_ignores_a_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings,
        "ECONOMIC_CALENDAR_SOURCE",
        EconomicCalendarSource.DEVELOPMENT_FAKE,
        raising=True,
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    service = deps.get_economic_calendar_service()

    # An explicit selection wins over the ambient key, so a developer can always
    # get deterministic placeholder data without touching the key.
    assert service.source == FakeEconomicCalendarProvider.source


def test_development_with_an_explicit_quantgist_source_uses_the_free_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.QUANTGIST, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    service = deps.get_economic_calendar_service()

    assert service.source == "quantgist-free-development"


def test_development_with_an_explicit_quantgist_source_without_a_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.QUANTGIST, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", "", raising=True)

    with caplog.at_level(logging.WARNING, logger="app.core.dependencies"):
        with pytest.raises(HTTPException) as excinfo:
            deps.get_economic_calendar_service()

    # Choosing a source whose configuration is missing is an error, never a
    # silent fallback to another source.
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == GENERIC_SOURCE_DETAIL
    # The operator gets the precise, value-free reason; the client never does.
    assert "QUANTGIST_API_KEY is not configured" in caplog.text
    assert TEST_API_KEY not in caplog.text


def test_the_production_seam_fails_closed_until_a_vendor_is_registered(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Even in development: "production" names the seam a real vendor is
    # registered behind, and until one exists selecting it must not quietly
    # serve placeholder data.
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(
        app_settings, "ECONOMIC_CALENDAR_SOURCE", EconomicCalendarSource.PRODUCTION, raising=True
    )
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    with caplog.at_level(logging.WARNING, logger="app.core.dependencies"):
        with pytest.raises(HTTPException) as excinfo:
            deps.get_economic_calendar_service()

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == GENERIC_SOURCE_DETAIL
    assert "no production economic-calendar provider is implemented" in caplog.text


@pytest.mark.parametrize("environment", ["staging", "production", ""])
@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (EconomicCalendarSource.AUTO, "no production economic-calendar source is configured"),
        (EconomicCalendarSource.DEVELOPMENT_FAKE, "development-only"),
        (EconomicCalendarSource.QUANTGIST, "development/test source only"),
        (
            EconomicCalendarSource.PRODUCTION,
            "no production economic-calendar provider is implemented",
        ),
    ],
)
def test_no_calendar_source_is_served_outside_development(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    environment: str,
    source: EconomicCalendarSource,
    reason: str,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", environment, raising=True)
    monkeypatch.setattr(app_settings, "ECONOMIC_CALENDAR_SOURCE", source, raising=True)
    monkeypatch.setattr(app_settings, "QUANTGIST_API_KEY", TEST_API_KEY, raising=True)

    with caplog.at_level(logging.WARNING, logger="app.core.dependencies"):
        with pytest.raises(HTTPException) as excinfo:
            deps.get_economic_calendar_service()

    # Every cell fails closed with the SAME client-facing detail, so a caller
    # cannot probe the deployment's configuration; the precise reason is logged
    # for the operator instead.
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == GENERIC_SOURCE_DETAIL
    assert reason in caplog.text
    # Secret hygiene: the configured key is present in every cell above and must
    # never reach a log line or a response detail.
    assert TEST_API_KEY not in caplog.text
    assert TEST_API_KEY not in str(excinfo.value)
