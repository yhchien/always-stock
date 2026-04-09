import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import brokers, industries, realtime, stocks

logger = logging.getLogger(__name__)

app = FastAPI(
    title="always-stock API",
    description="Taiwan stock institutional flow API by industry",
    version="0.1.0",
)

import os

_allowed_origins = [
    "http://localhost:3000",
    "https://always-stock-web.fly.dev",
]
# Allow custom origins via env var (comma-separated)
_extra = os.getenv("CORS_ORIGINS", "")
if _extra:
    _allowed_origins.extend(o.strip() for o in _extra.split(",") if o.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(industries.router, prefix="/api")
app.include_router(stocks.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(brokers.router, prefix="/api")

logger.info("always-stock API initialized")


@app.get("/health")
def health():
    logger.debug("Health check called")
    return {"status": "ok"}
