"""
Fetch daily institutional investor buy/sell data from TWSE T86.

API: https://www.twse.com.tw/rwd/zh/fund/T86
Params: date=YYYYMMDD&selectType=ALL&response=json

Column order:
  0:  stock_id
  1:  stock_name
  2:  foreign buy shares (excl. foreign dealers)
  3:  foreign sell shares (excl. foreign dealers)
  4:  foreign net shares
  5:  foreign dealer buy shares
  6:  foreign dealer sell shares
  7:  foreign dealer net shares
  8:  trust buy shares
  9:  trust sell shares
  10: trust net shares
  11: dealer net shares (total)
  12: dealer self-trading buy shares
  13: dealer self-trading sell shares
  14: dealer self-trading net shares
  15: dealer hedge buy shares
  16: dealer hedge sell shares
  17: dealer hedge net shares
  18: total three-institution net shares

Institution type mapping:
  foreign = foreign investors (index 2, 3, 4)
  trust   = investment trust (index 8, 9, 10)
  dealer  = dealers (buy = 12+15, sell = 13+16, net = 11)

Amount estimate = shares * closing price for the day (0 if price unavailable)
"""
import json
import logging
import urllib.parse
import urllib.request
from datetime import date
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models import DailyPrice, InstStockFlow

logger = logging.getLogger(__name__)

TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

INST_TYPES = ("foreign", "trust", "dealer")


def _parse_shares(s: str) -> float:
    """Parse a TWSE share count string (with thousands commas) to float. Returns 0 for invalid values."""
    s = s.strip().replace(",", "")
    if not s or s == "--":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _load_close_prices(db: Session, trade_date: date) -> Dict[str, float]:
    """Load closing prices for the given date from DB. Returns {stock_id: close_price}."""
    rows = db.query(DailyPrice).filter_by(trade_date=trade_date).all()
    return {r.stock_id: r.close_price for r in rows if r.close_price is not None}


def fetch_and_upsert_inst_flow(db: Session, trade_date: date) -> int:
    """
    Fetch T86 institutional flow for the given date and upsert into inst_stock_flow.
    Each stock produces 3 rows (foreign / trust / dealer).
    Amount estimates are calculated using the day's closing price; defaults to 0 if unavailable.

    Args:
        db: SQLAlchemy session
        trade_date: target date (non-trading days return stat != OK, yielding 0)

    Returns:
        total number of records inserted or updated
    """
    # Skip weekends — TWSE never trades on Saturday/Sunday.
    if trade_date.weekday() >= 5:
        logger.info("Skipping weekend: %s (weekday=%d)", trade_date, trade_date.weekday())
        return 0

    date_str = trade_date.strftime("%Y%m%d")
    params = {"date": date_str, "selectType": "ALL", "response": "json"}
    url = TWSE_T86_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-dashboard/1.0"})

    logger.debug("Fetching institutional flow for %s", date_str)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("stat") != "OK":
        logger.warning("TWSE T86 non-OK for %s: %s", date_str, data.get("stat"))
        return 0

    close_prices = _load_close_prices(db, trade_date)
    logger.debug("Loaded close prices for %d stocks", len(close_prices))
    count = 0

    for row in data.get("data", []):
        if len(row) < 19:
            # Rows with insufficient columns are warrants/ETFs with a different format — skip
            continue
        stock_id = row[0].strip()
        close = close_prices.get(stock_id, 0.0)

        inst_data = {
            "foreign": {
                "buy":  _parse_shares(row[2]),
                "sell": _parse_shares(row[3]),
                "net":  _parse_shares(row[4]),
            },
            "trust": {
                "buy":  _parse_shares(row[8]),
                "sell": _parse_shares(row[9]),
                "net":  _parse_shares(row[10]),
            },
            "dealer": {
                "buy":  _parse_shares(row[12]) + _parse_shares(row[15]),
                "sell": _parse_shares(row[13]) + _parse_shares(row[16]),
                "net":  _parse_shares(row[11]),
            },
        }

        for inst_type, shares in inst_data.items():
            buy_shares  = shares["buy"]
            sell_shares = shares["sell"]
            net_shares  = shares["net"]

            buy_amount_est  = buy_shares  * close
            sell_amount_est = sell_shares * close
            net_amount_est  = net_shares  * close

            existing = (
                db.query(InstStockFlow)
                .filter_by(trade_date=trade_date, stock_id=stock_id, inst_type=inst_type)
                .first()
            )
            if existing:
                existing.buy_shares      = buy_shares
                existing.sell_shares     = sell_shares
                existing.net_shares      = net_shares
                existing.buy_amount_est  = buy_amount_est
                existing.sell_amount_est = sell_amount_est
                existing.net_amount_est  = net_amount_est
            else:
                db.add(InstStockFlow(
                    trade_date       = trade_date,
                    stock_id         = stock_id,
                    inst_type        = inst_type,
                    buy_shares       = buy_shares,
                    sell_shares      = sell_shares,
                    net_shares       = net_shares,
                    buy_amount_est   = buy_amount_est,
                    sell_amount_est  = sell_amount_est,
                    net_amount_est   = net_amount_est,
                ))
            count += 1

    db.commit()
    logger.info("Inst flow upserted: %d records for %s", count, date_str)
    return count
