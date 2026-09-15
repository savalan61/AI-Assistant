import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.core.dependencies import (
    get_market_data_provider,
    shutdown_account_info,
    shutdown_market_data,
    shutdown_positions,
    shutdown_trade_history,
)
from app.api.account_info_router import router as account_info_router
from app.api.agent_router import router as agent_router
from app.api.auth_router import router as auth_router
from app.api.broker_llm_config_router import router as broker_llm_config_router
from app.api.economic_intelligence_router import router as economic_intelligence_router
from app.api.market_data_router import router as market_data_router
from app.api.portfolio_intelligence_router import router as portfolio_intelligence_router
from app.api.positions_router import router as positions_router
from app.api.trade_history_router import router as trade_history_router
from app.api.users_router import router as users_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the process-wide MT5 provider once at startup. A missing terminal
    # must not prevent FastAPI from starting: later requests retry lazily.
    try:
        get_market_data_provider()
    except RuntimeError as exc:
        logger.warning("MT5 provider warm-up failed (requests will retry): %s", exc)
    yield
    # Release the terminal connection exactly once at shutdown: every provider
    # cache attaches to the same MT5 terminal session.
    shutdown_market_data()
    shutdown_account_info()
    shutdown_positions()
    shutdown_trade_history()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(market_data_router)
app.include_router(users_router)
app.include_router(account_info_router)
app.include_router(positions_router)
app.include_router(trade_history_router)
app.include_router(economic_intelligence_router)
app.include_router(portfolio_intelligence_router)
app.include_router(agent_router)
app.include_router(broker_llm_config_router)


@app.get("/health")
def health():
    # Health endpoint used by infrastructure monitors; returns a simple ok.
    return {"status": "ok"}
