"""M23 後續：個股「一個月內資金行情可期待價格區間」預測。

對應 prompt: backend/app/prompts/expectation_price.md

職責：
  - `build_expectation_context(db, stock_id, first_detected_date)` — 從 DB 組裝 prompt 需要的 INPUT JSON
  - `generate_for_stock(db, stock_id, *, first_detected_date=None, source)` — 呼叫 OpenAI、parse、UPSERT
  - `generate_for_new_signals(db, snapshot_date, *, source="cron")` — cron 入口；
    從 signal_watch_hits 取出 first_seen_date == snapshot_date 的新進股，逐檔跑
  - `update_hit_targets(db, target_date)` — 每日 cron 用當日收盤價比對 conservative / dream，
    首次達標寫入 `hit_conservative_at` / `hit_dream_at`

設計：
  - 與 M23 LLM caller 用同一份 OpenAI / Responses API client 設定
  - 失敗時寫 status='failed' + error_message，不阻擋整批
  - theme_score（0-3）由後端 deterministic mapping：
      LEADER + theme_fit=HIGH → 3
      LEADER + theme_fit=MEDIUM → 2
      FOLLOWER + theme_fit=HIGH → 2
      其他 HIGH → 1
      MEDIUM → 1
      LOW / NONE → 0
    prompt 允許 LLM 自己再判，但 backend 提供 anchor 確保有 stable baseline
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DailyPrice,
    DailyValuation,
    FinancialStatement,
    InstStockFlow,
    MarginTrade,
    MonthlyRevenue,
    SignalExpectationPrice,
    SignalSnapshot,
    SignalWatchHit,
    StockMaster,
)
from app.settings import get_openai_api_key
from app.signals.market_margin import compute_market_margin_snapshot

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "expectation_price.md"
)

_FALLBACK_MODEL = "gpt-5.4-mini"
DEFAULT_MODEL = os.getenv(
    "OPENAI_EXPECTATION_PRICE_MODEL",
    os.getenv("OPENAI_MODEL", _FALLBACK_MODEL),
).strip()

_MAX_OUTPUT_TOKENS = 4000
_OPENAI_TIMEOUT_SECONDS = 90.0
_OPENAI_MAX_RETRIES = 1
_PROMPT_CACHE_RETENTION = "in_memory"
_CACHE_KEY = "m23:expectation-price:v1"


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


@dataclass
class ExpectationContext:
    """Wrapper：build_expectation_context 回傳的兩塊資料。

    - `payload` — 直接餵給 LLM 的 INPUT JSON dict
    - `meta` — 給 persist 層用的附加資訊（detected_day_high / close / current_price /
      detected_type / industry / sub_industry / first_detected_date / latest_detected_date）
    """

    payload: Dict[str, Any]
    meta: Dict[str, Any]


def build_expectation_context(
    db: Session,
    stock_id: str,
    *,
    first_detected_date: Optional[date] = None,
) -> ExpectationContext:
    """組裝餵給 prompt 的 INPUT JSON。

    若 `first_detected_date` 未提供，自動從 `signal_watch_hits` 取該股最早的 `snapshot_date`；
    若該股完全沒被 M23 抓過，raise ValueError（caller 應該擋掉這種 case）。
    """
    stock = db.query(StockMaster).filter(StockMaster.stock_id == stock_id).first()
    if stock is None:
        raise ValueError(f"unknown stock_id={stock_id}")

    # 1. 解析 first_detected_date / latest_detected_date / hit_count
    hit_rows = (
        db.query(SignalWatchHit)
        .filter(SignalWatchHit.stock_id == stock_id)
        .order_by(SignalWatchHit.snapshot_date.asc())
        .all()
    )
    if not hit_rows and first_detected_date is None:
        raise ValueError(
            f"stock_id={stock_id} has no signal_watch_hits; cannot infer first_detected_date"
        )

    if first_detected_date is None:
        first_detected_date = hit_rows[0].snapshot_date

    latest_hit = hit_rows[-1] if hit_rows else None
    latest_detected_date = (
        latest_hit.snapshot_date if latest_hit is not None else first_detected_date
    )
    hit_count = len(hit_rows)

    # detected_day OHLC（first_detected_date 那天）
    detected_price = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date == first_detected_date,
        )
        .first()
    )
    detected_day_high = detected_price.high_price if detected_price else None
    detected_day_close = detected_price.close_price if detected_price else None

    # 2. 找最新一個交易日（<= today）給 current_price / 技術
    latest_price_row = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )
    if latest_price_row is None:
        raise ValueError(f"stock_id={stock_id} has no daily_price")
    today_date = latest_price_row.trade_date
    current_price = latest_price_row.close_price

    # 3. price_data：近 21 個交易日 OHLC（給 high_5d/10d/20d、low_5d/10d、ma5/10/20 用）
    price_rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date <= today_date,
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(30)
        .all()
    )
    price_data = _compute_price_data(price_rows, current_price)
    # detected_day_high / detected_day_close 不是「近 21 日 OHLC」計算出來，
    # 而是 first_detected_date 那天的 high/close；統一塞進 price_data 對齊 prompt schema
    price_data["detected_day_high"] = detected_day_high
    price_data["detected_day_close"] = detected_day_close

    # tracking_performance
    days_since_first_detected = max(
        (today_date - first_detected_date).days,
        0,
    )
    return_since_first_detected_pct: Optional[float] = None
    if detected_day_close and current_price:
        return_since_first_detected_pct = round(
            (current_price - detected_day_close) / detected_day_close * 100.0, 2
        )

    max_positive_return_pct = (
        max([(h.max_positive_return_pct or 0.0) for h in hit_rows], default=None)
        if hit_rows
        else None
    )
    max_negative_return_pct = (
        min([(h.max_negative_return_pct or 0.0) for h in hit_rows], default=None)
        if hit_rows
        else None
    )
    has_reached_new_high_after_detected = False
    if detected_day_high is not None:
        for row in price_rows:
            if row.trade_date <= first_detected_date:
                continue
            if (row.high_price or 0) > detected_day_high:
                has_reached_new_high_after_detected = True
                break
    failed_follow_through = bool(
        max_positive_return_pct is not None
        and max_positive_return_pct < 3.0
        and max_negative_return_pct is not None
        and max_negative_return_pct < -8.0
    )

    # 4. institution_flow（近 5 個交易日 by inst_type）
    inst_flow = _compute_institution_flow(db, stock_id, today_date)

    # 5. margin_short（個股近 5 日 + 大盤 climate）
    margin = _compute_margin_short(db, stock_id, today_date)

    # 6. fundamental
    fundamental = _compute_fundamental(db, stock_id, today_date)

    # 7. theme_context（從 signal_watch_hits.theme 抽）
    theme_context = _compute_theme_context(
        db,
        stock_id,
        latest_hit=latest_hit,
        snapshot_date=latest_detected_date,
    )

    # 8. previous_report — 最近一次 LLM reason 摘要
    previous_report_summary = ""
    if latest_hit is not None:
        previous_report_summary = (latest_hit.reason or "")[:1200]

    # 9. detected_type / follower_subtype（從最新 hit 抽）
    detected_type = (latest_hit.signal_type if latest_hit else None) or "FOLLOWER"
    follower_subtype = None
    if latest_hit is not None:
        theme_blob = latest_hit.theme if isinstance(latest_hit.theme, dict) else {}
        # M23 並沒有顯式 follower_subtype；保留 None 讓 LLM 自己判
        follower_subtype = theme_blob.get("follower_subtype")

    payload = {
        "date": today_date.isoformat(),
        "stock": {
            "code": stock_id,
            "name": stock.stock_name,
            "industry": stock.industry_name,
            "sub_industry": stock.sub_industry,
            "detected_type": detected_type,
            "follower_subtype": follower_subtype,
            "detected_grade": None,  # M23 未提供
            "first_detected_date": first_detected_date.isoformat(),
            "latest_detected_date": latest_detected_date.isoformat(),
            "hit_count": hit_count,
            "days_since_first_detected": days_since_first_detected,
        },
        "price_data": price_data,
        "tracking_performance": {
            "return_since_first_detected_pct": return_since_first_detected_pct,
            "max_positive_return_pct": max_positive_return_pct,
            "max_negative_return_pct": max_negative_return_pct,
            "has_reached_new_high_after_detected": has_reached_new_high_after_detected,
            "failed_follow_through": failed_follow_through,
        },
        "institution_flow": inst_flow,
        "margin_short": margin,
        "fundamental": fundamental,
        "theme_context": theme_context,
        "previous_report": {
            "summary": previous_report_summary,
        },
    }

    meta = {
        "stock_name": stock.stock_name,
        "industry_name": stock.industry_name,
        "sub_industry": stock.sub_industry,
        "first_detected_date": first_detected_date,
        "latest_detected_date": latest_detected_date,
        "detected_type": detected_type,
        "detected_day_high": detected_day_high,
        "detected_day_close": detected_day_close,
        "current_price": current_price,
    }
    return ExpectationContext(payload=payload, meta=meta)


def _compute_price_data(
    price_rows: List[DailyPrice],
    current_price: Optional[float],
) -> Dict[str, Any]:
    """近 21 交易日 OHLC → high/low N 日 + ma5/10/20 + 漲跌幅 + 量能比。

    price_rows: 由 DESC 排序的 max 30 筆。
    """
    if not price_rows:
        return {
            "current_price": current_price,
            "detected_day_high": None,
            "detected_day_close": None,
            "high_5d": None,
            "high_10d": None,
            "high_20d": None,
            "low_5d": None,
            "low_10d": None,
            "price_change_1d_pct": None,
            "price_change_3d_pct": None,
            "price_change_5d_pct": None,
            "price_change_10d_pct": None,
            "volume_ratio_5d": None,
            "volume_ratio_20d": None,
            "ma5": None,
            "ma10": None,
            "ma20": None,
        }

    # rows DESC → 第一筆是最新
    rows_desc = price_rows  # already desc
    highs = [r.high_price for r in rows_desc if r.high_price is not None]
    lows = [r.low_price for r in rows_desc if r.low_price is not None]
    closes = [r.close_price for r in rows_desc if r.close_price is not None]
    volumes = [r.volume for r in rows_desc if r.volume is not None]

    def top_max(values: List[float], n: int) -> Optional[float]:
        if not values:
            return None
        return max(values[:n]) if len(values) >= 1 else None

    def top_min(values: List[float], n: int) -> Optional[float]:
        if not values:
            return None
        return min(values[:n])

    def pct_change(values: List[float], n: int) -> Optional[float]:
        if len(values) <= n:
            return None
        base = values[n]
        latest = values[0]
        if base in (None, 0) or latest is None:
            return None
        return round((latest - base) / base * 100.0, 2)

    def average(values: List[float], n: int) -> Optional[float]:
        if len(values) < n:
            return None
        return round(sum(values[:n]) / n, 4)

    high_5d = top_max(highs, 5)
    high_10d = top_max(highs, 10)
    high_20d = top_max(highs, 20)
    low_5d = top_min(lows, 5)
    low_10d = top_min(lows, 10)

    volume_5d_avg = average(volumes, 5)
    volume_20d_avg = average(volumes, 20)
    volume_latest = volumes[0] if volumes else None
    volume_ratio_5d = None
    volume_ratio_20d = None
    if volume_latest is not None and volume_5d_avg:
        volume_ratio_5d = round(volume_latest / volume_5d_avg, 2)
    if volume_latest is not None and volume_20d_avg:
        volume_ratio_20d = round(volume_latest / volume_20d_avg, 2)

    return {
        "current_price": current_price,
        # detected_day_high / detected_day_close 由 caller 在 payload 外層填（meta 用 first_detected_date row）
        # 這裡僅放置 placeholder，下方 build_expectation_context 會覆寫
        "high_5d": high_5d,
        "high_10d": high_10d,
        "high_20d": high_20d,
        "low_5d": low_5d,
        "low_10d": low_10d,
        "price_change_1d_pct": pct_change(closes, 1),
        "price_change_3d_pct": pct_change(closes, 3),
        "price_change_5d_pct": pct_change(closes, 5),
        "price_change_10d_pct": pct_change(closes, 10),
        "volume_ratio_5d": volume_ratio_5d,
        "volume_ratio_20d": volume_ratio_20d,
        "ma5": average(closes, 5),
        "ma10": average(closes, 10),
        "ma20": average(closes, 20),
    }


def _compute_institution_flow(
    db: Session,
    stock_id: str,
    today_date: date,
) -> Dict[str, Any]:
    """近 5 個交易日法人合計買賣超（買 - 賣，單位：張）。

    inst_stock_flow 是 daily 表，每天 3 種 inst_type；
    我們聚合 1d / 3d / 5d 各別與合計。
    """
    # 取近 5 個 distinct trade_date（含 today）
    recent_dates_q = (
        db.query(InstStockFlow.trade_date)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date <= today_date,
        )
        .distinct()
        .order_by(InstStockFlow.trade_date.desc())
        .limit(5)
    )
    recent_dates = [row[0] for row in recent_dates_q.all()]
    if not recent_dates:
        return _empty_institution_flow()

    rows = (
        db.query(InstStockFlow)
        .filter(
            InstStockFlow.stock_id == stock_id,
            InstStockFlow.trade_date.in_(recent_dates),
        )
        .all()
    )
    # 以 trade_date desc 排序 → recent_dates 已 desc
    by_date_inst: Dict[Tuple[date, str], float] = {}
    for r in rows:
        by_date_inst[(r.trade_date, r.inst_type)] = (r.net_shares or 0.0) / 1000.0  # 股 → 張

    def sum_window(inst_type: str, n: int) -> Optional[float]:
        if len(recent_dates) < n:
            n = len(recent_dates)
        s = 0.0
        any_found = False
        for d in recent_dates[:n]:
            v = by_date_inst.get((d, inst_type))
            if v is not None:
                any_found = True
                s += v
        return round(s, 0) if any_found else None

    def total_window(n: int) -> Optional[float]:
        a = sum_window("foreign", n) or 0.0
        b = sum_window("trust", n) or 0.0
        c = sum_window("dealer", n) or 0.0
        return round(a + b + c, 0)

    # institution_buy_days_5d：合計 net_shares > 0 的天數
    buy_days = 0
    for d in recent_dates[:5]:
        total = 0.0
        for inst in ("foreign", "trust", "dealer"):
            v = by_date_inst.get((d, inst))
            if v is not None:
                total += v
        if total > 0:
            buy_days += 1

    return {
        "foreign_flow_1d": sum_window("foreign", 1),
        "foreign_flow_3d": sum_window("foreign", 3),
        "foreign_flow_5d": sum_window("foreign", 5),
        "investment_trust_flow_1d": sum_window("trust", 1),
        "investment_trust_flow_3d": sum_window("trust", 3),
        "investment_trust_flow_5d": sum_window("trust", 5),
        "dealer_flow_1d": sum_window("dealer", 1),
        "dealer_flow_3d": sum_window("dealer", 3),
        "dealer_flow_5d": sum_window("dealer", 5),
        "total_institution_flow_1d": total_window(1),
        "total_institution_flow_3d": total_window(3),
        "total_institution_flow_5d": total_window(5),
        "institution_buy_days_5d": buy_days,
    }


def _empty_institution_flow() -> Dict[str, Any]:
    return {k: None for k in (
        "foreign_flow_1d", "foreign_flow_3d", "foreign_flow_5d",
        "investment_trust_flow_1d", "investment_trust_flow_3d", "investment_trust_flow_5d",
        "dealer_flow_1d", "dealer_flow_3d", "dealer_flow_5d",
        "total_institution_flow_1d", "total_institution_flow_3d", "total_institution_flow_5d",
    )} | {"institution_buy_days_5d": 0}


def _compute_margin_short(
    db: Session,
    stock_id: str,
    today_date: date,
) -> Dict[str, Any]:
    """個股近 5 日融資融券變化（%）+ 大盤 climate。"""
    rows = (
        db.query(MarginTrade)
        .filter(
            MarginTrade.stock_id == stock_id,
            MarginTrade.trade_date <= today_date,
        )
        .order_by(MarginTrade.trade_date.desc())
        .limit(6)
        .all()
    )

    def pct_change(rows_list: List[MarginTrade], field: str, n: int) -> Optional[float]:
        if len(rows_list) <= n:
            return None
        latest = getattr(rows_list[0], field)
        base = getattr(rows_list[n], field)
        if base in (None, 0) or latest is None:
            return None
        return round((latest - base) / base * 100.0, 2)

    margin_short_ratio_pct: Optional[float] = None
    if rows:
        latest = rows[0]
        if latest.margin_balance and latest.short_balance:
            try:
                margin_short_ratio_pct = round(
                    (float(latest.short_balance) / float(latest.margin_balance)) * 100.0, 2
                )
            except ZeroDivisionError:
                margin_short_ratio_pct = None

    # 大盤 margin climate
    market_climate_label = "unknown"
    market_climate_reason = ""
    try:
        snapshot = compute_market_margin_snapshot(db, today_date)
        market_climate_label = snapshot.get("climate_label", "unknown")
        market_climate_reason = snapshot.get("climate_reason", "")
    except Exception:
        logger.exception("compute_market_margin_snapshot failed for %s", today_date)

    return {
        "market_margin_climate": market_climate_label,
        "market_margin_reason": market_climate_reason,
        "stock_margin_change_1d_pct": pct_change(rows, "margin_balance", 1),
        "stock_margin_change_3d_pct": pct_change(rows, "margin_balance", 3),
        "stock_margin_change_5d_pct": pct_change(rows, "margin_balance", 5),
        "stock_short_change_1d_pct": pct_change(rows, "short_balance", 1),
        "stock_short_change_3d_pct": pct_change(rows, "short_balance", 3),
        "stock_short_change_5d_pct": pct_change(rows, "short_balance", 5),
        "margin_short_ratio_pct": margin_short_ratio_pct,
    }


def _compute_fundamental(
    db: Session,
    stock_id: str,
    today_date: date,
) -> Dict[str, Any]:
    """current_pe + EPS（TTM 估算）+ revenue YoY/MoM。"""
    latest_val = (
        db.query(DailyValuation)
        .filter(
            DailyValuation.stock_id == stock_id,
            DailyValuation.trade_date <= today_date,
        )
        .order_by(DailyValuation.trade_date.desc())
        .first()
    )
    current_pe = latest_val.per if latest_val and latest_val.per and latest_val.per > 0 else None

    # 估 TTM EPS：用 latest close / current_pe（粗略）
    eps_ttm: Optional[float] = None
    if current_pe and latest_val:
        latest_close = (
            db.query(DailyPrice)
            .filter(
                DailyPrice.stock_id == stock_id,
                DailyPrice.trade_date == latest_val.trade_date,
            )
            .first()
        )
        if latest_close and latest_close.close_price:
            try:
                eps_ttm = round(latest_close.close_price / current_pe, 4)
            except ZeroDivisionError:
                eps_ttm = None

    # PE 歷史區間（近 252 trading days）— low / mid / high
    pe_series_rows = (
        db.query(DailyValuation.per)
        .filter(
            DailyValuation.stock_id == stock_id,
            DailyValuation.trade_date <= today_date,
            DailyValuation.per != None,  # noqa: E711
            DailyValuation.per > 0,
        )
        .order_by(DailyValuation.trade_date.desc())
        .limit(252)
        .all()
    )
    pe_values = sorted([r[0] for r in pe_series_rows if r[0] is not None])
    historical_pe_low = pe_values[int(len(pe_values) * 0.1)] if pe_values else None
    historical_pe_mid = pe_values[int(len(pe_values) * 0.5)] if pe_values else None
    historical_pe_high = pe_values[int(len(pe_values) * 0.9)] if pe_values else None

    # revenue YoY / MoM（最近一筆 monthly_revenue）
    latest_rev = (
        db.query(MonthlyRevenue)
        .filter(MonthlyRevenue.stock_id == stock_id)
        .order_by(MonthlyRevenue.revenue_month.desc())
        .first()
    )
    revenue_yoy_pct = latest_rev.yoy_pct if latest_rev else None
    revenue_mom_pct = latest_rev.mom_pct if latest_rev else None

    return {
        "latest_eps_ttm": eps_ttm,
        "estimated_forward_eps": None,  # DB 無 forward EPS
        "current_pe": current_pe,
        "historical_pe_low": historical_pe_low,
        "historical_pe_mid": historical_pe_mid,
        "historical_pe_high": historical_pe_high,
        "revenue_yoy_pct": revenue_yoy_pct,
        "revenue_mom_pct": revenue_mom_pct,
        "gross_margin_trend": "unknown",  # 沒精準資料源
        "earnings_momentum": "unknown",
    }


def _compute_theme_context(
    db: Session,
    stock_id: str,
    *,
    latest_hit: Optional[SignalWatchHit],
    snapshot_date: date,
) -> Dict[str, Any]:
    """從 latest signal_watch_hits.theme + 該 snapshot 同產業命中數推導 theme_context。

    theme_score deterministic mapping：
      detected_type=LEADER + theme_fit=HIGH      → 3
      detected_type=LEADER + theme_fit=MEDIUM    → 2
      detected_type=FOLLOWER + theme_fit=HIGH    → 2
      其他 HIGH                                   → 2
      MEDIUM                                       → 1
      LOW / NONE                                   → 0
    若 watchlist payload 有 theme_score，優先用該值；否則用上面 mapping。
    """
    main_theme = ""
    theme_fit = "MEDIUM"
    theme_duration = "unknown"
    theme_score_payload: Optional[int] = None
    detected_type = "FOLLOWER"

    if latest_hit is not None and isinstance(latest_hit.theme, dict):
        main_theme = latest_hit.theme.get("main_theme") or ""
        theme_duration = latest_hit.theme.get("theme_duration") or "unknown"
        raw_score = latest_hit.theme.get("theme_score")
        if isinstance(raw_score, (int, float)):
            theme_score_payload = int(raw_score)
    if latest_hit is not None:
        detected_type = latest_hit.signal_type or "FOLLOWER"

    # theme_fit 從 watchlist signals 或舊 payload；signal_watch_hits 並未直接存
    # 嘗試從 SignalSnapshot.watchlist 找
    snap = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == snapshot_date)
        .first()
    )
    if snap and isinstance(snap.watchlist, list):
        for entry in snap.watchlist:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("stock") or entry.get("stock_id") or "") == stock_id:
                tf = entry.get("theme_fit")
                if isinstance(tf, str):
                    theme_fit = tf.upper()
                break

    # theme_score 後端 deterministic
    if theme_score_payload is not None:
        theme_score = theme_score_payload
    else:
        theme_score = _derive_theme_score(detected_type, theme_fit)

    theme_is_market_mainstream = theme_score >= 2 and theme_fit in ("HIGH", "MEDIUM")

    # same_theme_strong_stock_count：同 snapshot 同產業命中數
    same_count = 0
    same_group_strength = "weak"
    if snap and isinstance(snap.watchlist, list):
        same_industry_stocks = [
            entry for entry in snap.watchlist
            if isinstance(entry, dict)
            and entry.get("industry")
            and latest_hit is not None
            and entry.get("industry") == latest_hit.industry_name
        ]
        same_count = len(same_industry_stocks)
        if same_count >= 3:
            same_group_strength = "strong"
        elif same_count == 2:
            same_group_strength = "moderate"

    # market_sentiment 從 market_context.market_state 推
    market_sentiment = "neutral"
    if snap and isinstance(snap.market_context, dict):
        state = (snap.market_context.get("market_state") or "").upper()
        if state in ("STRONG_BULL", "STRUCTURAL_BULL"):
            market_sentiment = "risk_on"
        elif state == "WEAK":
            market_sentiment = "risk_off"

    return {
        "main_theme": main_theme,
        "theme_is_market_mainstream": theme_is_market_mainstream,
        "theme_score": theme_score,
        "theme_duration": theme_duration,
        "theme_fit": theme_fit,
        "market_sentiment": market_sentiment,
        "same_group_or_peer_strength": same_group_strength,
        "same_theme_strong_stock_count": same_count,
    }


def _derive_theme_score(detected_type: str, theme_fit: str) -> int:
    """deterministic mapping（caller 可由 prompt 內 step 1 細部覆寫）。"""
    detected_type = (detected_type or "").upper()
    theme_fit = (theme_fit or "").upper()
    if theme_fit == "HIGH" and detected_type == "LEADER":
        return 3
    if theme_fit == "HIGH":
        return 2
    if theme_fit == "MEDIUM":
        return 1
    return 0


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _call_llm(
    payload: Dict[str, Any],
    *,
    model: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """呼叫 OpenAI Responses API；回 (parsed JSON | None, diagnostic dict)。"""
    diagnostic: Dict[str, Any] = {
        "stage": "expectation_price",
        "model": model,
        "use_web_search": False,
        "prompt_cache_key": _CACHE_KEY,
        "status": "ok",
    }
    api_key = get_openai_api_key()
    if not api_key:
        diagnostic["status"] = "api_key_missing"
        diagnostic["message"] = "OPENAI_API_KEY not configured."
        return None, diagnostic

    client = OpenAI(
        api_key=api_key,
        timeout=_OPENAI_TIMEOUT_SECONDS,
        max_retries=_OPENAI_MAX_RETRIES,
    )
    try:
        system_prompt = _load_system_prompt()
    except FileNotFoundError:
        diagnostic["status"] = "prompt_missing"
        diagnostic["message"] = f"prompt file not found: {_PROMPT_PATH}"
        return None, diagnostic

    user_msg = (
        "請依照系統 prompt 規則，對下列股票輸出 expectation price JSON。\n"
        "只輸出 JSON，不要 markdown code fence。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )

    try:
        kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": user_msg,
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
            "prompt_cache_retention": _PROMPT_CACHE_RETENTION,
            "prompt_cache_key": _CACHE_KEY,
        }
        response = client.responses.create(**kwargs)
        raw = getattr(response, "output_text", None)
        if not raw:
            # fallback：嘗試從 response.output[].content[].text 抽
            output = getattr(response, "output", None) or []
            chunks: List[str] = []
            for item in output:
                if getattr(item, "type", None) != "message":
                    continue
                for c in getattr(item, "content", None) or []:
                    if getattr(c, "type", None) == "output_text":
                        chunks.append(getattr(c, "text", "") or "")
            raw = "\n".join(chunks)
        if not raw or not raw.strip():
            diagnostic["status"] = "empty_output"
            return None, diagnostic
        parsed = _extract_json(raw)
        if parsed is None:
            diagnostic["status"] = "invalid_json"
            diagnostic["raw_preview"] = raw.strip()[:500]
            return None, diagnostic
        # 抽 token usage（若 SDK 提供）
        usage = getattr(response, "usage", None)
        if usage is not None:
            diagnostic["total_tokens"] = getattr(usage, "total_tokens", None)
        return parsed, diagnostic
    except Exception as exc:
        logger.exception("expectation_price LLM call failed model=%s", model)
        diagnostic["status"] = "openai_exception"
        diagnostic["exception_type"] = exc.__class__.__name__
        diagnostic["message"] = str(exc)[:300]
        return None, diagnostic


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        elif "```" in text:
            text = text[: text.rindex("```")]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------


def _upsert_expectation_row(
    db: Session,
    *,
    stock_id: str,
    meta: Dict[str, Any],
    parsed: Optional[Dict[str, Any]],
    diagnostic: Dict[str, Any],
    source: str,
    status_str: str = "ok",
    error_message: Optional[str] = None,
) -> SignalExpectationPrice:
    """UPSERT (stock_id, first_detected_date)；保留既有 hit_*_at 旗標。"""
    first_detected_date = meta["first_detected_date"]
    existing = (
        db.query(SignalExpectationPrice)
        .filter(
            SignalExpectationPrice.stock_id == stock_id,
            SignalExpectationPrice.first_detected_date == first_detected_date,
        )
        .first()
    )

    expectation_result = (parsed or {}).get("expectation_result") if parsed else None
    valuation_detail = (parsed or {}).get("valuation_detail") if parsed else None
    scorecard = (parsed or {}).get("scorecard") if parsed else None
    classification = (parsed or {}).get("classification") if parsed else None
    reason_50 = (parsed or {}).get("reason_50_words") if parsed else None
    risk_note = (parsed or {}).get("risk_note_30_words") if parsed else None

    fields = {
        "stock_id": stock_id,
        "stock_name": meta["stock_name"],
        "first_detected_date": first_detected_date,
        "latest_detected_date": meta.get("latest_detected_date"),
        "detected_type": meta.get("detected_type"),
        "industry_name": meta.get("industry_name"),
        "sub_industry": meta.get("sub_industry"),
        "detected_day_high": meta.get("detected_day_high"),
        "detected_day_close": meta.get("detected_day_close"),
        "current_price": meta.get("current_price"),
        "conservative_price": _maybe_num(expectation_result, "conservative_price"),
        "dream_price": _maybe_num(expectation_result, "dream_price"),
        "price_base": _maybe_str(expectation_result, "price_base"),
        "valuation_mode": _maybe_str(expectation_result, "valuation_mode"),
        "valuation_basis": _maybe_str(expectation_result, "valuation_basis"),
        "current_price_position": _maybe_str(expectation_result, "current_price_position"),
        "chase_risk": _maybe_str(expectation_result, "chase_risk"),
        "confidence": _maybe_str(expectation_result, "confidence"),
        "scorecard": scorecard if isinstance(scorecard, dict) else None,
        "classification": classification if isinstance(classification, dict) else None,
        "valuation_detail": valuation_detail if isinstance(valuation_detail, dict) else None,
        "reason_50_words": reason_50 if isinstance(reason_50, str) else None,
        "risk_note_30_words": risk_note if isinstance(risk_note, str) else None,
        "raw_payload": parsed if isinstance(parsed, dict) else None,
        "source": source,
        "status": status_str,
        "error_message": error_message,
        "llm_model": diagnostic.get("model"),
        "llm_diagnostic": diagnostic,
        "updated_at": datetime.utcnow(),
    }

    if existing is not None:
        # 保留 hit_conservative_at / hit_dream_at（達標旗標一旦設定不應被覆寫）
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing

    new_row = SignalExpectationPrice(
        **fields,
        hit_conservative_at=None,
        hit_dream_at=None,
        generated_at=datetime.utcnow(),
    )
    db.add(new_row)
    return new_row


def _maybe_num(blob: Any, key: str) -> Optional[float]:
    if not isinstance(blob, dict):
        return None
    v = blob.get(key)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _maybe_str(blob: Any, key: str) -> Optional[str]:
    if not isinstance(blob, dict):
        return None
    v = blob.get(key)
    if isinstance(v, str):
        return v[:64]
    return None


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def generate_for_stock(
    db: Session,
    stock_id: str,
    *,
    first_detected_date: Optional[date] = None,
    source: str = "manual",
    model: str = DEFAULT_MODEL,
) -> SignalExpectationPrice:
    """單檔產生 expectation price 並 UPSERT。

    Raises:
      - ValueError 若 stock_id 找不到或無 signal_watch_hits 記錄
    """
    try:
        ctx = build_expectation_context(
            db,
            stock_id,
            first_detected_date=first_detected_date,
        )
    except ValueError:
        raise
    except Exception as exc:
        logger.exception("build_expectation_context failed stock=%s", stock_id)
        # 寫一筆 failed row 方便前端顯示「資料不全」
        raise

    parsed, diagnostic = _call_llm(ctx.payload, model=model)

    if parsed is None:
        row = _upsert_expectation_row(
            db,
            stock_id=stock_id,
            meta=ctx.meta,
            parsed=None,
            diagnostic=diagnostic,
            source=source,
            status_str="failed",
            error_message=diagnostic.get("message"),
        )
        db.commit()
        return row

    row = _upsert_expectation_row(
        db,
        stock_id=stock_id,
        meta=ctx.meta,
        parsed=parsed,
        diagnostic=diagnostic,
        source=source,
        status_str="ok",
    )
    db.commit()
    return row


def generate_for_new_signals(
    db: Session,
    snapshot_date: date,
    *,
    source: str = "cron",
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """cron 入口：找出 first_seen_date == snapshot_date 的新進股票，逐檔產生 expectation price。

    回傳結構：
        {
            "snapshot_date": date,
            "total": N,
            "ok": K,
            "failed": [stock_id, ...],
            "skipped_existing": [stock_id, ...]
        }
    """
    # 找出該日 watchlist 的 stock_id，再篩出 first_seen_date == snapshot_date 的
    snap_rows = (
        db.query(SignalWatchHit.stock_id)
        .filter(SignalWatchHit.snapshot_date == snapshot_date)
        .all()
    )
    candidate_ids = [r[0] for r in snap_rows]

    if not candidate_ids:
        return {
            "snapshot_date": snapshot_date,
            "total": 0,
            "ok": 0,
            "failed": [],
            "skipped_existing": [],
        }

    # 對每檔判斷 first_seen_date 是不是 snapshot_date（= 今日新抓到）
    first_seen_map = dict(
        db.query(
            SignalWatchHit.stock_id,
            func.min(SignalWatchHit.snapshot_date),
        )
        .filter(SignalWatchHit.stock_id.in_(candidate_ids))
        .group_by(SignalWatchHit.stock_id)
        .all()
    )
    new_stocks = [
        sid for sid in candidate_ids
        if first_seen_map.get(sid) == snapshot_date
    ]
    if not new_stocks:
        return {
            "snapshot_date": snapshot_date,
            "total": 0,
            "ok": 0,
            "failed": [],
            "skipped_existing": [],
        }

    ok = 0
    failed: List[str] = []
    skipped: List[str] = []

    for sid in new_stocks:
        # 同 cycle 已有 row → 仍重跑（cron 視為當日 sanity refresh）；
        # 若想保守可改成「skip if exists & status=ok」
        try:
            row = generate_for_stock(
                db,
                sid,
                first_detected_date=snapshot_date,
                source=source,
                model=model,
            )
            if row.status == "ok":
                ok += 1
            else:
                failed.append(sid)
        except Exception:
            logger.exception("generate_for_stock failed for %s", sid)
            failed.append(sid)
            db.rollback()  # 清掉 transaction error state，避免影響下一檔

    return {
        "snapshot_date": snapshot_date,
        "total": len(new_stocks),
        "ok": ok,
        "failed": failed,
        "skipped_existing": skipped,
    }


# ---------------------------------------------------------------------------
# Hit detection
# ---------------------------------------------------------------------------


def update_hit_targets(db: Session, target_date: date) -> Dict[str, int]:
    """對所有 active expectation_price 比對 target_date 當日收盤是否觸及保守 / 夢想價。

    首次觸及才寫入 `hit_conservative_at` / `hit_dream_at`；之後不再覆寫。

    回傳 {"conservative_hits": N, "dream_hits": M}。
    """
    rows = (
        db.query(SignalExpectationPrice)
        .filter(SignalExpectationPrice.status == "ok")
        .all()
    )

    if not rows:
        return {"conservative_hits": 0, "dream_hits": 0}

    stock_ids = list({r.stock_id for r in rows})
    # 一次撈當日收盤
    closes = (
        db.query(DailyPrice.stock_id, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id.in_(stock_ids),
            DailyPrice.trade_date == target_date,
        )
        .all()
    )
    close_map = {sid: cp for sid, cp in closes}

    c_hits = 0
    d_hits = 0
    for r in rows:
        close = close_map.get(r.stock_id)
        if close is None:
            continue
        if (
            r.conservative_price is not None
            and r.hit_conservative_at is None
            and close >= r.conservative_price
        ):
            r.hit_conservative_at = target_date
            c_hits += 1
        if (
            r.dream_price is not None
            and r.hit_dream_at is None
            and close >= r.dream_price
        ):
            r.hit_dream_at = target_date
            d_hits += 1

    if c_hits or d_hits:
        db.commit()
    return {"conservative_hits": c_hits, "dream_hits": d_hits}
