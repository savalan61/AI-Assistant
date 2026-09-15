"""Tests for the ordered LLM provider pool.

Offline only: the pool is exercised with deterministic fake free providers, so
no network call, credential, or real provider is involved.
"""
import pytest

from app.providers.fake_llm import FakeFreeLLMProvider
from app.providers.llm import LLMFallbackError, LLMPrompt, LLMProvider
from app.providers.llm_pool import LLMProviderPool

PROMPT = LLMPrompt(instructions="be factual", content="what is my exposure?")


def test_pool_implements_the_provider_contract() -> None:
    assert isinstance(LLMProviderPool([FakeFreeLLMProvider("only")]), LLMProvider)


def test_first_provider_success_prevents_later_providers_being_called() -> None:
    first = FakeFreeLLMProvider("first", response="from first")
    second = FakeFreeLLMProvider("second", response="from second")

    answer = LLMProviderPool([first, second]).complete(PROMPT)

    assert answer == "from first"
    assert first.call_count == 1
    assert second.call_count == 0


def test_fallback_eligible_failure_tries_the_next_provider() -> None:
    first = FakeFreeLLMProvider("first", error=LLMFallbackError("rate limited"))
    second = FakeFreeLLMProvider("second", response="from second")

    answer = LLMProviderPool([first, second]).complete(PROMPT)

    assert answer == "from second"
    assert first.call_count == 1
    assert second.call_count == 1


def test_providers_are_tried_in_order_until_one_succeeds() -> None:
    providers = [FakeFreeLLMProvider(f"p{index}", error=LLMFallbackError("busy")) for index in range(3)]
    winner = FakeFreeLLMProvider("winner", response="answer")
    providers.append(winner)

    answer = LLMProviderPool(providers).complete(PROMPT)

    assert answer == "answer"
    assert [provider.call_count for provider in providers] == [1, 1, 1, 1]


def test_non_fallback_failure_stops_the_pool_immediately() -> None:
    # An authentication/configuration-shaped failure must not be masked by
    # trying another provider: the pool stops and the caller sees the failure.
    first = FakeFreeLLMProvider("first", error=RuntimeError("invalid credentials"))
    second = FakeFreeLLMProvider("second", response="from second")

    with pytest.raises(RuntimeError, match="invalid credentials"):
        LLMProviderPool([first, second]).complete(PROMPT)

    assert first.call_count == 1
    assert second.call_count == 0


def test_each_provider_is_tried_at_most_once() -> None:
    first = FakeFreeLLMProvider("first", error=LLMFallbackError("busy"))
    second = FakeFreeLLMProvider("second", error=LLMFallbackError("busy"))

    with pytest.raises(RuntimeError):
        LLMProviderPool([first, second]).complete(PROMPT)

    assert first.call_count == 1
    assert second.call_count == 1


def test_all_providers_failing_produces_one_safe_error() -> None:
    secret = "sk-pool-secret-must-not-leak"
    providers = [
        FakeFreeLLMProvider("first", error=LLMFallbackError(f"429 unauthorized for {secret}")),
        FakeFreeLLMProvider("second", error=LLMFallbackError("upstream timeout")),
    ]

    with pytest.raises(RuntimeError) as excinfo:
        LLMProviderPool(providers).complete(PROMPT)

    message = str(excinfo.value)
    assert message == "no LLM provider is currently available"
    assert secret not in message
    assert "429" not in message
    # The terminal failure is not itself fallback-eligible: a caller must not
    # try to fall through again from here.
    assert not isinstance(excinfo.value, LLMFallbackError)


def test_empty_pool_fails_safely_instead_of_raising_at_composition() -> None:
    pool = LLMProviderPool([])

    assert pool.size == 0
    with pytest.raises(RuntimeError, match="no LLM provider is currently available"):
        pool.complete(PROMPT)


def test_pool_snapshots_its_providers() -> None:
    providers = [FakeFreeLLMProvider("first", response="ok")]
    pool = LLMProviderPool(providers)

    # Later mutation of the caller's list cannot change the pool's order.
    providers.append(FakeFreeLLMProvider("second"))

    assert pool.size == 1
    assert pool.complete(PROMPT) == "ok"
