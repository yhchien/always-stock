"""Download the point-in-time data used by the Taiwan market calendar.

Examples
--------
    python3 tools/download_market_calendar_data.py \
      --start 2026-07-15 --end 2026-11-15 \
      --output docs/reports/market_calendar_2026-07-15_2026-11-15.csv

Sources are deliberately explicit in the output metadata columns:
TAIEX/stock institutional data comes from the deployed always-stock API;
Taiwan VIX and MTX are from TAIFEX; US VIX is from Cboe and US 10-year
yield is from the US Treasury official XML feed;
USD/TWD is the CBC NTD/USD interbank close; gold is the Yahoo Finance COMEX
continuous front-month contract (GC=F).  Every output row includes source,
observation-date, fetch-time, and missing-status metadata.
"""

from __future__ import annotations

import argparse
import bisect
import calendar
import csv
import io
import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests


TAIEX_API = "https://always-stock.onrender.com/api/stocks/TAIEX/history"
INDUSTRIES_API = "https://always-stock.onrender.com/api/industries"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
CBOE_VIX_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
TREASURY_XML = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
TAIFEX_VIX = "https://www.taifex.com.tw/cht/7/getVixData"
TAIFEX_VIX_MONTH = "https://www.taifex.com.tw/file/taifex/Dailydownload/vix/log2data/{month}new.txt"
TAIFEX_MTX = "https://www.taifex.com.tw/enl/eng3/futContractsDateDown"
TAIFEX_DAILY = "https://www.taifex.com.tw/cht/3/futDataDown"
CBC_USD_TWD = "https://www.cbc.gov.tw/public/data/OpenData/%E5%A4%96%E5%8C%AF%E5%B1%80/FTDOpenData015.csv"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# FRED occasionally accepts the connection but never sends the CSV body.  A
# short bounded read timeout keeps the 06:30 action from appearing hung; the
# Yahoo fallback below remains available after these attempts.
FRED_RETRIES = 2
FRED_TIMEOUT = (10, 30)
TAIFEX_RETRIES = 3
TAIFEX_TIMEOUT = (10, 90)
YAHOO_FALLBACK_SYMBOLS = {
    "VIXCLS": "^VIX",
    # Yahoo publishes ^TNX in percentage points (for example 4.30 means
    # 4.30%), which matches the DGS10 unit used by this panel.
    "DGS10": "^TNX",
    "SP500": "^GSPC",
    "NASDAQCOM": "^IXIC",
    "NASDAQSOX": "^SOX",
}
logger = logging.getLogger(__name__)


def _fetched_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(frame: pd.DataFrame) -> pd.DataFrame:
    frame.attrs["fetched_at"] = _fetched_at()
    return frame


def attach_source_metadata(
    frame: pd.DataFrame,
    specs: Dict[str, Dict[str, object]],
    fetched_at: Optional[str] = None,
) -> pd.DataFrame:
    """Attach row-level source/observation/freshness metadata.

    Missing values remain missing.  The metadata explicitly records that the
    source returned no observation for that requested date; it is never
    interpreted as a zero or a forward-filled value.
    """
    fetched_at = fetched_at or _fetched_at()
    records = []
    for _, row in frame.iterrows():
        row_sources = {}
        for key, spec in specs.items():
            columns = list(spec.get("columns", []))
            available = bool(columns) and all(
                column in row and pd.notna(row[column]) for column in columns
            )
            observation_column = spec.get("observation_date_column")
            observed_date = row.get(observation_column) if observation_column else row.get("trade_date")
            row_sources[key] = {
                "source": spec["source"],
                "series_id": spec.get("series_id"),
                "unit": spec.get("unit"),
                "session": spec.get("session"),
                "observed_date": str(observed_date)[:10] if available and pd.notna(observed_date) else None,
                "fetched_at": spec.get("fetched_at") or fetched_at,
                "status": "available" if available else "missing",
                "missing_reason": None if available else spec.get(
                    "missing_reason", "no value returned for requested observation date"
                ),
            }
        records.append(row_sources)
    result = frame.copy()
    result["source_metadata"] = records
    return result


def _get_json(url: str, params: Optional[Dict[str, str]] = None) -> dict:
    response = requests.get(url, params=params, timeout=45, headers={"User-Agent": "always-stock-market-calendar/1.0"})
    response.raise_for_status()
    return response.json()


def fetch_fred_csv(series_id: str, start: date, end: date) -> str:
    """Fetch a FRED CSV with bounded retries for transient read timeouts."""
    params = {"id": series_id, "cosd": str(start), "coed": str(end)}
    last_error: Optional[Exception] = None
    for attempt in range(1, FRED_RETRIES + 1):
        try:
            response = requests.get(
                FRED_CSV,
                params=params,
                timeout=FRED_TIMEOUT,
                headers={"User-Agent": "always-stock-market-calendar/1.0"},
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt < FRED_RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(
        f"FRED {series_id} request failed after {FRED_RETRIES} attempts: {last_error}"
    ) from last_error


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
    return _stamp(frame[["trade_date", "open_price", "high_price", "low_price", "close_price"]])


def fetch_institutional_flows(dates: Iterable[date]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for current in dates:
        try:
            payload = _get_json(INDUSTRIES_API, {"date": str(current)})
            items = payload if isinstance(payload, list) else payload.get("data", [])
        except requests.RequestException as exc:
            logger.warning("institutional flow unavailable for %s: %s", current, exc)
            items = []
        aggregate = {"foreign_net_amount": float("nan"), "trust_net_amount": float("nan"), "dealer_net_amount": float("nan")}
        if items:
            aggregate = {"foreign_net_amount": 0.0, "trust_net_amount": 0.0, "dealer_net_amount": 0.0}
        for item in items:
            for key in aggregate:
                aggregate[key] += float(item.get(key) or 0.0)
        aggregate["trade_date"] = str(current)
        aggregate["inst_total_net_amount"] = (
            sum(aggregate[key] for key in aggregate if key.endswith("net_amount"))
            if items
            else float("nan")
        )
        rows.append(aggregate)
        time.sleep(0.05)
    return _stamp(pd.DataFrame(rows, columns=[
        "foreign_net_amount", "trust_net_amount", "dealer_net_amount", "trade_date", "inst_total_net_amount"
    ]))


def fetch_fred(series_id: str, start: date, end: date, output_name: str) -> pd.DataFrame:
    source_url = FRED_CSV
    source_series_id = series_id
    try:
        csv_text = fetch_fred_csv(series_id, start, end)
        frame = pd.read_csv(io.StringIO(csv_text), na_values=[".", ""])
    except RuntimeError:
        fallback_symbol = YAHOO_FALLBACK_SYMBOLS.get(series_id)
        if not fallback_symbol:
            raise
        logger.warning(
            "FRED %s unavailable; using Yahoo Finance fallback %s for %s",
            series_id,
            fallback_symbol,
            output_name,
        )
        frame = fetch_yahoo(fallback_symbol, output_name)
        frame = frame[
            (frame["trade_date"] >= str(start)) & (frame["trade_date"] <= str(end))
        ].copy()
        frame.attrs["source_url"] = YAHOO_CHART.format(symbol=fallback_symbol)
        frame.attrs["series_id"] = fallback_symbol
        return frame
    frame = frame.rename(columns={"observation_date": "trade_date", series_id: output_name})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame[output_name] = pd.to_numeric(frame[output_name], errors="coerce")
    frame.attrs["source_url"] = source_url
    frame.attrs["series_id"] = source_series_id
    return _stamp(frame[["trade_date", output_name]])


def fetch_us_vix(start: date, end: date) -> pd.DataFrame:
    """Fetch VIX from Cboe's daily-history file, then use FRED/Yahoo fallbacks."""
    try:
        response = requests.get(
            CBOE_VIX_CSV,
            timeout=(10, 30),
            headers={"User-Agent": "always-stock-market-calendar/1.0"},
        )
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
        date_column = next(column for column in frame.columns if str(column).strip().lower() == "date")
        close_column = next(column for column in frame.columns if str(column).strip().lower() == "close")
        result = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(frame[date_column], errors="coerce").dt.strftime("%Y-%m-%d"),
                "us_vix": pd.to_numeric(frame[close_column], errors="coerce"),
            }
        ).dropna(subset=["trade_date", "us_vix"])
        result = result[
            (result["trade_date"] >= str(start)) & (result["trade_date"] <= str(end))
        ].copy()
        result.attrs["source_url"] = CBOE_VIX_CSV
        result.attrs["series_id"] = "VIX"
        return _stamp(result)
    except (requests.RequestException, ValueError, StopIteration, pd.errors.ParserError) as exc:
        logger.warning("Cboe VIX unavailable; trying FRED/Yahoo fallbacks: %s", exc)
        return fetch_fred("VIXCLS", start, end, "us_vix")


def fetch_treasury_10y(start: date, end: date) -> pd.DataFrame:
    """Fetch the official US Treasury daily 10-year par yield curve value."""
    rows = []
    try:
        for year in range(start.year, end.year + 1):
            response = requests.get(
                TREASURY_XML,
                params={"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(year)},
                timeout=(10, 30),
                headers={"User-Agent": "always-stock-market-calendar/1.0"},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for entry in root.iter():
                if not str(entry.tag).lower().endswith("entry"):
                    continue
                values = {
                    str(child.tag).split("}")[-1].upper(): (child.text or "").strip()
                    for child in entry.iter()
                }
                observed = pd.to_datetime(values.get("NEW_DATE"), errors="coerce")
                value = pd.to_numeric(values.get("BC_10YEAR"), errors="coerce")
                if pd.isna(observed) or pd.isna(value):
                    continue
                rows.append(
                    {
                        "trade_date": observed.strftime("%Y-%m-%d"),
                        "us10y": float(value),
                    }
                )
        result = pd.DataFrame(rows, columns=["trade_date", "us10y"])
        result = result[
            (result["trade_date"] >= str(start)) & (result["trade_date"] <= str(end))
        ].drop_duplicates("trade_date")
        if result.empty:
            raise RuntimeError("Treasury XML returned no 10-year observations")
        result.attrs["source_url"] = TREASURY_XML
        result.attrs["series_id"] = "BC_10YEAR"
        return _stamp(result.reset_index(drop=True))
    except (requests.RequestException, ET.ParseError, RuntimeError, ValueError) as exc:
        logger.warning("US Treasury 10Y unavailable; trying FRED/Yahoo fallbacks: %s", exc)
        return fetch_fred("DGS10", start, end, "us10y")


def align_external_observations(
    frame: pd.DataFrame,
    target_dates: Iterable[str],
    observation_date_column: str,
) -> pd.DataFrame:
    """Attach a foreign-market observation to the next Taiwan session.

    FRED dates are US calendar dates.  A US close on Friday is therefore an
    input to Monday's Taiwan open, not Friday afternoon's Taiwan prediction.
    The original observation date is retained for provenance and no value is
    forward-filled when there is no following Taiwan session.
    """
    result = frame.copy()
    targets = sorted(pd.to_datetime(list(target_dates), errors="coerce").dropna())
    if not targets or result.empty:
        result[observation_date_column] = result.get("trade_date")
        return result
    observed = pd.to_datetime(result["trade_date"], errors="coerce")
    result[observation_date_column] = observed.dt.strftime("%Y-%m-%d")
    result["trade_date"] = [
        targets[bisect.bisect_right(targets, value)] if pd.notna(value) and bisect.bisect_right(targets, value) < len(targets) else pd.NaT
        for value in observed
    ]
    result = result.dropna(subset=["trade_date"]).copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y-%m-%d")
    return _stamp(result)


def fetch_cbc_usd_twd(start: date, end: date) -> pd.DataFrame:
    """Fetch the CBC NTD/USD interbank closing rate.

    The existing strategy field is explicitly USD/TWD (NTD per USD), so this
    uses the CBC's NTD/USD close rather than FRED's inverse DEXTAUS series.
    """
    response = requests.get(
        CBC_USD_TWD,
        timeout=45,
        headers={"User-Agent": "always-stock-market-calendar/1.0"},
    )
    response.raise_for_status()
    last_error = None
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            frame = pd.read_csv(io.BytesIO(response.content), encoding=encoding)
            break
        except Exception as exc:  # pragma: no cover - depends on remote encoding
            last_error = exc
    else:
        raise RuntimeError(f"unable to decode CBC USD/TWD CSV: {last_error}")
    frame.columns = [str(column).strip() for column in frame.columns]
    date_column = next((column for column in frame.columns if "日期" in column or "date" in column.lower()), frame.columns[0])
    value_column = next((column for column in frame.columns if "NTD/USD" in column.upper()), None)
    if value_column is None:
        value_column = frame.columns[1]
    result = pd.DataFrame(
        {
            # CBC publishes this file as YYYYMMDD, not an ISO date string.
            "trade_date": pd.to_datetime(
                frame[date_column].astype(str).str.strip(),
                format="%Y%m%d",
                errors="coerce",
            ).dt.strftime("%Y-%m-%d"),
            "usd_twd": pd.to_numeric(
                frame[value_column].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            ),
        }
    )
    result = result.dropna(subset=["trade_date"])
    result = result[
        (result["trade_date"] >= str(start)) & (result["trade_date"] <= str(end))
    ]
    return _stamp(result[["trade_date", "usd_twd"]])


def fetch_yahoo(symbol: str, output_name: str) -> pd.DataFrame:
    try:
        response = requests.get(
            YAHOO_CHART.format(symbol=symbol),
            params={"range": "1y", "interval": "1d", "events": "history"},
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
    except (requests.RequestException, KeyError, TypeError, IndexError) as exc:
        logger.warning("Yahoo %s unavailable; leaving %s missing: %s", symbol, output_name, exc)
        return _stamp(pd.DataFrame(columns=["trade_date", output_name]))
    quote = result["indicators"]["quote"][0]
    rows = []
    for timestamp, close in zip(result.get("timestamp", []), quote.get("close", [])):
        if close is None:
            continue
        rows.append({"trade_date": pd.to_datetime(timestamp, unit="s", utc=True).strftime("%Y-%m-%d"), output_name: float(close)})
    return _stamp(pd.DataFrame(rows, columns=["trade_date", output_name]))


def fetch_taiwan_vix(trading_dates: Iterable[str]) -> pd.DataFrame:
    requested = set(trading_dates)
    rows = []
    months = sorted({trade_date[:7].replace("-", "") for trade_date in requested})
    for month in months:
        response = None
        for attempt in range(1, TAIFEX_RETRIES + 1):
            try:
                response = requests.get(
                    TAIFEX_VIX_MONTH.format(month=month),
                    timeout=TAIFEX_TIMEOUT,
                    headers={"User-Agent": "always-stock-market-calendar/1.0"},
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                logger.warning("TAIFEX Taiwan VIX %s attempt %s/%s failed: %s", month, attempt, TAIFEX_RETRIES, exc)
                if attempt < TAIFEX_RETRIES:
                    time.sleep(2 * attempt)
        if response is None or response.status_code >= 400:
            logger.warning("TAIFEX Taiwan VIX %s unavailable; leaving factor missing", month)
            continue
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
    return _stamp(pd.DataFrame(rows, columns=["trade_date", "tw_vix"]))


def _number(value: object) -> Optional[float]:
    if value is None or str(value).strip() in {"", "-"}:
        return None
    return float(str(value).replace(",", "").replace("%", ""))


def fetch_mtx_general_session(start: date, end: date) -> pd.DataFrame:
    """Fetch MTX total open interest from the general session market table.

    This is deliberately separate from the institutional long/short table.
    The latter is the strategy's three-institution aggregate and is not
    silently replaced by this total-market OI value.
    """
    response = None
    for attempt in range(1, TAIFEX_RETRIES + 1):
        try:
            response = requests.post(
                TAIFEX_DAILY,
                data={
                    "queryStartDate": start.strftime("%Y/%m/%d"),
                    "queryEndDate": end.strftime("%Y/%m/%d"),
                    "commodity_id": "MTX",
                    "commodity_id2": "",
                    "down_type": "1",
                },
                timeout=TAIFEX_TIMEOUT,
                headers={"User-Agent": "always-stock-market-calendar/1.0"},
            )
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            logger.warning("TAIFEX MTX general session attempt %s/%s failed: %s", attempt, TAIFEX_RETRIES, exc)
            if attempt < TAIFEX_RETRIES:
                time.sleep(2 * attempt)
    if response is None or response.status_code >= 400:
        logger.warning("TAIFEX MTX general session unavailable; leaving factor missing")
        return _stamp(pd.DataFrame(columns=["trade_date", "mtx_day_oi"]))
    reader = csv.DictReader(io.StringIO(response.content.decode("cp950", errors="replace")))
    rows = []
    for row in reader:
        if str(row.get("交易時段", "")).strip() != "一般":
            continue
        trade_date = pd.to_datetime(row.get("交易日期"), errors="coerce")
        if pd.isna(trade_date):
            continue
        expiry = str(row.get("到期月份(週別)", "")).strip()
        expiry_key = re.search(r"\d{6}", expiry)
        if not expiry_key:
            continue
        rows.append(
            {
                "trade_date": trade_date.strftime("%Y-%m-%d"),
                "expiry_key": expiry_key.group(0),
                "pure_month": bool(re.fullmatch(r"\d{6}", expiry)),
                # TAIFEX's current Chinese export calls this field
                # ``未沖銷契約數``; older wording used ``未沖銷契約量``.
                "mtx_day_oi": _number(
                    row.get("未沖銷契約數") or row.get("未沖銷契約量")
                ),
            }
        )
    if not rows:
        return _stamp(pd.DataFrame(columns=["trade_date", "mtx_day_oi"]))
    frame = pd.DataFrame(rows).sort_values(
        ["trade_date", "expiry_key", "pure_month"], ascending=[True, True, False]
    )
    frame = frame.drop_duplicates("trade_date", keep="first").drop(columns=["expiry_key", "pure_month"])
    return _stamp(frame)


def _month_ranges(start: date, end: date) -> Iterable[tuple[date, date]]:
    current = date(start.year, start.month, 1)
    while current <= end:
        month_end = date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
        yield max(start, current), min(end, month_end)
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)


def fetch_mtx(start: date, end: date) -> pd.DataFrame:
    rows = []
    # TAIFEX rejects a query whose lastDate is a not-yet-published date with a
    # 200 HTML error page.  Query completed months separately and never ask for
    # today, because the daily action runs before today's settlement data is
    # available.
    available_end = min(end, date.today() - timedelta(days=1))
    if start > available_end:
        return _stamp(pd.DataFrame(columns=["trade_date", "mtx_oi_long", "mtx_oi_short", "mtx_oi_net"]))
    for range_start, range_end in _month_ranges(start, available_end):
        response = None
        for attempt in range(1, TAIFEX_RETRIES + 1):
            try:
                response = requests.post(
                    TAIFEX_MTX,
                    data={
                        "queryStartDate": range_start.strftime("%Y/%m/%d"),
                        "queryEndDate": range_end.strftime("%Y/%m/%d"),
                        "commodityId": "MXF",
                        "firstDate": "2023-11-24 00:00:00.0",
                        "lastDate": str(range_end) + " 00:00:00.0",
                    },
                    timeout=TAIFEX_TIMEOUT,
                    headers={"User-Agent": "always-stock-market-calendar/1.0"},
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                logger.warning("TAIFEX MTX institutional %s-%s attempt %s/%s failed: %s", range_start, range_end, attempt, TAIFEX_RETRIES, exc)
                if attempt < TAIFEX_RETRIES:
                    time.sleep(2 * attempt)
        if response is None or response.status_code >= 400:
            logger.warning("TAIFEX MTX institutional %s-%s unavailable; leaving factors missing", range_start, range_end)
            continue
        text = response.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        if not {"Date", "Contract Code", "Item"}.issubset(reader.fieldnames or set()):
            # A 200 HTML error page is a source-level missing result, not a
            # reason to turn the whole panel into zeros.
            continue
        for row in reader:
            if row.get("Contract Code") not in {"MTX", "小型臺指期貨"}:
                continue
            item = row.get("Item", "")
            if item not in {"Dealers", "Investment Trust Companies", "FINI"}:
                continue
            observed = pd.to_datetime(row.get("Date"), format="%Y/%m/%d", errors="coerce")
            if pd.isna(observed):
                continue
            rows.append({
                "trade_date": observed.strftime("%Y-%m-%d"),
                "mtx_oi_long": _number(row.get("Open Interest (Long)")),
                "mtx_oi_short": _number(row.get("Open Interest (Short)")),
                "mtx_oi_net": _number(row.get("Open Interest (Net)")),
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return _stamp(pd.DataFrame(columns=["trade_date", "mtx_oi_long", "mtx_oi_short", "mtx_oi_net"]))
    result = frame.groupby("trade_date", as_index=False).sum(numeric_only=True)
    result["mtx_oi_net"] = result["mtx_oi_long"] - result["mtx_oi_short"]
    return _stamp(result)


def build_panel(start: date, end: date) -> pd.DataFrame:
    taiex = fetch_taiex(start, end)
    dates = taiex["trade_date"].astype(str).tolist()
    panel = taiex.copy()
    datasets = {
        "institutional": fetch_institutional_flows(pd.to_datetime(dates).date),
        "tw_vix": fetch_taiwan_vix(dates),
        "us_vix": fetch_us_vix(start, end),
        "us10y": fetch_treasury_10y(start, end),
        "usd_twd": fetch_cbc_usd_twd(start, end),
        "gold": fetch_yahoo("GC%3DF", "gold"),
        "mtx_institutional": fetch_mtx(start, end),
        "mtx_general": fetch_mtx_general_session(start, end),
    }
    target_dates = taiex["trade_date"].astype(str).tolist()
    for name, observation_column in (
        ("us_vix", "us_vix_observation_date"),
        ("us10y", "us10y_observation_date"),
    ):
        datasets[name] = align_external_observations(
            datasets[name], target_dates, observation_column
        )
    for dataset in datasets.values():
        panel = panel.merge(dataset, on="trade_date", how="left")
    panel = panel.sort_values("trade_date").reset_index(drop=True)
    specs = {
        "taiex": {"columns": ["close_price"], "source": TAIEX_API, "unit": "index points", "fetched_at": taiex.attrs.get("fetched_at")},
        "institutional": {"columns": ["inst_total_net_amount"], "source": INDUSTRIES_API, "unit": "TWD", "fetched_at": datasets["institutional"].attrs.get("fetched_at"), "missing_reason": "always-stock institutional endpoint returned no rows for this Taiwan date"},
        "tw_vix": {"columns": ["tw_vix"], "source": TAIFEX_VIX_MONTH, "series_id": "TAIFEX Taiwan VIX", "unit": "index points", "fetched_at": datasets["tw_vix"].attrs.get("fetched_at"), "missing_reason": "TAIFEX Taiwan VIX has no observation for this date"},
        "us_vix": {"columns": ["us_vix"], "source": datasets["us_vix"].attrs.get("source_url", CBOE_VIX_CSV), "series_id": datasets["us_vix"].attrs.get("series_id", "VIX"), "unit": "index points", "observation_date_column": "us_vix_observation_date", "fetched_at": datasets["us_vix"].attrs.get("fetched_at"), "missing_reason": "US VIX source has no observation for this date or was not yet published"},
        "us10y": {"columns": ["us10y"], "source": datasets["us10y"].attrs.get("source_url", TREASURY_XML), "series_id": datasets["us10y"].attrs.get("series_id", "BC_10YEAR"), "unit": "percent", "observation_date_column": "us10y_observation_date", "fetched_at": datasets["us10y"].attrs.get("fetched_at"), "missing_reason": "US 10-year yield source has no observation for this date or was not yet published"},
        "usd_twd": {"columns": ["usd_twd"], "source": CBC_USD_TWD, "series_id": "NTD/USD", "unit": "TWD per USD", "fetched_at": datasets["usd_twd"].attrs.get("fetched_at"), "missing_reason": "CBC has no Taiwan-business-day NTD/USD close for this date or was not yet published"},
        "gold": {"columns": ["gold"], "source": "Yahoo Finance chart API", "series_id": "GC=F", "unit": "USD per troy ounce", "fetched_at": datasets["gold"].attrs.get("fetched_at"), "missing_reason": "Yahoo GC=F has no observation for this date or was not yet published"},
        "mtx_institutional": {"columns": ["mtx_oi_long", "mtx_oi_short"], "source": TAIFEX_MTX, "series_id": "MTX", "unit": "contracts", "session": "institutional OI table; not session-mixed with night OHLC", "fetched_at": datasets["mtx_institutional"].attrs.get("fetched_at"), "missing_reason": "TAIFEX MTX institutional OI table returned no long/short positions for this date"},
        "mtx_general": {"columns": ["mtx_day_oi"], "source": TAIFEX_DAILY, "series_id": "MTX", "unit": "contracts", "session": "一般交易時段", "fetched_at": datasets["mtx_general"].attrs.get("fetched_at"), "missing_reason": "TAIFEX MTX general-session table returned no total OI for this date"},
    }
    return attach_source_metadata(panel, specs)


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
    output_frame = frame.copy()
    output_frame["source_metadata"] = output_frame["source_metadata"].map(
        lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    output_frame.to_csv(args.output, index=False)
    metadata = {
        "start": str(args.start),
        "end": str(args.end),
        "rows": len(frame),
        "sources": {
            "taiex": TAIEX_API,
            "institutional": INDUSTRIES_API,
            "taiwan_vix": TAIFEX_VIX,
            "mtx": TAIFEX_MTX,
            "mtx_general_session": TAIFEX_DAILY,
            "us_vix": "Cboe VIX daily history CSV (FRED/Yahoo fallback)",
            "us10y": "US Treasury official XML: BC_10YEAR (FRED/Yahoo fallback)",
            "usd_twd": CBC_USD_TWD,
            "gold": "Yahoo Finance:GC=F",
        },
        "definitions": {
            "institutional": "foreign + investment trust + dealer, summed as three-institution total",
            "mtx_institutional_oi": "MTX institutional table Open Interest Long/Short; strategy uses three-institution aggregate",
            "mtx_general_session_oi": "MTX market table general-session open interest for the nearest pure monthly contract, separate from institutional OI",
            "usd_twd": "CBC NTD/USD close, stored as TWD per USD; not FRED DEXTAUS inverse series",
            "us10y_change": "percentage-point difference; us10y_change_5d is difference across five valid observations",
            "foreign_observation_alignment": "FRED US observations are attached to the next Taiwan trading session; original observation date is retained in source_metadata",
        },
    }
    args.output.with_suffix(".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
