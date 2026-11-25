"""Download and aggregate Taiwan market margin/short-sale data.

The FinMind dataset is stock-level.  The calendar model uses market-wide
aggregates so that the resulting features are aligned with the TAIEX rather
than with one selected security.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests


FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
DATASET = "TaiwanStockMarginPurchaseShortSale"


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_finmind_token(env_file: Optional[Path] = None) -> str:
    """Load a token without printing it or including it in output metadata."""
    if os.environ.get("FINMIND_TOKEN"):
        return os.environ["FINMIND_TOKEN"]
    candidates = [env_file] if env_file else []
    candidates.extend([Path("backend/.env.finmind"), Path("backend/.env")])
    for candidate in candidates:
        if candidate is None:
            continue
        token = _read_env_file(candidate).get("FINMIND_TOKEN")
        if token:
            return token
    raise RuntimeError("FINMIND_TOKEN was not found in the environment or backend env files")


def _date_range(start: str, end: str):
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        yield current.isoformat()
        current += timedelta(days=1)


def fetch_margin_raw(start: str, end: str, token: str) -> pd.DataFrame:
    """Fetch one day at a time because this FinMind dataset ignores end_date."""
    frames = []
    for trade_date in _date_range(start, end):
        response = requests.get(
            FINMIND_URL,
            params={
                "dataset": DATASET,
                "start_date": trade_date,
                "end_date": trade_date,
                "token": token,
            },
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") not in (200, "200"):
            raise RuntimeError(
                "FinMind returned status %s on %s: %s"
                % (body.get("status"), trade_date, body.get("msg"))
            )
        rows = body.get("data") or []
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        raise RuntimeError("FinMind returned no margin/short-sale records")
    return pd.concat(frames, ignore_index=True)


def aggregate_margin(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate stock-level records into daily market-wide indicators."""
    required = {
        "date",
        "MarginPurchaseBuy",
        "MarginPurchaseTodayBalance",
        "MarginPurchaseYesterdayBalance",
        "ShortSaleBuy",
        "ShortSaleTodayBalance",
        "ShortSaleYesterdayBalance",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise RuntimeError("FinMind margin dataset is missing columns: %s" % ", ".join(missing))

    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    numeric_columns = [
        "MarginPurchaseBuy",
        "MarginPurchaseTodayBalance",
        "MarginPurchaseYesterdayBalance",
        "ShortSaleBuy",
        "ShortSaleTodayBalance",
        "ShortSaleYesterdayBalance",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result["margin_change"] = (
        result["MarginPurchaseTodayBalance"] - result["MarginPurchaseYesterdayBalance"]
    )
    result["short_change"] = (
        result["ShortSaleTodayBalance"] - result["ShortSaleYesterdayBalance"]
    )
    grouped = result.groupby("trade_date", as_index=False).agg(
        margin_purchase_buy=("MarginPurchaseBuy", "sum"),
        margin_balance=("MarginPurchaseTodayBalance", "sum"),
        margin_change=("margin_change", "sum"),
        short_sale_buy=("ShortSaleBuy", "sum"),
        short_balance=("ShortSaleTodayBalance", "sum"),
        short_change=("short_change", "sum"),
        margin_stock_count=("stock_id", "nunique"),
    )
    return grouped.sort_values("trade_date").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    token = load_finmind_token(args.env_file)
    raw = fetch_margin_raw(args.start, args.end, token)
    frame = aggregate_margin(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "start": args.start,
        "end": args.end,
        "rows": len(frame),
        "raw_rows": len(raw),
        "dataset": DATASET,
        "source": FINMIND_URL,
        "columns": list(frame.columns),
    }
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
