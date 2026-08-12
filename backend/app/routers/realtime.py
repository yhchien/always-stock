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
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

TWSE_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

# Maximum number of stocks per request (TWSE limit)
MAX_BATCH_SIZE = 50

# 2026-08-13：per-stock 短 TTL cache（成本/穩定性修正，見下方 gotcha）。
#
# 症狀：使用者反映首頁報價「很慢很慢，而且常常出不來」。實測（本機直連 TWSE，
# 非 Render 網路環境）：單一請求循序打 5~6 次穩定在 0.6~1.7 秒；8 個並行請求
# （模擬多個瀏覽器分頁/多位使用者同時輪詢首頁）延遲直接拉到 1.2~4.2 秒——這個
# endpoint 是同步阻塞呼叫（`urllib.request.urlopen`），沒有任何快取，每一次前端
# 輪詢（首頁預設每 15 秒一次）都會對 TWSE 打一次全新的請求，多個使用者同時瀏覽
# 首頁時，同一批股票的報價會被重複打好幾次 TWSE，直接放大延遲與失敗機率。
#
# 修法：per-stock（不是 per-request-batch）短 TTL cache——不同使用者請求的股票
# 集合通常有重疊（同一批熱門/自選股），per-stock cache 能讓重疊的部分直接命中，
# 只有真正沒被任何人最近查過的股票才需要重打 TWSE。TTL 刻意設短（8 秒），在
# 「大幅降低並行重複請求量」跟「維持接近即時的新鮮度」之間取平衡——比前端輪詢
# 間隔（15~60 秒視頁面而定）短很多，不會讓使用者覺得資料卡住不動。
_QUOTE_CACHE_TTL_SECONDS = 8.0
_quote_cache_lock = threading.Lock()
_quote_cache: Dict[str, Tuple[float, dict]] = {}  # stock_id -> (expires_at, raw msgArray item)


def _get_cached_quotes(stock_ids: List[str]) -> Tuple[Dict[str, dict], List[str]]:
    """回傳 (命中的 raw item by stock_id, 需要重打 TWSE 的 stock_id 清單)。"""
    now = time.monotonic()
    cached: Dict[str, dict] = {}
    missing: List[str] = []
    with _quote_cache_lock:
        for sid in stock_ids:
            entry = _quote_cache.get(sid)
            if entry is not None and entry[0] > now:
                cached[sid] = entry[1]
            else:
                missing.append(sid)
    return cached, missing


def _store_quotes_in_cache(raw_items: List[dict]) -> None:
    expires_at = time.monotonic() + _QUOTE_CACHE_TTL_SECONDS
    with _quote_cache_lock:
        for item in raw_items:
            sid = item.get("c")
            if sid:
                _quote_cache[sid] = (expires_at, item)


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


def _fetch_quotes_from_twse(stock_ids: List[str]) -> List[dict]:
    """Fetch real-time quotes from TWSE for a list of stock IDs (no cache)."""
    ex_ch = "|".join(f"tse_{sid}.tw" for sid in stock_ids)
    params = {"ex_ch": ex_ch}
    url = TWSE_REALTIME_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    return data.get("msgArray", [])


def _fetch_quotes(stock_ids: List[str]) -> List[dict]:
    """Fetch real-time quotes for a list of stock IDs, using the short-TTL
    per-stock cache to dedupe overlapping concurrent requests before hitting
    TWSE. Only stock IDs not cached (or expired) trigger a real TWSE call.

    若 TWSE 呼叫失敗（timeout／連線錯誤），仍然回傳快取命中的部分，不要因為
    「缺的那幾檔打不到」就讓整批（含已經有快取的部分）也一起失敗——這是使用者
    回報「常常出不來」的其中一種情境：一次請求裡有幾檔剛好沒命中快取又遇到
    TWSE 短暫變慢，不該波及同批裡其他明明拿得到資料的股票。呼叫端（route
    handler）只有在「一檔都拿不到」時才視為真正的失敗。
    """
    cached, missing = _get_cached_quotes(stock_ids)
    if missing:
        try:
            fresh = _fetch_quotes_from_twse(missing)
        except Exception:
            logger.warning(
                "TWSE fetch failed for %d/%d stocks; returning %d cached quote(s) only",
                len(missing),
                len(stock_ids),
                len(cached),
            )
        else:
            _store_quotes_in_cache(fresh)
            for item in fresh:
                sid = item.get("c")
                if sid:
                    cached[sid] = item
    # 保留原本請求順序，跟舊行為一致（雖然下游是用 stock_id 對應，順序其實不影響
    # 正確性，但維持原樣讓行為改動範圍最小）。
    return [cached[sid] for sid in stock_ids if sid in cached]


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
