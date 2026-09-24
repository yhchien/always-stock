"""Telegram trade quality 包裝層。

優先讀既有 M25 shared snapshot，miss 時才呼叫既有
`run_trade_quality_for_user(...)` 並把結果寫回 shared cache，再同步寫入
`telegram_trade_quality_snapshots`。

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

from app.auth import ensure_demo_user
from app.industry_flow_service import get_latest_industry_trade_date
from app.models import StockMaster, TelegramTradeQualitySnapshot
from app.routers.analysis import TradeQualityResponse, run_trade_quality_for_user
from app.trade_quality_cache import (
    load_latest_ok_snapshot_for_stock_date,
    snapshot_to_response_dict,
)

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
    snapshot_trade_date = get_latest_industry_trade_date(db)
    response: Optional[TradeQualityResponse] = None

    # M25 每日快照與 Telegram 報表內容相同；跨 process 也能命中，
    # 避免 daily workflow 和 Telegram workflow 各打一份 OpenAI request。
    if snapshot_trade_date is not None:
        shared_row = load_latest_ok_snapshot_for_stock_date(
            db,
            stock_id=stock_id,
            snapshot_trade_date=snapshot_trade_date,
        )
        stock = db.get(StockMaster, stock_id)
        if shared_row is not None and stock is not None:
            payload = snapshot_to_response_dict(shared_row)
            payload["stock_name"] = stock.stock_name
            response = TradeQualityResponse(**payload)

    try:
        if response is None:
            # Telegram 沒有 user/buy-date 概念；在 auth-disabled 部署使用共用
            # demo user，讓 miss 後的結果也能寫入 M25，供後續入口共用。
            shared_user = ensure_demo_user(db)
            run_result = run_trade_quality_for_user(
                db,
                user=shared_user,
                stock_id=stock_id,
                buy_date_input=snapshot_trade_date,
                persist_db_cache=True,
                use_db_cache=True,
                persist_source=source,
                snapshot_trade_date_override=snapshot_trade_date,
            )
            response = run_result.response
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
