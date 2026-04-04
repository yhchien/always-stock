"""
從 FinMind 取得 TWSE 上市公司基本資料，並合併 Fugle 子產業 mapping。

API: https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo
回傳欄位: stock_id, stock_name, industry_category, type, date

產業分類邏輯：
- 若股票在 Fugle mapping CSV 中，取第一筆 row（主要子產業）作為 industry/chain/sub_industry
- 否則使用 FinMind 的 industry_category，chain/sub_industry 留空
- 同一股票 FinMind 可能有多筆，取第一筆（較細分類）
"""
import csv
import urllib.request
import urllib.parse
import json
import logging
from typing import Optional
from sqlalchemy.orm import Session
from app.models import StockMaster

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def load_fugle_mapping(csv_path: str) -> "dict[str, dict]":
    """
    讀取 Fugle 產業分類 CSV，回傳 {stock_id: {industry, chain, sub_industry}}。
    同一股票出現多筆時，取第一筆（主要子產業）。

    CSV 格式: stock_id, stock_name, industry, chain, sub_industry
    """
    mapping = {}  # type: dict[str, dict]
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row["stock_id"].strip()
            if sid not in mapping:
                mapping[sid] = {
                    "industry": row["industry"].strip(),
                    "chain": row["chain"].strip(),
                    "sub_industry": row["sub_industry"].strip(),
                }
    return mapping


def fetch_and_upsert_stock_master(
    db: Session,
    token: str = "",
    fugle_mapping_path: Optional[str] = None,
) -> int:
    """
    從 FinMind 抓取 TWSE 上市股票清單，合併 Fugle 子產業 mapping 後寫入 DB。

    Args:
        db: SQLAlchemy session
        token: FinMind API token（免費額度無需填）
        fugle_mapping_path: Fugle 產業分類 CSV 路徑，None 則不套用

    Returns:
        寫入（新增或更新）的股票數量
    """
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

    # 每支股票只保留第一筆（最細的 FinMind 產業分類），且只取 TWSE 上市
    seen = {}  # type: dict[str, dict]
    for row in rows:
        if row.get("type") != "twse":
            continue
        sid = row["stock_id"].strip()
        if sid not in seen:
            seen[sid] = row

    fugle_map = load_fugle_mapping(fugle_mapping_path) if fugle_mapping_path else {}

    count = 0
    for sid, row in seen.items():
        stock_name = row["stock_name"].strip()

        if sid in fugle_map:
            industry_name = fugle_map[sid]["industry"]
            chain = fugle_map[sid]["chain"] or None
            sub_industry = fugle_map[sid]["sub_industry"] or None
        else:
            industry_name = row["industry_category"].strip() or "其他"
            chain = None
            sub_industry = None

        existing = db.get(StockMaster, sid)
        if existing:
            existing.stock_name = stock_name
            existing.industry_name = industry_name
            existing.chain = chain
            existing.sub_industry = sub_industry
            existing.is_active = True
        else:
            db.add(StockMaster(
                stock_id=sid,
                stock_name=stock_name,
                industry_name=industry_name,
                chain=chain,
                sub_industry=sub_industry,
                is_active=True,
            ))
        count += 1

    db.commit()
    logger.info("Stock master upserted: %d stocks", count)
    return count
