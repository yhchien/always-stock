"""Telegram 魚尾策略每日廣播 cron 入口（21:30 台北）。

執行流程：
1. 讀取最新的台股行事曆策略預測
2. 讀取兩套模擬交易策略的待執行動作、持倉、現價與報酬率
3. 撈所有 telegram_chats
4. 對每個 chat 組合兩類策略訊息 → 推送
5. exit code: 0 全部 ok / 1 部分失敗 / 2 全失敗 / 5 holiday（DB 無策略資料）

不依賴 python-telegram-bot 的 Application — 直接打 HTTPS 送訊息，避免 cron script
需要管理 async event loop。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import func

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ALL_FAILED = 2
EXIT_HOLIDAY = 5
EXIT_CONFIG_ERROR = 3


TELEGRAM_API_BASE = "https://api.telegram.org"
_SEND_TIMEOUT_SECONDS = 30


def _send_message(token: str, chat_id: int, text: str) -> bool:
    """同步打 Telegram Bot API sendMessage；成功回 True，失敗 log 後回 False。"""
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT_SECONDS) as resp:
            body = json.load(resp)
            if not body.get("ok"):
                logger.error("Telegram sendMessage returned ok=False: %s", body)
                return False
            return True
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:
            err_body = "<unreadable>"
        logger.error("Telegram sendMessage HTTPError %s: %s", exc.code, err_body[:500])
        return False
    except Exception:
        logger.exception("Telegram sendMessage failed chat=%s", chat_id)
        return False


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram 魚尾策略廣播（21:30 cron）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出會推送哪些 chat 與訊息，不打 Telegram",
    )
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="只跑單一 chat（debug 用）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and not args.dry_run:
        logger.error("TELEGRAM_BOT_TOKEN not set; cannot send messages")
        return EXIT_CONFIG_ERROR

    try:
        from app.database import SessionLocal
        from app.models import (
            DailyPrice,
            MarketCalendarPrediction,
            ShadowPositionLot,
            ShadowStrategyOrder,
            ShadowVirtualPosition,
            TelegramChat,
        )
        from app.signals.shadow_portfolio import next_trading_execution_date
        from app.telegram import formatters
    except Exception:
        logger.exception("Failed to import dependencies")
        return EXIT_CONFIG_ERROR

    db = SessionLocal()
    try:
        prediction = (
            db.query(MarketCalendarPrediction)
            .order_by(MarketCalendarPrediction.signal_date.desc())
            .first()
        )
        if prediction is None:
            logger.warning("DB 無台股行事曆策略預測，視為 holiday 跳過")
            return EXIT_HOLIDAY

        logger.info(
            "Telegram market-calendar strategy report start: signal_date=%s target_date=%s",
            prediction.signal_date,
            prediction.target_date,
        )

        latest_trade_date = db.query(func.max(DailyPrice.trade_date)).scalar()
        if latest_trade_date is None:
            logger.warning("DB 無模擬交易行情資料，視為 holiday 跳過")
            return EXIT_HOLIDAY
        execution_date = next_trading_execution_date(db, latest_trade_date)
        if prediction.target_date != execution_date:
            logger.warning(
                "最新台股行事曆預測已過期：target_date=%s，下一交易日=%s；停止發送舊訊息",
                prediction.target_date,
                execution_date,
            )
            return EXIT_HOLIDAY
        strategy_versions = (
            ("v1_frozen", "原策略｜v1 Dual-Engine"),
            ("V1_MARKET_OVERLAY_202611", "新策略｜202611 市場風險分層"),
        )
        shadow_reports = [
            _load_strategy_report(
                db,
                strategy_version=version,
                label=label,
                latest_trade_date=latest_trade_date,
            )
            for version, label in strategy_versions
        ]
        market_calendar_report = formatters.MarketCalendarStrategyReport(
            signal_date=prediction.signal_date,
            target_date=prediction.target_date,
            predicted_binary=prediction.predicted_binary,
            predicted_direction=prediction.predicted_direction,
            predicted_return=prediction.predicted_return,
            predicted_return_low=prediction.predicted_return_low,
            predicted_return_high=prediction.predicted_return_high,
            explanation=prediction.explanation,
        )
        market_calendar_text = formatters.format_market_calendar_strategy_report(
            market_calendar_report
        )
        shadow_text = formatters.format_shadow_daily_report(
            shadow_reports,
            signal_date=latest_trade_date,
            execution_date=execution_date,
        )
        text = f"{market_calendar_text}\n\n{shadow_text}"

        query = db.query(TelegramChat)
        if args.chat_id is not None:
            query = query.filter(TelegramChat.chat_id == args.chat_id)
        chats = query.all()

        if not chats:
            logger.info("No registered telegram chats; nothing to send")
            return EXIT_OK

        logger.info("Pushing report to %d chat(s)", len(chats))

        ok_count = 0
        failed_count = 0
        for chat in chats:
            try:
                if args.dry_run:
                    logger.info(
                        "[DRY] would send to chat=%s (%d chars):\n%s",
                        chat.chat_id, len(text), text,
                    )
                    ok_count += 1
                    continue

                # 切 chunk 推送
                chunks = formatters.chunk_for_telegram(text)
                all_sent = True
                for chunk in chunks:
                    if not _send_message(token, chat.chat_id, chunk):
                        all_sent = False
                        break

                if all_sent:
                    ok_count += 1
                    logger.info("ok chat=%s market_calendar_and_shadow_strategy=true", chat.chat_id)
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1
                logger.exception("Failed to send report chat=%s", chat.chat_id)

        logger.info(
            "Telegram daily report done: ok=%d failed=%d",
            ok_count, failed_count,
        )

        if failed_count == 0:
            return EXIT_OK
        if ok_count == 0 and failed_count > 0:
            return EXIT_ALL_FAILED
        return EXIT_PARTIAL
    finally:
        db.close()


def _load_strategy_report(db, *, strategy_version: str, label: str, latest_trade_date):
    """讀一套模擬策略的待執行動作、持倉、現價與目前報酬率。"""
    from app.models import (
        DailyPrice,
        ShadowPositionLot,
        ShadowStrategyOrder,
        ShadowVirtualPosition,
    )
    from app.telegram.formatters import (
        ShadowActionReport,
        ShadowPositionReport,
        ShadowStrategyReport,
    )

    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == "PENDING",
        )
        .order_by(
            ShadowStrategyOrder.scheduled_execution_date.asc(),
            ShadowStrategyOrder.id.asc(),
        )
        .all()
    )
    actions = [
        ShadowActionReport(
            action=order.action,
            stock_id=order.stock_id,
            stock_name=order.stock_name,
            units=order.units,
        )
        for order in orders
    ]

    positions = []
    position_rows = (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .order_by(ShadowVirtualPosition.stock_id.asc())
        .all()
    )
    for position in position_rows:
        lots = (
            db.query(ShadowPositionLot)
            .filter(ShadowPositionLot.position_id == position.id)
            .order_by(
                ShadowPositionLot.entry_execution_date.asc(),
                ShadowPositionLot.id.asc(),
            )
            .all()
        )
        total_shares = sum(lot.shares for lot in lots)
        total_cost = sum(lot.allocation for lot in lots)
        if not total_shares:
            continue
        entry_price = total_cost / total_shares
        current_price = (
            db.query(DailyPrice.close_price)
            .filter(
                DailyPrice.stock_id == position.stock_id,
                DailyPrice.trade_date == latest_trade_date,
            )
            .scalar()
        )
        performance_pct = (
            (current_price / entry_price - 1.0) * 100.0
            if current_price is not None and entry_price
            else None
        )
        positions.append(
            ShadowPositionReport(
                stock_id=position.stock_id,
                stock_name=position.stock_name,
                entry_date=lots[0].entry_execution_date,
                entry_price=entry_price,
                current_price=float(current_price) if current_price is not None else None,
                performance_pct=performance_pct,
                units=len(lots),
            )
        )

    return ShadowStrategyReport(label=label, actions=actions, positions=positions)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
