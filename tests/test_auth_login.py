"""Tests for the /auth/login endpoint.

Require none of: real PostgreSQL, real MT5, network access, or real
credentials. The get_db dependency is overridden with a per-test file-based
async SQLite database containing the real User/Broker models; JWT config uses
test-only values. No pytest asyncio plugin: async setup is driven with
asyncio.run.
"""
import asyncio
from typing import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.auth_router import router
from app.core.config import settings as app_settings
from app.core.security import decode_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture(autouse=True)
def fresh_login_throttle() -> Iterator[None]:
    # The throttle is process-wide state by design, so each test starts from a
    # clean one (the established reset_agent_usage_limiter pattern).
    deps.reset_login_throttle()
    yield
    deps.reset_login_throttle()


@pytest.fixture()
def small_login_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Shrink the lockout threshold so it can be reached in a few requests."""
    monkeypatch.setattr(app_settings, "LOGIN_MAX_FAILURES", 3, raising=True)
    deps.reset_login_throttle()
    yield 3
    deps.reset_login_throttle()


@pytest.fixture()
def auth_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int]":
    """Seed a per-test SQLite file DB; yield (session factory, user id)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/login_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="Test Broker", code="TB-1")
            session.add(broker)
            await session.commit()
            user = User(
                broker_id=broker.id,
                login="10001",
                # Real bcrypt hash created via the app's own primitive.
                password_hash=hash_for_test(TEST_PASSWORD),
                is_active=True,
            )
            session.add(user)
            await session.commit()
            return user.id

    user_id = asyncio.run(seed())
    yield factory, user_id
    asyncio.run(engine.dispose())


def hash_for_test(password: str) -> str:
    # Local helper (kept out of fixtures) so the hash call stays explicit.
    from app.core.security import hash_password

    return hash_password(password)


def make_client(factory: async_sessionmaker[AsyncSession]) -> TestClient:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def login_payload(login: str = "10001", password: str = TEST_PASSWORD) -> dict[str, str]:
    return {"login": login, "password": password}


def set_active(factory: async_sessionmaker[AsyncSession], model: type, row_id: int, active: bool) -> None:
    async def mutate() -> None:
        async with factory() as session:
            row = await session.get(model, row_id)
            assert row is not None
            row.is_active = active
            await session.commit()

    asyncio.run(mutate())


# --- success path -------------------------------------------------------------


def test_valid_credentials_return_200_with_token(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_returned_token_decodes_with_existing_decode_token(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        token = client.post("/auth/login", json=login_payload()).json()["access_token"]

    payload = decode_token(token)  # validates signature + expiry
    assert payload["sub"].isdigit()


def test_token_subject_equals_user_id(auth_db):
    factory, user_id = auth_db

    with make_client(factory) as client:
        token = client.post("/auth/login", json=login_payload()).json()["access_token"]

    assert decode_token(token)["sub"] == str(user_id)


# --- generic failure paths (all indistinguishable 401s) ------------------------


def test_wrong_password_returns_401(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload(password="wrong password"))

    assert response.status_code == 401


def test_nonexistent_login_returns_401(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload(login="no-such-user"))

    assert response.status_code == 401


def test_inactive_user_returns_401(auth_db):
    factory, user_id = auth_db
    set_active(factory, User, user_id, active=False)

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 401


def test_inactive_broker_returns_401(auth_db):
    factory, _ = auth_db
    set_active(factory, Broker, 1, active=False)

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 401


# --- failure-response hygiene ---------------------------------------------------


FAILURE_PATHS = ("wrong password", "no-such-user")


@pytest.mark.parametrize("bad_password", FAILURE_PATHS)
def test_all_failure_paths_are_indistinguishable(auth_db, bad_password):
    factory, _ = auth_db

    with make_client(factory) as client:
        wrong_pw = client.post("/auth/login", json=login_payload(password=bad_password))
        wrong_user = client.post("/auth/login", json=login_payload(login="no-such-user"))

    assert wrong_pw.status_code == wrong_user.status_code == 401
    # Identical body and challenge for every rejection path.
    assert wrong_pw.json() == wrong_user.json()
    assert wrong_pw.headers["www-authenticate"] == "Bearer"


def test_error_response_exposes_no_credentials_or_token(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload(password="wrong password"))

    body = str(response.json())
    assert TEST_PASSWORD not in body
    assert "password_hash" not in body
    assert "$2b$" not in body
    assert "mt5_password" not in body


def test_success_response_exposes_only_token_fields(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert set(response.json().keys()) == {"access_token", "token_type"}


# --- brute-force protection -----------------------------------------------------------


def statuses_for(client: TestClient, attempts: int, **overrides: str) -> list[int]:
    return [client.post("/auth/login", json=login_payload(**overrides)).status_code for _ in range(attempts)]


def test_repeated_failures_eventually_return_429(auth_db, small_login_limit):
    factory, _ = auth_db

    with make_client(factory) as client:
        statuses = statuses_for(client, small_login_limit + 1, password="wrong password")

    assert statuses[:-1] == [401] * small_login_limit
    assert statuses[-1] == 429


def test_throttle_is_identical_for_existing_and_unknown_logins(auth_db, small_login_limit):
    factory, _ = auth_db

    with make_client(factory) as client:
        existing = statuses_for(client, small_login_limit + 1, password="wrong password")

    # Same keys, fresh counters: the unknown login must behave identically,
    # so the lockout cannot be used to probe which accounts exist.
    deps.reset_login_throttle()
    with make_client(factory) as client:
        unknown = statuses_for(client, small_login_limit + 1, login="no-such-user", password="wrong password")

    assert existing == unknown


def test_throttle_applies_even_to_a_correct_password(auth_db, small_login_limit):
    factory, _ = auth_db

    with make_client(factory) as client:
        failed = statuses_for(client, small_login_limit, password="wrong password")
        blocked = client.post("/auth/login", json=login_payload())

    assert failed == [401] * small_login_limit
    # The lockout is deliberate: it cannot be bypassed by suddenly knowing the
    # password, which is what makes it effective against guessing.
    assert blocked.status_code == 429


def test_successful_login_clears_failed_attempts(auth_db, small_login_limit):
    factory, _ = auth_db

    with make_client(factory) as client:
        assert statuses_for(client, small_login_limit - 1, password="wrong password") == [401] * (small_login_limit - 1)
        assert client.post("/auth/login", json=login_payload()).status_code == 200
        # Counters were cleared by the success, so failing again is not 429.
        assert client.post("/auth/login", json=login_payload(password="wrong password")).status_code == 401


def test_throttled_response_is_generic_and_secret_free(auth_db, small_login_limit):
    factory, _ = auth_db

    with make_client(factory) as client:
        statuses_for(client, small_login_limit, password="wrong password")
        response = client.post("/auth/login", json=login_payload())

    body = str(response.json())
    assert response.status_code == 429
    # The body is exactly the fixed generic message: it names neither the
    # submitted login (asserted below) nor any internal detail.
    assert body == str({"detail": "Too many failed login attempts; try again later"})
    assert "10001" not in body
    assert TEST_PASSWORD not in body
    assert "no-such-user" not in body
    assert "password_hash" not in body
    assert "$2b$" not in body
