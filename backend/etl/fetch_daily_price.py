"""
Fetch daily closing prices for all TWSE stocks from STOCK_DAY_ALL.

API: https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL
Params: date=YYYYMMDD&response=json

Column order:
  0: stock_id
  1: stock_name
  2: volume (shares traded)
  3: turnover (NT$)
  4: open
  5: high
  6: low
  7: close_price
  8: price change
  9: transaction count

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

TWSE_STOCK_DAY_ALL_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL"


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
    # The API may still return stat="OK" with the previous trading day's data.
    if trade_date.weekday() >= 5:
        logger.info("Skipping weekend: %s (weekday=%d)", trade_date, trade_date.weekday())
        return 0

    date_str = trade_date.strftime("%Y%m%d")
    params = {"date": date_str, "response": "json"}
    url = TWSE_STOCK_DAY_ALL_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-dashboard/1.0"})

    logger.debug("Fetching daily price for %s", date_str)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("stat") != "OK":
        logger.warning("TWSE STOCK_DAY_ALL non-OK for %s: %s", date_str, data.get("stat"))
        return 0

    count = 0
    for row in data.get("data", []):
        stock_id = row[0].strip()
        close_price = _parse_number(row[7])
        volume = _parse_number(row[2])
        turnover = _parse_number(row[3])

        if close_price is None:
            continue  # suspended or no closing price for the day

        avg_price = (turnover / volume) if (volume and turnover is not None) else None

        existing = (
            db.query(DailyPrice)
            .filter_by(trade_date=trade_date, stock_id=stock_id)
            .first()
        )
        if existing:
            existing.close_price = close_price
            existing.volume = volume
            existing.turnover = turnover
            existing.avg_price = avg_price
        else:
            db.add(DailyPrice(
                trade_date=trade_date,
                stock_id=stock_id,
                close_price=close_price,
                volume=volume,
                turnover=turnover,
                avg_price=avg_price,
            ))
        count += 1

    db.commit()
    logger.info("Daily price upserted: %d records for %s", count, date_str)
    return count
