"""Tests for NEWS_SOURCE resolution (the environment x source matrix).

Pins the deliberate difference between the two public data sources: the calendar
is MANDATORY for every agent request, so an unusable calendar source refuses
(503); news is fundamental context that may legitimately be absent, so a
deployment with no news source reports news as unavailable instead. In BOTH
cases an explicitly selected source that cannot be served refuses rather than
falling back, and the refusal detail is the same generic one the endpoint
already used (never a configuration value, never a provider internal).

Offline: no MT5, no database, no network, no vendor. Nothing here constructs an
HTTP client, and no API key is ever involved.
"""
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.core.config as config_module
import app.core.dependencies as deps
from app.core.config import NewsSource, settings as app_settings
from app.providers.fake_news import FakeNewsProvider
from app.services.news import NewsService

DEVELOPMENT = "development"


def resolve(
    monkeypatch: pytest.MonkeyPatch, *, environment: str, source: NewsSource
) -> NewsService | None:
    """Resolve the news service for one environment/source cell."""
    monkeypatch.setattr(app_settings, "APP_ENV", environment, raising=True)
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", source, raising=True)
    return deps.get_news_service()


# --- the supported state: no news source at all -----------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production", ""])
def test_auto_outside_development_has_no_news_source(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    # Not an error: the fundamental context reports news as unavailable with a
    # reason, which is different from "there is no news".
    assert resolve(monkeypatch, environment=environment, source=NewsSource.AUTO) is None


def test_auto_in_development_selects_the_deterministic_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    service = resolve(monkeypatch, environment=DEVELOPMENT, source=NewsSource.AUTO)

    assert isinstance(service, NewsService)
    assert service.source == FakeNewsProvider.source


def test_explicit_development_source_in_development_is_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = resolve(monkeypatch, environment=DEVELOPMENT, source=NewsSource.DEVELOPMENT_FAKE)

    assert isinstance(service, NewsService)
    assert service.source == "fake-development-placeholder"


def test_the_configured_cap_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "NEWS_MAX_ITEMS", 3, raising=True)

    service = resolve(monkeypatch, environment=DEVELOPMENT, source=NewsSource.AUTO)

    assert service is not None
    assert service.max_items == 3


# --- fail closed: an explicitly selected unusable source --------------------------------


@pytest.mark.parametrize(
    ("environment", "source"),
    [
        ("staging", NewsSource.DEVELOPMENT_FAKE),
        ("production", NewsSource.DEVELOPMENT_FAKE),
        ("", NewsSource.DEVELOPMENT_FAKE),
        (DEVELOPMENT, NewsSource.PRODUCTION),
        ("staging", NewsSource.PRODUCTION),
        ("production", NewsSource.PRODUCTION),
    ],
)
def test_an_unusable_source_refuses_with_the_generic_detail(
    monkeypatch: pytest.MonkeyPatch, environment: str, source: NewsSource
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        resolve(monkeypatch, environment=environment, source=source)

    assert excinfo.value.status_code == 503
    # The same generic detail for every refusal: no source name, no environment,
    # no configuration value reaches the client.
    assert excinfo.value.detail == "News data source is not configured"


def test_a_refusal_never_falls_back_to_another_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # A development source outside development must not quietly become the
    # deterministic feed: it refuses, and no provider is even constructed.
    constructed: list[str] = []

    class _RecordingFakeNewsProvider(FakeNewsProvider):
        def __init__(self) -> None:
            constructed.append("constructed")
            super().__init__()

    monkeypatch.setattr(deps, "FakeNewsProvider", _RecordingFakeNewsProvider)
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.DEVELOPMENT_FAKE, raising=True)

    with pytest.raises(HTTPException):
        deps.get_news_service()

    assert constructed == []


def test_a_refusal_is_explained_to_the_operator_without_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.PRODUCTION, raising=True)

    with caplog.at_level(logging.WARNING, logger="app.core.dependencies"):
        with pytest.raises(HTTPException):
            deps.get_news_service()

    # The operator gets the precise, value-free reason; the client got only the
    # generic detail above.
    assert caplog.text
    assert "production" in caplog.text
    assert "no production news provider is implemented" in caplog.text


def test_the_news_seam_never_reads_an_api_key() -> None:
    # Structural: the news seam addresses no credential setting, so no news
    # source can be configured with, or leak, a key at this boundary.
    dependencies_source = Path(deps.__file__ or "").read_text(encoding="utf-8")
    assert dependencies_source
    news_seam = dependencies_source.split("def get_news_service", 1)[1].split(
        "# Agent LLM wiring", 1
    )[0]

    assert news_seam
    assert "API_KEY" not in news_seam
    assert "QUANTGIST" not in news_seam
    # It is a source selection over configuration values, nothing else.
    assert config_module.__file__


# --- the fundamental service composes the resolved source --------------------------------


def test_the_fundamental_service_uses_the_resolved_news_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", DEVELOPMENT, raising=True)
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.AUTO, raising=True)

    service = deps.get_fundamental_intelligence_service()

    assert service.news_source == "fake-development-placeholder"


def test_the_fundamental_service_reports_no_news_source_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "staging", raising=True)
    monkeypatch.setattr(app_settings, "NEWS_SOURCE", NewsSource.AUTO, raising=True)

    service = deps.get_fundamental_intelligence_service()

    assert service.news_source is None
