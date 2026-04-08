"""
Fetch daily closing prices for all TWSE stocks from MI_INDEX (historical).

API: https://www.twse.com.tw/exchangeReport/MI_INDEX
Params: date=YYYYMMDD&response=json&type=ALLBUT0999

Response: { stat, tables: [...] }
  tables[8] = "每日收盤行情" with fields:
    0: stock_id (證券代號)
    1: stock_name (證券名稱)
    2: volume in shares (成交股數)
    3: transaction count (成交筆數)
    4: turnover in NT$ (成交金額)
    5: open
    6: high
    7: low
    8: close_price (收盤價)
    9: change direction HTML tag
   10: change amount
   ...

avg_price = turnover / volume (volume-weighted average price, NT$)
"""
import json
import logging
import urllib.parse
import urllib.request
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DailyPrice

logger = logging.getLogger(__name__)

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"

# Stock data table index within the MI_INDEX response
_STOCK_TABLE_INDEX = 8


def _parse_number(s: str) -> Optional[float]:
    """Parse a TWSE number string (with thousands commas) to float. Returns None for '--' or empty."""
    s = s.strip().replace(",", "")
    if not s or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_and_upsert_daily_price(db: Session, trade_date: date) -> int:
    """
    Fetch all TWSE closing prices for the given trade date and upsert into DB.

    Args:
        db: SQLAlchemy session
        trade_date: target date (non-trading days return stat != 'OK', yielding 0)

    Returns:
        number of records inserted or updated
    """
    # Skip weekends — TWSE never trades on Saturday/Sunday.
    if trade_date.weekday() >= 5:
        logger.info("Skipping weekend: %s (weekday=%d)", trade_date, trade_date.weekday())
        return 0

    date_str = trade_date.strftime("%Y%m%d")
    params = {"date": date_str, "response": "json", "type": "ALLBUT0999"}
    url = TWSE_MI_INDEX_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "always-stock/1.0"})

    logger.debug("Fetching daily price for %s", date_str)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("stat") != "OK":
        logger.warning("TWSE MI_INDEX non-OK for %s: %s", date_str, data.get("stat"))
        return 0

    # Extract stock data from the response tables
    tables = data.get("tables", [])
    if len(tables) <= _STOCK_TABLE_INDEX:
        logger.warning("MI_INDEX response has only %d tables for %s, expected > %d",
                        len(tables), date_str, _STOCK_TABLE_INDEX)
        return 0

    stock_table = tables[_STOCK_TABLE_INDEX]
    rows = stock_table.get("data", [])

    count = 0
    for row in rows:
        if len(row) < 9:
            continue

        stock_id = row[0].strip()
        open_price = _parse_number(row[5])
        high_price = _parse_number(row[6])
        low_price = _parse_number(row[7])
        close_price = _parse_number(row[8])
        volume = _parse_number(row[2])
        turnover = _parse_number(row[4])

        if close_price is None:
            continue  # suspended or no closing price for the day

        avg_price = (turnover / volume) if (volume and turnover is not None) else None

        existing = (
            db.query(DailyPrice)
            .filter_by(trade_date=trade_date, stock_id=stock_id)
            .first()
        )
        if existing:
            existing.open_price = open_price
            existing.high_price = high_price
            existing.low_price = low_price
            existing.close_price = close_price
            existing.volume = volume
            existing.turnover = turnover
            existing.avg_price = avg_price
        else:
            db.add(DailyPrice(
                trade_date=trade_date,
                stock_id=stock_id,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=volume,
                turnover=turnover,
                avg_price=avg_price,
            ))
        count += 1

    db.commit()
    logger.info("Daily price upserted: %d records for %s", count, date_str)
    return count
