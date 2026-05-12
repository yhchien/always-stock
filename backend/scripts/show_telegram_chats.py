"""CLI script：列出所有 Telegram 註冊 chat + 各自的觀察清單（管理員專屬）。

用途：在 Render Shell（或本地連到 prod DB）查看所有 Telegram bot 使用者的清單狀態，
作為 `list admin chats` Telegram 指令的備援（適合大量資料 / 不想開 Telegram 看時用）。

使用方式：
    python3 backend/scripts/show_telegram_chats.py           # 印所有 chat + watchlist
    python3 backend/scripts/show_telegram_chats.py --chat-id 12345  # 只看單一 chat
    python3 backend/scripts/show_telegram_chats.py --json    # JSON 輸出（給程式處理用）

需要的 env：DATABASE_URL（連到目標 DB）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent.parent

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="列出所有 Telegram 註冊 chat + 觀察清單")
    parser.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="只看單一 chat（省略則列出全部）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 輸出（給程式處理用）",
    )
    return parser.parse_args(argv)


def _format_chat_text(chat_id, label, registered_at, last_seen_at, watchlist_size, snapshots=None):
    label = label or "—"
    lines = [
        f"chat_id      = {chat_id}",
        f"label        = {label}",
        f"registered   = {registered_at}",
        f"last_seen    = {last_seen_at}",
        f"watchlist    = {watchlist_size}/20",
    ]
    if snapshots is not None and snapshots:
        lines.append("觀察清單：")
        for s in snapshots:
            price = f"{s.close_price:.2f}" if s.close_price else "—"
            spread = f"{s.spread_pct:+.2f}%" if s.spread_pct is not None else "—"
            sub = s.sub_industry or s.industry_name or "—"
            lines.append(f"  - {s.stock_id} {s.stock_name} ({sub}) 收 {price} ({spread})")
    elif snapshots is not None:
        lines.append("觀察清單：（空）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    try:
        from app.database import SessionLocal
        from app.telegram import watchlist_service
    except Exception:
        logger.exception("Failed to import dependencies")
        return 1

    db = SessionLocal()
    try:
        if args.chat_id is not None:
            detail = watchlist_service.get_chat_detail(db, args.chat_id)
            if detail is None:
                logger.error("chat_id %s not found", args.chat_id)
                return 2
            chat, snapshots = detail
            if args.json:
                print(json.dumps({
                    "chat_id": chat.chat_id,
                    "chat_label": chat.chat_label,
                    "registered_at": chat.registered_at.isoformat(),
                    "last_seen_at": chat.last_seen_at.isoformat(),
                    "watchlist": [
                        {
                            "stock_id": s.stock_id,
                            "stock_name": s.stock_name,
                            "industry_name": s.industry_name,
                            "sub_industry": s.sub_industry,
                            "close_price": s.close_price,
                            "spread_pct": s.spread_pct,
                            "trade_date": s.trade_date.isoformat() if s.trade_date else None,
                        }
                        for s in snapshots
                    ],
                }, ensure_ascii=False, indent=2))
            else:
                print(_format_chat_text(
                    chat.chat_id, chat.chat_label, chat.registered_at,
                    chat.last_seen_at, len(snapshots), snapshots,
                ))
            return 0

        items = watchlist_service.all_chats_with_summary(db)
        if not items:
            print("（目前沒有任何註冊的 Telegram chat）")
            return 0

        if args.json:
            print(json.dumps([
                {
                    "chat_id": it.chat_id,
                    "chat_label": it.chat_label,
                    "registered_at": it.registered_at.isoformat(),
                    "last_seen_at": it.last_seen_at.isoformat(),
                    "watchlist_size": it.watchlist_size,
                }
                for it in items
            ], ensure_ascii=False, indent=2))
            return 0

        print(f"# Telegram 註冊 chat（{len(items)} 個，依 last_seen DESC）")
        print()
        for idx, item in enumerate(items, start=1):
            print(f"--- [{idx}/{len(items)}] ---")
            print(_format_chat_text(
                item.chat_id, item.chat_label, item.registered_at,
                item.last_seen_at, item.watchlist_size,
            ))
            print()
        print("用 --chat-id <id> 查看單一 chat 的完整清單")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
