"""
Tests for AI analyst module (OpenAI backend).

These tests verify data collection and formatting logic against an in-memory DB,
and mock the OpenAI API call to avoid real API usage.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models import DailyPrice, InstStockFlow, StockMaster
from app.ai_analyst import _collect_stock_context, analyze_stock


# ── Helper: seed test data ───────────────────────────────────────────────────

def _seed_stock(db, stock_id="2330", stock_name="台積電",
                industry_name="半導體", sub_industry="晶圓代工", chain="上游"):
    stock = StockMaster(
        stock_id=stock_id,
        stock_name=stock_name,
        industry_name=industry_name,
        sub_industry=sub_industry,
        chain=chain,
    )
    db.add(stock)
    db.commit()
    return stock


def _seed_prices(db, stock_id="2330", days=5):
    """Seed 5 consecutive weekday prices."""
    base_date = date(2025, 4, 1)
    prices = []
    for i in range(days):
        d = date(2025, 4, 1 + i)
        if d.weekday() >= 5:
            continue
        p = DailyPrice(
            stock_id=stock_id,
            trade_date=d,
            close_price=890.0 + i * 5,
            volume=25000.0 + i * 1000,
        )
        prices.append(p)
    db.add_all(prices)
    db.commit()
    return prices


def _seed_flows(db, stock_id="2330", trade_date=date(2025, 4, 1)):
    """Seed flows for a single day."""
    flows = [
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="foreign",
            net_shares=2000000, net_amount_est=1790000000,
            buy_shares=5000000, sell_shares=3000000,
            buy_amount_est=4475000000, sell_amount_est=2685000000,
        ),
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="trust",
            net_shares=-400000, net_amount_est=-358000000,
            buy_shares=800000, sell_shares=1200000,
            buy_amount_est=716000000, sell_amount_est=1074000000,
        ),
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="dealer",
            net_shares=200000, net_amount_est=179000000,
            buy_shares=300000, sell_shares=100000,
            buy_amount_est=268500000, sell_amount_est=89500000,
        ),
    ]
    db.add_all(flows)
    db.commit()
    return flows


# ── _collect_stock_context ───────────────────────────────────────────────────

class TestCollectStockContext:
    def test_returns_none_for_unknown_stock(self, db):
        result = _collect_stock_context(db, "9999")
        assert result is None

    def test_returns_none_when_no_prices(self, db):
        _seed_stock(db)
        result = _collect_stock_context(db, "2330")
        assert result is None

    def test_includes_stock_info(self, db):
        _seed_stock(db)
        _seed_prices(db)
        result = _collect_stock_context(db, "2330")

        assert "台積電" in result
        assert "2330" in result
        assert "半導體" in result
        assert "晶圓代工" in result
        assert "上游" in result

    def test_includes_price_data(self, db):
        _seed_stock(db)
        _seed_prices(db)
        result = _collect_stock_context(db, "2330")

        assert "收盤" in result
        assert "890" in result

    def test_includes_flow_data(self, db):
        _seed_stock(db)
        _seed_prices(db)
        _seed_flows(db, trade_date=date(2025, 4, 1))
        result = _collect_stock_context(db, "2330")

        assert "外資" in result
        assert "投信" in result
        assert "自營" in result

    def test_without_sub_industry(self, db):
        _seed_stock(db, sub_industry=None, chain=None)
        _seed_prices(db)
        result = _collect_stock_context(db, "2330")

        assert "台積電" in result
        assert "子產業" not in result
        assert "供應鏈" not in result


# ── analyze_stock ────────────────────────────────────────────────────────────

class TestAnalyzeStock:
    def test_no_api_key(self, db):
        with patch("app.ai_analyst.get_openai_api_key", return_value=""):
            result = analyze_stock("2330")
        assert "未啟用" in result

    def test_stock_not_found(self, db):
        with (
            patch("app.ai_analyst.get_openai_api_key", return_value="fake-key"),
            patch("app.ai_analyst.SessionLocal", return_value=db),
        ):
            result = analyze_stock("9999")
        assert "找不到" in result

    def test_successful_analysis(self, db):
        _seed_stock(db)
        _seed_prices(db)
        _seed_flows(db, trade_date=date(2025, 4, 1))

        mock_message = MagicMock()
        mock_message.content = "外資連續買超，短期偏多。⚠️ 以上為 AI 分析僅供參考，不構成投資建議。"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("app.ai_analyst.get_openai_api_key", return_value="fake-key"),
            patch("app.ai_analyst.SessionLocal", return_value=db),
            patch("app.ai_analyst.OpenAI", return_value=mock_client),
        ):
            result = analyze_stock("2330")

        assert "AI 籌碼分析" in result
        assert "外資連續買超" in result
        assert "僅供參考" in result

    def test_api_error_handling(self, db):
        _seed_stock(db)
        _seed_prices(db)

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with (
            patch("app.ai_analyst.get_openai_api_key", return_value="fake-key"),
            patch("app.ai_analyst.SessionLocal", return_value=db),
            patch("app.ai_analyst.OpenAI", return_value=mock_client),
        ):
            result = analyze_stock("2330")

        assert "發生錯誤" in result


# ── Telegram /ai handler ────────────────────────────────────────────────────

class TestAiHandler:
    @pytest.mark.asyncio
    async def test_ai_no_args(self):
        from app.telegram_bot import ai_handler
        from unittest.mock import AsyncMock

        update = AsyncMock()
        context = AsyncMock()
        context.args = []
        await ai_handler(update, context)
        call_args = update.message.reply_text.call_args
        assert "請提供股票代號" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_ai_invalid_stock_id(self):
        from app.telegram_bot import ai_handler
        from unittest.mock import AsyncMock

        update = AsyncMock()
        context = AsyncMock()
        context.args = ["abc"]
        await ai_handler(update, context)
        call_args = update.message.reply_text.call_args
        assert "4~6 碼" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_ai_valid_call(self, db):
        from app.telegram_bot import ai_handler
        from unittest.mock import AsyncMock

        _seed_stock(db)
        _seed_prices(db)

        update = AsyncMock()
        update.effective_user.id = 12345
        context = AsyncMock()
        context.args = ["2330"]

        mock_message = MagicMock()
        mock_message.content = "測試 AI 分析結果"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with (
            patch("app.ai_analyst.get_openai_api_key", return_value="fake-key"),
            patch("app.ai_analyst.SessionLocal", return_value=db),
            patch("app.ai_analyst.OpenAI", return_value=mock_client),
        ):
            await ai_handler(update, context)

        call_args = update.message.reply_text.call_args
        assert "AI 籌碼分析" in call_args[0][0]
