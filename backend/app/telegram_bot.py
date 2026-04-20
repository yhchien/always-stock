"""
Telegram Bot for always-stock.

Commands:
    /start          - Welcome message and usage instructions
    /help           - Show available commands
    /ai <stock_id>  - AI analysis for a stock (e.g. "/ai 2330")
    <stock_id>      - Query institutional flow for a stock (e.g. "2330")

The bot queries the local database for the most recent trading day's data
and returns institutional net buy/sell details + industry classification.
"""
import logging
import os
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.database import SessionLocal
from app.models import DailyPrice, InstStockFlow, StockMaster

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

HELP_TEXT = (
    "📊 *台股法人追蹤 Bot*\n\n"
    "直接輸入股票代號即可查詢最近一個交易日的三大法人買賣超。\n\n"
    "*指令：*\n"
    "/start — 歡迎訊息\n"
    "/help — 顯示此說明\n"
    "/brief — 今日盤前觀察重點\n"
    "/ai `股號` — AI 籌碼分析（如 `/ai 2330`）\n\n"
    "*範例：*\n"
    "`2330` — 查詢台積電法人資料\n"
    "`2317` — 查詢鴻海法人資料\n"
    "/ai `2330` — AI 分析台積電籌碼動向\n"
    "/brief — 今日盤前觀察重點\n"
)


def _get_latest_trade_date(db: Session) -> Optional[date]:
    """Return the most recent trade_date in daily_price table."""
    result = db.query(func.max(DailyPrice.trade_date)).scalar()
    return result


def _format_amount(value: float) -> str:
    """Format amount in 億/萬 for readability."""
    abs_val = abs(value)
    if abs_val >= 1e8:
        return f"{value / 1e8:+,.2f} 億"
    elif abs_val >= 1e4:
        return f"{value / 1e4:+,.1f} 萬"
    else:
        return f"{value:+,.0f}"


def _format_shares(value: float) -> str:
    """Format share count in 張 (1000 shares = 1 張)."""
    lots = value / 1000
    if abs(lots) >= 1000:
        return f"{lots:+,.0f} 張"
    else:
        return f"{lots:+,.1f} 張"


def query_stock(stock_id: str) -> str:
    """
    Query institutional flow for a given stock_id.
    Returns a formatted message string.
    """
    db = SessionLocal()
    try:
        # Look up stock master
        stock = db.get(StockMaster, stock_id)
        if not stock:
            return f"❌ 找不到股票代號 `{stock_id}`，請確認後重新輸入。"

        # Find latest trade date
        trade_date = _get_latest_trade_date(db)
        if not trade_date:
            return "⚠️ 資料庫尚無交易資料。"

        # Get closing price
        price = (
            db.query(DailyPrice)
            .filter(
                DailyPrice.stock_id == stock_id,
                DailyPrice.trade_date == trade_date,
            )
            .first()
        )

        # Get institutional flows
        flows = (
            db.query(InstStockFlow)
            .filter(
                InstStockFlow.stock_id == stock_id,
                InstStockFlow.trade_date == trade_date,
            )
            .all()
        )

        flow_map = {f.inst_type: f for f in flows}

        # Build response
        lines = [
            f"📊 *{stock.stock_name}* ({stock.stock_id})",
            f"📅 {trade_date.strftime('%Y-%m-%d')}",
        ]

        if price and price.close_price:
            lines.append(f"💰 收盤價：{price.close_price:.2f} 元")

        lines.append(f"🏭 產業：{stock.industry_name}")
        if stock.sub_industry:
            lines.append(f"🔗 子產業：{stock.sub_industry}")
        if stock.chain:
            lines.append(f"⛓ 供應鏈：{stock.chain}")

        lines.append("")
        lines.append("*三大法人買賣超：*")

        inst_labels = [
            ("foreign", "🌐 外資"),
            ("trust", "🏦 投信"),
            ("dealer", "🏢 自營商"),
        ]

        total_net_shares = 0.0
        total_net_amount = 0.0

        for inst_type, label in inst_labels:
            f = flow_map.get(inst_type)
            if f:
                net_s = f.net_shares or 0.0
                net_a = f.net_amount_est or 0.0
                total_net_shares += net_s
                total_net_amount += net_a
                lines.append(
                    f"  {label}：{_format_shares(net_s)}（{_format_amount(net_a)}）"
                )
            else:
                lines.append(f"  {label}：無資料")

        lines.append(
            f"  📋 *合計*：{_format_shares(total_net_shares)}（{_format_amount(total_net_amount)}）"
        )

        return "\n".join(lines)

    except Exception:
        logger.exception("Error querying stock %s", stock_id)
        return "⚠️ 查詢時發生錯誤，請稍後再試。"
    finally:
        db.close()


# ── Telegram handlers ────────────────────────────────────────────────────────


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def brief_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /brief command — send today's pre-market briefing."""
    from app.routers.market import build_daily_brief

    logger.info("brief requested by user=%s", update.effective_user.id)
    await update.message.chat.send_action("typing")

    db = SessionLocal()
    try:
        resp = build_daily_brief(db, None)
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception:
        logger.exception("Failed to build daily brief")
        await update.message.reply_text("⚠️ 產生盤前觀察重點時發生錯誤，請稍後再試。")
        return
    finally:
        db.close()

    header = f"📊 今日盤前觀察重點（{resp.trade_date}）\n\n"
    body = resp.content or "（無內容）"
    text = header + body

    # Telegram single-message limit is 4096 chars; chunk conservatively.
    chunk_size = 3900
    if len(text) <= chunk_size:
        await update.message.reply_text(text)
    else:
        for i in range(0, len(text), chunk_size):
            await update.message.reply_text(text[i:i + chunk_size])


async def ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ai <stock_id> command — AI-powered stock analysis."""
    from app.ai_analyst import analyze_stock

    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "請提供股票代號，例如 `/ai 2330`。",
            parse_mode="Markdown",
        )
        return

    stock_id = context.args[0].strip()
    if not stock_id.isdigit() or len(stock_id) < 4 or len(stock_id) > 6:
        await update.message.reply_text(
            "請輸入 4~6 碼的股票代號，例如 `/ai 2330`。",
            parse_mode="Markdown",
        )
        return

    logger.info("AI analysis: stock_id=%s from user=%s", stock_id, update.effective_user.id)

    # Send "typing" indicator since AI call takes a few seconds
    await update.message.chat.send_action("typing")

    result = analyze_stock(stock_id)
    await update.message.reply_text(result, parse_mode="Markdown")


async def stock_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages — treat as stock ID query."""
    text = update.message.text.strip()

    # Accept numeric stock IDs (4-6 digits)
    if not text.isdigit() or len(text) < 4 or len(text) > 6:
        await update.message.reply_text(
            "請輸入 4~6 碼的股票代號，例如 `2330`。",
            parse_mode="Markdown",
        )
        return

    logger.info("Telegram query: stock_id=%s from user=%s", text, update.effective_user.id)
    result = query_stock(text)
    await update.message.reply_text(result, parse_mode="Markdown")


def create_bot_app(token: str = "") -> Application:
    """Create and configure the Telegram bot Application."""
    bot_token = token or TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Set it via environment variable or pass it directly."
        )

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("brief", brief_handler))
    app.add_handler(CommandHandler("ai", ai_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, stock_query_handler))

    logger.info("Telegram bot application created")
    return app
