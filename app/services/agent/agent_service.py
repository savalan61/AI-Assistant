"""Minimal AI agent boundary (read-only).

Turns an authenticated user request into an answer: it resolves the request
against the existing FinancialContextService, prepares a provider-neutral
prompt, and asks the injected LLMProvider for text. It orchestrates and
composes; it computes no finance of its own.

Design notes:

* the model is behind the LLMProvider abstraction — the agent never names,
  configures or imports a vendor, and holds no API key or model setting;
* the prompt is prepared in the agent layer (prompt.py), so the provider does
  not depend on FinancialContext and the agent does not depend on any vendor's
  request format;
* no HTTP endpoint — this is an internal service/domain boundary;
* no agent framework (LangChain, LangGraph, ...) and no tool use;
* read-only by construction: the only capabilities held here are reading a
  context and asking for text, so there is no trading tool and no mutation.
"""
from datetime import datetime
from typing import NamedTuple

from app.providers.llm import LLMProvider
from app.services.agent.prompt import build_prompt
from app.services.financial_context import (
    DEFAULT_TRADE_HISTORY_DAYS,
    FinancialContext,
    FinancialContextService,
)


class AgentResponse(NamedTuple):
    """The agent's answer envelope for one request.

    ``context`` is the read-only financial snapshot the request was resolved
    against. ``request`` echoes the caller's message verbatim (no rewriting,
    no normalization) so an answer can be attributed to the exact request it
    came from. ``answer`` is the text the injected provider returned, unchanged.
    """

    request: str
    broker_id: int
    context: FinancialContext
    answer: str


class AgentService:
    """Answers one authenticated user request from the read-only context.

    Depends on FinancialContextService (the single financial-context
    architecture) and an injected LLMProvider, so the agent never touches MT5,
    the database, or a provider directly, and never duplicates
    financial-context logic.
    """

    def __init__(self, financial_context_service: FinancialContextService, llm_provider: LLMProvider):
        self._context = financial_context_service
        self._llm = llm_provider

    def handle(
        self,
        request: str,
        broker_id: int,
        trade_history_days: int = DEFAULT_TRADE_HISTORY_DAYS,
        now: datetime | None = None,
    ) -> AgentResponse:
        """Answer ``request`` from the current financial context.

        ``broker_id`` must come from the authenticated database user; the agent
        never derives tenant identity from the request text. The context's
        trade-history window stays configurable with the existing 30-day
        default. ``now`` injects the reference time (defaults to the current
        UTC time) so callers and tests stay deterministic.

        The context read blocks (MT5), so an API caller must offload this call
        through the consolidated MT5 blocking boundary. Failures from the
        context service or the LLM provider propagate unchanged — this boundary
        neither swallows nor reinterprets them, leaving translation to the
        caller's own layer. The model is only asked once the context exists, so
        a failed read never reaches the provider.
        """
        context = self._context.build(
            broker_id=broker_id,
            trade_history_days=trade_history_days,
            now=now,
        )
        answer = self._llm.complete(build_prompt(request, context))
        return AgentResponse(
            request=request,
            broker_id=broker_id,
            context=context,
            answer=answer,
        )
