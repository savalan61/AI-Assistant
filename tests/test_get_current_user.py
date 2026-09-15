"""Tests for the get_current_user authentication dependency.

Require none of: real MT5, external network, real credentials, real PostgreSQL.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models, and JWT config uses test-only
values. No pytest asyncio plugin is used: async setup/teardown is driven
explicitly with asyncio.run.
"""
import asyncio
from datetime import timedelta
from typing import AsyncIterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings as app_settings
from app.core.dependencies import get_current_user
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def user_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int]":
    """Seed a per-test SQLite file DB and yield (session factory, user id)."""
    # A file-backed DB (not :memory:) so the schema persists across connections;
    # tmp_path gives each test an isolated database file.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/auth_test.db")
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
                password_hash="$2b$12$notarealhashbutcolumnisrequired01234567890123456789",
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            session.add(user)
            await session.commit()
            return user.id

    user_id = asyncio.run(seed())
    yield factory, user_id
    asyncio.run(engine.dispose())


def make_client(factory: async_sessionmaker[AsyncSession]) -> TestClient:
    """Build a probe app exposing the dependency's decision as a status code."""
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    probe_app = FastAPI()

    @probe_app.get("/whoami")
    async def whoami(current_user: User = Depends(get_current_user)) -> dict[str, int | str]:
        # Deliberately exposes only non-sensitive identifiers for assertions.
        # role comes from the database-backed User, never from the token.
        return {"user_id": current_user.id, "broker_id": current_user.broker_id, "role": current_user.role.value}

    probe_app.dependency_overrides[get_db] = override_get_db
    return TestClient(probe_app)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def set_user_active(factory: async_sessionmaker[AsyncSession], user_id: int, active: bool) -> None:
    async def mutate() -> None:
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            user.is_active = active
            await session.commit()

    asyncio.run(mutate())


# --- happy path --------------------------------------------------------------


def test_valid_token_and_active_user_returns_user(user_db):
    factory, user_id = user_db

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token(str(user_id))))

    assert response.status_code == 200
    # role is served from the database record, not from any token claim.
    assert response.json() == {"user_id": user_id, "broker_id": 1, "role": "super_admin"}


def test_bearer_prefix_is_required(user_db):
    factory, user_id = user_db

    with make_client(factory) as client:
        # A raw token without the Bearer scheme must not authenticate.
        response = client.get("/whoami", headers={"Authorization": create_access_token(str(user_id))})

    assert response.status_code == 401


# --- missing/invalid credentials ----------------------------------------------


def test_missing_bearer_token_returns_401(user_db):
    factory, _ = user_db

    with make_client(factory) as client:
        response = client.get("/whoami")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_jwt_returns_401(user_db):
    factory, _ = user_db

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header("not-a-jwt-token"))

    assert response.status_code == 401


def test_expired_jwt_returns_401(user_db):
    factory, user_id = user_db
    expired = create_access_token(str(user_id), expires_delta=timedelta(seconds=-10))

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(expired))

    assert response.status_code == 401


def test_non_numeric_subject_returns_401(user_db):
    factory, _ = user_db

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token("not-a-number")))

    assert response.status_code == 401


def test_empty_subject_returns_401(user_db):
    factory, _ = user_db

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token("")))  # empty sub

    assert response.status_code == 401


# --- database-backed identity checks -------------------------------------------


def test_token_for_nonexistent_user_returns_401(user_db):
    factory, _ = user_db

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token("999999")))

    assert response.status_code == 401


def test_token_for_inactive_user_returns_401(user_db):
    factory, user_id = user_db
    set_user_active(factory, user_id, active=False)

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token(str(user_id))))

    assert response.status_code == 401


def test_suspended_broker_returns_401_despite_a_valid_token(user_db):
    # Suspending a broker must take effect for tokens that were already issued,
    # not only at the next login.
    factory, user_id = user_db

    async def suspend() -> None:
        async with factory() as session:
            broker = await session.get(Broker, 1)
            assert broker is not None
            broker.is_active = False
            await session.commit()

    asyncio.run(suspend())

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(create_access_token(str(user_id))))

    assert response.status_code == 401


def test_suspended_broker_error_is_indistinguishable_from_an_unknown_user(user_db):
    factory, user_id = user_db

    async def suspend() -> None:
        async with factory() as session:
            broker = await session.get(Broker, 1)
            assert broker is not None
            broker.is_active = False
            await session.commit()

    asyncio.run(suspend())

    with make_client(factory) as client:
        suspended = client.get("/whoami", headers=auth_header(create_access_token(str(user_id))))
        unknown = client.get("/whoami", headers=auth_header(create_access_token("999999")))

    # Neither body nor status reveals that the broker exists but is suspended.
    assert suspended.status_code == unknown.status_code == 401
    assert suspended.json() == unknown.json()


def test_forged_signature_returns_401(user_db):
    import jwt as pyjwt

    factory, user_id = user_db
    forged = pyjwt.encode({"sub": str(user_id), "exp": 4102444800}, "wrong-secret-that-is-long-enough-32!", algorithm=TEST_ALGORITHM)

    with make_client(factory) as client:
        response = client.get("/whoami", headers=auth_header(forged))

    assert response.status_code == 401
