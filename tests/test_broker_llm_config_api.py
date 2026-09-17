"""Tests for the broker LLM configuration API (Step 32).

Require none of: real PostgreSQL, real MT5, network, real credentials, or a real
LLM. get_db is overridden with a per-test file-based async SQLite database
holding the real Broker/User/BrokerLLMConfig models; the real authentication and
super_admin authorization dependencies run (real JWT decode + database role
check). The LLM provider seam is patched with a deterministic offline double, so
no test performs a network call. JWT and encryption config use test-only values.

The worker-boundary tests at the bottom measure anyio's per-event-loop worker
limiter — the exact token run_mt5_call borrows — to prove the connection test's
model round trip holds no MT5 worker: the endpoint crosses only the run_llm_call
boundary /agent uses, on the real router coroutine with the real boundaries.
"""
import asyncio
import inspect
import logging
import socket
import threading
import time
from typing import Any, AsyncIterator

import pytest
from anyio.to_thread import current_default_thread_limiter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.broker_llm_config.broker_llm_config_service as tester_module
from app.api import broker_llm_config_router
from app.api.broker_llm_config_router import router
from app.core.blocking import run_mt5_call
from app.services.broker_llm_config import LLMConnectionStatus, LLMConnectionTester
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
    """Seed the deployment's ONE broker with a super_admin, an admin and a customer.

    This is a one-broker product: the LLM configuration belongs to the whole
    deployment, and the boundary to attack is the ROLE boundary (an admin or a
    customer must never reach it), which every test here asserts.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/broker_llm_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="LLM-ONE", mt5_server="TheBroker-Live")
            session.add(broker)
            await session.flush()

            def make(broker: Broker, login: str, role: UserRole) -> User:
                return User(
                    broker_id=broker.id,
                    login=login,
                    password_hash="x-not-a-real-hash",
                    is_active=True,
                    role=role,
                )

            top = make(broker, "9001", UserRole.SUPER_ADMIN)
            manager = make(broker, "9002", UserRole.ADMIN)
            customer = make(broker, "10001", UserRole.CUSTOMER)
            session.add_all([top, manager, customer])
            await session.commit()
            return {
                "broker_a_id": broker.id,
                "super_a_id": top.id,
                "admin_a_id": manager.id,
                "cust_a_id": customer.id,
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
    release: threading.Event | None = None,
    entered: threading.Event | None = None,
) -> "tuple[list[tuple[str, str, str]], list[int]]":
    """Patch the provider seam used by the connection tester.

    Records the credentials the tester passed down and the thread the blocking
    call ran on. Never performs network I/O. When ``release`` is given, the fake
    model call is HELD inside whichever thread ran it until the test sets the
    event (and ``entered`` is set the moment the call starts), which is what
    makes an in-flight round trip observable to the worker-boundary tests.
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
            if entered is not None:
                entered.set()
            if release is not None and not release.wait(timeout=30):  # pragma: no cover - a hung model call
                raise RuntimeError("the test never released the model call")
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


# The real endpoint coroutine, under a name pytest will not try to collect: the
# suite runs no asyncio plugin (async scenarios are driven with asyncio.run),
# and the endpoint's own name starts with test_.
run_connection_test = broker_llm_config_router.test_llm_connection


async def _load_user(factory: async_sessionmaker[AsyncSession], user_id: int) -> User:
    async with factory() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        return result.scalar_one()


async def _await_event(event: threading.Event, *, timeout: float = 20.0) -> None:
    """Wait (bounded) for the gated model phase to start, without hanging the suite."""
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("the gated model call never started")
        await asyncio.sleep(0.005)


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


def test_request_cannot_choose_a_broker(broker_db) -> None:
    # There is exactly one broker, so a caller has nothing to choose — and the
    # request cannot even try: the field is structurally impossible.
    super_a_id = broker_db["super_a_id"]

    body_status, _ = put_config(broker_db, super_a_id, {**VALID_BODY, "broker_id": 2})
    assert body_status == 422

    # A broker_id query parameter does not exist: it is ignored and the response
    # stays the deployment's own configuration (none yet, so 404).
    query_status, query_body = get_config(broker_db, super_a_id, query="?broker_id=2")
    assert query_status == 404
    assert query_body == {"detail": "LLM configuration is not set for this broker"}

    # Path-based selection does not exist either.
    with make_client(broker_db["factory"]) as client:
        path_response = client.get(f"{CONFIG_PATH}/2", headers=auth_header(token_for(super_a_id)))
    assert path_response.status_code == 404

    # Nothing was written by any of those attempts.
    assert count_configs(broker_db, broker_db["broker_a_id"]) == 0


def test_one_configuration_for_the_deployment_is_enforced_by_the_database(broker_db) -> None:
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


def test_connection_test_cannot_be_reached_by_a_non_super_admin(broker_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # The stored credential is the deployment's; the only way to exercise it is
    # the super_admin role, and an admin/customer is refused before any provider
    # is built (so the configured key is never decrypted for them).
    calls, _ = install_fake_provider(monkeypatch)
    put_config(broker_db, broker_db["super_a_id"], VALID_BODY)

    admin_status, _ = post_connection_test(broker_db, broker_db["admin_a_id"])
    customer_status, _ = post_connection_test(broker_db, broker_db["cust_a_id"])

    assert (admin_status, customer_status) == (403, 403)
    assert calls == []


# --- the connection test holds no MT5 blocking worker -------------------------------------
# The endpoint used to cross the consolidated MT5 boundary (run_mt5_call) with
# the model round trip inside it, so one administrative "test this
# configuration" call parked a worker thread an MT5 read needs for the whole
# provider latency — the same defect the /agent split fixed. These tests drive
# the REAL router coroutine (test_llm_connection) with the REAL boundaries from
# app.core.blocking, over the same offline double, and measure anyio's
# per-event-loop worker limiter: exactly what run_mt5_call borrows. A held
# provider answer makes the in-flight round trip observable.


def test_connection_test_holds_no_mt5_worker_while_the_model_is_thinking(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While the probe's model call is in flight, zero MT5 workers are borrowed.

    Matched with the control below: the probe is sensitive because the OLD
    run_mt5_call shape measurably parks the worker (it cannot even serve another
    MT5 read); the in-flight model round trip shows no borrowed token at all, so
    it cannot be starving MT5 reads.
    """

    async def scenario() -> int:
        limiter = current_default_thread_limiter()
        entered = threading.Event()
        release = threading.Event()
        install_fake_provider(monkeypatch, entered=entered, release=release)
        put_config(broker_db, broker_db["super_a_id"], VALID_BODY)
        user = await _load_user(broker_db["factory"], broker_db["super_a_id"])

        # The real dependencies the endpoint resolves: DB session, authenticated
        # super_admin, real LLMConnectionTester, real boundaries.
        session = broker_db["factory"]()
        try:
            task = asyncio.ensure_future(run_connection_test(user, session, LLMConnectionTester()))
            await _await_event(entered)
            # The model round trip is in flight right now.
            while_model = limiter.borrowed_tokens
            release.set()
            response = await task
        finally:
            await session.close()

        assert response.status == LLMConnectionStatus.OK
        return while_model

    assert asyncio.run(scenario()) == 0


def test_an_mt5_read_can_complete_while_a_connection_test_is_in_flight(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operational consequence, at the hardest setting: ONE MT5 worker.

    With the worker pool squeezed to a single token and the probe's model call
    held open, a call through run_mt5_call still completes — the worker was
    never taken by the connection test.
    """

    async def scenario() -> str:
        limiter = current_default_thread_limiter()
        limiter.total_tokens = 1  # the whole pool: one worker
        entered = threading.Event()
        release = threading.Event()
        install_fake_provider(monkeypatch, entered=entered, release=release)
        put_config(broker_db, broker_db["super_a_id"], VALID_BODY)
        user = await _load_user(broker_db["factory"], broker_db["super_a_id"])

        session = broker_db["factory"]()
        try:
            task = asyncio.ensure_future(run_connection_test(user, session, LLMConnectionTester()))
            await _await_event(entered)

            # The only MT5 worker is free while the probe waits on the model.
            read = await asyncio.wait_for(run_mt5_call(lambda: "mt5 read completed"), timeout=10)

            release.set()
            response = await task
        finally:
            await session.close()

        assert response.status == LLMConnectionStatus.OK
        return read

    assert asyncio.run(scenario()) == "mt5 read completed"


def test_the_old_mt5_boundary_call_would_have_held_the_worker(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the OLD router call really does occupy the worker for the probe.

    This is the exact call the endpoint used to make — tester.check inside
    run_mt5_call — and with one worker it leaves that worker unavailable, so the
    tests above are meaningful rather than vacuously true: the measurement
    discriminates the two shapes.
    """

    async def scenario() -> None:
        limiter = current_default_thread_limiter()
        limiter.total_tokens = 1  # the whole pool: one worker
        entered = threading.Event()
        release = threading.Event()
        install_fake_provider(monkeypatch, entered=entered, release=release)

        # The pre-change router call: the model probe INSIDE the MT5 boundary.
        task = asyncio.ensure_future(
            run_mt5_call(LLMConnectionTester().check, API_KEY, VALID_BODY["base_url"], VALID_BODY["model"])
        )
        await _await_event(entered)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(run_mt5_call(lambda: "never runs"), timeout=0.5)

        release.set()
        await task

    asyncio.run(scenario())


def test_the_connection_tester_crosses_only_the_llm_boundary(
    broker_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call runs off the loop on the LLM pool, and the endpoint names no MT5."""

    async def scenario() -> bool:
        entered = threading.Event()
        release = threading.Event()
        _, threads = install_fake_provider(monkeypatch, entered=entered, release=release)
        put_config(broker_db, broker_db["super_a_id"], VALID_BODY)
        user = await _load_user(broker_db["factory"], broker_db["super_a_id"])

        loop_thread = threading.get_ident()
        session = broker_db["factory"]()
        try:
            task = asyncio.ensure_future(run_connection_test(user, session, LLMConnectionTester()))
            await _await_event(entered)
            release.set()
            await task
        finally:
            await session.close()

        # The blocking provider call ran on a worker thread, never the event
        # loop, and (by the limiter test above) never the MT5 worker pool either.
        return bool(threads) and threads[0] != loop_thread

    assert asyncio.run(scenario())

    source = inspect.getsource(broker_llm_config_router)

    # The endpoint crosses the consolidated boundaries, and only those.
    assert "from app.core.blocking import run_llm_call" in source
    assert "run_mt5_call" not in source, "the LLM probe must never cross the MT5 boundary"
    assert "starlette.concurrency" not in source and "run_in_threadpool" not in source
    assert "run_in_executor" not in source
    # Read-only by construction: the endpoint gained no order/mutation surface.
    for forbidden in ("order_send", "order_check", "positions_modify", "positions_close"):
        assert forbidden not in source


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
