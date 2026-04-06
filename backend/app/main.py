from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import industries, stocks

app = FastAPI(
    title="tw-stock-dashboard API",
    description="台股產業別三大法人資金流向 API",
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


@app.get("/health")
def health():
    return {"status": "ok"}
