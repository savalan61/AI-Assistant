"""Tests for the tenant-scoped MT5 session boundary (Step 36).

Everything here drives MT5SessionManager/MetaTrader5 through the injected
``mt5_api`` seam, so no test needs a real terminal, a real account, PostgreSQL
or network access. The encryption key is a test-only value generated
in-process, so the decryption path under test is the real one.
"""
import threading
import time

import pytest

import app.core.dependencies as deps
from app.core.config import settings as app_settings
from app.core.encryption import encrypt_secret, generate_encryption_key
from app.core.mt5_session import (
    MT5AccountCredentials,
    MT5SessionError,
    MT5SessionManager,
)
from app.db.models import Broker, User

# The only MT5 functions the session boundary may ever touch.
ALLOWED_MT5_FUNCTIONS = {"initialize", "login", "last_error", "shutdown"}
TRADING_FUNCTIONS = {"order_send", "order_check", "positions_modify", "orders_modify"}

SERVER_A = "BrokerA-Live"
SERVER_B = "BrokerB-Live"
PASSWORD = "mt5-account-password-under-test"


class FakeMT5:
    """Configurable fake of the MT5 surface the session boundary touches.

    Authentication calls are recorded with their arguments so tests can prove
    which account was authenticated, on which server, with the decrypted
    password — and that no other MT5 function was used.
    """

    def __init__(
        self,
        initialize_results: list[object] | None = None,
        login_results: list[object] | None = None,
        initialize_error: Exception | None = None,
        login_error: Exception | None = None,
        shutdown_error: Exception | None = None,
    ):
        self._initialize_results = list(initialize_results or [True])
        self._login_results = list(login_results or [True])
        self.initialize_error = initialize_error
        self.login_error = login_error
        self.shutdown_error = shutdown_error
        self.initialize_calls: list[dict[str, object]] = []
        self.login_calls: list[dict[str, object]] = []
        self.shutdown_calls = 0
        self.accessed: list[str] = []

    def _record(self, name: str) -> None:
        self.accessed.append(name)

    def initialize(self, **kwargs: object) -> object:
        self._record("initialize")
        self.initialize_calls.append(dict(kwargs))
        if self.initialize_error is not None:
            raise self.initialize_error
        # The last configured result applies to any further call.
        return self._initialize_results.pop(0) if len(self._initialize_results) > 1 else self._initialize_results[0]

    def login(self, **kwargs: object) -> object:
        self._record("login")
        self.login_calls.append(dict(kwargs))
        if self.login_error is not None:
            raise self.login_error
        return self._login_results.pop(0) if len(self._login_results) > 1 else self._login_results[0]

    def last_error(self) -> tuple[int, str]:
        self._record("last_error")
        return (-6, "simulated authorization failure")

    def shutdown(self) -> None:
        self._record("shutdown")
        self.shutdown_calls += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error

    def order_send(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - must never be reached
        self._record("order_send")
        raise AssertionError("the session boundary must never place an order")


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a usable, test-only encryption key."""
    monkeypatch.setattr(app_settings, "SECRET_ENCRYPTION_KEY", generate_encryption_key(), raising=True)


def credentials(login: int = 10001, server: str = SERVER_A, password: str = PASSWORD) -> MT5AccountCredentials:
    """Tenant credentials whose stored password is really ciphertext."""
    return MT5AccountCredentials(login=login, server=server, password_encrypted=encrypt_secret(password))


# --- authentication: first connect vs. account switch ------------------------


def test_first_read_initializes_the_terminal_on_the_tenant_account() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials()) as api:
        assert api is fake

    assert fake.initialize_calls == [{"login": 10001, "password": PASSWORD, "server": SERVER_A}]
    assert fake.login_calls == []
    assert manager.authenticated_account == (SERVER_A, 10001)


def test_switching_tenants_logs_in_instead_of_reinitializing() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials(login=10001, server=SERVER_A)):
        pass
    with manager.acquire(credentials(login=20002, server=SERVER_B)):
        pass

    # One terminal initialization, then an account switch on the live session.
    assert len(fake.initialize_calls) == 1
    assert fake.login_calls == [{"login": 20002, "password": PASSWORD, "server": SERVER_B}]
    assert manager.authenticated_account == (SERVER_B, 20002)


def test_repeated_reads_for_the_same_tenant_do_not_reauthenticate() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)

    for _ in range(5):
        with manager.acquire(credentials()):
            pass

    assert len(fake.initialize_calls) == 1
    assert fake.login_calls == []


def test_same_login_on_a_different_server_is_a_different_session() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials(login=10001, server=SERVER_A)):
        pass
    with manager.acquire(credentials(login=10001, server=SERVER_B)):
        pass

    # The server is part of the session identity, not just the account number.
    assert fake.login_calls == [{"login": 10001, "password": PASSWORD, "server": SERVER_B}]


# --- tenant isolation --------------------------------------------------------


def test_each_read_observes_its_own_tenants_authenticated_session() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)
    creds_a = credentials(login=10001, server=SERVER_A)
    creds_b = credentials(login=20002, server=SERVER_B)

    with manager.acquire(creds_a):
        assert manager.authenticated_account == (SERVER_A, 10001)
    with manager.acquire(creds_b):
        assert manager.authenticated_account == (SERVER_B, 20002)
    # Back to A: the terminal switches again rather than serving B's session.
    with manager.acquire(creds_a):
        assert manager.authenticated_account == (SERVER_A, 10001)

    assert [call["login"] for call in fake.login_calls] == [20002, 10001]


def test_concurrent_tenants_are_serialized_and_never_share_a_session() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)
    creds_a = credentials(login=10001, server=SERVER_A)
    creds_b = credentials(login=20002, server=SERVER_B)

    observations: list[tuple[tuple[str, int], tuple[str, int] | None]] = []
    errors: list[BaseException] = []
    guard = threading.Lock()
    inside = 0
    max_inside = 0

    def read(creds: MT5AccountCredentials, expected: tuple[str, int]) -> None:
        nonlocal inside, max_inside
        try:
            with manager.acquire(creds):
                with guard:
                    inside += 1
                    max_inside = max(max_inside, inside)
                # Hold the session long enough that an unserialized second
                # tenant would necessarily re-authenticate the terminal here.
                time.sleep(0.02)
                observations.append((expected, manager.authenticated_account))
                with guard:
                    inside -= 1
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [
        threading.Thread(target=read, args=(creds_a, (SERVER_A, 10001))),
        threading.Thread(target=read, args=(creds_b, (SERVER_B, 20002))),
        threading.Thread(target=read, args=(creds_a, (SERVER_A, 10001))),
        threading.Thread(target=read, args=(creds_b, (SERVER_B, 20002))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    # Every read saw exactly its own tenant's authenticated account.
    assert all(observed == expected for expected, observed in observations)
    assert len(observations) == 4
    # And no two reads were ever inside the session at the same time.
    assert max_inside == 1


# --- fail closed -------------------------------------------------------------


@pytest.mark.parametrize(
    "creds",
    [
        MT5AccountCredentials(login=None, server=SERVER_A, password_encrypted="x"),
        MT5AccountCredentials(login=10001, server=None, password_encrypted="x"),
        MT5AccountCredentials(login=10001, server="   ", password_encrypted="x"),
        MT5AccountCredentials(login=10001, server=SERVER_A, password_encrypted=None),
        MT5AccountCredentials(login=10001, server=SERVER_A, password_encrypted="   "),
    ],
)
def test_incomplete_credentials_fail_closed_without_touching_mt5(creds: MT5AccountCredentials) -> None:
    fake = FakeMT5()

    with pytest.raises(MT5SessionError) as excinfo:
        with MT5SessionManager(mt5_api=fake).acquire(creds):
            pass  # pragma: no cover - the acquire must fail first

    assert str(excinfo.value) == "MT5 account credentials are not configured"
    assert fake.accessed == []  # no terminal call was attempted


def test_undecryptable_password_fails_closed_without_touching_mt5() -> None:
    fake = FakeMT5()
    creds = MT5AccountCredentials(login=10001, server=SERVER_A, password_encrypted="not-a-fernet-token")

    with pytest.raises(MT5SessionError) as excinfo:
        with MT5SessionManager(mt5_api=fake).acquire(creds):
            pass  # pragma: no cover - the acquire must fail first

    assert str(excinfo.value) == "MT5 account credentials could not be used"
    assert fake.accessed == []


def test_password_encrypted_under_another_key_fails_closed() -> None:
    creds = credentials()
    # Simulate key rotation: the stored ciphertext no longer decrypts.
    app_settings.SECRET_ENCRYPTION_KEY = generate_encryption_key()

    with pytest.raises(MT5SessionError):
        with MT5SessionManager(mt5_api=FakeMT5()).acquire(creds):
            pass  # pragma: no cover - the acquire must fail first


def test_empty_decrypted_password_fails_closed() -> None:
    with pytest.raises(MT5SessionError):
        with MT5SessionManager(mt5_api=FakeMT5()).acquire(credentials(password="")):
            pass  # pragma: no cover - the acquire must fail first


def test_initialize_returning_false_raises_and_reports_last_error() -> None:
    fake = FakeMT5(initialize_results=[False])

    with pytest.raises(MT5SessionError) as excinfo:
        with MT5SessionManager(mt5_api=fake).acquire(credentials()):
            pass  # pragma: no cover - the acquire must fail first

    assert "simulated authorization failure" in str(excinfo.value)


def test_login_returning_false_raises() -> None:
    fake = FakeMT5(login_results=[False])
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials(login=10001, server=SERVER_A)):
        pass
    with pytest.raises(MT5SessionError):
        with manager.acquire(credentials(login=20002, server=SERVER_B)):
            pass  # pragma: no cover - the acquire must fail first


def test_initialize_raising_external_exception_is_translated_and_chained() -> None:
    fake = FakeMT5(initialize_error=OSError("simulated IPC crash"))

    with pytest.raises(MT5SessionError) as excinfo:
        with MT5SessionManager(mt5_api=fake).acquire(credentials()):
            pass  # pragma: no cover - the acquire must fail first

    assert "MT5 terminal initialization failed" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, OSError)


def test_login_raising_external_exception_is_translated_and_chained() -> None:
    fake = FakeMT5(login_error=OSError("simulated terminal disconnect"))
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials(login=10001, server=SERVER_A)):
        pass
    with pytest.raises(MT5SessionError) as excinfo:
        with manager.acquire(credentials(login=20002, server=SERVER_B)):
            pass  # pragma: no cover - the acquire must fail first

    assert isinstance(excinfo.value.__cause__, OSError)


def test_session_errors_are_runtime_errors_so_the_api_maps_them_to_503() -> None:
    assert issubclass(MT5SessionError, RuntimeError)


def test_failed_authentication_forgets_the_identity_so_the_next_read_retries() -> None:
    fake = FakeMT5(login_results=[False, True])
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials(login=10001, server=SERVER_A)):
        pass
    with pytest.raises(MT5SessionError):
        with manager.acquire(credentials(login=20002, server=SERVER_B)):
            pass  # pragma: no cover - the acquire must fail first

    # The unknown terminal state was dropped rather than cached as usable.
    assert manager.authenticated_account is None
    # A retry re-authenticates (initialize, since no session is trusted) and works.
    with manager.acquire(credentials(login=20002, server=SERVER_B)):
        assert manager.authenticated_account == (SERVER_B, 20002)


def test_exception_inside_a_read_forgets_the_identity() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)
    creds = credentials()

    with pytest.raises(RuntimeError):
        with manager.acquire(creds):
            raise RuntimeError("MT5 positions request failed")

    assert manager.authenticated_account is None
    # The next read re-authenticates from a clean initialize().
    with manager.acquire(creds):
        pass
    assert len(fake.initialize_calls) == 2


# --- shutdown ----------------------------------------------------------------


def test_shutdown_releases_the_terminal_and_forgets_the_identity() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)

    with manager.acquire(credentials()):
        pass
    manager.shutdown()

    assert fake.shutdown_calls == 1
    assert manager.authenticated_account is None


def test_shutdown_never_raises_when_mt5_shutdown_fails() -> None:
    manager = MT5SessionManager(mt5_api=FakeMT5(shutdown_error=OSError("terminal already gone")))

    manager.shutdown()  # must not raise


def test_shutdown_is_safe_when_nothing_was_ever_authenticated() -> None:
    fake = FakeMT5()

    MT5SessionManager(mt5_api=fake).shutdown()  # must not raise

    assert fake.shutdown_calls == 1


# --- read-only guarantee and secret hygiene ----------------------------------


def test_only_session_functions_are_used_and_no_trading_call_exists() -> None:
    fake = FakeMT5()
    manager = MT5SessionManager(mt5_api=fake)
    creds = credentials()

    with manager.acquire(creds):
        pass
    with manager.acquire(credentials(login=20002, server=SERVER_B)):
        pass
    manager.shutdown()

    assert set(fake.accessed) <= ALLOWED_MT5_FUNCTIONS
    assert set(fake.accessed).isdisjoint(TRADING_FUNCTIONS)
    assert not hasattr(manager, "order_send")


def test_no_error_or_repr_ever_contains_the_password_or_ciphertext() -> None:
    creds = credentials()
    ciphertext = creds.password_encrypted or ""

    messages: list[str] = [repr(creds)]
    # Incomplete credentials and an undecryptable secret both fail closed.
    for bad in (
        MT5AccountCredentials(login=None, server=SERVER_A, password_encrypted=ciphertext),
        MT5AccountCredentials(login=10001, server=SERVER_A, password_encrypted="gAAAAA-tampered"),
    ):
        try:
            with MT5SessionManager(mt5_api=FakeMT5()).acquire(bad):
                pass
        except MT5SessionError as exc:
            messages.append(str(exc))

    # A rejected login reports MT5's own text, which must not carry credentials.
    fake = FakeMT5(initialize_results=[False])
    try:
        with MT5SessionManager(mt5_api=fake).acquire(creds):
            pass
    except MT5SessionError as exc:
        messages.append(str(exc))

    for message in messages:
        assert PASSWORD not in message
        assert ciphertext not in message
        assert "gAAAAA" not in message


def test_credentials_repr_omits_the_stored_password() -> None:
    creds = credentials()

    text = repr(creds)

    assert "10001" in text and SERVER_A in text
    assert "password_encrypted" not in text
    assert PASSWORD not in text


# --- composition root: tenant credentials come from the database identity -----


def _user(login: str, broker_id: int = 7, encrypted: str | None = "cipher") -> User:
    return User(
        broker_id=broker_id,
        login=login,
        password_hash="x" * 60,
        mt5_password_encrypted=encrypted,
        is_active=True,
    )


def test_credentials_are_resolved_from_the_user_and_broker_rows() -> None:
    broker = Broker(name="Broker A", code="BA", mt5_server=SERVER_A)

    resolved = deps.resolve_mt5_account_credentials(_user("10001"), broker)

    assert resolved == MT5AccountCredentials(login=10001, server=SERVER_A, password_encrypted="cipher")


def test_non_numeric_login_is_not_an_mt5_login() -> None:
    broker = Broker(name="Broker A", code="BA", mt5_server=SERVER_A)

    assert deps.resolve_mt5_account_credentials(_user("customer-alice"), broker).login is None


def test_missing_broker_or_password_resolves_to_incomplete_credentials() -> None:
    without_broker = deps.resolve_mt5_account_credentials(_user("10001"), None)
    without_password = deps.resolve_mt5_account_credentials(
        _user("10001", encrypted=None), Broker(name="Broker A", code="BA", mt5_server=SERVER_A)
    )

    assert without_broker.server is None and without_broker.login == 10001
    assert without_password.password_encrypted is None


def test_resolution_never_decrypts_the_stored_password() -> None:
    # No encryption key is usable for a value that is not real ciphertext: if
    # resolution tried to decrypt, this would raise instead of returning it.
    app_settings.SECRET_ENCRYPTION_KEY = ""

    resolved = deps.resolve_mt5_account_credentials(
        _user("10001", encrypted="still-encrypted"), Broker(name="Broker A", code="BA", mt5_server=SERVER_A)
    )

    assert resolved.password_encrypted == "still-encrypted"


def test_each_tenant_resolves_its_own_server_login_and_ciphertext() -> None:
    broker_a = Broker(name="Broker A", code="BA", mt5_server=SERVER_A)
    broker_b = Broker(name="Broker B", code="BB", mt5_server=SERVER_B)

    resolved_a = deps.resolve_mt5_account_credentials(_user("10001", broker_id=1, encrypted="cipher-a"), broker_a)
    resolved_b = deps.resolve_mt5_account_credentials(_user("20002", broker_id=2, encrypted="cipher-b"), broker_b)

    assert (resolved_a.login, resolved_a.server, resolved_a.password_encrypted) == (10001, SERVER_A, "cipher-a")
    assert (resolved_b.login, resolved_b.server, resolved_b.password_encrypted) == (20002, SERVER_B, "cipher-b")


def test_session_manager_is_a_lock_guarded_process_wide_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "_mt5_session_manager", None, raising=True)

    first = deps.get_mt5_session_manager()
    second = deps.get_mt5_session_manager()

    assert first is second
    deps.shutdown_mt5_session()
    assert deps._mt5_session_manager is None
