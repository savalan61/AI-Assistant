"""LLM provider abstraction: the boundary between the agent and any model.

Deliberately tiny and provider-agnostic: one synchronous call that takes a
prepared prompt and returns generated text. It knows nothing about
FinancialContext, FastAPI, SQLAlchemy, MT5 or authentication models — the agent
layer prepares the prompt, so a concrete provider (a hosted API, a local
runtime, or any other vendor) can be added later without changing the agent or
this contract.

Read-only: the contract exposes generation only. There is no tool, function or
argument that can trade, mutate account state, or reach MT5.
"""
import abc
from enum import StrEnum
from typing import NamedTuple


class LLMProviderKind(StrEnum):
    """Kinds of LLM provider a broker may configure.

    A StrEnum (mirrors UserRole / PositionType) so the stored value is a typed
    domain value and a new provider kind can only be introduced deliberately;
    the database stores the plain value. Only the OpenAI-compatible wire format
    is implemented today, so this is the sole member until another provider
    kind is genuinely added (e.g. a future pooled provider).
    """

    OPENAI_COMPATIBLE = "openai_compatible"


class LLMPrompt(NamedTuple):
    """A provider-neutral prompt: framing instructions plus prepared content.

    ``instructions`` is the system-side framing (what the assistant is and what
    it must never do). ``content`` is the prepared, self-contained user-side
    input the model reasons over, rendered by the agent layer.
    """

    instructions: str
    content: str


class LLMFallbackError(RuntimeError):
    """A fallback-eligible provider failure.

    Raised only for transient conditions: rate limiting, timeouts, and upstream
    unavailability. Authentication, configuration, and malformed-response
    failures deliberately raise plain RuntimeError instead, so an ordered
    provider pool stops on them rather than masking a real misconfiguration by
    trying somewhere else.

    Subclasses RuntimeError so every existing caller that translates provider
    failures (for example into HTTP 503) keeps working unchanged.
    """


class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def complete(self, prompt: LLMPrompt) -> str:
        """Return generated text for ``prompt``.

        A provider that cannot serve the request raises RuntimeError, which the
        caller translates at its own boundary; providers never return partial
        or fabricated text on failure. Transient failures raise LLMFallbackError
        so a provider pool may try the next provider.
        """
        ...
