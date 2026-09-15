"""Agent API: one authenticated, read-only assistant endpoint.

Exposes the existing AgentService capability over HTTP: the user's message is
answered from their current read-only financial context through the injected
LLM provider. Strictly read-only: no trading tool, no order action, and no
mutation exists behind this endpoint, and the agent layer itself cannot trade.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.blocking import run_mt5_call
from app.core.config import settings
from app.core.dependencies import get_agent_service, get_agent_usage_limiter, get_current_user
from app.db.models import User
from app.services.agent import (
    AgentService,
    AgentUsageLimiter,
    PromptTooLargeError,
    UsageLimitExceededError,
    check_scope,
)
from app.services.agent.scope import ScopeDecision
from app.services.financial_context import DEFAULT_TRADE_HISTORY_DAYS

router = APIRouter()


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The user's question/request, answered against their own financial context.
    message: str = Field(min_length=1)
    # Optional trade-history look-back in days; defaults to the established
    # 30-day window. Values below 1 are rejected with 422 before any work.
    trade_history_days: int = Field(default=DEFAULT_TRADE_HISTORY_DAYS, ge=1)

    # Upper bound on the message: it is rendered into an outbound LLM prompt, so
    # an unbounded body would be an unbounded (and billable) payload. Read from
    # settings at validation time so the limit stays configurable per deployment.
    @field_validator("message")
    @classmethod
    def _validate_message_length(cls, value: str) -> str:
        limit = settings.AGENT_MAX_MESSAGE_LENGTH
        if len(value) > limit:
            raise ValueError(f"message must be at most {limit} characters")
        return value

    # Deliberately no broker_id/user_id field: extra="forbid" turns any such
    # supplied field into 422, so a caller can never widen or redirect the
    # tenant scope. Tenant identity comes only from the authenticated user.


# Numbers only: login, holder name and server are deliberately omitted — the
# same identity fields the LLM prompt excludes — so no account identity is
# exposed through the API.
class AccountSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float


# Maps the Position contract to a JSON-safe schema (the same projection as
# GET /positions); the raw MT5 object never reaches this layer.
class PositionSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    symbol: str
    type: str
    volume: float
    open_price: float
    current_price: float
    profit: float


# Maps the TradeHistoryEntry contract (the same projection as
# GET /trade-history); close_reason/stop_loss/take_profit stay nullable when
# the data does not exist — never invented.
class TradeSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticket: int
    order_ticket: int
    symbol: str
    type: str
    volume: float
    price: float
    profit: float
    time: datetime
    close_reason: str | None
    stop_loss: float | None
    take_profit: float | None


class SymbolExposureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    buy_volume: float
    sell_volume: float
    net_volume: float
    position_count: int


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Margin-based risk band (FLAT / LOW / ELEVATED / HIGH / UNKNOWN).
    level: str
    # Deterministic factual reason for the band. Never a forecast.
    basis: str


# The exposure/risk part of the portfolio analysis; the account fields it also
# carries are already exposed once, under "account".
class PortfolioSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    open_positions: int
    buy_positions: int
    sell_positions: int
    symbols: list[str]
    total_volume: float
    buy_volume: float
    sell_volume: float
    directional_balance: float
    exposure: list[SymbolExposureResponse]
    risk: RiskAssessmentResponse


class FinancialContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of: datetime
    account: AccountSnapshotResponse
    positions: list[PositionSnapshotResponse]
    trade_history: list[TradeSnapshotResponse]
    portfolio_intelligence: PortfolioSnapshotResponse


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # The caller's message, echoed verbatim.
    request: str
    # Tenant identity of the authenticated user, read from the database record —
    # never from the request or a token claim.
    broker_id: int
    # The text the injected LLM provider returned, unchanged. Production wiring
    # selects the OpenAI-compatible adapter from settings and is unavailable
    # (503) until the key/model are configured; the fake remains the explicit
    # test/development provider.
    answer: str
    # The read-only financial context the answer was resolved against.
    context: FinancialContextResponse


@router.post("/agent", response_model=AgentResponse)
async def handle_agent_message(
    # Body is validated before any work: message required, trade_history_days
    # >= 1 (422 otherwise), and no extra fields are accepted.
    payload: AgentRequest,
    # Authentication boundary: any active user (customer, admin or super_admin)
    # may ask the assistant about the MT5 account this process is attached to.
    # Tenant identity stays with the database-backed User.
    current_user: User = Depends(get_current_user),
    service: AgentService = Depends(get_agent_service),
    limiter: AgentUsageLimiter = Depends(get_agent_usage_limiter),
) -> AgentResponse:
    # Guard chain, cheapest checks first, in this exact order:
    # 1. Scope: a deterministic financial-scope classification — no LLM, no I/O.
    # 2. Usage limit: per-user daily quota, keyed by the authenticated user.
    # 3. Context + LLM: the blocking financial read, off the event loop.
    #    Rejections at steps 1–2 never read the context or call the LLM.
    decision = check_scope(payload.message)
    if decision is ScopeDecision.REJECT:
        raise HTTPException(status_code=422, detail="Request is outside the assistant's financial scope")

    try:
        limiter.check_and_consume(current_user.broker_id, current_user.id, datetime.now(UTC))
    except UsageLimitExceededError:
        # Generic message: no counters, identities or internals are exposed.
        raise HTTPException(status_code=429, detail="Daily agent request limit reached")

    # The agent reads the financial context (blocking MT5), so the whole call is
    # offloaded through the consolidated MT5 blocking boundary. RuntimeError
    # means an MT5/LLM infrastructure failure (server error 503); the generic
    # detail never leaks provider internals. Unexpected exceptions propagate.
    try:
        result = await run_mt5_call(
            service.handle,
            payload.message,
            current_user.broker_id,
            payload.trade_history_days,
        )
    except PromptTooLargeError:
        # The request was within its limits, but the financial context could not
        # be rendered into a bounded prompt even after the deterministic
        # reductions. Reported as an unprocessable request with a generic detail.
        raise HTTPException(
            status_code=422,
            detail="The financial context is too large to process this request",
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Agent service temporarily unavailable")

    return AgentResponse.model_validate(result)
