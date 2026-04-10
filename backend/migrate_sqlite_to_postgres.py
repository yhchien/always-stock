"""
Batch import tool: copy always-stock data from SQLite into a target database.

Typical usage:

    python migrate_sqlite_to_postgres.py \
        --target-database-url postgresql+psycopg://user:pass@host/db
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeMeta

sys.path.insert(0, os.path.dirname(__file__))

from app.models import (  # noqa: E402
    Base,
    BrokerTrade,
    DailyPrice,
    IndustryDailyFlow,
    InstStockFlow,
    StockMaster,
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    model: DeclarativeMeta
    conflict_columns: Sequence[str]
    exclude_columns: Sequence[str] = ()


TABLE_SPECS: Sequence[TableSpec] = (
    TableSpec("stocks_master", StockMaster, ("stock_id",)),
    TableSpec("daily_price", DailyPrice, ("trade_date", "stock_id"), exclude_columns=("id",)),
    TableSpec(
        "inst_stock_flow",
        InstStockFlow,
        ("trade_date", "stock_id", "inst_type"),
        exclude_columns=("id",),
    ),
    TableSpec(
        "industry_daily_flow",
        IndustryDailyFlow,
        ("trade_date", "industry_name"),
        exclude_columns=("id",),
    ),
    TableSpec(
        "broker_trade",
        BrokerTrade,
        ("trade_date", "stock_id", "broker_id"),
        exclude_columns=("id",),
    ),
)

TABLE_NAME_TO_SPEC = {spec.name: spec for spec in TABLE_SPECS}


def default_sqlite_path() -> Path:
    return Path(os.getenv("DB_PATH", Path(__file__).parent / "db" / "tw_stock.db")).resolve()


def sqlite_url_from_path(path: Path) -> str:
    return f"sqlite:///{path.resolve()}"


def build_engine(database_url: str) -> Engine:
    kwargs = {}
    if database_url.startswith("sqlite:///"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(database_url, **kwargs)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_table_specs(selected_tables: Sequence[str]) -> List[TableSpec]:
    if not selected_tables:
        return list(TABLE_SPECS)

    specs = []
    for table_name in selected_tables:
        if table_name not in TABLE_NAME_TO_SPEC:
            raise ValueError(f"Unknown table: {table_name}")
        specs.append(TABLE_NAME_TO_SPEC[table_name])
    return specs


def get_import_columns(spec: TableSpec) -> List[str]:
    return [
        column.name
        for column in spec.model.__table__.columns
        if column.name not in spec.exclude_columns
    ]


def load_checkpoint(checkpoint_path: Path) -> dict:
    if not checkpoint_path.exists():
        return {"completed_tables": [], "table_reports": {}}

    with checkpoint_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_checkpoint(checkpoint_path: Path, checkpoint: dict) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("w", encoding="utf-8") as fh:
        json.dump(checkpoint, fh, ensure_ascii=True, indent=2, sort_keys=True)


def truncate_target_tables(target_engine: Engine, specs: Sequence[TableSpec]) -> None:
    with target_engine.begin() as conn:
        for spec in reversed(specs):
            conn.execute(spec.model.__table__.delete())


def reset_postgres_sequences(target_engine: Engine, specs: Sequence[TableSpec]) -> None:
    if target_engine.dialect.name != "postgresql":
        return

    with target_engine.begin() as conn:
        for spec in specs:
            table = spec.model.__table__
            id_column = table.c.get("id")
            if id_column is None:
                continue

            conn.exec_driver_sql(
                """
                SELECT setval(
                    pg_get_serial_sequence(:table_name, 'id'),
                    COALESCE((SELECT MAX(id) FROM {}), 1),
                    COALESCE((SELECT MAX(id) FROM {}) IS NOT NULL, false)
                )
                """.format(table.name, table.name),
                {"table_name": table.name},
            )


def iter_source_rows(source_engine: Engine, spec: TableSpec, batch_size: int) -> Iterable[List[dict]]:
    table = spec.model.__table__
    columns = get_import_columns(spec)
    statement = select(*(table.c[column] for column in columns))

    with source_engine.connect() as conn:
        result = conn.execution_options(stream_results=True).execute(statement)
        while True:
            chunk = result.fetchmany(batch_size)
            if not chunk:
                break
            yield [dict(row._mapping) for row in chunk]


def build_upsert_statement(spec: TableSpec, rows: List[dict], target_dialect: str):
    table = spec.model.__table__
    if target_dialect == "postgresql":
        insert_stmt = pg_insert(table).values(rows)
    elif target_dialect == "sqlite":
        insert_stmt = sqlite_insert(table).values(rows)
    else:
        raise ValueError(f"Unsupported target dialect for upsert: {target_dialect}")

    update_columns = {
        column: getattr(insert_stmt.excluded, column)
        for column in rows[0].keys()
        if column not in spec.conflict_columns
    }
    return insert_stmt.on_conflict_do_update(
        index_elements=list(spec.conflict_columns),
        set_=update_columns,
    )


def count_rows(engine: Engine, spec: TableSpec) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(spec.model.__table__)).scalar_one()


def import_table(
    source_engine: Engine,
    target_engine: Engine,
    spec: TableSpec,
    batch_size: int,
    dry_run: bool = False,
) -> int:
    imported_rows = 0

    for rows in iter_source_rows(source_engine, spec, batch_size):
        imported_rows += len(rows)
        if dry_run:
            continue

        statement = build_upsert_statement(spec, rows, target_engine.dialect.name)
        with target_engine.begin() as conn:
            conn.execute(statement)

    return imported_rows


def verify_row_counts(source_engine: Engine, target_engine: Engine, specs: Sequence[TableSpec]) -> None:
    for spec in specs:
        source_count = count_rows(source_engine, spec)
        target_count = count_rows(target_engine, spec)
        status = "OK" if source_count == target_count else "MISMATCH"
        print(f"[verify] {spec.name}: source={source_count} target={target_count} status={status}")


def build_table_report(
    source_engine: Engine,
    target_engine: Engine,
    spec: TableSpec,
    processed_rows: int,
) -> Dict[str, int]:
    source_count = count_rows(source_engine, spec)
    target_count = count_rows(target_engine, spec)
    return {
        "processed_rows": processed_rows,
        "source_count": source_count,
        "target_count": target_count,
        "count_match": int(source_count == target_count),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import always-stock data from SQLite to Postgres")
    parser.add_argument(
        "--source-sqlite-path",
        default=str(default_sqlite_path()),
        help="Path to source SQLite database file",
    )
    parser.add_argument(
        "--target-database-url",
        default=os.getenv("TARGET_DATABASE_URL") or os.getenv("DATABASE_URL"),
        help="Target database URL (usually Postgres)",
    )
    parser.add_argument(
        "--tables",
        nargs="*",
        default=[],
        help="Optional subset of tables to import",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-counts", action="store_true")
    parser.add_argument(
        "--checkpoint-file",
        default="logs/sqlite_to_postgres_checkpoint.json",
        help="Checkpoint file for resuming completed tables",
    )
    parser.add_argument("--reset-checkpoint", action="store_true")
    parser.add_argument("--truncate-target", action="store_true")
    parser.add_argument(
        "--report-json",
        default="logs/sqlite_to_postgres_report.json",
        help="Write import summary report to this JSON file",
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
    checkpoint_path = Path(args.checkpoint_file)
    report_path = Path(args.report_json)
    checkpoint = (
        {"completed_tables": [], "table_reports": {}}
        if args.reset_checkpoint
        else load_checkpoint(checkpoint_path)
    )

    Base.metadata.create_all(bind=target_engine)

    if args.truncate_target and not args.dry_run:
        truncate_target_tables(target_engine, specs)
        checkpoint = {"completed_tables": [], "table_reports": {}}

    print(f"[import] source={source_path}")
    print(f"[import] target={args.target_database_url}")
    print(f"[import] tables={', '.join(spec.name for spec in specs)}")
    print(f"[import] batch_size={args.batch_size}")
    if args.dry_run:
        print("[import] dry_run=true")

    report = {
        "started_at": utc_now_iso(),
        "source_sqlite_path": str(source_path),
        "target_database_url": args.target_database_url,
        "tables": [spec.name for spec in specs],
        "table_reports": dict(checkpoint.get("table_reports", {})),
    }

    completed_tables = set(checkpoint.get("completed_tables", []))

    for spec in specs:
        if spec.name in completed_tables:
            print(f"[import] {spec.name}: skipped (already completed in checkpoint)")
            continue

        imported_rows = import_table(
            source_engine=source_engine,
            target_engine=target_engine,
            spec=spec,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        print(f"[import] {spec.name}: processed_rows={imported_rows}")

        if not args.dry_run:
            table_report = build_table_report(source_engine, target_engine, spec, imported_rows)
            report["table_reports"][spec.name] = table_report
            completed_tables.add(spec.name)
            checkpoint["completed_tables"] = sorted(completed_tables)
            checkpoint["table_reports"] = report["table_reports"]
            checkpoint["updated_at"] = utc_now_iso()
            save_checkpoint(checkpoint_path, checkpoint)

    if not args.dry_run:
        reset_postgres_sequences(target_engine, specs)

    if args.verify_counts and not args.dry_run:
        verify_row_counts(source_engine, target_engine, specs)

    report["completed_at"] = utc_now_iso()
    report["completed_tables"] = sorted(completed_tables)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=True, indent=2, sort_keys=True)

    print("[import] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
