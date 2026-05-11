"""Telegram bot list-* commands package.

模組總覽：
- locks.py            — in-memory per-chat 鎖（給 list run / list run all 用）
- registration.py     — list register <password> 驗證 + 寫 telegram_chats
- watchlist_service.py — list add / list delete / list show CRUD
- trade_quality_service.py — list run / list watch detail；包 run_trade_quality_for_user
- formatters.py       — 純文字訊息組裝（list show / detail / daily report）
- commands.py         — 指令分派入口，由 telegram_bot.py 呼叫
"""
