"""Ordered LLM provider pool with explicit fallback semantics.

The system's free tier is a list of providers tried in order. The next provider
is attempted only when the current one raises ``LLMFallbackError`` — the
designated, transient failure. Any other failure stops the pool immediately, so
a misconfigured or rejected provider is never masked by trying elsewhere.

When every provider is unavailable, the pool raises one safe RuntimeError: no
provider message, endpoint, or credential travels past this boundary. The pool
holds providers and nothing else — no Redis, no queues, no background workers,
no token accounting, no billing.
"""
from collections.abc import Sequence

from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider


class LLMProviderPool(LLMProvider):
    """Try providers in order, falling through only on fallback-eligible errors."""

    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        # An empty pool is valid: it fails safely at call time (a generic
        # RuntimeError the caller translates), rather than exploding at
        # composition time in a deployment that has configured no provider.
        self._providers: tuple[LLMProvider, ...] = tuple(providers)

    @property
    def size(self) -> int:
        """Number of providers in the pool (never exposed to clients)."""
        return len(self._providers)

    def complete(self, prompt: LLMPrompt) -> str:
        """Return the first provider's answer, or raise one safe RuntimeError.

        ``LLMFallbackError`` from a provider means "transient — try the next
        one". Any other RuntimeError propagates unchanged: it is a configuration
        or request-level failure, and the pool must not paper over it.
        """
        for provider in self._providers:
            try:
                return provider.complete(prompt)
            except LLMFallbackError:
                continue
        raise RuntimeError("no LLM provider is currently available")
