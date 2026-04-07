"""
Resumable historical backfill script.

Fetches daily_price + inst_flow + aggregation for each trading day in the
specified date range. Supports automatic resume via a checkpoint file —
if the process is interrupted (network error, Ctrl-C, etc.), restarting
the script will pick up from the last successfully completed date.

Usage:
    python run_backfill.py --start 2023-01-01 --end 2026-04-01

Options:
    --start         Start date (YYYY-MM-DD), default 2023-01-01
    --end           End date (YYYY-MM-DD), default 2026-04-01
    --checkpoint    Checkpoint file path (default: db/backfill_checkpoint.txt)
    --delay         Seconds between API requests (default: 3.5, TWSE rate limit)
    --skip-master   Skip stock_master update
    --reset         Ignore checkpoint and start from --start
"""
import argparse
import logging
import os
import signal
import sys
import time
from datetime import date, timedelta
from typing import Optional

from logging_config import setup_logging
from app.database import SessionLocal
from init_db import init_db
from etl.fetch_stock_master import fetch_and_upsert_stock_master
from etl.fetch_daily_price import fetch_and_upsert_daily_price
from etl.fetch_inst_flow import fetch_and_upsert_inst_flow
from etl.aggregate_industry_flow import aggregate_industry_flow

logger = logging.getLogger(__name__)

# Graceful shutdown flag
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logger.warning("Shutdown requested (signal %d), finishing current date...", signum)


def _read_checkpoint(path: str) -> Optional[date]:
    """Read the last successfully completed date from checkpoint file."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            text = f.read().strip()
            if text:
                return date.fromisoformat(text)
    except (ValueError, OSError) as e:
        logger.warning("Could not read checkpoint %s: %s", path, e)
    return None


def _write_checkpoint(path: str, d: date):
    """Write the last successfully completed date to checkpoint file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(d.isoformat() + "\n")


def _generate_weekday_dates(start: date, end: date):
    """Yield weekday dates (Mon-Fri) from start to end inclusive."""
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon=0 .. Fri=4
            yield current
        current += timedelta(days=1)


def run_one_day(trade_date: date, delay: float) -> bool:
    """
    Run ETL for a single day. Returns True if data was committed,
    False for non-trading days (holidays).
    """
    db = SessionLocal()
    try:
        price_count = fetch_and_upsert_daily_price(db, trade_date)
        if price_count == 0:
            logger.info("  %s → non-trading day (holiday), skipped", trade_date)
            return False

        # Delay between TWSE API calls to avoid rate limiting
        time.sleep(delay)

        flow_count = fetch_and_upsert_inst_flow(db, trade_date)
        agg_count = aggregate_industry_flow(db, trade_date)

        logger.info(
            "  %s → price=%d, flow=%d, agg=%d",
            trade_date, price_count, flow_count, agg_count,
        )
        return True
    except Exception:
        logger.exception("ETL failed for %s", trade_date)
        db.rollback()
        return False
    finally:
        db.close()


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(description="Resumable historical backfill")
    parser.add_argument("--start", type=str, default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="2026-04-01", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--checkpoint", type=str, default="db/backfill_checkpoint.txt",
        help="Checkpoint file path",
    )
    parser.add_argument("--delay", type=float, default=3.5, help="Seconds between API calls")
    parser.add_argument("--skip-master", action="store_true", help="Skip stock_master update")
    parser.add_argument("--reset", action="store_true", help="Ignore checkpoint, start from --start")
    parser.add_argument("--token", type=str, default="", help="FinMind API token")
    parser.add_argument(
        "--fugle-mapping", type=str, default=None,
        help="Path to Fugle sub-industry CSV",
    )
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Initialize DB tables
    init_db()

    # Resolve Fugle mapping path
    fugle_mapping = args.fugle_mapping
    if fugle_mapping is None:
        default_path = os.path.join(
            os.path.dirname(__file__), "..", "tools", "output", "fugle_industry_mapping.csv"
        )
        if os.path.exists(default_path):
            fugle_mapping = os.path.normpath(default_path)

    # Update stock master once at start (unless skipped)
    if not args.skip_master:
        db = SessionLocal()
        try:
            n = fetch_and_upsert_stock_master(db, token=args.token, fugle_mapping_path=fugle_mapping)
            logger.info("Stock master updated: %d stocks", n)
        finally:
            db.close()

    # Resume from checkpoint
    resume_date = None
    if not args.reset:
        resume_date = _read_checkpoint(args.checkpoint)

    if resume_date and resume_date >= start_date:
        effective_start = resume_date + timedelta(days=1)
        logger.info("Resuming from checkpoint: %s (next: %s)", resume_date, effective_start)
    else:
        effective_start = start_date
        logger.info("Starting fresh from %s", effective_start)

    # Count total weekdays for progress reporting
    all_dates = list(_generate_weekday_dates(effective_start, end_date))
    total = len(all_dates)
    logger.info("Backfill: %s → %s (%d weekdays to process)", effective_start, end_date, total)

    success = 0
    skipped = 0
    errors = 0
    consecutive_errors = 0
    max_consecutive_errors = 5

    for i, d in enumerate(all_dates):
        if _shutdown_requested:
            logger.warning("Shutdown requested, stopping after %d/%d dates", i, total)
            break

        try:
            result = run_one_day(d, args.delay)
            if result:
                success += 1
            else:
                skipped += 1

            # Save checkpoint after each successful day
            _write_checkpoint(args.checkpoint, d)
            consecutive_errors = 0

            # Progress log every 10 dates
            if (i + 1) % 10 == 0:
                logger.info(
                    "Progress: %d/%d (%.1f%%) — success=%d, skipped=%d, errors=%d",
                    i + 1, total, (i + 1) / total * 100, success, skipped, errors,
                )

        except Exception:
            logger.exception("Unexpected error processing %s", d)
            errors += 1
            consecutive_errors += 1

            if consecutive_errors >= max_consecutive_errors:
                logger.error(
                    "Too many consecutive errors (%d), stopping. Resume later.",
                    consecutive_errors,
                )
                break

        # Delay between dates to avoid TWSE rate limiting
        if i < total - 1:
            time.sleep(args.delay)

    logger.info(
        "Backfill complete. success=%d, skipped=%d, errors=%d, total_processed=%d/%d",
        success, skipped, errors, success + skipped + errors, total,
    )
    if _shutdown_requested:
        logger.info("Process was interrupted. Run again to resume from checkpoint.")


if __name__ == "__main__":
    main()
