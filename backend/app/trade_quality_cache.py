"""
M25：自選清單 trade quality 快照 cache 模組。

責任：
1. resolve_snapshot_trade_date(db) — 同 signal archive 規則決定「該用哪個交易日當 snapshot_trade_date」
2. load_snapshot(...) — 讀取既有快照（給 analyze_trade_quality / GET /watchlist/trade-quality 用）
3. save_snapshot(...) — UPSERT 寫入快照（給 cron / on-demand / manual 共用）
4. snapshot_to_response(...) — 把 ORM row 轉成 TradeQualityResponse-like dict（不 import router 避免循環）

被以下三個入口共用：
- backend/app/routers/analysis.py（manual 入口 A）
- backend/app/routers/watchlist.py（GET /watchlist/trade-quality + POST refresh）
- backend/run_watchlist_trade_quality.py（每日 cron）
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import DailyPrice, WatchlistTradeQualitySnapshot

logger = logging.getLogger(__name__)

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# ETL workflow 跑在台北 18:00；保守抓 20:00 為 ETL 完成 + retry buffer。
# 20:00 前打 trade quality → 用前一個交易日的快照（避免吃到還沒完整的當日 ETL）
# 20:00 後 → 用當日交易日（前提：daily_price 已寫入該日）
ETL_DONE_TIME = time(hour=20, minute=0)


# M25 燈號完整性：6 個 category 全部齊備才算合格 ok 快照。
# Why: 早期 cron run 偶爾會留下 status='ok' 但 key_factors=null 的「假 ok」row
#      (LLM 沒回 key_factors，但其他欄位齊全)；前端會顯示日期但所有燈號都灰，
#      使用者體感是「燈沒亮」。下游用 is_snapshot_complete 視為 cache miss，
#      強制下次 cron / refresh 重打 LLM 補洞。
REQUIRED_KEY_FACTOR_CATEGORIES = frozenset({
    "industry", "industry_heat", "return", "chip", "technical", "fundamental",
})


def is_snapshot_complete(row: Optional[WatchlistTradeQualitySnapshot]) -> bool:
    """status='ok' 且 key_factors 涵蓋 6 個必備 category 才算完整。

    給 cron skip-check 與 HTTP cache lookup 共用，避免「假 ok」row 被當成有效快照。
    """
    if row is None or row.status != "ok":
        return False
    factors = row.key_factors
    if not isinstance(factors, list):
        return False
    seen: set[str] = set()
    for f in factors:
        if isinstance(f, dict):
            cat = str(f.get("category", "")).strip().lower()
            if cat:
                seen.add(cat)
    return REQUIRED_KEY_FACTOR_CATEGORIES.issubset(seen)


def resolve_snapshot_trade_date(
    db: Session,
    *,
    now: Optional[datetime] = None,
) -> Optional[date]:
    """回傳「該用哪個交易日當 snapshot_trade_date」。

    規則：
    - 開盤日 20:00 ~ 隔天 19:59 → 該交易日
    - 假日 / 休市 → 自動回退到最近一個 daily_price 有資料的交易日
    - DB 完全空 → None

    與 backend/run_signal_archive_returns.py 的 _resolve_default_trade_date 邏輯一致。
    """
    now_tpe = now or datetime.now(TAIPEI_TZ)
    ceiling = (
        now_tpe.date()
        if now_tpe.time() >= ETL_DONE_TIME
        else now_tpe.date() - timedelta(days=1)
    )
    return (
        db.query(func.max(DailyPrice.trade_date))
        .filter(DailyPrice.trade_date <= ceiling)
        .scalar()
    )


def load_snapshot(
    db: Session,
    *,
    user_id: int,
    stock_id: str,
    buy_date: date,
    snapshot_trade_date: date,
) -> Optional[WatchlistTradeQualitySnapshot]:
    """讀取特定 (user_id, stock_id, buy_date, snapshot_trade_date) 的快照。"""
    return (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(
            WatchlistTradeQualitySnapshot.user_id == user_id,
            WatchlistTradeQualitySnapshot.stock_id == stock_id,
            WatchlistTradeQualitySnapshot.buy_date == buy_date,
            WatchlistTradeQualitySnapshot.snapshot_trade_date == snapshot_trade_date,
        )
        .first()
    )


def load_latest_ok_snapshot(
    db: Session,
    *,
    user_id: int,
    stock_id: str,
    buy_date: date,
    before_or_eq_trade_date: Optional[date] = None,
) -> Optional[WatchlistTradeQualitySnapshot]:
    """讀取使用者該檔股票 buy_date 對應的最近一筆「完整 ok」快照。

    用途：
    - failed 今日快照時 fallback 顯示舊快照
    - delta layer 取「上一次成功評級」用於 B → C 比對
    """
    q = (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(
            WatchlistTradeQualitySnapshot.user_id == user_id,
            WatchlistTradeQualitySnapshot.stock_id == stock_id,
            WatchlistTradeQualitySnapshot.buy_date == buy_date,
            WatchlistTradeQualitySnapshot.status == "ok",
        )
    )
    if before_or_eq_trade_date is not None:
        q = q.filter(
            WatchlistTradeQualitySnapshot.snapshot_trade_date <= before_or_eq_trade_date
        )
    rows = q.order_by(WatchlistTradeQualitySnapshot.snapshot_trade_date.desc()).all()
    for row in rows:
        if is_snapshot_complete(row):
            return row
    return None


def load_recent_ok_snapshots(
    db: Session,
    *,
    user_id: int,
    stock_id: str,
    buy_date: date,
    limit: int = 3,
    before_or_eq_trade_date: Optional[date] = None,
) -> List[WatchlistTradeQualitySnapshot]:
    """讀取最近 N 筆「完整 ok」快照，給 KeyFactorsTimeline 趨勢顯示用。

    回傳順序：snapshot_trade_date 倒序（最新在前）；caller 若要時間軸由舊→新可自行 reverse。
    若該股累積快照不足 N 筆，回傳實際筆數（可能空 list）。
    """
    q = (
        db.query(WatchlistTradeQualitySnapshot)
        .filter(
            WatchlistTradeQualitySnapshot.user_id == user_id,
            WatchlistTradeQualitySnapshot.stock_id == stock_id,
            WatchlistTradeQualitySnapshot.buy_date == buy_date,
            WatchlistTradeQualitySnapshot.status == "ok",
        )
    )
    if before_or_eq_trade_date is not None:
        q = q.filter(
            WatchlistTradeQualitySnapshot.snapshot_trade_date <= before_or_eq_trade_date
        )
    rows = q.order_by(WatchlistTradeQualitySnapshot.snapshot_trade_date.desc()).all()
    complete_rows: List[WatchlistTradeQualitySnapshot] = []
    for row in rows:
        if is_snapshot_complete(row):
            complete_rows.append(row)
            if len(complete_rows) >= max(1, limit):
                break
    return complete_rows


def save_snapshot_ok(
    db: Session,
    *,
    user_id: int,
    stock_id: str,
    buy_date: date,
    snapshot_trade_date: date,
    response_payload: dict,
    source: str,
) -> WatchlistTradeQualitySnapshot:
    """UPSERT 寫入一筆 status='ok' 快照。

    response_payload 必須是 TradeQualityResponse.model_dump() 的 dict（含 key_factors 巢狀）。
    若該 (user_id, stock_id, buy_date, snapshot_trade_date) 已存在 → 覆蓋。
    """
    existing = load_snapshot(
        db,
        user_id=user_id,
        stock_id=stock_id,
        buy_date=buy_date,
        snapshot_trade_date=snapshot_trade_date,
    )

    fields = _payload_to_columns(response_payload)
    fields.update({
        "user_id": user_id,
        "stock_id": stock_id,
        "buy_date": buy_date,
        "snapshot_trade_date": snapshot_trade_date,
        "source": source,
        "status": "ok",
        "error_message": None,
        "generated_at": datetime.utcnow(),
    })

    if existing is None:
        row = WatchlistTradeQualitySnapshot(**fields)
        db.add(row)
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
        row = existing
    db.commit()
    db.refresh(row)
    return row


def save_snapshot_failed(
    db: Session,
    *,
    user_id: int,
    stock_id: str,
    buy_date: date,
    snapshot_trade_date: date,
    error_message: str,
    source: str,
) -> WatchlistTradeQualitySnapshot:
    """UPSERT 寫入一筆 status='failed' 快照。

    用途：cron 對某檔 OpenAI 失敗、避免重複 retry 寫多筆，同時讓前端能 detect failure
    並 fallback 顯示舊快照 + 顯示重試按鈕。
    """
    existing = load_snapshot(
        db,
        user_id=user_id,
        stock_id=stock_id,
        buy_date=buy_date,
        snapshot_trade_date=snapshot_trade_date,
    )
    fields = {
        "user_id": user_id,
        "stock_id": stock_id,
        "buy_date": buy_date,
        "snapshot_trade_date": snapshot_trade_date,
        "source": source,
        "status": "failed",
        "error_message": (error_message or "")[:2000],
        "generated_at": datetime.utcnow(),
    }
    if existing is None:
        row = WatchlistTradeQualitySnapshot(**fields)
        db.add(row)
    else:
        # failed 覆蓋既有 row 時，舊的 ok payload 也會被清掉（避免被誤讀為 ok）
        # 但 caller 可以用 load_latest_ok_snapshot fallback 找更早的 ok
        for k in [
            "rating", "rating_label", "classification", "market_state", "quadrant",
            "expectation_gap", "action", "summary", "core_logic", "risk_level",
            "target_price_low", "target_price_high", "time_horizon_days",
            "exit_price_low", "exit_price_high", "max_holding_days",
            "report_markdown", "key_factors", "sections_json",
        ]:
            setattr(existing, k, None)
        for k, v in fields.items():
            setattr(existing, k, v)
        row = existing
    db.commit()
    db.refresh(row)
    return row


def snapshot_to_response_dict(row: WatchlistTradeQualitySnapshot) -> dict:
    """把 ORM row 轉成可餵給 TradeQualityResponse(**dict) 的 dict。

    注意：caller 仍需自己補 stock_name（ORM 表沒存）+ source 改寫為 "cache"。
    """
    return {
        "stock_id": row.stock_id,
        # stock_name 由 caller 從 stocks_master 補
        "buy_date": row.buy_date.isoformat() if row.buy_date else "",
        "rating": row.rating or "NEUTRAL",
        "rating_label": row.rating_label or "中立",
        "classification": row.classification,
        "market_state": row.market_state,
        "quadrant": row.quadrant,
        "expectation_gap": row.expectation_gap,
        "action": row.action,
        "summary": row.summary or "",
        "core_logic": row.core_logic,
        "risk_level": row.risk_level,
        "target_price_low": row.target_price_low,
        "target_price_high": row.target_price_high,
        "time_horizon_days": row.time_horizon_days,
        "exit_price_low": row.exit_price_low,
        "exit_price_high": row.exit_price_high,
        "max_holding_days": row.max_holding_days,
        "report_markdown": row.report_markdown or "",
        "key_factors": row.key_factors,
        # M3 sections（舊快照無此欄位時為 None，前端顯示 fallback）
        **({
            "action_one_liner": row.sections_json.get("action_one_liner"),
            "industry_section": row.sections_json.get("industry_section"),
            "chip_section": row.sections_json.get("chip_section"),
            "fundamental_section": row.sections_json.get("fundamental_section"),
            "technical_section": row.sections_json.get("technical_section"),
            "peer_section": row.sections_json.get("peer_section"),
            "news_section": row.sections_json.get("news_section"),
        } if row.sections_json else {}),
        "warnings": [],
        "source": "cache",
    }


def _payload_to_columns(payload: dict) -> dict[str, Any]:
    """把 TradeQualityResponse.model_dump() 拆成 ORM 欄位 dict（不含 user_id / 索引欄）。"""
    key_factors = payload.get("key_factors")
    # KeyFactor instances → list of plain dict（給 JSON column 存）
    if key_factors and isinstance(key_factors, list):
        normalized: List[dict] = []
        for kf in key_factors:
            if isinstance(kf, dict):
                normalized.append(kf)
            elif hasattr(kf, "model_dump"):
                normalized.append(kf.model_dump())
            elif hasattr(kf, "dict"):
                normalized.append(kf.dict())
        key_factors = normalized or None

    return {
        "rating": payload.get("rating"),
        "rating_label": payload.get("rating_label"),
        "classification": payload.get("classification"),
        "market_state": payload.get("market_state"),
        "quadrant": payload.get("quadrant"),
        "expectation_gap": payload.get("expectation_gap"),
        "action": payload.get("action"),
        "summary": payload.get("summary"),
        "core_logic": payload.get("core_logic"),
        "risk_level": payload.get("risk_level"),
        "target_price_low": payload.get("target_price_low"),
        "target_price_high": payload.get("target_price_high"),
        "time_horizon_days": payload.get("time_horizon_days"),
        "exit_price_low": payload.get("exit_price_low"),
        "exit_price_high": payload.get("exit_price_high"),
        "max_holding_days": payload.get("max_holding_days"),
        "report_markdown": payload.get("report_markdown"),
        "key_factors": key_factors,
        # M3 sections 打包進單一 JSON 欄位，減少 ALTER TABLE 次數
        "sections_json": {
            "action_one_liner": payload.get("action_one_liner"),
            "industry_section": payload.get("industry_section"),
            "chip_section": payload.get("chip_section"),
            "fundamental_section": payload.get("fundamental_section"),
            "technical_section": payload.get("technical_section"),
            "peer_section": payload.get("peer_section"),
            "news_section": payload.get("news_section"),
        } if any(
            payload.get(k) is not None
            for k in ("action_one_liner", "industry_section", "chip_section",
                      "fundamental_section", "technical_section", "peer_section", "news_section")
        ) else None,
    }
