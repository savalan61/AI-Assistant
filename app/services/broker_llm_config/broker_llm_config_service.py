"""Broker LLM configuration: connection testing through the provider boundary.

A broker's stored credentials are exercised with one tiny probe prompt so the
super_admin can confirm the configuration works before the assistant relies on
it. The service reaches no MT5 account state and performs no trading operation:
its only capability is asking a configured LLM provider for one short reply.

It also does no persistence and no decryption itself — the API layer passes the
already-decrypted credentials in memory, so this boundary never sees the
database, the tenant, or the ciphertext.
"""
from enum import StrEnum

from app.core.config import settings
from app.providers.llm import LLMPrompt
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider


class LLMConnectionStatus(StrEnum):
    """Outcome of a connection test, surfaced to the API as safe metadata."""

    OK = "OK"
    FAILED = "FAILED"


# Deliberately tiny and non-financial: the probe proves the endpoint, the
# credentials and the model name work. Its answer is never used or returned,
# and it asks for nothing about the user's account.
_PROBE_PROMPT = LLMPrompt(
    instructions="You are a connectivity probe. Reply with a single short word.",
    content="Reply with the single word OK.",
)


class LLMConnectionTester:
    """Checks a broker's configured LLM credentials through the provider seam."""

    def __init__(self, timeout_seconds: float | None = None) -> None:
        # Resolved once (by the composition root) so the process-wide timeout
        # setting is read at construction rather than on every check.
        self._timeout_seconds = settings.LLM_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds

    def check(self, api_key: str, base_url: str, model: str) -> None:
        """Return normally when the provider answered; raise RuntimeError otherwise.

        ``api_key`` is held in memory for the duration of the call only. Every
        expected failure (unreachable endpoint, rejected credentials, malformed
        or empty answer, missing configuration) surfaces as the provider's
        generic RuntimeError — never carrying the key, the URL, or the payload —
        so the caller can translate it into a safe result. Unexpected
        exceptions propagate unchanged.
        """
        provider = OpenAICompatibleLLMProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=self._timeout_seconds,
        )
        provider.complete(_PROBE_PROMPT)
