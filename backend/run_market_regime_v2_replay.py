"""
M27 Market Regime v2 — point-in-time replay（§二十三）。

用真實 DB point-in-time 資料（不用未來 outcome 反推）重跑指定日期範圍的
trend_regime / market_stress / effective_market_state，並列出每個 Family
的狀態、reason codes、關鍵 raw evidence，方便對照舊版 trend-only regime
判斷有沒有意義的落差。

用法：
    python3 run_market_regime_v2_replay.py 2026-09-02 2026-09-04
    python3 run_market_regime_v2_replay.py 2026-09-04  # 單日
"""

import json
import sys
from datetime import date, timedelta

from app.database import SessionLocal
from app.signals import market_breadth, market_regime, market_stress, momentum
from app.signals.candidate_pool import ingest_data


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def replay_one_day(db, target_date: date) -> dict:
    old_regime_info = market_regime.compute_market_regime(db, target_date)
    ingestion = ingest_data(db, target_date)
    frame = momentum.compute_market_momentum_frame(
        db, target_date, ingestion.get("stocks_master") or {}
    )
    breadth = market_breadth.compute_breadth_from_frame(
        frame, ingestion.get("stocks_master") or {}
    )
    taiex_ret_1d = (old_regime_info.get("metrics") or {}).get("return_1d_pct")

    result = market_stress.compute_market_stress(
        db,
        target_date,
        trend_regime=old_regime_info["regime"],
        momentum_frame=frame,
        breadth=breadth,
        taiex_return_1d_pct=taiex_ret_1d,
    )
    return {
        "date": target_date.isoformat(),
        "old_market_regime": old_regime_info["regime"],
        "old_market_regime_reason": old_regime_info["reason"],
        "trend_regime": result["trend_regime"],
        "market_stress": result["market_stress"],
        "market_stress_reason": result["market_stress_reason"],
        "effective_market_state": result["effective_market_state"],
        "stress_families": result["stress_families"],
        "key_reason_codes": result["key_reason_codes"],
        "market_stress_data_complete": result["market_stress_data_complete"],
        "family_detail": {
            name: {
                "status": fs["status"],
                "reason_codes": fs["reason_codes"],
                "raw_values": fs["raw_values"],
                "data_available_count": fs["data_available_count"],
                "data_expected_count": fs["data_expected_count"],
            }
            for name, fs in result["stress_family_detail"].items()
        },
        "version": result["market_regime_v2_version"],
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 run_market_regime_v2_replay.py START [END]")
        return 1
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else start

    db = SessionLocal()
    try:
        out = []
        for d in _daterange(start, end):
            print(f"Replaying {d}...", file=sys.stderr)
            try:
                out.append(replay_one_day(db, d))
            except Exception as exc:  # noqa: BLE001
                out.append({"date": d.isoformat(), "error": str(exc)})
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
