import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.core.dependencies import get_market_data_provider, shutdown_market_data
from app.api.auth_router import router as auth_router
from app.api.market_data_router import router as market_data_router
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
    # Release the terminal connection exactly once at shutdown.
    shutdown_market_data()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(market_data_router)
app.include_router(users_router)


@app.get("/health")
def health():
    # Health endpoint used by infrastructure monitors; returns a simple ok.
    return {"status": "ok"}
