"""
Fetch TWSE listed stock master data from FinMind and merge Fugle sub-industry mapping.

API: https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo
Response fields: stock_id, stock_name, industry_category, type, date

Industry classification logic:
- If a stock exists in the Fugle mapping CSV, use the first row (primary sub-industry)
  to populate industry, chain, and sub_industry.
- Otherwise fall back to FinMind's industry_category; chain and sub_industry are left null.
- FinMind may return multiple rows per stock; only the first row is used (finer classification).
"""
import csv
import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

from sqlalchemy.orm import Session

from app.models import StockMaster

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def load_fugle_mapping(csv_path: str) -> "dict[str, dict]":
    """
    Load Fugle industry mapping CSV and return {stock_id: {industry, chain, sub_industry}}.
    When a stock appears multiple times, only the first row is kept (primary sub-industry).

    CSV columns: stock_id, stock_name, industry, chain, sub_industry
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
    logger.debug("Fugle mapping loaded: %d stocks from %s", len(mapping), csv_path)
    return mapping


def fetch_and_upsert_stock_master(
    db: Session,
    token: str = "",
    fugle_mapping_path: Optional[str] = None,
) -> int:
    """
    Fetch TWSE listed stocks from FinMind, merge Fugle sub-industry mapping, and upsert into DB.

    Args:
        db: SQLAlchemy session
        token: FinMind API token (not required for free tier)
        fugle_mapping_path: path to Fugle industry mapping CSV; skipped if None

    Returns:
        number of stocks inserted or updated
    """
    params = {"dataset": "TaiwanStockInfo"}
    if token:
        params["token"] = token

    url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    logger.debug("Fetching stock master from FinMind: %s", url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("status") != 200:
        raise RuntimeError(f"FinMind API error: {data.get('msg')}")

    rows = data.get("data", [])
    logger.debug("FinMind returned %d rows", len(rows))

    # Keep only the first row per stock, TWSE-listed only
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
