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
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd
import requests

from tools.download_market_calendar_data import (
    FRED_CSV,
    align_external_observations,
    attach_source_metadata,
    fetch_fred,
)


YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOOQ_CSV = "https://stooq.com/q/d/l/"
TAIFEX_DAILY = "https://www.taifex.com.tw/cht/3/futDataDown"
USER_AGENT = "always-stock-market-calendar/1.0"
TAIFEX_RETRIES = 1
TAIFEX_TIMEOUT = (5, 20)
logger = logging.getLogger(__name__)

US_SYMBOLS = {
    "us_es": "ES%3DF",
    "us_nq": "NQ%3DF",
    "us_ym": "YM%3DF",
}

US_SPOT_SYMBOLS = {
    "us_sp500": "^GSPC",
    "us_nasdaq": "^IXIC",  # Nasdaq Composite, not Nasdaq-100.
    "us_sox": "^SOX",
}

US_SPOT_SERIES = {
    "us_sp500": "SP500",
    "us_nasdaq": "NASDAQCOM",
    "us_sox": "NASDAQSOX",
}


def fetch_fred_spot_returns(start: date, end: date) -> pd.DataFrame:
    yahoo = fetch_yahoo_returns(start, end, US_SPOT_SYMBOLS)
    frames = []
    source_details = {}
    for name, series_id in US_SPOT_SERIES.items():
        change_column = name + "_change"
        if change_column in yahoo:
            frame = yahoo[["trade_date", change_column]].copy()
            source_details[name] = yahoo.attrs.get("source_details", {}).get(
                name,
                {"source": "Yahoo Finance chart API", "series_id": US_SPOT_SYMBOLS[name]},
            )
        else:
            frame = fetch_fred(series_id, start, end, name + "_close")
            frame[change_column] = frame[name + "_close"].pct_change() * 100.0
            frame = frame[["trade_date", change_column]]
            source_details[name] = {
                "source": frame.attrs.get("source_url", FRED_CSV),
                "series_id": frame.attrs.get("series_id", series_id),
            }
        frame[name + "_observation_date"] = frame["trade_date"]
        frames.append(frame[["trade_date", name + "_change", name + "_observation_date"]])
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    result.attrs["source_details"] = source_details
    return result.sort_values("trade_date").reset_index(drop=True)


def fetch_yahoo_returns(start: date, end: date, symbols=None) -> pd.DataFrame:
    return _fetch_yahoo_returns(start, end, symbols, allow_stooq_fallback=False)


def fetch_stooq_returns(start: date, end: date, symbols: Dict[str, str]) -> pd.DataFrame:
    """Fetch daily continuous-futures closes from Stooq as a Yahoo fallback.

    Stooq is used only when Yahoo has no usable series.  It is a daily close
    fallback for the same continuous contracts, not a substitute for
    TAIFEX's official Taiwan night-session OHLC data.
    """
    frames = []
    source_details = {}
    for name, symbol in symbols.items():
        try:
            response = requests.get(
                STOOQ_CSV,
                params={
                    "s": symbol,
                    "d1": start.strftime("%Y%m%d"),
                    "d2": end.strftime("%Y%m%d"),
                    "i": "d",
                },
                timeout=30,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.text))
            if not {"Date", "Close"}.issubset(frame.columns):
                raise ValueError("Stooq CSV has no Date/Close columns")
            frame = frame.rename(columns={"Date": "trade_date", "Close": name + "_close"})
            frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            frame[name + "_close"] = pd.to_numeric(frame[name + "_close"], errors="coerce")
            frame = frame.dropna(subset=["trade_date", name + "_close"])
            if frame.empty:
                raise ValueError("Stooq returned no usable rows")
            frame[name + "_change"] = frame[name + "_close"].pct_change() * 100.0
            frames.append(frame[["trade_date", name + "_change"]])
            source_details[name] = {
                "source": STOOQ_CSV,
                "series_id": symbol,
            }
        except (requests.RequestException, ValueError, pd.errors.ParserError) as exc:
            logger.warning("Stooq %s unavailable; leaving futures factor missing: %s", symbol, exc)
    if not frames:
        result = pd.DataFrame(columns=["trade_date"])
    else:
        result = frames[0]
        for frame in frames[1:]:
            result = result.merge(frame, on="trade_date", how="outer")
        result = result.sort_values("trade_date").reset_index(drop=True)
    result.attrs["source_details"] = source_details
    return result


def _fetch_yahoo_returns(start: date, end: date, symbols=None, allow_stooq_fallback: bool = False) -> pd.DataFrame:
    frames = []
    symbols = symbols or US_SYMBOLS
    period_start = int(pd.Timestamp(start).tz_localize("UTC").timestamp())
    period_end = int((pd.Timestamp(end) + pd.Timedelta(days=1)).tz_localize("UTC").timestamp())
    source_details = {}
    missing_names = set(symbols)
    for name, symbol in symbols.items():
        try:
            response = requests.get(
                YAHOO_CHART.format(symbol=symbol),
                params={"period1": period_start, "period2": period_end, "interval": "1d", "events": "history"},
                timeout=60,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
        except (requests.RequestException, KeyError, TypeError, IndexError) as exc:
            logger.warning("Yahoo %s unavailable; leaving futures factor missing: %s", symbol, exc)
            continue
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
        source_details[name] = {"source": "Yahoo Finance chart API", "series_id": symbol}
        missing_names.discard(name)
        frames.append(frame[["trade_date", name + "_change"]])
    if allow_stooq_fallback and missing_names:
        stooq_symbols = {name: {"us_es": "es.f", "us_nq": "nq.f", "us_ym": "ym.f"}[name] for name in missing_names if name in {"us_es", "us_nq", "us_ym"}}
        stooq = fetch_stooq_returns(start, end, stooq_symbols)
        stooq_details = stooq.attrs.get("source_details", {})
        for name in stooq_symbols:
            change_column = name + "_change"
            if change_column in stooq:
                frames.append(stooq[["trade_date", change_column]])
                source_details[name] = stooq_details.get(name, {"source": STOOQ_CSV, "series_id": stooq_symbols[name]})
    if not frames:
        result = pd.DataFrame(columns=["trade_date"])
        result.attrs["source_details"] = source_details
        return result
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="trade_date", how="outer")
    result = result.sort_values("trade_date").reset_index(drop=True)
    result.attrs["source_details"] = source_details
    return result


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
    columns = [
        "trade_date",
        prefix + "_night_open",
        prefix + "_night_high",
        prefix + "_night_low",
        prefix + "_night_close",
        prefix + "_night_volume",
    ]
    for range_start, range_end in _month_ranges(start, end):
        response = None
        for attempt in range(1, TAIFEX_RETRIES + 1):
            try:
                response = requests.post(
                    TAIFEX_DAILY,
                    data={
                        "queryStartDate": range_start.strftime("%Y/%m/%d"),
                        "queryEndDate": range_end.strftime("%Y/%m/%d"),
                        "commodity_id": commodity_id,
                        "commodity_id2": "",
                        "down_type": "1",
                    },
                    timeout=TAIFEX_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                logger.warning(
                    "TAIFEX %s %s-%s attempt %s/%s failed: %s",
                    commodity_id,
                    range_start,
                    range_end,
                    attempt,
                    TAIFEX_RETRIES,
                    exc,
                )
                if attempt < TAIFEX_RETRIES:
                    time.sleep(2 * attempt)
        if response is None or response.status_code >= 400:
            logger.warning("TAIFEX %s %s-%s unavailable; leaving night-session factors missing", commodity_id, range_start, range_end)
            continue
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
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows).sort_values(["trade_date", "expiry_key", "pure_month"], ascending=[True, True, False])
    return frame.drop_duplicates("trade_date", keep="first").drop(columns=["expiry", "expiry_key", "pure_month"])


def build_overnight(start: date, end: date, target_dates: Iterable[str] | None = None) -> pd.DataFrame:
    # Cash indices use the requested spot series.  Futures
    # remain Yahoo's ES/NQ/YM overnight contracts and are kept separate.
    result = fetch_fred_spot_returns(start, end)
    futures = _fetch_yahoo_returns(start, end, allow_stooq_fallback=True)
    source_details = result.attrs.get("source_details", {}).copy()
    source_details.update(futures.attrs.get("source_details", {}))
    taifex_frames = []
    for commodity, prefix in (("TX", "tx"), ("MTX", "mtx")):
        frame = fetch_taifex_session(start, end, commodity, prefix)
        taifex_frames.append(frame)
    alignment_dates = sorted(
        set(target_dates or [])
        or {date_value for frame in taifex_frames for date_value in frame.get("trade_date", [])}
    )
    for name in US_SPOT_SERIES:
        observation_column = name + "_observation_date"
        if observation_column in result:
            result = align_external_observations(result, alignment_dates, observation_column)
            break
    # Futures have the same US-calendar timing as the cash indices.  Align
    # them to the next Taiwan session as well; otherwise a US holiday leaves
    # the target row empty even though the latest effective futures session is
    # present in the source response.
    if not futures.empty and alignment_dates:
        aligned_futures = []
        for name in US_SYMBOLS:
            change_column = name + "_change"
            if change_column not in futures:
                continue
            observation_column = name + "_observation_date"
            future_frame = futures[["trade_date", change_column]].copy()
            future_frame[observation_column] = future_frame["trade_date"]
            future_frame = align_external_observations(
                future_frame,
                alignment_dates,
                observation_column,
            )
            aligned_futures.append(future_frame[["trade_date", change_column, observation_column]])
        if aligned_futures:
            futures = aligned_futures[0]
            for frame in aligned_futures[1:]:
                futures = futures.merge(frame, on="trade_date", how="outer")
    if not futures.empty:
        result = result.merge(futures, on="trade_date", how="outer")
    for frame in taifex_frames:
        result = frame if result.empty else result.merge(frame, on="trade_date", how="outer")
    result = result.sort_values("trade_date").reset_index(drop=True)
    result.attrs["source_details"] = source_details
    specs = {
        "us_sp500": {"columns": ["us_sp500_change"], "source": source_details.get("us_sp500", {}).get("source", FRED_CSV), "series_id": source_details.get("us_sp500", {}).get("series_id", "SP500"), "unit": "percent change", "observation_date_column": "us_sp500_observation_date", "missing_reason": "US S&P 500 source has no observation for this date or was not yet published"},
        "us_nasdaq": {"columns": ["us_nasdaq_change"], "source": source_details.get("us_nasdaq", {}).get("source", FRED_CSV), "series_id": source_details.get("us_nasdaq", {}).get("series_id", "NASDAQCOM"), "unit": "percent change", "observation_date_column": "us_nasdaq_observation_date", "missing_reason": "US Nasdaq source has no observation for this date or was not yet published"},
        "us_sox": {"columns": ["us_sox_change"], "source": source_details.get("us_sox", {}).get("source", FRED_CSV), "series_id": source_details.get("us_sox", {}).get("series_id", "NASDAQSOX"), "unit": "percent change", "observation_date_column": "us_sox_observation_date", "missing_reason": "US SOX source has no observation for this date or was not yet published"},
        "us_es": {"columns": ["us_es_change"], "source": source_details.get("us_es", {}).get("source", YAHOO_CHART), "series_id": source_details.get("us_es", {}).get("series_id", "ES=F"), "unit": "percent change", "missing_reason": "Yahoo/Stooq ES continuous future has no observation for this date or was not yet published"},
        "us_nq": {"columns": ["us_nq_change"], "source": source_details.get("us_nq", {}).get("source", YAHOO_CHART), "series_id": source_details.get("us_nq", {}).get("series_id", "NQ=F"), "unit": "percent change", "missing_reason": "Yahoo/Stooq NQ continuous future has no observation for this date or was not yet published"},
        "us_ym": {"columns": ["us_ym_change"], "source": source_details.get("us_ym", {}).get("source", YAHOO_CHART), "series_id": source_details.get("us_ym", {}).get("series_id", "YM=F"), "unit": "percent change", "missing_reason": "Yahoo/Stooq YM continuous future has no observation for this date or was not yet published"},
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
            "us_cash": "Yahoo Finance:^GSPC,^IXIC,^SOX (FRED fallback)",
            "us_futures": "Yahoo Finance chart API, Stooq fallback: ES=F/NQ=F/YM=F",
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
