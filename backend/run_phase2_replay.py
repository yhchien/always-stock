"""
Phase 2 §T：Offline Historical Replay Harness。

對任一歷史交易日，用 DB 裡已經存在的 `daily_price` / `inst_stock_flow` /
`margin_trade` / `monthly_revenue` 資料**重建**候選池（跟 legacy cron 當天實際
跑的邏輯完全相同：candidate_pool → classification → hard/soft filters →
deterministic_signals），然後分別餵給：
    (a) legacy 的 `market_regime.compute_market_regime` + `filters.apply_regime_gate`
    (b) Phase 2 的 `pipeline_v2.run_phase2_pipeline`
兩邊都不呼叫 LLM（純 deterministic 決策層比較），輸出：
    - 兩邊各自的存活數 / 存活名單
    - 指定 regression stock_id 的完整 explain trace（"這檔死在哪一關"）
    - funnel metrics

用法：
    python run_phase2_replay.py 2026-07-20
    python run_phase2_replay.py 2026-07-20 --stocks 2634,8039,2603,2646,1326,2912,2308
    python run_phase2_replay.py 2026-07-20 --persist   # 額外寫入 signal_shadow_snapshots
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from app.database import SessionLocal
from app.signals import candidate_pool, classification, filters, market_breadth, market_regime, momentum
from app.signals import deterministic_signals as det_signals
from app.signals.phase2 import pipeline_v2

DEFAULT_REGRESSION_STOCKS = ["2634", "8039", "2603", "2646", "2615", "6505", "1709", "1326", "2912", "2308"]


def _rebuild_legacy_candidate_pool(db, target_date: date, pool, momentum_frame, ingestion):
    """完全比照 `pipeline.py` legacy 流程：跑 legacy `classify_stocks`
    （三選一硬刪除）→ hard/soft filters → deterministic_signals。
    回傳 legacy 的 `after_soft`。"""
    classified = classification.classify_stocks(db, target_date, pool)
    after_hard = filters.apply_hard_exclusions(db, target_date, classified)
    after_soft = filters.apply_soft_filters(db, target_date, after_hard)
    return det_signals.attach_deterministic_signals(after_soft)


def _rebuild_shared_inputs(db, target_date: date):
    """兩邊（legacy / phase2）共用的候選池建立前段（ingest/rank/momentum_frame/
    build_candidate_pool）與 market regime——這段不是 Phase 2 要改的範圍。"""
    ingestion = candidate_pool.ingest_data(db, target_date)
    rankings = candidate_pool.compute_rankings(db, target_date, ingestion)
    momentum_frame = momentum.compute_market_momentum_frame(
        db, target_date, ingestion.get("stocks_master") or {}
    )
    pool = candidate_pool.build_candidate_pool(
        db, target_date, ingestion, rankings, momentum_frame=momentum_frame
    )
    if not pool:
        raise ValueError(f"no candidate stocks for target_date={target_date}")

    regime_info = market_regime.compute_market_regime(db, target_date)
    breadth = market_breadth.compute_breadth_from_frame(momentum_frame, ingestion.get("stocks_master") or {})
    regime_detail = market_breadth.resolve_regime_detail(regime_info["regime"], breadth.get("breadth_score"))
    regime_info = {**regime_info, "regime_detail": regime_detail, "breadth_score": breadth.get("breadth_score")}

    return ingestion, momentum_frame, pool, regime_info


def _legacy_survivors(after_soft, regime_info):
    return filters.apply_regime_gate(
        after_soft, regime_info["regime"], regime_detail=regime_info.get("regime_detail")
    )


def run_replay(target_date: date, watch_stock_ids: list[str], persist: bool, quiet: bool = False) -> int:
    def _p(*a, **kw):
        if not quiet:
            print(*a, **kw)

    db = SessionLocal()
    try:
        _p(f"=== Phase 2 Replay: {target_date} ===\n")

        ingestion, momentum_frame, pool, regime_info = _rebuild_shared_inputs(db, target_date)
        _p(f"候選池（Candidate Discovery 後，兩邊共用）：{len(pool)} 檔")
        _p(f"Market regime：{regime_info['regime']}（{regime_info.get('reason', '')}）")
        _p(f"Breadth score：{regime_info.get('breadth_score')}\n")

        legacy_after_soft = _rebuild_legacy_candidate_pool(db, target_date, pool, momentum_frame, ingestion)
        _p(f"[Legacy] classify_stocks（三選一）後：{len(legacy_after_soft)} 檔")
        legacy_survivors = _legacy_survivors(legacy_after_soft, regime_info)
        _p(f"[Legacy] regime gate 後存活：{len(legacy_survivors)} 檔")
        if legacy_survivors:
            _p("  ", [c.get("stock_id") for c in legacy_survivors])

        phase2_pool = pipeline_v2.build_phase2_pool(pool)
        _p(f"\n[Phase 2] 定義性 hard exclusion 後（無 classify_stocks 硬刪除）：{len(phase2_pool)} 檔")

        result = pipeline_v2.run_phase2_pipeline(db, phase2_pool, regime_info["regime"])
        survivors = result["survivors"]
        _p(f"[Phase 2] regime gate 後存活：{len(survivors)} 檔")
        if survivors:
            for c in survivors:
                _p(f"   {c['stock_id']}: role={c.get('role')} conviction={c.get('conviction')}")

        _p("\n=== Funnel Metrics ===")
        _p(json.dumps(result["funnel_metrics"], ensure_ascii=False, indent=2))

        if quiet:
            print(
                f"{target_date} | regime={regime_info['regime']} | pool={len(pool)} "
                f"| legacy_survivors={len(legacy_survivors)} | phase2_survivors={len(survivors)}"
            )

        raw_pool_ids = {c["stock_id"] for c in pool}
        phase2_pool_ids = {c["stock_id"] for c in phase2_pool}
        legacy_survivor_ids = {c.get("stock_id") for c in legacy_survivors}
        if not quiet:
            print("\n=== 指定股票 Explain Trace ===")
            for sid in watch_stock_ids:
                if sid not in raw_pool_ids:
                    print(f"\n--- {sid}：連 Candidate Discovery 都沒進來（build_candidate_pool 未收錄）---")
                    continue
                if sid not in phase2_pool_ids:
                    print(f"\n--- {sid}：Candidate Discovery 有進來，但被 Phase 2 定義性 hard exclusion 剔除 ---")
                    continue
                trace = result["explain_traces"].get(sid)
                print(f"\n--- {sid} ---")
                print(json.dumps(trace, ensure_ascii=False, indent=2, default=str))

        if persist:
            from app.models import SignalShadowSnapshot

            row = (
                db.query(SignalShadowSnapshot)
                .filter(
                    SignalShadowSnapshot.snapshot_date == target_date,
                    SignalShadowSnapshot.pipeline_version == pipeline_v2.PIPELINE_VERSION,
                )
                .first()
            )
            if row is None:
                row = SignalShadowSnapshot(snapshot_date=target_date, pipeline_version=pipeline_v2.PIPELINE_VERSION)
                db.add(row)
            row.funnel_metrics = result["funnel_metrics"]
            row.explain_traces = {k: v for k, v in result["explain_traces"].items()}
            row.candidate_pool_size = len(phase2_pool)
            row.role_survivor_count = sum(1 for c in phase2_pool if c.get("stock_id") in result["explain_traces"])
            row.regime_survivor_count = len(survivors)
            row.comparison_summary = {
                "legacy_survivor_count": len(legacy_survivors),
                "legacy_survivor_ids": [c.get("stock_id") for c in legacy_survivors],
                "phase2_survivor_count": len(survivors),
                "phase2_survivor_ids": [c.get("stock_id") for c in survivors],
            }
            db.commit()
            _p(f"\n已寫入 signal_shadow_snapshots（snapshot_date={target_date}）")

        return 0
    except ValueError as e:
        print(f"ERROR ({target_date}): {e}")
        return 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target_date", type=str, help="YYYY-MM-DD")
    parser.add_argument("--stocks", type=str, default=",".join(DEFAULT_REGRESSION_STOCKS))
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="批次跑多天時降低輸出量，只印一行摘要")
    args = parser.parse_args()

    target_date = datetime.strptime(args.target_date, "%Y-%m-%d").date()
    watch_stock_ids = [s.strip() for s in args.stocks.split(",") if s.strip()]
    return run_replay(target_date, watch_stock_ids, args.persist, quiet=args.quiet)


if __name__ == "__main__":
    sys.exit(main())
