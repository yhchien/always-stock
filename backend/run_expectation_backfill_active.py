"""M26 backfill：對所有 active signal_watch_hits 補跑 expectation price。

行為：
  1. 列出 distinct (stock_id, first_seen_date)（每檔的最早 snapshot）
  2. 過濾掉 SignalExpectationPrice 已有 (stock_id, first_detected_date) status='ok' 的
  3. 逐檔呼叫 generate_for_stock(first_detected_date=first_seen_date, source="manual")
  4. 累計 ok / failed / total_tokens；最後印總結與估算 USD

用法：
    python run_expectation_backfill_active.py            # 跑全部缺漏的 active 股票
    python run_expectation_backfill_active.py --limit 5  # dry-run 前 5 檔
    python run_expectation_backfill_active.py --dry-run  # 只印計畫不執行

Rate limit：直接呼叫 generate_for_stock 不過 router，**不受 daily quota 限制**。

Exit code:
    0 = ok（全部成功 或 已無缺漏）
    1 = partial（部分失敗）
    2 = all failed
    3 = error（DB 連線 / 模組 import 失敗）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time as time_mod
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_ERROR = 3

# gpt-5.4 / gpt-4o 級暫用旗艦 pricing；gpt-mini 級乘 0.2 即可粗估
# 僅用於最後總結時估算 USD，不是精確帳單
_PRICE_INPUT_USD_PER_1M = 1.25
_PRICE_OUTPUT_USD_PER_1M = 10.0


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="M26 expectation backfill for active stocks")
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 檔 (dry-run 用)")
    p.add_argument("--dry-run", action="store_true", help="只印計畫不執行")
    return p.parse_args(argv[1:])


def _list_active_targets(db) -> List[Tuple[str, "datetime.date"]]:
    """回 [(stock_id, first_seen_date), ...]，已過濾掉已有 status='ok' 的。"""
    from sqlalchemy import func

    from app.models import SignalExpectationPrice, SignalWatchHit

    sub = (
        db.query(
            SignalWatchHit.stock_id,
            func.min(SignalWatchHit.snapshot_date).label("first_seen_date"),
        )
        .group_by(SignalWatchHit.stock_id)
        .subquery()
    )
    existing = {
        (sid, fdate)
        for sid, fdate in (
            db.query(
                SignalExpectationPrice.stock_id,
                SignalExpectationPrice.first_detected_date,
            )
            .filter(SignalExpectationPrice.status == "ok")
            .all()
        )
    }
    rows = db.query(sub.c.stock_id, sub.c.first_seen_date).all()
    return [(sid, fdate) for sid, fdate in rows if (sid, fdate) not in existing]


def main(argv: List[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _parse_args(argv)

    try:
        from app.database import SessionLocal
        from app.signals import expectation_price as svc
    except Exception:
        logger.exception("Failed to import modules")
        return EXIT_ERROR

    try:
        with SessionLocal() as db:
            targets = _list_active_targets(db)
    except Exception:
        logger.exception("Failed to list active targets")
        return EXIT_ERROR

    if args.limit is not None:
        targets = targets[: args.limit]

    total = len(targets)
    logger.info("Active 缺漏股票數: %d", total)
    if total == 0:
        logger.info("Nothing to do.")
        return EXIT_OK

    if args.dry_run:
        for i, (sid, fdate) in enumerate(targets[:10], 1):
            logger.info("[plan %d/%d] %s first_seen=%s", i, total, sid, fdate)
        if total > 10:
            logger.info("... (還有 %d 檔)", total - 10)
        return EXIT_OK

    ok = 0
    failed: List[str] = []
    total_tokens = 0
    start_ts = time_mod.time()
    model_seen = None

    for i, (sid, fdate) in enumerate(targets, 1):
        try:
            with SessionLocal() as db:
                row = svc.generate_for_stock(
                    db,
                    sid,
                    first_detected_date=fdate,
                    source="manual",
                )
            model_seen = row.llm_model or model_seen
            tokens = 0
            if row.llm_diagnostic:
                tokens = int(row.llm_diagnostic.get("total_tokens") or 0)
            total_tokens += tokens

            elapsed = time_mod.time() - start_ts
            rate = i / max(elapsed, 1e-6)
            eta_sec = (total - i) / max(rate, 1e-6)
            logger.info(
                "[%d/%d] %s first_seen=%s status=%s tokens=%s (avg_tokens=%d, eta=%.0fs)",
                i, total, sid, fdate, row.status, tokens,
                total_tokens // max(i, 1),
                eta_sec,
            )
            if row.status == "ok":
                ok += 1
            else:
                failed.append(sid)
        except Exception:
            logger.exception("generate_for_stock failed for %s", sid)
            failed.append(sid)

    elapsed = time_mod.time() - start_ts
    # 粗估 USD：假設 input/output 約 6:4 split
    avg_tokens = total_tokens / max(total, 1)
    est_input_tokens = total_tokens * 0.6
    est_output_tokens = total_tokens * 0.4
    est_usd = (
        est_input_tokens * _PRICE_INPUT_USD_PER_1M / 1_000_000
        + est_output_tokens * _PRICE_OUTPUT_USD_PER_1M / 1_000_000
    )

    logger.info("=" * 60)
    logger.info("Backfill done in %.1f min", elapsed / 60)
    logger.info("Model: %s", model_seen or "(unknown)")
    logger.info("Total: %d, ok: %d, failed: %d", total, ok, len(failed))
    logger.info("Total tokens: %d (avg %.0f / stock)", total_tokens, avg_tokens)
    logger.info(
        "粗估 USD (flagship 級 input=$%.2f/1M, output=$%.2f/1M, 未計 cache): $%.3f (~ NT$%d)",
        _PRICE_INPUT_USD_PER_1M, _PRICE_OUTPUT_USD_PER_1M,
        est_usd, int(est_usd * 32),
    )
    if failed:
        logger.info("Failed stock_ids: %s", ", ".join(failed[:20]) + ("..." if len(failed) > 20 else ""))

    if ok == total:
        return EXIT_OK
    if ok == 0:
        return EXIT_ALL_FAILED
    return EXIT_PARTIAL


if __name__ == "__main__":
    sys.exit(main(sys.argv))
