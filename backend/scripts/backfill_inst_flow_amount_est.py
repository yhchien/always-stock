"""
回填 FinMind inst_stock_flow 的金額估算欄位。

用法：
  python backend/scripts/backfill_inst_flow_amount_est.py
  python backend/scripts/backfill_inst_flow_amount_est.py --from 2026-04-10
  python backend/scripts/backfill_inst_flow_amount_est.py --dry-run
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal


def build_where_clause(from_date: Optional[str]) -> tuple[str, dict]:
    clauses = [
        "f.source = 'finmind'",
        "("
        "f.buy_amount_est IS NULL OR "
        "f.sell_amount_est IS NULL OR "
        "f.net_amount_est IS NULL"
        ")",
    ]
    params = {}

    if from_date:
        clauses.append("f.trade_date >= :from_date")
        params["from_date"] = from_date

    return " AND ".join(clauses), params


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill inst_stock_flow amount_est from daily_price")
    parser.add_argument("--from", dest="from_date", help="只回填此日期起的資料（YYYY-MM-DD）")
    parser.add_argument("--dry-run", action="store_true", help="只顯示待更新筆數，不寫入 DB")
    args = parser.parse_args()

    where_clause, params = build_where_clause(args.from_date)

    db = SessionLocal()
    try:
        count_sql = text(
            f"""
            SELECT COUNT(*)
            FROM inst_stock_flow f
            WHERE {where_clause}
            """
        )
        target_count = db.execute(count_sql, params).scalar() or 0
        print(f"Target rows: {target_count}")

        if args.dry_run or target_count == 0:
            return 0

        update_sql = text(
            f"""
            UPDATE inst_stock_flow AS f
            SET
                buy_amount_est = COALESCE(f.buy_shares, 0) * COALESCE((
                    SELECT dp.close_price
                    FROM daily_price AS dp
                    WHERE dp.trade_date = f.trade_date
                      AND dp.stock_id = f.stock_id
                ), 0),
                sell_amount_est = COALESCE(f.sell_shares, 0) * COALESCE((
                    SELECT dp.close_price
                    FROM daily_price AS dp
                    WHERE dp.trade_date = f.trade_date
                      AND dp.stock_id = f.stock_id
                ), 0),
                net_amount_est = COALESCE(f.net_shares, 0) * COALESCE((
                    SELECT dp.close_price
                    FROM daily_price AS dp
                    WHERE dp.trade_date = f.trade_date
                      AND dp.stock_id = f.stock_id
                ), 0),
                ingested_at = CURRENT_TIMESTAMP
            WHERE {where_clause}
            """
        )
        result = db.execute(update_sql, params)
        db.commit()
        print(f"Updated rows: {result.rowcount}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
