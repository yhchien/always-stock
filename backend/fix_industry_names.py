"""
修正 stocks_master.industry_name：
  1. 合併重複命名（半導體業 → 半導體 等）
  2. 合入邊界分類（金融保險 → 金融 等）

執行後需重跑 rebuild_industry_flow.py --skip-master
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.database import SessionLocal
from app.models import StockMaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fix_industry_names")

RENAME_MAP = {
    # 明確重複（命名正規化）
    "半導體業":         "半導體",
    "汽車工業":         "汽車",
    "造紙工業":         "造紙",
    "紡織纖維":         "紡織",
    "電腦及週邊設備業": "電腦及週邊設備",
    "通信網路業":       "通信網路",
    "創新版股票":       "創新板股票",   # 錯字修正
    # 邊界合入
    "金融保險":         "金融",
    "塑膠工業":         "石化及塑橡膠",
    "航運業":           "交通運輸及航運",
    "其他電子業":       "其他",
    "電子通路業":       "其他",
    "電子零組件業":     "其他",
    "電器電纜":         "電機機械",
    "運動休閒":         "休閒娛樂",
    "運動科技":         "休閒娛樂",
    "生技醫療業":       "醫療器材",
}

def main():
    db = SessionLocal()
    try:
        updated = 0
        for old_name, new_name in RENAME_MAP.items():
            stocks = db.query(StockMaster).filter(StockMaster.industry_name == old_name).all()
            for s in stocks:
                s.industry_name = new_name
                updated += 1
                logger.debug("  %s: %s → %s", s.stock_id, old_name, new_name)
            if stocks:
                logger.info("%-18s → %-18s  (%d 支)", old_name, new_name, len(stocks))

        db.commit()
        logger.info("完成，共更新 %d 支股票", updated)
        logger.info("請執行：python3 rebuild_industry_flow.py --skip-master")
    finally:
        db.close()

if __name__ == "__main__":
    main()
