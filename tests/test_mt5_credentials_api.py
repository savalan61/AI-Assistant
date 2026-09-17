"""Tests for PUT/GET /users/{user_id}/mt5-credentials (Step 38, evolved to the
ONE-BROKER architecture).

Require none of: real PostgreSQL, real MT5, network, or real credentials. The
get_db dependency is overridden with a per-test file-based async SQLite database
built from the real models (so the real column types and the real one-super-admin
partial unique index are in play); the REAL authentication and authorization
dependencies run (real JWT decode + database role check). The encryption key is
generated in-process, so the credential that exercises encryption is a test-only
value and the real Fernet round-trip is what is verified. No pytest asyncio
plugin: async setup is driven with asyncio.run.

This deployment serves ONE broker, so there is no cross-broker boundary left to
attack here. The boundary that must hold is the CUSTOMER-to-CUSTOMER one: two
customers of the same broker have two different MT5 accounts, and neither the
API nor a request may make one read the other's account. Every isolation test
below asserts exactly that.
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
# The deployment's ONE MT5 server, configured on the single broker row. It is
# also an MT5 account number that must NEVER be acceptable as request input:
# the account number is the user's own `login`, so a client cannot aim a
# credential — or a read — at a different account or a different server.
BROKER_SERVER = "BrokerTest-Live"
MT5_LOGIN = "30001"
RESPONSE_FIELDS = {"user_id", "login", "mt5_server", "mt5_configured"}


@pytest.fixture(autouse=True)
def test_only_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only JWT and encryption configuration; no real secret is involved."""
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


@pytest.fixture()
def users_db(tmp_path) -> "tuple[async_sessionmaker[AsyncSession], dict[str, int]]":
    """Seed the deployment's ONE broker with a super_admin, an admin and TWO customers.

    Two customers are required to prove CUSTOMER-to-customer isolation: they are
    the same broker's customers, on the same MT5 server, and only their own
    ``login`` (the MT5 account number) tells their accounts apart.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/mt5_credentials_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> "dict[str, int]":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="TA-ONE", mt5_server=BROKER_SERVER)
            session.add(broker)
            await session.flush()
            rows = {
                "super_a": User(
                    broker_id=broker.id, login="90001", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.SUPER_ADMIN,
                ),
                "admin_a": User(
                    broker_id=broker.id, login="90002", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.ADMIN,
                ),
                "customer_a": User(
                    broker_id=broker.id, login="90003", password_hash="x-not-a-real-hash",
                    is_active=True, role=UserRole.CUSTOMER,
                ),
                "customer_b": User(
                    broker_id=broker.id, login="90004", password_hash="x-not-a-real-hash",
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
    """Write-only MT5 investor-credential payload for provisioning tests.

    Deliberately carries NEITHER an account number NOR a server: the MT5 account
    is the target user's own ``login`` and the server is the broker's own
    configuration, so an ``mt5_login`` or ``mt5_server`` key is an unknown field
    that the request model refuses with 422.
    """
    payload: dict[str, object] = {"mt5_investor_password": INVESTOR_SECRET}
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


def load_broker(factory: async_sessionmaker[AsyncSession]) -> Broker:
    """The deployment's one broker row, read back in a fresh session."""

    async def load() -> Broker:
        async with factory() as session:
            broker = await deps.load_deployment_broker(session)
            assert broker is not None
            return broker

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
    assert load_user(factory, ids["admin_a"]).mt5_password_encrypted is not None


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
        "login": "90003",
        "mt5_server": BROKER_SERVER,
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
    broker_before = load_broker(factory)

    with make_client(factory) as client:
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    after = load_user(factory, ids["customer_a"])
    assert (after.login, after.password_hash, after.role) == (before.login, before.password_hash, before.role)
    assert (after.broker_id, after.is_active, after.email, after.phone) == (
        before.broker_id,
        before.is_active,
        before.email,
        before.phone,
    )
    # The account number is the user's own login and is NOT part of what
    # provisioning writes: only the encrypted secret is. There is no server
    # column on the row to write either.
    assert after.login == "90003"
    assert not hasattr(after, "mt5_server")
    assert after.mt5_password_encrypted is not None
    # The broker's own server is untouched by provisioning.
    assert load_broker(factory).mt5_server == broker_before.mt5_server


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
            json=credentials_payload(mt5_investor_password=REPLACEMENT_SECRET),
            headers=auth_header(ids["admin_a"]),
        )

    assert (first.status_code, second.status_code) == (200, 200)
    row = load_user(factory, ids["customer_a"])
    assert row.login == "90003"
    assert row.mt5_password_encrypted is not None
    # A fresh encryption of a different secret: the stored value changed and the
    # replacement — not the original — is what decrypts.
    assert row.mt5_password_encrypted != first_ciphertext
    assert decrypt_secret(row.mt5_password_encrypted) == REPLACEMENT_SECRET


# --- customer-to-customer isolation -------------------------------------------


def test_provisioning_one_customer_never_touches_another(users_db) -> None:
    factory, ids = users_db
    other_before = load_user(factory, ids["customer_b"])

    with make_client(factory) as client:
        # The admin provisions BOTH customers in turn: each write must land on
        # exactly one row — its own target.
        first = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_investor_password=INVESTOR_SECRET),
            headers=auth_header(ids["admin_a"]),
        )
        second = client.put(
            credentials_url(ids["customer_b"]),
            json=credentials_payload(mt5_investor_password=REPLACEMENT_SECRET),
            headers=auth_header(ids["admin_a"]),
        )

    assert (first.status_code, second.status_code) == (200, 200)
    target = load_user(factory, ids["customer_a"])
    other_after = load_user(factory, ids["customer_b"])
    assert decrypt_secret(target.mt5_password_encrypted or "") == INVESTOR_SECRET
    assert decrypt_secret(other_after.mt5_password_encrypted or "") == REPLACEMENT_SECRET
    # The other customer's row is otherwise byte-for-byte what it was.
    assert (other_after.login, other_after.role, other_after.broker_id) == (
        other_before.login,
        other_before.role,
        other_before.broker_id,
    )


def test_customer_cannot_read_another_customers_credential_status(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )
        # Customer B asking about customer A: refused by role, before the target
        # is even resolved, so nothing about A's configuration is disclosed.
        response = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["customer_b"]))

    assert response.status_code == 403
    assert INVESTOR_SECRET not in response.text
    assert "90003" not in response.text


def test_each_customer_resolves_to_its_own_mt5_account(users_db) -> None:
    """The one identity that must never be shared between two customers."""
    factory, ids = users_db

    with make_client(factory) as client:
        client.put(credentials_url(ids["customer_a"]), json=credentials_payload(), headers=auth_header(ids["admin_a"]))
        client.put(credentials_url(ids["customer_b"]), json=credentials_payload(), headers=auth_header(ids["admin_a"]))

    broker = load_broker(factory)
    customer_a = deps.resolve_mt5_account_credentials(load_user(factory, ids["customer_a"]), broker)
    customer_b = deps.resolve_mt5_account_credentials(load_user(factory, ids["customer_b"]), broker)

    # Same broker, same server, two different accounts — each its own login.
    assert (customer_a.login, customer_b.login) == (90003, 90004)
    assert customer_a.login != customer_b.login
    assert (customer_a.server, customer_b.server) == (BROKER_SERVER, BROKER_SERVER)
    # And two independently encrypted secrets, neither usable as the other.
    assert customer_a.password_encrypted != customer_b.password_encrypted


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
        # Every attempt to re-aim the credential is refused outright — even with
        # well-formed values — so provisioning can never target another account
        # or another server.
        account_override = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_login=MT5_LOGIN),
            headers=auth_header(ids["admin_a"]),
        )
        server_override = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_server="AnotherBroker-Live"),
            headers=auth_header(ids["admin_a"]),
        )
        empty_password = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(mt5_investor_password="   "),
            headers=auth_header(ids["admin_a"]),
        )

    assert (account_override.status_code, server_override.status_code, empty_password.status_code) == (422, 422, 422)
    # No accepted credential is echoed, and a rejected request stores nothing.
    for response in (account_override, server_override, empty_password):
        assert INVESTOR_SECRET not in response.text
        assert "password_hash" not in response.text
    row = load_user(factory, ids["customer_a"])
    assert (row.login, row.mt5_password_encrypted) == ("90003", None)


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
    assert row.mt5_password_encrypted is None


def test_provisioning_refuses_when_the_deployment_has_no_mt5_server(users_db) -> None:
    """A one-broker deployment IS one MT5 server; without it nothing can work."""
    factory, ids = users_db
    update_broker(factory, load_user(factory, ids["customer_a"]).broker_id, mt5_server=None)

    with make_client(factory) as client:
        response = client.put(
            credentials_url(ids["customer_a"]),
            json=credentials_payload(),
            headers=auth_header(ids["admin_a"]),
        )

    # The established generic 503: which setting is missing stays server-side.
    assert response.status_code == 503
    assert INVESTOR_SECRET not in response.text
    assert load_user(factory, ids["customer_a"]).mt5_password_encrypted is None


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
    # The server is the deployment's own, known before any credential exists;
    # what the first call reports as not yet configured is the SECRET.
    assert before.json() == {
        "user_id": ids["customer_a"],
        "login": "90003",
        "mt5_server": BROKER_SERVER,
        "mt5_configured": False,
    }
    assert set(after.json().keys()) == RESPONSE_FIELDS
    assert after.json()["mt5_configured"] is True
    assert "password" not in after.text


def test_admin_sees_the_status_of_every_customer_it_manages(users_db) -> None:
    factory, ids = users_db

    with make_client(factory) as client:
        client.put(credentials_url(ids["customer_a"]), json=credentials_payload(), headers=auth_header(ids["admin_a"]))
        customer_a = client.get(credentials_url(ids["customer_a"]), headers=auth_header(ids["admin_a"]))
        customer_b = client.get(credentials_url(ids["customer_b"]), headers=auth_header(ids["admin_a"]))

    assert (customer_a.status_code, customer_b.status_code) == (200, 200)
    # Each status answers for its own account, and only the provisioned one
    # reports configured.
    assert customer_a.json()["login"] == "90003" and customer_a.json()["mt5_configured"] is True
    assert customer_b.json()["login"] == "90004" and customer_b.json()["mt5_configured"] is False


def test_row_with_broker_level_server_and_password_is_reported_configured(users_db) -> None:
    factory, ids = users_db
    # The account number is the user's own login and the server is the broker's
    # configuration, so a stored secret on the row is the whole credential.
    update_broker(factory, load_user(factory, ids["customer_a"]).broker_id, mt5_server="BrokerLegacy-Live")

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
        "login": "90003",
        "mt5_server": "BrokerLegacy-Live",
        "mt5_configured": True,
    }


# --- resolution precedence (composition root) ---------------------------------


def test_resolution_uses_the_login_and_the_broker_server() -> None:
    broker = Broker(name="The Broker", code="RA", mt5_server="BrokerFallback-Live")
    user = User(
        broker_id=1,
        login="90003",
        password_hash="x",
        mt5_password_encrypted="cipher",
        is_active=True,
        role=UserRole.CUSTOMER,
    )

    resolved = deps.resolve_mt5_account_credentials(user, broker)

    assert (resolved.login, resolved.server) == (90003, "BrokerFallback-Live")
    assert resolved.password_encrypted == "cipher"


def test_a_user_row_cannot_carry_an_mt5_server() -> None:
    """The per-user server override is structurally gone, not merely ignored."""
    user = User(
        broker_id=1,
        login="90003",
        password_hash="x",
        mt5_password_encrypted="cipher",
        is_active=True,
        role=UserRole.CUSTOMER,
    )

    assert not hasattr(user, "mt5_server")
    assert not hasattr(user, "mt5_login")


def test_the_server_is_only_the_brokers_own() -> None:
    """No broker and a blank broker both fail closed; nothing else sets a server."""
    assert deps.effective_mt5_server(None) is None
    assert deps.effective_mt5_server(Broker(name="B", code="B", mt5_server="   ")) is None
    assert deps.effective_mt5_server(Broker(name="B", code="B", mt5_server=" Broker-Live ")) == "Broker-Live"


def test_non_numeric_login_is_not_an_mt5_login() -> None:
    broker = Broker(name="The Broker", code="RA", mt5_server="BrokerFallback-Live")
    user = User(
        broker_id=1,
        login="customer-without-mt5",
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
    credentials = deps.resolve_mt5_account_credentials(row, Broker(name="B", code="B", mt5_server=BROKER_SERVER))

    # The session boundary receives exactly the provisioned identity plus the
    # CIPHERTEXT — and its repr cannot carry the ciphertext either.
    assert (credentials.login, credentials.server) == (90003, BROKER_SERVER)
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
