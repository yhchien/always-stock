from unittest.mock import MagicMock, patch

from app.backtest_advisor import build_local_backtest_advice, generate_backtest_advice


def _sample_payload():
    return {
        "stock_id": "2330",
        "strategy_text": "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出",
        "normalized_text": "買進：收盤價站上 MA20 且外資連買 3 天；賣出：跌破 MA20 或外資賣超",
        "metrics": {
            "total_return_pct": 12.3,
            "annual_return_pct": 8.1,
            "win_rate_pct": 66.0,
            "max_drawdown_pct": -9.5,
            "sharpe_ratio": 1.18,
            "trade_count": 4,
            "avg_holding_days": 18,
        },
        "trades": [
            {"entry_date": "2024-01-03", "exit_date": "2024-01-17", "return_pct": 5.5},
        ],
        "latest_recommendation": {
            "latest_signal_date": "2024-01-30",
            "action": "hold",
            "reason": "目前仍持有部位，且尚未出現新的出場訊號。",
        },
    }


def test_build_local_backtest_advice_returns_structured_sections():
    advice = build_local_backtest_advice(_sample_payload())
    assert advice["summary"]
    assert advice["strengths"]
    assert advice["weaknesses"]
    assert advice["rewrite_suggestions"]
    assert advice["risk_notes"]
    assert advice["source"] == "heuristic"


def test_generate_backtest_advice_without_api_key_returns_fallback():
    with patch("app.backtest_advisor.OPENAI_API_KEY", ""):
        advice = generate_backtest_advice(_sample_payload())
    assert advice["source"] == "heuristic"


def test_generate_backtest_advice_uses_openai_when_available():
    mock_message = MagicMock()
    mock_message.content = (
        '{"summary":"這是趨勢追蹤策略","strengths":["報酬為正"],'
        '"weaknesses":["交易次數偏少"],"rewrite_suggestions":["加入停損"],'
        '"risk_notes":["仍要留意滑價"]}'
    )
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with (
        patch("app.backtest_advisor.OPENAI_API_KEY", "fake-key"),
        patch("app.backtest_advisor.OpenAI", return_value=mock_client),
    ):
        advice = generate_backtest_advice(_sample_payload())

    assert advice["source"] == "openai"
    assert advice["summary"] == "這是趨勢追蹤策略"
    assert advice["rewrite_suggestions"] == ["加入停損"]
