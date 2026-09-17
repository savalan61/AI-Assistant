"""The /agent model round trip must not occupy an MT5 blocking worker.

POST /agent used to run the whole agent flow — context reads AND the outbound
model call — inside the consolidated MT5 blocking boundary, so one worker thread
stayed busy for the entire LLM round trip even though the model call touches no
MT5 at all. The agent is now two phases: `prepare()` (every blocking read: MT5,
calendar, news) on the MT5 boundary, and `respond()` (prompt rendering plus the
single model call) on the outbound-LLM boundary, which is a different pool.

These tests drive the REAL router coroutine — the function FastAPI calls, with
the real `run_mt5_call` / `run_llm_call` boundaries and a real AgentService over
fakes — so they observe the boundary the application actually uses.

The measurement is anyio's per-event-loop worker-thread limiter: it is exactly
what starlette's `run_in_threadpool` (and therefore `run_mt5_call`) borrows from,
so a phase that holds a token holds an MT5 worker, and a phase that holds none
cannot be starving MT5 reads. Two tests are a matched pair — one shows the probe
IS sensitive (a phase inside the MT5 boundary borrows the token), the other shows
the model round trip borrows none.

Everything here is offline: fake contexts, a gated fake provider, no MT5, no
terminal, no database, no network, no API key. No pytest asyncio plugin: the async
scenarios are driven with asyncio.run.
"""
import asyncio
import inspect
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from anyio.to_thread import current_default_thread_limiter
from fastapi import HTTPException

from app.api import agent_router
from app.api.agent_router import AgentRequest, handle_agent_message
from app.core.blocking import run_llm_call, run_mt5_call
from app.db.models import User, UserRole
from app.providers.account_info import AccountInfo
from app.providers.llm import LLMPrompt, LLMProvider
from app.providers.position import Position, PositionType
from app.providers.trade_history import TradeHistoryEntry, TradeType
from app.services.agent import AgentService, AgentUsageLimiter, PromptTooLargeError
from app.services.financial_context import FinancialContext, FinancialContextService
from app.services.portfolio_intelligence import build_portfolio_intelligence

AS_OF = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
MESSAGE = "What is my exposure?"
ANSWER = "Your exposure is 0.10 lots of XAUUSD."

ACCOUNT = AccountInfo(
    login=10007,
    name="Test Trader",
    balance=Decimal("10000.00"),
    equity=Decimal("10050.00"),
    margin=Decimal("250.00"),
    free_margin=Decimal("9800.00"),
    margin_level=4020.0,
    currency="USD",
    server="Test-Server",
)

POSITIONS: tuple[Position, ...] = (
    Position(
        ticket=123456789,
        symbol="XAUUSD",
        type=PositionType.BUY,
        volume=Decimal("0.10"),
        open_price=Decimal("3642.50"),
        current_price=Decimal("3648.20"),
        profit=Decimal("57.00"),
    ),
)

TRADES: tuple[TradeHistoryEntry, ...] = (
    TradeHistoryEntry(
        ticket=246802468,
        order_ticket=987654321,
        symbol="XAUUSD",
        type=TradeType.BUY,
        volume=Decimal("0.10"),
        price=Decimal("3648.20"),
        profit=Decimal("57.00"),
        time=datetime(2026, 9, 16, 12, 30, 0, tzinfo=UTC),
        close_reason=None,
        stop_loss=Decimal("3635.00"),
        take_profit=Decimal("3650.00"),
    ),
)


def make_context(broker_id: int = 1) -> FinancialContext:
    """The canned read-only context every fake resolves (no MT5 involved)."""
    return FinancialContext(
        broker_id=broker_id,
        as_of=AS_OF,
        account=ACCOUNT,
        positions=POSITIONS,
        trade_history=TRADES,
        portfolio_intelligence=build_portfolio_intelligence(ACCOUNT, POSITIONS, AS_OF),
    )


def make_user(broker_id: int = 3, user_id: int = 7) -> User:
    """The authenticated user the router reads tenant identity from."""
    return User(
        id=user_id,
        broker_id=broker_id,
        login="10007",
        password_hash="x-not-a-real-hash",
        is_active=True,
        role=UserRole.CUSTOMER,
    )


class _GatedContextService(FinancialContextService):
    """Records the MT5 phase and can hold it inside the blocking boundary.

    ``build`` models the real blocking read: when ``hold`` is set it stays inside
    the MT5 worker until the test releases it, which is what makes the borrowed
    worker observable.
    """

    def __init__(self, context: FinancialContext, *, error: Exception | None = None) -> None:
        self.context = context
        self.error = error
        self.calls = 0
        self.entered = threading.Event()
        self.hold = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.thread_ident: int | None = None

    def build(
        self,
        broker_id: int,
        trade_history_days: int = 30,
        now: datetime | None = None,
    ) -> FinancialContext:
        self.calls += 1
        self.thread_ident = threading.get_ident()
        self.entered.set()
        if self.hold.is_set():  # pragma: no cover - only the gated scenarios set it
            self.release.wait(timeout=30)
        self.finished.set()
        if self.error is not None:
            raise self.error
        return self.context


class _GatedLLMProvider(LLMProvider):
    """Records the model phase and holds it until the test releases it."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.thread_ident: int | None = None
        self.prompts: list[LLMPrompt] = []

    def complete(self, prompt: LLMPrompt) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        self.thread_ident = threading.get_ident()
        self.entered.set()
        if not self.release.wait(timeout=30):  # pragma: no cover - a hung provider
            raise RuntimeError("the test never released the model call")
        self.finished.set()
        if self.error is not None:
            raise self.error
        return ANSWER


async def _await_event(event: threading.Event, *, timeout: float = 20.0) -> None:
    """Wait (bounded) for a gated phase to start, without hanging the suite."""
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("the gated phase never started")
        await asyncio.sleep(0.005)


def _submit(service: AgentService):
    """Start the real router coroutine on the running loop (one /agent request)."""
    return asyncio.ensure_future(
        handle_agent_message(
            AgentRequest(message=MESSAGE),
            make_user(),
            service,
            AgentUsageLimiter(daily_limit=5),
        )
    )


# --- the boundary split ------------------------------------------------------


def test_the_mt5_phase_borrows_an_mt5_worker_and_the_model_round_trip_does_not() -> None:
    """The regression this step exists for, measured on the real router.

    While the context read is inside the MT5 boundary the limiter shows a
    borrowed worker; while the SAME request is waiting on the model it shows
    none, even though the model call is still in flight.
    """

    async def scenario() -> tuple[int, int]:
        limiter = current_default_thread_limiter()
        context = make_context()
        context_service = _GatedContextService(context)
        context_service.hold.set()
        provider = _GatedLLMProvider()
        service = AgentService(financial_context_service=context_service, llm_provider=provider)

        task = _submit(service)
        await _await_event(context_service.entered)
        while_mt5 = limiter.borrowed_tokens
        context_service.release.set()

        await _await_event(provider.entered)
        while_model = limiter.borrowed_tokens
        # The model phase started only after the blocking read finished.
        assert context_service.finished.is_set()
        assert provider.finished.is_set() is False

        provider.release.set()
        response = await task
        assert response.answer == ANSWER
        # The two phases ran on different threads: the MT5 worker was released,
        # and the model ran on the outbound boundary's own pool.
        assert context_service.thread_ident is not None
        assert provider.thread_ident is not None
        assert provider.thread_ident != context_service.thread_ident
        return while_mt5, while_model

    while_mt5, while_model = asyncio.run(scenario())

    # The MT5 read genuinely holds one worker (so the probe is sensitive, and the
    # blocking read was NOT moved off the MT5 boundary) ...
    assert while_mt5 == 1
    # ... and the model round trip holds none of them.
    assert while_model == 0


def test_an_mt5_read_can_run_while_the_model_is_thinking() -> None:
    """The operational consequence, at the hardest setting: ONE MT5 worker.

    The worker pool is squeezed to a single token, the model call is held open,
    and an MT5-backed call still completes — proving the worker was released
    rather than parked on the model for the whole round trip.
    """

    async def scenario() -> str:
        limiter = current_default_thread_limiter()
        limiter.total_tokens = 1  # the whole pool: one worker
        provider = _GatedLLMProvider()
        service = AgentService(
            financial_context_service=_GatedContextService(make_context()),
            llm_provider=provider,
        )

        task = _submit(service)
        await _await_event(provider.entered)

        # The only MT5 worker is free while the request waits on the model.
        read = await asyncio.wait_for(run_mt5_call(lambda: "mt5 read completed"), timeout=10)

        provider.release.set()
        response = await task
        assert response.answer == ANSWER
        return read

    assert asyncio.run(scenario()) == "mt5 read completed"


def test_the_old_one_phase_call_would_have_held_the_mt5_worker() -> None:
    """Control: the OLD /agent shape really does occupy the worker for the call.

    This is the exact call the router used to make — the whole agent flow inside
    the MT5 blocking boundary — and it leaves the single worker unavailable, so
    the same probe cannot complete. This is what makes the test above meaningful
    rather than vacuously true: the measurement discriminates the two shapes.
    """

    async def scenario() -> None:
        limiter = current_default_thread_limiter()
        limiter.total_tokens = 1  # the whole pool: one worker
        provider = _GatedLLMProvider()
        service = AgentService(
            financial_context_service=_GatedContextService(make_context()),
            llm_provider=provider,
        )

        # The pre-change router call: context AND model inside the MT5 boundary.
        task = asyncio.ensure_future(run_mt5_call(service.handle, MESSAGE, 1, 30))
        await _await_event(provider.entered)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(run_mt5_call(lambda: "never runs"), timeout=0.5)

        provider.release.set()
        await task

    asyncio.run(scenario())


# --- behavior preserved by the split ----------------------------------------


def test_the_router_returns_exactly_what_the_one_phase_call_returns() -> None:
    """The split changes threading, not the answer or the response envelope."""
    context = make_context()
    provider = _GatedLLMProvider()
    provider.release.set()  # never gated in this test
    service = AgentService(
        financial_context_service=_GatedContextService(context),
        llm_provider=provider,
    )

    through_the_router = asyncio.run(
        handle_agent_message(
            AgentRequest(message=MESSAGE),
            make_user(broker_id=3),
            service,
            AgentUsageLimiter(daily_limit=5),
        )
    )
    one_phase = service.handle(MESSAGE, broker_id=3, now=AS_OF)

    assert through_the_router.answer == one_phase.answer == ANSWER
    assert through_the_router.request == one_phase.request == MESSAGE
    assert through_the_router.broker_id == one_phase.broker_id == 3
    # The context reaching the caller is the same read-only context either way
    # (the router validates it into its own response schema, so compare the
    # resolved values rather than the object).
    assert one_phase.context is context
    assert through_the_router.context.as_of == context.as_of
    assert through_the_router.context.account.balance == ACCOUNT.balance
    assert through_the_router.context.portfolio_intelligence.open_positions == 1
    # One model call per request either way — the split never duplicates it.
    assert provider.calls == 2 and len(provider.prompts) == 2


def test_a_failed_mt5_read_still_maps_to_503_and_never_reaches_the_model() -> None:
    """The MT5 failure path is unchanged — and it stops at the first phase."""
    provider = _GatedLLMProvider()
    provider.release.set()
    service = AgentService(
        financial_context_service=_GatedContextService(
            make_context(), error=RuntimeError("MT5 session unavailable")
        ),
        llm_provider=provider,
    )

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            handle_agent_message(
                AgentRequest(message=MESSAGE),
                make_user(),
                service,
                AgentUsageLimiter(daily_limit=5),
            )
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Agent service temporarily unavailable"
    # A failed read never reaches the provider, exactly as before the split.
    assert provider.calls == 0


def test_a_failed_model_call_still_maps_to_503_after_a_successful_read() -> None:
    """The LLM failure path is unchanged: the read happens, then the model fails."""
    context_service = _GatedContextService(make_context())
    provider = _GatedLLMProvider(error=RuntimeError("model unavailable"))
    provider.release.set()
    service = AgentService(financial_context_service=context_service, llm_provider=provider)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            handle_agent_message(
                AgentRequest(message=MESSAGE),
                make_user(),
                service,
                AgentUsageLimiter(daily_limit=5),
            )
        )

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Agent service temporarily unavailable"
    assert context_service.calls == 1 and provider.calls == 1


def test_an_unbounded_prompt_still_maps_to_422_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering still happens before the provider, so the 422 path is intact.

    The prompt is rendered in the second phase (it needs no MT5), so this also
    pins that moving it there did not move the size check after the model call.
    """
    provider = _GatedLLMProvider()
    provider.release.set()
    service = AgentService(
        financial_context_service=_GatedContextService(make_context()),
        llm_provider=provider,
    )

    def unbounded(*args: object, **kwargs: object) -> object:
        raise PromptTooLargeError("context too large")

    monkeypatch.setattr("app.services.agent.agent_service.build_prompt", unbounded)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            handle_agent_message(
                AgentRequest(message=MESSAGE),
                make_user(),
                service,
                AgentUsageLimiter(daily_limit=5),
            )
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail == "The financial context is too large to process this request"
    assert provider.calls == 0


def test_scope_and_quota_guards_still_run_before_any_read_or_model_call() -> None:
    """The two cheap guards still precede both phases (and both boundaries)."""
    context_service = _GatedContextService(make_context())
    provider = _GatedLLMProvider()
    provider.release.set()
    service = AgentService(financial_context_service=context_service, llm_provider=provider)

    with pytest.raises(HTTPException) as out_of_scope:
        asyncio.run(
            handle_agent_message(
                AgentRequest(message="Write me a poem about my cat"),
                make_user(),
                service,
                AgentUsageLimiter(daily_limit=5),
            )
        )
    assert out_of_scope.value.status_code == 422
    # The out-of-scope request never read the context or asked the model.
    assert context_service.calls == 0 and provider.calls == 0

    # One limiter across both requests: the first is admitted (and does its
    # work), the second is refused by the quota. Neither phase ran for it.
    quota_limiter = AgentUsageLimiter(daily_limit=1)

    async def two_requests() -> None:
        await handle_agent_message(AgentRequest(message=MESSAGE), make_user(), service, quota_limiter)
        await handle_agent_message(AgentRequest(message=MESSAGE), make_user(), service, quota_limiter)

    with pytest.raises(HTTPException) as quota:
        asyncio.run(two_requests())
    assert quota.value.status_code == 429
    # Exactly the admitted request did work: the rejected one added nothing.
    assert context_service.calls == 1 and provider.calls == 1


# --- the boundaries themselves ----------------------------------------------


def test_the_llm_call_runs_outside_the_mt5_worker_threadpool() -> None:
    """The two boundary functions really do use two different pools."""

    async def scenario() -> tuple[str, str]:
        seen: list[str] = []

        def record() -> str:
            seen.append(threading.current_thread().name)
            return seen[-1]

        mt5_thread = await run_mt5_call(record)
        llm_thread = await run_llm_call(record)
        return mt5_thread, llm_thread

    mt5_thread, llm_thread = asyncio.run(scenario())

    # Both are off the event loop, and the model boundary is not the MT5 pool.
    assert mt5_thread != threading.current_thread().name
    assert llm_thread != mt5_thread


def test_the_boundaries_are_the_only_thread_crossings_in_the_agent_router() -> None:
    """No router re-implements a boundary, and no trading capability appeared."""
    source = inspect.getsource(agent_router)

    assert "from app.core.blocking import run_llm_call, run_mt5_call" in source
    # starlette's threadpool (or a bare executor) must not be imported/used here:
    # the consolidated boundaries stay the single crossing point.
    assert "starlette.concurrency" not in source
    assert "run_in_threadpool" not in source
    assert "run_in_executor" not in source
    # Read-only by construction: the endpoint gained no order/mutation surface.
    for forbidden in ("order_send", "order_check", "positions_modify", "positions_close"):
        assert forbidden not in source
