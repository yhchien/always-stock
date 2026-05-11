"""Telegram 每日清單報告 cron 入口（21:30 台北）。

執行流程：
1. 撈所有 telegram_chats
2. 對每個 chat：
   a. 取 watchlist（空則跳過）
   b. 對每檔股票：
      - 若今日 snapshot 已有 ok 快照（manual / cron 早跑過）→ 直接讀
      - 否則跑 run_trade_quality_for_user（source='cron'）並寫快照
   c. 用 formatters.format_daily_report 組訊息 → 透過 Telegram HTTP API 推送
3. exit code: 0 全部 ok / 1 部分失敗 / 2 全失敗 / 5 holiday（DB 無交易日）

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
    parser = argparse.ArgumentParser(description="Telegram 每日清單報告（21:30 cron）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出會推送哪些 chat，不打 OpenAI、不打 Telegram",
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
        from app.industry_flow_service import get_latest_industry_trade_date
        from app.models import StockMaster, TelegramChat
        from app.routers.analysis import KeyFactor, TradeQualityResponse
        from app.telegram import formatters, trade_quality_service, watchlist_service
    except Exception:
        logger.exception("Failed to import dependencies")
        return EXIT_CONFIG_ERROR

    db = SessionLocal()
    try:
        snapshot_trade_date = get_latest_industry_trade_date(db)
        if snapshot_trade_date is None:
            logger.warning("DB 無交易日資料，視為 holiday 跳過")
            return EXIT_HOLIDAY

        logger.info("Telegram daily report start: snapshot_date=%s", snapshot_trade_date)

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
        empty_count = 0

        for chat in chats:
            try:
                watchlist = watchlist_service.list_watchlist(db, chat.chat_id)
                if not watchlist:
                    empty_count += 1
                    logger.info("chat=%s watchlist empty; skip", chat.chat_id)
                    continue

                results: list = []
                for snap in watchlist:
                    # 1. 嘗試讀已存在的今日 snapshot
                    existing = trade_quality_service.load_latest_snapshot(
                        db, chat_id=chat.chat_id, stock_id=snap.stock_id,
                    )
                    if (
                        existing is not None
                        and existing.snapshot_trade_date == snapshot_trade_date
                    ):
                        response = _snapshot_to_response(
                            existing, snap.stock_name, snap.stock_id,
                            KeyFactor, TradeQualityResponse,
                        )
                        results.append((snap, response))
                        continue

                    # 2. 跑新分析
                    if args.dry_run:
                        logger.info(
                            "[DRY] would run trade quality chat=%s stock=%s",
                            chat.chat_id, snap.stock_id,
                        )
                        results.append((snap, None))
                        continue

                    run_result = trade_quality_service.run_for_stock(
                        db, chat_id=chat.chat_id, stock_id=snap.stock_id, source="cron",
                    )
                    results.append(
                        (snap, run_result.response if run_result.success else None)
                    )

                # 3. 組訊息 + 推送
                text = formatters.format_daily_report(chat.chat_label, results)
                if args.dry_run:
                    logger.info(
                        "[DRY] would send to chat=%s (%d chars)",
                        chat.chat_id, len(text),
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
                    logger.info("ok chat=%s stocks=%d", chat.chat_id, len(results))
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1
                logger.exception("Failed to send report chat=%s", chat.chat_id)

        logger.info(
            "Telegram daily report done: ok=%d failed=%d empty=%d",
            ok_count, failed_count, empty_count,
        )

        if failed_count == 0:
            return EXIT_OK
        if ok_count == 0 and failed_count > 0:
            return EXIT_ALL_FAILED
        return EXIT_PARTIAL
    finally:
        db.close()


def _snapshot_to_response(
    snapshot,
    stock_name: str,
    stock_id: str,
    KeyFactor,
    TradeQualityResponse,
):
    """把 TelegramTradeQualitySnapshot row 重組成 TradeQualityResponse-like 物件供 formatter 用。"""
    key_factors = None
    if snapshot.key_factors:
        try:
            key_factors = [KeyFactor(**row) for row in snapshot.key_factors]
        except Exception:
            logger.exception(
                "Failed to parse key_factors stock=%s snapshot_id=%s",
                stock_id, snapshot.id,
            )

    return TradeQualityResponse(
        stock_id=stock_id,
        stock_name=stock_name,
        buy_date=str(snapshot.snapshot_trade_date),
        rating=snapshot.rating or "NEUTRAL",
        rating_label=snapshot.rating_label or "—",
        classification=snapshot.classification,
        summary=snapshot.summary or "—",
        target_price_low=snapshot.target_price_low,
        target_price_high=snapshot.target_price_high,
        exit_price_low=snapshot.exit_price_low,
        exit_price_high=snapshot.exit_price_high,
        report_markdown=snapshot.report_markdown or "",
        key_factors=key_factors,
        source="cache",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
