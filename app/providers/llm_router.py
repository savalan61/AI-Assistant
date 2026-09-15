"""Broker-aware LLM routing: selection policy only.

Policy for one authenticated broker:

* The broker has an ACTIVE, usable configuration -> always use the broker's own
  provider. A failure there is surfaced safely and never silently consumes the
  shared free pool: falling back would hide a broken credential and quietly
  shift that broker's traffic onto the system pool.
* The broker has no active configuration -> use the shared free pool, which
  falls through its own ordered providers on transient failures.

The router performs selection and nothing else. It holds no credential of its
own, never logs, exposes no API key, imports no FastAPI/SQLAlchemy/MT5 code, and
contains no financial or trading capability. Tenant identity arrives as a
``broker_id`` bound by the application composition boundary from the
authenticated database user — never from a request body.
"""
from app.providers.llm import LLMPrompt, LLMProvider


class LLMRouter(LLMProvider):
    """Selects the provider one broker's requests should use."""

    def __init__(
        self,
        *,
        broker_id: int,
        broker_provider: LLMProvider | None,
        free_pool: LLMProvider,
    ) -> None:
        self._broker_id = broker_id
        # None means "this broker has no active configuration" — a deliberate
        # state, not an error.
        self._broker_provider = broker_provider
        self._free_pool = free_pool

    @property
    def broker_id(self) -> int:
        """The tenant this router is bound to (from the authenticated user)."""
        return self._broker_id

    @property
    def uses_broker_provider(self) -> bool:
        """True when a broker-specific provider is active for this router."""
        return self._broker_provider is not None

    def complete(self, prompt: LLMPrompt) -> str:
        """Answer through the selected provider, or raise a safe RuntimeError."""
        if self._broker_provider is not None:
            try:
                return self._broker_provider.complete(prompt)
            except RuntimeError as exc:
                # Policy: a broker's own provider never falls back to the shared
                # free pool. The replacement message is fixed, so no credential,
                # endpoint, or upstream detail can leak through this boundary.
                raise RuntimeError("LLM provider request failed") from exc
        # No broker provider: the free pool applies its own fallback policy and
        # raises one safe RuntimeError when nothing is available.
        return self._free_pool.complete(prompt)
