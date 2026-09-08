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
   **-8% 真實停損 > P4_STOP > 官方平倉日（魚尾追蹤週期結束）> +10% 固定停利**——不是單純
   「P4_STOP 最優先」

v1 沒有獨立的「加碼（WINNER_ADD）」判斷邏輯（那是 v2/Unified 才有的機制）：v1 的加碼純粹是
「已持有 1 lot 的股票，某天又觸發一次 setup_a/setup_b」，用**完全相同**的
`generate_entry_signal`／`compute_entry_score`／`rank_candidates`，只是持倉容量允許加到
第 2 lot。BUY vs ADD 只是「這檔股票原本有沒有持倉」的顯示區分，不是兩套演算法。

**2026-09-08 修復：cohort 身份（first_seen_date）與 P4 SignalObservation 混用的重大 bug**——
第一版把 `resolve_tracking_universe`／`build_daily_evidence` 的 `first_seen_date`／
`day_index`／`hit_count_so_far`／`is_official_exit_signal_day` 全部建在
`SignalObservation.started_signal_date`（P4 自己的觀察生命週期起點）上，但沙盒驗證用的
`daily_data.csv` 其實是從**魚尾**（`signal_watch_hits`／`signal_watch_completed_archives`／
`signal_watch_stopped_observations`，P3 選股驅動的追蹤系統）匯出的。P4 與魚尾是兩套獨立管理
的生命週期系統，起訖日經常對不上（例如 2026-08-11 那次「強制結算誤刪過廣」事件只重置了魚尾
側，P4 的 `SignalObservation` 完全沒被動到）。用錯來源會在沙盒從未出現過的日期產生「幽靈交易」，
真實驗證：對 2026-09-04 的 production 資料重跑，發現 49 個 P4 判定要評估的股票裡有 24 個
（近半數）first_seen_date 跟魚尾側完全對不上，backfill 結果因此從沙盒驗證過的 +12.05%／
+9.93% 惡化成 -9.09%。

同一輪還發現 `is_official_exit_signal_day` 的定義本身就寫錯：docstring 寫「30 個交易日期滿」
並直接套用 `day_index >= archive.ARCHIVE_RETENTION_TRADE_DAYS`，但這個門檻在整個 21 個交易日
的驗證視窗裡**從未被觸發過**（day_index 最高只到 17），然而沙盒 CSV 裡這個欄位確實有 174 筆
`True`——實際比對後發現它真正代表的是「魚尾判定這個追蹤週期在這一天正式結束」（`signal_watch_
completed_archives`／`signal_watch_stopped_observations` 的 `completed_trade_date == 這天`），
不論結束原因是 30 日期滿、提前停損/回落規則、P4 STOP 還是人工重置，皆算。

修復後的正確資料來源：
- `first_seen_date`／cohort 身份：魚尾（`_load_grouped_hits` 同語意，取目前仍活躍的
  `signal_watch_hits` 最早 `snapshot_date`；backfill 對已經封存的歷史週期額外查
  `signal_watch_completed_archives`／`signal_watch_stopped_observations`）
- `hit_count_so_far`／`p3_selected_today`／`momentum_score`：週期仍活躍時直接查
  `signal_watch_hits`（跟原本一樣快）；週期已經封存（`signal_watch_hits` 已被硬刪除，只有
  backfill 重播已經是過去的日期才會發生）時，改用永久保留的 `SignalSnapshot.watchlist`
  逐日重建
- `p4_decision`：不能要求 P4 `started_signal_date` 完全等於魚尾 `first_seen_date`（兩者本來
  就經常不同），改成用跟 `resolve_tracking_universe` 相同的「review 歷史判斷是否仍在進行中」
  邏輯，找出這檔股票在 `target_date` 當下真正對應的 P4 觀察 episode
- `is_official_exit_signal_day`：改查魚尾封存表的 `completed_trade_date == target_date`
- ETF 排除：改用 Phase 1 canonical classification 的 `EtfClassification` 表（`SignalObservation.
  asset_type` 對槓桿/反向 ETF（如 `00753L`）分類不準確，已在真實資料驗證中發現）
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
    EtfClassification,
    ShadowCompletedTrade,
    ShadowMissedCandidate,
    ShadowPositionLot,
    ShadowPortfolioDailySnapshot,
    ShadowStrategyDailyDecision,
    ShadowStrategyOrder,
    ShadowVirtualPortfolio,
    ShadowVirtualPosition,
    ShadowWinnerTracking,
    SignalObservation,
    SignalObservationReview,
    SignalSnapshot,
    SignalWatchCompletedArchive,
    SignalWatchHit,
    SignalWatchStoppedObservation,
)
from app.signals import archive

# 魚尾封存表：一個追蹤週期會落在哪張表，取決於它結束時是自然/提前/P4 停止（進
# signal_watch_completed_archives）還是人工重置（signal_watch_stopped_observations 也會有，
# 兩表 schema 相同、语意重疊，backfill/point-in-time 查詢一律兩張都查，見 M23 CLAUDE.md）
_FISHTAIL_ARCHIVE_MODELS = (SignalWatchCompletedArchive, SignalWatchStoppedObservation)

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
            ShadowMissedCandidate.__table__,
            ShadowWinnerTracking.__table__,
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

CYCLE_LENGTH_TRADING_DAYS = 35

STRATEGY_VERSION_V1_FROZEN = "v1_frozen"
STRATEGY_VERSION_CLEAN_FIXED_TP = "CLEAN_FIXED_TP"
STRATEGY_VERSION_CLEAN_NO_FIXED_TP = "CLEAN_NO_FIXED_TP"
STRATEGY_VERSION_FORWARD_V1 = "FORWARD_V1_202609"

# ---------------------------------------------------------------------------
# 多策略版本參數登記表 —— 2026-09 新增（Clean Baselines + FORWARD_V1_202609）。
#
# `V1_STRATEGY_PARAMS` 本身逐字不動（上面凍結區塊）；這裡只用「淺層 dict 合併」
# 疊加語意旗標，v1_frozen 疊上去的旗標值全部等於它既有的隱含行為（見各旗標註解），
# 保證這次重構對 v1_frozen 是零行為變更。任何呼叫這個引擎的地方都必須先
# `params = STRATEGY_PARAMS_BY_VERSION[strategy_version]` 拿到完整參數，
# **不可再直接引用模組層級的 `V1_STRATEGY_PARAMS`**（那是 2026-09 之前的舊 bug：
# `execute_pending_strategy_orders`/`run_daily_trading_strategy` 幾處都曾經硬編碼
# 這個常數，導致任何非 v1 的 strategy_version 實際上仍會用 v1 的資金/門檻執行）。
#
# 語意旗標：
#   take_profit_basis: "mark_to_market" | "actual_position" | None（None=完全不做固定停利）
#   max_units_per_stock / max_total_units: None = 無上限（FORWARD_V1 拿掉這兩個上限）
#   max_position_exposure_pct: None = 無曝險上限；0.50 = 單檔 position cost 不得超過
#       目前 portfolio equity 的 50%（FORWARD_V1 專用）
#   add_requires_profit: True 時，加碼前必須 actual_position_return > 0，否則
#       SKIP_ADD_POSITION_NOT_PROFITABLE（絕不攤平，FORWARD_V1 專用）
#   cycle_reset_trading_days: None = 不做強制循環重置；v1_frozen 沿用既有 35 交易日
#   granular_skip_reasons: True 時才會把「容量不足」拆成 SKIP_PORTFOLIO_FULL /
#       SKIP_INSUFFICIENT_CASH / SKIP_POSITION_EXPOSURE_LIMIT 並寫入
#       `ShadowMissedCandidate`；v1_frozen/Clean Baselines 維持既有單一
#       ACTION_SKIPPED_CAPACITY 決策文字，逐字不變
#   track_winners: True 時，daily runner 會額外呼叫 `update_winner_tracking()`
# ---------------------------------------------------------------------------
STRATEGY_PARAMS_BY_VERSION: Dict[str, Dict[str, Any]] = {
    STRATEGY_VERSION_V1_FROZEN: {
        **V1_STRATEGY_PARAMS,
        "take_profit_basis": "mark_to_market",
        "max_position_exposure_pct": None,
        "add_requires_profit": False,
        "cycle_reset_trading_days": CYCLE_LENGTH_TRADING_DAYS,
        "granular_skip_reasons": False,
        "track_winners": False,
    },
    STRATEGY_VERSION_CLEAN_FIXED_TP: {
        **V1_STRATEGY_PARAMS,
        "take_profit_basis": "actual_position",
        "max_position_exposure_pct": None,
        "add_requires_profit": False,
        "cycle_reset_trading_days": None,
        "granular_skip_reasons": False,
        "track_winners": False,
    },
    STRATEGY_VERSION_CLEAN_NO_FIXED_TP: {
        **V1_STRATEGY_PARAMS,
        "take_profit_signal_pct": None,
        "take_profit_basis": None,
        "max_position_exposure_pct": None,
        "add_requires_profit": False,
        "cycle_reset_trading_days": None,
        "granular_skip_reasons": False,
        "track_winners": False,
    },
    STRATEGY_VERSION_FORWARD_V1: {
        "initial_capital": V1_STRATEGY_PARAMS["initial_capital"],
        "unit_capital": V1_STRATEGY_PARAMS["unit_capital"],
        "max_stocks": V1_STRATEGY_PARAMS["max_stocks"],
        "max_units_per_stock": None,
        "max_total_units": None,
        "max_position_exposure_pct": 0.50,
        "setup_a": V1_STRATEGY_PARAMS["setup_a"],
        "setup_b": V1_STRATEGY_PARAMS["setup_b"],
        "take_profit_signal_pct": None,
        "take_profit_basis": None,
        "real_stop_loss_pct": V1_STRATEGY_PARAMS["real_stop_loss_pct"],
        "add_requires_profit": True,
        "cycle_reset_trading_days": None,
        "granular_skip_reasons": True,
        "track_winners": True,
    },
}

SKIP_REASON_PORTFOLIO_FULL = "SKIP_PORTFOLIO_FULL"
SKIP_REASON_INSUFFICIENT_CASH = "SKIP_INSUFFICIENT_CASH"
SKIP_REASON_POSITION_EXPOSURE_LIMIT = "SKIP_POSITION_EXPOSURE_LIMIT"
ACTION_SKIPPED_ADD_NOT_PROFITABLE = "SKIP_ADD_POSITION_NOT_PROFITABLE"
WINNER_10_FLAG = "WINNER_10_REACHED"
WINNER_10_THRESHOLD_PCT = 10.0

_SKIP_REASON_LABELS = {
    SKIP_REASON_PORTFOLIO_FULL: "名額已滿",
    SKIP_REASON_INSUFFICIENT_CASH: "現金不足",
    SKIP_REASON_POSITION_EXPOSURE_LIMIT: "單檔曝險已達上限",
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


def generate_exit_signal(
    row: EvidenceRow, params: dict, *, actual_position_return: Optional[float] = None
) -> Optional[ExitSignal]:
    """優先序：P4_STOP > 官方平倉日 > 固定停利（若啟用）。**-8% 真實停損不在這裡**——
    呼叫端必須先用實際部位報酬（average_entry_price 對今日收盤）檢查真實停損，
    只有沒觸發時才呼叫這個函式（見 `run_daily_trading_strategy` 與模組頂部說明）。

    `params["take_profit_basis"]` 決定固定停利的判斷基準：
      - "mark_to_market"（v1_frozen 既有行為，逐字不變）：用 `row.mark_to_market_return_pct`
      - "actual_position"（CLEAN_FIXED_TP）：用呼叫端傳入的 `actual_position_return`
        （真正 (今日收盤/實際持倉均價 - 1)，不是 tracking baseline）
      - None（CLEAN_NO_FIXED_TP / FORWARD_V1_202609）：完全不做固定停利判斷
    `actual_position_return` 未傳入時預設 None，對 `take_profit_basis="mark_to_market"`
    的既有呼叫端（v1_frozen）完全零影響。
    """
    if row.p4_decision == "STOP_OBSERVING":
        return ExitSignal(reason=EXIT_REASON_P4_STOP, row=row)
    if row.is_official_exit_signal_day:
        return ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=row)

    take_profit = params.get("take_profit_signal_pct")
    # `.get(..., "mark_to_market")`：key 完全缺席（呼叫端傳入尚未套用這次重構的舊版
    # params dict，例如直接傳 `V1_STRATEGY_PARAMS` 常數本身）時，預設回到 v1 既有行為；
    # 只有明確設成 `None`（CLEAN_NO_FIXED_TP / FORWARD_V1_202609 在登記表裡就是這樣設）
    # 才代表「這個策略版本刻意關掉固定停利」。
    basis = params.get("take_profit_basis", "mark_to_market")
    if take_profit is None or basis is None:
        return None

    if basis == "mark_to_market":
        reference = row.mark_to_market_return_pct
    elif basis == "actual_position":
        reference = actual_position_return
    else:
        raise ValueError(f"Unknown take_profit_basis: {basis!r}")

    if reference is not None and reference >= take_profit:
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


def _fishtail_cohort_is_active(db: Session, *, stock_id: str, first_seen_date: date) -> bool:
    """判斷 (stock_id, first_seen_date) 這個魚尾追蹤週期，在**呼叫當下**（不是
    target_date）是否仍然活躍（尚未結算/封存）。活躍時 `signal_watch_hits` 還在，可以
    直接查；已封存時該表已被硬刪除（見 `archive.py` 既有 settle/refresh 邏輯），
    必須改用永久保留的 `SignalSnapshot.watchlist` 逐日重建（見 `_count_hits_so_far_
    from_snapshots`／`_p3_selection_from_snapshot`）。這個分流只在 backfill/replay
    對著早就結束的歷史日期重播時才會走到「已封存」分支——正常每日排程評估「今天」的
    活躍持股，一定是走「活躍」分支。"""
    earliest = (
        db.query(func.min(SignalWatchHit.snapshot_date))
        .filter(SignalWatchHit.stock_id == stock_id)
        .scalar()
    )
    return earliest == first_seen_date


def _count_hits_so_far(db: Session, *, stock_id: str, first_seen_date: date, target_date: date) -> int:
    """hit_count_so_far（週期仍活躍時）：這個魚尾 cohort 被 P3 選中過幾次，到
    target_date（含）為止。

    `first_seen_date` 本身就代表 P3 第一次選中——這永遠算「第 1 次」。之後每多一筆
    `signal_watch_hits`（`snapshot_date > first_seen_date`）代表額外一次真正的重選
    確認，才 +1。
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


# process-local 快取：{snapshot_date: {stock_id: momentum_score}}——backfill/replay 對
# 已封存週期逐股票逐日重播時，同一天的 SignalSnapshot.watchlist（一份可能上百檔股票的
# 大型 JSON）常被重複查詢上百次；第一次真的跑production backfill 時，這個重複查詢量
# 直接把遠端 Postgres 連線拖到逾時斷線（`consuming input failed: server closed the
# connection unexpectedly`）。快取讓每個 snapshot_date 全程只真的查一次 DB，同一個
# Python process 內的其餘查詢全部命中記憶體。單日 snapshot 一旦寫入（`signal_snapshots`
# 是 append-only 的歷史紀錄，只有「今天」這筆可能被重新產生覆蓋），對已經是過去的日期
# 不會再變動，process 內快取不會有 stale 風險；`run_shadow_portfolio.py`／
# `backfill_shadow_portfolio_replay.py` 都是每次執行都是全新 process，重啟自然清空。
_SNAPSHOT_WATCHLIST_CACHE: Dict[date, Dict[str, Optional[float]]] = {}


def _snapshot_watchlist_index(db: Session, snapshot_date_: date) -> Dict[str, Optional[float]]:
    """某一天 `SignalSnapshot.watchlist` 的 `{stock_id: momentum_score}` 索引（一次查詢
    整理成 dict，之後同一天的任何股票查詢都直接命中記憶體）。watchlist item 的股票代號
    欄位是 `"stock"` 不是 `"stock_id"`（既有全站慣例，見
    `archive._load_snapshot_reports_for_stock`）。"""
    cached = _SNAPSHOT_WATCHLIST_CACHE.get(snapshot_date_)
    if cached is not None:
        return cached
    snap = db.query(SignalSnapshot).filter(SignalSnapshot.snapshot_date == snapshot_date_).first()
    index: Dict[str, Optional[float]] = {}
    if snap is not None:
        for item in snap.watchlist or []:
            metrics = item.get("signal_metrics") or {}
            index[str(item.get("stock"))] = metrics.get("momentum_score")
    _SNAPSHOT_WATCHLIST_CACHE[snapshot_date_] = index
    return index


def _count_hits_so_far_from_snapshots(
    db: Session, *, stock_id: str, first_seen_date: date, target_date: date
) -> int:
    """hit_count_so_far（週期已封存時的 fallback）：`signal_watch_hits` 已被硬刪除，
    改從永久保留的 `SignalSnapshot.watchlist` 逐日重建，透過 `_snapshot_watchlist_index`
    的 process-local 快取避免對每一天重複查詢。"""
    additional = 0
    d = first_seen_date + timedelta(days=1)
    while d <= target_date:
        if stock_id in _snapshot_watchlist_index(db, d):
            additional += 1
        d += timedelta(days=1)
    return 1 + additional


def _p3_selection_from_snapshot(
    db: Session, *, stock_id: str, target_date: date
) -> Tuple[bool, Optional[float]]:
    """週期已封存時的 fallback：從 `SignalSnapshot.watchlist` 讀 target_date 當天
    是否有這檔股票、以及當天的 momentum_score，取代已被硬刪除的 `signal_watch_hits`
    單日查詢。"""
    index = _snapshot_watchlist_index(db, target_date)
    if stock_id not in index:
        return False, None
    return True, index[stock_id]


def _resolve_relevant_observation(
    db: Session, *, stock_id: str, target_date: date
) -> Optional[SignalObservation]:
    """找出 stock_id 在 target_date 當下「正在進行」的 P4 觀察 episode——**不要求**
    `SignalObservation.started_signal_date` 等於魚尾的 first_seen_date（兩套系統的
    episode 邊界獨立、經常不同步，見模組頂部說明）。同一檔股票理論上同時只會有一個
    進行中的 episode，若因資料異常同時有多個符合，取 started_signal_date 最新的一個。

    「已經停止」判斷用 `review_date < target_date`（**嚴格小於**，不是 `<=`）——
    STOP_OBSERVING 判定當天，仍然要能讀到「今天」的這個決策本身，`generate_exit_signal`
    的 P4_STOP 分支才有機會在正確的那天觸發（沙盒驗證資料的真實案例：6213 聯茂在追蹤
    最後一天，`p4_decision=STOP_OBSERVING` 跟平倉都記在同一天）。這跟
    `resolve_tracking_universe` 判斷「今天的 universe 該不該包含這檔股票」是不同問題
    （那邊已完全改用魚尾資料，不再查這兩張表）。
    """
    candidates = (
        db.query(SignalObservation)
        .filter(
            SignalObservation.stock_id == stock_id,
            SignalObservation.started_signal_date <= target_date,
        )
        .order_by(SignalObservation.started_signal_date.desc())
        .all()
    )
    for obs in candidates:
        already_stopped = (
            db.query(SignalObservationReview.id)
            .filter(
                SignalObservationReview.observation_id == obs.id,
                SignalObservationReview.review_date < target_date,
                SignalObservationReview.decision == "STOP_OBSERVING",
            )
            .first()
            is not None
        )
        if not already_stopped:
            return obs
    return None


def _is_official_exit_signal_day(
    db: Session, *, stock_id: str, first_seen_date: date, target_date: date
) -> bool:
    """官方平倉日：魚尾（`signal_watch_completed_archives`／`signal_watch_stopped_
    observations`）判定這個 (stock_id, first_seen_date) 追蹤週期在 target_date 當天
    正式結束——不論原因（30 交易日期滿、提前結算停損/回落規則、P4 STOP、人工重置皆算）。
    沙盒驗證用的 v1 策略把這個日子當成官方平倉日的保底出場訊號，**不是** `day_index >=
    30`（真實資料比對後確認：這個門檻在正常追蹤窗口內幾乎不會被觸發，且與沙盒 CSV 的
    實際欄位語意不符）。
    """
    for model_cls in _FISHTAIL_ARCHIVE_MODELS:
        hit = (
            db.query(model_cls.stock_id)
            .filter(
                model_cls.stock_id == stock_id,
                model_cls.first_seen_date == first_seen_date,
                model_cls.completed_trade_date == target_date,
            )
            .first()
        )
        if hit is not None:
            return True
    return False


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

    `first_seen_date` 必須是**魚尾**（`signal_watch_hits`／已封存的
    `signal_watch_completed_archives`／`signal_watch_stopped_observations`）認定的
    追蹤週期起點，不是 P4 `SignalObservation.started_signal_date`（見模組頂部說明，
    兩者經常不同步）；呼叫端（`resolve_tracking_universe`）已經保證這點。

    **報酬率完全由 `daily_price` 直接算出，不依賴 `signal_watch_hits`**：baseline 沿用
    既有 `archive.py` 的慣例（第 2 個交易日 `(open+close)/2`，第 2 天固定 0%），對
    `daily_price` 直接算，跟這個週期是否仍活躍無關。
    """
    day_index = _count_market_trading_days(db, first_seen_date=first_seen_date, target_date=target_date)

    if _fishtail_cohort_is_active(db, stock_id=stock_id, first_seen_date=first_seen_date):
        hit_count_so_far = _count_hits_so_far(
            db, stock_id=stock_id, first_seen_date=first_seen_date, target_date=target_date
        )
        hit_today = (
            db.query(SignalWatchHit)
            .filter(SignalWatchHit.stock_id == stock_id, SignalWatchHit.snapshot_date == target_date)
            .first()
        )
        p3_selected_today = hit_today is not None
        momentum_score: Optional[float] = (
            (hit_today.signal_metrics or {}).get("momentum_score") if hit_today is not None else None
        )
    else:
        # 這個週期已經結算/封存（signal_watch_hits 已被硬刪除）——只有 backfill/replay
        # 重播早就結束的歷史日期才會走到這裡；改用永久保留的 SignalSnapshot 逐日重建。
        hit_count_so_far = _count_hits_so_far_from_snapshots(
            db, stock_id=stock_id, first_seen_date=first_seen_date, target_date=target_date
        )
        p3_selected_today, momentum_score = _p3_selection_from_snapshot(
            db, stock_id=stock_id, target_date=target_date
        )

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

    observation = _resolve_relevant_observation(db, stock_id=stock_id, target_date=target_date)
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

    is_official_exit_signal_day = _is_official_exit_signal_day(
        db, stock_id=stock_id, first_seen_date=first_seen_date, target_date=target_date
    )

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


def resolve_fishtail_universe(db: Session, *, target_date: date) -> Dict[str, Tuple[str, str, date]]:
    """魚尾（`signal_watch_hits`／已封存的 `signal_watch_completed_archives`／
    `signal_watch_stopped_observations`）認定的追蹤週期聯集，`{stock_id: (stock_id,
    stock_name, first_seen_date)}`——`resolve_tracking_universe()` 的 (a)+(b) 部分抽出
    成獨立公開函式，供其他策略版本（例如實驗性的 Winner Management / Rotation 引擎，見
    `shadow_portfolio_experiments.py`）重用同一套「魚尾候選來源」邏輯，不用各自重寫一份；
    ETF 排除／(c) 現有持倉 union 留在 `resolve_tracking_universe()`（那兩步是 v1 orchestrator
    特有的行為，不是所有呼叫端都需要）。

    (a) 目前仍在 `signal_watch_hits` 活躍追蹤中的股票（尚未結算/封存），first_seen_date
        = 該股票目前這輪的最早 `snapshot_date`
    (b) 已經結算/封存、但 `[first_seen_date, completed_trade_date]` 涵蓋 target_date 的
        歷史週期——(a) 只反映「呼叫當下」的即時狀態，對已經結算的歷史週期查不到任何列；
        backfill/replay 重播「已經是過去」的日期時，必須額外查這兩張封存表才拿得到正確
        的 universe。
    """
    universe: Dict[str, Tuple[str, str, date]] = {}

    active_rows = (
        db.query(SignalWatchHit.stock_id, SignalWatchHit.stock_name, func.min(SignalWatchHit.snapshot_date))
        .group_by(SignalWatchHit.stock_id, SignalWatchHit.stock_name)
        .all()
    )
    for stock_id, stock_name, first_seen_date in active_rows:
        if first_seen_date <= target_date:
            universe[stock_id] = (stock_id, stock_name, first_seen_date)

    for model_cls in _FISHTAIL_ARCHIVE_MODELS:
        archived_rows = (
            db.query(model_cls.stock_id, model_cls.stock_name, model_cls.first_seen_date)
            .filter(
                model_cls.first_seen_date <= target_date,
                model_cls.completed_trade_date >= target_date,
            )
            .all()
        )
        for stock_id, stock_name, first_seen_date in archived_rows:
            universe.setdefault(stock_id, (stock_id, stock_name, first_seen_date))

    return universe


def filter_out_etfs(db: Session, universe: Dict[str, Tuple[str, str, date]]) -> Dict[str, Tuple[str, str, date]]:
    """v1 凍結參數 `exclude_etf=True`：改查 Phase 1 canonical classification 的
    `EtfClassification` 表——真實資料驗證發現 `SignalObservation.asset_type` 對槓桿/反向
    ETF（如 `00753L`）分類不準確，`EtfClassification` 才是正確辨識來源。抽成獨立函式
    供 `resolve_tracking_universe()` 與其他策略版本共用。"""
    etf_ids = {
        row[0]
        for row in db.query(EtfClassification.stock_id)
        .filter(EtfClassification.stock_id.in_(list(universe.keys())))
        .all()
    }
    return {sid: v for sid, v in universe.items() if sid not in etf_ids}


def resolve_tracking_universe(
    db: Session, *, strategy_version: str, target_date: date
) -> List[Tuple[str, str, date]]:
    """回傳 target_date 當天要評估的 (stock_id, stock_name, first_seen_date) 清單——
    `first_seen_date` 一律以**魚尾**認定的追蹤週期起點為準，**不是** P4
    `SignalObservation.started_signal_date`（見模組頂部說明：兩套系統的 episode 邊界
    獨立、經常不同步；沙盒驗證用的資料正是從魚尾匯出的）。

    `resolve_fishtail_universe()` 的聯集，再 union (c) 目前這個 strategy_version 在
    Shadow Portfolio 有持倉的股票（防禦性——即使魚尾/P4 那邊已結算，已持倉的股票仍要
    繼續被評估是否該出場，不能因為追蹤週期已終止就漏掉真正持有的部位），最後套用
    `filter_out_etfs()`。
    """
    universe = dict(resolve_fishtail_universe(db, target_date=target_date))

    for pos in (
        db.query(ShadowVirtualPosition)
        .filter(ShadowVirtualPosition.strategy_version == strategy_version)
        .all()
    ):
        universe.setdefault(pos.stock_id, (pos.stock_id, pos.stock_name, pos.first_seen_date))

    filtered = filter_out_etfs(db, universe)

    return sorted(filtered.values(), key=lambda t: t[0])


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
            cash=STRATEGY_PARAMS_BY_VERSION[strategy_version]["initial_capital"],
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


def _position_cost(db: Session, position_id: int) -> float:
    """該部位所有 lot 的成本加總（不是市值）——Part 14 單檔曝險上限比的是
    position cost，不是 mark-to-market 市值。"""
    return (
        db.query(func.coalesce(func.sum(ShadowPositionLot.allocation), 0.0))
        .filter(ShadowPositionLot.position_id == position_id)
        .scalar()
        or 0.0
    )


def _compute_mark_to_market_equity(
    db: Session, *, portfolio: ShadowVirtualPortfolio, positions: Dict[str, ShadowVirtualPosition], target_date: date
) -> float:
    """cash + 目前所有持倉的市值（缺當日收盤價時保守以成本代替，比照
    `create_portfolio_daily_snapshot` 既有算法）——FORWARD_V1_202609 的
    `max_position_exposure_pct` 需要用同一個「當下 portfolio equity」定義，
    抽成獨立函式供兩處共用，避免出現兩套不一致的 equity 算法。"""
    market_value = 0.0
    for stock_id, pos in positions.items():
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == pos.id).all()
        close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        for lot in lots:
            market_value += lot.shares * close if close is not None else lot.allocation
    return portfolio.cash + market_value


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
    Idempotent：只處理 status=PENDING 的訂單，已執行過的訂單天然不會被重複處理。

    **2026-09 修正**：unit_capital 一律用 `STRATEGY_PARAMS_BY_VERSION[strategy_version]`，
    不可再直接引用模組層級的 `V1_STRATEGY_PARAMS`（舊 bug：無論 `strategy_version` 是誰，
    這裡先前都拿 v1 的資金額度執行，任何非 v1 策略會被靜默套用錯誤的單位金額）。
    """
    params = STRATEGY_PARAMS_BY_VERSION[strategy_version]
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
            allocation = min(params["unit_capital"], portfolio.cash)
            if allocation < params["unit_capital"] - 1e-6:
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
    的動作）。

    Idempotent：`already_decided` 用 `(strategy_version, trade_date, stock_id)` 排除
    今天已經處理過的股票；同一天重跑只會補上「這次呼叫之前還沒被評估過」的股票，不會對
    已存在的決策重寫或重複建立訂單。
    """
    params = STRATEGY_PARAMS_BY_VERSION[strategy_version]
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

    # ---- 出場判斷：真實 -8% 停損最優先，短路其他所有判斷；順便記下每檔目前持有股票
    # 「今天」的真實部位報酬（Part 5 的 actual_position_return），下面加碼門檻與
    # HOLD 決策都重用同一份，不重算兩次、也不會跟這裡用的基準不一致 ----
    decided_exits: Dict[str, ExitSignal] = {}
    actual_return_by_stock: Dict[str, Optional[float]] = {}
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
        actual_return_by_stock[stock_id] = actual_position_return
        if actual_position_return is not None and actual_position_return <= params["real_stop_loss_pct"]:
            decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_REAL_STOP_LOSS, row=evidence)
            continue
        sig = generate_exit_signal(evidence, params, actual_position_return=actual_position_return)
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

    # FORWARD_V1_202609 專用：單檔曝險比對用的 equity，決策當下算一次固定值，不隨這次
    # 迴圈內陸續 accept 的候選重算（見 `_compute_mark_to_market_equity` docstring）。
    # 其餘策略版本 `max_position_exposure_pct` 為 None，完全不會走進這個分支。
    current_equity: Optional[float] = None
    if params.get("max_position_exposure_pct") is not None:
        current_equity = _compute_mark_to_market_equity(
            db, portfolio=portfolio, positions=positions, target_date=target_date
        )

    accepted: Dict[str, EntrySignal] = {}
    skipped_capacity: Dict[str, Tuple[EntrySignal, str]] = {}
    skipped_not_profitable: Dict[str, EntrySignal] = {}
    for sig in candidates:
        stock_id = sig.row.stock_id
        already_held = stock_id in projected_stocks

        # Part 16：絕不攤平——加碼前必須先確認目前部位已經是賺錢的，否則整筆直接
        # 略過（既不是 accepted，也不算容量不足，是獨立的策略性拒絕）。只有
        # `add_requires_profit=True` 的版本（FORWARD_V1_202609）會走到這個分支。
        if already_held and params.get("add_requires_profit"):
            current_return = actual_return_by_stock.get(stock_id)
            if current_return is None or current_return <= 0:
                skipped_not_profitable[stock_id] = sig
                continue

        existing_units = 0
        existing_cost = 0.0
        if already_held and stock_id in positions:
            existing_units = _position_units(db, positions[stock_id].id)
            existing_cost = _position_cost(db, positions[stock_id].id)

        skip_reason: Optional[str] = None
        if not already_held and len(projected_stocks) >= params["max_stocks"]:
            skip_reason = SKIP_REASON_PORTFOLIO_FULL
        elif params.get("max_units_per_stock") is not None and existing_units >= params["max_units_per_stock"]:
            skip_reason = SKIP_REASON_PORTFOLIO_FULL
        elif params.get("max_total_units") is not None and projected_units >= params["max_total_units"]:
            skip_reason = SKIP_REASON_PORTFOLIO_FULL
        elif projected_cash < params["unit_capital"]:
            skip_reason = SKIP_REASON_INSUFFICIENT_CASH
        elif (
            params.get("max_position_exposure_pct") is not None
            and current_equity
            and (existing_cost + params["unit_capital"]) / current_equity > params["max_position_exposure_pct"]
        ):
            skip_reason = SKIP_REASON_POSITION_EXPOSURE_LIMIT

        if skip_reason is not None:
            skipped_capacity[stock_id] = (sig, skip_reason)
            continue

        accepted[stock_id] = sig
        projected_stocks.add(stock_id)
        projected_units += 1
        projected_cash -= params["unit_capital"]

    counts = {
        "buy": 0, "add": 0, "sell": 0, "hold": 0, "watch": 0,
        "skipped_capacity": 0, "skipped_not_profitable": 0,
    }

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
                position_units=held_units, actual_position_return=actual_return_by_stock.get(stock_id),
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
                planned_amount=params["unit_capital"], signal_snapshot=snapshot,
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
                actual_position_return=(actual_return_by_stock.get(stock_id) if already_held else None),
                entry_score=sig.entry_score, scheduled_execution_date=next_weekday_guess(target_date),
            )
        )
        counts["add" if action == ACTION_ADD else "buy"] += 1

    for stock_id, (sig, skip_reason) in skipped_capacity.items():
        evidence = sig.row
        if params.get("granular_skip_reasons"):
            # FORWARD_V1_202609：拆分具體原因 + 寫入 ShadowMissedCandidate 供事後
            # 歸因（Part 22/50，5d/10d/max return 一律在報告產生時另外 join 現算）。
            action_reason = (
                f"{skip_reason}：符合 {sig.entry_type} 但{_SKIP_REASON_LABELS[skip_reason]}，"
                f"entry_score={sig.entry_score:.2f}"
            )
            db.add(
                ShadowStrategyDailyDecision(
                    strategy_version=strategy_version, trade_date=target_date,
                    stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SKIPPED_CAPACITY,
                    action_reason=action_reason,
                    entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                    hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                    mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                    entry_score=sig.entry_score,
                )
            )
            db.add(
                ShadowMissedCandidate(
                    strategy_version=strategy_version, trade_date=target_date,
                    stock_id=stock_id, stock_name=evidence.stock_name,
                    entry_pattern=sig.entry_type, entry_score=sig.entry_score,
                    skip_reason=skip_reason,
                    portfolio_snapshot={
                        "cash": portfolio.cash,
                        "position_count": len(positions),
                        "equity": current_equity,
                    },
                )
            )
        else:
            # v1_frozen / Clean Baselines：逐字保留既有決策文字（單一籠統原因），
            # 零行為變更——這兩類策略從不啟用 granular_skip_reasons。
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

    for stock_id, sig in skipped_not_profitable.items():
        evidence = sig.row
        current_return = actual_return_by_stock.get(stock_id)
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SKIPPED_ADD_NOT_PROFITABLE,
                action_reason=(
                    f"{sig.entry_type} 再次成立（entry_score={sig.entry_score:.2f}），"
                    f"但目前部位尚未獲利（actual_position_return="
                    f"{current_return:.2f}% ）—— 依規則絕不攤平，不加碼"
                    if current_return is not None
                    else f"{sig.entry_type} 再次成立，但無法確認目前部位是否獲利——依規則絕不攤平，不加碼"
                ),
                entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                actual_position_return=current_return, entry_score=sig.entry_score,
                position_units=(_position_units(db, positions[stock_id].id) if stock_id in positions else None),
            )
        )
        counts["skipped_not_profitable"] += 1

    handled = set(decided_exits) | set(accepted) | set(skipped_capacity) | set(skipped_not_profitable)
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
                actual_position_return=(actual_return_by_stock.get(stock_id) if held else None),
                position_units=(_position_units(db, positions[stock_id].id) if held else None),
            )
        )
        counts["hold" if held else "watch"] += 1

    return counts


# ---------------------------------------------------------------------------
# Orchestrator 2b（FORWARD_V1_202609 專用）：Winner 生命週期純觀察紀錄
# ---------------------------------------------------------------------------
def update_winner_tracking(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> int:
    """Part 49：任一持倉的 actual_position_return 首次 `>= +10%` 之後，只要部位還開著，
    每個交易日 UPSERT 一列到 `ShadowWinnerTracking`，記錄它後續怎麼走（現在報酬／歷史
    最高報酬／從高點回落多少）。**純觀察，決策引擎（`run_daily_trading_strategy`）完全
    不讀這張表**——這裡故意不做 `params["track_winners"]` 短路判斷，維持函式本身單純、
    好測試；要不要呼叫它是呼叫端（daily runner／replay harness）依策略版本自行決定。

    只有一次 `actual_position_return >= 10%` 之後才開始建列（見迴圈內的
    `existing_any is None: continue` 短路）；一旦開始追蹤，即使之後報酬又跌回 10% 以下
    也會繼續記錄（觀察它完整的漲多少、回落多少），不會半路停止觀察。

    回傳這次呼叫實際 UPSERT 了幾列，方便呼叫端記 log。
    """
    positions = _load_positions(db, strategy_version)
    updated = 0
    for stock_id, position in positions.items():
        avg_entry = _position_average_entry_price(db, position.id)
        today_close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        if avg_entry in (None, 0) or today_close is None:
            continue
        current_actual_return = (today_close / avg_entry - 1) * 100.0

        prior_rows = (
            db.query(ShadowWinnerTracking)
            .filter(
                ShadowWinnerTracking.strategy_version == strategy_version,
                ShadowWinnerTracking.stock_id == stock_id,
                ShadowWinnerTracking.first_seen_date == position.first_seen_date,
            )
            .order_by(ShadowWinnerTracking.trade_date.asc())
            .all()
        )

        if current_actual_return < WINNER_10_THRESHOLD_PCT and not prior_rows:
            # 從未達標過、今天也沒達標 -> 這個部位還沒進入 Winner 生命週期，不用建列
            continue

        if prior_rows:
            winner_10_first_date = prior_rows[0].winner_10_first_date
            highest_actual_return = max(prior_rows[-1].highest_actual_return, current_actual_return)
        else:
            winner_10_first_date = target_date
            highest_actual_return = current_actual_return
        drawdown_from_peak_pct = current_actual_return - highest_actual_return  # 恆 <= 0

        evidence = build_daily_evidence(
            db, stock_id=stock_id, stock_name=position.stock_name,
            first_seen_date=position.first_seen_date, target_date=target_date,
        )

        row = (
            db.query(ShadowWinnerTracking)
            .filter(
                ShadowWinnerTracking.strategy_version == strategy_version,
                ShadowWinnerTracking.stock_id == stock_id,
                ShadowWinnerTracking.first_seen_date == position.first_seen_date,
                ShadowWinnerTracking.trade_date == target_date,
            )
            .first()
        )
        if row is None:
            row = ShadowWinnerTracking(
                strategy_version=strategy_version, stock_id=stock_id,
                first_seen_date=position.first_seen_date, trade_date=target_date,
                winner_10_first_date=winner_10_first_date, current_actual_return=current_actual_return,
                highest_actual_return=highest_actual_return, drawdown_from_peak_pct=drawdown_from_peak_pct,
            )
            db.add(row)
        else:
            row.winner_10_first_date = winner_10_first_date
            row.current_actual_return = current_actual_return
            row.highest_actual_return = highest_actual_return
            row.drawdown_from_peak_pct = drawdown_from_peak_pct
        row.momentum_score = evidence.momentum_score
        row.p4_decision = evidence.p4_decision
        row.tracking_return = evidence.mark_to_market_return_pct
        updated += 1
    return updated


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
    initial_capital = STRATEGY_PARAMS_BY_VERSION[strategy_version]["initial_capital"]
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

    `strategy_version` 的 `cycle_reset_trading_days` 若是 `None`（Clean Baselines /
    FORWARD_V1_202609 皆是）——這個概念只屬於 v1_frozen 的既有生產迴圈設計，spec 對
    FORWARD_V1_202609 完全沒有提到強制重置，直接整段 no-op。
    """
    params = STRATEGY_PARAMS_BY_VERSION[strategy_version]
    cycle_length = params.get("cycle_reset_trading_days")
    if cycle_length is None:
        return False

    portfolio = _get_or_create_portfolio(db, strategy_version)

    if portfolio.cycle_start_trade_date is None:
        # 這個 strategy_version 第一次真正運作（第一天不可能滿一個完整循環）
        portfolio.cycle_start_trade_date = target_date
        return False

    days_in_cycle = _count_cycle_trading_days(
        db, strategy_version=strategy_version,
        cycle_start_trade_date=portfolio.cycle_start_trade_date, target_date=target_date,
    )
    if days_in_cycle < cycle_length:
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
    portfolio.cash = params["initial_capital"]
    portfolio.realized_pnl_cumulative = 0.0
    portfolio.cycle_number += 1
    portfolio.cycle_start_trade_date = None  # 下次呼叫的第一天會重新設定

    return True
