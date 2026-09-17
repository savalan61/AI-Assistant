"""Tests for the /auth/login endpoint in the ONE-BROKER deployment.

Require none of: real PostgreSQL, real MT5, network access, or real
credentials. The get_db dependency is overridden with a per-test file-based
async SQLite database containing the real User/Broker models; JWT config uses
test-only values. No pytest asyncio plugin: async setup is driven with
asyncio.run.

The endpoint has NO tenant selector: this deployment serves one broker, which
the server resolves from its own configuration, so the client sends only its
own credentials. Two CUSTOMERS of that one broker (two different MT5 accounts,
each with its own application password) are what the tests below separate:
authenticating one must never authenticate, block or throttle the other.
"""
import asyncio
from typing import AsyncIterator, Iterator, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.api.auth_router as auth_router
import app.core.dependencies as deps
from app.api.auth_router import router
from app.core.config import settings as app_settings
from app.core.security import decode_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"
TEST_PASSWORD = "correct horse battery staple"

# The deployment's one broker, and two of its customers: same broker, same MT5
# server, two different MT5 accounts and two different application passwords.
BROKER_CODE = "TB-1"
CUSTOMER_ONE_LOGIN = "80009"
CUSTOMER_TWO_LOGIN = "80010"
OTHER_PASSWORD = "second customer application password"

IP_ONE = ("1.1.1.1", 50000)
IP_TWO = ("2.2.2.2", 50000)


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


@pytest.fixture(autouse=True)
def fresh_login_throttle() -> Iterator[None]:
    # The throttle is process-wide state by design, so each test starts from a
    # clean one (the established reset_agent_usage_limiter pattern).
    deps.reset_login_throttle()
    yield
    deps.reset_login_throttle()


@pytest.fixture()
def small_login_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Shrink the lockout threshold so it can be reached in a few requests."""
    monkeypatch.setattr(app_settings, "LOGIN_MAX_FAILURES", 3, raising=True)
    deps.reset_login_throttle()
    yield 3
    deps.reset_login_throttle()


class SeededAuthDb(NamedTuple):
    factory: async_sessionmaker[AsyncSession]
    broker_id: int
    user_one_id: int
    user_two_id: int


@pytest.fixture()
def auth_db(tmp_path) -> Iterator[SeededAuthDb]:
    """Seed the deployment's ONE broker with two customers."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/login_test.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> SeededAuthDb:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="Test Broker", code=BROKER_CODE, mt5_server="TestBroker-Live")
            session.add(broker)
            await session.commit()
            user_one = User(
                broker_id=broker.id,
                login=CUSTOMER_ONE_LOGIN,
                # Real bcrypt hashes created via the app's own primitive.
                password_hash=hash_for_test(TEST_PASSWORD),
                is_active=True,
            )
            user_two = User(
                broker_id=broker.id,
                login=CUSTOMER_TWO_LOGIN,
                password_hash=hash_for_test(OTHER_PASSWORD),
                is_active=True,
            )
            session.add_all([user_one, user_two])
            await session.commit()
            return SeededAuthDb(factory, broker.id, user_one.id, user_two.id)

    seeded = asyncio.run(seed())
    yield seeded
    asyncio.run(engine.dispose())


def hash_for_test(password: str) -> str:
    # Local helper (kept out of fixtures) so the hash call stays explicit.
    from app.core.security import hash_password

    return hash_password(password)


def make_client(
    factory: async_sessionmaker[AsyncSession],
    client_address: tuple[str, int] = IP_ONE,
) -> TestClient:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    # client_address is the source address the throttle counts: distinct
    # addresses keep the per-IP bucket out of the customer-isolation assertions.
    return TestClient(app, client=client_address)


def login_payload(
    login: str = CUSTOMER_ONE_LOGIN,
    password: str = TEST_PASSWORD,
    **extra: str,
) -> dict[str, str]:
    payload = {"login": login, "password": password}
    payload.update(extra)
    return payload


def set_active(factory: async_sessionmaker[AsyncSession], model: type, row_id: int, active: bool) -> None:
    async def mutate() -> None:
        async with factory() as session:
            row = await session.get(model, row_id)
            assert row is not None
            row.is_active = active
            await session.commit()

    asyncio.run(mutate())


# --- success path -------------------------------------------------------------


def test_valid_credentials_return_200_with_token(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_returned_token_decodes_with_existing_decode_token(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        token = client.post("/auth/login", json=login_payload()).json()["access_token"]

    payload = decode_token(token)  # validates signature + expiry
    assert payload["sub"].isdigit()


def test_token_subject_equals_user_id(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        token = client.post("/auth/login", json=login_payload()).json()["access_token"]

    assert decode_token(token)["sub"] == str(auth_db.user_one_id)


def test_each_customer_authenticates_as_itself(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        token_one = client.post("/auth/login", json=login_payload()).json()["access_token"]
        token_two = (
            client.post("/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=OTHER_PASSWORD))
            .json()["access_token"]
        )

    # Two accounts of one broker, and each token names only its own user.
    assert decode_token(token_one)["sub"] == str(auth_db.user_one_id)
    assert decode_token(token_two)["sub"] == str(auth_db.user_two_id)
    assert auth_db.user_one_id != auth_db.user_two_id


def test_each_customer_accepts_only_its_own_password(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        # Customer two's password must not open customer one's account, nor the
        # reverse: the credential is bound to the account it was stored for.
        one_with_two_password = client.post("/auth/login", json=login_payload(password=OTHER_PASSWORD))
        two_with_one_password = client.post(
            "/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=TEST_PASSWORD)
        )

    assert one_with_two_password.status_code == two_with_one_password.status_code == 401


def test_a_submitted_broker_value_is_ignored_not_honoured(auth_db: SeededAuthDb):
    """The tenant selector is gone: a stale field can neither help nor block.

    The deployment's broker is resolved server-side, so an unknown, blank or
    mismatched ``broker`` value cannot authenticate anything on its own — and
    cannot stop a legitimate customer either. Nothing the client sends selects
    a customer, so the field is simply not part of the contract.
    """
    with make_client(auth_db.factory) as client:
        unknown = client.post("/auth/login", json=login_payload(broker="NO-SUCH-BROKER"))
        blank = client.post("/auth/login", json=login_payload(broker="   "))
        matching = client.post("/auth/login", json=login_payload(broker=BROKER_CODE))

    # All three are the deployment's own credentials, so all three authenticate
    # the SAME user: the field carries no authority in either direction.
    assert (unknown.status_code, blank.status_code, matching.status_code) == (200, 200, 200)
    subjects = {decode_token(response.json()["access_token"])["sub"] for response in (unknown, blank, matching)}
    assert subjects == {str(auth_db.user_one_id)}

    # And it cannot smuggle a different customer in, even with a correct password.
    with make_client(auth_db.factory) as client:
        wrong_login = client.post(
            "/auth/login", json=login_payload(login=CUSTOMER_ONE_LOGIN, broker=BROKER_CODE)
        )

    assert wrong_login.status_code == 200
    assert decode_token(wrong_login.json()["access_token"])["sub"] == str(auth_db.user_one_id)


# --- generic failure paths (all indistinguishable 401s) ------------------------


def test_wrong_password_returns_401(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(password="wrong password"))

    assert response.status_code == 401


def test_nonexistent_login_returns_401(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(login="99999999"))

    assert response.status_code == 401


def test_inactive_user_returns_401(auth_db: SeededAuthDb):
    set_active(auth_db.factory, User, auth_db.user_one_id, active=False)

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 401


def test_inactive_customer_does_not_block_another_customer(auth_db: SeededAuthDb):
    """One suspended account must never take the whole broker's login down."""
    set_active(auth_db.factory, User, auth_db.user_one_id, active=False)

    with make_client(auth_db.factory) as client:
        blocked = client.post("/auth/login", json=login_payload())
        other_customer = client.post(
            "/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=OTHER_PASSWORD)
        )

    assert blocked.status_code == 401
    assert other_customer.status_code == 200


def test_inactive_broker_returns_401(auth_db: SeededAuthDb):
    """A suspended broker fails closed for every customer — there is no other."""
    set_active(auth_db.factory, Broker, auth_db.broker_id, active=False)

    with make_client(auth_db.factory) as client:
        first = client.post("/auth/login", json=login_payload())
        second = client.post("/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=OTHER_PASSWORD))

    assert (first.status_code, second.status_code) == (401, 401)


def test_a_leftover_second_broker_row_fails_closed(auth_db: SeededAuthDb):
    """One broker means exactly one: an ambiguous database authenticates nobody."""
    async def add_stray_broker() -> None:
        async with auth_db.factory() as session:
            session.add(Broker(name="Stray Broker", code="TB-STRAY", mt5_server="Stray-Live"))
            await session.commit()

    asyncio.run(add_stray_broker())

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 401


# --- failure-response hygiene ---------------------------------------------------


FAILURE_CASES = {
    "wrong password": {"password": "wrong password"},
    "unknown login": {"login": "99999999"},
}


@pytest.mark.parametrize("reason", list(FAILURE_CASES))
def test_all_failure_paths_are_indistinguishable(auth_db: SeededAuthDb, reason: str):
    payload = login_payload(**FAILURE_CASES[reason])

    with make_client(auth_db.factory) as client:
        failure = client.post("/auth/login", json=payload)
        baseline = client.post("/auth/login", json=login_payload(password="wrong password"))

    assert failure.status_code == baseline.status_code == 401
    # Identical body and challenge for every rejection path, so the response
    # cannot be used to tell logins, accounts or states apart.
    assert failure.json() == baseline.json()
    assert failure.headers["www-authenticate"] == "Bearer"


def test_error_response_echoes_no_submitted_identity(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(login="99999999"))

    body = str(response.json())
    assert "99999999" not in body
    assert CUSTOMER_ONE_LOGIN not in body


def test_error_response_exposes_no_credentials_or_token(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(password="wrong password"))

    body = str(response.json())
    assert TEST_PASSWORD not in body
    assert "password_hash" not in body
    assert "$2b$" not in body
    assert "mt5_password" not in body


def test_success_response_exposes_only_token_fields(auth_db: SeededAuthDb):
    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert set(response.json().keys()) == {"access_token", "token_type"}


# --- request validation ---------------------------------------------------------


@pytest.mark.parametrize("missing", ["login", "password"])
def test_each_credential_field_is_required(auth_db: SeededAuthDb, missing: str):
    payload = login_payload()
    del payload[missing]

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=payload)

    assert response.status_code == 422
    assert missing in response.text


# --- timing hardening -----------------------------------------------------------


def test_unknown_login_still_performs_password_verification(auth_db: SeededAuthDb, monkeypatch):
    # No stored hash exists for an unknown login, so the endpoint must spend a
    # dummy verification instead of returning early: an early return is
    # measurably faster and would reveal which logins exist.
    calls: list[str] = []
    real_dummy = auth_router.dummy_password_verification

    def spy(password: str) -> None:
        calls.append(password)
        real_dummy(password)

    monkeypatch.setattr(auth_router, "dummy_password_verification", spy)

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(login="99999999"))

    assert response.status_code == 401
    assert calls == [TEST_PASSWORD]


def test_an_unusable_deployment_broker_still_performs_password_verification(auth_db: SeededAuthDb, monkeypatch):
    """A missing/suspended broker must not be distinguishable by timing either."""
    set_active(auth_db.factory, Broker, auth_db.broker_id, active=False)

    calls: list[str] = []
    real_dummy = auth_router.dummy_password_verification

    def spy(password: str) -> None:
        calls.append(password)
        real_dummy(password)

    monkeypatch.setattr(auth_router, "dummy_password_verification", spy)

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload())

    assert response.status_code == 401
    assert calls == [TEST_PASSWORD]


def test_known_login_verifies_the_stored_hash_and_needs_no_dummy(auth_db: SeededAuthDb, monkeypatch):
    verified_hashes: list[str] = []
    real_verify = auth_router.verify_password

    def spy(password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return real_verify(password, password_hash)

    monkeypatch.setattr(auth_router, "verify_password", spy)

    def unexpected_dummy(password: str) -> None:
        raise AssertionError("a stored hash was available, so no dummy check is expected")

    monkeypatch.setattr(auth_router, "dummy_password_verification", unexpected_dummy)

    with make_client(auth_db.factory) as client:
        response = client.post("/auth/login", json=login_payload(password="wrong password"))

    assert response.status_code == 401
    assert len(verified_hashes) == 1
    assert verified_hashes[0].startswith("$2b$")  # the user's real bcrypt hash


# --- brute-force protection -----------------------------------------------------------


def statuses_for(client: TestClient, attempts: int, **overrides: str) -> list[int]:
    return [client.post("/auth/login", json=login_payload(**overrides)).status_code for _ in range(attempts)]


def test_repeated_failures_eventually_return_429(auth_db: SeededAuthDb, small_login_limit):
    with make_client(auth_db.factory) as client:
        statuses = statuses_for(client, small_login_limit + 1, password="wrong password")

    assert statuses[:-1] == [401] * small_login_limit
    assert statuses[-1] == 429


def test_throttle_is_identical_for_existing_and_unknown_logins(auth_db: SeededAuthDb, small_login_limit):
    with make_client(auth_db.factory) as client:
        existing = statuses_for(client, small_login_limit + 1, password="wrong password")

    # Same keys, fresh counters: the unknown login must behave identically,
    # so the lockout cannot be used to probe which accounts exist.
    deps.reset_login_throttle()
    with make_client(auth_db.factory) as client:
        unknown = statuses_for(client, small_login_limit + 1, login="99999999", password="wrong password")

    assert existing == unknown


def test_throttle_applies_even_to_a_correct_password(auth_db: SeededAuthDb, small_login_limit):
    with make_client(auth_db.factory) as client:
        failed = statuses_for(client, small_login_limit, password="wrong password")
        blocked = client.post("/auth/login", json=login_payload())

    assert failed == [401] * small_login_limit
    # The lockout is deliberate: it cannot be bypassed by suddenly knowing the
    # password, which is what makes it effective against guessing.
    assert blocked.status_code == 429


def test_successful_login_clears_failed_attempts(auth_db: SeededAuthDb, small_login_limit):
    with make_client(auth_db.factory) as client:
        assert statuses_for(client, small_login_limit - 1, password="wrong password") == [401] * (small_login_limit - 1)
        assert client.post("/auth/login", json=login_payload()).status_code == 200
        # Counters were cleared by the success, so failing again is not 429.
        assert client.post("/auth/login", json=login_payload(password="wrong password")).status_code == 401


def test_throttled_response_is_generic_and_secret_free(auth_db: SeededAuthDb, small_login_limit):
    with make_client(auth_db.factory) as client:
        statuses_for(client, small_login_limit, password="wrong password")
        response = client.post("/auth/login", json=login_payload())

    body = str(response.json())
    assert response.status_code == 429
    # The body is exactly the fixed generic message: it names neither the
    # submitted login (asserted below) nor any internal detail.
    assert body == str({"detail": "Too many failed login attempts; try again later"})
    assert CUSTOMER_ONE_LOGIN not in body
    assert TEST_PASSWORD not in body
    assert "99999999" not in body
    assert "password_hash" not in body
    assert "$2b$" not in body


def test_throttle_of_one_customer_does_not_block_another_customer(auth_db: SeededAuthDb, small_login_limit):
    # Exhaust customer one's login bucket from one address...
    with make_client(auth_db.factory, client_address=IP_ONE) as exhausting_client:
        statuses = statuses_for(exhausting_client, small_login_limit, password="wrong password")
        assert statuses == [401] * small_login_limit

    # ...then observe the other customer from a clean address. Customer one is
    # blocked by its own login bucket (the address itself has no failures),
    # while the other account of the same broker is untouched.
    with make_client(auth_db.factory, client_address=IP_TWO) as fresh_client:
        same_customer = fresh_client.post("/auth/login", json=login_payload())
        other_customer = fresh_client.post(
            "/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=OTHER_PASSWORD)
        )

    assert same_customer.status_code == 429
    assert other_customer.status_code == 200


def test_throttle_of_one_customer_does_not_leak_across_addresses(auth_db: SeededAuthDb, small_login_limit):
    # Same account, different addresses: the login key is deliberately shared
    # across addresses, so a distributed spray on one account is still caught —
    # and it stays confined to that account.
    with make_client(auth_db.factory, client_address=IP_ONE) as first_client:
        statuses = statuses_for(first_client, small_login_limit, password="wrong password")
        assert statuses == [401] * small_login_limit

    with make_client(auth_db.factory, client_address=IP_TWO) as second_client:
        same_customer = second_client.post("/auth/login", json=login_payload())
        other_customer = second_client.post(
            "/auth/login", json=login_payload(login=CUSTOMER_TWO_LOGIN, password=OTHER_PASSWORD)
        )

    assert same_customer.status_code == 429
    assert other_customer.status_code == 200
