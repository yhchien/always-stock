"""指令解析 + 同步處理。

非同步指令（list run / list run all）由 telegram_bot.py 自己協調背景任務，
因為它要存取 PTB 的 `context.application.create_task` + `context.bot`。
這個模組只負責純資料層 + 同步訊息組裝。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy.orm import Session

from app.telegram import formatters, registration, trade_quality_service, watchlist_service

logger = logging.getLogger(__name__)


# ── 解析 ─────────────────────────────────────────────────────────────────────


@dataclass
class ParsedCommand:
    kind: str  # help | register | show | add | delete | watch_detail | run_single | run_all | unknown
    stock_ids: List[str] = field(default_factory=list)
    password: Optional[str] = None
    error: Optional[str] = None


def parse(text: str) -> ParsedCommand:
    """解析 `list ...` 指令；不符合任何規則回 kind='unknown'。"""
    stripped = (text or "").strip()
    if not stripped:
        return ParsedCommand(kind="unknown")

    # 預期所有指令以 "list" 開頭（caller 已過濾）
    tokens = stripped.split(None, 2)  # 至多切前兩個 whitespace；剩餘保留在 tokens[2]
    if not tokens or tokens[0].lower() != "list":
        return ParsedCommand(kind="unknown")

    if len(tokens) < 2:
        return ParsedCommand(kind="unknown")

    sub = tokens[1].lower()
    rest = tokens[2] if len(tokens) > 2 else ""

    if sub == "help":
        return ParsedCommand(kind="help")

    if sub == "register":
        password = rest.strip()
        if not password:
            return ParsedCommand(kind="register", error="請提供密碼：`list register <密碼>`")
        return ParsedCommand(kind="register", password=password)

    if sub == "show":
        return ParsedCommand(kind="show")

    if sub == "add":
        ids = watchlist_service.parse_stock_ids(rest)
        if not ids:
            return ParsedCommand(
                kind="add",
                error="請提供股票代號：`list add 2330` 或 `list add 2330, 2317`",
            )
        return ParsedCommand(kind="add", stock_ids=ids)

    if sub == "delete":
        ids = watchlist_service.parse_stock_ids(rest)
        if not ids:
            return ParsedCommand(
                kind="delete",
                error="請提供股票代號：`list delete 2330` 或 `list delete 2330, 2317`",
            )
        return ParsedCommand(kind="delete", stock_ids=ids)

    if sub == "watch":
        # `list watch <id> detail`
        watch_tokens = rest.strip().split()
        if len(watch_tokens) < 2 or watch_tokens[-1].lower() != "detail":
            return ParsedCommand(
                kind="watch_detail",
                error="格式：`list watch <代號> detail`",
            )
        stock_id = watch_tokens[0].strip()
        if not stock_id:
            return ParsedCommand(
                kind="watch_detail",
                error="格式：`list watch <代號> detail`",
            )
        return ParsedCommand(kind="watch_detail", stock_ids=[stock_id])

    if sub == "run":
        target = rest.strip()
        if not target:
            return ParsedCommand(
                kind="unknown",
                error="格式：`list run <代號>` 或 `list run all`",
            )
        if target.lower() == "all":
            return ParsedCommand(kind="run_all")
        ids = watchlist_service.parse_stock_ids(target)
        if not ids:
            return ParsedCommand(kind="run_single", error="請提供有效的股票代號。")
        # `list run` 只支援單一代號（多檔請用 `list run all` 或重複呼叫）
        return ParsedCommand(kind="run_single", stock_ids=[ids[0]])

    return ParsedCommand(kind="unknown")


# ── 同步處理（直接回 string）──────────────────────────────────────────────


def handle_help() -> str:
    return formatters.HELP_TEXT


def handle_register(
    db: Session, *, chat_id: int, password: str, chat_label: Optional[str]
) -> str:
    result = registration.register_chat(
        db, chat_id=chat_id, password=password, chat_label=chat_label
    )
    return result.message


def handle_show(db: Session, *, chat_id: int) -> str:
    snapshots = watchlist_service.list_watchlist(db, chat_id)
    return formatters.format_watchlist(snapshots)


def handle_add(db: Session, *, chat_id: int, stock_ids: List[str]) -> str:
    result = watchlist_service.add_stocks(db, chat_id=chat_id, stock_ids=stock_ids)
    return formatters.format_add_result(result)


def handle_delete(db: Session, *, chat_id: int, stock_ids: List[str]) -> str:
    result = watchlist_service.remove_stocks(db, chat_id=chat_id, stock_ids=stock_ids)
    return formatters.format_delete_result(result)


def handle_watch_detail(db: Session, *, chat_id: int, stock_id: str) -> str:
    snapshot = trade_quality_service.load_latest_snapshot(
        db, chat_id=chat_id, stock_id=stock_id
    )
    if snapshot is None:
        return formatters.format_trade_quality_not_found(stock_id)

    # 把 snapshot row 重組成 TradeQualityResponse-like 物件（直接借用 formatter）
    from app.routers.analysis import KeyFactor, TradeQualityResponse

    key_factors = None
    if snapshot.key_factors:
        try:
            key_factors = [KeyFactor(**row) for row in snapshot.key_factors]
        except Exception:
            logger.exception("Failed to parse key_factors from snapshot id=%s", snapshot.id)
            key_factors = None

    # snapshot 沒存 stock_name；用 stocks_master 補
    from app.models import StockMaster
    stock = db.get(StockMaster, snapshot.stock_id)
    stock_name = stock.stock_name if stock else snapshot.stock_id

    response = TradeQualityResponse(
        stock_id=snapshot.stock_id,
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
        report_markdown=snapshot.report_markdown or "（無詳細報告）",
        key_factors=key_factors,
        source="cache",
    )
    return formatters.format_trade_quality_detail(response)
