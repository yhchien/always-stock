"""
M23 Step 1~5：DB ingest、產業/個股 ranking、候選池建立 + 擴散。

Slice 5：deterministic 邏輯實作完成。

對應 spec：
  - §5 Step 1～5
  - §6 Candidate Pool 設計
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.hot_money_service import compute_hot_money, get_recent_trade_dates
from app.models import (
    DailyPrice,
    IndustryDailyFlow,
    InstStockFlow,
    MarginTrade,
    StockMaster,
)
from app.signals.exclusions import (
    find_group_for_stock,
    get_group_members,
    should_exclude,
)

logger = logging.getLogger(__name__)

_INST_TYPES = ("foreign", "trust", "dealer")

# Spec §5 Step 1 / Step 2
TOP_INDUSTRIES_LIMIT = 10
TOP_STOCKS_LIMIT = 40
TOP_STOCKS_INNER = 10  # spec §6 group expansion 取 top 10

# Spec §6.1 候選池規模
POOL_SOFT_TRIGGER = 150  # 超過此數量啟動截斷
POOL_HARD_LIMIT = 120


# ---------- Step 1：ingest ----------


def ingest_data(db: Session, target_date: date) -> Dict[str, Any]:
    """讀取 DB 原始資料的窗口元資料（spec §5 Step 1）。

    回傳：
      {
        "target_date": date,
        "trade_dates_3d": [...],   # 由舊到新（最舊→最新）
        "trade_dates_5d": [...],
        "trade_dates_10d": [...],
        "trade_dates_60d": [...],
        "stocks_master": {stock_id: StockMaster, ...} (僅 is_active=True)
      }
    """
    trade_dates_60d = get_recent_trade_dates(db, target_date, 60)
    if not trade_dates_60d:
        return {
            "target_date": target_date,
            "trade_dates_3d": [],
            "trade_dates_5d": [],
            "trade_dates_10d": [],
            "trade_dates_60d": [],
            "stocks_master": {},
        }
    trade_dates_5d = trade_dates_60d[-5:]
    trade_dates_3d = trade_dates_60d[-3:]
    trade_dates_10d = trade_dates_60d[-10:]

    masters = (
        db.query(StockMaster)
        .filter(StockMaster.is_active.is_(True))
        .all()
    )

    return {
        "target_date": target_date,
        "trade_dates_3d": trade_dates_3d,
        "trade_dates_5d": trade_dates_5d,
        "trade_dates_10d": trade_dates_10d,
        "trade_dates_60d": trade_dates_60d,
        "stocks_master": {m.stock_id: m for m in masters},
    }


# ---------- Step 2/3：rankings ----------


def compute_rankings(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
) -> Dict[str, Any]:
    """spec §5 Step 1 / Step 2：產業 3d 熱錢前 10 + 個股 3d 熱錢前 40。"""
    trade_dates_3d = ingestion.get("trade_dates_3d") or []
    masters: Dict[str, StockMaster] = ingestion.get("stocks_master") or {}

    if not trade_dates_3d:
        return {"top_industries_3d": [], "top_stocks_3d": []}

    # 產業排行：直接從 industry_daily_flow 聚合（已含三大法人總額）
    industry_rows = (
        db.query(
            IndustryDailyFlow.industry_name,
            func.sum(IndustryDailyFlow.total_net_amount).label("net_3d"),
        )
        .filter(IndustryDailyFlow.trade_date.in_(trade_dates_3d))
        .group_by(IndustryDailyFlow.industry_name)
        .order_by(func.sum(IndustryDailyFlow.total_net_amount).desc())
        .limit(TOP_INDUSTRIES_LIMIT)
        .all()
    )

    industry_counts: Dict[str, int] = {}
    for master in masters.values():
        if master.industry_name:
            industry_counts[master.industry_name] = (
                industry_counts.get(master.industry_name, 0) + 1
            )

    top_industries = [
        {
            "industry_name": ind,
            "net_3d": float(net or 0.0),
            "stock_count": industry_counts.get(ind, 0),
            "rank": idx,
        }
        for idx, (ind, net) in enumerate(industry_rows, start=1)
    ]

    # 個股排行：沿用 M22 hot_money_service
    hot_result = compute_hot_money(db, target_date, days=3, limit=TOP_STOCKS_LIMIT)
    top_stocks = [
        {
            "stock_id": item.stock_id,
            "name": item.stock_name,
            "industry": item.industry_name,
            "sub_industry": item.sub_industry,
            "net_3d": float(item.total_net_amount or 0.0),
            "price_change_3d": item.price_change_pct,
            "rank": item.rank,
        }
        for item in hot_result.items
    ]

    return {
        "top_industries_3d": top_industries,
        "top_stocks_3d": top_stocks,
    }


# ---------- Step 4/5：candidate pool ----------


def build_candidate_pool(
    db: Session,
    target_date: date,
    ingestion: Dict[str, Any],
    rankings: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """spec §5 Step 4 + §6：候選池組合 + 擴散 + 截斷。"""
    masters: Dict[str, StockMaster] = ingestion.get("stocks_master") or {}
    top_industries = rankings.get("top_industries_3d") or []
    top_stocks = rankings.get("top_stocks_3d") or []

    if not masters or (not top_industries and not top_stocks):
        return []

    # 1. 收集候選 stock_id（聯集）
    candidate_ids: Set[str] = set()

    # 1a. top_stocks_3d 前 40
    for s in top_stocks:
        candidate_ids.add(s["stock_id"])

    # 1b. top_industries_3d 前 10 的所有成分股
    industry_set = {ind["industry_name"] for ind in top_industries}
    for sid, master in masters.items():
        if master.industry_name in industry_set:
            candidate_ids.add(sid)

    # 1c. top_stocks_3d 前 10 的同集團（spec §6）
    for s in top_stocks[:TOP_STOCKS_INNER]:
        group_name = find_group_for_stock(s["stock_id"])
        if not group_name:
            continue
        for member_id in get_group_members(group_name):
            candidate_ids.add(member_id)

    # 2. 排除 ETF / 金融股 / 不在 stocks_master 的（無業務面資料）
    filtered_ids: List[str] = []
    for sid in candidate_ids:
        master = masters.get(sid)
        if master is None:
            continue
        if should_exclude(sid, master.stock_name, master.industry_name):
            continue
        filtered_ids.append(sid)

    if not filtered_ids:
        return []

    # 3. 計算每檔的 metrics（一次性 query，不 per-stock）
    metrics = _compute_pool_metrics(db, ingestion, filtered_ids)

    # 4. 為每檔 enrich 集團 / industry / top flag
    top_stock_id_set = {s["stock_id"] for s in top_stocks}

    candidates: List[Dict[str, Any]] = []
    for sid in filtered_ids:
        master = masters.get(sid)
        if master is None:
            continue
        m = metrics.get(sid) or _empty_metrics()
        in_top = master.industry_name in industry_set
        candidates.append(
            {
                "stock_id": sid,
                "name": master.stock_name,
                "industry": master.industry_name,
                "sub_industry": master.sub_industry,
                "is_etf": False,
                "is_financial": False,
                "group_name": find_group_for_stock(sid),
                "in_top_industries_3d": in_top,
                "in_top_stocks_3d": sid in top_stock_id_set,
                **m,
            }
        )

    # 5. 算「該產業內」price_change_5d / net_3d 排名
    _attach_industry_rankings(candidates)

    # 6. 截斷（spec §6.1）
    # spec 描述「LEADER candidate (rank 高) > FOLLOWER candidate > 其他」應優先保留，
    # 但截斷發生在 classification.classify_stocks() 之前，這時候還沒有 prelim_type 可用，
    # 所以用 total_institution_flow_3d（三大法人 3 日累計淨買超）做近似 proxy：
    #   - LEADER 通常法人連買金額最大 → 排序前段
    #   - FOLLOWER 法人金額中等
    #   - LAGGARD / 弱勢 / 雜訊 → 法人金額 ~0 或負 → 截斷時優先丟
    # 實務上候選池 60~120 檔幾乎觸發不到 SOFT_TRIGGER=150 hard limit，這段是安全網，
    # 真的觸發時用 deterministic proxy 排序而不是隨機切，避免 LEADER 被丟掉的最壞情況。
    # 若日後發現 proxy 不夠精準，可加一層 lite 預分類（看 net_3d > 0 + price_change_5d > 0
    # 提早標 prelim_type）再截斷；目前 proxy 已足夠。
    if len(candidates) > POOL_SOFT_TRIGGER:
        candidates.sort(
            key=lambda c: c.get("total_institution_flow_3d") or 0.0,
            reverse=True,
        )
        candidates = candidates[:POOL_HARD_LIMIT]

    return candidates


# ---------- helpers：metrics 計算 ----------


def _empty_metrics() -> Dict[str, Any]:
    return {
        "price_change_1d": None,
        "price_change_3d": None,
        "price_change_5d": None,
        "price_change_10d": None,
        "volume_5d_to_60d_ratio": None,
        "volume_1d_to_60d_ratio": None,
        "volume_1d_to_5d_ratio": None,
        "avg_turnover_5d": None,
        "open_1d": None,
        "high_1d": None,
        "low_1d": None,
        "close_1d": None,
        "high_10d": None,
        "low_10d": None,
        "ma_5d": None,
        "ma_10d": None,
        "foreign_flow_1d": 0.0,
        "foreign_flow_3d": 0.0,
        "foreign_flow_5d": 0.0,
        "trust_flow_1d": 0.0,
        "trust_flow_3d": 0.0,
        "trust_flow_5d": 0.0,
        "dealer_flow_1d": 0.0,
        "dealer_flow_3d": 0.0,
        "dealer_flow_5d": 0.0,
        "total_institution_flow_1d": 0.0,
        "total_institution_flow_3d": 0.0,
        "total_institution_flow_5d": 0.0,
        "consecutive_buy_days_3d": 0,
        "margin_change_1d": None,
        "margin_change_3d": None,
        "short_change_1d": None,
        "short_change_3d": None,
    }


def _compute_pool_metrics(
    db: Session,
    ingestion: Dict[str, Any],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """一次性 query daily_price / inst_stock_flow / margin_trade，再 per-stock 計算。"""
    if not stock_ids:
        return {}

    trade_dates_3d: List[date] = ingestion.get("trade_dates_3d") or []
    trade_dates_5d: List[date] = ingestion.get("trade_dates_5d") or []
    trade_dates_10d: List[date] = ingestion.get("trade_dates_10d") or []
    trade_dates_60d: List[date] = ingestion.get("trade_dates_60d") or []
    if not trade_dates_5d:
        return {sid: _empty_metrics() for sid in stock_ids}

    # daily_price 60d
    price_rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.trade_date.in_(trade_dates_60d),
            DailyPrice.stock_id.in_(list(stock_ids)),
        )
        .all()
    )
    by_stock: Dict[str, List[DailyPrice]] = {}
    for row in price_rows:
        by_stock.setdefault(row.stock_id, []).append(row)
    for sid in by_stock:
        by_stock[sid].sort(key=lambda r: r.trade_date)

    # inst_stock_flow 5d（涵蓋 1d/3d/5d）
    flow_by_stock = _aggregate_flow_by_day(db, trade_dates_5d, stock_ids)

    # margin_trade 5d
    margin_by_stock = _load_margin_by_day(db, trade_dates_5d, stock_ids)

    metrics: Dict[str, Dict[str, Any]] = {}
    target_date = trade_dates_5d[-1]
    for sid in stock_ids:
        rows = by_stock.get(sid, [])
        m = _build_stock_metrics(
            rows,
            flow_by_stock.get(sid, {}),
            margin_by_stock.get(sid, {}),
            target_date=target_date,
            trade_dates_3d=trade_dates_3d,
            trade_dates_5d=trade_dates_5d,
            trade_dates_10d=trade_dates_10d,
        )
        metrics[sid] = m
    return metrics


def _aggregate_flow_by_day(
    db: Session,
    trade_dates: Sequence[date],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[date, Dict[str, float]]]:
    """`{stock_id: {trade_date: {foreign, trust, dealer, total}}}`"""
    if not trade_dates or not stock_ids:
        return {}
    rows = (
        db.query(
            InstStockFlow.stock_id,
            InstStockFlow.trade_date,
            InstStockFlow.inst_type,
            InstStockFlow.net_amount_est,
        )
        .filter(
            InstStockFlow.trade_date.in_(list(trade_dates)),
            InstStockFlow.stock_id.in_(list(stock_ids)),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )
    bucket: Dict[str, Dict[date, Dict[str, float]]] = {}
    for sid, td, inst, amt in rows:
        day = bucket.setdefault(sid, {}).setdefault(
            td, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0, "total": 0.0}
        )
        v = float(amt or 0.0)
        day[inst] += v
        day["total"] += v
    return bucket


def _load_margin_by_day(
    db: Session,
    trade_dates: Sequence[date],
    stock_ids: Sequence[str],
) -> Dict[str, Dict[date, Dict[str, Optional[int]]]]:
    if not trade_dates or not stock_ids:
        return {}
    rows = (
        db.query(MarginTrade)
        .filter(
            MarginTrade.trade_date.in_(list(trade_dates)),
            MarginTrade.stock_id.in_(list(stock_ids)),
        )
        .all()
    )
    bucket: Dict[str, Dict[date, Dict[str, Optional[int]]]] = {}
    for row in rows:
        bucket.setdefault(row.stock_id, {})[row.trade_date] = {
            "margin_balance": row.margin_balance,
            "margin_change": row.margin_change,
            "short_balance": row.short_balance,
            "short_change": row.short_change,
        }
    return bucket


def _build_stock_metrics(
    price_rows: Sequence[DailyPrice],
    flow_by_day: Dict[date, Dict[str, float]],
    margin_by_day: Dict[date, Dict[str, Optional[int]]],
    *,
    target_date: date,
    trade_dates_3d: Sequence[date],
    trade_dates_5d: Sequence[date],
    trade_dates_10d: Sequence[date],
) -> Dict[str, Any]:
    out = _empty_metrics()
    if not price_rows:
        # 即使無 price，仍要算 flow（可能停牌但仍有法人資料）
        _fill_flow_metrics(out, flow_by_day, trade_dates_3d, trade_dates_5d)
        _fill_margin_metrics(out, margin_by_day, trade_dates_3d)
        return out

    # 索引：trade_date → row
    by_date = {r.trade_date: r for r in price_rows}
    last_row = price_rows[-1]
    out["open_1d"] = last_row.open_price
    out["high_1d"] = last_row.high_price
    out["low_1d"] = last_row.low_price
    out["close_1d"] = last_row.close_price

    # price_change_*：close(target) / close(target-Nd 前一交易日) - 1
    out["price_change_1d"] = _pct_change(price_rows, n=1)
    out["price_change_3d"] = _pct_change(price_rows, n=3)
    out["price_change_5d"] = _pct_change(price_rows, n=5)
    out["price_change_10d"] = _pct_change(price_rows, n=10)

    # 5MA / 10MA
    closes = [r.close_price for r in price_rows if r.close_price is not None]
    if closes:
        out["ma_5d"] = sum(closes[-5:]) / min(5, len(closes))
        out["ma_10d"] = sum(closes[-10:]) / min(10, len(closes))

    # 60d / 5d / 1d 量能
    volumes = [r.volume for r in price_rows if r.volume is not None and r.volume > 0]
    if volumes:
        avg_60d = sum(volumes) / len(volumes)
        avg_5d = sum(volumes[-5:]) / min(5, len(volumes))
        last_vol = volumes[-1]
        if avg_60d > 0:
            out["volume_5d_to_60d_ratio"] = avg_5d / avg_60d
            out["volume_1d_to_60d_ratio"] = last_vol / avg_60d
        if avg_5d > 0:
            out["volume_1d_to_5d_ratio"] = last_vol / avg_5d

    # avg_turnover_5d（流動性 filter 用）
    turnovers = [r.turnover for r in price_rows[-5:] if r.turnover is not None]
    if turnovers:
        out["avg_turnover_5d"] = sum(turnovers) / len(turnovers)

    # high_10d / low_10d
    last_10 = [r for r in price_rows if r.trade_date in trade_dates_10d]
    if last_10:
        highs = [r.high_price for r in last_10 if r.high_price is not None]
        lows = [r.low_price for r in last_10 if r.low_price is not None]
        if highs:
            out["high_10d"] = max(highs)
        if lows:
            out["low_10d"] = min(lows)

    _fill_flow_metrics(out, flow_by_day, trade_dates_3d, trade_dates_5d)
    _fill_margin_metrics(out, margin_by_day, trade_dates_3d)
    return out


def _pct_change(price_rows: Sequence[DailyPrice], *, n: int) -> Optional[float]:
    """close(latest) / close(latest - n trade days) - 1，% 表示。"""
    if len(price_rows) < n + 1:
        return None
    cur = price_rows[-1].close_price
    prev = price_rows[-(n + 1)].close_price
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / prev * 100.0


def _fill_flow_metrics(
    out: Dict[str, Any],
    flow_by_day: Dict[date, Dict[str, float]],
    trade_dates_3d: Sequence[date],
    trade_dates_5d: Sequence[date],
) -> None:
    """法人 1d / 3d / 5d 累計 + consecutive_buy_days_3d。"""
    if not trade_dates_5d:
        return
    last_day = trade_dates_5d[-1]

    def _sum(window: Sequence[date], key: str) -> float:
        return sum(
            (flow_by_day.get(d, {}).get(key) or 0.0) for d in window
        )

    out["foreign_flow_1d"] = float(flow_by_day.get(last_day, {}).get("foreign", 0.0))
    out["trust_flow_1d"] = float(flow_by_day.get(last_day, {}).get("trust", 0.0))
    out["dealer_flow_1d"] = float(flow_by_day.get(last_day, {}).get("dealer", 0.0))
    out["total_institution_flow_1d"] = float(
        flow_by_day.get(last_day, {}).get("total", 0.0)
    )

    out["foreign_flow_3d"] = _sum(trade_dates_3d, "foreign")
    out["trust_flow_3d"] = _sum(trade_dates_3d, "trust")
    out["dealer_flow_3d"] = _sum(trade_dates_3d, "dealer")
    out["total_institution_flow_3d"] = _sum(trade_dates_3d, "total")

    out["foreign_flow_5d"] = _sum(trade_dates_5d, "foreign")
    out["trust_flow_5d"] = _sum(trade_dates_5d, "trust")
    out["dealer_flow_5d"] = _sum(trade_dates_5d, "dealer")
    out["total_institution_flow_5d"] = _sum(trade_dates_5d, "total")

    # 三大法人合計近 3 日連買日數（spec §7.1）
    out["consecutive_buy_days_3d"] = sum(
        1 for d in trade_dates_3d if (flow_by_day.get(d, {}).get("total", 0.0) or 0.0) > 0
    )


def _fill_margin_metrics(
    out: Dict[str, Any],
    margin_by_day: Dict[date, Dict[str, Optional[int]]],
    trade_dates_3d: Sequence[date],
) -> None:
    """spec §10.1 margin_change_1d / 3d 為「相對昨日餘額的變動率」。"""
    if not trade_dates_3d:
        return
    today = trade_dates_3d[-1]
    today_row = margin_by_day.get(today)

    if today_row:
        bal = today_row.get("margin_balance") or 0
        chg = today_row.get("margin_change")
        if chg is not None and bal:
            prev = bal - chg
            if prev:
                out["margin_change_1d"] = chg / prev
        s_bal = today_row.get("short_balance") or 0
        s_chg = today_row.get("short_change")
        if s_chg is not None and s_bal:
            prev_s = s_bal - s_chg
            if prev_s:
                out["short_change_1d"] = s_chg / prev_s

    # 3d ratio：今日 - 3d 前的餘額 / 3d 前餘額
    if len(trade_dates_3d) >= 3:
        oldest = trade_dates_3d[0]
        oldest_row = margin_by_day.get(oldest)
        if today_row and oldest_row:
            today_bal = today_row.get("margin_balance")
            old_bal = oldest_row.get("margin_balance")
            if today_bal is not None and old_bal:
                # old_bal 是「該日收盤」，前一日餘額 = old_bal - old_change
                old_chg = oldest_row.get("margin_change") or 0
                base = old_bal - old_chg
                if base:
                    out["margin_change_3d"] = (today_bal - base) / base

            today_short = today_row.get("short_balance")
            old_short = oldest_row.get("short_balance")
            if today_short is not None and old_short:
                old_s_chg = oldest_row.get("short_change") or 0
                base_s = old_short - old_s_chg
                if base_s:
                    out["short_change_3d"] = (today_short - base_s) / base_s


def _attach_industry_rankings(candidates: List[Dict[str, Any]]) -> None:
    """為每筆候選股加 `industry_rank_5d` / `industry_rank_net_3d` / `industry_count`。

    排名為 1-indexed，None 值排在最後。
    """
    by_industry: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        ind = c.get("industry") or ""
        by_industry.setdefault(ind, []).append(c)

    for ind, group in by_industry.items():
        count = len(group)
        # price_change_5d 排名（None 視為 -inf）
        ranked_price = sorted(
            group,
            key=lambda c: (
                c.get("price_change_5d") if c.get("price_change_5d") is not None else float("-inf")
            ),
            reverse=True,
        )
        for idx, c in enumerate(ranked_price, start=1):
            c["industry_rank_5d"] = idx
            c["industry_count"] = count
        # net_3d 排名
        ranked_net = sorted(
            group,
            key=lambda c: c.get("total_institution_flow_3d") or 0.0,
            reverse=True,
        )
        for idx, c in enumerate(ranked_net, start=1):
            c["industry_rank_net_3d"] = idx


def _industry_top_pct(rank: Optional[int], count: Optional[int], pct: float) -> bool:
    """rank 是否在前 pct%（用 ceil；count<=0 視為 False）。"""
    if rank is None or count is None or count <= 0:
        return False
    threshold = max(1, math.ceil(count * pct))
    return rank <= threshold
