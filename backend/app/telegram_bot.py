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
    "/ai `股號` — AI 籌碼分析（如 `/ai 2330`）\n"
    "`list help` — 清單功能說明（觀察清單 + 交易質量分析）\n\n"
    "*範例：*\n"
    "`2330` — 查詢台積電法人資料\n"
    "`2317` — 查詢鴻海法人資料\n"
    "/ai `2330` — AI 分析台積電籌碼動向\n"
    "/brief — 今日盤前觀察重點\n"
    "`list register <密碼>` — 註冊個人清單（首次使用）\n"
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
    """Handle plain text messages — treat as stock ID query.

    若訊息以 `list` 開頭，路由到 list_handler 處理清單功能。
    """
    text = update.message.text.strip()

    if text.lower().startswith("list"):
        await list_handler(update, context)
        return

    # Accept numeric stock IDs (4-6 digits)
    if not text.isdigit() or len(text) < 4 or len(text) > 6:
        await update.message.reply_text(
            "請輸入 4~6 碼的股票代號，例如 `2330`，或輸入 `list help` 查看清單功能。",
            parse_mode="Markdown",
        )
        return

    logger.info("Telegram query: stock_id=%s from user=%s", text, update.effective_user.id)
    result = query_stock(text)
    await update.message.reply_text(result, parse_mode="Markdown")


# ── list 指令處理 ────────────────────────────────────────────────────────────


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch `list ...` commands. 同步指令直接回；list run / list run all 走背景。"""
    from app.telegram import commands as telegram_commands
    from app.telegram import locks as telegram_locks
    from app.telegram import registration as telegram_registration

    text = update.message.text
    chat = update.effective_chat
    chat_id = chat.id
    chat_label = chat.username or (
        update.effective_user.username if update.effective_user else None
    )

    parsed = telegram_commands.parse(text)

    # parse 階段錯誤（缺參數等）直接回覆
    if parsed.error and parsed.kind != "unknown":
        await update.message.reply_text(parsed.error, parse_mode="Markdown")
        return

    if parsed.kind == "unknown":
        await update.message.reply_text(
            parsed.error or "❓ 未知指令。輸入 `list help` 查看支援指令。",
            parse_mode="Markdown",
        )
        return

    if parsed.kind == "help":
        await update.message.reply_text(telegram_commands.handle_help(), parse_mode="Markdown")
        return

    # Admin 指令獨立檢查（不需註冊；不在白名單則偽裝成 unknown，不洩漏指令存在）
    if parsed.kind in ("admin_chats", "admin_show"):
        from app.settings import get_admin_telegram_chat_ids
        admin_ids = get_admin_telegram_chat_ids()
        if chat_id not in admin_ids:
            await update.message.reply_text(
                "❓ 未知指令。輸入 `list help` 查看支援指令。",
                parse_mode="Markdown",
            )
            return

        db = SessionLocal()
        try:
            if parsed.kind == "admin_chats":
                reply = telegram_commands.handle_admin_chats(db)
            else:
                reply = telegram_commands.handle_admin_show(
                    db, target_chat_id=parsed.target_chat_id,
                )
            from app.telegram.formatters import chunk_for_telegram
            for chunk in chunk_for_telegram(reply):
                await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            logger.exception("admin command failed chat_id=%s text=%s", chat_id, text)
            await update.message.reply_text(
                "⚠️ 管理指令執行失敗，請查 log。",
                parse_mode="Markdown",
            )
        finally:
            db.close()
        return

    db = SessionLocal()
    try:
        if parsed.kind == "register":
            reply = telegram_commands.handle_register(
                db, chat_id=chat_id, password=parsed.password or "", chat_label=chat_label,
            )
            await update.message.reply_text(reply, parse_mode="Markdown")
            return

        # 其他所有指令都需要先註冊
        if not telegram_registration.is_registered(db, chat_id):
            await update.message.reply_text(
                "🔒 此 chat 尚未註冊。\n\n"
                "請先輸入：`list register <密碼>` 完成註冊。",
                parse_mode="Markdown",
            )
            return

        # 順手更新 last_seen_at
        telegram_registration.touch_last_seen(db, chat_id)

        # list run / list run all 需要鎖
        if parsed.kind in ("run_single", "run_all"):
            if not telegram_locks.try_acquire(chat_id):
                await update.message.reply_text(
                    "⏳ 已有分析任務在執行中，請等候完成後再試。",
                    parse_mode="Markdown",
                )
                return

            if parsed.kind == "run_single":
                stock_id = parsed.stock_ids[0]
                await update.message.reply_text(
                    f"⏳ 已開始分析 `{stock_id}`，跑完會自動推送結果（約 10~30 秒）。",
                    parse_mode="Markdown",
                )
                context.application.create_task(
                    _run_single_background(chat_id, stock_id, context.bot)
                )
            else:  # run_all
                await update.message.reply_text(
                    "⏳ 已開始重跑全部清單，跑完會自動推送。\n"
                    "其他 `list run` 指令將被暫時鎖定，直到本次完成。",
                    parse_mode="Markdown",
                )
                context.application.create_task(
                    _run_all_background(chat_id, context.bot)
                )
            return

        # 同步指令
        if parsed.kind == "show":
            reply = telegram_commands.handle_show(db, chat_id=chat_id)
        elif parsed.kind == "add":
            reply = telegram_commands.handle_add(
                db, chat_id=chat_id, stock_ids=parsed.stock_ids,
            )
        elif parsed.kind == "delete":
            reply = telegram_commands.handle_delete(
                db, chat_id=chat_id, stock_ids=parsed.stock_ids,
            )
        elif parsed.kind == "watch_detail":
            reply = telegram_commands.handle_watch_detail(
                db, chat_id=chat_id, stock_id=parsed.stock_ids[0],
            )
        else:
            reply = "❓ 未知指令，輸入 `list help` 查看支援指令。"

        # watch_detail 可能很長，切 chunk
        from app.telegram.formatters import chunk_for_telegram

        for chunk in chunk_for_telegram(reply):
            await update.message.reply_text(chunk, parse_mode="Markdown")
    except Exception:
        logger.exception("list_handler failed chat_id=%s text=%s", chat_id, text)
        await update.message.reply_text(
            "⚠️ 處理指令時發生錯誤，請稍後再試。",
            parse_mode="Markdown",
        )
    finally:
        db.close()


async def _run_single_background(chat_id: int, stock_id: str, bot):
    """`list run <stock_id>` 背景任務：跑完 trade quality → 推送結果 → 釋放鎖。"""
    from app.models import StockMaster
    from app.telegram import formatters, locks as telegram_locks, trade_quality_service

    db = SessionLocal()
    try:
        stock = db.get(StockMaster, stock_id)
        if stock is None:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ 找不到代號 `{stock_id}`，請確認後重新輸入。",
                parse_mode="Markdown",
            )
            return

        result = trade_quality_service.run_for_stock(
            db, chat_id=chat_id, stock_id=stock_id, source="manual",
        )
        if not result.success or result.response is None:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ 分析 `{stock_id}` 失敗：{result.error_message or '未知錯誤'}",
                parse_mode="Markdown",
            )
            return

        text = formatters.format_trade_quality_brief(result.response)
        for chunk in formatters.chunk_for_telegram(text):
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
    except Exception:
        logger.exception("Background list run failed chat=%s stock=%s", chat_id, stock_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ 背景分析發生未預期錯誤，請稍後再試。",
            )
        except Exception:
            logger.exception("Also failed to send error message to chat=%s", chat_id)
    finally:
        db.close()
        telegram_locks.release(chat_id)


async def _run_all_background(chat_id: int, bot):
    """`list run all` 背景任務：逐檔跑完 → 推送彙整 → 釋放鎖。"""
    from app.telegram import formatters, locks as telegram_locks, trade_quality_service
    from app.telegram import watchlist_service

    db = SessionLocal()
    try:
        snapshots = watchlist_service.list_watchlist(db, chat_id)
        if not snapshots:
            await bot.send_message(
                chat_id=chat_id,
                text="📋 清單目前是空的，請先 `list add 2330` 加入個股。",
                parse_mode="Markdown",
            )
            return

        total = len(snapshots)
        results: list = []
        for idx, snap in enumerate(snapshots, start=1):
            run_result = trade_quality_service.run_for_stock(
                db, chat_id=chat_id, stock_id=snap.stock_id, source="manual",
            )
            results.append(
                (snap, run_result.response if run_result.success else None)
            )

        # 推送彙整訊息
        text = formatters.format_daily_report(None, results)
        text = f"✅ 全部 {total} 檔分析完成。\n\n{text}"
        for chunk in formatters.chunk_for_telegram(text):
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
    except Exception:
        logger.exception("Background list run all failed chat=%s", chat_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ 背景分析發生未預期錯誤，請稍後再試。",
            )
        except Exception:
            logger.exception("Also failed to send error message to chat=%s", chat_id)
    finally:
        db.close()
        telegram_locks.release(chat_id)


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
