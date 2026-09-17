from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.core.dependencies import get_mt5_ownership_guard, shutdown_mt5_session
from app.api.account_info_router import router as account_info_router
from app.api.agent_router import router as agent_router
from app.api.auth_router import router as auth_router
from app.api.broker_llm_config_router import router as broker_llm_config_router
from app.api.economic_intelligence_router import router as economic_intelligence_router
from app.api.fundamental_intelligence_router import router as fundamental_intelligence_router
from app.api.instruments_router import router as instruments_router
from app.api.market_data_router import router as market_data_router
from app.api.portfolio_intelligence_router import router as portfolio_intelligence_router
from app.api.positions_router import router as positions_router
from app.api.trade_history_router import router as trade_history_router
from app.api.users_router import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Single-owner enforcement comes FIRST, before anything can serve MT5 data.
    # MT5 state is process-global and the session lock is process-local, so a
    # second process driving the same terminal would break customer isolation. This
    # takes an exclusive OS lock for the process lifetime; if it cannot be
    # established the error propagates here and the process refuses to start
    # instead of serving reads it cannot isolate.
    ownership = get_mt5_ownership_guard()
    ownership.acquire()
    try:
        # No startup warm-up: MT5 is authenticated per customer, and at boot there
        # is no authenticated user whose account could be connected. The session
        # is established lazily by the first authenticated MT5 request (and a
        # failure there maps to 503 as before, so a missing terminal never blocks
        # boot).
        yield
    finally:
        # Close the terminal session BEFORE giving up ownership, so no other
        # process can start driving the terminal while this one still holds a
        # connection to it. Every customer's read went through that one session.
        shutdown_mt5_session()
        ownership.release()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(market_data_router)
app.include_router(users_router)
app.include_router(account_info_router)
app.include_router(positions_router)
app.include_router(trade_history_router)
app.include_router(instruments_router)
app.include_router(economic_intelligence_router)
app.include_router(fundamental_intelligence_router)
app.include_router(portfolio_intelligence_router)
app.include_router(agent_router)
app.include_router(broker_llm_config_router)


@app.get("/health")
def health():
    # Health endpoint used by infrastructure monitors; returns a simple ok.
    return {"status": "ok"}
