import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import industries, realtime, stocks

logger = logging.getLogger(__name__)

app = FastAPI(
    title="tw-stock-dashboard API",
    description="Taiwan stock institutional flow API by industry",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(industries.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")

logger.info("tw-stock-dashboard API initialized")


@app.get("/health")
def health():
    logger.debug("Health check called")
    return {"status": "ok"}
