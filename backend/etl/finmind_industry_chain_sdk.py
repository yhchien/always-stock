"""
從 FinMind 拉取 TaiwanStockIndustryChain，更新 stocks_master.industry_name。

資料集：TaiwanStockIndustryChain
欄位：stock_id / industry / sub_industry / date
權限：Backer / Sponsor

更新策略：
- 每支股票取最新一筆（按 date 倒序）
- 更新 stocks_master.industry_name = industry（FinMind 細分類）
- 更新 stocks_master.sub_industry = sub_industry
- 更新 stocks_master.source = 'finmind'
- 不在 TaiwanStockIndustryChain 裡的股票保留原值，不異動
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import StockMaster

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def fetch_industry_chain(token: str) -> Dict[str, Dict[str, str]]:
    """
    從 FinMind REST API 拉取 TaiwanStockIndustryChain，
    回傳 {stock_id: {industry, sub_industry}} 各取最新一筆。

    Args:
        token: FinMind API token（Backer/Sponsor 必須帶 token）

    Returns:
        {stock_id: {"industry": str, "sub_industry": str}}
    """
    params: dict = {"dataset": "TaiwanStockIndustryChain"}
    if token:
        params["token"] = token

    url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    logger.info("Fetching TaiwanStockIndustryChain from FinMind...")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    if data.get("status") != 200:
        raise RuntimeError(f"FinMind API error: status={data.get('status')} msg={data.get('msg')}")

    rows = data.get("data", [])
    logger.info("TaiwanStockIndustryChain: %d raw rows received", len(rows))

    # 每支股票可能有多筆（不同 date），取 date 最大那筆
    # rows 通常已按 date 升序；倒著走 dict 的 setdefault 只取第一筆（即最舊），
    # 所以我們用正向遍歷，後來的覆蓋前面的（後來的 date 較大）
    latest: Dict[str, Dict[str, str]] = {}
    for row in rows:
        sid = row.get("stock_id", "").strip()
        if not sid:
            continue
        latest[sid] = {
            "industry": (row.get("industry") or "").strip(),
            "sub_industry": (row.get("sub_industry") or "").strip(),
        }

    logger.info("Unique stocks in TaiwanStockIndustryChain: %d", len(latest))
    return latest


def update_stocks_master_industry(
    db: Session,
    token: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    用 FinMind TaiwanStockIndustryChain 更新 stocks_master 的產業分類。

    Args:
        db:      SQLAlchemy session
        token:   FinMind API token
        dry_run: True 時只計算不寫入，方便預覽變更量

    Returns:
        {"updated": int, "skipped": int, "not_in_master": int}
    """
    industry_map = fetch_industry_chain(token)

    masters = {m.stock_id: m for m in db.query(StockMaster).all()}
    logger.info("stocks_master: %d stocks loaded", len(masters))

    updated = 0
    skipped = 0
    not_in_master = 0

    for stock_id, vals in industry_map.items():
        new_industry = vals["industry"]
        new_sub = vals["sub_industry"] or None

        if not new_industry:
            skipped += 1
            continue

        master = masters.get(stock_id)
        if master is None:
            not_in_master += 1
            continue

        old_industry = master.industry_name
        old_sub = master.sub_industry

        if old_industry == new_industry and old_sub == new_sub:
            skipped += 1
            continue

        if not dry_run:
            master.industry_name = new_industry
            master.sub_industry = new_sub
            master.source = "finmind"

        updated += 1
        logger.debug(
            "  %s: %r -> %r (sub: %r -> %r)",
            stock_id, old_industry, new_industry, old_sub, new_sub,
        )

    if not dry_run:
        db.commit()
        logger.info(
            "stocks_master updated: %d changed, %d unchanged, %d not in master",
            updated, skipped, not_in_master,
        )
    else:
        logger.info(
            "[dry-run] Would update %d stocks, skip %d, %d not in master",
            updated, skipped, not_in_master,
        )

    return {"updated": updated, "skipped": skipped, "not_in_master": not_in_master}
