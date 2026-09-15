"""Tests for the Super Admin POST /users/admins endpoint (Step 22).

Require none of: real PostgreSQL, real MT5, network, or real credentials.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models; the REAL authentication and
super-admin authorization dependencies run (real JWT decode + database role
check). JWT config uses test-only values. No pytest asyncio plugin: async
setup is driven with asyncio.run.
"""
import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.users_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token, verify_password
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
SUPER_PASSWORD = "super application password"

ALLOWED_FIELDS = {"id", "broker_id", "username", "email", "phone", "role", "is_active"}


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int, int, int, int]":
    """Seed two brokers: broker A (super_admin + admin + customer), broker B (super_admin).

    Yields (session factory, super_a_id, admin_a_id, customer_id, super_b_id).
    Two tenants are needed to prove cross-tenant isolation and tenant-scoped
    duplicate behavior; the in-tenant admin/customer prove the 403 matrix.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/users_admins_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "tuple[int, int, int, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TA-A")
            broker_b = Broker(name="Broker B", code="TA-B")
            session.add_all([broker_a, broker_b])
            await session.flush()
            super_a = User(
                broker_id=broker_a.id,
                username="super-a",
                # Real bcrypt hash via the app's own primitive.
                password_hash=hash_for_test(SUPER_PASSWORD),
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            admin_a = User(
                broker_id=broker_a.id,
                username="admin-a",
                password_hash="x-not-a-real-hash",
                is_active=True,
                role=UserRole.ADMIN,
            )
            customer = User(
                broker_id=broker_a.id,
                # Digit username: the API enforces MT5-login format on created
                # users, so the seeded row used by duplicate tests matches it.
                username="10002",
                password_hash="x-not-a-real-hash",
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            super_b = User(
                broker_id=broker_b.id,
                username="super-b",
                password_hash=hash_for_test(SUPER_PASSWORD),
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            session.add_all([super_a, admin_a, customer, super_b])
            await session.commit()
            return super_a.id, admin_a.id, customer.id, super_b.id

    ids = asyncio.run(seed())
    yield (factory, *ids)
    asyncio.run(engine.dispose())


def hash_for_test(password: str) -> str:
    # Local helper so the hash call stays explicit at the call sites.
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


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def admin_token(user_id: int) -> str:
    return create_access_token(str(user_id))


def admin_payload(username: str = "30001", password: str = "admin password", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"username": username, "password": password}
    payload.update(extra)
    return payload


# --- authentication / authorization -------------------------------------------


def test_unauthenticated_admin_creation_returns_401(users_db) -> None:
    factory, *_ = users_db

    with make_client(factory) as client:
        response = client.post("/users/admins", json=admin_payload())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_customer_cannot_create_admin_returns_403(users_db) -> None:
    factory, _, _, customer_id, _ = users_db

    with make_client(factory) as client:
        response = client.post("/users/admins", json=admin_payload(), headers=auth_header(admin_token(customer_id)))

    assert response.status_code == 403


def test_admin_cannot_create_admin_returns_403(users_db) -> None:
    factory, _, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post("/users/admins", json=admin_payload(), headers=auth_header(admin_token(admin_a_id)))

    assert response.status_code == 403


# --- super_admin success paths ---------------------------------------------------


def test_super_admin_creates_admin_returns_201(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins",
            json=admin_payload(email="admin@example.com", phone="+12345678901"),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "30001"
    assert body["email"] == "admin@example.com"
    assert body["phone"] == "+12345678901"
    assert body["is_active"] is True
    # Exactly the non-sensitive projection; no credential fields exist.
    assert set(body.keys()) == ALLOWED_FIELDS


def test_created_admin_has_admin_role(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        body = client.post("/users/admins", json=admin_payload(), headers=auth_header(admin_token(super_a_id))).json()

    # Server-side constant: the endpoint only ever creates admins.
    assert body["role"] == "admin"


def test_created_admin_belongs_to_super_admins_broker(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        body = client.post("/users/admins", json=admin_payload(), headers=auth_header(admin_token(super_a_id))).json()

    # Tenant is derived from the authenticated super_admin, never the request.
    assert body["broker_id"] == 1


def test_persisted_admin_role_and_tenant_and_hash(users_db) -> None:
    factory, super_a_id, *_ = users_db
    plaintext = "admin password"

    with make_client(factory) as client:
        created_id = client.post(
            "/users/admins", json=admin_payload(password=plaintext), headers=auth_header(admin_token(super_a_id))
        ).json()["id"]

    async def fetch() -> User | None:
        async with factory() as session:
            return await session.get(User, created_id)

    user = asyncio.run(fetch())
    assert user is not None
    # Database is the authority: role and tenant were derived server-side.
    assert user.role is UserRole.ADMIN
    assert user.broker_id == 1
    # Plaintext never reaches persistence; the stored hash is a real bcrypt
    # hash of exactly the supplied password.
    assert user.password_hash.startswith("$2")
    assert verify_password(plaintext, user.password_hash) is True


# --- caller cannot forge server-derived fields -------------------------------------


def test_broker_id_cannot_be_supplied(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins",
            # Cross-tenant creation attempt via broker_id: the request model
            # forbids unknown fields, so this is a 422 validation error.
            json=admin_payload(broker_id=999),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("escalation_role", ["super_admin", "admin", "broker_admin", "customer"])
def test_caller_cannot_escalate_role(users_db, escalation_role: str) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins",
            # Role is not a request field (extra="forbid"): no caller can
            # select a role at all — in particular not another super_admin.
            # The legacy value is included to prove old escalation paths stay
            # closed.
            json=admin_payload(role=escalation_role),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert response.status_code == 422


# --- duplicate / uniqueness handling (existing conventions) -------------------------


def test_duplicate_username_in_same_broker_returns_409(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins",
            # "10002" already exists in broker A (the seeded customer).
            json=admin_payload(username="10002"),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert response.status_code == 409


def test_same_username_in_different_broker_is_allowed(users_db) -> None:
    factory, *_, super_b_id = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins",
            # Uniqueness is tenant-scoped: broker B may reuse broker A's name.
            json=admin_payload(username="10002"),
            headers=auth_header(admin_token(super_b_id)),
        )

    assert response.status_code == 201
    assert response.json()["broker_id"] == 2


def test_duplicate_email_in_same_broker_returns_409(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        first = client.post(
            "/users/admins",
            json=admin_payload(username="30011", email="dup@example.com"),
            headers=auth_header(admin_token(super_a_id)),
        )
        second = client.post(
            "/users/admins",
            json=admin_payload(username="30012", email="dup@example.com"),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert first.status_code == 201
    assert second.status_code == 409


def test_duplicate_phone_in_same_broker_returns_409(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        first = client.post(
            "/users/admins",
            json=admin_payload(username="30013", phone="+12345678901"),
            headers=auth_header(admin_token(super_a_id)),
        )
        second = client.post(
            "/users/admins",
            json=admin_payload(username="30014", phone="+12345678901"),
            headers=auth_header(admin_token(super_a_id)),
        )

    assert first.status_code == 201
    assert second.status_code == 409


# --- credential safety ----------------------------------------------------------------


def test_response_exposes_no_sensitive_fields(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users/admins", json=admin_payload(password="admin password"), headers=auth_header(admin_token(super_a_id))
        )

    body = str(response.json())
    assert "password" not in body
    assert "$2b$" not in body
    assert "password_hash" not in body
    assert "mt5_password_encrypted" not in body


# --- existing customer-creation behavior is untouched -----------------------------------


def test_super_admin_can_still_create_customer_via_post_users(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users", json={"username": "30015", "password": "customer password"}, headers=auth_header(admin_token(super_a_id))
        )

    # POST /users keeps its customer-only contract alongside /users/admins.
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "customer"
    assert body["broker_id"] == 1
