"""Tests for the AgentService boundary (context + LLM provider orchestration).

Require none of: real MT5, PostgreSQL, network, credentials, or an external LLM.
The boundary is exercised with a recording FinancialContextService double and
the deterministic FakeLLMProvider, and one integration test drives the real
FinancialContextService over in-memory provider fakes (the established
fake-provider pattern). No pytest asyncio plugin.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.providers.account_info import AccountInfo, AccountInfoProvider
from app.providers.fake_llm import FakeLLMProvider
from app.providers.fake_position import FakePositionProvider
from app.providers.fake_trade_history import FakeTradeHistoryProvider
from app.providers.llm import LLMPrompt, LLMProvider
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeHistoryEntry, TradeType
from app.services.account import AccountInfoService
from app.services.agent import AgentResponse, AgentService, build_prompt
from app.services.financial_context import (
    DEFAULT_TRADE_HISTORY_DAYS,
    FinancialContext,
    FinancialContextService,
)
from app.services.portfolio_intelligence import build_portfolio_intelligence
from app.services.positions import PositionService
from app.services.trade_history import TradeHistoryService

AS_OF = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

ACCOUNT = AccountInfo(
    login=10001,
    name="Test Trader",
    balance=10000.0,
    equity=10050.0,
    margin=250.0,
    free_margin=9800.0,
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

POSITIONS: tuple[Position, ...] = (
    Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=0.10,
        open_price=3642.50,
        current_price=3648.20,
        profit=57.00,
    ),
    Position(
        ticket=987654321,
        symbol="EURUSD",
        type=PositionType.SELL,
        volume=1.00,
        open_price=1.0850,
        current_price=1.0820,
        profit=-30.00,
    ),
)

TRADES: tuple[TradeHistoryEntry, ...] = (
    TradeHistoryEntry(
        ticket=246802468,
        order_ticket=987654321,
        symbol="XAUUSD",
        type=TradeType.BUY,
        volume=0.10,
        price=3648.20,
        profit=57.00,
        time=datetime(2026, 9, 14, 12, 30, 0, tzinfo=UTC),
        close_reason=None,
        stop_loss=3635.00,
        take_profit=3650.00,
    ),
)


class _FakeAccountInfoProvider(AccountInfoProvider):
    """Deterministic in-memory account provider (no MT5)."""

    def __init__(self, account: AccountInfo = ACCOUNT) -> None:
        self.account = account
        self.call_count = 0

    def get_account_info(self) -> AccountInfo:
        self.call_count += 1
        return self.account


class _RecordingFinancialContextService(FinancialContextService):
    """Test double recording build() calls; returns a canned context or raises."""

    def __init__(self, context: FinancialContext, error: Exception | None = None) -> None:
        self.context = context
        self.error = error
        self.calls: list[tuple[int, int, datetime | None]] = []

    def build(
        self,
        broker_id: int,
        trade_history_days: int = DEFAULT_TRADE_HISTORY_DAYS,
        now: datetime | None = None,
    ) -> FinancialContext:
        self.calls.append((broker_id, trade_history_days, now))
        if self.error is not None:
            raise self.error
        return self.context


class _FailingLLMProvider(LLMProvider):
    def complete(self, prompt: LLMPrompt) -> str:
        raise RuntimeError("model unavailable")


def make_context(broker_id: int = 1, as_of: datetime = AS_OF) -> FinancialContext:
    return FinancialContext(
        broker_id=broker_id,
        as_of=as_of,
        account=ACCOUNT,
        positions=POSITIONS,
        trade_history=TRADES,
        portfolio_intelligence=build_portfolio_intelligence(ACCOUNT, POSITIONS, as_of),
    )


def make_real_service() -> tuple[
    FinancialContextService, _FakeAccountInfoProvider, FakePositionProvider, FakeTradeHistoryProvider
]:
    account_provider = _FakeAccountInfoProvider()
    position_provider = FakePositionProvider(positions=POSITIONS)
    trade_provider = FakeTradeHistoryProvider(trades=TRADES)
    service = FinancialContextService(
        account_service=AccountInfoService(account_provider),
        position_service=PositionService(position_provider),
        trade_history_service=TradeHistoryService(trade_provider),
    )
    return service, account_provider, position_provider, trade_provider


def make_agent(
    context: FinancialContext | None = None,
    *,
    context_error: Exception | None = None,
    llm: LLMProvider | None = None,
) -> tuple[AgentService, _RecordingFinancialContextService, LLMProvider]:
    double = _RecordingFinancialContextService(context if context is not None else make_context(), context_error)
    provider: LLMProvider = llm if llm is not None else FakeLLMProvider()
    return AgentService(double, provider), double, provider


# --- delegation to the injected context service -----------------------------------------


def test_agent_obtains_context_through_the_injected_service() -> None:
    agent, double, _ = make_agent()

    response = agent.handle("What is my exposure?", broker_id=1, now=AS_OF)

    # Exactly one context build, and its result is what the agent returns.
    assert len(double.calls) == 1
    assert response.context is double.context


def test_agent_reads_the_context_exactly_once() -> None:
    agent, double, _ = make_agent()

    agent.handle("Anything I should know today?", broker_id=1, now=AS_OF)

    assert len(double.calls) == 1


def test_context_is_returned_unchanged() -> None:
    context = make_context(broker_id=9)
    agent, _, _ = make_agent(context)

    response = agent.handle("hello", broker_id=9, now=AS_OF)

    assert response.context == context
    assert response.context.broker_id == 9
    assert response.context.portfolio_intelligence.open_positions == 2


# --- request and tenant propagation ------------------------------------------------------


def test_user_request_is_preserved_verbatim() -> None:
    agent, _, _ = make_agent()
    message = "  Which of my positions is most exposed to USD?  "

    response = agent.handle(message, broker_id=1, now=AS_OF)

    assert response.request == message
    assert isinstance(response, AgentResponse)


def test_broker_id_is_propagated_to_the_context_service() -> None:
    agent, double, _ = make_agent(make_context(broker_id=77))

    response = agent.handle("hello", broker_id=77, now=AS_OF)

    assert response.broker_id == 77
    assert double.calls[0][0] == 77


def test_reference_time_is_forwarded_to_the_context_service() -> None:
    agent, double, _ = make_agent()

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert double.calls[0][2] == AS_OF


# --- configurable trade-history window ---------------------------------------------------


def test_default_window_is_thirty_days() -> None:
    agent, double, _ = make_agent()

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert DEFAULT_TRADE_HISTORY_DAYS == 30
    assert double.calls[0][1] == 30


def test_custom_window_is_passed_through() -> None:
    agent, double, _ = make_agent()

    agent.handle("hello", broker_id=1, trade_history_days=7, now=AS_OF)

    assert double.calls[0][1] == 7


# --- failure propagation -----------------------------------------------------------------


def test_context_service_runtime_error_propagates() -> None:
    agent, _, _ = make_agent(context_error=RuntimeError("terminal unavailable"))

    with pytest.raises(RuntimeError, match="terminal unavailable"):
        agent.handle("hello", broker_id=1, now=AS_OF)


def test_invalid_window_error_from_the_context_service_propagates() -> None:
    service, account_provider, _, _ = make_real_service()
    agent = AgentService(service, FakeLLMProvider())

    with pytest.raises(ValueError):
        agent.handle("hello", broker_id=1, trade_history_days=0, now=AS_OF)

    # Rejected by the context service before any provider read.
    assert account_provider.call_count == 0


# --- integration with the real financial-context service ---------------------------------


def test_agent_works_with_the_real_financial_context_service() -> None:
    service, account_provider, position_provider, trade_provider = make_real_service()
    agent = AgentService(service, FakeLLMProvider())

    response = agent.handle("Show me my portfolio.", broker_id=3, trade_history_days=10, now=AS_OF)

    assert response.request == "Show me my portfolio."
    assert response.broker_id == 3
    assert response.context.broker_id == 3
    assert response.context.as_of == AS_OF
    assert response.context.account == ACCOUNT
    assert response.context.positions == POSITIONS
    assert response.context.trade_history == TRADES
    assert response.context.portfolio_intelligence.directional_balance == pytest.approx(-0.90)
    # One consistent snapshot, and the custom window reached the provider.
    assert account_provider.call_count == 1
    assert position_provider.call_count == 1
    assert trade_provider.last_from_time == AS_OF - timedelta(days=10)
    assert trade_provider.last_to_time == AS_OF


def test_agent_applies_the_default_window_with_real_services() -> None:
    service, _, _, trade_provider = make_real_service()
    agent = AgentService(service, FakeLLMProvider())

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert trade_provider.last_from_time == AS_OF - timedelta(days=30)


# --- LLM provider orchestration ----------------------------------------------------------


def test_agent_uses_the_injected_llm_provider() -> None:
    provider = FakeLLMProvider(response="stubbed answer")
    agent, _, _ = make_agent(llm=provider)

    response = agent.handle("hello", broker_id=1, now=AS_OF)

    assert provider.call_count == 1
    assert response.answer == "stubbed answer"


def test_provider_is_asked_exactly_once_per_request() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    assert provider.call_count == 1
    assert len(provider.prompts) == 1


def test_provider_receives_a_provider_neutral_prompt() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    prompt = provider.prompts[0]
    assert isinstance(prompt, LLMPrompt)
    assert set(LLMPrompt._fields) == {"instructions", "content"}
    assert prompt.instructions
    assert prompt.content


def test_prompt_content_carries_the_request_and_context_facts() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(llm=provider)
    message = "How exposed am I?"

    agent.handle(message, broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    assert message in content
    assert AS_OF.isoformat() in content
    assert "USD" in content
    assert "10000.00" in content  # balance
    assert "XAUUSD" in content
    assert "4020.00" in content  # margin level
    assert "Directional balance" in content
    assert "Risk classification: LOW" in content


def test_prompt_omits_account_identity() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(llm=provider)

    agent.handle("hello", broker_id=1, now=AS_OF)

    content = provider.prompts[0].content
    # Only the numbers are sent: no login, holder name or server identifier.
    assert str(ACCOUNT.login) not in content
    assert ACCOUNT.name not in content
    assert ACCOUNT.server not in content


def test_prompt_is_deterministic() -> None:
    first_provider = FakeLLMProvider()
    second_provider = FakeLLMProvider()

    make_agent(llm=first_provider)[0].handle("hello", broker_id=1, now=AS_OF)
    make_agent(llm=second_provider)[0].handle("hello", broker_id=1, now=AS_OF)

    assert first_provider.prompts == second_provider.prompts


def test_built_prompt_is_identical_for_the_same_input() -> None:
    context = make_context()

    assert build_prompt("hello", context) == build_prompt("hello", context)


def test_instructions_forbid_prediction_and_trading_advice() -> None:
    instructions = build_prompt("hello", make_context()).instructions.lower()

    assert "do not predict" in instructions
    assert "advice" in instructions
    assert "never suggest opening, closing or modifying a trade" in instructions


def test_empty_context_renders_explicit_none_placeholders() -> None:
    account = ACCOUNT._replace(balance=0.0, equity=0.0, margin=0.0, free_margin=0.0, margin_level=0.0)
    context = FinancialContext(
        broker_id=1,
        as_of=AS_OF,
        account=account,
        positions=(),
        trade_history=(),
        portfolio_intelligence=build_portfolio_intelligence(account, (), AS_OF),
    )

    prompt = build_prompt("anything?", context)

    assert "- none" in prompt.content
    assert "Open positions: 0" in prompt.content


def test_llm_provider_failure_propagates() -> None:
    agent, double, _ = make_agent(llm=_FailingLLMProvider())

    with pytest.raises(RuntimeError, match="model unavailable"):
        agent.handle("hello", broker_id=1, now=AS_OF)

    # The context was read before the provider was asked, and the failure was
    # not swallowed into a partial response.
    assert len(double.calls) == 1


def test_context_failure_happens_before_the_llm_is_called() -> None:
    provider = FakeLLMProvider()
    agent, _, _ = make_agent(context_error=RuntimeError("terminal unavailable"), llm=provider)

    with pytest.raises(RuntimeError):
        agent.handle("hello", broker_id=1, now=AS_OF)

    assert provider.call_count == 0


# --- read-only boundary ------------------------------------------------------------------


def test_boundary_exposes_only_a_read_capability() -> None:
    # No capability other than resolving a request exists on this boundary —
    # there is no trading/order tool to call.
    public = {name for name in dir(AgentService) if not name.startswith("_")}
    assert public == {"handle"}


def test_agent_response_is_immutable() -> None:
    agent, _, _ = make_agent()
    response = agent.handle("hello", broker_id=1, now=AS_OF)

    # Read-only by construction: the envelope cannot be mutated in place.
    with pytest.raises(AttributeError):
        setattr(response, "request", "rewritten")
