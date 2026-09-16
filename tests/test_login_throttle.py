"""Focused tests for the in-process login brute-force throttle.

Require none of: database, network, MT5, credentials.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.services.auth import LoginThrottle, LoginThrottleExceededError
from app.services.auth import login_throttle as login_throttle_module

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

# Two tenants that legitimately share the same MT5 login number: the whole point
# of the tenant-scoped key is that they never share throttle state.
BROKER_A = "TB-1"
BROKER_B = "TB-2"
SHARED_LOGIN = "80009"


def make_throttle(max_failures: int = 3, window_seconds: int = 300) -> LoginThrottle:
    return LoginThrottle(max_failures=max_failures, window_seconds=window_seconds)


def fail_n(
    throttle: LoginThrottle,
    times: int,
    *,
    ip: str = "1.2.3.4",
    broker: str = BROKER_A,
    login: str = "10001",
) -> None:
    for _ in range(times):
        throttle.record_failure(ip, broker, login, NOW)


# --- construction ----------------------------------------------------------------------


@pytest.mark.parametrize(("max_failures", "window"), [(0, 60), (-1, 60), (5, 0), (5, -1)])
def test_invalid_configuration_is_rejected(max_failures: int, window: int) -> None:
    with pytest.raises(ValueError):
        LoginThrottle(max_failures=max_failures, window_seconds=window)


def test_naive_time_is_rejected() -> None:
    throttle = make_throttle()

    with pytest.raises(ValueError, match="timezone-aware"):
        throttle.check("1.2.3.4", BROKER_A, "10001", datetime(2026, 9, 15, 12, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        throttle.record_failure("1.2.3.4", BROKER_A, "10001", datetime(2026, 9, 15, 12, 0))


# --- admission -------------------------------------------------------------------------


def test_fresh_client_is_admitted() -> None:
    throttle = make_throttle()

    throttle.check("1.2.3.4", BROKER_A, "10001", NOW)  # does not raise


def test_below_the_limit_is_admitted() -> None:
    throttle = make_throttle(max_failures=3)
    fail_n(throttle, 2)

    throttle.check("1.2.3.4", BROKER_A, "10001", NOW)  # 2 < 3


def test_at_the_limit_is_rejected_for_the_login() -> None:
    throttle = make_throttle(max_failures=3)
    fail_n(throttle, 3)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("9.9.9.9", BROKER_A, "10001", NOW)  # different IP, same login


def test_at_the_limit_is_rejected_for_the_ip() -> None:
    throttle = make_throttle(max_failures=3)
    for login in ("1", "2", "3"):
        throttle.record_failure("1.2.3.4", BROKER_A, login, NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("1.2.3.4", BROKER_A, "never-seen", NOW)  # same IP, new login


def test_login_casing_cannot_evade_the_counter() -> None:
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", BROKER_A, "Admin", NOW)
    throttle.record_failure("1.2.3.4", BROKER_A, "admin", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("8.8.8.8", BROKER_A, "ADMIN", NOW)


def test_surrounding_whitespace_cannot_evade_the_counter() -> None:
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", BROKER_A, " 10001 ", NOW)
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("8.8.8.8", BROKER_A, "10001", NOW)


def test_broker_casing_cannot_evade_the_counter() -> None:
    # The login endpoint resolves broker codes case-insensitively, so the
    # counter must fold casing too — otherwise "tb-1" would be a fresh bucket.
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", "TB-1", "10001", NOW)
    throttle.record_failure("1.2.3.4", "tb-1", "10001", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("8.8.8.8", "Tb-1", "10001", NOW)


def test_surrounding_whitespace_in_the_broker_cannot_evade_the_counter() -> None:
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", " TB-1 ", "10001", NOW)
    throttle.record_failure("1.2.3.4", "TB-1", "10001", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("8.8.8.8", "TB-1", "10001", NOW)


def test_unrelated_client_and_login_is_admitted() -> None:
    throttle = make_throttle(max_failures=2)
    fail_n(throttle, 2, ip="1.1.1.1", login="10001")

    # Neither key matches the throttled pair, so this client is unaffected.
    throttle.check("2.2.2.2", BROKER_A, "20002", NOW)


def test_login_key_is_global_across_client_ips() -> None:
    # A distributed spray on one login (a new address each time) is caught
    # by the login key alone.
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.1.1.1", BROKER_A, "10001", NOW)
    throttle.record_failure("2.2.2.2", BROKER_A, "10001", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("3.3.3.3", BROKER_A, "10001", NOW)


# --- tenant scoping of the login key -----------------------------------------------------


def test_the_same_login_at_another_broker_is_not_throttled() -> None:
    # The core tenant-isolation property: exhausting one tenant's 80009 must
    # leave another tenant's identical 80009 untouched (fresh IP too, so the
    # IP bucket cannot be what admits it).
    throttle = make_throttle(max_failures=2)
    fail_n(throttle, 2, ip="1.1.1.1", broker=BROKER_A, login=SHARED_LOGIN)

    throttle.check("2.2.2.2", BROKER_B, SHARED_LOGIN, NOW)  # does not raise


def test_one_brokers_failures_do_not_lock_the_shared_login_out_for_the_other() -> None:
    throttle = make_throttle(max_failures=1)
    throttle.record_failure("1.1.1.1", BROKER_A, SHARED_LOGIN, NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("1.1.1.1", BROKER_A, SHARED_LOGIN, NOW)

    # Same login, other tenant, other address: admitted.
    throttle.check("2.2.2.2", BROKER_B, SHARED_LOGIN, NOW)


def test_ip_key_still_spans_every_broker_at_that_address() -> None:
    # Tenant scoping applies to the login key only: one address is one address,
    # however many tenants it tries.
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)
    throttle.record_failure("1.2.3.4", BROKER_B, "20002", NOW)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("1.2.3.4", BROKER_A, "30003", NOW)


def test_an_unknown_broker_code_gets_its_own_bucket() -> None:
    # Unknown codes cannot be resolved to a tenant, so they are bucketed by the
    # submitted code; they always fail authentication anyway and the IP key
    # still applies.
    throttle = make_throttle(max_failures=2)
    throttle.record_failure("1.2.3.4", "no-such-broker", SHARED_LOGIN, NOW)
    throttle.record_failure("1.2.3.4", "no-such-broker", SHARED_LOGIN, NOW)

    throttle.check("2.2.2.2", BROKER_A, SHARED_LOGIN, NOW)  # real tenant unaffected


# --- window and success reset ------------------------------------------------------------


def test_failures_outside_the_window_no_longer_count() -> None:
    throttle = make_throttle(max_failures=2, window_seconds=60)
    fail_n(throttle, 2)

    later = NOW + timedelta(seconds=61)

    throttle.check("1.2.3.4", BROKER_A, "10001", later)  # window has passed


def test_partial_window_expiry_keeps_recent_failures() -> None:
    throttle = make_throttle(max_failures=2, window_seconds=60)
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW - timedelta(seconds=120))
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)

    # Only the recent failure is inside the window: still one attempt left.
    throttle.check("1.2.3.4", BROKER_A, "10001", NOW)

    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)
    with pytest.raises(LoginThrottleExceededError):
        throttle.check("1.2.3.4", BROKER_A, "10001", NOW)


def test_successful_login_clears_both_keys() -> None:
    throttle = make_throttle(max_failures=2)
    fail_n(throttle, 2)

    throttle.record_success("1.2.3.4", BROKER_A, "10001")

    throttle.check("1.2.3.4", BROKER_A, "10001", NOW)  # admitted again


def test_success_clears_the_ip_key_for_other_logins() -> None:
    throttle = make_throttle(max_failures=3)
    for login in ("1", "2", "3"):
        throttle.record_failure("1.2.3.4", BROKER_A, login, NOW)

    throttle.record_success("1.2.3.4", BROKER_A, "2")  # clears the ip key too

    throttle.check("1.2.3.4", BROKER_A, "new-user", NOW)


def test_success_at_another_broker_leaves_this_brokers_login_bucket_alone() -> None:
    # A valid login at one tenant must not erase the failure history recorded
    # against another tenant's identical login number.
    throttle = make_throttle(max_failures=2)
    fail_n(throttle, 2, broker=BROKER_A, login=SHARED_LOGIN)

    throttle.record_success("1.2.3.4", BROKER_B, SHARED_LOGIN)

    with pytest.raises(LoginThrottleExceededError):
        throttle.check("9.9.9.9", BROKER_A, SHARED_LOGIN, NOW)


# --- bounds and hygiene --------------------------------------------------------------------


def test_rejection_message_is_generic() -> None:
    throttle = make_throttle(max_failures=1)
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)

    with pytest.raises(LoginThrottleExceededError) as excinfo:
        throttle.check("1.2.3.4", BROKER_A, "10001", NOW)

    message = str(excinfo.value)
    # The error must not say whether the login exists, nor echo the input.
    assert "10001" not in message
    assert "1.2.3.4" not in message
    assert BROKER_A not in message
    assert "exist" not in message


def test_stale_keys_are_evicted_once_tracking_grows(monkeypatch: pytest.MonkeyPatch) -> None:
    # Lower the sweep threshold so the bound can be exercised with a handful of
    # keys instead of thousands.
    monkeypatch.setattr(login_throttle_module, "_SWEEP_THRESHOLD", 10)
    throttle = make_throttle(max_failures=2, window_seconds=60)

    for index in range(40):
        throttle.record_failure("1.2.3.4", BROKER_A, f"user-{index}", NOW)
    assert throttle.tracked_keys > 10

    # A check past the window sweeps every now-stale entry away.
    throttle.check("9.9.9.9", BROKER_A, "someone", NOW + timedelta(seconds=61))

    assert throttle.tracked_keys == 0


def test_recent_keys_survive_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(login_throttle_module, "_SWEEP_THRESHOLD", 2)
    throttle = make_throttle(max_failures=2, window_seconds=60)

    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW)
    throttle.record_failure("1.2.3.4", BROKER_A, "10001", NOW + timedelta(seconds=30))
    throttle.check("9.9.9.9", BROKER_A, "someone", NOW + timedelta(seconds=40))

    # Inside the window, so nothing was evicted.
    assert throttle.tracked_keys > 0


def test_module_does_not_log() -> None:
    import inspect

    from app.services.auth import login_throttle as module

    source = inspect.getsource(module)
    assert "logging" not in source
