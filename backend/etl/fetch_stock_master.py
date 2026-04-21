"""
Fetch TWSE listed stock master data from FinMind.

Data sources (both from FinMind):
- TaiwanStockInfo: stock_id, stock_name, industry_category (fallback), type
- TaiwanStockIndustryChain: industry, sub_industry (primary classification)

Classification logic (all stocks_master records marked source='finmind'):
- Primary: TaiwanStockIndustryChain 的 industry / sub_industry
- Fallback: 不在 IndustryChain 的股票（ETF、剛上市）退回 TaiwanStockInfo.industry_category，
           sub_industry 留 None
- chain 欄位（原 Fugle 上中下游）全面停用，永遠寫 None
"""
import json
import logging
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from app.models import StockMaster
from etl.finmind_industry_chain_sdk import fetch_industry_chain

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def fetch_and_upsert_stock_master(db: Session, token: str = "") -> int:
    """
    Fetch TWSE listed stocks from FinMind, merge with TaiwanStockIndustryChain,
    and upsert into stocks_master.

    Args:
        db:    SQLAlchemy session
        token: FinMind API token（TaiwanStockIndustryChain 需要 Backer/Sponsor）

    Returns:
        number of stocks inserted or updated
    """
    params = {"dataset": "TaiwanStockInfo"}
    if token:
        params["token"] = token

    url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    logger.debug("Fetching TaiwanStockInfo from FinMind: %s", url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("status") != 200:
        raise RuntimeError(f"FinMind API error: {data.get('msg')}")

    rows = data.get("data", [])
    logger.debug("TaiwanStockInfo returned %d rows", len(rows))

    seen: "dict[str, dict]" = {}
    for row in rows:
        if row.get("type") != "twse":
            continue
        sid = row["stock_id"].strip()
        if sid not in seen:
            seen[sid] = row

    industry_chain_map: "dict[str, dict]" = {}
    if token:
        try:
            industry_chain_map = fetch_industry_chain(token)
            logger.info(
                "TaiwanStockIndustryChain coverage: %d / %d stocks",
                len(industry_chain_map), len(seen),
            )
        except Exception as exc:
            logger.warning(
                "Failed to fetch TaiwanStockIndustryChain, fallback to industry_category only: %s",
                exc,
            )
    else:
        logger.warning(
            "No FinMind token provided; skipping TaiwanStockIndustryChain (sub_industry will be NULL)"
        )

    count = 0
    for sid, row in seen.items():
        stock_name = row["stock_name"].strip()
        chain_entry = industry_chain_map.get(sid)

        if chain_entry and chain_entry.get("industry"):
            industry_name = chain_entry["industry"]
            sub_industry = chain_entry.get("sub_industry") or None
        else:
            industry_name = row.get("industry_category", "").strip() or "其他"
            sub_industry = None

        existing = db.get(StockMaster, sid)
        if existing:
            existing.stock_name = stock_name
            existing.industry_name = industry_name
            existing.chain = None
            existing.sub_industry = sub_industry
            existing.is_active = True
            existing.source = "finmind"
        else:
            db.add(StockMaster(
                stock_id=sid,
                stock_name=stock_name,
                industry_name=industry_name,
                chain=None,
                sub_industry=sub_industry,
                is_active=True,
                source="finmind",
            ))
        count += 1

    db.commit()
    logger.info("Stock master upserted: %d stocks", count)
    return count
