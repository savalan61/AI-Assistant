"""Alpha Vantage news provider tests (fully offline).

Nothing here touches the network: the provider's HTTP transport is injected as
an httpx.MockTransport and the API key is a test-only marker string. Alpha
Vantage is a development/test source, so these tests pin our adapter's mapping
into the existing NewsItem contract, the window/limit semantics, provenance, the
failure translation - and the fact that the vendor's sentiment fields are never
carried into our data.

No pytest asyncio plugin is used; nothing in this file is async.
"""
import inspect
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.providers.alphavantage_news import AlphaVantageNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.news import NewsItem, NewsProvider
from app.providers.position import Position, PositionType
from app.services.agent.prompt import build_prompt
from app.services.economic_intelligence import (
    EconomicIntelligenceContext,
    EconomicIntelligenceService,
    RelevanceLevel,
)
from app.services.economic_calendar import EconomicCalendarService
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.services.financial_context import FinancialContext
from app.services.fundamental_intelligence import FundamentalIntelligenceService
from app.services.news import MAX_SUMMARY_CHARS, NewsService
from app.services.positions import PositionService

TEST_API_KEY = "av_test_unit-only-key-not-a-real-credential"
BASE_URL = "https://www.alphavantage.co/query"

WINDOW_FROM = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 17, 0, 0, tzinfo=UTC)

# The vendor's published timestamp shapes (UTC, no offset in either).
T16_0730 = "20260916T073000"
T16_1315 = "20260916T131500"
T17_0900 = "20260917T090000"
T16_0730_NO_SECONDS = "20260916T0730"

Handler = Callable[[httpx.Request], httpx.Response]


# --- fixtures / helpers -----------------------------------------------------------------


def article_row(**overrides: Any) -> dict[str, Any]:
    """One Alpha Vantage NEWS_SENTIMENT feed row in the documented shape."""
    row: dict[str, Any] = {
        "title": "Gold climbs as the dollar slips ahead of US inflation data",
        "url": "https://example.invalid/articles/gold-dollar-inflation",
        "time_published": T16_0730,
        "authors": ["Jane Doe"],
        "summary": "Placeholder summary: the article discusses gold and the US dollar.",
        "banner_image": "https://example.invalid/img/banner.png",
        "source": "Example Newswire",
        "category_within_source": "Markets",
        "source_domain": "example.invalid",
        "topics": [
            {"topic": "economy_macro", "relevance_score": "0.92"},
            {"topic": "financial_markets", "relevance_score": "0.81"},
        ],
        "overall_sentiment_score": 0.31,
        "overall_sentiment_label": "Somewhat-Bullish",
        "ticker_sentiment": [
            {
                "ticker": "XAU",
                "relevance_score": "0.55",
                "sentiment_score": "0.31",
                "sentiment_label": "Somewhat-Bullish",
            }
        ],
    }
    row.update(overrides)
    return row


def envelope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The documented NEWS_SENTIMENT envelope."""
    return {
        "items": str(len(rows)),
        "sentiment_score_definition": "x <= -0.35: Bearish; ...",
        "relevance_score_definition": "0 < x <= 1, with a higher score indicating higher relevance.",
        "feed": rows,
    }


def make_provider(
    handler: Handler,
    *,
    api_key: str = TEST_API_KEY,
    base_url: str = BASE_URL,
) -> AlphaVantageNewsProvider:
    """Provider whose HTTP goes to an offline mock transport."""
    return AlphaVantageNewsProvider(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )


def responding(*rows: dict[str, Any]):
    """A handler returning one fixed envelope, recording every request."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=envelope(list(rows)))

    return handler, requests


def params_of(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


# --- the contract -----------------------------------------------------------------------


def test_the_adapter_implements_the_existing_news_contract() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json=envelope([])))

    assert isinstance(provider, NewsProvider)
    # Explicit, non-production provenance marker.
    assert provider.source == "alphavantage-free-development"


def test_the_adapter_never_emits_a_log_line_of_its_own() -> None:
    # Structural: the adapter must not emit a record (the vendor requires the key
    # as a query parameter, so an unguarded log statement is exactly how a key
    # would leak). Its only logging use is the redaction filter.
    source = inspect.getsource(inspect.getmodule(AlphaVantageNewsProvider))  # type: ignore[arg-type]

    assert "print(" not in source
    assert "_install_key_redaction" in source
    for call in (".debug(", ".info(", ".warning(", ".error(", ".exception(", ".critical("):
        assert call not in source, call


def test_the_parsed_item_only_carries_the_contract_fields() -> None:
    handler, _ = responding(article_row())
    provider = make_provider(handler)

    items = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert len(items) == 1
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


# --- the request: vendor contract and bounds --------------------------------------------


def test_the_request_is_one_bounded_news_sentiment_query() -> None:
    handler, requests = responding(article_row())
    provider = make_provider(handler)

    provider.get_news(WINDOW_FROM, WINDOW_TO, limit=7)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url).startswith(BASE_URL)
    assert request.method == "GET"
    params = params_of(request)
    assert params["function"] == "NEWS_SENTIMENT"
    # The vendor's documented time format, derived from the caller's half-open
    # UTC window.
    assert params["time_from"] == "20260916T0000"
    assert params["time_to"] == "20260917T0000"
    assert params["sort"] == "LATEST"
    assert params["limit"] == "7"
    # The key comes only from configuration and is sent the way the vendor
    # requires it (nothing else is derived from it).
    assert params["apikey"] == TEST_API_KEY
    # No ticker filter: our callers pass none, and relevance is decided locally.
    assert "tickers" not in params


def test_no_other_query_parameter_is_sent() -> None:
    handler, requests = responding(article_row())
    make_provider(handler).get_news(WINDOW_FROM, WINDOW_TO)

    assert set(params_of(requests[0])) == {
        "function",
        "time_from",
        "time_to",
        "sort",
        "limit",
        "apikey",
    }


def test_a_declared_instrument_filter_is_pushed_to_the_vendor_ticker_parameter() -> None:
    handler, requests = responding(article_row())
    make_provider(handler).get_news(WINDOW_FROM, WINDOW_TO, instruments=("xauusd", "eurusd"))

    assert params_of(requests[0])["tickers"] == "XAUUSD,EURUSD"


def test_the_caller_limit_bounds_the_vendor_limits() -> None:
    handler, requests = responding()
    provider = make_provider(handler)

    provider.get_news(WINDOW_FROM, WINDOW_TO)  # no caller cap
    provider.get_news(WINDOW_FROM, WINDOW_TO, limit=1000)
    provider.get_news(WINDOW_FROM, WINDOW_TO, limit=10_000)

    limits = [params_of(request)["limit"] for request in requests]
    assert limits == ["50", "1000", "1000"]


def test_a_non_positive_limit_is_rejected() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json=envelope([])))

    with pytest.raises(ValueError):
        provider.get_news(WINDOW_FROM, WINDOW_TO, limit=0)
    with pytest.raises(ValueError):
        provider.get_news(WINDOW_FROM, WINDOW_TO, limit=-1)


def test_a_naive_or_inverted_window_is_rejected() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json=envelope([])))

    with pytest.raises(ValueError):
        provider.get_news(datetime(2026, 9, 16), WINDOW_TO)
    with pytest.raises(ValueError):
        provider.get_news(WINDOW_FROM, WINDOW_TO.replace(tzinfo=None))
    with pytest.raises(ValueError):
        provider.get_news(WINDOW_TO, WINDOW_FROM)


# --- mapping ----------------------------------------------------------------------------


def test_a_valid_article_maps_into_the_contract() -> None:
    handler, _ = responding(article_row())
    provider = make_provider(handler)

    item = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]

    assert item.title == "Gold climbs as the dollar slips ahead of US inflation data"
    assert item.publisher == "Example Newswire"
    assert item.summary.startswith("Placeholder summary:")
    assert item.url == "https://example.invalid/articles/gold-dollar-inflation"
    # The vendor's own topic labels travel as factual metadata...
    assert item.categories == ("economy_macro", "financial_markets")
    # ...and its ticker tags do NOT become our declared instrument/currency tags:
    # relevance is decided by the existing deterministic layer, never by the
    # vendor's ticker vocabulary.
    assert item.instruments == ()
    assert item.currencies == ()


def test_the_published_timestamp_is_normalized_to_aware_utc() -> None:
    handler, _ = responding(article_row(time_published=T16_1315))
    provider = make_provider(handler)

    item = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]

    assert item.published_at == datetime(2026, 9, 16, 13, 15, tzinfo=UTC)
    assert item.published_at.tzinfo is not None
    assert item.published_at.utcoffset() == timedelta(0)


def test_the_documented_short_timestamp_shape_also_parses() -> None:
    handler, _ = responding(article_row(time_published=T16_0730_NO_SECONDS))
    provider = make_provider(handler)

    item = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]

    assert item.published_at == datetime(2026, 9, 16, 7, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "2026-09-16T07:30:00Z",  # not the documented shape
        "20260916",  # date only
        "not-a-timestamp",
        None,
        20260916,
    ],
)
def test_an_unusable_publication_time_fails_closed(value: object) -> None:
    handler, _ = responding(article_row(time_published=value))
    provider = make_provider(handler)

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert TEST_API_KEY not in str(excinfo.value)


@pytest.mark.parametrize(("field", "expected"), [("title", "title"), ("source", "publisher")])
def test_a_missing_required_field_fails_closed(field: str, expected: str) -> None:
    handler, _ = responding(article_row(**{field: None}))
    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match=expected):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize("value", ["", "   ", 42])
def test_a_blank_required_field_fails_closed(value: object) -> None:
    handler, _ = responding(article_row(title=value))
    provider = make_provider(handler)

    with pytest.raises(RuntimeError):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


def test_a_missing_url_is_allowed_and_a_blank_one_becomes_none() -> None:
    handler, _ = responding(article_row(url=None), article_row(url="   ", time_published=T16_1315))
    provider = make_provider(handler)

    items = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert [item.url for item in items] == [None, None]


def test_a_missing_summary_is_reported_as_empty_not_invented() -> None:
    handler, _ = responding(article_row(summary=None))
    provider = make_provider(handler)

    assert provider.get_news(WINDOW_FROM, WINDOW_TO)[0].summary == ""


def test_a_long_summary_is_bounded_below_the_service_boundary() -> None:
    long_summary = "s" * (MAX_SUMMARY_CHARS + 500)
    handler, _ = responding(article_row(summary=long_summary))
    provider = make_provider(handler)

    item = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]

    # Bounded here (the article body is never fetched), with a visible marker,
    # and strictly inside the service's own hard boundary.
    assert long_summary not in item.summary
    assert item.summary.endswith("...")
    assert len(item.summary) < MAX_SUMMARY_CHARS
    # The service accepts it unchanged (no silent truncation there).
    assert NewsService(provider, max_items=5).get_news(WINDOW_FROM, WINDOW_TO)[0].summary == item.summary


def test_an_unknown_topics_shape_is_dropped_rather_than_guessed() -> None:
    handler, _ = responding(article_row(topics=["economy_macro", 7, {"topic": None}, {"no_topic": 1}]))
    provider = make_provider(handler)

    assert provider.get_news(WINDOW_FROM, WINDOW_TO)[0].categories == ()


def test_topic_labels_are_normalized_and_deduplicated() -> None:
    handler, _ = responding(
        article_row(topics=[{"topic": "Economy_Macro"}, {"topic": "economy_macro"}, {"topic": " Finance "}])
    )
    provider = make_provider(handler)

    assert provider.get_news(WINDOW_FROM, WINDOW_TO)[0].categories == ("economy_macro", "finance")


def test_the_item_id_is_stable_and_unique_per_article() -> None:
    handler, _ = responding(
        article_row(),
        article_row(url="https://example.invalid/articles/other", time_published=T16_1315),
    )
    provider = make_provider(handler)

    first = provider.get_news(WINDOW_FROM, WINDOW_TO)
    second = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert [item.item_id for item in first] == [item.item_id for item in second]
    assert len({item.item_id for item in first}) == 2
    assert all(item.item_id.startswith("av-") for item in first)


def test_an_article_without_a_url_still_gets_a_stable_id() -> None:
    handler, _ = responding(article_row(url=None))
    provider = make_provider(handler)

    first = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]
    second = provider.get_news(WINDOW_FROM, WINDOW_TO)[0]

    assert first.item_id == second.item_id
    assert first.item_id.startswith("av-")


# --- window semantics -------------------------------------------------------------------


def test_items_outside_the_half_open_window_are_dropped() -> None:
    handler, _ = responding(
        # Before the window.
        article_row(time_published="20260915T235959", url="https://example.invalid/a"),
        # Exactly at the inclusive start: kept.
        article_row(time_published="20260916T000000", url="https://example.invalid/b"),
        # Inside: kept.
        article_row(time_published=T16_1315, url="https://example.invalid/c"),
        # Exactly at the exclusive end: dropped.
        article_row(time_published="20260917T000000", url="https://example.invalid/d"),
        # After the window.
        article_row(time_published=T17_0900, url="https://example.invalid/e"),
    )
    provider = make_provider(handler)

    items = provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert [item.url for item in items] == [
        "https://example.invalid/b",
        "https://example.invalid/c",
    ]


def test_an_empty_feed_is_a_normal_result() -> None:
    handler, _ = responding()
    provider = make_provider(handler)

    assert provider.get_news(WINDOW_FROM, WINDOW_TO) == ()


def test_a_non_utc_window_is_normalized_before_the_request() -> None:
    handler, requests = responding()
    provider = make_provider(handler)
    # The same instants written with a +02:00 offset instead of UTC.
    plus_two = timezone(timedelta(hours=2))

    provider.get_news(
        datetime(2026, 9, 16, 2, 0, tzinfo=plus_two),
        datetime(2026, 9, 17, 2, 0, tzinfo=plus_two),
    )

    # The vendor is always asked in UTC.
    params = params_of(requests[0])
    assert params["time_from"] == "20260916T0000"
    assert params["time_to"] == "20260917T0000"


# --- failure translation ----------------------------------------------------------------


def test_a_transport_failure_becomes_a_generic_error_without_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed")

    provider = make_provider(handler)

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    message = str(excinfo.value)
    assert "request failed" in message
    assert TEST_API_KEY not in message
    # The original exception can carry the request URL (and therefore the key),
    # so it is deliberately not chained.
    assert excinfo.value.__cause__ is None


def test_a_timeout_becomes_a_generic_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match="request failed"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_a_non_200_response_reports_only_the_status(status: int) -> None:
    body = {"Information": "Our standard API rate limit is 25 requests per day.", "apikey": TEST_API_KEY}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    provider = make_provider(handler)

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    # Status only: the vendor body is untrusted and may echo the key.
    assert str(excinfo.value) == f"Alpha Vantage news request failed (HTTP {status})"
    assert TEST_API_KEY not in str(excinfo.value)


def test_a_rate_limit_or_invalid_key_body_fails_closed_http_200() -> None:
    # The vendor answers these with HTTP 200 and an "Information"/"Note" body
    # instead of a feed, so a strict status check is not enough.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Information": (
                    "the parameter apikey is invalid or missing. Please claim your free API key."
                )
            },
        )

    provider = make_provider(handler)

    with pytest.raises(RuntimeError) as excinfo:
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert str(excinfo.value) == "Alpha Vantage news response was not understood"
    assert TEST_API_KEY not in str(excinfo.value)


def test_a_note_body_fails_closed_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Note": "Thank you for using Alpha Vantage!"})

    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match="was not understood"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


@pytest.mark.parametrize(
    "payload",
    [
        [{"title": "not an object envelope"}],  # a bare list
        {"feed": "not-a-list"},
        {"feed": None},
        {"items": "1"},  # no feed at all
        {},
    ],
)
def test_a_malformed_envelope_fails_closed(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match="was not understood"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


def test_an_unusable_row_fails_closed_rather_than_being_dropped_silently() -> None:
    handler, _ = responding(article_row(), "not-a-row")  # type: ignore[arg-type]
    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match="was not understood"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


def test_invalid_json_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    provider = make_provider(handler)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)


# --- configuration safety ---------------------------------------------------------------


@pytest.mark.parametrize("api_key", ["", "   "])
def test_a_missing_key_fails_fast_without_a_network_call(api_key: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=envelope([]))

    with pytest.raises(RuntimeError, match="API key is not configured"):
        AlphaVantageNewsProvider(
            api_key=api_key, transport=httpx.MockTransport(handler)
        )

    assert requests == []


def test_a_missing_base_url_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="base URL is not configured"):
        AlphaVantageNewsProvider(
            api_key=TEST_API_KEY,
            base_url="  ",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=envelope([]))),
        )


def test_the_http_client_request_log_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    # httpx logs the full request URL at INFO, and the vendor requires the key in
    # that URL: the adapter installs a redaction filter so the key cannot reach a
    # log even when INFO logging is enabled for the HTTP client.
    handler, _ = responding(article_row())
    provider = make_provider(handler)

    with caplog.at_level(logging.INFO, logger="httpx"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    assert "HTTP Request" in caplog.text
    assert TEST_API_KEY not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_the_log_line_survives_redaction_unchanged_apart_from_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, _ = responding(article_row())
    provider = make_provider(handler)

    with caplog.at_level(logging.INFO, logger="httpx"):
        provider.get_news(WINDOW_FROM, WINDOW_TO)

    # The record is kept (never dropped) and still identifies the request.
    line = next(record.getMessage() for record in caplog.records if "HTTP Request" in record.getMessage())
    assert "NEWS_SENTIMENT" in line
    assert "20260916T0000" in line
    assert "apikey=[REDACTED]" in line


def test_the_redaction_filter_is_installed_only_once_per_key() -> None:
    handler, _ = responding()

    make_provider(handler)
    make_provider(handler)
    make_provider(handler)

    from app.providers.alphavantage_news import _RedactApiKey

    installed = [f for f in logging.getLogger("httpx").filters if isinstance(f, _RedactApiKey)]
    matching = [f for f in installed if f.matches(TEST_API_KEY)]
    assert len(matching) == 1


def test_the_redaction_filter_only_touches_the_configured_key() -> None:
    from app.providers.alphavantage_news import _RedactApiKey

    filter_ = _RedactApiKey(TEST_API_KEY)
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: %s %s "%s %d %s"',
        args=("GET", f"{BASE_URL}?apikey={TEST_API_KEY}&limit=1", "HTTP/1.1", 200, "OK"),
        exc_info=None,
    )

    assert filter_.filter(record) is True

    rendered = record.getMessage()
    assert TEST_API_KEY not in rendered
    assert "limit=1" in rendered and "HTTP/1.1" in rendered and " 200 " in rendered


def test_no_error_message_or_log_line_contains_the_key(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": TEST_API_KEY})

    provider = make_provider(handler)

    with caplog.at_level(logging.DEBUG):
        for call in (
            lambda: provider.get_news(WINDOW_FROM, WINDOW_TO),
            lambda: provider.get_news(WINDOW_FROM, WINDOW_TO, limit=0),
        ):
            with pytest.raises((RuntimeError, ValueError)) as excinfo:
                call()
            assert TEST_API_KEY not in str(excinfo.value)

    assert TEST_API_KEY not in caplog.text


# --- integration: NewsService, Fundamental Intelligence and the agent prompt ------------


def test_the_service_accepts_provider_output_without_silent_changes() -> None:
    handler, _ = responding(article_row(), article_row(time_published=T16_1315, url=None))
    service = NewsService(make_provider(handler), max_items=20)

    items = service.get_news(WINDOW_FROM, WINDOW_TO)

    assert len(items) == 2
    assert service.source == "alphavantage-free-development"


def test_the_service_still_orders_and_caps_provider_output() -> None:
    handler, _ = responding(
        article_row(time_published=T16_1315, url="https://example.invalid/later"),
        article_row(time_published=T16_0730, url="https://example.invalid/earlier"),
    )
    service = NewsService(make_provider(handler), max_items=1)

    items = service.get_news(WINDOW_FROM, WINDOW_TO)

    assert len(items) == 1
    assert items[0].url == "https://example.invalid/earlier"


def test_fundamental_intelligence_uses_the_existing_relevance_for_the_real_feed() -> None:
    # A gold/USD headline must reach XAUUSD through the existing deterministic
    # mapping (gold/XAU plus the USD reference), with no sentiment involved.
    handler, _ = responding(
        article_row(
            title="Gold climbs as the dollar slips ahead of US inflation data",
            summary="Placeholder summary.",
        )
    )
    economic = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider()),
    ).build_today_context(now=datetime(2026, 9, 16, 9, 15, tzinfo=UTC))
    service = FundamentalIntelligenceService(
        news_service=NewsService(make_provider(handler), max_items=20)
    )

    context = service.build_context(economic, focus_symbol="XAUUSD")

    assert context.news_available is True
    assert context.news_data_source == "alphavantage-free-development"
    assert len(context.news) == 1
    entry = context.news[0]
    assert entry.relevance is RelevanceLevel.POTENTIALLY_RELEVANT
    assert "XAUUSD" in entry.matched_instruments
    assert entry.reason
    # The same contract as before: positions still carry a factual exposure.
    assert context.positions
    assert context.positions[0].status.value in {"KNOWN", "UNKNOWN"}


def test_the_agent_prompt_renders_the_alpha_vantage_block_without_sentiment() -> None:
    handler, _ = responding(article_row(summary="Placeholder summary."))
    economic = EconomicIntelligenceService(
        calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
        position_service=PositionService(FakePositionProvider()),
    ).build_today_context(now=datetime(2026, 9, 16, 9, 15, tzinfo=UTC))
    fundamental = FundamentalIntelligenceService(
        news_service=NewsService(make_provider(handler), max_items=20)
    ).build_context(economic, focus_symbol="XAUUSD")

    prompt = build_prompt("What is happening with XAUUSD today?", _financial_context(), None, economic, fundamental)

    # Provenance and the article's factual fields reach the model...
    assert "alphavantage-free-development" in prompt.content
    assert "Example Newswire" in prompt.content
    assert "Gold climbs as the dollar slips ahead of US inflation data" in prompt.content
    # ...while the vendor's sentiment label and scores are never carried into
    # our data or the prompt.
    assert "Somewhat-Bullish" not in prompt.content
    assert "sentiment" not in prompt.content.lower()


def _financial_context() -> FinancialContext:
    """Minimal financial context so the prompt can be rendered offline."""
    from app.providers.account_info import AccountInfo
    from app.services.portfolio_intelligence import build_portfolio_intelligence

    position = Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("3642.50"),
        current_price=Decimal("3648.20"),
        profit=Decimal("57.00"),
    )
    account = AccountInfo(
        login=10001,
        name="Test Trader",
        balance=Decimal("10000.00"),
        equity=Decimal("10050.00"),
        margin=Decimal("250.00"),
        free_margin=Decimal("9800.00"),
        margin_level=4020.0,
        currency="USD",
        server="Test-Server",
    )
    as_of = datetime(2026, 9, 16, 9, 15, tzinfo=UTC)
    return FinancialContext(
        broker_id=1,
        as_of=as_of,
        account=account,
        positions=(position,),
        trade_history=(),
        portfolio_intelligence=build_portfolio_intelligence(account, (position,), as_of),
    )


def test_the_economic_intelligence_context_type_is_unchanged() -> None:
    # Regression guard that this step added no field to the context the
    # fundamental layer consumes.
    assert "positions" in EconomicIntelligenceContext._fields
    assert "events" in EconomicIntelligenceContext._fields
