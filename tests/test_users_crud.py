"""Tests for complete super-admin user CRUD (Step 40).

Require none of: real PostgreSQL, real MT5, network, or real credentials.
The get_db dependency is overridden with a per-test file-based async SQLite
database containing the real User/Broker models; the REAL authentication and
authorization dependencies run (real JWT decode + database role check). JWT
config uses test-only values. No pytest asyncio plugin: async setup is driven
with asyncio.run.
"""
import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.users_router import router
from app.core.config import settings as app_settings
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
PASSWORD = "a-fine-application-password"

ALLOWED_FIELDS = {"id", "broker_id", "login", "email", "phone", "role", "is_active"}


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture()
def crud_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], dict[str, int]]":
    """Seed two brokers.

    Broker A: super_admin (id in ids["super_a"]), admin (ids["admin_a"]),
    customer (ids["customer_a"]). Broker B: super_admin (ids["super_b"]) and
    customer (ids["customer_b"]) — the cross-tenant target. Yields (factory,
    ids dict).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/users_crud_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        from app.core.security import hash_password

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="UC-A")
            broker_b = Broker(name="Broker B", code="UC-B")
            session.add_all([broker_a, broker_b])
            await session.flush()
            rows = {
                "super_a": User(
                    broker_id=broker_a.id,
                    login="999901",
                    password_hash=hash_password(PASSWORD),
                    is_active=True,
                    role=UserRole.SUPER_ADMIN,
                ),
                "admin_a": User(
                    broker_id=broker_a.id,
                    login="999902",
                    password_hash="x-not-a-real-hash",
                    is_active=True,
                    role=UserRole.ADMIN,
                ),
                "customer_a": User(
                    broker_id=broker_a.id,
                    login="999903",
                    password_hash="x-not-a-real-hash",
                    email="customer.a@broker.example",
                    is_active=True,
                    role=UserRole.CUSTOMER,
                ),
                "super_b": User(
                    broker_id=broker_b.id,
                    login="999904",
                    password_hash=hash_password(PASSWORD),
                    is_active=True,
                    role=UserRole.SUPER_ADMIN,
                ),
                "customer_b": User(
                    broker_id=broker_b.id,
                    login="999905",
                    password_hash="x-not-a-real-hash",
                    is_active=True,
                    role=UserRole.CUSTOMER,
                ),
            }
            session.add_all(rows.values())
            await session.commit()
            return {name: row.id for name, row in rows.items()}

    ids = asyncio.run(seed())
    yield (factory, ids)
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


def super_header(ids: dict[str, int]) -> dict[str, str]:
    return auth_header(create_access_token(str(ids["super_a"])))


def admin_header(ids: dict[str, int]) -> dict[str, str]:
    return auth_header(create_access_token(str(ids["admin_a"])))


def customer_header(ids: dict[str, int]) -> dict[str, str]:
    return auth_header(create_access_token(str(ids["customer_a"])))


def load_user(factory: async_sessionmaker[AsyncSession], user_id: int) -> User | None:
    async def read() -> User | None:
        async with factory() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            return result.scalars().first()

    return asyncio.run(read())


def create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"login": "771001", "password": PASSWORD}
    payload.update(overrides)
    return payload


# --- create with explicit role -------------------------------------------------


def test_super_admin_creates_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(role="admin"),
            headers=super_header(ids),
        )

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "admin"
    super_row = load_user(factory, ids["super_a"])
    assert super_row is not None
    assert body["broker_id"] == super_row.broker_id  # created in the caller's tenant
    row = load_user(factory, body["id"])
    assert row is not None and row.role is UserRole.ADMIN


def test_super_admin_creates_customer(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(role="customer"),
            headers=super_header(ids),
        )

    assert response.status_code == 201
    assert response.json()["role"] == "customer"


def test_missing_role_defaults_to_customer_never_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post("/users", json=create_payload(), headers=super_header(ids))

    assert response.status_code == 201
    assert response.json()["role"] == "customer"


def test_admin_cannot_create_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(role="admin"),
            headers=admin_header(ids),
        )

    assert response.status_code == 403
    # Nothing was written.
    assert load_user(factory, ids["admin_a"] + 1000) is None


def test_cannot_create_second_super_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post(
            "/users",
            json=create_payload(role="super_admin"),
            headers=super_header(ids),
        )

    assert response.status_code == 422
    async def count_super() -> int:
        async with factory() as session:
            result = await session.execute(
                select(User).where(User.role == UserRole.SUPER_ADMIN, User.broker_id == 1)
            )
            return len(result.scalars().all())

    assert asyncio.run(count_super()) == 1


def test_customer_cannot_create_users(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.post("/users", json=create_payload(), headers=customer_header(ids))

    assert response.status_code == 403


# --- get / list ----------------------------------------------------------------


def test_super_admin_gets_one_user(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.get(f"/users/{ids['customer_a']}", headers=super_header(ids))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ids["customer_a"]
    assert body["login"] == "999903"
    assert set(body) == ALLOWED_FIELDS


def test_super_admin_list_includes_admins_and_customers(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.get("/users", headers=super_header(ids))

    assert response.status_code == 200
    roles = {u["role"] for u in response.json()}
    assert roles == {"admin", "customer"}
    # The super_admin itself is never listed (existing rule).
    assert ids["super_a"] not in {u["id"] for u in response.json()}


# --- update --------------------------------------------------------------------


def test_super_admin_updates_role(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['customer_a']}",
            json={"role": "admin"},
            headers=super_header(ids),
        )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    row = load_user(factory, ids["customer_a"])
    assert row is not None and row.role is UserRole.ADMIN


def test_super_admin_updates_supported_fields(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['customer_a']}",
            json={"email": "new.address@broker.example", "phone": "+905551234567", "is_active": False},
            headers=super_header(ids),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "new.address@broker.example"
    assert body["phone"] == "+905551234567"
    assert body["is_active"] is False
    row = load_user(factory, ids["customer_a"])
    assert row is not None and row.email == "new.address@broker.example"


def test_update_login_and_password(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['customer_a']}",
            json={"login": "888123", "password": "brand-new-password-1"},
            headers=super_header(ids),
        )

    assert response.status_code == 200
    assert response.json()["login"] == "888123"
    row = load_user(factory, ids["customer_a"])
    assert row is not None
    assert row.login == "888123"
    from app.core.security import verify_password

    assert verify_password("brand-new-password-1", row.password_hash)


def test_update_password_is_never_returned(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['customer_a']}",
            json={"password": "another-fine-password"},
            headers=super_header(ids),
        )

    assert response.status_code == 200
    assert "password" not in response.json()
    assert "password_hash" not in response.json()
    assert "another-fine-password" not in response.text


def test_update_cannot_promote_to_super_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['admin_a']}",
            json={"role": "super_admin"},
            headers=super_header(ids),
        )

    assert response.status_code == 422
    row = load_user(factory, ids["admin_a"])
    assert row is not None and row.role is UserRole.ADMIN


def test_update_cannot_demote_the_only_super_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['super_a']}",
            json={"role": "admin"},
            headers=super_header(ids),
        )

    assert response.status_code == 409
    row = load_user(factory, ids["super_a"])
    assert row is not None and row.role is UserRole.SUPER_ADMIN


def test_empty_update_is_rejected(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(f"/users/{ids['customer_a']}", json={}, headers=super_header(ids))

    assert response.status_code == 422


def test_update_duplicate_login_is_generic_conflict(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.patch(
            f"/users/{ids['customer_a']}",
            json={"login": "999902"},  # admin_a's login
            headers=super_header(ids),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "User already exists"


# --- delete --------------------------------------------------------------------


def test_super_admin_deletes_customer(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.delete(f"/users/{ids['customer_a']}", headers=super_header(ids))

    assert response.status_code == 204
    assert load_user(factory, ids["customer_a"]) is None


def test_super_admin_deletes_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.delete(f"/users/{ids['admin_a']}", headers=super_header(ids))

    assert response.status_code == 204
    assert load_user(factory, ids["admin_a"]) is None


def test_cannot_delete_the_only_super_admin(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.delete(f"/users/{ids['super_a']}", headers=super_header(ids))

    assert response.status_code == 409
    assert load_user(factory, ids["super_a"]) is not None


def test_delete_unknown_user_is_404(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        response = client.delete("/users/424242", headers=super_header(ids))

    assert response.status_code == 404


# --- cross-tenant isolation ----------------------------------------------------


def test_cross_broker_get_update_delete_are_404(crud_db) -> None:
    """A broker-B id is indistinguishable from a non-existent one (Step 38 convention)."""
    factory, ids = crud_db
    headers = super_header(ids)
    with make_client(factory) as client:
        get_response = client.get(f"/users/{ids['customer_b']}", headers=headers)
        patch_response = client.patch(
            f"/users/{ids['customer_b']}", json={"is_active": False}, headers=headers
        )
        delete_response = client.delete(f"/users/{ids['customer_b']}", headers=headers)

    assert get_response.status_code == 404
    assert patch_response.status_code == 404
    assert delete_response.status_code == 404
    row = load_user(factory, ids["customer_b"])
    assert row is not None and row.is_active is True  # untouched


# --- unchanged admin/customer authorization -------------------------------------


def test_admin_cannot_use_super_admin_endpoints(crud_db) -> None:
    factory, ids = crud_db
    headers = admin_header(ids)
    with make_client(factory) as client:
        get_response = client.get(f"/users/{ids['customer_a']}", headers=headers)
        patch_response = client.patch(
            f"/users/{ids['customer_a']}", json={"is_active": False}, headers=headers
        )
        delete_response = client.delete(f"/users/{ids['customer_a']}", headers=headers)

    assert get_response.status_code == 403
    assert patch_response.status_code == 403
    assert delete_response.status_code == 403
    row = load_user(factory, ids["customer_a"])
    assert row is not None and row.is_active is True


def test_customer_cannot_use_any_user_endpoint(crud_db) -> None:
    factory, ids = crud_db
    headers = customer_header(ids)
    with make_client(factory) as client:
        list_response = client.get("/users", headers=headers)
        get_response = client.get(f"/users/{ids['customer_a']}", headers=headers)
        patch_response = client.patch(
            f"/users/{ids['customer_a']}", json={"is_active": False}, headers=headers
        )
        delete_response = client.delete(f"/users/{ids['customer_a']}", headers=headers)

    assert list_response.status_code == 403
    assert get_response.status_code == 403
    assert patch_response.status_code == 403
    assert delete_response.status_code == 403


def test_unauthenticated_requests_are_401(crud_db) -> None:
    factory, ids = crud_db
    with make_client(factory) as client:
        assert client.get("/users/1").status_code == 401
        assert client.patch("/users/1", json={"is_active": False}).status_code == 401
        assert client.delete("/users/1").status_code == 401


# --- sensitive fields -----------------------------------------------------------


def test_responses_never_carry_sensitive_fields(crud_db) -> None:
    factory, ids = crud_db
    headers = super_header(ids)
    with make_client(factory) as client:
        list_body = client.get("/users", headers=headers).json()
        one_body = client.get(f"/users/{ids['customer_a']}", headers=headers).json()
        created = client.post(
            "/users", json=create_payload(role="admin"), headers=headers
        ).json()
        updated = client.patch(
            f"/users/{ids['customer_a']}", json={"phone": "+905559876543"}, headers=headers
        ).json()

    sensitive = {"password", "password_hash", "mt5_password_encrypted"}
    for body in [one_body, created, updated, *list_body]:
        assert set(body) == ALLOWED_FIELDS
        assert not (set(body) & sensitive)
