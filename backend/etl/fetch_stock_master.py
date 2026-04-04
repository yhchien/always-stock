"""
從 FinMind 取得 TWSE 上市公司基本資料（含產業別）。

API: https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo
回傳欄位: stock_id, stock_name, industry_category, type, date

注意：同一支股票可能有多筆（如 2330 同時列在「半導體業」和「電子工業」）。
取第一筆（較細分類）作為主要產業。
"""
import urllib.request
import urllib.parse
import json
import logging
from sqlalchemy.orm import Session
from app.models import StockMaster

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def fetch_and_upsert_stock_master(db: Session, token: str = "") -> int:
    params = {"dataset": "TaiwanStockInfo"}
    if token:
        params["token"] = token

    url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-dashboard/1.0"})

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("status") != 200:
        raise RuntimeError(f"FinMind API error: {data.get('msg')}")

    rows = data.get("data", [])

    # 每支股票只保留第一筆（最細的產業分類），且只取 TWSE 上市
    seen: dict[str, dict] = {}
    for row in rows:
        if row.get("type") != "twse":
            continue
        sid = row["stock_id"].strip()
        if sid not in seen:
            seen[sid] = row

    count = 0
    for sid, row in seen.items():
        stock_name = row["stock_name"].strip()
        industry_name = row["industry_category"].strip()
        if not industry_name:
            industry_name = "其他"

        existing = db.get(StockMaster, sid)
        if existing:
            existing.stock_name = stock_name
            existing.industry_name = industry_name
            existing.is_active = True
        else:
            db.add(StockMaster(
                stock_id=sid,
                stock_name=stock_name,
                industry_name=industry_name,
                is_active=True,
            ))
        count += 1

    db.commit()
    logger.info("Stock master upserted: %d stocks", count)
    return count
