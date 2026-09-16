"""In-process login brute-force protection.

The login endpoint is the only credential gate in front of a financial account,
so an unlimited number of attempts against it is not acceptable. This is the
smallest control that removes trivial credential stuffing:

* **Two keys.** Failures are counted per client IP *and* per submitted
  (broker, login), so neither a single address spraying many logins nor many
  addresses targeting one login goes unnoticed.
* **Tenant-scoped login key.** The login key carries the broker code the client
  submitted, so the same MT5 login number at another broker keeps its own
  counter: failures against one tenant's 80009 can never lock another tenant's
  80009 out.
* **Submitted value, not account existence.** The login key is the string the
  client sent, normalized for case, and it is counted whether or not that
  account exists. The rejection behaviour is therefore identical for an
  existing and a non-existing login, so the throttle cannot be used to probe
  which accounts exist.
* **In-process.** A lock-guarded dict in this process's memory, no Redis and no
  database table. It resets on restart and is per worker — the same documented
  limitation as the Agent daily limit, acceptable for the current
  single-process deployment and replaced by a shared store only if/when a
  multi-worker deployment is designed.
* **Bounded.** Stale entries are evicted, so the dict cannot grow without limit
  under a flood of distinct logins or addresses.

Known trade-off: because the lockout is keyed on the submitted (broker, login),
an attacker who floods one login can temporarily lock that user out for the
configured window. The window is intentionally short and configurable, and the
blast radius is one tenant's account rather than every tenant sharing the
number.
"""
import threading
from datetime import UTC, datetime, timedelta

# Above this many tracked keys, a sweep drops every entry whose newest failure
# is already outside the window. Keeps the dict bounded without an O(n) sweep
# on every ordinary login attempt.
_SWEEP_THRESHOLD = 10_000

# Bucket key: (scope, subject, login). The IP bucket is ("ip", <address>, "")
# and the login bucket is ("login", <broker code>, <login>). Keeping the parts
# separate — instead of joining them with a delimiter — means no submitted value
# can be shaped to collide with another tenant's bucket.
_BucketKey = tuple[str, str, str]


def _normalize(value: str) -> str:
    """Case- and padding-insensitive key part (broker codes and logins alike)."""
    return value.strip().lower()


class LoginThrottleExceededError(RuntimeError):
    """Too many failed login attempts for this client IP or (broker, login)."""


class LoginThrottle:
    """Counts failed login attempts per client IP and per submitted (broker, login)."""

    def __init__(self, max_failures: int, window_seconds: int) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._max_failures = max_failures
        self._window = timedelta(seconds=window_seconds)
        # {bucket key: [failure timestamps, oldest first]}; never exported.
        self._failures: dict[_BucketKey, list[datetime]] = {}
        self._lock = threading.Lock()

    @property
    def max_failures(self) -> int:
        return self._max_failures

    @property
    def tracked_keys(self) -> int:
        """Number of keys currently tracked (never exposed to clients).

        Observable so the eviction bound can be asserted directly in tests
        (the LLMProviderPool.size / AgentUsageLimiter.daily_limit convention).
        """
        with self._lock:
            return len(self._failures)

    def check(self, client_ip: str, broker: str, login: str, now: datetime) -> None:
        """Raise when either key has reached the limit inside the window.

        ``broker`` is the broker code as submitted, normalized exactly like the
        login endpoint resolves it, so casing or padding cannot move a failure
        into another bucket. Called before any credential lookup, so a throttled
        attempt costs no database work. Naive ``now`` values are rejected rather
        than guessed as local time (the established project convention).
        """
        self._require_aware(now)
        with self._lock:
            self._sweep_locked(now)
            for key in self._keys(client_ip, broker, login):
                if len(self._recent_locked(key, now)) >= self._max_failures:
                    raise LoginThrottleExceededError("too many failed login attempts")

    def record_failure(self, client_ip: str, broker: str, login: str, now: datetime) -> None:
        """Count one failed attempt against both keys."""
        self._require_aware(now)
        with self._lock:
            self._sweep_locked(now)
            for key in self._keys(client_ip, broker, login):
                recent = self._recent_locked(key, now)
                recent.append(now)
                self._failures[key] = recent

    def record_success(self, client_ip: str, broker: str, login: str) -> None:
        """Clear this client's and this broker/login's counters after a valid login.

        Another tenant's login bucket is left untouched: one tenant's success
        must never erase the failure history recorded against a different
        tenant's identical login number.
        """
        with self._lock:
            for key in self._keys(client_ip, broker, login):
                self._failures.pop(key, None)

    @staticmethod
    def _require_aware(now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

    @staticmethod
    def _keys(client_ip: str, broker: str, login: str) -> tuple[_BucketKey, ...]:
        # Broker and login are both normalized so casing or padding cannot evade
        # the counter, and the broker is part of the login key so one tenant's
        # failures cannot throttle another tenant's identical login number. The
        # IP key stays broker-independent: one address is one address.
        return (("ip", client_ip, ""), ("login", _normalize(broker), _normalize(login)))

    def _recent_locked(self, key: _BucketKey, now: datetime) -> list[datetime]:
        """Failures for ``key`` still inside the window (prunes the entry)."""
        cutoff = now.astimezone(UTC) - self._window
        recent = [stamp for stamp in self._failures.get(key, []) if stamp >= cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def _sweep_locked(self, now: datetime) -> None:
        """Drop stale entries once the dict grows past the sweep threshold."""
        if len(self._failures) <= _SWEEP_THRESHOLD:
            return
        cutoff = now.astimezone(UTC) - self._window
        stale = [key for key, stamps in self._failures.items() if not stamps or stamps[-1] < cutoff]
        for key in stale:
            del self._failures[key]
