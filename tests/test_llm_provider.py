"""Tests for the LLM provider contract and the deterministic FakeLLMProvider.

Require none of: network, API keys, credentials, or any external model. The
contract is checked directly and the fake is exercised as the only provider
implementation in this step.
"""
import pytest

from app.providers.fake_llm import DEFAULT_FAKE_RESPONSE, FakeLLMProvider
from app.providers.llm import LLMPrompt, LLMProvider

PROMPT = LLMPrompt(instructions="be factual", content="what is my exposure?")


def test_llm_provider_declares_complete_as_abstract() -> None:
    assert getattr(LLMProvider.complete, "__isabstractmethod__", False) is True


def test_llm_provider_cannot_be_instantiated_without_complete() -> None:
    # The contract is enforced: an implementation must supply complete().
    incomplete = type("IncompleteLLMProvider", (LLMProvider,), {})

    with pytest.raises(TypeError):
        incomplete()


def test_llm_prompt_is_a_provider_neutral_pair() -> None:
    assert set(LLMPrompt._fields) == {"instructions", "content"}
    assert PROMPT.instructions == "be factual"
    assert PROMPT.content == "what is my exposure?"


def test_fake_provider_implements_the_contract() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)


def test_fake_provider_returns_deterministic_text() -> None:
    provider = FakeLLMProvider()

    first = provider.complete(PROMPT)
    second = provider.complete(PROMPT)

    assert first == second == DEFAULT_FAKE_RESPONSE


def test_default_response_is_an_explicit_placeholder() -> None:
    # Development output must never read as a real model answer.
    provider = FakeLLMProvider()

    answer = provider.complete(PROMPT)

    assert "placeholder" in answer.lower()


def test_fake_provider_returns_a_configured_response() -> None:
    provider = FakeLLMProvider(response="stubbed")

    assert provider.complete(PROMPT) == "stubbed"


def test_fake_provider_records_prompts_and_call_count() -> None:
    provider = FakeLLMProvider()

    provider.complete(PROMPT)
    provider.complete(LLMPrompt(instructions="other", content="second"))

    assert provider.call_count == 2
    assert provider.prompts[0] is PROMPT
    assert provider.prompts[1].content == "second"


def test_provider_works_without_any_financial_context() -> None:
    # The provider needs nothing but a prepared prompt: no FinancialContext,
    # no FastAPI request, no database session, no authenticated user.
    answer = FakeLLMProvider().complete(LLMPrompt(instructions="i", content="c"))

    assert isinstance(answer, str)


def test_provider_modules_do_not_depend_on_app_layers() -> None:
    # The provider boundary must stay free of financial, persistence, transport
    # and MT5 concepts, so a real vendor can be added without touching them.
    import app.providers.fake_llm as fake_llm_module
    import app.providers.llm as llm_module

    forbidden = {
        "FinancialContext",
        "FinancialContextService",
        "AgentService",
        "User",
        "AsyncSession",
        "FastAPI",
        "MetaTrader5",
        "mt5",
    }
    for module in (llm_module, fake_llm_module):
        assert not (set(vars(module)) & forbidden)
