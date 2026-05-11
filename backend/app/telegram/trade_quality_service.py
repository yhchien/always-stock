"""Telegram trade quality 包裝層。

呼叫既有 `run_trade_quality_for_user(db, user=None, ...)` 跑分析（user=None 自動跳過
M25 DB cache 讀寫），再把結果寫進 `telegram_trade_quality_snapshots`。

入口：
- run_for_stock(db, chat_id, stock_id) — list run <id> / list run all 共用
- load_latest_snapshot(db, chat_id, stock_id) — list watch <id> detail 用
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.industry_flow_service import get_latest_industry_trade_date
from app.models import TelegramTradeQualitySnapshot
from app.routers.analysis import TradeQualityResponse, run_trade_quality_for_user

logger = logging.getLogger(__name__)


@dataclass
class TelegramTradeQualityResult:
    success: bool
    response: Optional[TradeQualityResponse]
    error_message: Optional[str] = None


def run_for_stock(
    db: Session,
    *,
    chat_id: int,
    stock_id: str,
    source: str = "manual",
) -> TelegramTradeQualityResult:
    """跑單檔 trade quality 並寫入 Telegram 專用 snapshot 表。

    使用 DB 最近交易日當 buy_date（Telegram 沒有「買進日」概念）。
    異常時寫一筆 status='failed' 並回傳錯誤訊息給 caller 推送給使用者。
    """
    try:
        run_result = run_trade_quality_for_user(
            db,
            user=None,  # Telegram 不寫 M25 snapshot，借用 user=None 路徑
            stock_id=stock_id,
            buy_date_input=None,  # 自動 fallback 到 latest trade date
            persist_db_cache=False,
            use_db_cache=False,
        )
    except Exception as exc:
        logger.exception("Telegram trade quality run failed chat=%s stock=%s", chat_id, stock_id)
        _save_failed(
            db,
            chat_id=chat_id,
            stock_id=stock_id,
            error=f"{type(exc).__name__}: {exc}",
            source=source,
        )
        return TelegramTradeQualityResult(
            success=False,
            response=None,
            error_message=f"分析失敗：{exc}",
        )

    response = run_result.response
    snapshot_trade_date = get_latest_industry_trade_date(db)
    if snapshot_trade_date is None:
        # DB 暫無交易日 — 不寫 DB 但仍把 response 回給 caller
        logger.warning(
            "Telegram trade quality: no trade date available chat=%s stock=%s",
            chat_id, stock_id,
        )
        return TelegramTradeQualityResult(success=True, response=response)

    _save_ok(
        db,
        chat_id=chat_id,
        stock_id=stock_id,
        snapshot_trade_date=snapshot_trade_date,
        response=response,
        source=source,
    )
    return TelegramTradeQualityResult(success=True, response=response)


def load_latest_snapshot(
    db: Session, *, chat_id: int, stock_id: str
) -> Optional[TelegramTradeQualitySnapshot]:
    """讀 (chat_id, stock_id) 最新一筆 ok 快照；找不到 → None。

    list watch <id> detail 用；status='failed' 的 row 不顯示給使用者看
    （避免使用者讀到 partial / error payload）。
    """
    return (
        db.query(TelegramTradeQualitySnapshot)
        .filter(
            TelegramTradeQualitySnapshot.chat_id == chat_id,
            TelegramTradeQualitySnapshot.stock_id == stock_id,
            TelegramTradeQualitySnapshot.status == "ok",
        )
        .order_by(TelegramTradeQualitySnapshot.snapshot_trade_date.desc())
        .first()
    )


def _save_ok(
    db: Session,
    *,
    chat_id: int,
    stock_id: str,
    snapshot_trade_date,
    response: TradeQualityResponse,
    source: str,
) -> None:
    existing = (
        db.query(TelegramTradeQualitySnapshot)
        .filter(
            TelegramTradeQualitySnapshot.chat_id == chat_id,
            TelegramTradeQualitySnapshot.stock_id == stock_id,
            TelegramTradeQualitySnapshot.snapshot_trade_date == snapshot_trade_date,
        )
        .first()
    )
    key_factors_dump = (
        [f.model_dump() for f in response.key_factors]
        if response.key_factors
        else None
    )
    now = datetime.utcnow()
    if existing is not None:
        existing.rating = response.rating
        existing.rating_label = response.rating_label
        existing.classification = response.classification
        existing.summary = response.summary
        existing.target_price_low = response.target_price_low
        existing.target_price_high = response.target_price_high
        existing.exit_price_low = response.exit_price_low
        existing.exit_price_high = response.exit_price_high
        existing.report_markdown = response.report_markdown
        existing.key_factors = key_factors_dump
        existing.source = source
        existing.status = "ok"
        existing.error_message = None
        existing.generated_at = now
    else:
        db.add(
            TelegramTradeQualitySnapshot(
                chat_id=chat_id,
                stock_id=stock_id,
                snapshot_trade_date=snapshot_trade_date,
                rating=response.rating,
                rating_label=response.rating_label,
                classification=response.classification,
                summary=response.summary,
                target_price_low=response.target_price_low,
                target_price_high=response.target_price_high,
                exit_price_low=response.exit_price_low,
                exit_price_high=response.exit_price_high,
                report_markdown=response.report_markdown,
                key_factors=key_factors_dump,
                source=source,
                status="ok",
                generated_at=now,
            )
        )
    try:
        db.commit()
    except Exception:
        logger.exception(
            "Failed to persist Telegram trade quality snapshot chat=%s stock=%s",
            chat_id, stock_id,
        )
        db.rollback()


def _save_failed(
    db: Session,
    *,
    chat_id: int,
    stock_id: str,
    error: str,
    source: str,
) -> None:
    snapshot_trade_date = get_latest_industry_trade_date(db)
    if snapshot_trade_date is None:
        return  # 無交易日資料就不寫
    try:
        db.add(
            TelegramTradeQualitySnapshot(
                chat_id=chat_id,
                stock_id=stock_id,
                snapshot_trade_date=snapshot_trade_date,
                source=source,
                status="failed",
                error_message=error[:500],
                generated_at=datetime.utcnow(),
            )
        )
        db.commit()
    except Exception:
        logger.exception(
            "Failed to persist failure marker chat=%s stock=%s",
            chat_id, stock_id,
        )
        db.rollback()
