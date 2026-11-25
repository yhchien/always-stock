"""Create a recent-source coverage and derivation report for market calendar data.

Examples:
    python3 tools/report_market_calendar_data_quality.py \
      --input docs/reports/market_calendar_2026-08-01_2026-11-23.csv \
      --output docs/reports/market_calendar_data_quality.md --recent 10

The daily downloader writes ``source_metadata`` as JSON in the panel CSV.
Older snapshots without that column are still readable, but are reported as
having no provenance rather than being treated as complete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import pandas as pd


def _parse_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if pd.isna(value) if not isinstance(value, dict) else False:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def build_report(frame: pd.DataFrame, recent: int = 10) -> str:
    frame = frame.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.sort_values("trade_date").dropna(subset=["trade_date"])
    metadata = frame.get("source_metadata", pd.Series([{}] * len(frame), index=frame.index)).map(_parse_metadata)

    source_keys = sorted({key for row in metadata for key in row})
    lines = [
        "# 台股行事曆資料品質報告",
        "",
        f"資料列數：{len(frame)}",
        f"檢查區間：{frame['trade_date'].min().date()} ～ {frame['trade_date'].max().date()}",
        "",
        "## 來源覆蓋率",
        "",
        "| 來源 | 有值 | 總數 | 覆蓋率 |",
        "|---|---:|---:|---:|",
    ]
    for key in source_keys:
        available = sum(row.get(key, {}).get("status") == "available" for row in metadata)
        lines.append(f"| {key} | {available} | {len(frame)} | {available / len(frame):.1%} |")

    lines.extend(["", "## 最近資料列缺值", "", "| 觀測日 | 缺少來源 | 缺值原因 |", "|---|---|---|"])
    for index in metadata.index[-recent:]:
        row = metadata.loc[index]
        missing = [key for key, value in row.items() if value.get("status") == "missing"]
        reasons = sorted({value.get("missing_reason") for value in row.values() if value.get("status") == "missing"})
        lines.append(
            f"| {frame.loc[index, 'trade_date'].date()} | "
            f"{', '.join(missing) if missing else '無'} | "
            f"{'; '.join(reason for reason in reasons if reason) if reasons else '—'} |"
        )

    derived_columns = [
        "us_vix_change", "us_vix_z20", "us10y_change", "us10y_change_5d",
        "usd_twd_change", "usd_twd_change_5d", "us_sp500_change",
        "us_nasdaq_change", "us_sox_change", "mtx_net_oi_change",
    ]
    present = [column for column in derived_columns if column in frame]
    lines.extend(["", "## 衍生欄位最近值", "", "| 觀測日 | " + " | ".join(present) + " |", "|---|" + "---:|" * len(present)])
    for _, row in frame.tail(recent).iterrows():
        values = ["" if pd.isna(row[column]) else f"{float(row[column]):.6g}" for column in present]
        lines.append(f"| {row['trade_date'].date()} | " + " | ".join(values) + " |")
    lines.extend([
        "",
        "## 判讀規則",
        "",
        "- 缺值保留為空值，不以前一日數值填補。",
        "- VIX z-score 需要 20 個有效觀測值；未達到時屬於指標暖機，不是 0。",
        "- `us10y_change` 單位為百分點；換算基點時乘以 100。",
        "- `usd_twd` 使用央行 NTD/USD，數值是每 1 美元的新臺幣價格。",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--recent", type=int, default=10)
    args = parser.parse_args()
    frame = pd.read_csv(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(frame, recent=args.recent), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
