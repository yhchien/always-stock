"""
M21 PART 1：industry_summary

跨股聚合：同產業全員的報酬、量能、法人參與度 → 結論層熱度訊號。

Fields:
- industry_name
- industry_price_strength: strong / medium / weak（多少家同步漲）
- industry_volume_trend: expanding_3d / intermittent / flat（近 3d vs 前 5d turnover）
- industry_institution_flow: strong_buy / mixed / none（近 3 日裡 >= 2 日法人淨買的家數）
- industry_news_heat: 永遠 None（DB 無來源）
- industry_hot_score: 0~8
- industry_hot_level: S / A / B / C
- industry_capital_type: trading_hot / re_rating_hot / None
- is_false_hot: True / False

資料不足時個別欄位可能為 None；整段不 raise。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.analysis.context_thresholds import (
    FALSE_HOT_SPIKE_DAYS,
    HOT_LEVEL_A_MIN,
    HOT_LEVEL_B_MIN,
    HOT_LEVEL_S_MIN,
    INDUSTRY_INSTITUTION_LOOKBACK_DAYS,
    INDUSTRY_INSTITUTION_MIXED_MIN_STOCKS,
    INDUSTRY_INSTITUTION_STRONG_MIN_STOCKS,
    INDUSTRY_PRICE_LOOKBACK_DAYS,
    INDUSTRY_PRICE_MEDIUM_POSITIVE_STOCKS,
    INDUSTRY_PRICE_STRONG_MIN_POSITIVE_STOCKS,
    INDUSTRY_VOLUME_BASELINE_DAYS,
    INDUSTRY_VOLUME_EXPANDING_PCT,
    INDUSTRY_VOLUME_INTERMITTENT_PCT,
    INDUSTRY_VOLUME_RECENT_DAYS,
)
from app.models import DailyPrice, InstStockFlow, StockMaster

_INST_TYPES = ("foreign", "trust", "dealer")


def compute_industry_signals(
    db: Session,
    stock_id: str,  # noqa: ARG001 — 產業層不需個股 id，保留統一簽章
    buy_date: date,
    industry_name: str,
) -> Tuple[dict, List[str]]:
    notes: List[str] = []

    peer_ids = _active_peer_ids(db, industry_name)
    if len(peer_ids) < INDUSTRY_PRICE_MEDIUM_POSITIVE_STOCKS:
        notes.append(
            f"industry signals degraded because industry '{industry_name}' has only "
            f"{len(peer_ids)} active peers"
        )

    price_strength = _industry_price_strength(db, peer_ids, buy_date)
    volume_trend = _industry_volume_trend(db, peer_ids, buy_date)
    institution_flow = _industry_institution_flow(db, peer_ids, buy_date)

    hot_score = _hot_score(price_strength, volume_trend, institution_flow)
    hot_level = _hot_level(hot_score) if hot_score is not None else None
    capital_type = _capital_type(price_strength, volume_trend, institution_flow)
    is_false_hot = _is_false_hot(
        db, peer_ids, buy_date, price_strength, institution_flow
    )

    return (
        {
            "industry_name": industry_name,
            "industry_price_strength": price_strength,
            "industry_volume_trend": volume_trend,
            "industry_institution_flow": institution_flow,
            "industry_news_heat": None,
            "industry_hot_score": hot_score,
            "industry_hot_level": hot_level,
            "industry_capital_type": capital_type,
            "is_false_hot": is_false_hot,
        },
        notes,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _active_peer_ids(db: Session, industry_name: str) -> List[str]:
    rows = (
        db.query(StockMaster.stock_id)
        .filter(StockMaster.industry_name == industry_name, StockMaster.is_active.is_(True))
        .all()
    )
    return [r.stock_id for r in rows]


def _industry_price_strength(
    db: Session, peer_ids: Sequence[str], buy_date: date
) -> Optional[str]:
    if not peer_ids:
        return None

    # 一次抓所有 peer 近 N 天收盤，在 Python 層算每檔 5d 報酬
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date <= buy_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        .all()
    )

    by_stock: Dict[str, List[Tuple[date, float]]] = defaultdict(list)
    for r in rows:
        if len(by_stock[r.stock_id]) < INDUSTRY_PRICE_LOOKBACK_DAYS:
            by_stock[r.stock_id].append((r.trade_date, float(r.close_price)))

    positive = 0
    total = 0
    for entries in by_stock.values():
        if len(entries) < INDUSTRY_PRICE_LOOKBACK_DAYS:
            continue  # 歷史不夠，跳過這檔
        latest = entries[0][1]
        earliest = entries[-1][1]
        if earliest == 0:
            continue
        ret = (latest - earliest) / earliest
        total += 1
        if ret > 0:
            positive += 1

    if total == 0:
        return None

    if positive >= INDUSTRY_PRICE_STRONG_MIN_POSITIVE_STOCKS:
        return "strong"
    if positive >= INDUSTRY_PRICE_MEDIUM_POSITIVE_STOCKS:
        return "medium"
    return "weak"


def _industry_volume_trend(
    db: Session, peer_ids: Sequence[str], buy_date: date
) -> Optional[str]:
    """產業整體成交值（turnover 優先，缺則用 volume）近 3d vs 前 5d。"""
    if not peer_ids:
        return None

    required = INDUSTRY_VOLUME_RECENT_DAYS + INDUSTRY_VOLUME_BASELINE_DAYS

    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.turnover, DailyPrice.volume)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date <= buy_date,
        )
        .all()
    )

    by_date: Dict[date, float] = defaultdict(float)
    for r in rows:
        metric = r.turnover if r.turnover is not None else r.volume
        if metric is None:
            continue
        by_date[r.trade_date] += float(metric)

    if len(by_date) < required:
        return None

    sorted_dates = sorted(by_date.keys(), reverse=True)[:required]
    recent = [by_date[d] for d in sorted_dates[:INDUSTRY_VOLUME_RECENT_DAYS]]
    baseline = [by_date[d] for d in sorted_dates[INDUSTRY_VOLUME_RECENT_DAYS:required]]

    baseline_avg = sum(baseline) / len(baseline) if baseline else 0
    recent_avg = sum(recent) / len(recent) if recent else 0
    if baseline_avg == 0:
        return None

    ratio = (recent_avg - baseline_avg) / baseline_avg
    if ratio >= INDUSTRY_VOLUME_EXPANDING_PCT:
        return "expanding_3d"
    if ratio >= INDUSTRY_VOLUME_INTERMITTENT_PCT:
        return "intermittent"
    return "flat"


def _industry_institution_flow(
    db: Session, peer_ids: Sequence[str], buy_date: date
) -> Optional[str]:
    """近 N 日裡，同產業有 >=2 日法人淨買的股票家數 → 分三檔。"""
    if not peer_ids:
        return None

    recent_dates = _recent_flow_dates(db, peer_ids, buy_date, INDUSTRY_INSTITUTION_LOOKBACK_DAYS)
    if not recent_dates:
        # 無任何法人資料 → 視為「無明顯法人參與」而非 unknown
        return "none"
    if len(recent_dates) < INDUSTRY_INSTITUTION_LOOKBACK_DAYS:
        return None

    rows = (
        db.query(
            InstStockFlow.stock_id,
            InstStockFlow.trade_date,
            InstStockFlow.net_shares,
        )
        .filter(
            InstStockFlow.stock_id.in_(peer_ids),
            InstStockFlow.trade_date.in_(recent_dates),
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )

    # (stock_id, trade_date) → 三大法人合計 net
    per_day_net: Dict[Tuple[str, date], float] = defaultdict(float)
    for r in rows:
        per_day_net[(r.stock_id, r.trade_date)] += float(r.net_shares or 0)

    qualifying_stocks = 0
    for sid in peer_ids:
        net_buy_days = sum(
            1 for d in recent_dates if per_day_net.get((sid, d), 0) > 0
        )
        if net_buy_days >= 2:
            qualifying_stocks += 1

    if qualifying_stocks >= INDUSTRY_INSTITUTION_STRONG_MIN_STOCKS:
        return "strong_buy"
    if qualifying_stocks >= INDUSTRY_INSTITUTION_MIXED_MIN_STOCKS:
        return "mixed"
    return "none"


def _recent_flow_dates(
    db: Session, peer_ids: Sequence[str], buy_date: date, n: int
) -> List[date]:
    rows = (
        db.query(InstStockFlow.trade_date)
        .filter(
            InstStockFlow.stock_id.in_(peer_ids),
            InstStockFlow.trade_date <= buy_date,
        )
        .distinct()
        .order_by(InstStockFlow.trade_date.desc())
        .limit(n)
        .all()
    )
    return [r.trade_date for r in rows]


def _hot_score(
    price_strength: Optional[str],
    volume_trend: Optional[str],
    institution_flow: Optional[str],
) -> Optional[int]:
    """四維度加總 0~8；任何一維缺資料 → 整體 None，避免誤導分數。"""
    if price_strength is None or volume_trend is None or institution_flow is None:
        return None

    score = 0
    score += {"strong": 2, "medium": 1, "weak": 0}.get(price_strength, 0)
    score += {"expanding_3d": 2, "intermittent": 1, "flat": 0}.get(volume_trend, 0)
    score += {"strong_buy": 2, "mixed": 1, "none": 0}.get(institution_flow, 0)
    # news_heat 永遠 0
    return score


def _hot_level(score: int) -> str:
    if score >= HOT_LEVEL_S_MIN:
        return "S"
    if score >= HOT_LEVEL_A_MIN:
        return "A"
    if score >= HOT_LEVEL_B_MIN:
        return "B"
    return "C"


def _capital_type(
    price_strength: Optional[str],
    volume_trend: Optional[str],
    institution_flow: Optional[str],
) -> Optional[str]:
    if price_strength is None or volume_trend is None or institution_flow is None:
        return None

    # re_rating：價量齊揚、法人續買、基本面擴散
    if (
        price_strength == "strong"
        and volume_trend == "expanding_3d"
        and institution_flow == "strong_buy"
    ):
        return "re_rating_hot"

    # trading：量能尖峰但法人沒跟 / 價漲集中
    if volume_trend != "flat" and institution_flow in (None, "none") and price_strength != "strong":
        return "trading_hot"

    return None


def _is_false_hot(
    db: Session,
    peer_ids: Sequence[str],
    buy_date: date,
    price_strength: Optional[str],
    institution_flow: Optional[str],
) -> Optional[bool]:
    """All of: 1-2 天 volume spike + 法人不支持 + 價格廣度未擴散。

    注意 volume_trend 與 spike_days 在單日爆量時可能同時成立（1 天 3 倍量會把 3d avg 也拉上去），
    兩者語義正交，這裡以 spike_days 為主判斷。
    """
    if not peer_ids:
        return None
    if institution_flow is None or price_strength is None:
        return None

    spike_days = _count_spike_days(db, peer_ids, buy_date)
    if not (1 <= spike_days <= FALSE_HOT_SPIKE_DAYS):
        return False

    if institution_flow == "strong_buy":
        return False
    if price_strength == "strong":
        return False

    return True


def _count_spike_days(
    db: Session, peer_ids: Sequence[str], buy_date: date
) -> int:
    """找產業 turnover 近 N 日是否只有 1-2 日異常高（相對前期）。"""
    required = INDUSTRY_VOLUME_RECENT_DAYS + INDUSTRY_VOLUME_BASELINE_DAYS
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.turnover, DailyPrice.volume)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date <= buy_date,
        )
        .all()
    )
    by_date: Dict[date, float] = defaultdict(float)
    for r in rows:
        metric = r.turnover if r.turnover is not None else r.volume
        if metric is None:
            continue
        by_date[r.trade_date] += float(metric)

    if len(by_date) < required:
        return 0

    sorted_dates = sorted(by_date.keys(), reverse=True)[:required]
    baseline_values = [by_date[d] for d in sorted_dates[INDUSTRY_VOLUME_RECENT_DAYS:required]]
    baseline_avg = sum(baseline_values) / len(baseline_values) if baseline_values else 0
    if baseline_avg == 0:
        return 0

    spike_threshold = baseline_avg * 1.5
    recent_values = [by_date[d] for d in sorted_dates[:INDUSTRY_VOLUME_RECENT_DAYS]]
    return sum(1 for v in recent_values if v >= spike_threshold)
