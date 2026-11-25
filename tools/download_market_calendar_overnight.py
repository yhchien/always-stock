"""Download overnight US and Taiwan futures factors for the market calendar.

US daily bars are aligned to the Taiwan signal date: the US session on date T
finishes before Taiwan opens on T+1.  TAIFEX after-hours bars are aligned to
their official trading-date field and later joined to the next TAIEX session;
TAIFEX documents that an after-hours session labelled D starts at 15:00 on
D-1 and ends at 05:00 on D.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
import requests

from tools.download_market_calendar_data import (
    FRED_CSV,
    align_external_observations,
    attach_source_metadata,
)


YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
TAIFEX_DAILY = "https://www.taifex.com.tw/cht/3/futDataDown"
USER_AGENT = "always-stock-market-calendar/1.0"

US_SYMBOLS = {
    "us_es": "ES%3DF",
    "us_nq": "NQ%3DF",
    "us_ym": "YM%3DF",
}

US_SPOT_SERIES = {
    "us_sp500": "SP500",
    "us_nasdaq": "NASDAQCOM",
    "us_sox": "NASDAQSOX",
}


def fetch_fred_spot_returns(start: date, end: date) -> pd.DataFrame:
    frames = []
    for name, series_id in US_SPOT_SERIES.items():
        response = requests.get(
            FRED_CSV,
            params={"id": series_id, "cosd": str(start), "coed": str(end)},
            timeout=45,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text), na_values=[".", ""])
        frame["trade_date"] = pd.to_datetime(frame["observation_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        frame[series_id] = pd.to_numeric(frame[series_id], errors="coerce")
        frame[name + "_change"] = frame[series_id].dropna().pct_change() * 100.0
        frame[name + "_observation_date"] = frame["trade_date"]
        frames.append(frame[["trade_date", name + "_change", name + "_observation_date"]])
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    return result.sort_values("trade_date").reset_index(drop=True)


def fetch_yahoo_returns(start: date, end: date) -> pd.DataFrame:
    frames = []
    period_start = int(pd.Timestamp(start).tz_localize("UTC").timestamp())
    period_end = int((pd.Timestamp(end) + pd.Timedelta(days=1)).tz_localize("UTC").timestamp())
    for name, symbol in US_SYMBOLS.items():
        response = requests.get(
            YAHOO_CHART.format(symbol=symbol),
            params={"period1": period_start, "period2": period_end, "interval": "1d", "events": "history"},
            timeout=60,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        timestamps = result.get("timestamp", [])
        closes = result["indicators"]["quote"][0].get("close", [])
        rows = []
        for timestamp, close in zip(timestamps, closes):
            if close is not None:
                rows.append({"trade_date": pd.to_datetime(timestamp, unit="s", utc=True).strftime("%Y-%m-%d"), name + "_close": float(close)})
        frame = pd.DataFrame(rows)
        if frame.empty:
            continue
        frame[name + "_change"] = frame[name + "_close"].pct_change() * 100.0
        frames.append(frame[["trade_date", name + "_change"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date"])
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    return result.sort_values("trade_date").reset_index(drop=True)


def _month_ranges(start: date, end: date) -> Iterable[tuple]:
    current = date(start.year, start.month, 1)
    while current <= end:
        last_day = date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        yield max(start, current), min(end, last_day)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)


def _number(value: object):
    if value is None or str(value).strip() in {"", "-"}:
        return None
    return float(str(value).replace(",", "").replace("%", ""))


def fetch_taifex_session(start: date, end: date, commodity_id: str, prefix: str) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for range_start, range_end in _month_ranges(start, end):
        response = requests.post(
            TAIFEX_DAILY,
            data={
                "queryStartDate": range_start.strftime("%Y/%m/%d"),
                "queryEndDate": range_end.strftime("%Y/%m/%d"),
                "commodity_id": commodity_id,
                "commodity_id2": "",
                "down_type": "1",
            },
            timeout=120,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        text = response.content.decode("cp950", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            if str(row.get("交易時段", "")).strip() != "盤後":
                continue
            trade_date = pd.to_datetime(row["交易日期"], errors="coerce")
            if pd.isna(trade_date):
                continue
            expiry = str(row["到期月份(週別)"]).strip()
            pure_month = bool(re.fullmatch(r"\d{6}", expiry))
            expiry_key = re.search(r"\d{6}", expiry)
            if not expiry_key:
                continue
            rows.append(
                {
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "expiry": expiry,
                    "expiry_key": expiry_key.group(0),
                    "pure_month": pure_month,
                    prefix + "_night_open": _number(row["開盤價"]),
                    prefix + "_night_high": _number(row["最高價"]),
                    prefix + "_night_low": _number(row["最低價"]),
                    prefix + "_night_close": _number(row["收盤價"]),
                    prefix + "_night_volume": _number(row["成交量"]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["trade_date"])
    frame = pd.DataFrame(rows).sort_values(["trade_date", "expiry_key", "pure_month"], ascending=[True, True, False])
    return frame.drop_duplicates("trade_date", keep="first").drop(columns=["expiry", "expiry_key", "pure_month"])


def build_overnight(start: date, end: date) -> pd.DataFrame:
    # Cash indices use the explicitly requested FRED spot series.  Futures
    # remain Yahoo's ES/NQ/YM overnight contracts and are kept separate.
    result = fetch_fred_spot_returns(start, end)
    futures = fetch_yahoo_returns(start, end)
    if not futures.empty:
        result = result.merge(futures, on="trade_date", how="outer")
    taifex_frames = []
    for commodity, prefix in (("TX", "tx"), ("MTX", "mtx")):
        frame = fetch_taifex_session(start, end, commodity, prefix)
        taifex_frames.append(frame)
    target_dates = sorted({date_value for frame in taifex_frames for date_value in frame.get("trade_date", [])})
    for name in US_SPOT_SERIES:
        observation_column = name + "_observation_date"
        if observation_column in result:
            result = align_external_observations(result, target_dates, observation_column)
            break
    for frame in taifex_frames:
        result = frame if result.empty else result.merge(frame, on="trade_date", how="outer")
    result = result.sort_values("trade_date").reset_index(drop=True)
    specs = {
        "us_sp500": {"columns": ["us_sp500_change"], "source": FRED_CSV, "series_id": "SP500", "unit": "percent change", "observation_date_column": "us_sp500_observation_date", "missing_reason": "FRED SP500 has no US-market observation for this date or was not yet published"},
        "us_nasdaq": {"columns": ["us_nasdaq_change"], "source": FRED_CSV, "series_id": "NASDAQCOM", "unit": "percent change", "observation_date_column": "us_nasdaq_observation_date", "missing_reason": "FRED NASDAQCOM has no US-market observation for this date or was not yet published"},
        "us_sox": {"columns": ["us_sox_change"], "source": FRED_CSV, "series_id": "NASDAQSOX", "unit": "percent change", "observation_date_column": "us_sox_observation_date", "missing_reason": "FRED NASDAQSOX has no US-market observation for this date or was not yet published"},
        "us_es": {"columns": ["us_es_change"], "source": "Yahoo Finance chart API", "series_id": "ES=F", "unit": "percent change", "missing_reason": "Yahoo ES=F has no observation for this date or was not yet published"},
        "us_nq": {"columns": ["us_nq_change"], "source": "Yahoo Finance chart API", "series_id": "NQ=F", "unit": "percent change", "missing_reason": "Yahoo NQ=F has no observation for this date or was not yet published"},
        "us_ym": {"columns": ["us_ym_change"], "source": "Yahoo Finance chart API", "series_id": "YM=F", "unit": "percent change", "missing_reason": "Yahoo YM=F has no observation for this date or was not yet published"},
        "tx_night": {"columns": ["tx_night_close"], "source": TAIFEX_DAILY, "series_id": "TX", "unit": "index points", "session": "盤後", "missing_reason": "TAIFEX TX after-hours session has no row for this trading date"},
        "mtx_night": {"columns": ["mtx_night_close"], "source": TAIFEX_DAILY, "series_id": "MTX", "unit": "index points", "session": "盤後", "missing_reason": "TAIFEX MTX after-hours session has no row for this trading date"},
    }
    return attach_source_metadata(result, specs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    frame = build_overnight(args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_frame = frame.copy()
    if "source_metadata" in output_frame:
        output_frame["source_metadata"] = output_frame["source_metadata"].map(
            lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
    output_frame.to_csv(args.output, index=False)
    metadata = {
        "start": str(args.start),
        "end": str(args.end),
        "rows": len(frame),
        "sources": {
            "us_cash": "FRED:SP500,NASDAQCOM,NASDAQSOX",
            "us_futures": "Yahoo Finance chart API: ES=F,NQ=F,YM=F",
            "taifex": TAIFEX_DAILY,
        },
        "definitions": {
            "us_nasdaq": "Nasdaq Composite (FRED NASDAQCOM), not Nasdaq-100",
            "us_spot_alignment": "US observation date is attached to the next Taiwan trading session; source_metadata retains the original date",
        },
        "taifex_session": "盤後",
        "alignment": "TAIFEX trade_date D is the overnight session ending at 05:00 on D; join to TAIEX target_date D.",
    }
    args.output.with_suffix(".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
