"""Tests for the user-listing endpoint GET /users (Step 21, evolved to the
three-role super_admin/admin/customer model).

Require none of: real PostgreSQL, real MT5, network, or real credentials.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models; the REAL authentication and
manager-authorization dependencies run (real JWT decode + database role
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
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
ADMIN_PASSWORD = "admin application password"

ALLOWED_FIELDS = {"id", "broker_id", "login", "email", "phone", "role", "is_active"}


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int, int, int, int, int, int, int, int]":
    """Seed three tenants covering the whole role matrix.

    Broker A: super_a + admins admin_a1/admin_a2 + customers 10002/10003.
    Broker B: super_b + admin_b + customer 10004 (cross-tenant rows that must
    never be returned to broker A). Broker C: super_c alone (the empty-listing
    case, since a manager can never list itself).

    Yields (session factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id,
    cust_a2_id, super_b_id, admin_b_id, cust_b_id, super_c_id) — 10 items.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/users_list_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "tuple[int, int, int, int, int, int, int, int, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TL-A")
            broker_b = Broker(name="Broker B", code="TL-B")
            broker_c = Broker(name="Broker C", code="TL-C")
            session.add_all([broker_a, broker_b, broker_c])
            await session.flush()

            def make(broker: Broker, login: str, role: UserRole) -> User:
                return User(
                    broker_id=broker.id,
                    login=login,
                    password_hash=hash_password(ADMIN_PASSWORD) if role is UserRole.SUPER_ADMIN else "x-not-a-real-hash",
                    is_active=True,
                    role=role,
                )

            super_a = make(broker_a, "super-a", UserRole.SUPER_ADMIN)
            admin_a1 = make(broker_a, "admin-a1", UserRole.ADMIN)
            admin_a2 = make(broker_a, "admin-a2", UserRole.ADMIN)
            cust_a1 = make(broker_a, "10002", UserRole.CUSTOMER)
            cust_a2 = make(broker_a, "10003", UserRole.CUSTOMER)
            super_b = make(broker_b, "super-b", UserRole.SUPER_ADMIN)
            admin_b = make(broker_b, "admin-b", UserRole.ADMIN)
            cust_b = make(broker_b, "10004", UserRole.CUSTOMER)
            super_c = make(broker_c, "super-c", UserRole.SUPER_ADMIN)
            session.add_all([super_a, admin_a1, admin_a2, cust_a1, cust_a2, super_b, admin_b, cust_b, super_c])
            await session.commit()
            return (
                super_a.id,
                admin_a1.id,
                admin_a2.id,
                cust_a1.id,
                cust_a2.id,
                super_b.id,
                admin_b.id,
                cust_b.id,
                super_c.id,
            )

    ids = asyncio.run(seed())
    yield (factory, *ids)
    asyncio.run(engine.dispose())


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


def list_as(factory: async_sessionmaker[AsyncSession], user_id: int, query: str = "") -> list[dict[str, object]]:
    """Authenticate as the given user and return the parsed GET /users body."""
    with make_client(factory) as client:
        response = client.get(f"/users{query}", headers=auth_header(token_for(user_id)))
    assert response.status_code == 200
    body: list[dict[str, object]] = response.json()
    return body


def ids_of(body: list[dict[str, object]]) -> list[object]:
    return [entry["id"] for entry in body]


# --- authentication / authorization -------------------------------------------


def test_unauthenticated_list_returns_401(users_db) -> None:
    factory, *_ = users_db

    with make_client(factory) as client:
        response = client.get("/users")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_customer_list_returns_403(users_db) -> None:
    factory, _, _, _, cust_a1_id, *_ = users_db

    with make_client(factory) as client:
        response = client.get("/users", headers=auth_header(token_for(cust_a1_id)))

    assert response.status_code == 403


def test_super_admin_list_returns_200(users_db) -> None:
    factory, super_a_id, *_ = users_db

    with make_client(factory) as client:
        response = client.get("/users", headers=auth_header(token_for(super_a_id)))

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_admin_list_returns_200(users_db) -> None:
    factory, _, admin_a1_id, *_ = users_db

    with make_client(factory) as client:
        response = client.get("/users", headers=auth_header(token_for(admin_a1_id)))

    assert response.status_code == 200
    assert isinstance(response.json(), list)


# --- super_admin visibility: admins + customers, never itself --------------------


def test_super_admin_sees_admins_and_customers(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id, *_ = users_db

    body = list_as(factory, super_a_id)

    # Every managed user of broker A in stable id order — and never the
    # requesting super_admin itself.
    assert ids_of(body) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
    roles = {entry["role"] for entry in body}
    assert roles == {"admin", "customer"}
    assert all(entry["broker_id"] == 1 for entry in body)


def test_super_admin_does_not_see_itself(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body = list_as(factory, super_a_id)

    assert super_a_id not in ids_of(body)
    assert "super-a" not in [entry["login"] for entry in body]


# --- admin visibility: customers only ---------------------------------------------


def test_admin_sees_customers(users_db) -> None:
    factory, _, admin_a1_id, _, cust_a1_id, cust_a2_id, *_ = users_db

    body = list_as(factory, admin_a1_id)

    # The admin's manageable users: exactly the customers of broker A.
    assert ids_of(body) == sorted([cust_a1_id, cust_a2_id])
    assert {entry["role"] for entry in body} == {"customer"}
    assert all(entry["broker_id"] == 1 for entry in body)


def test_admin_cannot_list_admins_or_super_admins(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, *_ = users_db

    body = list_as(factory, admin_a1_id)

    # Neither the other admin nor the broker's super_admin is ever returned.
    assert admin_a2_id not in ids_of(body)
    assert super_a_id not in ids_of(body)
    assert "admin-a2" not in [entry["login"] for entry in body]
    assert "super-a" not in [entry["login"] for entry in body]


# --- cross-tenant isolation --------------------------------------------------------


def test_cross_broker_users_are_never_returned(users_db) -> None:
    factory, super_a_id, admin_a1_id, _, _, cust_a1_id, _, _, cust_b_id, _ = users_db

    for manager_id in (super_a_id, admin_a1_id):
        body = list_as(factory, manager_id)
        # Broker B's rows (including its customer 10004) must never appear.
        assert cust_b_id not in ids_of(body)
        assert "10004" not in [entry["login"] for entry in body]


def test_other_broker_super_admin_sees_only_their_own_tenant(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id, super_b_id, *_ = users_db

    body = list_as(factory, super_b_id)

    # Broker B's super_admin sees only broker B's managed users (its admin and
    # its customer), never any broker A row.
    assert all(entry["broker_id"] == 2 for entry in body)
    assert super_a_id not in ids_of(body)
    for broker_a_user in (admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id):
        assert broker_a_user not in ids_of(body)


# --- response contract --------------------------------------------------------------


def test_response_contains_exactly_allowed_fields(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body = list_as(factory, super_a_id)
    assert body, "seeded tenant must contain users"

    for entry in body:
        assert set(entry.keys()) == ALLOWED_FIELDS


def test_password_hash_not_exposed(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body_text = str(list_as(factory, super_a_id))

    assert "password_hash" not in body_text
    # No bcrypt artifact can leak even if a field were ever renamed.
    assert "$2b$" not in body_text
    assert "password" not in body_text


def test_mt5_password_encrypted_not_exposed(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body_text = str(list_as(factory, super_a_id))

    assert "mt5_password_encrypted" not in body_text
    assert "mt5" not in body_text.lower()


# --- no client-selectable tenant -----------------------------------------------------


def test_broker_id_query_cannot_select_another_tenant(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id, *_ = users_db

    # There is no broker_id parameter; supplying one must be ignored, so the
    # listing stays scoped to the authenticated manager's own broker.
    body = list_as(factory, super_a_id, query="?broker_id=2")

    assert ids_of(body) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
    assert all(entry["broker_id"] == 1 for entry in body)


# --- empty listing --------------------------------------------------------------------


def test_empty_tenant_returns_200_with_empty_list(users_db) -> None:
    factory, *_, super_c_id = users_db

    # Broker C contains only its super_admin, and a manager is excluded from
    # the listing: an empty result is a normal 200 with [], never a 404/403.
    # Supplying broker_id=1 (another tenant) must not change that.
    body = list_as(factory, super_c_id, query="?broker_id=1")

    assert body == []


# --- ordering ---------------------------------------------------------------------------


def test_deterministic_ordering_by_id_ascending(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id, *_ = users_db

    body = list_as(factory, super_a_id)
    ids = ids_of(body)

    # Stable ascending primary-key order, independent of insertion or DB plan.
    assert ids == sorted(ids) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
