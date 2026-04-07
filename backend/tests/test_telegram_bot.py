"""
Tests for Telegram bot query logic.

These tests verify the query_stock() function against an in-memory database,
without requiring a real Telegram connection.
"""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.models import DailyPrice, InstStockFlow, StockMaster
from app.telegram_bot import (
    _format_amount,
    _format_shares,
    _get_latest_trade_date,
    create_bot_app,
    query_stock,
)


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


def _seed_price(db, stock_id="2330", trade_date=date(2025, 4, 3),
                close_price=895.0, volume=25000.0):
    price = DailyPrice(
        stock_id=stock_id,
        trade_date=trade_date,
        close_price=close_price,
        volume=volume,
    )
    db.add(price)
    db.commit()
    return price


def _seed_flows(db, stock_id="2330", trade_date=date(2025, 4, 3)):
    flows = [
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="foreign",
            buy_shares=5000000, sell_shares=3000000,
            net_shares=2000000, net_amount_est=1790000000,
            buy_amount_est=4475000000, sell_amount_est=2685000000,
        ),
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="trust",
            buy_shares=800000, sell_shares=1200000,
            net_shares=-400000, net_amount_est=-358000000,
            buy_amount_est=716000000, sell_amount_est=1074000000,
        ),
        InstStockFlow(
            stock_id=stock_id, trade_date=trade_date,
            inst_type="dealer",
            buy_shares=300000, sell_shares=100000,
            net_shares=200000, net_amount_est=179000000,
            buy_amount_est=268500000, sell_amount_est=89500000,
        ),
    ]
    db.add_all(flows)
    db.commit()
    return flows


# ── Format helpers ───────────────────────────────────────────────────────────

class TestFormatAmount:
    def test_billions(self):
        assert "億" in _format_amount(1_500_000_000)
        assert "+15.00 億" == _format_amount(1_500_000_000)

    def test_negative_billions(self):
        assert "-3.58 億" == _format_amount(-358_000_000)

    def test_ten_thousands(self):
        assert "萬" in _format_amount(50_000)
        assert "+5.0 萬" == _format_amount(50_000)

    def test_small_amount(self):
        result = _format_amount(500)
        assert "+500" in result

    def test_zero(self):
        result = _format_amount(0)
        assert "+0" in result


class TestFormatShares:
    def test_large_shares(self):
        # 2,000,000 shares = 2,000 張
        result = _format_shares(2_000_000)
        assert "+2,000 張" == result

    def test_small_shares(self):
        # 500 shares = 0.5 張
        result = _format_shares(500)
        assert "+0.5 張" == result

    def test_negative(self):
        result = _format_shares(-400_000)
        assert "-400.0 張" == result


# ── Latest trade date ────────────────────────────────────────────────────────

class TestGetLatestTradeDate:
    def test_returns_none_when_empty(self, db):
        with patch("app.telegram_bot.SessionLocal", return_value=db):
            assert _get_latest_trade_date(db) is None

    def test_returns_latest_date(self, db):
        _seed_stock(db)
        _seed_price(db, trade_date=date(2025, 4, 1))
        _seed_price(db, trade_date=date(2025, 4, 3))
        result = _get_latest_trade_date(db)
        assert result == date(2025, 4, 3)


# ── query_stock ──────────────────────────────────────────────────────────────

class TestQueryStock:
    def test_stock_not_found(self, db):
        with patch("app.telegram_bot.SessionLocal", return_value=db):
            result = query_stock("9999")
        assert "找不到" in result
        assert "9999" in result

    def test_no_trade_data(self, db):
        _seed_stock(db)
        with patch("app.telegram_bot.SessionLocal", return_value=db):
            result = query_stock("2330")
        assert "尚無交易資料" in result

    def test_full_query(self, db):
        trade_date = date(2025, 4, 3)
        _seed_stock(db)
        _seed_price(db, trade_date=trade_date)
        _seed_flows(db, trade_date=trade_date)

        with patch("app.telegram_bot.SessionLocal", return_value=db):
            result = query_stock("2330")

        # Verify stock info
        assert "台積電" in result
        assert "2330" in result
        assert "895.00" in result

        # Verify industry info
        assert "半導體" in result
        assert "晶圓代工" in result

        # Verify institutional data present
        assert "外資" in result
        assert "投信" in result
        assert "自營商" in result
        assert "合計" in result

    def test_query_stock_without_flows(self, db):
        """Stock exists with price but no institutional flow data."""
        trade_date = date(2025, 4, 3)
        _seed_stock(db)
        _seed_price(db, trade_date=trade_date)

        with patch("app.telegram_bot.SessionLocal", return_value=db):
            result = query_stock("2330")

        assert "台積電" in result
        assert "無資料" in result

    def test_query_stock_without_sub_industry(self, db):
        """Stock without sub_industry should not show sub_industry line."""
        trade_date = date(2025, 4, 3)
        _seed_stock(db, sub_industry=None, chain=None)
        _seed_price(db, trade_date=trade_date)

        with patch("app.telegram_bot.SessionLocal", return_value=db):
            result = query_stock("2330")

        assert "台積電" in result
        assert "子產業" not in result
        assert "供應鏈" not in result


# ── create_bot_app ───────────────────────────────────────────────────────────

class TestCreateBotApp:
    def test_raises_without_token(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": ""}, clear=False):
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
                create_bot_app(token="")

    def test_creates_app_with_token(self):
        app = create_bot_app(token="123456:FAKE_TOKEN_FOR_TEST")
        assert app is not None


# ── Handler tests (async) ────────────────────────────────────────────────────

class TestHandlers:
    @pytest.mark.asyncio
    async def test_start_handler(self):
        from app.telegram_bot import start_handler

        update = AsyncMock()
        context = AsyncMock()
        await start_handler(update, context)
        update.message.reply_text.assert_called_once()
        call_args = update.message.reply_text.call_args
        assert "台股法人追蹤" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_help_handler(self):
        from app.telegram_bot import help_handler

        update = AsyncMock()
        context = AsyncMock()
        await help_handler(update, context)
        update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_stock_query_invalid_input(self):
        from app.telegram_bot import stock_query_handler

        update = AsyncMock()
        update.message.text = "hello"
        context = AsyncMock()
        await stock_query_handler(update, context)
        call_args = update.message.reply_text.call_args
        assert "4~6 碼" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_stock_query_valid_input(self, db):
        from app.telegram_bot import stock_query_handler

        trade_date = date(2025, 4, 3)
        _seed_stock(db)
        _seed_price(db, trade_date=trade_date)
        _seed_flows(db, trade_date=trade_date)

        update = AsyncMock()
        update.message.text = "2330"
        update.effective_user.id = 12345
        context = AsyncMock()

        with patch("app.telegram_bot.SessionLocal", return_value=db):
            await stock_query_handler(update, context)

        call_args = update.message.reply_text.call_args
        assert "台積電" in call_args[0][0]
