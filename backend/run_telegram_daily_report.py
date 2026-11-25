"""Telegram 台股行事曆策略每日廣播 cron 入口（21:30 台北）。

執行流程：
1. 讀取最新的台股行事曆策略預測
2. 撈所有 telegram_chats
3. 對每個 chat 用 formatters.format_market_calendar_strategy_report 組訊息 → 推送
4. exit code: 0 全部 ok / 1 部分失敗 / 2 全失敗 / 5 holiday（DB 無策略預測）

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
    parser = argparse.ArgumentParser(description="Telegram 台股行事曆策略廣播（21:30 cron）")
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
            MarketCalendarPrediction,
            TelegramChat,
        )
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
                report = formatters.MarketCalendarStrategyReport(
                    signal_date=prediction.signal_date,
                    target_date=prediction.target_date,
                    predicted_binary=prediction.predicted_binary,
                    predicted_direction=prediction.predicted_direction,
                    predicted_return=prediction.predicted_return,
                    predicted_return_low=prediction.predicted_return_low,
                    predicted_return_high=prediction.predicted_return_high,
                    explanation=prediction.explanation,
                )
                text = formatters.format_market_calendar_strategy_report(report)
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
                    logger.info("ok chat=%s market_calendar_strategy=true", chat.chat_id)
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
