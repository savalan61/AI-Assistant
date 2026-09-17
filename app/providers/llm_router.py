"""Deployment LLM routing: selection policy only.

Policy for this one-broker deployment:

* The broker has an ACTIVE, usable configuration -> always use it. A failure
  there is surfaced safely and never silently consumes the shared free pool:
  falling back would hide a broken credential and quietly shift the broker's
  traffic onto the system pool.
* The broker has no active configuration -> use the shared free pool, which
  falls through its own ordered providers on transient failures.

The router performs selection and nothing else. It holds no credential of its
own, never logs, exposes no API key, imports no FastAPI/SQLAlchemy/MT5 code, and
contains no financial or trading capability. Which configuration is "the" one is
decided by the application composition boundary from the single broker row —
never from a request.
"""
from app.providers.llm import LLMPrompt, LLMProvider


class LLMRouter(LLMProvider):
    """Selects the provider this deployment's requests should use."""

    def __init__(
        self,
        *,
        configured_provider: LLMProvider | None,
        free_pool: LLMProvider,
    ) -> None:
        # None means "this deployment has no active configuration" — a
        # deliberate state, not an error.
        self._configured_provider = configured_provider
        self._free_pool = free_pool

    @property
    def uses_configured_provider(self) -> bool:
        """True when this deployment's own configured provider is active."""
        return self._configured_provider is not None

    def complete(self, prompt: LLMPrompt) -> str:
        """Answer through the selected provider, or raise a safe RuntimeError."""
        if self._configured_provider is not None:
            try:
                return self._configured_provider.complete(prompt)
            except RuntimeError as exc:
                # Policy: the configured provider never falls back to the shared
                # free pool. The replacement message is fixed, so no credential,
                # endpoint, or upstream detail can leak through this boundary.
                raise RuntimeError("LLM provider request failed") from exc
        # No configured provider: the free pool applies its own fallback policy
        # and raises one safe RuntimeError when nothing is available.
        return self._free_pool.complete(prompt)
