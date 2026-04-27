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
from app.industry_names import CANONICAL_INDUSTRY_NAME_MAP
from app.models import StockMaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fix_industry_names")

RENAME_MAP = CANONICAL_INDUSTRY_NAME_MAP

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
