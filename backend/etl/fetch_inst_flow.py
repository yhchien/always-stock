"""
從 TWSE T86 取得每日三大法人買賣超資料。

API: https://www.twse.com.tw/rwd/zh/fund/T86
參數: date=YYYYMMDD&selectType=ALL&response=json

欄位順序:
  0:  證券代號
  1:  證券名稱
  2:  外陸資買進股數（不含外資自營商）
  3:  外陸資賣出股數（不含外資自營商）
  4:  外陸資買賣超股數
  5:  外資自營商買進股數
  6:  外資自營商賣出股數
  7:  外資自營商買賣超股數
  8:  投信買進股數
  9:  投信賣出股數
  10: 投信買賣超股數
  11: 自營商買賣超股數（合計）
  12: 自營商買進股數（自行買賣）
  13: 自營商賣出股數（自行買賣）
  14: 自營商買賣超股數（自行買賣）
  15: 自營商買進股數（避險）
  16: 自營商賣出股數（避險）
  17: 自營商買賣超股數（避險）
  18: 三大法人買賣超股數

法人類型對應:
  foreign = 外陸資（index 2, 3, 4）
  trust   = 投信（index 8, 9, 10）
  dealer  = 自營商（buy = 12+15, sell = 13+16, net = 11）

金額估計 = 股數 × 當日收盤價（若無收盤價則為 0）
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
    """將 TWSE 股數字串（含千分位逗號）轉為 float，無效值回傳 0。"""
    s = s.strip().replace(",", "")
    if not s or s == "--":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _load_close_prices(db: Session, trade_date: date) -> Dict[str, float]:
    """從 DB 讀取指定日期的收盤價，回傳 {stock_id: close_price}。"""
    rows = db.query(DailyPrice).filter_by(trade_date=trade_date).all()
    return {r.stock_id: r.close_price for r in rows if r.close_price is not None}


def fetch_and_upsert_inst_flow(db: Session, trade_date: date) -> int:
    """
    從 TWSE T86 抓取指定日三大法人買賣超，寫入 inst_stock_flow。
    每支股票寫入 3 筆（foreign / trust / dealer）。
    金額估計依當日收盤價計算；若無收盤價則為 0。

    Args:
        db: SQLAlchemy session
        trade_date: 交易日期（非交易日 stat != OK，回傳 0）

    Returns:
        寫入（新增或更新）的總筆數
    """
    date_str = trade_date.strftime("%Y%m%d")
    params = {"date": date_str, "selectType": "ALL", "response": "json"}
    url = TWSE_T86_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-dashboard/1.0"})

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if data.get("stat") != "OK":
        logger.warning("TWSE T86 non-OK for %s: %s", date_str, data.get("stat"))
        return 0

    close_prices = _load_close_prices(db, trade_date)
    count = 0

    for row in data.get("data", []):
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
