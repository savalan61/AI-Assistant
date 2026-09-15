from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider

# Deterministic development/test output. Explicitly labelled a placeholder so it
# can never be mistaken for a real model answer.
DEFAULT_FAKE_RESPONSE = (
    "Deterministic development placeholder: no external model was called and no "
    "financial advice is provided."
)


# Minimal in-memory provider for tests and development; implements the contract
# without a network call, an API key, or any vendor SDK. The response is fixed,
# so tests are repeatable (the FakeMarketDataProvider pattern, applied to LLMs).
class FakeLLMProvider(LLMProvider):
    def __init__(self, response: str = DEFAULT_FAKE_RESPONSE):
        self.response = response
        self.call_count = 0
        self.prompts: list[LLMPrompt] = []

    def complete(self, prompt: LLMPrompt) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        return self.response


# Deterministic stand-in for a free-tier provider, so the free pool's ordering
# and fallback behavior can be tested completely offline. It never reaches a
# network, holds no credential, and can be told to fail with either a
# fallback-eligible (LLMFallbackError) or a non-fallback (RuntimeError) error.
class FakeFreeLLMProvider(FakeLLMProvider):
    def __init__(self, name: str, response: str = DEFAULT_FAKE_RESPONSE, error: Exception | None = None):
        super().__init__(response)
        # A label for assertions about ordering; not user-visible output.
        self.name = name
        self.error = error

    def complete(self, prompt: LLMPrompt) -> str:
        # Bind the configured error to a local: reading it once keeps the
        # narrowing valid across the bookkeeping calls below and makes the
        # "no error -> normal completion" path obvious.
        error = self.error
        if error is not None:
            # Record the attempt before failing so tests can count how often
            # each provider was tried.
            self.call_count += 1
            self.prompts.append(prompt)
            raise error
        return super().complete(prompt)


def fallback_error(message: str = "rate limited") -> LLMFallbackError:
    """Build the designated fallback-eligible error (test convenience)."""
    return LLMFallbackError(message)
