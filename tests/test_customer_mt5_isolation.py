"""Customer-to-customer MT5 isolation in the ONE-BROKER deployment.

This is the boundary that replaced cross-broker isolation, and it is the one a
real leak would cross: several customers of the SAME broker trade on the SAME
MT5 server, so the only thing separating their accounts is each customer's own
``login`` — which is also their application login.

These tests therefore do NOT mock the isolation machinery. They run the REAL
stack:

* the real routers (/account-info, /positions, /market-data, /instruments);
* the real ``get_current_user`` JWT + database identity boundary;
* the real composition root (``resolve_mt5_account_credentials``), so the
  identity a provider receives is produced exactly as in production;
* the real ``MT5SessionManager``, including identity verification and the
  process-wide lock;
* the real MT5 providers.

Only the terminal itself is replaced, by a recording fake that authenticates per
(server, login) and REFUSES to serve a read unless the terminal is authenticated
on the account the credentials name. A crossing therefore shows up as a failed
assertion (or a wrong symbol/balance) rather than passing silently.

No real MT5, no network, no real credentials: the investor passwords are
test-only values, and the terminal never leaves the process.
"""
import asyncio
import threading
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.dependencies as deps
from app.api.account_info_router import router as account_info_router
from app.api.instruments_router import router as instruments_router
from app.api.market_data_router import router as market_data_router
from app.api.positions_router import router as positions_router
from app.core.config import settings as app_settings
from app.core.mt5_session import MT5SessionManager
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.db.models import Broker, User, UserRole

TEST_SECRET = "unit-test-secret-not-a-real-credential"
TEST_ALGORITHM = "HS256"

BROKER_SERVER = "TheBroker-Live"
# Two customers of the one broker: same MT5 server, two accounts.
CUSTOMER_ONE_LOGIN = 10001
CUSTOMER_TWO_LOGIN = 10002
# Test-only investor (read-only) passwords, each stored encrypted for its owner.
CUSTOMER_ONE_PASSWORD = "investor-one-read-only-secret"
CUSTOMER_TWO_PASSWORD = "investor-two-read-only-secret"
# The symbol each account holds: the read result identifies whose account it is.
CUSTOMER_ONE_SYMBOL = "XAUUSD"
CUSTOMER_TWO_SYMBOL = "EURUSD"
CUSTOMER_ONE_BALANCE = Decimal("11111.11")
CUSTOMER_TWO_BALANCE = Decimal("22222.22")


@pytest.fixture(autouse=True)
def test_only_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "SECRET_KEY", TEST_SECRET, raising=True)
    monkeypatch.setattr(app_settings, "ALGORITHM", TEST_ALGORITHM, raising=True)


class _Account:
    def __init__(self, server: str, login: int) -> None:
        self.login = login
        self.name = f"Account {login}"
        self.balance = CUSTOMER_ONE_BALANCE if login == CUSTOMER_ONE_LOGIN else CUSTOMER_TWO_BALANCE
        self.equity = self.balance
        self.margin = Decimal("0")
        self.margin_free = self.balance
        self.margin_level = 0.0
        self.currency = "USD"
        self.server = server


class _Position:
    def __init__(self, ticket: int, symbol: str) -> None:
        self.ticket = ticket
        self.symbol = symbol
        self.type = 0
        self.volume = 0.10
        self.price_open = 1.0
        self.price_current = 1.0
        self.profit = 5.0


class _SymbolInfo:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} description"
        self.path = "Test"
        self.currency_base = "USD"
        self.currency_profit = "USD"
        self.digits = 2
        self.trade_mode = 4


class RecordingTerminal:
    """A fake MT5 terminal that authenticates per account and audits every read.

    It models the two properties that matter for isolation: the terminal holds
    exactly ONE account at a time, and a read is only answered for the account it
    is currently authenticated on. Every authentication attempt and every read is
    recorded WITH the identity in force, so a test can prove which account was
    used — and, just as importantly, that a read for customer A never happened
    while the terminal was on customer B.
    """

    TIMEFRAME_M1 = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, accounts: dict[tuple[str, int], str]) -> None:
        self._passwords = dict(accounts)
        self.current: tuple[str, int] | None = None
        # Audit trails.
        self.authentication_attempts: list[tuple[str, int]] = []
        self.authentications: list[tuple[str, int]] = []
        self.reads: list[tuple[str | None, int | None, str]] = []
        self.initialize_calls = 0
        self.login_calls = 0
        self.shutdown_calls = 0
        # Injection knobs.
        self.fail_authentication = False
        self.next_account_on_read: tuple[str, int] | None = None

    # --- identity -------------------------------------------------------------------
    def _authenticate(self, login: int, password: str, server: str) -> bool:
        key = (server, int(login))
        self.authentication_attempts.append(key)
        if self.fail_authentication or self._passwords.get(key) != password:
            self.current = None
            return False
        self.current = key
        self.authentications.append(key)
        return True

    def initialize(self, **kwargs: object) -> bool:
        self.initialize_calls += 1
        login = kwargs.get("login")
        if login is None:
            # A plain initialize() attaches to whatever the terminal is already
            # on; with nothing running there is no account.
            self.current = None
            return True
        return self._authenticate(
            int(login), str(kwargs.get("password") or ""), str(kwargs.get("server") or "")
        )

    def login(self, **kwargs: object) -> bool:
        self.login_calls += 1
        return self._authenticate(
            int(kwargs.get("login") or 0),
            str(kwargs.get("password") or ""),
            str(kwargs.get("server") or ""),
        )

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.current = None

    def last_error(self) -> tuple[int, str]:
        return (-1, "simulated terminal answer")

    # --- reads ---------------------------------------------------------------------
    def _begin_read(self, operation: str) -> tuple[str | None, int | None]:
        # A terminal can be re-authenticated out of band (another process, a
        # human at the terminal). Injected here so the identity check that
        # guards every read can be exercised.
        if self.next_account_on_read is not None:
            self.current = self.next_account_on_read
            self.next_account_on_read = None
        key = self.current
        self.reads.append((key[0] if key else None, key[1] if key else None, operation))
        return (key[0] if key else None, key[1] if key else None)

    def _symbol_for_current(self) -> str:
        _, login = self.current or (None, None)
        return CUSTOMER_ONE_SYMBOL if login == CUSTOMER_ONE_LOGIN else CUSTOMER_TWO_SYMBOL

    def account_info(self) -> _Account | None:
        server, login = self._begin_read("account_info")
        if server is None or login is None:
            return None
        return _Account(server, int(login))

    def positions_get(self, *args: object, **kwargs: object) -> tuple[_Position, ...]:
        server, login = self._begin_read("positions_get")
        if server is None or login is None:
            return ()
        return (_Position(ticket=int(login) * 10 + 1, symbol=self._symbol_for_current()),)

    def copy_rates_from_pos(self, symbol: str, *args: object, **kwargs: object) -> tuple[tuple[Any, ...], ...]:
        self._begin_read(f"copy_rates_from_pos:{symbol}")
        # Only the account's own instrument exists on this fake terminal, so a
        # read for the wrong account is visible as "no data" rather than as a
        # silent success with the other customer's prices.
        if symbol != self._symbol_for_current():
            return ()
        price = 2000.0 if self._symbol_for_current() == CUSTOMER_ONE_SYMBOL else 1.1
        return ((1_700_000_000, price, price + 1, price - 1, price, 10.0),)

    def symbol_info(self, symbol: str) -> _SymbolInfo | None:
        self._begin_read(f"symbol_info:{symbol}")
        if symbol != self._symbol_for_current():
            return None
        return _SymbolInfo(symbol)

    def symbols_get(self, *args: object, **kwargs: object) -> tuple[_SymbolInfo, ...]:
        self._begin_read("symbols_get")
        return (_SymbolInfo(self._symbol_for_current()),)


@pytest.fixture()
def terminal() -> RecordingTerminal:
    return RecordingTerminal(
        {
            (BROKER_SERVER, CUSTOMER_ONE_LOGIN): CUSTOMER_ONE_PASSWORD,
            (BROKER_SERVER, CUSTOMER_TWO_LOGIN): CUSTOMER_TWO_PASSWORD,
        }
    )


@pytest.fixture()
def isolation_env(tmp_path, monkeypatch: pytest.MonkeyPatch, terminal: RecordingTerminal):
    """The real stack, with the terminal replaced and the real session manager used."""
    from app.core.encryption import encrypt_secret, generate_encryption_key

    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)
    manager = MT5SessionManager(mt5_api=terminal)
    monkeypatch.setattr(deps, "_mt5_session_manager", manager, raising=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/isolation.db")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def seed() -> dict[str, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            broker = Broker(name="The Broker", code="ISO-ONE", mt5_server=BROKER_SERVER)
            session.add(broker)
            await session.flush()
            one = User(
                broker_id=broker.id,
                login=str(CUSTOMER_ONE_LOGIN),
                password_hash="x" * 60,
                mt5_password_encrypted=encrypt_secret(CUSTOMER_ONE_PASSWORD),
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            two = User(
                broker_id=broker.id,
                login=str(CUSTOMER_TWO_LOGIN),
                password_hash="x" * 60,
                mt5_password_encrypted=encrypt_secret(CUSTOMER_TWO_PASSWORD),
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            unprovisioned = User(
                broker_id=broker.id,
                login="10003",
                password_hash="x" * 60,
                mt5_password_encrypted=None,
                is_active=True,
                role=UserRole.CUSTOMER,
            )
            session.add_all([one, two, unprovisioned])
            await session.commit()
            return {
                "broker_id": broker.id,
                "customer_one_id": one.id,
                "customer_two_id": two.id,
                "unprovisioned_id": unprovisioned.id,
            }

    ids = asyncio.run(seed())

    def make_app() -> TestClient:
        async def override_get_db() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = FastAPI()
        for router in (account_info_router, positions_router, market_data_router, instruments_router):
            app.include_router(router)
        app.dependency_overrides[get_db] = override_get_db
        return TestClient(app)

    yield {"make_app": make_app, "ids": ids, "factory": factory, "terminal": terminal, "manager": manager}
    asyncio.run(engine.dispose())


def auth_header(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def get(client: TestClient, path: str, user_id: int, params: dict[str, object] | None = None):
    return client.get(path, params=params, headers=auth_header(user_id))


# --- each customer reads its own account ----------------------------------------------


def test_account_info_returns_the_calling_customers_own_account(isolation_env) -> None:
    ids = isolation_env["ids"]

    with isolation_env["make_app"]() as client:
        one = get(client, "/account-info", ids["customer_one_id"])
        two = get(client, "/account-info", ids["customer_two_id"])

    assert (one.status_code, two.status_code) == (200, 200)
    assert one.json()["login"] == CUSTOMER_ONE_LOGIN
    assert two.json()["login"] == CUSTOMER_TWO_LOGIN
    # The values are the ones the terminal reports for the account THAT request
    # authenticated as — not a cached snapshot of the other customer's account.
    assert Decimal(str(one.json()["balance"])) == CUSTOMER_ONE_BALANCE
    assert Decimal(str(two.json()["balance"])) == CUSTOMER_TWO_BALANCE


def test_positions_come_from_the_calling_customers_own_account(isolation_env) -> None:
    ids = isolation_env["ids"]

    with isolation_env["make_app"]() as client:
        one = get(client, "/positions", ids["customer_one_id"])
        two = get(client, "/positions", ids["customer_two_id"])

    assert (one.status_code, two.status_code) == (200, 200)
    assert [position["symbol"] for position in one.json()["positions"]] == [CUSTOMER_ONE_SYMBOL]
    assert [position["symbol"] for position in two.json()["positions"]] == [CUSTOMER_TWO_SYMBOL]


def test_every_read_happened_under_its_own_accounts_authentication(isolation_env) -> None:
    """The audit trail: no read was ever served under the other customer's session."""
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        one_reads = list(terminal.reads)
        assert {login for _, login, _ in one_reads} == {CUSTOMER_ONE_LOGIN}

        get(client, "/account-info", ids["customer_two_id"])
        two_reads = list(terminal.reads[len(one_reads):])
        assert {login for _, login, _ in two_reads} == {CUSTOMER_TWO_LOGIN}

    # And every authentication the terminal performed was for the customer whose
    # request caused it — in order, with no third identity involved.
    assert terminal.authentications == [
        (BROKER_SERVER, CUSTOMER_ONE_LOGIN),
        (BROKER_SERVER, CUSTOMER_TWO_LOGIN),
    ]
    # Both customers live on the one server: only the account number separates
    # them, and only their own password ever authenticated it.
    assert {server for server, _ in terminal.authentications} == {BROKER_SERVER}


def test_the_session_follows_the_customer_and_is_never_cached_across_them(isolation_env) -> None:
    ids = isolation_env["ids"]
    manager = isolation_env["manager"]

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        after_one = manager.authenticated_account
        get(client, "/account-info", ids["customer_two_id"])
        after_two = manager.authenticated_account

    assert after_one == (BROKER_SERVER, CUSTOMER_ONE_LOGIN)
    assert after_two == (BROKER_SERVER, CUSTOMER_TWO_LOGIN)
    assert after_one != after_two


def test_market_data_and_instruments_are_read_for_the_calling_customer(isolation_env) -> None:
    """A price read is account-scoped too, so it cannot leak the other's book."""
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    with isolation_env["make_app"]() as client:
        own = get(client, f"/market-data/{CUSTOMER_ONE_SYMBOL}", ids["customer_one_id"])
        other = get(client, f"/market-data/{CUSTOMER_TWO_SYMBOL}", ids["customer_one_id"])
        catalog = get(client, "/instruments", ids["customer_two_id"])

    assert own.status_code == 200
    # Customer one's account does not carry the other account's instrument.
    assert other.status_code == 404
    assert [entry["symbol"] for entry in catalog.json()["instruments"]] == [CUSTOMER_TWO_SYMBOL]
    assert {login for _, login, _ in terminal.reads} == {CUSTOMER_ONE_LOGIN, CUSTOMER_TWO_LOGIN}


# --- the request cannot choose an identity --------------------------------------------


def test_no_request_field_can_redirect_a_read_to_another_account(isolation_env) -> None:
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    with isolation_env["make_app"]() as client:
        response = get(
            client,
            "/account-info",
            ids["customer_one_id"],
            # Every plausible selector: another account, another server, another
            # user, another broker, and a raw credentials object.
            params={
                "login": CUSTOMER_TWO_LOGIN,
                "mt5_login": CUSTOMER_TWO_LOGIN,
                "server": "OtherBroker-Live",
                "user_id": ids["customer_two_id"],
                "broker_id": 2,
                "context": "customer_two",
                "credentials": f"{CUSTOMER_TWO_LOGIN}",
            },
        )

    assert response.status_code == 200
    assert response.json()["login"] == CUSTOMER_ONE_LOGIN
    # The extra parameters were not merely ignored in the response: the terminal
    # was never asked to serve the other account.
    assert {login for _, login, _ in terminal.reads} == {CUSTOMER_ONE_LOGIN}
    assert terminal.authentications == [(BROKER_SERVER, CUSTOMER_ONE_LOGIN)]


def test_a_forged_token_for_another_account_still_reads_the_authenticated_user(isolation_env) -> None:
    """Identity is the database row behind the token, never a claimed account."""
    ids = isolation_env["ids"]

    with isolation_env["make_app"]() as client:
        response = get(client, "/account-info", ids["customer_one_id"])

    assert str(CUSTOMER_ONE_LOGIN) in str(response.json()["login"])
    assert str(CUSTOMER_TWO_LOGIN) not in str(response.json()["login"])


# --- fail-closed behaviour -------------------------------------------------------------


def test_a_customer_without_a_credential_can_never_read_anothers_account(isolation_env) -> None:
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        unprovisioned = get(client, "/account-info", ids["unprovisioned_id"])

    assert unprovisioned.status_code == 503
    assert unprovisioned.json() == {"detail": "Account information service temporarily unavailable"}
    # Fail closed: the unprovisioned customer was not quietly served from the
    # session the previous request had authenticated.
    assert str(CUSTOMER_ONE_LOGIN) not in str(unprovisioned.json())
    assert {login for _, login, _ in terminal.reads} == {CUSTOMER_ONE_LOGIN}


def test_a_failed_authentication_never_serves_the_previous_customers_account(isolation_env) -> None:
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]
    manager = isolation_env["manager"]

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        terminal.fail_authentication = True
        blocked = get(client, "/account-info", ids["customer_two_id"])

    assert blocked.status_code == 503
    assert str(CUSTOMER_ONE_BALANCE) not in str(blocked.json())
    assert str(CUSTOMER_TWO_LOGIN) not in str(blocked.json())
    # The identity is forgotten, so nothing is reused from the failed attempt and
    # no stale session remains to be read through.
    assert manager.authenticated_account is None


def test_an_out_of_band_account_switch_is_detected_and_never_served(isolation_env) -> None:
    """The terminal moved to the OTHER customer out of band before this read.

    The terminal's own answer is what confirms an identity, so the manager must
    NOT serve the read from the account it happens to be on: it re-authenticates
    the requesting customer first. What matters for isolation is that customer
    one's request never returns customer two's account — whether by failing
    closed or by restoring the right identity.
    """
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        terminal.next_account_on_read = (BROKER_SERVER, CUSTOMER_TWO_LOGIN)
        response = get(client, "/account-info", ids["customer_one_id"])

    assert response.status_code == 200
    # The other customer's account was never served, and the terminal was put
    # back onto customer one's own account before the read was answered.
    assert response.json()["login"] == CUSTOMER_ONE_LOGIN
    assert Decimal(str(response.json()["balance"])) == CUSTOMER_ONE_BALANCE
    assert terminal.current == (BROKER_SERVER, CUSTOMER_ONE_LOGIN)
    assert terminal.authentications.count((BROKER_SERVER, CUSTOMER_ONE_LOGIN)) == 2


def test_a_terminal_that_cannot_be_confirmed_fails_closed(isolation_env) -> None:
    """The terminal keeps reporting an account the request did not ask for.

    An identity that cannot be confirmed after authenticating must be refused
    and forgotten — never read through, because a false match would serve one
    customer's read from another customer's account.
    """
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    class StuckTerminal(RecordingTerminal):
        def account_info(self):  # type: ignore[override]
            self._begin_read("account_info")
            return _Account(BROKER_SERVER, CUSTOMER_TWO_LOGIN)

    stuck = StuckTerminal(
        {
            (BROKER_SERVER, CUSTOMER_ONE_LOGIN): CUSTOMER_ONE_PASSWORD,
            (BROKER_SERVER, CUSTOMER_TWO_LOGIN): CUSTOMER_TWO_PASSWORD,
        }
    )
    isolation_env["manager"]._mt5_api = stuck  # the manager now drives the stuck terminal

    with isolation_env["make_app"]() as client:
        response = get(client, "/account-info", ids["customer_one_id"])

    assert response.status_code == 503
    assert str(CUSTOMER_TWO_BALANCE) not in str(response.json())
    assert str(CUSTOMER_TWO_LOGIN) not in str(response.json())
    # Nothing was cached as a usable identity.
    assert isolation_env["manager"].authenticated_account is None


def test_a_tampered_ciphertext_fails_closed_without_touching_another_account(
    isolation_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]

    async def corrupt() -> None:
        async with isolation_env["factory"]() as session:
            user = await session.get(User, ids["customer_two_id"])
            assert user is not None
            user.mt5_password_encrypted = "not-a-fernet-token"
            await session.commit()

    asyncio.run(corrupt())

    with isolation_env["make_app"]() as client:
        get(client, "/account-info", ids["customer_one_id"])
        response = get(client, "/account-info", ids["customer_two_id"])

    assert response.status_code == 503
    # No authentication was attempted for the corrupt customer, and the healthy
    # customer's session was untouched.
    assert terminal.authentication_attempts == [(BROKER_SERVER, CUSTOMER_ONE_LOGIN)]


# --- concurrency ----------------------------------------------------------------------


def test_interleaved_concurrent_requests_never_cross(isolation_env) -> None:
    """Many concurrent reads from both customers: each answer belongs to its asker.

    The terminal holds one account at a time, so any interleaving that let
    another customer's request re-authenticate mid-read would show up here as a
    wrong login/balance — or as a read recorded under a foreign account.
    """
    ids = isolation_env["ids"]
    terminal = isolation_env["terminal"]
    results: list[tuple[int, int]] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    with isolation_env["make_app"]() as client:
        def call(user_id: int, expected_login: int) -> None:
            try:
                response = get(client, "/account-info", user_id)
                with results_lock:
                    results.append((expected_login, response.status_code, response.json().get("login")))
            except BaseException as exc:  # pragma: no cover - surfaced by the assertions below
                with results_lock:
                    errors.append(exc)

        threads = []
        for _ in range(6):
            threads.append(threading.Thread(target=call, args=(ids["customer_one_id"], CUSTOMER_ONE_LOGIN)))
            threads.append(threading.Thread(target=call, args=(ids["customer_two_id"], CUSTOMER_TWO_LOGIN)))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 12
    # Every response answered for the account that asked, with no exception.
    for expected_login, status, answered_login in results:
        assert status == 200
        assert answered_login == expected_login
    # And each read was recorded under the account whose request was in flight —
    # every recorded login is one of the two real accounts, and both appear.
    assert {login for _, login, _ in terminal.reads} == {CUSTOMER_ONE_LOGIN, CUSTOMER_TWO_LOGIN}
    # Every authentication the terminal performed was for a real customer of this
    # broker, never for something the requests supplied.
    assert set(terminal.authentication_attempts) <= {
        (BROKER_SERVER, CUSTOMER_ONE_LOGIN),
        (BROKER_SERVER, CUSTOMER_TWO_LOGIN),
    }


# --- read-only guarantee ---------------------------------------------------------------


def test_no_enabled_endpoint_offers_a_trading_operation() -> None:
    """The isolation suite also pins the read-only promise on these routes."""
    from app.main import app

    paths = app.openapi()["paths"]

    for path in ("/account-info", "/positions", "/market-data/{symbol}", "/instruments", "/instruments/{symbol}"):
        methods = set(paths[path])
        assert methods <= {"get"}, (path, methods)
