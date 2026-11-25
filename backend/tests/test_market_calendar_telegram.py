from datetime import date

from app.telegram import formatters


def test_market_calendar_strategy_report_contains_strategy_not_stock_list():
    report = formatters.MarketCalendarStrategyReport(
        signal_date=date(2026, 11, 24),
        target_date=date(2026, 11, 25),
        predicted_binary="偏跌",
        predicted_direction="震盪",
        predicted_return=-4.52,
        predicted_return_low=-5.10,
        predicted_return_high=-3.90,
        explanation={
            "ai_interpretation": {
                "summary": "法人偏空且夜盤走弱。",
                "regime": "風險升高",
                "bullish_factors": [],
                "bearish_factors": [
                    {"factor": "法人同步偏空", "interpretation": "資金面不利。"}
                ],
                "risks": [],
            }
        },
    )
    out = formatters.format_market_calendar_strategy_report(report)

    assert "魚尾台股行事曆策略" in out
    assert "策略結論：*偏跌*" in out
    assert "預估漲跌幅：-4.52%" in out
    assert "法人同步偏空：資金面不利。" in out
    assert "股票清單" not in out
    assert "台積電" not in out
