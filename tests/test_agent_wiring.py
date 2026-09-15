"""Tests for the agent composition-root wiring (Step 30, revised by Step 33).

Require none of: real MT5, PostgreSQL, network, credentials, or an external
LLM. The production LLM seam (deps.get_llm_provider) is now the broker-aware
LLMRouter: a broker with an active configuration uses its own provider, and a
broker without one uses the shared free pool. Both are exercised here directly
with monkeypatched settings and a patched broker resolver, and get_agent_service
is driven with the MT5 provider classes patched to inert fakes and the tenant
credential resolution stubbed (the established lifecycle-test pattern), so no
terminal and no HTTP call is ever involved.
"""
import asyncio

import pytest
from fastapi import HTTPException

import app.core.dependencies as deps
from app.core.config import settings
from app.core.mt5_session import MT5AccountCredentials
from app.db.models import User, UserRole
from app.providers.fake_llm import FakeLLMProvider
from app.providers.llm import LLMPrompt, LLMProvider
from app.providers.llm_pool import LLMProviderPool
from app.providers.llm_router import LLMRouter
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider
from app.services.agent import AgentService, OutboundDataPolicy
from app.services.broker_llm_config import BrokerLLMConfigurationError


# The "cipher" value is a test-only marker; it is never a real secret.
TENANT_CREDENTIALS = MT5AccountCredentials(login=10001, server="Wiring-Broker", password_encrypted="cipher")


@pytest.fixture()
def inert_mt5_providers(monkeypatch: pytest.MonkeyPatch):
    """Replace the MT5 provider classes with inert fakes and stub credentials.

    Providers are constructed with the authenticated tenant's credentials and
    the process-wide session manager; both are recorded so the wiring can be
    asserted without a terminal.
    """

    account_constructions: list[dict[str, object]] = []

    class InertAccountProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            account_constructions.append({"session_manager": session_manager, "credentials": credentials})

    class InertPositionsProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

    class InertTradeProvider:
        def __init__(self, session_manager: object = None, credentials: object = None) -> None:
            pass

    async def stub_credentials(user: User, session: object) -> MT5AccountCredentials:
        # Stands in for the DB-backed resolution: get_agent_service receives the
        # tenant identity from the caller, never from a request.
        return TENANT_CREDENTIALS

    monkeypatch.setattr(deps, "MT5AccountInfoProvider", InertAccountProvider)
    monkeypatch.setattr(deps, "MT5PositionProvider", InertPositionsProvider)
    monkeypatch.setattr(deps, "MT5TradeHistoryProvider", InertTradeProvider)
    monkeypatch.setattr(deps, "get_mt5_credentials", stub_credentials)
    return {"account_constructions": account_constructions}


def make_user(broker_id: int = 1) -> User:
    return User(
        broker_id=broker_id,
        login="wiring-user",
        password_hash="x-not-a-real-hash",
        is_active=True,
        role=UserRole.CUSTOMER,
    )


# --- get_free_llm_pool: the shared fallback pool -------------------------------------


def test_free_pool_is_empty_when_the_deployment_endpoint_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "", raising=True)
    monkeypatch.setattr(settings, "LLM_MODEL", "", raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    assert pool.size == 0
    # An empty pool fails safely rather than inventing an answer.
    with pytest.raises(RuntimeError):
        pool.complete(LLMPrompt(instructions="be factual", content="hello"))


def test_free_pool_holds_the_deployment_endpoint_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "wiring-test-key", raising=True)
    monkeypatch.setattr(settings, "LLM_MODEL", "wiring-test-model", raising=True)
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://wiring.test/v1", raising=True)

    pool = deps.get_free_llm_pool()

    assert isinstance(pool, LLMProviderPool)
    assert pool.size == 1


def test_placeholder_api_key_leaves_the_free_pool_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LLM_API_KEY", "YOUR_API_KEY", raising=True)
    monkeypatch.setattr(settings, "LLM_MODEL", "wiring-test-model", raising=True)

    assert deps.get_free_llm_pool().size == 0


# --- get_llm_provider: broker-aware router selection ---------------------------------


def test_no_active_broker_config_selects_the_free_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_broker_provider(*, session: object, broker_id: int) -> LLMProvider | None:
        return None

    free_provider = FakeLLMProvider(response="from free pool")
    monkeypatch.setattr(deps, "resolve_broker_llm_provider", no_broker_provider)
    monkeypatch.setattr(deps, "get_free_llm_pool", lambda: free_provider)

    provider = asyncio.run(deps.get_llm_provider(7, object()))

    assert isinstance(provider, LLMRouter)
    assert provider.uses_broker_provider is False
    assert provider.broker_id == 7


def test_active_broker_config_selects_the_broker_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_provider = FakeLLMProvider(response="from broker provider")

    async def with_broker_provider(*, session: object, broker_id: int) -> LLMProvider:
        return broker_provider

    free_provider = FakeLLMProvider(response="from free pool")
    monkeypatch.setattr(deps, "resolve_broker_llm_provider", with_broker_provider)
    monkeypatch.setattr(deps, "get_free_llm_pool", lambda: free_provider)

    provider = asyncio.run(deps.get_llm_provider(7, object()))

    assert isinstance(provider, LLMRouter)
    assert provider.uses_broker_provider is True


def test_broker_configuration_error_surfaces_as_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken_config(*, session: object, broker_id: int) -> LLMProvider | None:
        raise BrokerLLMConfigurationError("broker LLM credentials are unavailable")

    monkeypatch.setattr(deps, "resolve_broker_llm_provider", broken_config)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(deps.get_llm_provider(7, object()))

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Agent service temporarily unavailable"


# --- get_agent_service: DI path and failure translation ------------------------------


def test_broker_configuration_error_is_translated_before_any_mt5_work(
    monkeypatch: pytest.MonkeyPatch, inert_mt5_providers
) -> None:
    # The seam must run before provider construction: a broker configuration
    # failure costs no MT5 initialization attempt.
    async def broken_config(*, session: object, broker_id: int) -> LLMProvider | None:
        raise BrokerLLMConfigurationError("broken")

    monkeypatch.setattr(deps, "resolve_broker_llm_provider", broken_config)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(deps.get_agent_service(make_user(), object()))

    assert excinfo.value.status_code == 503
    assert inert_mt5_providers["account_constructions"] == []


def test_agent_service_is_built_with_the_router_over_the_free_pool(
    monkeypatch: pytest.MonkeyPatch, inert_mt5_providers
) -> None:
    async def no_broker_provider(*, session: object, broker_id: int) -> LLMProvider | None:
        return None

    free_provider = FakeLLMProvider()
    monkeypatch.setattr(deps, "resolve_broker_llm_provider", no_broker_provider)
    monkeypatch.setattr(deps, "get_free_llm_pool", lambda: free_provider)

    service = asyncio.run(deps.get_agent_service(make_user(), object()))

    assert isinstance(service, AgentService)
    # AgentService still depends on LLMProvider only; the router is one such
    # provider, so the agent holds no concrete vendor or broker knowledge.
    assert isinstance(service._llm, LLMRouter)
    assert isinstance(service._llm, LLMProvider)


def test_agent_service_reuses_the_single_mt5_composition_paths(
    monkeypatch: pytest.MonkeyPatch, inert_mt5_providers
) -> None:
    async def no_broker_provider(*, session: object, broker_id: int) -> LLMProvider | None:
        return None

    monkeypatch.setattr(deps, "resolve_broker_llm_provider", no_broker_provider)
    monkeypatch.setattr(deps, "get_free_llm_pool", lambda: FakeLLMProvider())

    first = asyncio.run(deps.get_agent_service(make_user(), object()))
    second = asyncio.run(deps.get_agent_service(make_user(), object()))

    # The financial services flow through the existing composition-root paths;
    # repeated construction builds new per-request providers (no parallel wiring)
    # around the ONE process-wide MT5 session and this tenant's credentials.
    constructions = inert_mt5_providers["account_constructions"]
    assert [c["credentials"] for c in constructions] == [TENANT_CREDENTIALS, TENANT_CREDENTIALS]
    assert constructions[0]["session_manager"] is constructions[1]["session_manager"]
    assert constructions[0]["session_manager"] is deps.get_mt5_session_manager()
    assert first is not second


# --- outbound LLM data policy --------------------------------------------------------


def test_outbound_data_policy_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LLM_SEND_ACCOUNT_BALANCES", False, raising=True)
    monkeypatch.setattr(settings, "LLM_SEND_POSITION_PRICING", False, raising=True)
    monkeypatch.setattr(settings, "LLM_SEND_TRADE_HISTORY", False, raising=True)

    policy = deps.get_outbound_data_policy()

    assert policy == OutboundDataPolicy(
        allow_account_balances=False,
        allow_position_pricing=False,
        allow_trade_history=False,
    )


def test_agent_service_is_built_with_the_configured_policy(
    monkeypatch: pytest.MonkeyPatch, inert_mt5_providers
) -> None:
    async def no_broker_provider(*, session: object, broker_id: int) -> LLMProvider | None:
        return None

    monkeypatch.setattr(deps, "resolve_broker_llm_provider", no_broker_provider)
    monkeypatch.setattr(deps, "get_free_llm_pool", lambda: FakeLLMProvider())
    monkeypatch.setattr(settings, "LLM_SEND_TRADE_HISTORY", False, raising=True)

    service = asyncio.run(deps.get_agent_service(make_user(), object()))

    assert isinstance(service._data_policy, OutboundDataPolicy)
    assert service._data_policy.allow_trade_history is False


def test_openai_adapter_is_a_provider_the_pool_accepts() -> None:
    # Guards the seam's construction contract: the deployment adapter is an
    # LLMProvider, so it can sit inside the pool without special-casing.
    adapter = OpenAICompatibleLLMProvider(
        api_key="wiring-test-key", base_url="https://wiring.test/v1", model="wiring-test-model"
    )
    assert isinstance(adapter, LLMProvider)
    assert isinstance(LLMProviderPool([adapter]), LLMProvider)
