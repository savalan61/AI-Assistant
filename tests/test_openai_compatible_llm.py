"""Tests for the OpenAI-compatible LLM adapter (contract, config, failures).

Require none of: network, a real LLM, API keys, or credentials. HTTP calls are
intercepted with an httpx MockTransport, so the adapter's wire behavior is
exercised end-to-end without any external service.
"""
import json

import httpx
import pytest

from app.providers.fake_llm import FakeLLMProvider
from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider
from app.providers.openai_compatible_llm import OpenAICompatibleLLMProvider

PROMPT = LLMPrompt(instructions="be factual", content="what is my exposure?")


def valid_payload(text: str = "A factual answer.") -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def make_provider(
    handler,
    *,
    api_key: str = "test-key",
    base_url: str = "https://llm.example/v1",
    model: str = "test-model",
) -> OpenAICompatibleLLMProvider:
    """Build the adapter around an injected mock transport (no real network)."""
    return OpenAICompatibleLLMProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        transport=httpx.MockTransport(handler),
    )

def captured_request(handler) -> httpx.Request:
    """Run one completion through the handler and return the captured request."""
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    make_provider(recording).complete(PROMPT)
    assert len(requests) == 1
    return requests[0]


# --- construction-time configuration -------------------------------------------------


def test_adapter_implements_the_contract() -> None:
    provider = OpenAICompatibleLLMProvider(api_key="k", base_url="https://x/v1", model="m")

    assert isinstance(provider, LLMProvider)


def test_empty_api_key_fails_closed_without_a_network_call() -> None:
    with pytest.raises(RuntimeError, match="key is not configured"):
        OpenAICompatibleLLMProvider(api_key="  ", base_url="https://x/v1", model="m")


def test_empty_model_fails_closed_without_a_network_call() -> None:
    with pytest.raises(RuntimeError, match="model is not configured"):
        OpenAICompatibleLLMProvider(api_key="k", base_url="https://x/v1", model="")


def test_placeholder_key_is_rejected() -> None:
    # A .env template value must not silently become a live credential.
    with pytest.raises(RuntimeError, match="key is not configured"):
        OpenAICompatibleLLMProvider(api_key="YOUR_API_KEY", base_url="https://x/v1", model="m")


# --- wire format ---------------------------------------------------------------------


def test_sends_openai_compatible_request() -> None:
    request = captured_request(
        lambda request: httpx.Response(200, json=valid_payload())
    )

    assert request.method == "POST"
    assert str(request.url) == "https://llm.example/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": PROMPT.instructions},
        {"role": "user", "content": PROMPT.content},
    ]
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Content-Type"] == "application/json"


def test_base_url_trailing_slash_is_normalized() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=valid_payload())

    provider = make_provider(handler, base_url="https://llm.example/v1/")
    provider.complete(PROMPT)

    assert str(requests[0].url) == "https://llm.example/v1/chat/completions"


# --- success paths -------------------------------------------------------------------


def test_returns_the_first_choice_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=valid_payload("The answer."))

    assert make_provider(handler).complete(PROMPT) == "The answer."


def test_uses_the_first_message_when_multiple_choices() -> None:
    payload = {
        "choices": [
            {"message": {"content": "first"}},
            {"message": {"content": "second"}},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    assert make_provider(handler).complete(PROMPT) == "first"


# --- failure translation --------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_non_transient_http_status_raises_plain_runtime_error(status: int) -> None:
    # Authentication and request failures are configuration problems: they must
    # not be silently retried against another provider by a pool.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(RuntimeError, match="invalid response") as exc_info:
        make_provider(handler).complete(PROMPT)
    assert not isinstance(exc_info.value, LLMFallbackError)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_status_raises_fallback_error(status: int) -> None:
    # Rate limiting and upstream unavailability are fallback-eligible, so an
    # ordered provider pool may try its next provider.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    with pytest.raises(LLMFallbackError, match="temporarily unavailable"):
        make_provider(handler).complete(PROMPT)


def test_network_failure_raises_fallback_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    # A transport failure is transient, so a pool may fall through from it.
    with pytest.raises(LLMFallbackError, match="LLM provider unavailable"):
        make_provider(handler).complete(PROMPT)


def test_timeout_raises_fallback_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(LLMFallbackError, match="LLM provider unavailable"):
        make_provider(handler).complete(PROMPT)


def test_non_json_body_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(RuntimeError, match="invalid response"):
        make_provider(handler).complete(PROMPT)


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({}, "invalid response"),
        ({"choices": []}, "invalid response"),
        ({"choices": [{}]}, "invalid response"),
        ({"choices": [{"message": {}}]}, "invalid response"),
        ({"choices": [{"message": {"content": None}}]}, "invalid response"),
        ({"choices": [{"message": {"content": 42}}]}, "invalid response"),
    ],
)
def test_malformed_payloads_raise_runtime_error(
    payload: dict[str, object], expected_error: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(RuntimeError, match=expected_error):
        make_provider(handler).complete(PROMPT)


def test_empty_text_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=valid_payload("   "))

    with pytest.raises(RuntimeError, match="empty response"):
        make_provider(handler).complete(PROMPT)


# --- secret safety ---------------------------------------------------------------------


def test_error_messages_never_contain_the_api_key_or_url() -> None:
    secret = "super-secret-key-value"

    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    for handler in (failing, invalid):
        with pytest.raises(RuntimeError) as exc_info:
            make_provider(handler, api_key=secret, base_url="https://secret-host.example/v1").complete(PROMPT)
        assert secret not in str(exc_info.value)
        assert "secret-host" not in str(exc_info.value)


def test_error_messages_never_contain_the_payload() -> None:
    leaked = "sensitive-server-echo"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": leaked})

    with pytest.raises(RuntimeError) as exc_info:
        make_provider(handler).complete(PROMPT)

    assert leaked not in str(exc_info.value)


def test_module_source_contains_no_hardcoded_credential() -> None:
    import inspect

    from app.providers import openai_compatible_llm as module

    source = inspect.getsource(module)
    assert "sk-" not in source
    assert "Bearer sk" not in source


# --- contract boundaries ---------------------------------------------------------------


def test_fake_provider_still_implements_the_contract() -> None:
    # Both implementations satisfy the same seam; tests may keep using the fake.
    assert isinstance(FakeLLMProvider(), LLMProvider)


def test_adapter_uses_the_unchanged_llm_prompt_contract() -> None:
    request = captured_request(lambda request: httpx.Response(200, json=valid_payload()))

    body = json.loads(request.content)
    assert body["messages"][0]["content"] == "be factual"
    assert body["messages"][1]["content"] == "what is my exposure?"
