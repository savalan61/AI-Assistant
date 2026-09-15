"""Tests for the broker LLM configuration API (Step 32).

Require none of: real PostgreSQL, real MT5, network, real credentials, or a real
LLM. get_db is overridden with a per-test file-based async SQLite database
holding the real Broker/User/BrokerLLMConfig models; the real authentication and
super_admin authorization dependencies run (real JWT decode + database role
check). The LLM provider seam is patched with a deterministic offline double, so
no test performs a network call. JWT and encryption config use test-only values.
"""
import asyncio
import logging
import socket
import threading
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.broker_llm_config.broker_llm_config_service as tester_module
from app.api.broker_llm_config_router import router
from app.core.config import settings as app_settings
from app.core.encryption import decrypt_secret, encrypt_secret, generate_encryption_key
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, BrokerLLMConfig, User, UserRole
from app.providers.llm import LLMProviderKind

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
API_KEY = "sk-broker-test-key-0123456789"
SECOND_API_KEY = "sk-broker-second-key-9876543210"
CONFIG_PATH = "/broker/llm-config"

ALLOWED_FIELDS = {"provider", "model", "base_url", "is_active", "api_key_set", "updated_at"}

VALID_BODY: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "test-model",
    "base_url": "https://llm.example.test/v1",
    "api_key": API_KEY,
}


# A public, documentation-range address the fake resolver returns for every
# configured endpoint host, so the SSRF validator's address checks pass without
# any real DNS traffic in the suite.
PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def test_only_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)
    # Test-only encryption key: generated in-process, never a real credential.
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


@pytest.fixture(autouse=True)
def offline_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve configured endpoint hostnames to a public address, fully offline.

    The endpoint validator resolves every host on write (that is the
    DNS-based SSRF defence), so these API tests must not depend on real DNS.
    """

    def fake_getaddrinfo(host: str, port: object, *args: object, **kwargs: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_ADDRESS, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture()
def broker_db(tmp_path) -> dict[str, Any]:
    """Seed two tenants: broker A (super/admin/customer) and broker B (super).

    Broker A's rows exist so the API's tenant boundary can be attacked: every
    test asserts that broker B can neither read nor modify A's configuration.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/broker_llm_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="LLM-A")
            broker_b = Broker(name="Broker B", code="LLM-B")
            session.add_all([broker_a, broker_b])
            await session.flush()

            def make(broker: Broker, username: str, role: UserRole) -> User:
                return User(
                    broker_id=broker.id,
                    username=username,
                    password_hash="x-not-a-real-hash",
                    is_active=True,
                    role=role,
                )

            super_a = make(broker_a, "super-a", UserRole.SUPER_ADMIN)
            admin_a = make(broker_a, "admin-a", UserRole.ADMIN)
            cust_a = make(broker_a, "10001", UserRole.CUSTOMER)
            super_b = make(broker_b, "super-b", UserRole.SUPER_ADMIN)
            session.add_all([super_a, admin_a, cust_a, super_b])
            await session.commit()
            return {
                "broker_a_id": broker_a.id,
                "broker_b_id": broker_b.id,
                "super_a_id": super_a.id,
                "admin_a_id": admin_a.id,
                "cust_a_id": cust_a.id,
                "super_b_id": super_b.id,
            }

    ids = asyncio.run(seed())
    yield {"factory": factory, **ids}
    asyncio.run(engine.dispose())


# --- helpers -------------------------------------------------------------------------


def make_client(factory: async_sessionmaker[AsyncSession]) -> TestClient:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(user_id: int) -> str:
    return create_access_token(str(user_id))


def put_config(env: dict[str, Any], user_id: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    with make_client(env["factory"]) as client:
        response = client.put(CONFIG_PATH, json=payload, headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


def get_config(env: dict[str, Any], user_id: int, query: str = "") -> tuple[int, dict[str, Any]]:
    with make_client(env["factory"]) as client:
        response = client.get(f"{CONFIG_PATH}{query}", headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


def post_connection_test(env: dict[str, Any], user_id: int) -> tuple[int, dict[str, Any]]:
    with make_client(env["factory"]) as client:
        response = client.post(f"{CONFIG_PATH}/test", headers=auth_header(token_for(user_id)))
    return response.status_code, response.json()


def install_fake_provider(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error: Exception | None = None,
    answer: str = "OK",
) -> "tuple[list[tuple[str, str, str]], list[int]]":
    """Patch the provider seam used by the connection tester.

    Records the credentials the tester passed down and the thread the blocking
    call ran on. Never performs network I/O.
    """
    calls: list[tuple[str, str, str]] = []
    threads: list[int] = []

    class FakeOpenAICompatibleLLMProvider:
        def __init__(
            self,
            api_key: str,
            base_url: str,
            model: str,
            timeout_seconds: float = 30.0,
            transport: object = None,
        ) -> None:
            calls.append((api_key, base_url, model))

        def complete(self, prompt: object) -> str:
            threads.append(threading.get_ident())
            if error is not None:
                raise error
            return answer

    monkeypatch.setattr(tester_module, "OpenAICompatibleLLMProvider", FakeOpenAICompatibleLLMProvider)
    return calls, threads


async def _read_ciphertext(factory: async_sessionmaker[AsyncSession], broker_id: int) -> str | None:
    async with factory() as session:
        result = await session.execute(
            select(BrokerLLMConfig.api_key_encrypted).where(BrokerLLMConfig.broker_id == broker_id)
        )
        return result.scalar_one_or_none()


async def _count_configs(factory: async_sessionmaker[AsyncSession], broker_id: int) -> int:
    async with factory() as session:
        result = await session.execute(
            select(func.count()).select_from(BrokerLLMConfig).where(BrokerLLMConfig.broker_id == broker_id)
        )
        return int(result.scalar_one())


def count_configs(env: dict[str, Any], broker_id: int) -> int:
    return asyncio.run(_count_configs(env["factory"], broker_id))


def stored_ciphertext(env: dict[str, Any], broker_id: int) -> str:
    value = asyncio.run(_read_ciphertext(env["factory"], broker_id))
    assert value is not None, "configuration row must exist"
    return value


# --- authentication / authorization ---------------------------------------------------


def test_unauthenticated_requests_return_401(broker_db) -> None:
    with make_client(broker_db["factory"]) as client:
        get_response = client.get(CONFIG_PATH)
        put_response = client.put(CONFIG_PATH, json=VALID_BODY)
        test_response = client.post(f"{CONFIG_PATH}/test")

    assert (get_response.status_code, put_response.status_code, test_response.status_code) == (401, 401, 401)
    assert get_response.headers["www-authenticate"] == "Bearer"


def test_admin_receives_403_on_every_endpoint(broker_db) -> None:
    admin_id = broker_db["admin_a_id"]

    put_status, _ = put_config(broker_db, admin_id, VALID_BODY)
    get_status, _ = get_config(broker_db, admin_id)
    test_status, _ = post_connection_test(broker_db, admin_id)

    assert (put_status, get_status, test_status) == (403, 403, 403)


def test_customer_receives_403_on_every_endpoint(broker_db) -> None:
    customer_id = broker_db["cust_a_id"]

    put_status, _ = put_config(broker_db, customer_id, VALID_BODY)
    get_status, _ = get_config(broker_db, customer_id)
    test_status, _ = post_connection_test(broker_db, customer_id)

    assert (put_status, get_status, test_status) == (403, 403, 403)


# --- create / read --------------------------------------------------------------------


def test_super_admin_creates_and_reads_own_configuration(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]

    put_status, put_body = put_config(broker_db, super_a_id, VALID_BODY)
    get_status, get_body = get_config(broker_db, super_a_id)

    assert put_status == 200
    assert get_status == 200
    assert set(put_body.keys()) == ALLOWED_FIELDS
    for body in (put_body, get_body):
        assert body["provider"] == "openai_compatible"
        assert body["model"] == "test-model"
        assert body["base_url"] == "https://llm.example.test/v1"
        assert body["is_active"] is True
        assert body["api_key_set"] is True
        assert body["updated_at"]
    assert get_body == put_body
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 1


def test_get_without_configuration_returns_404(broker_db) -> None:
    status, body = get_config(broker_db, broker_db["super_a_id"])

    assert status == 404
    assert body == {"detail": "LLM configuration is not set for this broker"}


def test_super_admin_updates_the_existing_configuration(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    put_config(broker_db, super_a_id, VALID_BODY)

    update = {
        **VALID_BODY,
        "model": "second-model",
        "base_url": "https://other.example.test/v1",
        "api_key": SECOND_API_KEY,
        "is_active": False,
    }
    status, body = put_config(broker_db, super_a_id, update)

    assert status == 200
    assert body["model"] == "second-model"
    assert body["base_url"] == "https://other.example.test/v1"
    assert body["is_active"] is False
    # Still exactly one row for the broker: PUT is an upsert, not an insert.
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 1
    # The stored credential was replaced, not appended.
    assert decrypt_secret(stored_ciphertext(broker_db, broker_db["broker_a_id"])) == SECOND_API_KEY


def test_update_without_is_active_keeps_the_current_state(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    put_config(broker_db, super_a_id, {**VALID_BODY, "is_active": False})

    status, body = put_config(broker_db, super_a_id, VALID_BODY)

    assert status == 200
    assert body["is_active"] is False


def test_disabled_configuration_still_reports_safe_metadata(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    put_config(broker_db, super_a_id, {**VALID_BODY, "is_active": False})

    status, body = get_config(broker_db, super_a_id)

    assert status == 200
    assert body["is_active"] is False
    assert body["api_key_set"] is True


# --- validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {k: v for k, v in VALID_BODY.items() if k != "model"},
        {k: v for k, v in VALID_BODY.items() if k != "api_key"},
        {**VALID_BODY, "model": ""},
        {**VALID_BODY, "api_key": "short"},
        {**VALID_BODY, "api_key": "        "},
        {**VALID_BODY, "base_url": "not-a-url"},
        {**VALID_BODY, "base_url": "ftp://example.test/key"},
        {**VALID_BODY, "provider": "some_other_provider"},
    ],
)
def test_invalid_configuration_is_rejected_with_422(broker_db, payload: dict[str, Any]) -> None:
    status, _ = put_config(broker_db, broker_db["super_a_id"], payload)

    assert status == 422
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 0


# --- secret handling --------------------------------------------------------------------


def test_api_key_is_never_returned(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    _, put_body = put_config(broker_db, super_a_id, VALID_BODY)
    _, get_body = get_config(broker_db, super_a_id)

    for body in (put_body, get_body):
        text = str(body)
        assert API_KEY not in text
        assert SECOND_API_KEY not in text
        assert "sk-" not in text
        assert "api_key" not in body
        assert "api_key_encrypted" not in text
        assert "encrypted" not in text


def test_api_key_is_stored_encrypted_not_plaintext(broker_db) -> None:
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    stored = stored_ciphertext(broker_db, broker_db["broker_a_id"])

    assert stored != API_KEY
    assert API_KEY not in stored
    # Fernet ciphertext, and it round-trips to the real credential with the
    # configured key (proving encryption, not some lossy transform).
    assert stored.startswith("gAAAAA")
    assert decrypt_secret(stored) == API_KEY


def test_missing_encryption_key_fails_closed_without_storing(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", "", raising=True)

    status, body = put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    assert status == 503
    assert body == {"detail": "LLM credential storage is temporarily unavailable"}
    # Fail closed: nothing recoverable was written.
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 0
    assert API_KEY not in str(body)


def test_api_key_is_never_logged(broker_db, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    install_fake_provider(monkeypatch, error=RuntimeError(f"boom while authenticating {API_KEY}"))

    with caplog.at_level(logging.DEBUG):
        put_config(broker_db, broker_db["super_a_id"], VALID_BODY)
        post_connection_test(broker_db, broker_db["super_a_id"])

    assert API_KEY not in caplog.text


# --- tenant isolation -------------------------------------------------------------------


def test_another_broker_cannot_read_or_modify_the_configuration(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    super_b_id = broker_db["super_b_id"]
    put_config(broker_db, super_a_id, VALID_BODY)

    # Broker B has no configuration of its own ...
    assert get_config(broker_db, super_b_id)[0] == 404

    # ... and creating one leaves broker A's row untouched.
    status, body = put_config(
        broker_db,
        super_b_id,
        {**VALID_BODY, "model": "broker-b-model", "base_url": "https://b.example.test/v1"},
    )

    assert status == 200
    assert body["model"] == "broker-b-model"
    a_status, a_body = get_config(broker_db, super_a_id)
    assert a_status == 200
    assert a_body["model"] == "test-model"
    assert a_body["base_url"] == "https://llm.example.test/v1"
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 1
    assert count_configs(broker_db, broker_db["broker_b_id"]) == 1


def test_another_broker_can_never_see_the_other_tenants_endpoint_or_metadata(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    super_b_id = broker_db["super_b_id"]
    put_config(broker_db, super_a_id, VALID_BODY)

    status, body = get_config(broker_db, super_b_id)

    assert status == 404
    assert "llm.example.test" not in str(body)


def test_request_cannot_choose_another_broker(broker_db) -> None:
    super_a_id = broker_db["super_a_id"]
    super_b_id = broker_db["super_b_id"]
    put_config(broker_db, super_b_id, {**VALID_BODY, "model": "broker-b-model"})

    # A broker_id in the body is structurally impossible (extra="forbid").
    body_status, _ = put_config(broker_db, super_a_id, {**VALID_BODY, "broker_id": 2})
    assert body_status == 422

    # A broker_id query parameter does not exist and is ignored: the response
    # stays scoped to the authenticated broker (which has no configuration).
    query_status, query_body = get_config(broker_db, super_a_id, query="?broker_id=2")
    assert query_status == 404
    assert query_body == {"detail": "LLM configuration is not set for this broker"}

    # Path-based selection does not exist either.
    with make_client(broker_db["factory"]) as client:
        path_response = client.get(f"{CONFIG_PATH}/2", headers=auth_header(token_for(super_a_id)))
    assert path_response.status_code == 404

    # And nothing was written for the attacker's broker.
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 0


def test_one_configuration_per_broker_is_enforced_by_the_database(broker_db) -> None:
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    async def insert_duplicate() -> None:
        async with broker_db["factory"]() as session:
            session.add(
                BrokerLLMConfig(
                    broker_id=broker_db["broker_a_id"],
                    provider=LLMProviderKind.OPENAI_COMPATIBLE,
                    model="duplicate",
                    base_url="https://duplicate.example.test/v1",
                    api_key_encrypted="ciphertext",
                    is_active=True,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(insert_duplicate())

    assert count_configs(broker_db, broker_db["broker_a_id"]) == 1


# --- connection test ----------------------------------------------------------------------


def test_connection_test_success_uses_the_stored_credentials(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, threads = install_fake_provider(monkeypatch)
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 200
    assert body == {"status": "OK", "provider": "openai_compatible", "model": "test-model", "detail": None}
    # The provider received the DECRYPTED credential plus the stored endpoint
    # and model, and the blocking call ran off the event loop.
    assert calls == [(API_KEY, "https://llm.example.test/v1", "test-model")]
    assert threads and threads[0] != threading.get_ident()
    assert API_KEY not in str(body)


def test_connection_test_without_configuration_returns_404(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_provider(monkeypatch)

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 404
    assert body == {"detail": "LLM configuration is not set for this broker"}


def test_connection_test_on_disabled_configuration_returns_409(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_provider(monkeypatch)
    put_config(broker_db, broker_db["super_a_id"], {**VALID_BODY, "is_active": False})

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 409
    assert body == {"detail": "LLM configuration is disabled"}


def test_connection_test_failure_returns_safe_result(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider error deliberately embeds the key and endpoint: none of it
    # may reach the client.
    install_fake_provider(
        monkeypatch,
        error=RuntimeError(f"401 unauthorized for key {API_KEY} at https://llm.example.test/v1"),
    )
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 200
    assert body == {
        "status": "FAILED",
        "provider": "openai_compatible",
        "model": "test-model",
        "detail": "The configured LLM provider could not be reached or rejected the credentials",
    }
    text = str(body)
    assert API_KEY not in text
    assert "401" not in text
    assert "unauthorized" not in text
    assert "llm.example.test" not in text


def test_connection_test_with_undecryptable_credential_fails_closed(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake_provider(monkeypatch)
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)
    # Simulate key rotation: the stored ciphertext can no longer be opened, so
    # the test fails closed (503) instead of sending garbage to the provider.
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 503
    assert body == {"detail": "LLM credential storage is temporarily unavailable"}


def test_connection_test_rejects_other_tenants_credentials(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _ = install_fake_provider(monkeypatch)
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    # Broker B has no configuration, so its test can never touch broker A's.
    status, _ = post_connection_test(broker_db, broker_db["super_b_id"])

    assert status == 404
    assert calls == []


# --- outbound endpoint security (SSRF) ---------------------------------------------------

# Every one of these would make the server issue a request into its own network
# or send the broker's key in the clear.
BLOCKED_BASE_URLS = (
    "https://localhost/v1",
    "https://ip6-localhost/v1",
    "https://127.0.0.1/v1",
    "https://[::1]/v1",
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://10.0.0.5/v1",
    "https://192.168.1.10/v1",
    "https://172.16.0.9/v1",
    "https://user:secret@llm.example.test/v1",
    "http://llm.example.test/v1",  # plaintext rejected outside development
)


def raw_insert_config(env: dict[str, Any], broker_id: int, base_url: str) -> None:
    """Write a configuration row directly, bypassing API validation.

    Simulates a row edited outside the API (or written before the endpoint
    policy existed), so the call-time re-validation can be exercised.
    """

    async def insert() -> None:
        async with env["factory"]() as session:
            session.add(
                BrokerLLMConfig(
                    broker_id=broker_id,
                    provider=LLMProviderKind.OPENAI_COMPATIBLE,
                    model="test-model",
                    base_url=base_url,
                    api_key_encrypted=encrypt_secret(API_KEY),
                    is_active=True,
                )
            )
            await session.commit()

    asyncio.run(insert())


@pytest.mark.parametrize("base_url", BLOCKED_BASE_URLS)
def test_blocked_endpoint_is_rejected_and_nothing_is_stored(
    broker_db, base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production posture: the plaintext-http case must be refused as well.
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    status, body = put_config(broker_db, broker_db["super_a_id"], {**VALID_BODY, "base_url": base_url})

    assert status == 422
    assert "not an allowed outbound endpoint" in body["detail"]
    # Rejected before anything was written.
    assert get_config(broker_db, broker_db["super_a_id"])[0] == 404


def test_blocked_endpoint_error_never_echoes_embedded_credentials(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "APP_ENV", "production", raising=True)

    _, body = put_config(
        broker_db, broker_db["super_a_id"], {**VALID_BODY, "base_url": "https://user:topsecret@llm.example.test/v1"}
    )

    assert "topsecret" not in str(body)


def test_http_endpoint_is_accepted_in_development(broker_db) -> None:
    # APP_ENV is development in the test settings, where a self-hosted runtime
    # over plain http is a legitimate convenience.
    status, body = put_config(broker_db, broker_db["super_a_id"], {**VALID_BODY, "base_url": "http://llm.example.test/v1"})

    assert status == 200
    assert body["base_url"] == "http://llm.example.test/v1"


def test_hostname_resolving_to_a_private_address_is_rejected(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Overrides the autouse public-DNS stub: this host resolves internally.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
    )

    status, body = put_config(
        broker_db, broker_db["super_a_id"], {**VALID_BODY, "base_url": "https://internal.example.test/v1"}
    )

    assert status == 422
    assert "private" in body["detail"]
    assert get_config(broker_db, broker_db["super_a_id"])[0] == 404


def test_unresolvable_host_is_rejected(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    status, body = put_config(
        broker_db, broker_db["super_a_id"], {**VALID_BODY, "base_url": "https://typo.example.invalid/v1"}
    )

    assert status == 422
    assert "could not be resolved" in body["detail"]


def test_stored_private_endpoint_is_refused_by_the_connection_test(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Defense in depth: a row that bypassed the write-time policy must still not
    # turn the probe into a request-forgery primitive.
    calls, _ = install_fake_provider(monkeypatch)
    raw_insert_config(broker_db, broker_db["super_a_id"], "https://169.254.169.254/v1")

    status, body = post_connection_test(broker_db, broker_db["super_a_id"])

    assert status == 422
    assert calls == []
