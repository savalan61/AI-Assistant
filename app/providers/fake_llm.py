from app.providers.llm import LLMPrompt, LLMProvider

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
