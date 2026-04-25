"""
M21 PART 3：peer_rank

同產業同期表現對照。四個欄位使用 top-percentile convention：
- 0.0 = 最強（產業排名第一）
- 1.0 = 最弱（產業排名最後）
- 例：0.12 = top 12%

Leader 判定：return/breakout/institution/volume 四條件，滿足 >= 2 條即 leader。

Peers < PEER_RANK_MIN_PEERS → 整段 None + data_quality note。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.analysis._helpers import fetch_active_peer_ids, resolve_query_start_date
from app.analysis.context_thresholds import (
    INDUSTRY_PRICE_LOOKBACK_DAYS,
    INDUSTRY_VOLUME_BASELINE_DAYS,
    INDUSTRY_VOLUME_RECENT_DAYS,
    LEADER_REQUIRED_CRITERIA,
    LEADER_RETURN_PCT_TOP,
    PEER_RANK_MIN_PEERS,
    PRICE_BREAKOUT_BASELINE_DAYS,
)
from app.models import DailyPrice, InstStockFlow

_INST_TYPES = ("foreign", "trust", "dealer")


def compute_peer_rank(
    db: Session,
    stock_id: str,
    buy_date: date,
    industry_name: str,
    peer_ids: Optional[List[str]] = None,
    query_start_date: Optional[date] = None,
) -> Tuple[dict, List[str]]:
    notes: List[str] = []
    empty_result = {
        "industry_name": industry_name,
        "return_5d_percentile": None,
        "volume_percentile": None,
        "institution_rank_percentile": None,
        "leader_or_follower": None,
    }

    if peer_ids is None:
        peer_ids = fetch_active_peer_ids(db, industry_name)
    if query_start_date is None:
        query_start_date = resolve_query_start_date(db, buy_date)

    if len(peer_ids) < PEER_RANK_MIN_PEERS or stock_id not in peer_ids:
        if len(peer_ids) < PEER_RANK_MIN_PEERS:
            notes.append(
                f"peer_rank is null because industry '{industry_name}' has only "
                f"{len(peer_ids)} active peers (need >= {PEER_RANK_MIN_PEERS})"
            )
        elif stock_id not in peer_ids:
            notes.append(
                f"peer_rank is null because stock {stock_id} is not in industry '{industry_name}' peer set"
            )
        return empty_result, notes

    returns = _peer_returns(db, peer_ids, buy_date, query_start_date)
    volume_ratios = _peer_volume_ratios(db, peer_ids, buy_date, query_start_date)
    inst_intensities = _peer_institution_intensity(db, peer_ids, buy_date, query_start_date)
    breakouts = _peer_breakouts(db, peer_ids, buy_date, query_start_date)

    return_pct = _top_percentile(returns, stock_id)
    volume_pct = _top_percentile(volume_ratios, stock_id)
    inst_pct = _top_percentile(inst_intensities, stock_id)

    leader = _classify_leader(
        stock_id=stock_id,
        return_pct=return_pct,
        volume_ratios=volume_ratios,
        inst_intensities=inst_intensities,
        breakouts=breakouts,
    )

    return (
        {
            "industry_name": industry_name,
            "return_5d_percentile": return_pct,
            "volume_percentile": volume_pct,
            "institution_rank_percentile": inst_pct,
            "leader_or_follower": leader,
        },
        notes,
    )


# ---------------------------------------------------------------------------
# data extraction
# ---------------------------------------------------------------------------


def _peer_returns(
    db: Session, peer_ids: Sequence[str], buy_date: date, query_start_date: date
) -> Dict[str, float]:
    """每檔 5 日收盤報酬；資料不足的檔剔除。"""
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date >= query_start_date,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        .all()
    )
    by_stock: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        if len(by_stock[r.stock_id]) < INDUSTRY_PRICE_LOOKBACK_DAYS:
            by_stock[r.stock_id].append(float(r.close_price))

    returns: Dict[str, float] = {}
    for sid, closes in by_stock.items():
        if len(closes) < INDUSTRY_PRICE_LOOKBACK_DAYS:
            continue
        latest, earliest = closes[0], closes[-1]
        if earliest == 0:
            continue
        returns[sid] = (latest - earliest) / earliest
    return returns


def _peer_volume_ratios(
    db: Session, peer_ids: Sequence[str], buy_date: date, query_start_date: date
) -> Dict[str, float]:
    """每檔的 (近 3d avg / 前 5d avg) ratio。"""
    required = INDUSTRY_VOLUME_RECENT_DAYS + INDUSTRY_VOLUME_BASELINE_DAYS
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.volume, DailyPrice.turnover)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date >= query_start_date,
            DailyPrice.trade_date <= buy_date,
        )
        .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        .all()
    )
    by_stock: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        if len(by_stock[r.stock_id]) < required:
            metric = r.turnover if r.turnover is not None else r.volume
            if metric is not None:
                by_stock[r.stock_id].append(float(metric))

    ratios: Dict[str, float] = {}
    for sid, values in by_stock.items():
        if len(values) < required:
            continue
        recent_avg = sum(values[:INDUSTRY_VOLUME_RECENT_DAYS]) / INDUSTRY_VOLUME_RECENT_DAYS
        baseline_avg = (
            sum(values[INDUSTRY_VOLUME_RECENT_DAYS:required]) / INDUSTRY_VOLUME_BASELINE_DAYS
        )
        if baseline_avg == 0:
            continue
        ratios[sid] = recent_avg / baseline_avg
    return ratios


def _peer_institution_intensity(
    db: Session, peer_ids: Sequence[str], buy_date: date, query_start_date: date
) -> Dict[str, float]:
    """每檔近 N 日三大法人合計 net_shares（強度指標）。"""
    lookback = INDUSTRY_PRICE_LOOKBACK_DAYS  # 沿用 5 日視窗
    rows = (
        db.query(
            InstStockFlow.stock_id,
            InstStockFlow.trade_date,
            InstStockFlow.net_shares,
        )
        .filter(
            InstStockFlow.stock_id.in_(peer_ids),
            InstStockFlow.trade_date >= query_start_date,
            InstStockFlow.trade_date <= buy_date,
            InstStockFlow.inst_type.in_(_INST_TYPES),
        )
        .all()
    )

    per_stock_day: Dict[Tuple[str, date], float] = defaultdict(float)
    dates_seen: Dict[str, set] = defaultdict(set)
    for r in rows:
        per_stock_day[(r.stock_id, r.trade_date)] += float(r.net_shares or 0)
        dates_seen[r.stock_id].add(r.trade_date)

    intensities: Dict[str, float] = {}
    for sid, days in dates_seen.items():
        recent_days = sorted(days, reverse=True)[:lookback]
        if not recent_days:
            continue
        intensities[sid] = sum(per_stock_day[(sid, d)] for d in recent_days)
    return intensities


def _peer_breakouts(
    db: Session, peer_ids: Sequence[str], buy_date: date, query_start_date: date
) -> Dict[str, bool]:
    """每檔是否在 buy_date 當日處於 breakout（latest close > 前 N 交易日最高 close）。"""
    required = PRICE_BREAKOUT_BASELINE_DAYS + 1
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id.in_(peer_ids),
            DailyPrice.trade_date >= query_start_date,
            DailyPrice.trade_date <= buy_date,
            DailyPrice.close_price.isnot(None),
        )
        .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        .all()
    )
    by_stock: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        if len(by_stock[r.stock_id]) < required:
            by_stock[r.stock_id].append(float(r.close_price))

    breakouts: Dict[str, bool] = {}
    for sid, closes in by_stock.items():
        if len(closes) < required:
            continue
        latest = closes[0]  # 降冪，最新在前
        prior = closes[1:required]
        if not prior:
            continue
        breakouts[sid] = latest > max(prior)
    return breakouts


# ---------------------------------------------------------------------------
# percentile + leader
# ---------------------------------------------------------------------------


def _top_percentile(values: Dict[str, float], target: str) -> Optional[float]:
    """回傳 target 在 values 中的 top-percentile（0 最強 / 1 最弱）。target 缺則 None。"""
    if target not in values:
        return None
    n = len(values)
    if n < 2:
        return 0.0 if target in values else None
    # 降冪排序，同值共享排名（用 index，直觀）
    sorted_items = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    for rank, (sid, _) in enumerate(sorted_items):
        if sid == target:
            return rank / (n - 1)
    return None  # 不應發生


def _classify_leader(
    stock_id: str,
    return_pct: Optional[float],
    volume_ratios: Dict[str, float],
    inst_intensities: Dict[str, float],
    breakouts: Dict[str, bool],
) -> Optional[str]:
    """四條件滿足 >= LEADER_REQUIRED_CRITERIA 條即 leader。任何條件缺資料不計分。"""
    criteria_met = 0

    # 1) return top 30%
    if return_pct is not None and return_pct <= LEADER_RETURN_PCT_TOP:
        criteria_met += 1

    # 2) 當下處於 breakout 且產業裡 breakout 比例 < 50%（這檔相對早動）
    if stock_id in breakouts and breakouts[stock_id]:
        total = len(breakouts)
        in_breakout = sum(1 for v in breakouts.values() if v)
        if total > 0 and in_breakout / total < 0.5:
            criteria_met += 1

    # 3) institutional buying intensity >= peer average
    if stock_id in inst_intensities and inst_intensities:
        avg = sum(inst_intensities.values()) / len(inst_intensities)
        if inst_intensities[stock_id] >= avg:
            criteria_met += 1

    # 4) volume ratio > peer average
    if stock_id in volume_ratios and volume_ratios:
        avg = sum(volume_ratios.values()) / len(volume_ratios)
        if volume_ratios[stock_id] > avg:
            criteria_met += 1

    if criteria_met >= LEADER_REQUIRED_CRITERIA:
        return "leader"
    return "follower"
