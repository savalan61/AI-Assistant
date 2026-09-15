"""In-process, per-user daily Agent usage limiting.

Enforces a configurable maximum number of Agent requests per authenticated
user per day, before any context read or LLM call. Deliberately simple for the
current single-process development architecture:

* **In-process**: a lock-guarded counter in this process's memory. Counts reset
  on process restart and are not shared across workers — a known, accepted
  limitation of the development deployment, replaced later by a shared store
  if/when multi-worker deployment arrives. No Redis, no database table.
* **Identity**: keyed by the authenticated ``user_id`` (the database ``User.id``),
  so tenants are isolated by construction — the key is ``(broker_id, user_id)``
  and ``user_id`` alone is already globally unique. Identity comes only from
  the authenticated user object, never from the request body.
* **Day boundary**: UTC calendar day, consistent with the application's UTC
  conventions. The day is injected (``now``), so tests stay deterministic.
* **Ordering guarantee**: a request is either admitted (count incremented) or
  rejected; a rejected request never consumes quota, and the check happens
  before any MT5 read or LLM call.
"""
import threading
from datetime import UTC, datetime


class UsageLimitExceededError(RuntimeError):
    """The authenticated user has reached their daily Agent request limit."""


class AgentUsageLimiter:
    """Counts admitted Agent requests per user per UTC day (in process)."""

    def __init__(self, daily_limit: int):
        if daily_limit < 1:
            raise ValueError("daily_limit must be at least 1")
        self._daily_limit = daily_limit
        # {(user_id, day): admitted_count}; guarded, never exported.
        self._counts: dict[tuple[int, str], int] = {}
        self._lock = threading.Lock()

    @property
    def daily_limit(self) -> int:
        return self._daily_limit

    def check_and_consume(self, broker_id: int, user_id: int, now: datetime) -> None:
        """Admit one request for the user on ``now``'s UTC day, or raise.

        ``broker_id``/``user_id`` must come from the authenticated database
        user. Naive ``now`` values are rejected loudly rather than guessed as
        local time (the established project convention). Raises
        UsageLimitExceededError when the user has already reached the daily
        limit; a rejected request consumes nothing.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        key = (broker_id, user_id, now.astimezone(UTC).date().isoformat())
        with self._lock:
            current = self._counts.get(key, 0)
            if current >= self._daily_limit:
                # Generic: callers translate this into a plain 429; counters
                # and internals are never surfaced to clients.
                raise UsageLimitExceededError("daily agent request limit exceeded")
            self._counts[key] = current + 1
