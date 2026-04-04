"""
從 TWSE STOCK_DAY_ALL 取得每日全市場收盤價。

API: https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL
參數: date=YYYYMMDD&response=json

欄位順序:
  0: 證券代號
  1: 證券名稱
  2: 成交股數   → volume
  3: 成交金額   → turnover（NT$）
  4: 開盤價
  5: 最高價
  6: 最低價
  7: 收盤價     → close_price
  8: 漲跌價差
  9: 成交筆數

avg_price = 成交金額 / 成交股數（加權平均成交價，NT$）
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
    """將 TWSE 數字字串（含千分位逗號）轉為 float，無效值（空字串或 '--'）回傳 None。"""
    s = s.strip().replace(",", "")
    if not s or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_and_upsert_daily_price(db: Session, trade_date: date) -> int:
    """
    從 TWSE 抓取指定交易日的全市場收盤資料並寫入 DB。

    Args:
        db: SQLAlchemy session
        trade_date: 交易日期（非交易日 TWSE 回傳 stat != 'OK'，回傳 0）

    Returns:
        寫入（新增或更新）的筆數
    """
    date_str = trade_date.strftime("%Y%m%d")
    params = {"date": date_str, "response": "json"}
    url = TWSE_STOCK_DAY_ALL_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-dashboard/1.0"})

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
            continue  # 停牌或當日無收盤價

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
