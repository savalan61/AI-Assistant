"""Step 56 tests: the development-only OpenRouter free LLM behind the existing seam.

Covers the wiring only — OpenRouter is one more ``OpenAICompatibleLLMProvider`` in
the existing free pool, added in development and pinned to one free model:

* it is pooled in development when configured, and never outside development
  (a configured key must not put a broker's customers on a free tier);
* a blank or placeholder key leaves it out (fail closed, never degrade);
* the deployment endpoint keeps precedence, and stays the only provider outside
  development;
* the grounded context the project already builds (mandatory economic calendar,
  news + fundamental intelligence, position exposure) reaches the LLM unchanged,
  with the pinned model, through a mock transport — no network, no credentials;
* provider/configuration failures are translated the way the existing adapter
  already does (401 -> RuntimeError, 429/5xx/timeout -> LLMFallbackError -> pool
  falls through -> safe RuntimeError -> the API's 503), and the key never appears
  in a prompt, response or error message.

Requires none of: real MT5, PostgreSQL, network or an external model. The real
AgentService/EconomicIntelligenceService/FundamentalIntelligenceService run over
the deterministic placeholder sources, exactly as in the other agent tests.
"""
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

import app.core.dependencies as deps
from app.core.config import settings as app_settings
from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.fake_economic_calendar import FakeEconomicCalendarProvider
from app.providers.fake_llm import FakeFreeLLMProvider, FakeLLMProvider
from app.providers.fake_news import FakeNewsProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider
from app.providers.llm_pool import LLMProviderPool
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeCloseReason, TradeHistoryEntry, TradeType
from app.services.account import AccountInfoService
from app.services.agent import AgentService
from app.services.economic_calendar import EconomicCalendarService
from app.services.economic_intelligence import EconomicIntelligenceService
from app.services.financial_context import FinancialContextService
from app.services.fundamental_intelligence import FundamentalIntelligenceService
from app.services.news import NewsService
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

# Test-only marker strings. Never a real credential, and distinctive enough that
# a leak into a prompt, response or error message is unmistakable.
OPENROUTER_KEY = "sk-or-test-unit-only-not-a-real-key"

ACCOUNT = AccountInfo(
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

XAUUSD = Position(
    ticket=123456789,
    symbol="XAUUSD",
    type=PositionType.BUY,
    volume=Decimal("0.10"),
    open_price=Decimal("3642.50"),
    current_price=Decimal("3648.20"),
    profit=Decimal("57.00"),
)

TRADE = TradeHistoryEntry(
    ticket=246802468,
    order_ticket=987654321,
    symbol="XAUUSD",
    type=TradeType.BUY,
    volume=Decimal("0.10"),
    price=Decimal("3648.20"),
    profit=Decimal("57.00"),
    time=datetime(2026, 9, 15, 12, 30, 0, tzinfo=UTC),
    close_reason=TradeCloseReason.TP,
    stop_loss=Decimal("3635.00"),
    take_profit=Decimal("3650.00"),
)

QUESTION = "What news and economic events are relevant to XAUUSD today?"


class _FakeAccountInfoProvider(AccountInfoProvider):
    def __init__(self, account: AccountInfo = ACCOUNT) -> None:
        self.account = account

    def get_account_info(self) -> AccountInfo:
        return self.account


@pytest.fixture(autouse=True)
def _no_deployment_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from a developer's local .env LLM endpoint."""
    monkeypatch.setattr(app_settings, "LLM_API_KEY", "", raising=True)
    monkeypatch.setattr(app_settings, "LLM_MODEL", "", raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", "", raising=True)


def _openrouter_provider(handler) -> OpenAICompatibleLLMProvider:
    """The OpenRouter adapter, wired exactly as the composition root wires it.

    The model and base URL come from settings (the pinned values), and HTTP goes
    to an offline mock transport, so nothing here can reach the network.
    """
    return OpenAICompatibleLLMProvider(
        api_key=OPENROUTER_KEY,
        base_url=app_settings.OPENROUTER_BASE_URL,
        model=app_settings.OPENROUTER_MODEL,
        transport=httpx.MockTransport(handler),
    )


def _grounded_agent(llm: LLMProvider) -> AgentService:
    """AgentService over the real services and the deterministic placeholder data."""
    return AgentService(
        FinancialContextService(
            account_service=AccountInfoService(_FakeAccountInfoProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
            trade_history_service=TradeHistoryService(FakeTradeHistoryProvider(trades=(TRADE,))),
        ),
        llm,
        economic_intelligence_service=EconomicIntelligenceService(
            calendar_service=EconomicCalendarService(FakeEconomicCalendarProvider()),
            position_service=PositionService(FakePositionProvider(positions=(XAUUSD,))),
        ),
        fundamental_intelligence_service=FundamentalIntelligenceService(
            news_service=NewsService(FakeNewsProvider(), max_items=20)
        ),
    )


def _recording_handler(captured: dict[str, object], *, status: int = 200, content: str = "answer"):
    """Offline HTTP handler that records the request and returns a canned reply."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "rejected"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


# --- free-pool wiring: development-only, pinned, replaceable --------------------------


def test_openrouter_is_pooled_in_development_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", OPENROUTER_KEY, raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    assert isinstance(pool, LLMProvider)
    assert pool.size == 1
    provider = pool._providers[0]
    # It is the SAME OpenAI-compatible adapter the deployable endpoint uses, so
    # the vendor stays replaceable: nothing OpenRouter-specific reaches the agent.
    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider._base_url == app_settings.OPENROUTER_BASE_URL
    assert provider._model == app_settings.OPENROUTER_MODEL


@pytest.mark.parametrize("environment", ["production", "staging", ""])
def test_openrouter_is_never_pooled_outside_development(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", environment, raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", OPENROUTER_KEY, raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    # A configured free-tier key must never put a broker's customers on it: the
    # pool stays empty and the request fails safely instead.
    assert pool.size == 0
    with pytest.raises(RuntimeError):
        pool.complete(LLMPrompt(instructions="be factual", content="hello"))


@pytest.mark.parametrize("key", ["", "   ", "YOUR_OPENROUTER_API_KEY"])
def test_an_unconfigured_or_placeholder_openrouter_key_is_left_out(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", key, raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    assert pool.size == 0


def test_the_deployment_endpoint_keeps_precedence_over_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "development", raising=True)
    monkeypatch.setattr(app_settings, "LLM_API_KEY", "deployment-test-key", raising=True)
    monkeypatch.setattr(app_settings, "LLM_MODEL", "deployment-test-model", raising=True)
    monkeypatch.setattr(app_settings, "LLM_BASE_URL", "https://deployment.test/v1", raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", OPENROUTER_KEY, raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    # The operator's own endpoint is the production seam; the free tier is the
    # development fallback behind it.
    assert pool.size == 2
    deployment, openrouter = pool._providers
    assert isinstance(deployment, OpenAICompatibleLLMProvider)
    assert isinstance(openrouter, OpenAICompatibleLLMProvider)
    assert deployment._base_url == "https://deployment.test/v1"
    assert openrouter._base_url == app_settings.OPENROUTER_BASE_URL


def test_outside_development_only_the_deployment_endpoint_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)
    monkeypatch.setattr(app_settings, "LLM_API_KEY", "deployment-test-key", raising=True)
    monkeypatch.setattr(app_settings, "LLM_MODEL", "deployment-test-model", raising=True)
    monkeypatch.setattr(app_settings, "OPENROUTER_API_KEY", OPENROUTER_KEY, raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    assert pool.size == 1
    provider = pool._providers[0]
    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert provider._base_url == app_settings.LLM_BASE_URL


# --- the grounded context reaches the LLM -----------------------------------------------


def test_the_grounded_context_reaches_the_openrouter_llm() -> None:
    captured: dict[str, object] = {}
    provider = _openrouter_provider(_recording_handler(captured, content="grounded answer"))

    response = _grounded_agent(provider).handle(QUESTION, broker_id=1)

    # The model's own text is returned unchanged.
    assert response.answer == "grounded answer"

    # The request went to the pinned OpenRouter model through its chat endpoint.
    assert captured["url"] == f"{app_settings.OPENROUTER_BASE_URL}/chat/completions"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == app_settings.OPENROUTER_MODEL

    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    prompt = messages[1]["content"]
    assert isinstance(prompt, str)

    # Every grounded block the project already builds is present, labelled as
    # source facts with its provenance — the model is given facts, not permission
    # to invent them.
    assert "Economic calendar for today" in prompt
    assert "fake-development-placeholder" in prompt
    assert "Fundamental intelligence for XAUUSD" in prompt
    assert "News (published source facts, not analysis" in prompt
    assert "Position fundamental exposure (factual status, not advice)" in prompt
    assert "XAUUSD" in prompt

    # The key travels in the header only: never in the prompt, and the request
    # itself never leaks it back.
    assert captured["authorization"] == f"Bearer {OPENROUTER_KEY}"
    assert OPENROUTER_KEY not in prompt


def test_the_agent_still_depends_only_on_the_provider_abstraction() -> None:
    # Replaceability guard: swapping OpenRouter for a paid provider (or a fake)
    # is a construction change, never an agent change.
    agent = _grounded_agent(FakeLLMProvider(response="fake"))

    response = agent.handle(QUESTION, broker_id=1)

    assert response.answer == "fake"


# --- provider / configuration failure handling ------------------------------------------


def test_a_rejected_credential_raises_a_generic_error_without_falling_back() -> None:
    captured: dict[str, object] = {}
    provider = _openrouter_provider(_recording_handler(captured, status=401))
    pool = LLMProviderPool([provider])

    # 401 is a configuration problem, not a transient outage: the pool surfaces
    # the adapter's generic RuntimeError instead of trying elsewhere.
    with pytest.raises(RuntimeError) as excinfo:
        pool.complete(LLMPrompt(instructions="be factual", content="hello"))

    assert not isinstance(excinfo.value, LLMFallbackError)
    # No credential, URL or upstream body is echoed into the error.
    assert OPENROUTER_KEY not in str(excinfo.value)
    assert app_settings.OPENROUTER_BASE_URL not in str(excinfo.value)


def test_a_transient_openrouter_failure_falls_through_to_the_next_provider() -> None:
    captured: dict[str, object] = {}
    openrouter = _openrouter_provider(_recording_handler(captured, status=429))
    fallback = FakeFreeLLMProvider("fallback", response="answer from the next provider")
    pool = LLMProviderPool([openrouter, fallback])

    answer = pool.complete(LLMPrompt(instructions="be factual", content="hello"))

    assert answer == "answer from the next provider"
    assert fallback.call_count == 1


def test_a_pool_with_only_a_failing_openrouter_provider_fails_safely() -> None:
    captured: dict[str, object] = {}
    openrouter = _openrouter_provider(_recording_handler(captured, status=503))
    pool = LLMProviderPool([openrouter])

    # The pool's single safe RuntimeError is what the API layer maps to its
    # generic 503; nothing provider-specific escapes it.
    with pytest.raises(RuntimeError) as excinfo:
        pool.complete(LLMPrompt(instructions="be factual", content="hello"))

    assert "no LLM provider is currently available" in str(excinfo.value)
    assert OPENROUTER_KEY not in str(excinfo.value)
