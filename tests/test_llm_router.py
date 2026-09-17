"""Tests for the deployment LLM router and its configuration resolver (Step 33).

This deployment serves ONE broker, so there is exactly one stored LLM
configuration and no broker argument anywhere: a request cannot select, or even
influence, which credential is used. Two layers, both fully offline:

* ``LLMRouter`` / ``LLMProviderPool`` policy is exercised with deterministic fake
  providers — no network, no credential, no real vendor.
* ``resolve_llm_provider`` is exercised against a per-test file-based async
  SQLite database holding the real Broker/BrokerLLMConfig models, with a
  test-only Fernet key so the stored ciphertext round-trips.

The policy under test is explicit: the configured provider never silently
consumes the shared free pool; only a broker with no active configuration uses
the pool; and the free pool falls through only on fallback-eligible errors.
"""
import asyncio
import inspect
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
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
from app.services.broker_llm_config import BrokerLLMConfigurationError, resolve_llm_provider

PROMPT = LLMPrompt(instructions="be factual", content="what is my exposure?")
API_KEY = "sk-router-test-key-0123456789"


# --- router policy (pure, no database) -----------------------------------------------


def test_router_is_an_llm_provider() -> None:
    # AgentService depends on LLMProvider alone; the router must satisfy that.
    router = LLMRouter(configured_provider=None, free_pool=FakeLLMProvider())
    assert isinstance(router, LLMProvider)


def test_router_takes_no_tenant_selector() -> None:
    # One broker: there is nothing to select, and the constructor proves it —
    # no broker_id, broker code, user or request object can arrive here.
    parameters = set(inspect.signature(LLMRouter.__init__).parameters)
    assert parameters == {"self", "configured_provider", "free_pool"}


def test_configured_provider_is_selected_and_returns_its_answer() -> None:
    configured_provider = FakeLLMProvider(response="configured answer")
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(configured_provider=configured_provider, free_pool=free_provider)

    assert router.uses_configured_provider is True
    assert router.complete(PROMPT) == "configured answer"
    assert configured_provider.call_count == 1
    # The configured provider was used, so the shared pool was never touched.
    assert free_provider.call_count == 0


def test_no_configured_provider_uses_the_free_pool() -> None:
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(configured_provider=None, free_pool=free_provider)

    assert router.uses_configured_provider is False
    assert router.complete(PROMPT) == "free answer"
    assert free_provider.call_count == 1


def test_configured_provider_failure_does_not_silently_fall_back_to_the_pool() -> None:
    # Even a fallback-eligible failure from the configured provider must not
    # shift the broker's traffic onto the shared pool: hiding a broken
    # credential is worse than failing the request.
    secret = "sk-broker-secret-must-not-leak"
    configured_provider = FakeFreeLLMProvider("broker", error=LLMFallbackError(f"401 for {secret}"))
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(configured_provider=configured_provider, free_pool=free_provider)

    with pytest.raises(RuntimeError) as excinfo:
        router.complete(PROMPT)

    assert free_provider.call_count == 0
    message = str(excinfo.value)
    assert secret not in message
    assert "401" not in message
    # The router's own failure is not fallback-eligible: a caller must not be
    # tempted to retry it against another pool.
    assert not isinstance(excinfo.value, LLMFallbackError)


def test_configured_provider_plain_failure_is_also_contained() -> None:
    configured_provider = FakeFreeLLMProvider("broker", error=RuntimeError("invalid credentials"))
    free_provider = FakeLLMProvider(response="free answer")

    router = LLMRouter(configured_provider=configured_provider, free_pool=free_provider)

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
    router = LLMRouter(configured_provider=None, free_pool=pool)

    with pytest.raises(RuntimeError) as excinfo:
        router.complete(PROMPT)

    assert str(excinfo.value) == "no LLM provider is currently available"


def test_the_prompt_contract_carries_no_tenant_field() -> None:
    # Which credential is used is decided at the composition boundary from the
    # single broker row. The prompt carries no broker/user field at all, so a
    # request body cannot redirect it.
    assert set(LLMPrompt._fields) == {"instructions", "content"}


# --- resolver (real SQLite, test-only encryption key) --------------------------------


@pytest.fixture(autouse=True)
def test_only_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # A per-test Fernet key: generated in-process, never a real credential.
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


@pytest.fixture()
def broker_db(tmp_path) -> dict[str, Any]:
    """Seed the deployment's ONE broker (no configuration row yet)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/router_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="RTR-ONE", mt5_server="TheBroker-Live")
            session.add(broker)
            await session.commit()
            return broker.id

    broker_id = asyncio.run(seed())
    yield {"factory": factory, "broker_id": broker_id}
    asyncio.run(engine.dispose())


async def _store_config(env: dict[str, Any], **overrides: Any) -> None:
    """Write the deployment's one LLM configuration row."""
    async with env["factory"]() as session:
        fields: dict[str, Any] = {
            "broker_id": env["broker_id"],
            "provider": LLMProviderKind.OPENAI_COMPATIBLE,
            "model": "test-model",
            "base_url": "https://llm.example.test/v1",
            "api_key_encrypted": encrypt_secret(API_KEY),
            "is_active": True,
        }
        fields.update(overrides)
        session.add(BrokerLLMConfig(**fields))
        await session.commit()


def resolve(env: dict[str, Any]) -> LLMProvider | None:
    async def run() -> LLMProvider | None:
        async with env["factory"]() as session:
            return await resolve_llm_provider(session=session)

    return asyncio.run(run())


def test_no_configuration_resolves_to_none(broker_db: dict[str, Any]) -> None:
    # A deliberate state, not an error: the shared free pool then applies.
    assert resolve(broker_db) is None


def test_active_configuration_resolves_to_a_provider(broker_db: dict[str, Any]) -> None:
    asyncio.run(_store_config(broker_db))

    provider = resolve(broker_db)

    assert isinstance(provider, OpenAICompatibleLLMProvider)
    assert isinstance(provider, LLMProvider)
    assert provider._model == "test-model"


def test_stored_key_is_encrypted_not_plaintext(broker_db: dict[str, Any]) -> None:
    asyncio.run(_store_config(broker_db))

    async def load() -> str:
        async with broker_db["factory"]() as session:
            config = (await session.execute(_select_config())).scalar_one()
            return config.api_key_encrypted

    stored = asyncio.run(load())

    assert stored != API_KEY
    assert API_KEY not in stored


def _select_config():
    from sqlalchemy import select

    return select(BrokerLLMConfig)


async def _update_config(env: dict[str, Any], **overrides: Any) -> None:
    async with env["factory"]() as session:
        config = (await session.execute(_select_config())).scalar_one()
        for name, value in overrides.items():
            setattr(config, name, value)
        await session.commit()


def test_disabled_configuration_resolves_to_none(broker_db: dict[str, Any]) -> None:
    asyncio.run(_store_config(broker_db))
    asyncio.run(_update_config(broker_db, is_active=False))

    assert resolve(broker_db) is None


def test_undecryptable_credentials_raise_configuration_error(broker_db: dict[str, Any]) -> None:
    asyncio.run(_store_config(broker_db))
    # Ciphertext under a different key: authentication fails on decrypt.
    asyncio.run(_update_config(broker_db, api_key_encrypted="not-a-valid-fernet-token"))

    with pytest.raises(BrokerLLMConfigurationError) as excinfo:
        resolve(broker_db)

    # The failure is generic: no ciphertext, key, or upstream detail leaks.
    assert "not-a-valid-fernet-token" not in str(excinfo.value)


def test_invalid_stored_configuration_raises_configuration_error(broker_db: dict[str, Any]) -> None:
    asyncio.run(_store_config(broker_db))
    # Bypass API validation on purpose: a stored defect must still fail.
    asyncio.run(_update_config(broker_db, model=" "))

    with pytest.raises(BrokerLLMConfigurationError):
        resolve(broker_db)


def test_a_second_configuration_cannot_exist(broker_db: dict[str, Any]) -> None:
    # One broker -> one configuration, enforced by the database, so resolution
    # can never pick a "wrong" row: the second insert is refused outright.
    asyncio.run(_store_config(broker_db))

    with pytest.raises(IntegrityError):
        asyncio.run(_store_config(broker_db, model="second-model"))


@pytest.mark.parametrize("base_url", ["https://127.0.0.1/v1", "https://169.254.169.254/v1", "https://10.0.0.5/v1"])
def test_resolution_refuses_a_stored_endpoint_that_is_not_permitted(
    broker_db: dict[str, Any], base_url: str
) -> None:
    # Re-checked immediately before an outbound request, so a row that bypassed
    # the write-time policy still cannot make the server call its own network.
    asyncio.run(_store_config(broker_db, base_url=base_url))

    with pytest.raises(BrokerLLMConfigurationError):
        resolve(broker_db)


def test_resolution_refuses_a_plaintext_endpoint_outside_development(
    broker_db: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)
    asyncio.run(_store_config(broker_db, base_url="http://llm.example.test/v1"))

    with pytest.raises(BrokerLLMConfigurationError):
        resolve(broker_db)
