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
from typing import NamedTuple


class LLMPrompt(NamedTuple):
    """A provider-neutral prompt: framing instructions plus prepared content.

    ``instructions`` is the system-side framing (what the assistant is and what
    it must never do). ``content`` is the prepared, self-contained user-side
    input the model reasons over, rendered by the agent layer.
    """

    instructions: str
    content: str


class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def complete(self, prompt: LLMPrompt) -> str:
        """Return generated text for ``prompt``.

        A provider that cannot serve the request raises RuntimeError, which the
        caller translates at its own boundary; providers never return partial
        or fabricated text on failure.
        """
        ...
