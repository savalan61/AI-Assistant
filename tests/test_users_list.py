"""Tests for the user-listing endpoint GET /users (Step 21, evolved to the
three-role super_admin/admin/customer model and then to the ONE-BROKER
architecture).

Require none of: real PostgreSQL, real MT5, network, or real credentials.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models; the REAL authentication and
manager-authorization dependencies run (real JWT decode + database role
check). JWT config uses test-only values. No pytest asyncio plugin: async
setup is driven with asyncio.run.

This deployment serves ONE broker, so there is no second tenant to compare
against. What replaces the old cross-broker tests is the equivalent boundary
that still exists:

  * a customer is never listed to anybody, and cannot list at all;
  * an admin sees only customers, never another admin and never the
    deployment's super_admin;
  * a database that is not a one-broker database fails closed instead of
    silently serving the wrong broker's users.
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
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], int, int, int, int, int, int]":
    """Seed the deployment's ONE broker covering the whole role matrix.

    super + admin_a1 + admin_a2 (both manage every customer of the deployment)
    + customers 10002/10003. The requesting super_admin is never part of its own
    listing, and an admin's complete view is its customers only.

    Yields (session factory, super_id, admin_a1_id, admin_a2_id, cust_a1_id,
    cust_a2_id) — 6 items.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/users_list_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "tuple[int, int, int, int, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="TL-ONE", mt5_server="TheBroker-Live")
            session.add(broker)
            await session.flush()

            def make(login: str, role: UserRole) -> User:
                return User(
                    broker_id=broker.id,
                    login=login,
                    password_hash=hash_password(ADMIN_PASSWORD) if role is UserRole.SUPER_ADMIN else "x-not-a-real-hash",
                    is_active=True,
                    role=role,
                )

            super_admin = make("90001", UserRole.SUPER_ADMIN)
            admin_a1 = make("admin-1", UserRole.ADMIN)
            admin_a2 = make("admin-2", UserRole.ADMIN)
            cust_a1 = make("10002", UserRole.CUSTOMER)
            cust_a2 = make("10003", UserRole.CUSTOMER)
            session.add_all([super_admin, admin_a1, admin_a2, cust_a1, cust_a2])
            await session.commit()
            return (super_admin.id, admin_a1.id, admin_a2.id, cust_a1.id, cust_a2.id)

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
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id = users_db

    body = list_as(factory, super_a_id)

    # Every managed user of the deployment in stable id order — and never the
    # requesting super_admin itself.
    assert ids_of(body) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
    roles = {entry["role"] for entry in body}
    assert roles == {"admin", "customer"}
    assert {entry["broker_id"] for entry in body} == {1}


def test_super_admin_does_not_see_itself(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body = list_as(factory, super_a_id)

    assert super_a_id not in ids_of(body)
    assert "90001" not in [entry["login"] for entry in body]


# --- admin visibility: customers only ---------------------------------------------


def test_admin_sees_customers(users_db) -> None:
    factory, _, admin_a1_id, _, cust_a1_id, cust_a2_id = users_db

    body = list_as(factory, admin_a1_id)

    # The admin's manageable users: exactly the customers of the deployment.
    assert ids_of(body) == sorted([cust_a1_id, cust_a2_id])
    assert {entry["role"] for entry in body} == {"customer"}
    assert {entry["broker_id"] for entry in body} == {1}


def test_admin_cannot_list_admins_or_super_admins(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, *_ = users_db

    body = list_as(factory, admin_a1_id)

    # Neither the other admin nor the deployment's super_admin is ever returned.
    assert admin_a2_id not in ids_of(body)
    assert super_a_id not in ids_of(body)
    assert "admin-2" not in [entry["login"] for entry in body]
    assert "90001" not in [entry["login"] for entry in body]


def test_every_admin_of_the_deployment_sees_the_same_customers(users_db) -> None:
    """In a one-broker deployment the customer set IS the broker's customer set.

    There is no per-admin partition: both admins manage every customer of the
    broker, and neither of them is ever part of that set.
    """
    factory, super_a_id, admin_a1_id, admin_a2_id, *_ = users_db

    first = list_as(factory, admin_a1_id)
    second = list_as(factory, admin_a2_id)
    from_super = list_as(factory, super_a_id)

    assert first == second
    assert [entry for entry in from_super if entry["role"] == "customer"] == first
    assert admin_a1_id not in ids_of(first) and admin_a2_id not in ids_of(first)


# --- a leftover second broker row is not a second tenant ---------------------------


def test_a_leftover_second_broker_row_fails_closed(users_db) -> None:
    """One broker means exactly one: an ambiguous database refuses to serve.

    A stray brokers row (from the multi-broker era, or a bad migration) would
    make "which broker is this deployment?" unanswerable, so the request is
    refused rather than served against an arbitrary pick.
    """
    factory, super_a_id, admin_a1_id, cust_a1_id, *_ = users_db

    async def add_stray_broker() -> None:
        async with factory() as session:
            session.add(Broker(name="Stray Broker", code="TL-STRAY", mt5_server="Stray-Live"))
            await session.commit()

    asyncio.run(add_stray_broker())

    with make_client(factory) as client:
        for user_id in (super_a_id, admin_a1_id, cust_a1_id):
            response = client.get("/users", headers=auth_header(token_for(user_id)))
            assert response.status_code == 401


# --- response contract --------------------------------------------------------------


def test_response_contains_exactly_allowed_fields(users_db) -> None:
    factory, super_a_id, *_ = users_db

    body = list_as(factory, super_a_id)
    assert body, "seeded deployment must contain users"

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


def test_broker_id_query_cannot_change_the_scope(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id = users_db

    # There is no broker_id parameter; supplying one must be ignored, so the
    # listing stays exactly what the authenticated manager may see.
    body = list_as(factory, super_a_id, query="?broker_id=2")

    assert ids_of(body) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
    assert {entry["broker_id"] for entry in body} == {1}


# --- ordering ---------------------------------------------------------------------------


def test_deterministic_ordering_by_id_ascending(users_db) -> None:
    factory, super_a_id, admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id = users_db

    body = list_as(factory, super_a_id)
    ids = ids_of(body)

    # Stable ascending primary-key order, independent of insertion or DB plan.
    assert ids == sorted(ids) == sorted([admin_a1_id, admin_a2_id, cust_a1_id, cust_a2_id])
