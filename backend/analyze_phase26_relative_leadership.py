"""Phase 2.6 Relative Leadership 快速假設驗證（2026-07-24）。

**只做研究，不修改任何 production 程式碼、不重跑完整 pipeline_v2、不重跑 617 檔。**

沿用既有 20 檔 matched sample（`/tmp/phase26_matched_20.json`，10 BIG_LOSER +
10 WINNER，Day0 rs_market_percentile_20d/momentum_score 高度相近、EXTENDED_3D
狀態相同、regime 相同）。本輪只回答一件事：

    「真正的大贏家，是否比大虧股更具有『持續性的同族群領先地位』？」

Peer scope 沿用 Phase 2 `sector_context.py` 的 canonical taxonomy hierarchy
（SUB_SECTOR → PRIMARY_SECTOR → UNAVAILABLE，MIN_PEER_SAMPLE=5，
classification_confidence 需 HIGH/MEDIUM），但**不重新跑全市場 momentum frame**
——改用「20 日報酬」這個最簡單、可直接對 peer 群逐檔查詢的 composite ranking
metric（純粹是研究用的簡化選擇，不是新設計的 score，見 spec 指示「不要設計複雜
新 score」）。

用法：
    python analyze_phase26_relative_leadership.py
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice, SecurityClassification

MATCHED_20_PATH = "/tmp/phase26_matched_20.json"
REPLAY_617_PATH = "/tmp/phase25_replay_60d.json"
OUT_CSV = "/tmp/phase26_relative_leadership_matched20.csv"

MIN_PEER_SAMPLE = 5
USABLE_CONFIDENCE = ("HIGH", "MEDIUM")
RETURN_LOOKBACK_DAYS = 20  # composite ranking metric：20 日報酬（既有概念，非新設計）
TOP20_PCT_THRESHOLD = 0.20  # peer group 前 20% 視為「前段」


def load_cohort() -> Dict[str, Dict[str, Any]]:
    with open(REPLAY_617_PATH, encoding="utf-8") as f:
        data = json.load(f)
    flat = data["flat_records"]
    first_seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(flat, key=lambda r: r["catch_date"]):
        first_seen.setdefault(r["stock_id"], r)
    return first_seen


def resolve_peer_group(db, stock_id: str) -> Dict[str, Any]:
    """沿用 sector_context.py 的 hierarchy：SUB_SECTOR → PRIMARY_SECTOR → UNAVAILABLE。"""
    target = db.query(SecurityClassification).filter(SecurityClassification.stock_id == stock_id).first()
    if not target:
        return {"peer_scope": "UNAVAILABLE", "peer_ids": [], "sub_sector": None, "primary_sector": None}

    confidence_ok = target.classification_confidence in USABLE_CONFIDENCE
    sub_sector, primary_sector = target.sub_sector, target.primary_sector

    def _peers(filter_clause) -> List[str]:
        rows = (
            db.query(SecurityClassification.stock_id)
            .filter(filter_clause, SecurityClassification.classification_confidence.in_(USABLE_CONFIDENCE))
            .all()
        )
        return [r[0] for r in rows]

    if confidence_ok and primary_sector and sub_sector:
        sub_peers = _peers(
            (SecurityClassification.primary_sector == primary_sector)
            & (SecurityClassification.sub_sector == sub_sector)
        )
        if len(sub_peers) >= MIN_PEER_SAMPLE:
            return {"peer_scope": "SUB_SECTOR", "peer_ids": sub_peers, "sub_sector": sub_sector, "primary_sector": primary_sector}

    if confidence_ok and primary_sector:
        primary_peers = _peers(SecurityClassification.primary_sector == primary_sector)
        if len(primary_peers) >= MIN_PEER_SAMPLE:
            return {"peer_scope": "PRIMARY_SECTOR", "peer_ids": primary_peers, "sub_sector": sub_sector, "primary_sector": primary_sector}

    return {"peer_scope": "UNAVAILABLE", "peer_ids": [], "sub_sector": sub_sector, "primary_sector": primary_sector}


def compute_peer_ranks_for_window(
    db,
    stock_id: str,
    peer_ids: List[str],
    day0: date,
    all_days: List[date],
    day_index: Dict[date, int],
) -> Dict[int, Dict[str, Any]]:
    """對 Day-4~Day0（5 天），用「20 日報酬」當 composite ranking metric，
    算出 stock_id 在 peer_ids 群裡的排名（1=最強）與 top-percentile（0=最強/1=最弱）。
    一次查詢抓齊整個所需視窗（day0-4 往回推 20 日 ~ day0），peer 全部一起算。
    """
    if day0 not in day_index or stock_id not in peer_ids:
        return {}
    i0 = day_index[day0]
    offsets = (-4, -3, -2, -1, 0)
    needed_js = [i0 + off for off in offsets]
    min_j = min(needed_js) - RETURN_LOOKBACK_DAYS
    if min_j < 0:
        return {}
    query_days = all_days[min_j: max(needed_js) + 1]

    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close_price)
        .filter(DailyPrice.stock_id.in_(peer_ids), DailyPrice.trade_date.in_(query_days))
        .all()
    )
    price_by_stock: Dict[str, Dict[date, float]] = {}
    for sid, td, close in rows:
        if close is None:
            continue
        price_by_stock.setdefault(sid, {})[td] = float(close)

    out: Dict[int, Dict[str, Any]] = {}
    for off in offsets:
        j = i0 + off
        d = all_days[j]
        lookback_j = j - RETURN_LOOKBACK_DAYS
        if lookback_j < 0:
            continue
        lookback_d = all_days[lookback_j]

        returns: Dict[str, float] = {}
        for sid, prices in price_by_stock.items():
            cur = prices.get(d)
            prev = prices.get(lookback_d)
            if cur is not None and prev is not None and prev != 0:
                returns[sid] = (cur / prev - 1.0) * 100.0

        if stock_id not in returns or len(returns) < 2:
            out[off] = {"peer_rank": None, "peer_count": len(returns), "peer_rank_percentile": None}
            continue

        ordered = sorted(returns.items(), key=lambda kv: kv[1], reverse=True)
        rank = next(i for i, (sid, _) in enumerate(ordered, start=1) if sid == stock_id)
        n = len(ordered)
        percentile = (rank - 1) / (n - 1) if n > 1 else 0.0  # 0=最強 / 1=最弱
        out[off] = {"peer_rank": rank, "peer_count": n, "peer_rank_percentile": round(percentile, 3)}
    return out


def classify_direction(ranks: List[Optional[int]]) -> str:
    """Day-4→Day0 排名軌跡的簡單敘述性分類（不接 production，純描述）。"""
    valid = [(i, r) for i, r in enumerate(ranks) if r is not None]
    if len(valid) < 2:
        return "UNKNOWN"
    first_idx, first_rank = valid[0]
    last_idx, last_rank = valid[-1]
    diffs = [valid[i + 1][1] - valid[i][1] for i in range(len(valid) - 1)]
    improving_steps = sum(1 for d in diffs if d < 0)
    worsening_steps = sum(1 for d in diffs if d > 0)
    net_change = last_rank - first_rank  # 負值 = 名次數字變小 = 進步

    if abs(net_change) <= 1 and max(r for _, r in valid) - min(r for _, r in valid) <= 3:
        return "STABLE_LEADER" if last_rank <= 3 else "STABLE"
    if net_change < 0 and improving_steps >= worsening_steps:
        return "IMPROVING"
    if net_change > 0 and worsening_steps >= improving_steps:
        return "DETERIORATING"
    return "VOLATILE"


def main() -> None:
    cohort = load_cohort()
    with open(MATCHED_20_PATH, encoding="utf-8") as f:
        matched = json.load(f)
    all_stock_ids = matched["losers"] + matched["winners"]
    outcome = {sid: "BIG_LOSER" for sid in matched["losers"]}
    outcome.update({sid: "WINNER" for sid in matched["winners"]})

    db = SessionLocal()
    try:
        anchor_end = date(2026, 7, 22)
        # 130 天：涵蓋最早 catch_date（2026-04-13）前還要再扣 24 個交易日
        # （offset -4 + RETURN_LOOKBACK_DAYS 20）的緩衝，避免 min_j 算出負索引
        all_days = get_recent_trade_dates(db, anchor_end, 130)
        day_index = {d: i for i, d in enumerate(all_days)}
        print(f"trading calendar: {len(all_days)} days ({all_days[0]} ~ {all_days[-1]})")

        rows_out = []
        for sid in all_stock_ids:
            rec = cohort[sid]
            day0 = date.fromisoformat(rec["catch_date"])
            peer_info = resolve_peer_group(db, sid)
            peer_ranks = {}
            if peer_info["peer_scope"] != "UNAVAILABLE":
                peer_ranks = compute_peer_ranks_for_window(
                    db, sid, peer_info["peer_ids"], day0, all_days, day_index
                )

            ranks_5d = [peer_ranks.get(off, {}).get("peer_rank") for off in (-4, -3, -2, -1, 0)]
            pct_5d = [peer_ranks.get(off, {}).get("peer_rank_percentile") for off in (-4, -3, -2, -1, 0)]
            valid_pct = [p for p in pct_5d if p is not None]

            row = {
                "stock_id": sid,
                "outcome_group": outcome[sid],
                "future_return_10d": rec["forward_return_pct"],
                "peer_scope": peer_info["peer_scope"],
                "peer_count": peer_ranks.get(0, {}).get("peer_count"),
                "peer_rank_day0": ranks_5d[4],
                "peer_rank_percentile_day0": pct_5d[4],
                "peer_rank_day_minus_4": ranks_5d[0],
                "peer_rank_day_minus_3": ranks_5d[1],
                "peer_rank_day_minus_2": ranks_5d[2],
                "peer_rank_day_minus_1": ranks_5d[3],
                "peer_top20_days_5d": sum(1 for p in valid_pct if p <= TOP20_PCT_THRESHOLD),
                "peer_rank_median_5d": statistics.median([r for r in ranks_5d if r is not None]) if any(r is not None for r in ranks_5d) else None,
                "peer_rank_direction": classify_direction(ranks_5d),
            }
            rows_out.append(row)
            print(f"{sid} ({outcome[sid]}, ret={rec['forward_return_pct']:.1f}%) scope={peer_info['peer_scope']} "
                  f"ranks(-4..0)={ranks_5d} direction={row['peer_rank_direction']}")
    finally:
        db.close()

    columns = [
        "stock_id", "outcome_group", "future_return_10d",
        "peer_scope", "peer_count",
        "peer_rank_day0", "peer_rank_percentile_day0",
        "peer_rank_day_minus_4", "peer_rank_day_minus_3", "peer_rank_day_minus_2", "peer_rank_day_minus_1",
        "peer_top20_days_5d", "peer_rank_median_5d", "peer_rank_direction",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print(f"\nwrote {len(rows_out)} rows -> {OUT_CSV}")

    with open("/tmp/phase26_relative_leadership_raw.json", "w", encoding="utf-8") as f:
        json.dump(rows_out, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
