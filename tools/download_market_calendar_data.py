"""Download the point-in-time data used by the Taiwan market calendar.

Examples
--------
    python3 tools/download_market_calendar_data.py \
      --start 2026-07-15 --end 2026-11-15 \
      --output docs/reports/market_calendar_2026-07-15_2026-11-15.csv

Sources are deliberately explicit in the output metadata columns:
TAIEX/stock institutional data comes from the deployed always-stock API;
Taiwan VIX and MTX are from TAIFEX; VIX, DGS10 and USD/TWD are FRED series;
gold is the Yahoo Finance COMEX continuous front-month contract (GC=F).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests


TAIEX_API = "https://always-stock.onrender.com/api/stocks/TAIEX/history"
INDUSTRIES_API = "https://always-stock.onrender.com/api/industries"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
TAIFEX_VIX = "https://www.taifex.com.tw/cht/7/getVixData"
TAIFEX_VIX_MONTH = "https://www.taifex.com.tw/file/taifex/Dailydownload/vix/log2data/{month}new.txt"
TAIFEX_MTX = "https://www.taifex.com.tw/enl/eng3/futContractsDateDown"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _get_json(url: str, params: Optional[Dict[str, str]] = None) -> dict:
    response = requests.get(url, params=params, timeout=45, headers={"User-Agent": "always-stock-market-calendar/1.0"})
    response.raise_for_status()
    return response.json()


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def fetch_taiex(start: date, end: date) -> pd.DataFrame:
    data = _get_json(TAIEX_API, {"start_date": str(start), "end_date": str(end)})
    rows = data.get("history", [])
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("TAIEX API returned no history")
    return frame[["trade_date", "open_price", "high_price", "low_price", "close_price"]]


def fetch_institutional_flows(dates: Iterable[date]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for current in dates:
        payload = _get_json(INDUSTRIES_API, {"date": str(current)})
        items = payload if isinstance(payload, list) else payload.get("data", [])
        aggregate = {"foreign_net_amount": 0.0, "trust_net_amount": 0.0, "dealer_net_amount": 0.0}
        for item in items:
            for key in aggregate:
                aggregate[key] += float(item.get(key) or 0.0)
        aggregate["trade_date"] = str(current)
        aggregate["inst_total_net_amount"] = sum(aggregate[key] for key in aggregate if key.endswith("net_amount"))
        rows.append(aggregate)
        time.sleep(0.05)
    return pd.DataFrame(rows)


def fetch_fred(series_id: str, start: date, end: date, output_name: str) -> pd.DataFrame:
    response = requests.get(
        FRED_CSV,
        params={"id": series_id, "cosd": str(start), "coed": str(end)},
        timeout=45,
        headers={"User-Agent": "always-stock-market-calendar/1.0"},
    )
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text), na_values=[".", ""])
    frame = frame.rename(columns={"observation_date": "trade_date", series_id: output_name})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame[output_name] = pd.to_numeric(frame[output_name], errors="coerce")
    return frame[["trade_date", output_name]]


def fetch_yahoo(symbol: str, output_name: str) -> pd.DataFrame:
    response = requests.get(
        YAHOO_CHART.format(symbol=symbol),
        params={"range": "1y", "interval": "1d", "events": "history"},
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    rows = []
    for timestamp, close in zip(result.get("timestamp", []), quote.get("close", [])):
        if close is None:
            continue
        rows.append({"trade_date": pd.to_datetime(timestamp, unit="s", utc=True).strftime("%Y-%m-%d"), output_name: float(close)})
    return pd.DataFrame(rows)


def fetch_taiwan_vix(trading_dates: Iterable[str]) -> pd.DataFrame:
    requested = set(trading_dates)
    rows = []
    months = sorted({trade_date[:7].replace("-", "") for trade_date in requested})
    for month in months:
        response = requests.get(
            TAIFEX_VIX_MONTH.format(month=month),
            timeout=45,
            headers={"User-Agent": "always-stock-market-calendar/1.0"},
        )
        response.raise_for_status()
        text = response.content.decode("cp950", errors="replace")
        for line in text.splitlines():
            numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", line)
            if len(numbers) < 3:
                continue
            compact = numbers[0]
            trade_date = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
            if trade_date in requested:
                # Monthly log columns are date, timestamp, closing VIX and
                # the final-minute average.  Use the closing value (column 3).
                rows.append({"trade_date": trade_date, "tw_vix": float(numbers[2])})
        time.sleep(0.05)
    return pd.DataFrame(rows)


def fetch_mtx(start: date, end: date) -> pd.DataFrame:
    response = requests.post(
        TAIFEX_MTX,
        data={
            "queryStartDate": start.strftime("%Y/%m/%d"),
            "queryEndDate": end.strftime("%Y/%m/%d"),
            "commodityId": "MXF",
            "firstDate": "2023-11-24 00:00:00.0",
            "lastDate": str(end) + " 00:00:00.0",
        },
        timeout=90,
        headers={"User-Agent": "always-stock-market-calendar/1.0"},
    )
    response.raise_for_status()
    text = response.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if row.get("Contract Code") not in {"MTX", "小型臺指期貨"}:
            continue
        item = row.get("Item", "")
        if item not in {"Dealers", "Investment Trust Companies", "FINI"}:
            continue
        rows.append({
            "trade_date": pd.to_datetime(row["Date"], format="%Y/%m/%d").strftime("%Y-%m-%d"),
            "mtx_oi_long": float(row["Open Interest (Long)"].replace(",", "")),
            "mtx_oi_short": float(row["Open Interest (Short)"].replace(",", "")),
            "mtx_oi_net": float(row["Open Interest (Net)"].replace(",", "")),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.groupby("trade_date", as_index=False).sum(numeric_only=True)


def build_panel(start: date, end: date) -> pd.DataFrame:
    taiex = fetch_taiex(start, end)
    dates = taiex["trade_date"].astype(str).tolist()
    panel = taiex.copy()
    panel = panel.merge(fetch_institutional_flows(pd.to_datetime(dates).date), on="trade_date", how="left")
    panel = panel.merge(fetch_taiwan_vix(dates), on="trade_date", how="left")
    panel = panel.merge(fetch_fred("VIXCLS", start, end, "us_vix"), on="trade_date", how="left")
    panel = panel.merge(fetch_fred("DGS10", start, end, "us10y"), on="trade_date", how="left")
    panel = panel.merge(fetch_fred("DEXTAUS", start, end, "usd_twd"), on="trade_date", how="left")
    panel = panel.merge(fetch_yahoo("GC%3DF", "gold"), on="trade_date", how="left")
    panel = panel.merge(fetch_mtx(start, end), on="trade_date", how="left")
    return panel.sort_values("trade_date").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("end must not be before start")
    frame = build_panel(args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "start": str(args.start),
        "end": str(args.end),
        "rows": len(frame),
        "sources": {
            "taiex": TAIEX_API,
            "institutional": INDUSTRIES_API,
            "taiwan_vix": TAIFEX_VIX,
            "mtx": TAIFEX_MTX,
            "us_vix": "FRED:VIXCLS",
            "us10y": "FRED:DGS10",
            "usd_twd": "FRED:DEXTAUS",
            "gold": "Yahoo Finance:GC=F",
        },
    }
    args.output.with_suffix(".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
