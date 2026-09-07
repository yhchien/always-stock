"""魚尾每日模擬交易（Shadow Portfolio）Phase 1：把 fishtail_backtest/ 沙盒反覆驗證過的
v1 策略（+12.05% 無成本／+9.93% 含成本，28 筆交易，勝率 46.4%；v2/Unified/Hybrid 三套皆已
證明較差並刪除）正式接進生產系統。

**v1 策略門檻凍結，不可調參**——本模組的 `V1_STRATEGY_PARAMS`／進出場判斷函式，數值與邏輯
逐字對應沙盒 `fishtail_backtest/backtest/signals.py`／`run_backtest.py`。改動這些數值視同
毀棄整個沙盒反覆驗證的結論，若要改進策略必須另開新的 `strategy_version`（例如未來的
`v2_frozen`），不可回頭覆寫 `v1_frozen` 的歷史決策/訂單。

架構上兩個關鍵、與規格書字面描述不同之處（本輪 Plan Mode 查證 production 現有程式碼後確認）：

1. **必須排在 `signal_archive_returns` 之後執行**，不能塞進 P3/P4 當天的 pipeline.py 內部
   ——`SignalWatchHit.return_pct`（v1 進場條件的核心輸入）只有在該股票「當天被 P3 重選」時
   才是新鮮值；沒被重選的追蹤股票，這個欄位要等 `archive.update_signal_watch_returns()`
   （由獨立的 `run_signal_archive_returns.py`／`signal_archive_returns.yml` workflow 呼叫，
   鏈在 daily_signals 完成之後）跑過才會更新到當天收盤價。若把本模組的 orchestrator 塞進
   pipeline.py，沒被當天重選的持股會用到昨天的舊報酬率做進出場判斷
2. **出場優先序**：`-8% 真實停損`（用 `close/average_entry_price-1` 算的實際部位報酬，
   不是 `mark_to_market_return_pct`）是**最優先**檢查、短路其他所有判斷（見沙盒
   `run_backtest.py:208-237`）。正確優先序是
   **-8% 真實停損 > P4_STOP > 官方平倉日（30 個交易日期滿）> +10% 固定停利**——不是單純
   「P4_STOP 最優先」

v1 沒有獨立的「加碼（WINNER_ADD）」判斷邏輯（那是 v2/Unified 才有的機制）：v1 的加碼純粹是
「已持有 1 lot 的股票，某天又觸發一次 setup_a/setup_b」，用**完全相同**的
`generate_entry_signal`／`compute_entry_score`／`rank_candidates`，只是持倉容量允許加到
第 2 lot。BUY vs ADD 只是「這檔股票原本有沒有持倉」的顯示區分，不是兩套演算法。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    DailyPrice,
    ShadowCompletedTrade,
    ShadowPositionLot,
    ShadowPortfolioDailySnapshot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    SignalObservation,
    SignalObservationReview,
    SignalWatchHit,
)
from app.signals import archive

STRATEGY_VERSION = "v1_frozen"


def ensure_shadow_portfolio_tables(engine: Engine) -> None:
    """Idempotent table creation，供 `main.py` lifespan 與獨立 script（`run_shadow_
    portfolio.py`／`backfill_shadow_portfolio_replay.py`）共用（比照 `observation_schema.
    ensure_observation_tables` 的既有慣例）。"""
    Base.metadata.create_all(
        bind=engine,
        tables=[
            ShadowVirtualPortfolio.__table__,
            ShadowVirtualPosition.__table__,
            ShadowPositionLot.__table__,
            ShadowStrategyOrder.__table__,
            ShadowStrategyDailyDecision.__table__,
            ShadowPortfolioDailySnapshot.__table__,
            ShadowCompletedTrade.__table__,
        ],
    )
    _ensure_shadow_virtual_portfolio_cycle_columns(engine)


def _ensure_shadow_virtual_portfolio_cycle_columns(engine: Engine) -> None:
    """2026-09-08：35 交易日循環重置——`shadow_virtual_portfolios` 這張表在
    production 早已有資料（本輪之前的 backfill 驗證），`create_all` 不會替既有表
    補欄位，需要顯式 ALTER TABLE（比照 `signal_watch_schema.py` 既有 dict pattern）。
    """
    inspector = inspect(engine)
    if "shadow_virtual_portfolios" not in inspector.get_table_names():
        return
    wanted = {
        "cycle_number": "ALTER TABLE shadow_virtual_portfolios ADD COLUMN cycle_number INTEGER NOT NULL DEFAULT 1",
        "cycle_start_trade_date": "ALTER TABLE shadow_virtual_portfolios ADD COLUMN cycle_start_trade_date DATE",
    }
    columns = {c["name"] for c in inspector.get_columns("shadow_virtual_portfolios")}
    missing = [name for name in wanted if name not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            conn.execute(text(wanted[name]))

# ---------------------------------------------------------------------------
# 凍結參數 —— 逐字對應 fishtail_backtest/backtest/run_all.py 的 BASELINE_PARAMS。
# 不可調參；改動這些值視同毀棄沙盒的驗證結論。
# ---------------------------------------------------------------------------
V1_STRATEGY_PARAMS: Dict[str, Any] = {
    "initial_capital": 600000.0,
    "unit_capital": 100000.0,
    "max_stocks": 5,
    "max_units_per_stock": 2,
    "max_total_units": 6,
    "setup_a": {
        "day_index_min": 2, "day_index_max": 3, "hit_count": 1,
        "momentum_min": 68, "momentum_max": 80,
        "return_min": -2.5, "return_max": 0, "p4": "CAUTION",
    },
    "setup_b": {
        "day_index_min": 2, "day_index_max": 4,
        "momentum_min": 65, "momentum_max": 85,
        "return_min": -10, "return_max": -8, "p4": "CAUTION",
    },
    "take_profit_signal_pct": 10.0,
    "real_stop_loss_pct": -8.0,
}

ENTRY_TYPE_EARLY_HEALTHY_PULLBACK = "EARLY_HEALTHY_PULLBACK"
ENTRY_TYPE_DEEP_PULLBACK = "DEEP_PULLBACK"

EXIT_REASON_P4_STOP = "P4_STOP"
EXIT_REASON_OFFICIAL_EXIT = "OFFICIAL_EXIT"
EXIT_REASON_TAKE_PROFIT = "TAKE_PROFIT"
EXIT_REASON_REAL_STOP_LOSS = "REAL_POSITION_STOP_LOSS"
# 35 交易日循環強制重置（見 check_and_apply_cycle_reset）——不是策略訊號觸發的
# 正常出場，是行政性強制平倉，exit_execution_date 就是觸發當天，不等 T+1
EXIT_REASON_CYCLE_RESET = "CYCLE_RESET"

CYCLE_LENGTH_TRADING_DAYS = 35

ACTION_WATCH = "WATCH"
ACTION_BUY = "BUY"
ACTION_ADD = "ADD"
ACTION_HOLD = "HOLD"
ACTION_SELL = "SELL"
ACTION_SKIPPED_CAPACITY = "SKIPPED_PORTFOLIO_CAPACITY"

ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_EXECUTED = "EXECUTED"


# ---------------------------------------------------------------------------
# Evidence row —— production 版的 CohortDayRow（沙盒那個 dataclass 不可跨repo匯入，
# 這裡是同語意的獨立定義，欄位逐一對應）。
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EvidenceRow:
    stock_id: str
    stock_name: str
    first_seen_date: date
    trade_date: date
    day_index: int
    p3_selected_today: bool
    hit_count_so_far: int
    momentum_score: Optional[float]
    p4_decision: Optional[str]
    mark_to_market_return_pct: Optional[float]
    is_official_exit_signal_day: bool


@dataclass(frozen=True)
class EntrySignal:
    row: EvidenceRow
    entry_type: str
    entry_score: float


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    row: EvidenceRow


def _in_range(value: Optional[float], lo: float, hi: float) -> bool:
    return value is not None and lo <= value <= hi


def _matches_setup_a(row: EvidenceRow, cfg: dict) -> bool:
    return (
        row.p4_decision == cfg["p4"]
        and cfg["day_index_min"] <= row.day_index <= cfg["day_index_max"]
        and row.hit_count_so_far == cfg["hit_count"]
        and _in_range(row.momentum_score, cfg["momentum_min"], cfg["momentum_max"])
        and _in_range(row.mark_to_market_return_pct, cfg["return_min"], cfg["return_max"])
    )


def _matches_setup_b(row: EvidenceRow, cfg: dict) -> bool:
    return (
        row.p4_decision == cfg["p4"]
        and cfg["day_index_min"] <= row.day_index <= cfg["day_index_max"]
        and _in_range(row.momentum_score, cfg["momentum_min"], cfg["momentum_max"])
        and _in_range(row.mark_to_market_return_pct, cfg["return_min"], cfg["return_max"])
    )


def _day_bonus(day_index: int) -> float:
    return {2: 3.0, 3: 2.0, 4: 1.0}.get(day_index, 0.0)


def _momentum_bonus(momentum_score: Optional[float]) -> float:
    if momentum_score is None:
        return 0.0
    return max(0.0, 2.0 - abs(momentum_score - 75.0) / 5.0)


def _pullback_bonus(entry_type: str, return_pct: Optional[float]) -> float:
    if return_pct is None:
        return 0.0
    if entry_type == ENTRY_TYPE_EARLY_HEALTHY_PULLBACK:
        return max(0.0, 2.0 - abs(return_pct - (-1.5)) / 1.5)
    return max(0.0, 2.0 - abs(return_pct - (-9.0)) / 1.5)


def compute_entry_score(row: EvidenceRow, entry_type: str) -> float:
    score = _day_bonus(row.day_index)
    if row.p3_selected_today:
        score += 1.0
    score += 2.0 if entry_type == ENTRY_TYPE_EARLY_HEALTHY_PULLBACK else 1.0
    score += _momentum_bonus(row.momentum_score)
    score += _pullback_bonus(entry_type, row.mark_to_market_return_pct)
    return score


def generate_entry_signal(row: EvidenceRow, params: dict) -> Optional[EntrySignal]:
    """Pure function：只讀今天的 evidence，不查資料庫、不看任何未來欄位。"""
    candidates = []
    if _matches_setup_a(row, params["setup_a"]):
        candidates.append(ENTRY_TYPE_EARLY_HEALTHY_PULLBACK)
    if _matches_setup_b(row, params["setup_b"]):
        candidates.append(ENTRY_TYPE_DEEP_PULLBACK)
    if not candidates:
        return None
    best_type = max(candidates, key=lambda t: compute_entry_score(row, t))
    return EntrySignal(row=row, entry_type=best_type, entry_score=compute_entry_score(row, best_type))


def generate_exit_signal(row: EvidenceRow, params: dict) -> Optional[ExitSignal]:
    """優先序：P4_STOP > 官方平倉日 > +10% 固定停利。**-8% 真實停損不在這裡**——
    呼叫端必須先用實際部位報酬（average_entry_price 對今日收盤）檢查真實停損，
    只有沒觸發時才呼叫這個函式（見 `run_daily_trading_strategy` 與模組頂部說明）。
    """
    if row.p4_decision == "STOP_OBSERVING":
        return ExitSignal(reason=EXIT_REASON_P4_STOP, row=row)
    if row.is_official_exit_signal_day:
        return ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=row)
    take_profit = params.get("take_profit_signal_pct")
    if (
        take_profit is not None
        and row.mark_to_market_return_pct is not None
        and row.mark_to_market_return_pct >= take_profit
    ):
        return ExitSignal(reason=EXIT_REASON_TAKE_PROFIT, row=row)
    return None


def rank_candidates(candidates: List[EntrySignal]) -> List[EntrySignal]:
    """Deterministic ordering: entry_score desc, momentum_score desc, stock_id asc."""
    return sorted(
        candidates,
        key=lambda c: (-c.entry_score, -(c.row.momentum_score or 0.0), c.row.stock_id),
    )


# ---------------------------------------------------------------------------
# Evidence builder —— 讀 production DB 組出今天的 EvidenceRow
# ---------------------------------------------------------------------------
def _count_market_trading_days(db: Session, *, first_seen_date: date, target_date: date) -> int:
    """day_index：從 first_seen_date 到 target_date（含）之間有幾個全市場交易日。
    刻意不重用 `archive._count_tracking_days`（私有函式，跨模組只重用公開介面），
    這裡是同語意的獨立小 query。"""
    if target_date < first_seen_date:
        return 1
    count = (
        db.query(func.count(func.distinct(DailyPrice.trade_date)))
        .filter(DailyPrice.trade_date >= first_seen_date, DailyPrice.trade_date <= target_date)
        .scalar()
        or 0
    )
    return max(int(count), 1)


def _count_hits_so_far(db: Session, *, stock_id: str, first_seen_date: date, target_date: date) -> int:
    """hit_count_so_far：這個 P4 cohort 被 P3 選中過幾次，到 target_date（含）為止。

    `first_seen_date`（P4 episode 的 `started_signal_date`）本身就代表 P3 第一次選中
    ——這永遠算「第 1 次」，不管 `signal_watch_hits` 那天有沒有對應列。查證 production
    真實資料發現：不少 P4 episode 從頭到尾都沒有任何 `signal_watch_hits` 列（兩張表是
    獨立來源，並非每次 P3 選中都會反映在 `signal_watch_hits`），若只靠這張表數
    reselection，會把「這是全新第一次選中」誤判成「從未被選中過（0 次）」，導致 v1
    的 `hit_count==1`（setup_a）門檻永遠測不出正確結果。之後每多一筆 `signal_watch_hits`
    （`snapshot_date > first_seen_date`）代表額外一次真正的重選確認，才 +1。
    """
    additional = (
        db.query(func.count(func.distinct(SignalWatchHit.snapshot_date)))
        .filter(
            SignalWatchHit.stock_id == stock_id,
            SignalWatchHit.snapshot_date > first_seen_date,
            SignalWatchHit.snapshot_date <= target_date,
        )
        .scalar()
        or 0
    )
    return 1 + int(additional)


def _resolve_nth_market_trade_date(db: Session, *, first_seen_date: date, day_index: int) -> Optional[date]:
    rows = (
        db.query(DailyPrice.trade_date)
        .filter(DailyPrice.trade_date >= first_seen_date)
        .distinct()
        .order_by(DailyPrice.trade_date.asc())
        .limit(day_index)
        .all()
    )
    return rows[-1][0] if len(rows) >= day_index else None


def _resolve_baseline_price(db: Session, *, stock_id: str, first_seen_date: date) -> Tuple[Optional[date], Optional[float]]:
    """baseline = 第 2 個交易日（全市場交易日曆）的 (open+close)/2，比照
    `app.signals.archive._resolve_baseline_price`／`_resolve_nth_trade_date` 的既有
    慣例——本函式是同語意的獨立版本（archive.py 的是私有函式，跨模組只重用公開介面）。"""
    baseline_date = _resolve_nth_market_trade_date(db, first_seen_date=first_seen_date, day_index=2)
    if baseline_date is None:
        return None, None
    row = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == baseline_date)
        .first()
    )
    if row is None or row.open_price is None or row.close_price is None:
        return baseline_date, None
    return baseline_date, (float(row.open_price) + float(row.close_price)) / 2.0


def build_daily_evidence(
    db: Session,
    *,
    stock_id: str,
    stock_name: str,
    first_seen_date: date,
    target_date: date,
) -> EvidenceRow:
    """組出某檔股票在 target_date 當天的 v1 決策所需 evidence。只讀 <= target_date
    的資料，不使用任何未來欄位（no-lookahead）。

    **報酬率完全由 `daily_price` 直接算出，不依賴 `signal_watch_hits`**——查證 production
    真實資料發現：多數 P4 觀察中的股票從未有對應的 `signal_watch_hits` 列（`SignalWatchHit`
    只在該股票「當天被 P3 重選」才會有一列，P4 追蹤本身跟這張表是兩個獨立、不一定重疊的
    資料來源）。baseline 沿用既有 `archive.py` 的慣例（第 2 個交易日 `(open+close)/2`，
    第 2 天固定 0%），但直接對 `daily_price` 算，適用於所有股票不論有沒有被 P3 重選過。
    """
    day_index = _count_market_trading_days(db, first_seen_date=first_seen_date, target_date=target_date)
    hit_count_so_far = _count_hits_so_far(
        db, stock_id=stock_id, first_seen_date=first_seen_date, target_date=target_date
    )

    hit_today = (
        db.query(SignalWatchHit)
        .filter(SignalWatchHit.stock_id == stock_id, SignalWatchHit.snapshot_date == target_date)
        .first()
    )
    p3_selected_today = hit_today is not None

    momentum_score: Optional[float] = None
    if hit_today is not None:
        momentum_score = (hit_today.signal_metrics or {}).get("momentum_score")

    mark_to_market_return_pct: Optional[float] = None
    baseline_date, baseline_price = _resolve_baseline_price(db, stock_id=stock_id, first_seen_date=first_seen_date)
    if baseline_date is not None and baseline_price not in (None, 0) and target_date >= baseline_date:
        if target_date == baseline_date:
            mark_to_market_return_pct = 0.0  # 比照 archive.py：baseline 當天固定 0%
        else:
            today_close = (
                db.query(DailyPrice.close_price)
                .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == target_date)
                .scalar()
            )
            if today_close is not None:
                mark_to_market_return_pct = (float(today_close) - baseline_price) / baseline_price * 100.0

    observation = (
        db.query(SignalObservation)
        .filter(SignalObservation.stock_id == stock_id, SignalObservation.started_signal_date == first_seen_date)
        .first()
    )
    p4_decision: Optional[str] = None
    if observation is not None:
        review = (
            db.query(SignalObservationReview)
            .filter(
                SignalObservationReview.observation_id == observation.id,
                SignalObservationReview.review_date == target_date,
            )
            .first()
        )
        if review is not None:
            p4_decision = review.decision
            if momentum_score is None:
                momentum_score = review.momentum_score

    is_official_exit_signal_day = day_index >= archive.ARCHIVE_RETENTION_TRADE_DAYS

    return EvidenceRow(
        stock_id=stock_id,
        stock_name=stock_name,
        first_seen_date=first_seen_date,
        trade_date=target_date,
        day_index=day_index,
        p3_selected_today=p3_selected_today,
        hit_count_so_far=hit_count_so_far,
        momentum_score=momentum_score,
        p4_decision=p4_decision,
        mark_to_market_return_pct=mark_to_market_return_pct,
        is_official_exit_signal_day=is_official_exit_signal_day,
    )


def resolve_tracking_universe(
    db: Session, *, strategy_version: str, target_date: date
) -> List[Tuple[str, str, date]]:
    """回傳 target_date 當天要評估的 (stock_id, stock_name, first_seen_date) 清單：
    聯集 (a) P4 在 target_date 當下仍在追蹤中的股票、(b) 目前 Shadow Portfolio 有持倉的
    股票（防禦性——即使 P4 那邊已結算，已持倉的股票仍要繼續被評估是否該出場，不能因為觀察
    生命週期已終止就漏掉真正持有的部位）。

    **刻意不用 `SignalObservation.status`**（那是「現在」的最新狀態，是會隨時間變動的
    可變欄位）——backfill/replay 對過去某一天重跑時，若某個 episode 是「當時仍在追蹤、
    後來才被停止」，用現在的 status 篩選會把它整段歷史都排除掉，等同於用了「這檔股票
    未來會被停止」這個未來資訊，是 no-lookahead 違規。改用 review 歷史判斷：只要這個
    episode 在 target_date（含）之前沒有任何一次 `STOP_OBSERVING` 複核，就視為當時仍在
    追蹤中——這對「重播歷史」與「跑今天」兩種情境都同樣正確（跑今天時，`STOP_OBSERVING`
    review 存在與否本來就等價於現在的 status）。

    另外套用 v1 凍結參數 `exclude_etf=True`（沙盒 `run_all.py` 的 `BASELINE_PARAMS`）：
    只納入 `asset_type=="COMMON_STOCK"` 的觀察，ETF（例：00738U／00994A／00947）不進入
    候選（`SignalObservation.asset_type` 已有這個分類，不需要另外查表判斷）。
    """
    universe: Dict[str, Tuple[str, str, date]] = {}

    candidates = (
        db.query(SignalObservation)
        .filter(
            SignalObservation.started_signal_date <= target_date,
            SignalObservation.asset_type == "COMMON_STOCK",  # v1 凍結參數 exclude_etf=True
        )
        .all()
    )
    for obs in candidates:
        already_stopped = (
            db.query(SignalObservationReview.id)
            .filter(
                SignalObservationReview.observation_id == obs.id,
                SignalObservationReview.review_date <= target_date,
                SignalObservationReview.decision == "STOP_OBSERVING",
            )
            .first()
            is not None
        )
        if not already_stopped:
            universe[obs.stock_id] = (obs.stock_id, obs.stock_name, obs.started_signal_date)

    for pos in (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .all()
    ):
        if pos.stock_id not in universe:
            universe[pos.stock_id] = (pos.stock_id, pos.stock_name, pos.first_seen_date)

    return sorted(universe.values(), key=lambda t: t[0])


def next_weekday_guess(d: date) -> date:
    """`scheduled_execution_date` 的粗略預測值——只跳過週末，純供 UI「預計執行」顯示用。
    無法精確預測補班/連假（production 沒有交易日曆工具，且訊號當下 T+1 的 daily_price
    本來就還不存在），真正成交靠 `execute_pending_strategy_orders` 的 self-healing 查詢。
    """
    candidate = d + timedelta(days=1)
    while candidate.weekday() >= 5:  # 5=Sat, 6=Sun
        candidate += timedelta(days=1)
    return candidate


# ---------------------------------------------------------------------------
# Portfolio / position helpers
# ---------------------------------------------------------------------------
def _parse_snapshot_date(snapshot: Optional[dict], key: str, fallback: date) -> date:
    raw = (snapshot or {}).get(key)
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return fallback


def _get_or_create_portfolio(db: Session, strategy_version: str) -> ShadowVirtualPortfolio:
    portfolio = (
        db.query(ShadowVirtualPortfolio)
        .filter(ShadowVirtualPortfolio.strategy_version == strategy_version)
        .first()
    )
    if portfolio is None:
        portfolio = ShadowVirtualPortfolio(
            strategy_version=strategy_version,
            cash=V1_STRATEGY_PARAMS["initial_capital"],
            realized_pnl_cumulative=0.0,
        )
        db.add(portfolio)
        db.flush()
    return portfolio


def _load_positions(db: Session, strategy_version: str) -> Dict[str, ShadowVirtualPosition]:
    rows = (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .all()
    )
    return {p.stock_id: p for p in rows}


def _position_units(db: Session, position_id: int) -> int:
    return (
        db.query(func.count(ShadowPositionLot.id))
        .filter(ShadowPositionLot.position_id == position_id)
        .scalar()
        or 0
    )


def _position_average_entry_price(db: Session, position_id: int) -> Optional[float]:
    lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == position_id).all()
    total_shares = sum(lot.shares for lot in lots)
    total_cost = sum(lot.allocation for lot in lots)
    if total_shares <= 0:
        return None
    return total_cost / total_shares


def _record_completed_trade(
    db: Session,
    *,
    strategy_version: str,
    cycle_number: int,
    lot: ShadowPositionLot,
    stock_id: str,
    stock_name: str,
    exit_reason: str,
    exit_signal_date: date,
    exit_execution_date: date,
    exit_price: float,
) -> ShadowCompletedTrade:
    """把一個要平倉的 lot 轉成永久保存的 `ShadowCompletedTrade` 列——**呼叫端負責
    自己刪除 lot**，這個函式只 `db.add()` 新紀錄，不動 lot 本身。"""
    realized_pnl = lot.shares * exit_price - lot.allocation
    trade = ShadowCompletedTrade(
        strategy_version=strategy_version,
        cycle_number=cycle_number,
        stock_id=stock_id,
        stock_name=stock_name,
        entry_type=lot.entry_type,
        entry_signal_date=lot.entry_signal_date,
        entry_execution_date=lot.entry_execution_date,
        entry_price=lot.entry_price,
        entry_day_index=lot.entry_day_index,
        entry_hit_count=lot.entry_hit_count,
        entry_momentum=lot.entry_momentum,
        entry_p4_decision=lot.entry_p4_decision,
        entry_mark_to_market_return=lot.entry_mark_to_market_return,
        exit_reason=exit_reason,
        exit_signal_date=exit_signal_date,
        exit_execution_date=exit_execution_date,
        exit_price=exit_price,
        shares=lot.shares,
        allocation=lot.allocation,
        realized_pnl=realized_pnl,
        realized_return_pct=(realized_pnl / lot.allocation * 100.0) if lot.allocation else 0.0,
        holding_days=(exit_execution_date - lot.entry_execution_date).days,
        followed_by_rotation=False,  # 呼叫端事後統一更新（見 execute_pending_strategy_orders）
    )
    db.add(trade)
    return trade


def _latest_close(db: Session, *, stock_id: str, as_of: date) -> Optional[float]:
    row = (
        db.query(DailyPrice.close_price)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == as_of)
        .first()
    )
    return float(row[0]) if row is not None and row[0] is not None else None


# ---------------------------------------------------------------------------
# Orchestrator 1：執行前一交易日排定的 pending orders（用今天真實成交的 high/low）
# ---------------------------------------------------------------------------
def execute_pending_strategy_orders(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> Dict[str, int]:
    """SELL 先於 BUY/ADD（spec §6：賣出釋放的現金當天就能用於買進）。self-healing：
    某股票 target_date 當天還沒有 daily_price 就跳過，繼續留 PENDING 等下次呼叫。
    Idempotent：只處理 status=PENDING 的訂單，已執行過的訂單天然不會被重複處理。"""
    portfolio = _get_or_create_portfolio(db, strategy_version)
    orders = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
            ShadowStrategyOrder.scheduled_execution_date <= target_date,
        )
        .all()
    )
    orders.sort(key=lambda o: 0 if o.action == ACTION_SELL else 1)

    executed = {"sell": 0, "buy": 0, "add": 0, "skipped_no_price": 0, "failed": 0}
    trades_created_today: List[ShadowCompletedTrade] = []

    for order in orders:
        if order.action == ACTION_SELL:
            price_row = (
                db.query(DailyPrice.low_price)
                .filter(DailyPrice.stock_id == order.stock_id, DailyPrice.trade_date == target_date)
                .first()
            )
            if price_row is None or price_row[0] is None:
                executed["skipped_no_price"] += 1
                continue
            price = float(price_row[0])
            position = (
                db.query(ShadowVirtualPosition)
                .filter(
                    ShadowVirtualPosition.strategy_version == strategy_version,
                    ShadowVirtualPosition.stock_id == order.stock_id,
                )
                .first()
            )
            if position is None:
                order.status = "FAILED"
                executed["failed"] += 1
                continue
            lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == position.id).all()
            for lot in lots:
                proceeds = lot.shares * price
                portfolio.cash += proceeds
                portfolio.realized_pnl_cumulative += proceeds - lot.allocation
                trades_created_today.append(
                    _record_completed_trade(
                        db,
                        strategy_version=strategy_version,
                        cycle_number=portfolio.cycle_number,
                        lot=lot,
                        stock_id=order.stock_id,
                        stock_name=order.stock_name,
                        exit_reason=order.reason or "UNKNOWN",
                        exit_signal_date=order.signal_date,
                        exit_execution_date=target_date,
                        exit_price=price,
                    )
                )
                db.delete(lot)
            db.delete(position)
            order.status = ORDER_STATUS_EXECUTED
            order.execution_price = price
            order.executed_at = datetime.utcnow()
            executed["sell"] += 1
        else:
            price_row = (
                db.query(DailyPrice.high_price)
                .filter(DailyPrice.stock_id == order.stock_id, DailyPrice.trade_date == target_date)
                .first()
            )
            if price_row is None or price_row[0] is None:
                executed["skipped_no_price"] += 1
                continue
            price = float(price_row[0])
            allocation = min(V1_STRATEGY_PARAMS["unit_capital"], portfolio.cash)
            if allocation < V1_STRATEGY_PARAMS["unit_capital"] - 1e-6:
                order.status = "FAILED"
                order.reason = (order.reason or "") + "；執行時現金不足，訂單失敗"
                executed["failed"] += 1
                continue
            shares = allocation / price
            portfolio.cash -= allocation

            position = (
                db.query(ShadowVirtualPosition)
                .filter(
                    ShadowVirtualPosition.strategy_version == strategy_version,
                    ShadowVirtualPosition.stock_id == order.stock_id,
                )
                .first()
            )
            if position is None:
                position = ShadowVirtualPosition(
                    strategy_version=strategy_version,
                    stock_id=order.stock_id,
                    stock_name=order.stock_name,
                    first_seen_date=_parse_snapshot_date(order.signal_snapshot, "first_seen_date", target_date),
                )
                db.add(position)
                db.flush()

            snapshot = order.signal_snapshot or {}
            db.add(
                ShadowPositionLot(
                    position_id=position.id,
                    entry_type=order.entry_pattern or "UNKNOWN",
                    entry_signal_date=order.signal_date,
                    entry_execution_date=target_date,
                    entry_price=price,
                    shares=shares,
                    allocation=allocation,
                    entry_day_index=snapshot.get("day_index"),
                    entry_hit_count=snapshot.get("hit_count_so_far"),
                    entry_momentum=snapshot.get("momentum_score"),
                    entry_p4_decision=snapshot.get("p4_decision"),
                    entry_mark_to_market_return=snapshot.get("mark_to_market_return_pct"),
                )
            )
            order.status = ORDER_STATUS_EXECUTED
            order.execution_price = price
            order.executed_at = datetime.utcnow()
            executed["buy" if order.action == ACTION_BUY else "add"] += 1

    # 「賣出後有沒有換股」：這次呼叫只要有任何 BUY/ADD 成交，今天所有平倉紀錄都標記
    # followed_by_rotation=True（不分先後順序——SELL 已排在 BUY/ADD 之前執行，但
    # 「今天同時發生」才是使用者想問的換股語意，不是嚴格的因果順序）
    if (executed["buy"] + executed["add"]) > 0:
        for trade in trades_created_today:
            trade.followed_by_rotation = True

    return executed


# ---------------------------------------------------------------------------
# Orchestrator 2：今天的策略決策——WATCH/BUY/ADD/HOLD/SELL，建立明日 pending order
# ---------------------------------------------------------------------------
def run_daily_trading_strategy(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> Dict[str, int]:
    """必須排在 `execute_pending_strategy_orders(target_date=target_date)` 之後、同一次
    呼叫內執行（spec §36：先執行昨天的訂單、更新 portfolio，才能用正確的持倉狀態決定今天
    的動作）。"""
    params = V1_STRATEGY_PARAMS
    portfolio = _get_or_create_portfolio(db, strategy_version)
    positions = _load_positions(db, strategy_version)
    universe = resolve_tracking_universe(db, strategy_version=strategy_version, target_date=target_date)

    already_decided = {
        row.stock_id
        for row in db.query(ShadowStrategyDailyDecision.stock_id).filter(
            ShadowStrategyDailyDecision.strategy_version == strategy_version,
            ShadowStrategyDailyDecision.trade_date == target_date,
        )
    }

    evidence_by_stock: Dict[str, EvidenceRow] = {}
    for stock_id, stock_name, first_seen_date in universe:
        if stock_id in already_decided:
            continue
        evidence_by_stock[stock_id] = build_daily_evidence(
            db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date, target_date=target_date
        )

    # ---- 出場判斷：真實 -8% 停損最優先，短路其他所有判斷 ----
    decided_exits: Dict[str, ExitSignal] = {}
    for stock_id, evidence in evidence_by_stock.items():
        position = positions.get(stock_id)
        if position is None:
            continue
        avg_entry = _position_average_entry_price(db, position.id)
        today_close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        actual_position_return = (
            (today_close / avg_entry - 1) * 100.0
            if avg_entry not in (None, 0) and today_close is not None
            else None
        )
        if actual_position_return is not None and actual_position_return <= params["real_stop_loss_pct"]:
            decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_REAL_STOP_LOSS, row=evidence)
            continue
        sig = generate_exit_signal(evidence, params)
        if sig is not None:
            decided_exits[stock_id] = sig

    # ---- 進場/加碼候選：universe 內所有「今天沒有被判定出場」的股票 ----
    candidates: List[EntrySignal] = []
    for stock_id, evidence in evidence_by_stock.items():
        if stock_id in decided_exits:
            continue
        sig = generate_entry_signal(evidence, params)
        if sig is not None:
            candidates.append(sig)
    candidates = rank_candidates(candidates)

    # ---- 容量/現金分配（沿用沙盒 run_backtest.py 的「先扣今天要賣的股票釋放出的容量」邏輯）----
    projected_stocks = {sid for sid in positions if sid not in decided_exits}
    projected_units = 0
    for sid, pos in positions.items():
        if sid in decided_exits:
            continue
        projected_units += _position_units(db, pos.id)
    projected_cash = portfolio.cash
    for sid in decided_exits:
        pos = positions.get(sid)
        if pos is None:
            continue
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == pos.id).all()
        projected_cash += sum(lot.allocation for lot in lots)  # 保守估計，忽略損益

    accepted: Dict[str, EntrySignal] = {}
    skipped_capacity: Dict[str, EntrySignal] = {}
    for sig in candidates:
        stock_id = sig.row.stock_id
        already_held = stock_id in projected_stocks
        existing_units = 0
        if already_held and stock_id in positions:
            existing_units = _position_units(db, positions[stock_id].id)
        if not already_held and len(projected_stocks) >= params["max_stocks"]:
            skipped_capacity[stock_id] = sig
            continue
        if existing_units >= params["max_units_per_stock"]:
            skipped_capacity[stock_id] = sig
            continue
        if projected_units >= params["max_total_units"]:
            skipped_capacity[stock_id] = sig
            continue
        if projected_cash < params["unit_capital"]:
            skipped_capacity[stock_id] = sig
            continue
        accepted[stock_id] = sig
        projected_stocks.add(stock_id)
        projected_units += 1
        projected_cash -= params["unit_capital"]

    counts = {"buy": 0, "add": 0, "sell": 0, "hold": 0, "watch": 0, "skipped_capacity": 0}

    # ---- 寫 orders + 每日決策紀錄 ----
    for stock_id, exit_sig in decided_exits.items():
        # decided_exits 只會來自 positions.get(stock_id) 非 None 的股票（見上方出場判斷
        # 迴圈），這裡的 positions[stock_id] 保證存在。
        evidence = exit_sig.row
        held_units = _position_units(db, positions[stock_id].id)
        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version, stock_id=stock_id, stock_name=evidence.stock_name,
                action=ACTION_SELL, signal_date=target_date,
                scheduled_execution_date=next_weekday_guess(target_date),
                status=ORDER_STATUS_PENDING, reason=exit_sig.reason,
                units=held_units,
            )
        )
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SELL,
                action_reason=exit_sig.reason, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                position_units=held_units,
                scheduled_execution_date=next_weekday_guess(target_date),
            )
        )
        counts["sell"] += 1

    for stock_id, sig in accepted.items():
        evidence = sig.row
        already_held = stock_id in positions and stock_id not in decided_exits
        action = ACTION_ADD if already_held else ACTION_BUY
        snapshot = {
            "day_index": evidence.day_index, "hit_count_so_far": evidence.hit_count_so_far,
            "momentum_score": evidence.momentum_score, "p4_decision": evidence.p4_decision,
            "mark_to_market_return_pct": evidence.mark_to_market_return_pct,
            "first_seen_date": evidence.first_seen_date.isoformat(),
        }
        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version, stock_id=stock_id, stock_name=evidence.stock_name,
                action=action, signal_date=target_date,
                scheduled_execution_date=next_weekday_guess(target_date),
                status=ORDER_STATUS_PENDING, reason=f"entry_score={sig.entry_score:.2f}",
                entry_pattern=sig.entry_type, units=1,
                planned_amount=V1_STRATEGY_PARAMS["unit_capital"], signal_snapshot=snapshot,
            )
        )
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=action,
                action_reason=f"{sig.entry_type} entry_score={sig.entry_score:.2f}",
                entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                position_units=(_position_units(db, positions[stock_id].id) if already_held else 0),
                entry_score=sig.entry_score, scheduled_execution_date=next_weekday_guess(target_date),
            )
        )
        counts["add" if action == ACTION_ADD else "buy"] += 1

    for stock_id, sig in skipped_capacity.items():
        evidence = sig.row
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SKIPPED_CAPACITY,
                action_reason=f"符合 {sig.entry_type} 但資金/名額容量不足，entry_score={sig.entry_score:.2f}",
                entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                entry_score=sig.entry_score,
            )
        )
        counts["skipped_capacity"] += 1

    handled = set(decided_exits) | set(accepted) | set(skipped_capacity)
    for stock_id, evidence in evidence_by_stock.items():
        if stock_id in handled:
            continue
        held = stock_id in positions
        action = ACTION_HOLD if held else ACTION_WATCH
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=action,
                action_reason="持有中，今日未觸發出場/加碼條件" if held else "未持有，今日未觸發進場條件",
                p3_selected_today=evidence.p3_selected_today, hit_count=evidence.hit_count_so_far,
                momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                position_units=(_position_units(db, positions[stock_id].id) if held else None),
            )
        )
        counts["hold" if held else "watch"] += 1

    return counts


# ---------------------------------------------------------------------------
# Orchestrator 3：每日 portfolio 權益快照（equity curve 來源）
# ---------------------------------------------------------------------------
def create_portfolio_daily_snapshot(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> ShadowPortfolioDailySnapshot:
    portfolio = _get_or_create_portfolio(db, strategy_version)
    positions = _load_positions(db, strategy_version)

    invested_cost = 0.0
    market_value = 0.0
    total_units = 0
    for stock_id, pos in positions.items():
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == pos.id).all()
        total_units += len(lots)
        close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        for lot in lots:
            invested_cost += lot.allocation
            market_value += lot.shares * close if close is not None else lot.allocation

    total_equity = portfolio.cash + market_value
    initial_capital = V1_STRATEGY_PARAMS["initial_capital"]
    total_return_pct = (total_equity - initial_capital) / initial_capital * 100.0

    pending_buy_count = (
        db.query(func.count(ShadowStrategyOrder.id))
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
            ShadowStrategyOrder.action.in_([ACTION_BUY, ACTION_ADD]),
        )
        .scalar()
        or 0
    )
    pending_sell_count = (
        db.query(func.count(ShadowStrategyOrder.id))
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
            ShadowStrategyOrder.action == ACTION_SELL,
        )
        .scalar()
        or 0
    )

    snapshot = (
        db.query(ShadowPortfolioDailySnapshot)
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
            ShadowPortfolioDailySnapshot.trade_date == target_date,
        )
        .first()
    )
    if snapshot is None:
        snapshot = ShadowPortfolioDailySnapshot(strategy_version=strategy_version, trade_date=target_date)
        db.add(snapshot)

    snapshot.cash = portfolio.cash
    snapshot.invested_cost = invested_cost
    snapshot.market_value = market_value
    snapshot.total_equity = total_equity
    snapshot.total_return_pct = total_return_pct
    snapshot.realized_pnl = portfolio.realized_pnl_cumulative
    snapshot.unrealized_pnl = market_value - invested_cost
    snapshot.position_count = len(positions)
    snapshot.total_units = total_units
    snapshot.pending_buy_count = pending_buy_count
    snapshot.pending_sell_count = pending_sell_count

    return snapshot


# ---------------------------------------------------------------------------
# Orchestrator 4：35 個交易日一循環，循環結束強制清空重來
# ---------------------------------------------------------------------------
def _count_cycle_trading_days(
    db: Session, *, strategy_version: str, cycle_start_trade_date: date, target_date: date
) -> int:
    """比照全專案既有的 day_index 慣例（`_count_market_trading_days`）：用 COUNT
    query 算天數，不用遞增計數器欄位——同一天重跑天然 idempotent，不需要額外判斷
    這一天是否已經算過。"""
    count = (
        db.query(func.count(func.distinct(ShadowPortfolioDailySnapshot.trade_date)))
        .filter(
            ShadowPortfolioDailySnapshot.strategy_version == strategy_version,
            ShadowPortfolioDailySnapshot.trade_date >= cycle_start_trade_date,
            ShadowPortfolioDailySnapshot.trade_date <= target_date,
        )
        .scalar()
        or 0
    )
    return int(count)


def check_and_apply_cycle_reset(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> bool:
    """必須排在 `create_portfolio_daily_snapshot(target_date=target_date)` 之後
    （同一次呼叫的最後一步）——這個函式讀當天的 snapshot 判斷交易日數。

    回傳是否有觸發重置。**不會**清空 `ShadowStrategyDailyDecision`（append-only
    決策紀錄）或 `ShadowCompletedTrade`（永久保存的已平倉交易）——只清「目前部位」
    這個可變狀態，比照既有 `signal_watch_hits`（會清）vs
    `signal_watch_completed_archives`（永久）的既有分離原則。
    """
    portfolio = _get_or_create_portfolio(db, strategy_version)

    if portfolio.cycle_start_trade_date is None:
        # 這個 strategy_version 第一次真正運作（第一天不可能滿 35 天）
        portfolio.cycle_start_trade_date = target_date
        return False

    days_in_cycle = _count_cycle_trading_days(
        db, strategy_version=strategy_version,
        cycle_start_trade_date=portfolio.cycle_start_trade_date, target_date=target_date,
    )
    if days_in_cycle < CYCLE_LENGTH_TRADING_DAYS:
        return False

    # ---- 觸發重置：強制平倉所有目前持倉，寫入永久交易紀錄 ----
    positions = _load_positions(db, strategy_version)
    for stock_id, position in positions.items():
        close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == position.id).all()
        for lot in lots:
            exit_price = close if close is not None else lot.entry_price  # 缺當日收盤價的保守 fallback
            proceeds = lot.shares * exit_price
            portfolio.cash += proceeds
            portfolio.realized_pnl_cumulative += proceeds - lot.allocation
            _record_completed_trade(
                db,
                strategy_version=strategy_version,
                cycle_number=portfolio.cycle_number,
                lot=lot,
                stock_id=stock_id,
                stock_name=position.stock_name,
                exit_reason=EXIT_REASON_CYCLE_RESET,
                exit_signal_date=target_date,
                exit_execution_date=target_date,  # 行政性強制動作，不等 T+1
                exit_price=exit_price,
            )
            db.delete(lot)
        db.delete(position)

    # ---- 取消所有還在排隊的 pending 訂單（部位已經沒了，訂單也失去意義）----
    db.query(ShadowStrategyOrder).filter(
        ShadowStrategyOrder.strategy_version == strategy_version,
        ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
    ).update({"status": "CANCELLED"}, synchronize_session=False)

    # ---- 重設 portfolio 現金/循環狀態 ----
    portfolio.cash = V1_STRATEGY_PARAMS["initial_capital"]
    portfolio.realized_pnl_cumulative = 0.0
    portfolio.cycle_number += 1
    portfolio.cycle_start_trade_date = None  # 下次呼叫的第一天會重新設定

    return True
