"""
Real-time stock quotes from TWSE mis.twse.com.tw intraday API.

API: https://mis.twse.com.tw/stock/api/getStockInfo.jsp
Params: ex_ch=tse_{stock_id}.tw|tse_{stock_id2}.tw|...

Response key fields per stock (msgArray[]):
    c  = stock_id
    n  = stock_name
    z  = latest trade price (current / last trade)
    y  = yesterday close
    h  = today high
    l  = today low
    o  = today open
    v  = volume (in lots / 張)
    t  = trade time (HH:MM:SS)
    d  = trade date (YYYYMMDD)
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

TWSE_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

# Maximum number of stocks per request (TWSE limit)
MAX_BATCH_SIZE = 50


class RealtimeQuote(BaseModel):
    stock_id: str
    stock_name: str
    price: Optional[float]          # latest trade price (None if no trade yet)
    prev_close: float               # yesterday close
    change: Optional[float]         # price - prev_close
    change_pct: Optional[float]     # (change / prev_close) * 100
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    volume: Optional[float]         # volume in lots (張)
    trade_time: Optional[str]       # HH:MM:SS


def _parse_price(val: str) -> Optional[float]:
    """Parse TWSE real-time price string. Returns None for '-' or empty."""
    if not val or val == "-":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _fetch_quotes(stock_ids: List[str]) -> List[dict]:
    """Fetch real-time quotes from TWSE for a list of stock IDs."""
    ex_ch = "|".join(f"tse_{sid}.tw" for sid in stock_ids)
    params = {"ex_ch": ex_ch}
    url = TWSE_REALTIME_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    return data.get("msgArray", [])


@router.get("/realtime/quotes", response_model=List[RealtimeQuote])
def get_realtime_quotes(
    stock_ids: str = Query(
        ...,
        description="Comma-separated stock IDs, e.g. '2330,2317,2454'",
    ),
):
    """
    Fetch real-time intraday quotes for up to 50 stocks.
    Returns latest price, change, volume, and trade time.
    """
    ids = [s.strip() for s in stock_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No stock IDs provided")
    if len(ids) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Too many stock IDs ({len(ids)}), max {MAX_BATCH_SIZE}",
        )

    logger.info("GET /realtime/quotes stock_ids=%s", ",".join(ids[:5]))

    try:
        raw = _fetch_quotes(ids)
    except Exception:
        logger.exception("Failed to fetch real-time quotes")
        raise HTTPException(status_code=502, detail="Failed to fetch real-time data from TWSE")

    results = []
    for item in raw:
        sid = item.get("c", "")
        prev_close = _parse_price(item.get("y", ""))
        price = _parse_price(item.get("z", ""))

        if prev_close is None:
            continue  # skip if no baseline

        change = None
        change_pct = None
        if price is not None and prev_close != 0:
            change = price - prev_close
            change_pct = (change / prev_close) * 100.0

        results.append(RealtimeQuote(
            stock_id=sid,
            stock_name=item.get("n", ""),
            price=price,
            prev_close=prev_close,
            change=change,
            change_pct=change_pct,
            open=_parse_price(item.get("o", "")),
            high=_parse_price(item.get("h", "")),
            low=_parse_price(item.get("l", "")),
            volume=_parse_price(item.get("v", "")),
            trade_time=item.get("t"),
        ))

    logger.debug("Returning %d real-time quotes", len(results))
    return results
