import pandas as pd

from tools.download_market_calendar_margin import aggregate_margin


def test_aggregate_margin_keeps_financing_buy_and_market_balances():
    source = pd.DataFrame(
        [
            {
                "date": "2026-08-03",
                "stock_id": "1101",
                "MarginPurchaseBuy": 100,
                "MarginPurchaseTodayBalance": 1000,
                "MarginPurchaseYesterdayBalance": 900,
                "ShortSaleBuy": 20,
                "ShortSaleTodayBalance": 200,
                "ShortSaleYesterdayBalance": 180,
            },
            {
                "date": "2026-08-03",
                "stock_id": "2330",
                "MarginPurchaseBuy": 300,
                "MarginPurchaseTodayBalance": 5000,
                "MarginPurchaseYesterdayBalance": 5100,
                "ShortSaleBuy": 50,
                "ShortSaleTodayBalance": 400,
                "ShortSaleYesterdayBalance": 390,
            },
        ]
    )
    result = aggregate_margin(source).iloc[0]
    assert result["margin_purchase_buy"] == 400
    assert result["margin_balance"] == 6000
    assert result["margin_change"] == 0
    assert result["short_sale_buy"] == 70
    assert result["short_change"] == 30
    assert result["margin_stock_count"] == 2
