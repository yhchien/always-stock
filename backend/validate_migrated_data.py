"""
Validate migrated always-stock data between a source SQLite database and a target database.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

from dotenv import load_dotenv
from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv(Path(__file__).resolve().with_name(".env"))

from app.models import DailyPrice, IndustryDailyFlow, InstStockFlow  # noqa: E402
from migrate_sqlite_to_postgres import (  # noqa: E402
    TABLE_SPECS,
    TableSpec,
    build_engine,
    count_rows,
    default_sqlite_path,
    resolve_table_specs,
    sqlite_url_from_path,
)

KNOWN_MISSING_FLOW_DATES = ("2019-04-04", "2023-04-03", "2026-02-18")
KNOWN_MISSING_OHLC_DATES = (
    "2023-05-05",
    "2023-09-19",
    "2024-01-17",
    "2024-02-29",
    "2024-07-11",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def min_max_trade_date(engine, spec: TableSpec) -> Dict[str, Optional[str]]:
    table = spec.model.__table__
    if "trade_date" not in table.c:
        return {"min_trade_date": None, "max_trade_date": None}

    with engine.connect() as conn:
        row = conn.execute(
            select(func.min(table.c.trade_date), func.max(table.c.trade_date))
        ).one()
    return {
        "min_trade_date": row[0].isoformat() if row[0] else None,
        "max_trade_date": row[1].isoformat() if row[1] else None,
    }


def count_distinct_trade_dates(engine, model) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count(func.distinct(model.trade_date)))).scalar_one()


def build_table_report(source_engine, target_engine, spec: TableSpec) -> dict:
    source_count = count_rows(source_engine, spec)
    target_count = count_rows(target_engine, spec)
    source_dates = min_max_trade_date(source_engine, spec)
    target_dates = min_max_trade_date(target_engine, spec)
    return {
        "source_count": source_count,
        "target_count": target_count,
        "count_match": source_count == target_count,
        "source_dates": source_dates,
        "target_dates": target_dates,
        "date_range_match": source_dates == target_dates,
    }


def build_known_gap_report(source_engine, target_engine) -> dict:
    flow_gap_status = {}
    for date_str in KNOWN_MISSING_FLOW_DATES:
        flow_gap_status[date_str] = {
            "source_inst_stock_flow_rows": count_rows_for_date(source_engine, InstStockFlow, date_str),
            "target_inst_stock_flow_rows": count_rows_for_date(target_engine, InstStockFlow, date_str),
            "source_industry_daily_flow_rows": count_rows_for_date(
                source_engine, IndustryDailyFlow, date_str
            ),
            "target_industry_daily_flow_rows": count_rows_for_date(
                target_engine, IndustryDailyFlow, date_str
            ),
        }

    ohlc_gap_status = {}
    for date_str in KNOWN_MISSING_OHLC_DATES:
        ohlc_gap_status[date_str] = {
            "source_missing_ohlc_rows": count_missing_ohlc_rows(source_engine, date_str),
            "target_missing_ohlc_rows": count_missing_ohlc_rows(target_engine, date_str),
        }

    return {
        "known_missing_flow_dates": flow_gap_status,
        "known_missing_ohlc_dates": ohlc_gap_status,
    }


def count_rows_for_date(engine, model, date_str: str) -> int:
    target_date = date.fromisoformat(date_str)
    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(model.__table__).where(model.trade_date == target_date)
        ).scalar_one()


def count_missing_ohlc_rows(engine, date_str: str) -> int:
    target_date = date.fromisoformat(date_str)
    with engine.connect() as conn:
        return conn.execute(
            select(func.count())
            .select_from(DailyPrice.__table__)
            .where(DailyPrice.trade_date == target_date)
            .where(DailyPrice.open_price.is_(None))
            .where(DailyPrice.high_price.is_(None))
            .where(DailyPrice.low_price.is_(None))
        ).scalar_one()


def has_mismatch(table_reports: Dict[str, dict]) -> bool:
    return any(
        (not report["count_match"]) or (not report["date_range_match"])
        for report in table_reports.values()
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate migrated always-stock data")
    parser.add_argument(
        "--source-sqlite-path",
        default=str(default_sqlite_path()),
        help="Path to source SQLite database file",
    )
    parser.add_argument(
        "--target-database-url",
        default=os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Target database URL",
    )
    parser.add_argument("--tables", nargs="*", default=[], help="Optional subset of tables to validate")
    parser.add_argument(
        "--report-json",
        default="logs/sqlite_to_postgres_validation_report.json",
        help="Write validation report to this JSON file",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.target_database_url:
        print("target database url is required", file=sys.stderr)
        return 2

    source_path = Path(args.source_sqlite_path)
    if not source_path.exists():
        print(f"source sqlite db not found: {source_path}", file=sys.stderr)
        return 2

    specs = resolve_table_specs(args.tables)
    source_engine = build_engine(sqlite_url_from_path(source_path))
    target_engine = build_engine(args.target_database_url)

    report = {
        "generated_at": utc_now_iso(),
        "source_sqlite_path": str(source_path),
        "target_database_url": args.target_database_url,
        "tables": {},
        "known_gaps": build_known_gap_report(source_engine, target_engine),
    }

    for spec in specs:
        report["tables"][spec.name] = build_table_report(source_engine, target_engine, spec)

    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=True, indent=2, sort_keys=True)

    for table_name, table_report in report["tables"].items():
        count_status = "OK" if table_report["count_match"] else "MISMATCH"
        date_status = "OK" if table_report["date_range_match"] else "MISMATCH"
        print(
            f"[validate] {table_name}: count={count_status} "
            f"source={table_report['source_count']} target={table_report['target_count']} "
            f"date_range={date_status}"
        )

    print(f"[validate] report={report_path}")
    return 1 if has_mismatch(report["tables"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
