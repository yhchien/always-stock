"""Phase 2.5 deterministic replay（2026-07-23）：Momentum Freshness + Final Watch
Quality Layer 的 60~120 交易日回測，**完全不呼叫 OpenAI**（Freshness/Quality 是
deterministic 層，跟 LLM 無關），只比較「Phase2 現行（regime gate 存活者全數送 LLM）」
vs「Phase2.5（只有 READY+SETUP 送 LLM，RESERVE 保留但不送）」兩個候選集合的：

    - WATCH count（用 survivors / READY+SETUP 近似）
    - positive rate / mean return / median return（10 個交易日遠期報酬，evaluation-only，
      不進 feature，符合 spec §46/§52「不可用未來資料當特徵，只能用於事後評估」）
    - left-tail loss rate（<= -10%）
    - top winner retention（6505/8039/6414/1810 regression）

方法論與 `run_v6_llm_validation.py` / `run_phase2_replay.py` 一致：deterministic 重建
候選池（`candidate_pool` + `momentum` + `market_regime` + `pipeline_v2`），**只寫本機
scratch 檔案，完全不寫入任何 production 表**。

用法：
    python run_phase25_replay_analysis.py --days 90 --out /tmp/phase25_replay.json
    python run_phase25_replay_analysis.py --days 5 --out /tmp/phase25_replay_smoke.json  # 快速驗證用
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.hot_money_service import get_recent_trade_dates
from app.models import DailyPrice
from app.signals import candidate_pool, market_breadth, market_regime, momentum
from app.signals.phase2 import pipeline_v2

FORWARD_TRADE_DAYS = 10  # 遠期評估窗（交易日），evaluation-only，非 feature
WINNER_REGRESSION_STOCKS = ("6505", "8039", "6414", "1810")


def _rebuild_shared_inputs(db, target_date: date):
    ingestion = candidate_pool.ingest_data(db, target_date)
    if not ingestion.get("stocks_master"):
        return None, None
    rankings = candidate_pool.compute_rankings(db, target_date, ingestion)
    momentum_frame = momentum.compute_market_momentum_frame(
        db, target_date, ingestion.get("stocks_master") or {}
    )
    pool = candidate_pool.build_candidate_pool(
        db, target_date, ingestion, rankings, momentum_frame=momentum_frame
    )
    if not pool:
        return None, None

    regime_info = market_regime.compute_market_regime(db, target_date)
    breadth = market_breadth.compute_breadth_from_frame(momentum_frame, ingestion.get("stocks_master") or {})
    regime_detail = market_breadth.resolve_regime_detail(regime_info["regime"], breadth.get("breadth_score"))
    regime_info = {**regime_info, "regime_detail": regime_detail, "breadth_score": breadth.get("breadth_score")}
    return pool, regime_info


def _run_one_day(db, target_date: date) -> Optional[Dict[str, Any]]:
    pool, regime_info = _rebuild_shared_inputs(db, target_date)
    if pool is None:
        return None

    taiex_return_1d_pct = (regime_info.get("metrics") or {}).get("return_1d_pct")
    hard_excluded: list = []
    phase2_pool = pipeline_v2.build_phase2_pool(
        pool, taiex_return_1d_pct=taiex_return_1d_pct, excluded_out=hard_excluded
    )
    if not phase2_pool and not hard_excluded:
        return None

    result = pipeline_v2.run_phase2_pipeline(
        db, phase2_pool, regime_info["regime"],
        hard_excluded=hard_excluded, taiex_return_1d_pct=taiex_return_1d_pct,
    )

    survivors = result["survivors"]
    # 2026-07-23 修正：腳本強制 `WATCH_QUALITY_MODE="shadow"` 讓 pipeline 照算
    # freshness/quality 但**不過濾** `llm_eligible`（shadow 模式定義如此——這是
    # production 上線前故意保留全部候選的安全做法）。若這裡直接拿
    # `result["llm_eligible"]` 當「品質過濾後的名單」，在 shadow 模式下會恆等於
    # `survivors`（因為 shadow 模式本來就不過濾），導致「Phase2.5 品質過濾」cohort
    # 與「Phase2 現行」cohort 表面上一模一樣——不是真的沒有差異，而是量錯了東西。
    # 正確做法：直接用 `watch_quality_state` 欄位本身判斷「若切 production 這檔
    # 會不會被送進 LLM」，不依賴 mode-dependent 的 `llm_eligible` 名單。
    ready_setup_ids = {
        c["stock_id"] for c in survivors
        if c.get("watch_quality_state") in ("READY", "SETUP")
    }

    records = []
    for c in survivors:
        records.append({
            "stock_id": c["stock_id"],
            "name": c.get("name"),
            "industry": c.get("industry"),
            "role": c.get("role"),
            "tracking_state": c.get("tracking_state"),
            "entry_state": c.get("entry_state"),
            "conviction": c.get("conviction"),
            "momentum_score": c.get("momentum_score"),
            "rs_market_percentile_20d": c.get("rs_market_percentile_20d"),
            "momentum_freshness": c.get("momentum_freshness"),
            "watch_quality_state": c.get("watch_quality_state"),
            "watch_quality_score": c.get("watch_quality_score"),
            "quality_evidence": c.get("quality_evidence"),
            "quality_reasons": c.get("quality_reasons"),
            "risk_warnings": c.get("risk_warnings"),
            "in_price_momentum_pool": bool(c.get("in_price_momentum_pool")),
            "in_acceleration_pool": bool(c.get("in_acceleration_pool")),
            "in_fundamental_pool": bool(c.get("in_fundamental_pool")),
            "in_top_stocks_3d": bool(c.get("in_top_stocks_3d")),
            "close_1d": c.get("close_1d"),
            "is_quality_llm_eligible": c["stock_id"] in ready_setup_ids,
        })

    return {
        "catch_date": target_date.isoformat(),
        "regime": regime_info["regime"],
        "raw_pool_size": len(pool),
        "phase2_survivor_count": len(survivors),
        "llm_eligible_count": len(ready_setup_ids),
        "funnel_metrics": result["funnel_metrics"],
        "survivors": records,
    }


def _load_forward_closes(db, stock_ids: List[str], dates: List[date]) -> Dict[Any, float]:
    if not stock_ids or not dates:
        return {}
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.trade_date, DailyPrice.close_price)
        .filter(DailyPrice.stock_id.in_(stock_ids), DailyPrice.trade_date.in_(dates))
        .all()
    )
    return {(sid, td): float(close) for sid, td, close in rows if close is not None}


def run_replay(days: int, out_path: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        end_date = date(2026, 7, 22)
        all_days = get_recent_trade_dates(db, end_date, days + FORWARD_TRADE_DAYS + 5)
        if len(all_days) <= FORWARD_TRADE_DAYS:
            raise ValueError("not enough trading days available for replay")

        catch_eligible = all_days[: -FORWARD_TRADE_DAYS]
        catch_days = catch_eligible[-days:]
        index_of = {d: i for i, d in enumerate(all_days)}

        print(f"replay window: {catch_days[0]} ~ {catch_days[-1]} ({len(catch_days)} trading days), "
              f"forward={FORWARD_TRADE_DAYS} trading days, end_date={end_date}")

        daily_results: List[Dict[str, Any]] = []
        for i, d in enumerate(catch_days):
            r = _run_one_day(db, d)
            if r is not None:
                daily_results.append(r)
            if (i + 1) % 10 == 0 or i == len(catch_days) - 1:
                print(f"  progress {i + 1}/{len(catch_days)} days processed "
                      f"({len(daily_results)} produced non-empty pools)")

        # ---- 遠期報酬（evaluation-only）----
        all_stock_ids = sorted({rec["stock_id"] for day in daily_results for rec in day["survivors"]})
        forward_dates_needed = sorted({
            all_days[index_of[date.fromisoformat(day["catch_date"])] + FORWARD_TRADE_DAYS]
            for day in daily_results
        })
        forward_close_by_id_date = _load_forward_closes(db, all_stock_ids, forward_dates_needed)

        # 攤平成 (stock_id, catch_date) -> return_pct，並標記是否 phase2-current / phase2.5-quality
        flat_records: List[Dict[str, Any]] = []
        for day in daily_results:
            catch_date_obj = date.fromisoformat(day["catch_date"])
            fwd_date = all_days[index_of[catch_date_obj] + FORWARD_TRADE_DAYS]
            for rec in day["survivors"]:
                catch_close = rec.get("close_1d")
                fwd_close = forward_close_by_id_date.get((rec["stock_id"], fwd_date))
                ret = None
                if catch_close and fwd_close and catch_close > 0:
                    ret = (fwd_close / catch_close - 1.0) * 100.0
                flat_records.append({
                    **rec,
                    "catch_date": day["catch_date"],
                    "forward_date": fwd_date.isoformat(),
                    "forward_return_pct": ret,
                    "regime": day["regime"],
                })

        # ---- Dedup 規則：同檔股票多天出現，以「第一次出現」為準（跟使用者先前 52 檔分析一致）----
        first_seen: Dict[str, Dict[str, Any]] = {}
        for rec in sorted(flat_records, key=lambda r: r["catch_date"]):
            first_seen.setdefault(rec["stock_id"], rec)
        current_cohort = list(first_seen.values())  # Phase2 現行：regime gate 存活者全體
        quality_cohort = [r for r in current_cohort if r["is_quality_llm_eligible"]]  # Phase2.5：READY+SETUP

        def _cohort_stats(cohort: List[Dict[str, Any]]) -> Dict[str, Any]:
            rets = [r["forward_return_pct"] for r in cohort if r["forward_return_pct"] is not None]
            if not rets:
                return {"count": len(cohort), "n_with_return": 0}
            return {
                "count": len(cohort),
                "n_with_return": len(rets),
                "positive_rate_pct": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
                "mean_return_pct": round(statistics.mean(rets), 2),
                "median_return_pct": round(statistics.median(rets), 2),
                "left_tail_loss_rate_pct": round(100.0 * sum(1 for r in rets if r <= -10.0) / len(rets), 1),
                "gain_ge_10_rate_pct": round(100.0 * sum(1 for r in rets if r >= 10.0) / len(rets), 1),
            }

        current_stats = _cohort_stats(current_cohort)
        quality_stats = _cohort_stats(quality_cohort)

        # ---- Loss cohort（<=-10%）分析：Phase2 現行 cohort 裡的大虧股，quality 層是否已標記 ----
        loss_cohort = [r for r in current_cohort if r["forward_return_pct"] is not None and r["forward_return_pct"] <= -10.0]
        loss_breakdown = {
            "count": len(loss_cohort),
            "role_counts": dict(Counter(r.get("role") or "NONE" for r in loss_cohort)),
            "freshness_counts": dict(Counter(r.get("momentum_freshness") or "NONE" for r in loss_cohort)),
            "watch_quality_counts": dict(Counter(r.get("watch_quality_state") or "NONE" for r in loss_cohort)),
            "would_be_excluded_by_quality_layer": sum(1 for r in loss_cohort if not r["is_quality_llm_eligible"]),
            "would_still_be_included_by_quality_layer": sum(1 for r in loss_cohort if r["is_quality_llm_eligible"]),
            "risk_warning_counts": dict(Counter(
                w for r in loss_cohort for w in (r.get("risk_warnings") or [])
            )),
        }

        # ---- Winner retention regression（6505/8039/6414/1810）----
        winner_retention = {}
        for sid in WINNER_REGRESSION_STOCKS:
            appearances = [r for r in flat_records if r["stock_id"] == sid]
            if not appearances:
                winner_retention[sid] = {"appeared": False}
                continue
            reserve_days = [r for r in appearances if r.get("watch_quality_state") == "RESERVE"]
            winner_retention[sid] = {
                "appeared": True,
                "appearance_count": len(appearances),
                "watch_quality_states": dict(Counter(r.get("watch_quality_state") or "NONE" for r in appearances)),
                "ever_pushed_to_reserve": len(reserve_days) > 0,
                "reserve_day_count": len(reserve_days),
                "reserve_days": [r["catch_date"] for r in reserve_days],
            }

        # ---- Cohort report by caught_date（半月分桶，避免 90 個獨立小 cohort 太瑣碎）----
        by_catch_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for r in current_cohort:
            by_catch_date[r["catch_date"]].append(r)
        cohort_report = []
        for cd in sorted(by_catch_date.keys()):
            rets = [r["forward_return_pct"] for r in by_catch_date[cd] if r["forward_return_pct"] is not None]
            if not rets:
                continue
            cohort_report.append({
                "caught_date": cd,
                "count": len(by_catch_date[cd]),
                "positive_rate_pct": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
                "mean_return_pct": round(statistics.mean(rets), 2),
                "median_return_pct": round(statistics.median(rets), 2),
                "ge_5_pct_rate": round(100.0 * sum(1 for r in rets if r >= 5.0) / len(rets), 1),
                "ge_10_pct_rate": round(100.0 * sum(1 for r in rets if r >= 10.0) / len(rets), 1),
                "le_neg5_pct_rate": round(100.0 * sum(1 for r in rets if r <= -5.0) / len(rets), 1),
                "le_neg10_pct_rate": round(100.0 * sum(1 for r in rets if r <= -10.0) / len(rets), 1),
            })

        summary = {
            "replay_window": {"start": catch_days[0].isoformat(), "end": catch_days[-1].isoformat(),
                               "trading_days": len(catch_days), "forward_trade_days": FORWARD_TRADE_DAYS,
                               "days_with_pool": len(daily_results)},
            "current_cohort_stats": current_stats,
            "quality_cohort_stats": quality_stats,
            "loss_cohort_analysis": loss_breakdown,
            "winner_retention_regression": winner_retention,
            "cohort_report_by_caught_date": cohort_report,
        }

        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {"summary": summary, "daily_results": daily_results, "flat_records": flat_records},
                f, ensure_ascii=False, indent=2, default=str,
            )
        print(f"已寫入 {out_path}（不影響任何 production 表）")
        return summary
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--out", type=str, default="/tmp/phase25_replay.json")
    args = parser.parse_args()
    pipeline_v2.WATCH_QUALITY_MODE = "shadow"  # 強制計算 freshness/quality，但不影響 survivors 內容
    run_replay(args.days, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
