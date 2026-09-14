"""Tests for the UserRole foundation (Step 13).

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


async def _add_user(factory: async_sessionmaker[AsyncSession], username: str, role: UserRole | None = None) -> int:
    async with factory() as session:
        broker = Broker(name="Test Broker", code=f"TB-{username}")
        session.add(broker)
        await session.flush()
        if role is None:
            # Omitting role exercises the model/server default path explicitly.
            user = User(broker_id=broker.id, username=username, password_hash="x", is_active=True)
        else:
            user = User(broker_id=broker.id, username=username, password_hash="x", is_active=True, role=role)
        session.add(user)
        await session.commit()
        return user.id


async def _get_role(factory: async_sessionmaker[AsyncSession], user_id: int) -> UserRole:
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        return user.role


def test_user_role_enum_values() -> None:
    # Exactly the two roles the architecture currently defines.
    assert {r.value for r in UserRole} == {"broker_admin", "customer"}


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
                    "INSERT INTO users (broker_id, username, password_hash, is_active) "
                    "VALUES (:b, :u, :p, 1)"
                ),
                {"b": broker.id, "u": "raw-user", "p": "x"},
            )
            await session.commit()
            value = (
                await session.execute(text("SELECT role FROM users WHERE username = 'raw-user'"))
            ).scalar_one()
            return UserRole(value)

    assert asyncio.run(raw_insert_and_read()) is UserRole.CUSTOMER


def test_broker_admin_role_round_trips(role_db) -> None:
    factory = role_db
    user_id = asyncio.run(_add_user(factory, "u-admin", role=UserRole.BROKER_ADMIN))

    assert asyncio.run(_get_role(factory, user_id)) is UserRole.BROKER_ADMIN


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
                    "INSERT INTO users (broker_id, username, password_hash, is_active, role) "
                    "VALUES (:b, :u, :p, 1, 'superuser')"
                ),
                {"b": broker.id, "u": "bad-role", "p": "x"},
            )
            await session.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(raw_bad_role())
