"""Tests for the UserRole foundation (Step 13; evolved to the three-role
super_admin/admin/customer model with exactly one super_admin per broker).

Require none of: real PostgreSQL, real MT5, network, or credentials. Each test
uses a per-test file-based async SQLite database with the real User/Broker
models. No pytest asyncio plugin: async setup is driven with asyncio.run.
"""
import asyncio
from typing import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Broker, User, UserRole


@pytest.fixture()
def role_db(tmp_path) -> "async_sessionmaker[AsyncSession]":
    """Per-test file-based SQLite DB with the real schema."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/role_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_schema())
    yield factory
    asyncio.run(engine.dispose())


async def _add_user(factory: async_sessionmaker[AsyncSession], login: str, role: UserRole | None = None) -> int:
    async with factory() as session:
        broker = Broker(name="Test Broker", code=f"TB-{login}")
        session.add(broker)
        await session.flush()
        if role is None:
            # Omitting role exercises the model/server default path explicitly.
            user = User(broker_id=broker.id, login=login, password_hash="x", is_active=True)
        else:
            user = User(broker_id=broker.id, login=login, password_hash="x", is_active=True, role=role)
        session.add(user)
        await session.commit()
        return user.id


async def _get_role(factory: async_sessionmaker[AsyncSession], user_id: int) -> UserRole:
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.role


def test_user_role_enum_values() -> None:
    # Exactly the three roles the architecture currently defines.
    assert {r.value for r in UserRole} == {"super_admin", "admin", "customer"}


def test_role_defaults_to_customer_when_omitted(role_db) -> None:
    # Legacy-style creation (no role argument) must land on the safe default.
    factory = role_db
    user_id = asyncio.run(_add_user(factory, "u-default"))

    assert asyncio.run(_get_role(factory, user_id)) is UserRole.CUSTOMER


def test_role_defaults_to_customer_at_database_level(role_db) -> None:
    # Insert through raw SQL to bypass the ORM default: the server_default must
    # still produce 'customer' for any row written without a role.
    async def raw_insert_and_read() -> UserRole:
        async with role_db() as session:
            broker = Broker(name="Test Broker", code="TB-RAW")
            session.add(broker)
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO users (broker_id, login, password_hash, is_active) "
                    "VALUES (:b, :u, :p, 1)"
                ),
                {"b": broker.id, "u": "raw-user", "p": "x"},
            )
            await session.commit()
            value = (
                await session.execute(text("SELECT role FROM users WHERE login = 'raw-user'"))
            ).scalar_one()
            return UserRole(value)

    assert asyncio.run(raw_insert_and_read()) is UserRole.CUSTOMER


def test_super_admin_role_round_trips(role_db) -> None:
    factory = role_db
    user_id = asyncio.run(_add_user(factory, "u-super", role=UserRole.SUPER_ADMIN))

    assert asyncio.run(_get_role(factory, user_id)) is UserRole.SUPER_ADMIN


def test_admin_role_round_trips(role_db) -> None:
    factory = role_db
    user_id = asyncio.run(_add_user(factory, "u-admin", role=UserRole.ADMIN))

    assert asyncio.run(_get_role(factory, user_id)) is UserRole.ADMIN


def test_customer_role_round_trips(role_db) -> None:
    factory = role_db
    user_id = asyncio.run(_add_user(factory, "u-customer", role=UserRole.CUSTOMER))

    assert asyncio.run(_get_role(factory, user_id)) is UserRole.CUSTOMER


def test_database_rejects_unknown_role_value(role_db) -> None:
    # The CHECK constraint is the database-level boundary: even a raw write
    # with a role value outside the enum domain must be refused.
    async def raw_bad_role() -> None:
        async with role_db() as session:
            broker = Broker(name="Test Broker", code="TB-BAD")
            session.add(broker)
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO users (broker_id, login, password_hash, is_active, role) "
                    "VALUES (:b, :u, :p, 1, 'superuser')"
                ),
                {"b": broker.id, "u": "bad-role", "p": "x"},
            )
            await session.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(raw_bad_role())


# --- exactly one super_admin per broker (database-level invariant) -----------


async def _add_user_in_broker(
    factory: async_sessionmaker[AsyncSession], broker_id: int, login: str, role: UserRole
) -> int:
    async with factory() as session:
        user = User(broker_id=broker_id, login=login, password_hash="x", is_active=True, role=role)
        session.add(user)
        await session.commit()
        return user.id


async def _create_broker_with_user(
    factory: async_sessionmaker[AsyncSession], broker_code: str, login: str, role: UserRole
) -> tuple[int, int]:
    async with factory() as session:
        broker = Broker(name=broker_code, code=broker_code)
        session.add(broker)
        await session.flush()
        user = User(broker_id=broker.id, login=login, password_hash="x", is_active=True, role=role)
        session.add(user)
        await session.commit()
        return broker.id, user.id


def test_second_super_admin_in_same_broker_rejected(role_db) -> None:
    factory = role_db
    broker_id, _ = asyncio.run(_create_broker_with_user(factory, "TB-SUP-A", "super-a", UserRole.SUPER_ADMIN))

    # The partial unique index is the enforcement boundary: a second
    # super_admin row for the same broker must be refused by the database,
    # not merely by API logic.
    with pytest.raises(IntegrityError):
        asyncio.run(_add_user_in_broker(factory, broker_id, "super-b", UserRole.SUPER_ADMIN))


def test_second_super_admin_rejected_via_raw_sql(role_db) -> None:
    factory = role_db
    broker_id, _ = asyncio.run(_create_broker_with_user(factory, "TB-SUP-R", "super-raw", UserRole.SUPER_ADMIN))

    # A raw insert bypasses every application layer: only the database
    # constraint can refuse it, proving the invariant holds at the boundary.
    async def raw_second_super_admin() -> None:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO users (broker_id, login, password_hash, is_active, role) "
                    "VALUES (:b, :u, :p, 1, 'super_admin')"
                ),
                {"b": broker_id, "u": "super-raw-2", "p": "x"},
            )
            await session.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(raw_second_super_admin())


def test_admins_and_customers_do_not_consume_the_super_admin_slot(role_db) -> None:
    factory = role_db
    broker_id, _ = asyncio.run(_create_broker_with_user(factory, "TB-SUP-M", "super-m", UserRole.SUPER_ADMIN))

    # The index is partial: any number of admins and customers may coexist
    # with the single super_admin of the same broker.
    admin_id = asyncio.run(_add_user_in_broker(factory, broker_id, "admin-m", UserRole.ADMIN))
    admin2_id = asyncio.run(_add_user_in_broker(factory, broker_id, "admin-m2", UserRole.ADMIN))
    customer_id = asyncio.run(_add_user_in_broker(factory, broker_id, "customer-m", UserRole.CUSTOMER))

    assert admin_id > 0 and admin2_id > 0 and customer_id > 0


def test_separate_brokers_each_have_one_super_admin(role_db) -> None:
    factory = role_db
    broker_a_id, super_a = asyncio.run(_create_broker_with_user(factory, "TB-SUP-1", "super-1", UserRole.SUPER_ADMIN))
    broker_b_id, super_b = asyncio.run(_create_broker_with_user(factory, "TB-SUP-2", "super-2", UserRole.SUPER_ADMIN))

    # One super_admin per broker is allowed; the two brokers are distinct
    # tenants and never interfere with each other's constraint slot.
    assert super_a > 0 and super_b > 0 and broker_a_id != broker_b_id
