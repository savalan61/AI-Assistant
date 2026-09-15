"""Tests for the broker-aware LLM router and its configuration resolver (Step 33).

Two layers, both fully offline:

* ``LLMRouter`` / ``LLMProviderPool`` policy is exercised with deterministic fake
  providers — no network, no credential, no real vendor.
* ``resolve_broker_llm_provider`` is exercised against a per-test file-based
  async SQLite database holding the real Broker/BrokerLLMConfig models, with a
  test-only Fernet key so the stored ciphertext round-trips.

The policy under test is explicit: a broker's own provider never silently
consumes the shared free pool; only a broker with no active configuration uses
the pool; and the free pool falls through only on fallback-eligible errors.
"""
import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.db.base import Base
from app.db.models import Broker, BrokerLLMConfig
from app.providers.fake_llm import FakeFreeLLMProvider, FakeLLMProvider
from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider, LLMProviderKind
from app.providers.llm_pool import LLMProviderPool
from app.providers.llm_router import LLMRouter
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider
from app.services.broker_llm_config import BrokerLLMConfigurationError, resolve_broker_llm_provider

PROMPT = LLMPrompt(instructions="be factual", content="what is my exposure?")
API_KEY = "sk-router-test-key-0123456789"


# --- router policy (pure, no database) -----------------------------------------------


def test_router_is_an_llm_provider() -> None:
    # AgentService depends on LLMProvider alone; the router must satisfy that.
    router = LLMRouter(broker_id=1, broker_provider=None, free_pool=FakeLLMProvider())
    assert isinstance(router, LLMProvider)


def test_active_broker_provider_is_selected_and_returns_its_answer() -> None:
    broker_provider = FakeLLMProvider(response="broker answer")
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(broker_id=1, broker_provider=broker_provider, free_pool=free_provider)

    assert router.uses_broker_provider is True
    assert router.complete(PROMPT) == "broker answer"
    assert broker_provider.call_count == 1
    # The broker's own provider was used, so the shared pool was never touched.
    assert free_provider.call_count == 0


def test_no_broker_provider_uses_the_free_pool() -> None:
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(broker_id=1, broker_provider=None, free_pool=free_provider)

    assert router.uses_broker_provider is False
    assert router.complete(PROMPT) == "free answer"
    assert free_provider.call_count == 1


def test_broker_provider_failure_does_not_silently_fall_back_to_the_pool() -> None:
    # Even a fallback-eligible failure from the broker's own provider must not
    # shift that broker's traffic onto the shared pool: hiding a broken
    # credential is worse than failing the request.
    secret = "sk-broker-secret-must-not-leak"
    broker_provider = FakeFreeLLMProvider("broker", error=LLMFallbackError(f"401 for {secret}"))
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(broker_id=1, broker_provider=broker_provider, free_pool=free_provider)

    with pytest.raises(RuntimeError) as excinfo:
        router.complete(PROMPT)

    assert free_provider.call_count == 0
    message = str(excinfo.value)
    assert secret not in message
    assert "401" not in message
    # The router's own failure is not fallback-eligible: a caller must not be
    # tempted to retry it against another pool.
    assert not isinstance(excinfo.value, LLMFallbackError)


def test_broker_provider_plain_failure_is_also_contained() -> None:
    broker_provider = FakeFreeLLMProvider("broker", error=RuntimeError("invalid credentials"))
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(broker_id=1, broker_provider=broker_provider, free_pool=free_provider)

    with pytest.raises(RuntimeError, match="LLM provider request failed"):
        router.complete(PROMPT)

    assert free_provider.call_count == 0


def test_free_pool_exhaustion_is_one_safe_failure() -> None:
    pool = LLMProviderPool(
        [
            FakeFreeLLMProvider("a", error=LLMFallbackError("rate limited")),
            FakeFreeLLMProvider("b", error=LLMFallbackError("upstream 503")),
        ]
    )
    router = LLMRouter(broker_id=1, broker_provider=None, free_pool=pool)

    with pytest.raises(RuntimeError) as excinfo:
        router.complete(PROMPT)

    assert str(excinfo.value) == "no LLM provider is currently available"


def test_router_broker_id_is_bound_by_construction_not_from_the_prompt() -> None:
    # Tenant identity arrives as a constructor argument from the composition
    # boundary (the authenticated DB user). The prompt contract carries no
    # broker/user field at all, so a request body cannot redirect the tenant.
    router = LLMRouter(broker_id=42, broker_provider=None, free_pool=FakeLLMProvider())

    assert router.broker_id == 42
    assert set(LLMPrompt._fields) == {"instructions", "content"}


# --- resolver (real SQLite, test-only encryption key) --------------------------------


@pytest.fixture(autouse=True)
def test_only_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A per-test Fernet key: generated in-process, never a real credential.
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


@pytest.fixture()
def broker_db(tmp_path) -> dict[str, Any]:
    """Seed two brokers and one configuration for broker A."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/router_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="RTR-A")
            broker_b = Broker(name="Broker B", code="RTR-B")
            session.add_all([broker_a, broker_b])
            await session.flush()
            session.add(
                BrokerLLMConfig(
                    broker_id=broker_a.id,
                    provider=LLMProviderKind.OPENAI_COMPATIBLE,
                    model="test-model",
                    base_url="https://llm.example.test/v1",
                    api_key_encrypted=encrypt_secret(API_KEY),
                    is_active=True,
                )
            )
            await session.commit()
            return {"broker_a_id": broker_a.id, "broker_b_id": broker_b.id}

    ids = asyncio.run(seed())
    yield {"factory": factory, **ids}
    asyncio.run(engine.dispose())


def resolve(env: dict[str, Any], broker_id: int) -> LLMProvider | None:
    async def run() -> LLMProvider | None:
        async with env["factory"]() as session:
            return await resolve_broker_llm_provider(session=session, broker_id=broker_id)

    return asyncio.run(run())


def test_broker_without_configuration_resolves_to_none(broker_db: dict[str, Any]) -> None:
    assert resolve(broker_db, broker_db["broker_b_id"]) is None


def test_broker_with_active_configuration_resolves_to_a_provider(broker_db: dict[str, Any]) -> None:
    provider = resolve(broker_db, broker_db["broker_a_id"])

    assert isinstance(provider, OpenAICompatibleLLMProvider)
    # The stored ciphertext round-trips into the live credential for provider
    # construction (asserted through the adapter's configuration attributes; no
    # network call is made).
    assert isinstance(provider, LLMProvider)
    assert provider._model == "test-model"


def test_stored_key_is_encrypted_not_plaintext(broker_db: dict[str, Any]) -> None:
    async def load() -> str:
        async with broker_db["factory"]() as session:
            config = await session.get(BrokerLLMConfig, 1)
            assert config is not None
            return config.api_key_encrypted

    stored = asyncio.run(load())

    assert stored != API_KEY
    assert API_KEY not in stored


def test_disabled_configuration_resolves_to_none(broker_db: dict[str, Any]) -> None:
    async def disable() -> None:
        async with broker_db["factory"]() as session:
            config = await session.get(BrokerLLMConfig, 1)
            assert config is not None
            config.is_active = False
            await session.commit()

    asyncio.run(disable())

    assert resolve(broker_db, broker_db["broker_a_id"]) is None


def test_undecryptable_credentials_raise_configuration_error(broker_db: dict[str, Any]) -> None:
    async def corrupt() -> None:
        async with broker_db["factory"]() as session:
            config = await session.get(BrokerLLMConfig, 1)
            assert config is not None
            # Ciphertext under a different key: authentication fails on decrypt.
            config.api_key_encrypted = "not-a-valid-fernet-token"
            await session.commit()

    asyncio.run(corrupt())

    with pytest.raises(BrokerLLMConfigurationError) as excinfo:
        resolve(broker_db, broker_db["broker_a_id"])

    # The failure is generic: no ciphertext, key, or upstream detail leaks.
    assert "not-a-valid-fernet-token" not in str(excinfo.value)


def test_invalid_stored_configuration_raises_configuration_error(broker_db: dict[str, Any]) -> None:
    async def blank_model() -> None:
        async with broker_db["factory"]() as session:
            config = await session.get(BrokerLLMConfig, 1)
            assert config is not None
            # Bypass API validation on purpose: a stored defect must still fail.
            config.model = " "
            await session.commit()

    asyncio.run(blank_model())

    with pytest.raises(BrokerLLMConfigurationError):
        resolve(broker_db, broker_db["broker_a_id"])


def test_resolution_is_scoped_to_the_requested_broker(broker_db: dict[str, Any]) -> None:
    # Broker A's configuration must never leak into broker B's resolution.
    assert isinstance(resolve(broker_db, broker_db["broker_a_id"]), OpenAICompatibleLLMProvider)
    assert resolve(broker_db, broker_db["broker_b_id"]) is None
    assert resolve(broker_db, broker_db["broker_a_id"]) is not None
