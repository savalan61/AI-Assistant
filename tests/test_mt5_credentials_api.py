"""Tests for PUT/GET /users/{user_id}/mt5-credentials (Step 38).

Require none of: real PostgreSQL, real MT5, network, or real credentials. The
get_db dependency is overridden with a per-test file-based async SQLite database
built from the real models (so the real column types and the real one-super-admin
partial unique index are in play); the REAL authentication and authorization
dependencies run (real JWT decode + database role check). The encryption key is
generated in-process, so the credential that exercises encryption is a test-only
value and the real Fernet round-trip is what is verified. No pytest asyncio
plugin: async setup is driven with asyncio.run.
"""
import asyncio
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
import app.core.encryption as encryption
from app.api.users_router import router
from app.core.config import settings as app_settings
from app.core.encryption import decrypt_secret, generate_encryption_key
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
# Test-only credential values. Never a real MT5 password: MT5 is not contacted
# anywhere in this module, and the value only has to prove the encryption path.
INVESTOR_SECRET = "investor-read-only-credential-under-test"
REPLACEMENT_SECRET = "replacement-read-only-credential-under-test"
MT5_LOGIN = "30001"
MT5_SERVER = "BrokerTest-Live"
RESPONSE_FIELDS = {"user_id", "username", "mt5_login", "mt5_server", "mt5_configured"}


@pytest.fixture(autouse=True)
def test_only_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only JWT and encryption configuration; no real secret is involved."""
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


@pytest.fixture()
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], dict[str, int]]":
    """Seed two tenants: broker A (super_admin, admin, customer) and broker B.

    Two tenants are required to prove cross-broker isolation rather than merely
    the absence of a broker_id parameter.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/mt5_credentials_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "dict[str, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker_a = Broker(name="Broker A", code="TA-A")
            broker_b = Broker(name="Broker B", code="TA-B")
            session.add_all([broker_a, broker_b])
            await session.flush()
            rows = {
                "super_a": User(
                    broker_id=broker_a.id, username="90001", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.SUPER_ADMIN,
                ),
                "admin_a": User(
                    broker_id=broker_a.id, username="90002", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.ADMIN,
                ),
                "customer_a": User(
                    broker_id=broker_a.id, username="90003", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.CUSTOMER,
                ),
                "super_b": User(
                    broker_id=broker_b.id, username="90004", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.SUPER_ADMIN,
                ),
                "customer_b": User(
                    broker_id=broker_b.id, username="90005", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.CUSTOMER,
                ),
            }
            session.add_all(list(rows.values()))
            await session.commit()
            return {name: user.id for name, user in rows.items()}

    ids = asyncio.run(seed())
    yield factory, ids
    asyncio.run(engine.dispose())


def make_client(factory: async_sessionmaker[AsyncSession]) -> TestClient:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def auth_header(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def credentials_payload(**extra: object) -> dict[str, object]:
    """Write-only MT5 investor-credential payload for provisioning tests."""
    payload: dict[str, object] = {
        "mt5_login": MT5_LOGIN,
        "mt5_server": MT5_SERVER,
        "mt5_investor_password": INVESTOR_SECRET,
    }
    payload.update(extra)
    return payload


def load_user(factory: async_sessionmaker[AsyncSession], user_id: int) -> User:
    """Read one row back in a fresh session (attributes are pre-loaded)."""

    async def load() -> User:
        async with factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            return user

    return asyncio.run(load())


def update_broker(factory: async_sessionmaker[AsyncSession], broker_id: int, **values: object) -> None:
    async def update() -> None:
        async with factory() as session:
            broker = await session.get(Broker, broker_id)
            assert broker is not None
            for field, value in values.items():
                setattr(broker, field, value)
            await session.commit()

    asyncio.run(update())


def credentials_url(user_id: int) -> str:
    return f"/users/{user_id}/mt5-credentials"


# --- authentication / authorization -------------------------------------------


def test_unauthenticated_put_returns_401(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(credentials_url(ids["customer_a"]), json=credentials_payload())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unauthenticated_get_returns_401(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.get(credentials_url(ids["customer_a"]))

    assert response.status_code == 401


def test_customer_cannot_provision_credentials_returns_403(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["customer_a"]),
        )

    assert response.status_code == 403
    # Nothing was written for the caller.
    assert load_user(factory, ids["customer_a"]).mt5_password_encrypted is None


def test_customer_cannot_view_credentials_returns_403(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["customer_a"]))

    assert response.status_code == 403


def test_admin_cannot_provision_another_admin_returns_403(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["admin_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    assert response.status_code == 403
    assert load_user(factory, ids["admin_a"]).mt5_password_encrypted is None


def test_admin_cannot_provision_the_super_admin_returns_403(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["super_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    assert response.status_code == 403


def test_super_admin_may_provision_an_admin_returns_200(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["admin_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["super_a"]),
        )

    assert response.status_code == 200
    assert load_user(factory, ids["admin_a"]).mt5_login == MT5_LOGIN


# --- provisioning contract ----------------------------------------------------


def test_admin_provisions_a_customer_credential(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == RESPONSE_FIELDS
    assert body == {
        "user_id": ids["customer_a"],
        "username": "90003",
        "mt5_login": MT5_LOGIN,
        "mt5_server": MT5_SERVER,
        "mt5_configured": True,
    }
    # The response schema has no password field at all, so the submitted secret
    # can never be echoed back — asserted on the raw text too.
    assert "password" not in response.text
    assert INVESTOR_SECRET not in response.text


def test_super_admin_provisions_a_customer_credential(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["super_a"]),
        )

    assert response.status_code == 200
    assert response.json()["mt5_configured"] is True


def test_credential_is_encrypted_at_rest_and_round_trips(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    stored = load_user(factory, ids["customer_a"]).mt5_password_encrypted
    assert stored is not None
    # Ciphertext, not the plaintext: the raw value neither equals nor contains it.
    assert stored != INVESTOR_SECRET
    assert INVESTOR_SECRET not in stored
    # The real Fernet path is what produced it, so it decrypts back exactly.
    assert decrypt_secret(stored) == INVESTOR_SECRET


def test_provisioning_writes_only_the_credential_columns(users_db) -> None:
    factory, ids = users_db
    before = load_user(factory, ids["customer_a"])

    with make_client(factory) as client:
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    after = load_user(factory, ids["customer_a"])
    assert (after.username, after.password_hash, after.role) == (before.username, before.password_hash, before.role)
    assert (after.broker_id, after.is_active, after.email, after.phone) == (
        before.broker_id,
        before.is_active,
        before.email,
        before.phone,
    )
    assert (after.mt5_login, after.mt5_server, after.mt5_password_encrypted) == (
        MT5_LOGIN,
        MT5_SERVER,
        after.mt5_password_encrypted,
    )
    assert after.mt5_password_encrypted is not None


def test_reprovisioning_replaces_the_previous_credential(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        first = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )
        first_ciphertext = load_user(factory, ids["customer_a"]).mt5_password_encrypted
        second = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(
                mt5_login="30002", mt5_server="BrokerTest-Demo", mt5_investor_password=REPLACEMENT_SECRET
            ),
            headers=auth_header(ids["admin_a"]),
        )

    assert (first.status_code, second.status_code) == (200, 200)
    row = load_user(factory, ids["customer_a"])
    assert (row.mt5_login, row.mt5_server) == ("30002", "BrokerTest-Demo")
    assert row.mt5_password_encrypted is not None
    # A fresh encryption of a different secret: the stored value changed and the
    # replacement — not the original — is what decrypts.
    assert row.mt5_password_encrypted != first_ciphertext
    assert decrypt_secret(row.mt5_password_encrypted) == REPLACEMENT_SECRET


# --- tenant isolation ---------------------------------------------------------


def test_cross_broker_target_is_not_found_and_is_not_modified(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        # Broker A's admin targeting broker B's customer.
        response = client.put(
            credentials_url(ids["customer_b"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    # Indistinguishable from a non-existent user: existence in another tenant is
    # never revealed.
    assert response.status_code == 404
    row = load_user(factory, ids["customer_b"])
    assert (row.mt5_login, row.mt5_server, row.mt5_password_encrypted) == (None, None, None)


def test_cross_broker_read_is_not_found(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.get(credentials_url(ids["customer_b"]), headers=auth_header(ids["super_a"]))

    assert response.status_code == 404


def test_unknown_user_returns_404(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        response = client.get(credentials_url(999999), headers=auth_header(ids["super_a"]))

    assert response.status_code == 404


def test_request_cannot_choose_broker_or_user(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        broker_attempt = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(broker_id=2),
            headers=auth_header(ids["admin_a"]),
        )
        user_attempt = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(user_id=ids["super_a"]),
            headers=auth_header(ids["admin_a"]),
        )

    # extra="forbid": a tenant/identity override is refused, not ignored.
    assert (broker_attempt.status_code, user_attempt.status_code) == (422, 422)


def test_invalid_input_returns_422_and_writes_nothing(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        bad_login = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_login="not-a-number"),
            headers=auth_header(ids["admin_a"]),
        )
        empty_server = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_server="   "),
            headers=auth_header(ids["admin_a"]),
        )
        empty_password = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_investor_password="   "),
            headers=auth_header(ids["admin_a"]),
        )

    assert (bad_login.status_code, empty_server.status_code, empty_password.status_code) == (422, 422, 422)
    # No accepted credential is echoed, and a rejected request stores nothing.
    for response in (bad_login, empty_server, empty_password):
        assert INVESTOR_SECRET not in response.text
        assert "password_hash" not in response.text
    row = load_user(factory, ids["customer_a"])
    assert (row.mt5_login, row.mt5_server, row.mt5_password_encrypted) == (None, None, None)


def test_rejected_value_may_be_echoed_by_validation_but_is_never_stored(users_db) -> None:
    """Pins a known, app-wide limitation instead of hiding it.

    FastAPI's default 422 body includes the offending input under ``input``, so
    a value that FAILS validation is echoed back to the caller who just sent it
    — the same is already true of POST /users' application ``password`` field.
    Nothing is stored, and an ACCEPTED credential is never echoed (see the
    exposure tests above). Closing this fully needs a global
    RequestValidationError handler that strips ``input``/``ctx``, which is
    deliberately not introduced in this step. This test documents the current
    behaviour so it cannot change silently.
    """
    factory, ids = users_db
    over_long = "r" * 200

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_investor_password=over_long),
            headers=auth_header(ids["admin_a"]),
        )

    assert response.status_code == 422
    assert over_long in response.text  # documented limitation (see docstring)
    row = load_user(factory, ids["customer_a"])
    assert row.mt5_password_encrypted is None


def test_missing_encryption_key_fails_closed_and_writes_nothing(
    users_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, ids = users_db
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", "", raising=True)

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    assert response.status_code == 503
    assert INVESTOR_SECRET not in response.text
    row = load_user(factory, ids["customer_a"])
    # Fail closed: no plaintext and no partial write.
    assert (row.mt5_login, row.mt5_server, row.mt5_password_encrypted) == (None, None, None)


# --- status read --------------------------------------------------------------


def test_get_reports_status_without_the_password(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        before = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["admin_a"]))
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )
        after = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["admin_a"]))

    assert before.status_code == 200
    # Broker A has no mt5_server, so the legacy fallback yields no server yet.
    assert before.json() == {
        "user_id": ids["customer_a"],
        "username": "90003",
        "mt5_login": "90003",
        "mt5_server": None,
        "mt5_configured": False,
    }
    assert set(after.json().keys()) == RESPONSE_FIELDS
    assert after.json()["mt5_configured"] is True
    assert "password" not in after.text


def test_legacy_row_with_username_and_broker_server_is_reported_configured(users_db) -> None:
    factory, ids = users_db
    # A row provisioned the pre-Step-38 way: numeric username + broker server +
    # stored password must still count as configured (no regression).
    update_broker(factory, _broker_id_of(factory, ids["customer_a"]), mt5_server="BrokerLegacy-Live")

    async def seed_password() -> None:
        async with factory() as session:
            user = await session.get(User, ids["customer_a"])
            assert user is not None
            user.mt5_password_encrypted = encryption.encrypt_secret(INVESTOR_SECRET)
            await session.commit()

    asyncio.run(seed_password())

    with make_client(factory) as client:
        response = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["admin_a"]))

    assert response.json() == {
        "user_id": ids["customer_a"],
        "username": "90003",
        "mt5_login": "90003",
        "mt5_server": "BrokerLegacy-Live",
        "mt5_configured": True,
    }


def _broker_id_of(factory: async_sessionmaker[AsyncSession], user_id: int) -> int:
    return load_user(factory, user_id).broker_id


# --- resolution precedence (composition root) ---------------------------------


def test_resolution_prefers_the_provisioned_login_and_server() -> None:
    broker = Broker(name="Broker A", code="RA", mt5_server="BrokerFallback-Live")
    user = User(
        broker_id=1,
        username="90003",
        password_hash="x",
        mt5_login=MT5_LOGIN,
        mt5_server=MT5_SERVER,
        mt5_password_encrypted="cipher",
        is_active=True,
        role=UserRole.CUSTOMER,
    )

    resolved = deps.resolve_mt5_account_credentials(user, broker)

    assert (resolved.login, resolved.server) == (int(MT5_LOGIN), MT5_SERVER)
    assert resolved.password_encrypted == "cipher"


def test_resolution_falls_back_to_username_and_broker_server() -> None:
    broker = Broker(name="Broker A", code="RA", mt5_server="BrokerFallback-Live")
    user = User(
        broker_id=1,
        username="90003",
        password_hash="x",
        mt5_password_encrypted="cipher",
        is_active=True,
        role=UserRole.CUSTOMER,
    )

    resolved = deps.resolve_mt5_account_credentials(user, broker)

    assert (resolved.login, resolved.server) == (90003, "BrokerFallback-Live")


def test_non_numeric_username_without_provisioning_is_not_an_mt5_login() -> None:
    broker = Broker(name="Broker A", code="RA", mt5_server="BrokerFallback-Live")
    user = User(
        broker_id=1,
        username="customer-without-mt5",
        password_hash="x",
        mt5_password_encrypted="cipher",
        is_active=True,
        role=UserRole.CUSTOMER,
    )

    assert deps.resolve_mt5_account_credentials(user, broker).login is None


def test_provisioned_secret_reaches_the_session_boundary_masked(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    row = load_user(factory, ids["customer_a"])
    credentials = deps.resolve_mt5_account_credentials(row, Broker(name="B", code="B", mt5_server=MT5_SERVER))

    # The session boundary receives exactly the provisioned identity plus the
    # CIPHERTEXT — and its repr cannot carry the ciphertext either.
    assert (credentials.login, credentials.server) == (int(MT5_LOGIN), MT5_SERVER)
    assert credentials.password_encrypted is not None
    assert INVESTOR_SECRET not in repr(credentials)
    assert credentials.password_encrypted not in repr(credentials)


# --- read-only safety ---------------------------------------------------------


def test_credential_provisioning_introduces_no_trading_operation() -> None:
    import inspect

    from app.api import users_router as users_router_module

    source = inspect.getsource(users_router_module)

    # The provisioning path stores a credential and writes three columns; it
    # must never reach for an MT5 trading function of any kind.
    for forbidden in ("order_send", "order_check", "positions_modify", "orders_modify", "TRADE_ACTION"):
        assert forbidden not in source
