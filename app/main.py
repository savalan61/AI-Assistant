from fastapi import FastAPI

from app.core.config import settings
from app.api.market_data_router import router as market_data_router

app = FastAPI(title=settings.APP_NAME)
app.include_router(market_data_router)


@app.get("/health")
def health():
    return {"status": "ok"}
