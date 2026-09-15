"""OpenAI-compatible chat/completions LLM adapter.

The production counterpart to FakeLLMProvider: implements the existing
LLMProvider contract unchanged against any server exposing the
OpenAI-compatible ``POST {base_url}/chat/completions`` shape (a hosted vendor
or a self-hosted runtime). The adapter is deliberately vendor-agnostic — it
assumes no specific provider beyond that widely-supported wire format.

Design notes:

* Configuration (base URL, model, API key, timeout) is injected from
  application settings; nothing is hard-coded, and the key is never logged or
  embedded in an exception message.
* It uses httpx, which is already present in the project's dependency tree
  (starlette's TestClient depends on it) — no new HTTP framework is added.
  The HTTP transport is injectable (constructor parameter) so tests can
  substitute a mock without touching the network; production uses the
  default real transport.
* Failures are translated at this provider boundary into RuntimeError, exactly
  like the MT5 providers, so the existing API 503 behavior is preserved. HTTP
  errors, malformed payloads, and missing configuration all fail closed; a
  successful HTTP status with an unusable body is an error, never fabricated
  text.
* Transient failures (rate limiting, server-side errors, timeouts, transport
  errors) raise LLMFallbackError — a RuntimeError subclass — so an ordered
  provider pool may try its next provider. Authentication, request, and
  malformed-response failures raise plain RuntimeError and are never retried
  elsewhere, because that would mask a real misconfiguration.
* Synchronous by contract: it runs on the worker threadpool behind the
  existing run_mt5_call boundary, never on the event loop.
* Read-only: it can only generate text from a prompt. There is no tool, no
  function calling, and no parameter that could trade or mutate state.
"""
import httpx

from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider

# The one configuration requirement without a safe default: without a model the
# request would be ambiguous, so the adapter is unavailable rather than guessing.
_UNCONFIGURED_MODEL = "LLM model is not configured"
_UNCONFIGURED_KEY = "LLM API key is not configured"

# Ranges copied from the OpenAI-compatible chat/completions contract. Not
# enumerated as constants beyond this: the adapter treats the API as opaque
# JSON over HTTP and validates only what it consumes.
_TEMPERATURE = 0.2  # low but non-zero: deterministic-leaning, still natural

# Default real-world transport. Production uses this; tests inject a
# httpx.MockTransport instead, keeping the suite fully offline.
_DEFAULT_TRANSPORT = httpx.HTTPTransport()


class OpenAICompatibleLLMProvider(LLMProvider):
    """Generate text via an OpenAI-compatible chat/completions endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        # Fail fast on missing configuration, before any network call: an empty
        # key or model means the deployment intended the fake provider.
        if not api_key.strip() or api_key.strip().startswith("YOUR_"):
            raise RuntimeError(_UNCONFIGURED_KEY)
        if not model.strip():
            raise RuntimeError(_UNCONFIGURED_MODEL)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport if transport is not None else _DEFAULT_TRANSPORT

    def complete(self, prompt: LLMPrompt) -> str:
        """Return the assistant's reply for ``prompt``; raise RuntimeError on failure."""
        try:
            # A short-lived client per call: this adapter is invoked at most
            # once per agent request on a worker thread, so pooling adds no
            # value here, and the transport stays injectable for tests.
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": prompt.instructions},
                            {"role": "user", "content": prompt.content},
                        ],
                        "temperature": _TEMPERATURE,
                    },
                )
        except httpx.HTTPError:
            # Network/timeout/protocol failure: a generic boundary error. The
            # exception detail (which could contain the URL) never propagates.
            # Treated as transient so a provider pool may try the next provider.
            raise LLMFallbackError("LLM provider unavailable")
        return self._extract_text(response)

    def _extract_text(self, response: httpx.Response) -> str:
        """Validate the response and return the first choice's message text."""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Only transient upstream conditions are fallback-eligible: rate
            # limiting and server-side failures. Everything else (auth, bad
            # request) is a configuration problem that must fail loudly rather
            # than be silently retried against another provider.
            if exc.response.status_code == 429 or exc.response.status_code >= 500:
                raise LLMFallbackError("LLM provider is temporarily unavailable") from exc
            raise RuntimeError("LLM provider returned an invalid response") from exc
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            # Non-JSON body or an unexpected shape: the payload is never echoed
            # into the error (it could embed secrets echoed by a misbehaving
            # server), so failures stay generic.
            raise RuntimeError("LLM provider returned an invalid response")
        # Distinguish the two unusable-text cases: a non-string content field
        # is a malformed payload; a string without content is merely empty.
        if not isinstance(text, str):
            raise RuntimeError("LLM provider returned an invalid response")
        if not text.strip():
            raise RuntimeError("LLM provider returned an empty response")
        return text
