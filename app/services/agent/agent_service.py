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
* what may leave the process is governed by an explicit OutboundDataPolicy
  (egress.py), injected here so a stricter — later per-broker — policy can be
  supplied without touching this class;
* the economic-calendar component (Step 45) is the existing
  EconomicIntelligenceService, injected the same way the financial context is:
  the agent composes, it does not fetch calendar data itself;
* no HTTP endpoint — this is an internal service/domain boundary;
* no agent framework (LangChain, LangGraph, ...) and no tool use;
* read-only by construction: the only capabilities held here are reading a
  context and asking for text, so there is no trading tool and no mutation.
"""
from datetime import UTC, datetime
from typing import NamedTuple

from app.providers.llm import LLMProvider
from app.services.agent.egress import OutboundDataPolicy
from app.services.agent.prompt import build_prompt
from app.services.economic_intelligence import EconomicIntelligenceService
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
    """Answers one authenticated user request from the read-only contexts.

    Depends on FinancialContextService (the single financial-context
    architecture), an optional EconomicIntelligenceService (the single
    economic-intelligence architecture) and an injected LLMProvider, so the
    agent never touches MT5, the database, the calendar or a provider directly,
    and never duplicates their logic.
    """

    def __init__(
        self,
        financial_context_service: FinancialContextService,
        llm_provider: LLMProvider,
        data_policy: OutboundDataPolicy | None = None,
        economic_intelligence_service: EconomicIntelligenceService | None = None,
    ):
        self._context = financial_context_service
        self._llm = llm_provider
        # Resolved once per service: the policy decides which classes of
        # financial data may be rendered into an external prompt. Defaults to
        # the configured policy so existing construction keeps working.
        self._data_policy = data_policy if data_policy is not None else OutboundDataPolicy.from_settings()
        # Today's economic calendar (Step 45) is composed from the EXISTING
        # economic-intelligence service, so the agent gains no calendar logic of
        # its own. Optional so the boundary stays usable without a calendar
        # source: None means the prompt carries no economic block at all, and
        # the production composition root always supplies it.
        self._economic = economic_intelligence_service

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

        The same ``now`` drives both read-only contexts: the trade-history
        window and today's UTC economic-calendar window are computed from one
        reference instant, so they can never disagree about which day "today"
        is. When the caller passes no ``now``, the agent resolves it once here.

        The reads block (MT5 and the calendar provider), so an API caller must
        offload this call through the consolidated MT5 blocking boundary.
        Failures from the context service, the economic-calendar source or the
        LLM provider propagate unchanged — this boundary neither swallows nor
        reinterprets them, leaving translation to the caller's own layer (the
        API maps a RuntimeError to its existing 503). The model is only asked
        once both contexts exist, so a failed read never reaches the provider.
        """
        # One reference instant for the whole request.
        reference = now if now is not None else datetime.now(UTC)
        context = self._context.build(
            broker_id=broker_id,
            trade_history_days=trade_history_days,
            now=reference,
        )
        economic = (
            self._economic.build_today_context(now=reference)
            if self._economic is not None
            else None
        )
        answer = self._llm.complete(
            build_prompt(request, context, self._data_policy, economic)
        )
        return AgentResponse(
            request=request,
            broker_id=broker_id,
            context=context,
            answer=answer,
        )
