from datetime import date

import pandas as pd

from app.models import DailyPrice, InstStockFlow
from etl.finmind_inst_flow_sdk import fetch_and_upsert_inst_flow_finmind_sdk


class FakeFinMindSDKClient:
    def fetch_inst_investors_buysell_dataset(self, start_date, end_date):
        assert start_date == "2026-04-21"
        assert end_date == "2026-04-21"
        return pd.DataFrame(
            [
                {
                    "date": "2026-04-21",
                    "stock_id": "2330",
                    "name": "Foreign_Investor",
                    "buy": 1000,
                    "sell": 400,
                },
                {
                    "date": "2026-04-21",
                    "stock_id": "2330",
                    "name": "Foreign_Dealer_Self",
                    "buy": 200,
                    "sell": 50,
                },
            ]
        )


def test_inst_flow_writes_amount_est_with_close_price(db):
    trade_date = date(2026, 4, 21)
    db.add(
        DailyPrice(
            trade_date=trade_date,
            stock_id="2330",
            close_price=850.0,
            volume=1000000,
            turnover=850000000,
            avg_price=850.0,
        )
    )
    db.commit()

    result = fetch_and_upsert_inst_flow_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(),
    )

    assert result["status"] == "ok"
    assert result["upserted"] == 1

    rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").one()
    assert rec.buy_shares == 1200.0
    assert rec.sell_shares == 450.0
    assert rec.net_shares == 750.0
    assert rec.buy_amount_est == 1200.0 * 850.0
    assert rec.sell_amount_est == 450.0 * 850.0
    assert rec.net_amount_est == 750.0 * 850.0
    assert rec.source == "finmind"


def test_inst_flow_amount_est_falls_back_to_zero_without_close_price(db):
    trade_date = date(2026, 4, 21)

    result = fetch_and_upsert_inst_flow_finmind_sdk(
        db=db,
        stock_ids=["2330"],
        start_date=trade_date,
        end_date=trade_date,
        client=FakeFinMindSDKClient(),
    )

    assert result["status"] == "ok"

    rec = db.query(InstStockFlow).filter_by(stock_id="2330", inst_type="foreign").one()
    assert rec.buy_amount_est == 0.0
    assert rec.sell_amount_est == 0.0
    assert rec.net_amount_est == 0.0
