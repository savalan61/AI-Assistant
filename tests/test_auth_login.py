"""Tests for the /auth/login endpoint.

Require none of: real PostgreSQL, real MT5, network access, or real
credentials. The get_db dependency is overridden with a per-test file-based
async SQLite database containing the real User/Broker models; JWT config uses
test-only values. No pytest asyncio plugin: async setup is driven with
asyncio.run.
"""
import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
                username="10001",
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


def login_payload(username: str = "10001", password: str = TEST_PASSWORD) -> dict[str, str]:
    return {"username": username, "password": password}


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


def test_nonexistent_username_returns_401(auth_db):
    factory, _ = auth_db

    with make_client(factory) as client:
        response = client.post("/auth/login", json=login_payload(username="no-such-user"))

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
        wrong_user = client.post("/auth/login", json=login_payload(username="no-such-user"))

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
