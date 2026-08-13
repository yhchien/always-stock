"""
tests for backend/app/signals/market_snapshot.py

2026-08-13：新增。這個模組原本 OTC 猜測的 5 個 symbol（OTCI/OTC/TWO/TPEx/TPEX）在
FinMind 裡都不存在，DB 從未有對應的 daily_price row，`otc` 永遠回 None——
`/signals` 首頁的「今日市場狀態」因此永遠顯示櫃買為 null。查證後確認正確
FinMind stock_id 是混合大小寫的 "TPEx"，並讓 `fetch_stock_master.py` 把這個
指數佔位列納入 stocks_master，之後每日 ETL 會自動帶出它的收盤價。
"""
from datetime import date

from app.models import DailyPrice
from app.signals.market_snapshot import build_db_market_snapshot


def _seed_price(db, stock_id: str, trade_date: date, close_price: float) -> None:
    db.add(
        DailyPrice(
            stock_id=stock_id,
            trade_date=trade_date,
            close_price=close_price,
            source="finmind",
        )
    )


class TestBuildDbMarketSnapshot:
    def test_otc_uses_correct_tpex_symbol(self, db):
        """正確 symbol 是 'TPEx'（混合大小寫），不是舊版猜測的 OTCI/OTC/TWO/TPEX。"""
        _seed_price(db, "TPEx", date(2026, 8, 10), 250.0)
        _seed_price(db, "TPEx", date(2026, 8, 11), 255.0)
        db.commit()

        snapshot = build_db_market_snapshot(db, date(2026, 8, 11))

        assert snapshot["otc"] is not None
        assert snapshot["otc"]["close"] == 255.0
        assert snapshot["otc"]["change_pct_1d"] == (255.0 - 250.0) / 250.0 * 100.0

    def test_otc_symbol_guesses_no_longer_used(self, db):
        """舊版猜測的 symbol 不該再被查詢；即使 DB 裡剛好有這些代號的資料，也不該被當成 OTC。"""
        for guess in ("OTCI", "OTC", "TWO", "TPEX"):
            _seed_price(db, guess, date(2026, 8, 11), 999.0)
        db.commit()

        snapshot = build_db_market_snapshot(db, date(2026, 8, 11))

        assert snapshot["otc"] is None

    def test_taiex_and_otc_both_present(self, db):
        _seed_price(db, "TAIEX", date(2026, 8, 11), 45000.0)
        _seed_price(db, "TPEx", date(2026, 8, 11), 260.0)
        db.commit()

        snapshot = build_db_market_snapshot(db, date(2026, 8, 11))

        assert snapshot["taiex"]["close"] == 45000.0
        assert snapshot["otc"]["close"] == 260.0

    def test_no_data_returns_none_for_both(self, db):
        snapshot = build_db_market_snapshot(db, date(2026, 8, 11))
        assert snapshot["taiex"] is None
        assert snapshot["otc"] is None
