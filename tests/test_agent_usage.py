"""Tests for the in-process per-user daily Agent usage limiter.

No network, no database, no clock dependency: the day is injected so tests are
deterministic (the established timezone-aware `now` convention).
"""
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.agent.usage import AgentUsageLimiter, UsageLimitExceededError

DAY_ONE = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)


def make_limiter(daily_limit: int = 3) -> AgentUsageLimiter:
    return AgentUsageLimiter(daily_limit)


def test_below_quota_is_admitted() -> None:
    limiter = make_limiter(daily_limit=3)

    limiter.check_and_consume(1, 100, DAY_ONE)
    limiter.check_and_consume(1, 100, DAY_ONE)

    # Two admissions below the limit of three: no exception, nothing to assert
    # beyond the absence of an error (counters are never exported).
    assert limiter.daily_limit == 3


def test_exactly_at_quota_then_rejected() -> None:
    limiter = make_limiter(daily_limit=2)

    limiter.check_and_consume(1, 100, DAY_ONE)
    limiter.check_and_consume(1, 100, DAY_ONE)

    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, DAY_ONE)


def test_rejected_request_does_not_consume_quota() -> None:
    limiter = make_limiter(daily_limit=2)

    limiter.check_and_consume(1, 100, DAY_ONE)
    limiter.check_and_consume(1, 100, DAY_ONE)
    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, DAY_ONE)

    # The quota stays exhausted: the rejected call consumed nothing, so a later
    # same-day request is still rejected.
    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, DAY_ONE)


def test_quota_resets_on_the_next_utc_day() -> None:
    limiter = make_limiter(daily_limit=1)

    limiter.check_and_consume(1, 100, DAY_ONE)
    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, DAY_ONE)

    # New UTC day: fresh quota.
    limiter.check_and_consume(1, 100, DAY_TWO)


def test_users_have_independent_quotas() -> None:
    limiter = make_limiter(daily_limit=1)

    limiter.check_and_consume(1, 100, DAY_ONE)
    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, DAY_ONE)

    # A different user on the same broker is unaffected.
    limiter.check_and_consume(1, 200, DAY_ONE)


def test_brokers_have_independent_quotas_for_the_same_user_id() -> None:
    # user_id is the primary key, so this cannot occur for real users; the
    # composite key still keeps tenants isolated by construction.
    limiter = make_limiter(daily_limit=1)

    limiter.check_and_consume(1, 100, DAY_ONE)
    limiter.check_and_consume(2, 100, DAY_ONE)


def test_non_utc_time_is_normalized_to_its_utc_day() -> None:
    limiter = make_limiter(daily_limit=1)

    # 2026-09-16 01:00 in UTC+5 is still 2026-09-15 20:00 UTC — same UTC day.
    ahead = datetime(2026, 9, 16, 1, 0, tzinfo=timezone(timedelta(hours=5)))
    limiter.check_and_consume(1, 100, DAY_ONE)
    with pytest.raises(UsageLimitExceededError):
        limiter.check_and_consume(1, 100, ahead)


def test_naive_now_is_rejected_loudly() -> None:
    limiter = make_limiter()

    with pytest.raises(ValueError, match="timezone-aware"):
        limiter.check_and_consume(1, 100, DAY_ONE.replace(tzinfo=None))


def test_limit_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        AgentUsageLimiter(0)


def test_rejection_error_message_is_generic() -> None:
    limiter = make_limiter(daily_limit=1)
    limiter.check_and_consume(1, 100, DAY_ONE)

    with pytest.raises(UsageLimitExceededError) as excinfo:
        limiter.check_and_consume(1, 100, DAY_ONE)

    # No counters, user ids or internals in the message — it becomes a plain
    # generic 429 at the API boundary.
    message = str(excinfo.value)
    assert "100" not in message
    assert "1/1" not in message
    assert "limit" in message


def test_limiter_is_thread_safe_under_concurrent_admission() -> None:
    # Dependencies run on worker threads; the counter must hold exactly at the
    # limit under racing admissions.
    import threading

    limiter = make_limiter(daily_limit=10)
    admitted: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(5):
            try:
                limiter.check_and_consume(1, 100, DAY_ONE)
                with lock:
                    admitted.append(1)
            except UsageLimitExceededError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(admitted) == 10
