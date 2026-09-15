"""Tests for the POST /users endpoint (Step 14; super_admin and admin may
create customers under the evolved three-role model).

Require none of: real PostgreSQL, real MT5, network, or real credentials.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models; the REAL authentication and
broker-admin authorization dependencies run (real JWT decode + database role
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
ADMIN_PASSWORD = "admin application password"


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int, int, int]":
    """Seed two brokers (each with an admin) plus a customer in broker A.

    Yields (session factory, admin_a_id, customer_id, admin_b_id). Two tenants
    are needed to prove cross-tenant isolation, not just absence of broker_id.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/users_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "tuple[int, int, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TB-A")
            broker_b = Broker(name="Broker B", code="TB-B")
            session.add_all([broker_a, broker_b])
            await session.flush()
            admin_a = User(
                broker_id=broker_a.id,
                username="admin-a",
                # Real bcrypt hash via the app's own primitive.
                password_hash=hash_for_test(ADMIN_PASSWORD),
                is_active=True,
                # The seeded operator account is the broker's single
                # super_admin (the evolved role model).
                role=UserRole.SUPER_ADMIN,
            )
            admin_b = User(
                broker_id=broker_b.id,
                username="admin-b",
                password_hash=hash_for_test(ADMIN_PASSWORD),
                is_active=True,
                role=UserRole.SUPER_ADMIN,
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
            session.add_all([admin_a, admin_b, customer])
            await session.commit()
            return admin_a.id, customer.id, admin_b.id

    admin_a_id, customer_id, admin_b_id = asyncio.run(seed())
    yield factory, admin_a_id, customer_id, admin_b_id
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


def create_payload(username: str = "20002", password: str = "customer password", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"username": username, "password": password}
    payload.update(extra)
    return payload


# --- authentication / authorization -------------------------------------------


def test_unauthenticated_request_returns_401(users_db) -> None:
    factory, _, _, _ = users_db

    with make_client(factory) as client:
        response = client.post("/users", json=create_payload())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_customer_cannot_create_users_returns_403(users_db) -> None:
    factory, _, customer_id, _ = users_db

    with make_client(factory) as client:
        response = client.post("/users", json=create_payload(), headers=auth_header(admin_token(customer_id)))

    assert response.status_code == 403


def test_admin_can_create_customer_returns_201(users_db) -> None:
    factory, *_ = users_db

    # The role model allows multiple admins per broker; insert one directly
    # (POST /users forces customer role, so admins are seeded, not created).
    async def seed_admin() -> int:
        async with factory() as session:
            admin = User(
                broker_id=1,
                username="admin-a1",
                password_hash="x-not-a-real-hash",
                is_active=True,
                role=UserRole.ADMIN,
            )
            session.add(admin)
            await session.commit()
            return admin.id

    admin_id = asyncio.run(seed_admin())

    with make_client(factory) as client:
        response = client.post("/users", json=create_payload(username="30002"), headers=auth_header(admin_token(admin_id)))

    assert response.status_code == 201
    body = response.json()
    # An admin creates inside their own broker, and the created user is a
    # plain customer regardless of the creator's role.
    assert body["broker_id"] == 1
    assert body["role"] == "customer"


# --- creation success paths -----------------------------------------------------


def test_super_admin_creates_customer_returns_201(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(email="c@example.com", phone="+12345678901"),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 201
    body = response.json()
    # Derived fields only: role is forced customer and the tenant is the
    # super_admin's broker; optional contact fields round-trip.
    assert body["role"] == "customer"
    assert body["broker_id"] == 1
    assert body["is_active"] is True
    assert body["username"] == "20002"
    assert body["email"] == "c@example.com"
    assert body["phone"] == "+12345678901"
    # Exactly the non-sensitive projection; no credential fields exist.
    assert set(body.keys()) == {"id", "broker_id", "username", "email", "phone", "role", "is_active"}


def test_created_user_persisted_with_derived_role_and_tenant(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        created_id = client.post(
            "/users", json=create_payload(), headers=auth_header(admin_token(admin_a_id))
        ).json()["id"]

    async def fetch() -> User | None:
        async with factory() as session:
            return await session.get(User, created_id)

    user = asyncio.run(fetch())
    assert user is not None
    # Database is the authority: role and tenant were derived server-side.
    assert user.role is UserRole.CUSTOMER
    assert user.broker_id == 1


def test_second_broker_super_admin_creates_user_in_own_tenant(users_db) -> None:
    factory, _, _, admin_b_id = users_db

    with make_client(factory) as client:
        body = client.post(
            "/users", json=create_payload(username="30001"), headers=auth_header(admin_token(admin_b_id))
        ).json()

    # Broker B's super_admin gets a user in broker B, never in another tenant.
    assert body["broker_id"] == 2


# --- password handling ------------------------------------------------------------


def test_plaintext_password_not_stored_and_hash_verifies(users_db) -> None:
    factory, admin_a_id, _, _ = users_db
    plaintext = "customer password"

    with make_client(factory) as client:
        created_id = client.post(
            "/users", json=create_payload(password=plaintext), headers=auth_header(admin_token(admin_a_id))
        ).json()["id"]

    async def fetch_hash() -> str:
        async with factory() as session:
            user = await session.get(User, created_id)
            assert user is not None
            return user.password_hash

    stored_hash = asyncio.run(fetch_hash())
    # Plaintext never reaches persistence; the stored hash is a real bcrypt
    # hash of exactly the supplied password.
    assert stored_hash != plaintext
    assert stored_hash.startswith("$2")
    assert verify_password(plaintext, stored_hash) is True
    assert verify_password("wrong password", stored_hash) is False


def test_response_exposes_no_sensitive_fields(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users", json=create_payload(password="customer password"), headers=auth_header(admin_token(admin_a_id))
        )

    body = str(response.json())
    assert "password" not in body
    assert "$2b$" not in body
    assert "password_hash" not in body
    assert "mt5_password_encrypted" not in body


# --- client cannot forge server-derived fields -------------------------------------


def test_broker_id_cannot_be_supplied(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Cross-tenant creation attempt via broker_id: the request model
            # forbids unknown fields, so this is a 422 validation error.
            json=create_payload(broker_id=999),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("escalation_role", ["super_admin", "admin", "broker_admin"])
def test_role_cannot_be_supplied(users_db, escalation_role: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Privilege-escalation attempt via role (including the legacy
            # value): rejected with 422 by the request model, before any
            # authorization/DB work happens. Created users are always
            # customers; no caller can choose another role here.
            json=create_payload(role=escalation_role),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


# --- duplicate / uniqueness handling -------------------------------------------------


def test_duplicate_username_in_same_broker_returns_409(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # "10002" already exists in broker A.
            json=create_payload(username="10002"),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 409


def test_same_username_in_different_broker_is_allowed(users_db) -> None:
    factory, _, _, admin_b_id = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Uniqueness is tenant-scoped: broker B may reuse broker A's name.
            json=create_payload(username="10002"),
            headers=auth_header(admin_token(admin_b_id)),
        )

    assert response.status_code == 201
    assert response.json()["broker_id"] == 2


# --- request validation (422 at the schema boundary) --------------------------------


@pytest.mark.parametrize("bad_username", ["new-customer", "123", "1234567890123", "", "12a4", "１２３４"])
def test_invalid_username_rejected_with_422(users_db, bad_username: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Letters, wrong length, empty, mixed, and Unicode digits are all
            # outside the MT5-login contract (ASCII digits, 4-12).
            json=create_payload(username=bad_username),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("valid_username", ["1000", "123456789012"])
def test_username_length_boundaries_accepted(users_db, valid_username: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Exactly 4 and exactly 12 digits are the inclusive boundaries.
            json=create_payload(username=valid_username),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 201
    assert response.json()["username"] == valid_username


def test_short_password_rejected_with_422(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(password="short"),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


def test_oversized_password_rejected_with_422(users_db) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Beyond bcrypt's 72-byte input limit; must be a 422 at the schema
            # boundary, never a hashing error inside the endpoint.
            json=create_payload(password="x" * 73),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("bad_email", ["not-an-email", "a@b", "a@b.", "@example.com", "a b@example.com", "a@@example.com"])
def test_malformed_email_rejected_with_422(users_db, bad_email: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(email=bad_email),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("bad_phone", ["call-me", "123-456-7890", "+123", "1234567890123456", "phone 123"])
def test_arbitrary_phone_text_rejected_with_422(users_db, bad_phone: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(phone=bad_phone),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 422


@pytest.mark.parametrize("valid_phone", ["1234567890", "+123456789012345"])
def test_valid_international_phone_accepted(users_db, valid_phone: str) -> None:
    factory, admin_a_id, _, _ = users_db

    with make_client(factory) as client:
        response = client.post(
            "/users",
            # Without and with '+'; 15 digits is the inclusive upper boundary.
            json=create_payload(phone=valid_phone),
            headers=auth_header(admin_token(admin_a_id)),
        )

    assert response.status_code == 201
    assert response.json()["phone"] == valid_phone
