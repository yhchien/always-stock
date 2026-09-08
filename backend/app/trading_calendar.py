"""單一權威的「今天是不是交易日」判斷。

沿用全站既有慣例（`run_daily_signals.py` / `run_shadow_portfolio.py` 原本各自內嵌的檢查）：
`daily_price` 有沒有 `trade_date == target_date` 的任何一筆，是判斷某天是否為真實交易日的
唯一依據——不用曆法規則猜測（國定假日、補班日每年都會變，猜測必定會錯），只信任「ETL 真的
有抓到那天的股價資料」這個事實。

這個模組存在的目的：把原本分散在多個腳本/endpoint 裡幾乎一模一樣的
`db.query(DailyPrice.id).filter(DailyPrice.trade_date == target_date).first() is not None`
收斂成同一個函式，讓「非交易日不得產生任何邏輯 snapshot / 訂單 / 決策」這條規則有唯一的
執行點——之前 `POST /api/signals/regenerate`（手動觸發重新產生）就是因為沒有套用這個檢查，
才會在 2026-08-09（週日）產生一筆內容與 08-07（週五）幾乎相同的幽靈 SignalSnapshot（見
docs/strategy/v1_frozen_root_cause_investigation.md 分類 C）。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session


def is_trading_day(db: Session, target_date: date) -> bool:
    """`target_date` 當天是否有任何一筆 `daily_price` 資料——是的話視為真實交易日。"""
    from app.models import DailyPrice

    return (
        db.query(DailyPrice.id)
        .filter(DailyPrice.trade_date == target_date)
        .first()
        is not None
    )
