"""
M23 Step 1~5：DB ingest、產業/個股 ranking、候選池建立 + 擴散。

骨架（slice 4）：函式簽章 + docstring，body raise NotImplementedError。
真實邏輯由 slice 5 填入；型別暫以 Dict[str, Any] 占位，slice 5 再決定是否
拆 dataclass。

對應 spec：
  - §5 Step 1～5
  - §6 Candidate Pool 設計
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from sqlalchemy.orm import Session


def ingest_data(db: Session, target_date: date) -> Dict[str, Any]:
    """Step 1：讀取 DB 原始資料（spec §5 Step 1）。

    讀取範圍：inst_stock_flow / daily_price / industry_daily_flow /
    monthly_revenue / margin_trade / stocks_master，皆以 `target_date` 為基準。

    回傳 dict 預期 schema（slice 5 決定具體欄位）：
      {
        "trade_dates_recent_5d": List[date],
        "stocks_master": Dict[stock_id, master_row],
        ...
      }
    """
    raise NotImplementedError("Slice 5: deterministic ingest not implemented yet")


def compute_rankings(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
) -> Dict[str, Any]:
    """Step 2~3：產業 3d 熱錢前 10 + 個股 3d 熱錢前 40（spec §5 Step 2/3）。

    產業排行可沿用 `industry_daily_flow.total_net_amount` 3d 累加；
    個股排行可沿用 `app.hot_money_service.compute_hot_money(end_date, days=3, limit=40)`。

    回傳 dict 預期 schema：
      {
        "top_industries_3d": List[{industry_name, sub_industry, net_3d, stock_count}],
        "top_stocks_3d": List[{stock_id, name, industry, net_3d, price_change_3d}],
      }
    """
    raise NotImplementedError("Slice 5: deterministic ranking not implemented yet")


def build_candidate_pool(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
    rankings: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Step 4~5：候選池建立 + 同產業 / 集團 / 供應鏈擴散（spec §5 Step 4/5、§6）。

    擴散邏輯：
      1. top_stocks_3d 前 40 + top_industries_3d 前 10 所有成分股 + top_stocks_3d 前 10
      2. 加入熱門產業龍頭股（成交量 60d Top 1）
      3. 加入 top_stocks_3d 前 10 同集團（`exclusions.find_group_for_stock`）
      4. 加入熱門產業同 sub_industry 股票

    排除：`exclusions.should_exclude(stock_id, stock_name, industry_name)`
    截斷：超過 150 檔以「3d 法人合計 net_amount 降冪」截至 120 檔（spec §6.1）

    回傳 List of candidate dict（每筆對齊 spec §10.1 LLM Input `stock_pool[i]`，
    但尚未含 prelim_type / theme / leader_check 等 LLM 階段欄位）。
    """
    raise NotImplementedError("Slice 5: candidate pool builder not implemented yet")
