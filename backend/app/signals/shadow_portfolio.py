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

**2026-09-09：`v1_frozen` 直接改版為 Dual-Engine Strategy（CONTINUATION + PULLBACK_
RECOVERY），取代原本單一 setup_a/setup_b pullback 規則**——這是使用者明確要求的「直接
覆蓋 v1_frozen 本身」，不是另開 `v2_frozen`／`TEST_C` 之類的新 `strategy_version`；舊
`v1_frozen` 的歷史意義與其 Shadow Portfolio 交易紀錄視同被取代（`backfill_shadow_
portfolio_replay.py` 已移除「不可對 v1_frozen 執行 --execute」的舊保護，改為這次任務的
主要重跑工具）。

架構上刻意保留的邊界（避免波及其他 `strategy_version`）：
- `V1_STRATEGY_PARAMS`（模組層級常數）**逐字不動**——`CLEAN_FIXED_TP`／`CLEAN_NO_
  FIXED_TP`／`FORWARD_V1_202609` 三個其他策略版本仍然全部或部分 spread 自這個常數，
  改它就會連坐波及這三個完全不相關的版本。`STRATEGY_PARAMS_BY_VERSION[STRATEGY_
  VERSION_V1_FROZEN]` 改成一份**獨立**的 Dual-Engine 參數 dict（見 `DUAL_ENGINE_
  PARAMS`），不再 spread 自 `V1_STRATEGY_PARAMS`
- `generate_entry_signal`／`generate_exit_signal`／`rank_candidates`／`compute_
  entry_score`（本節上方的既有 pure functions）**逐字不動**——一樣是給 `CLEAN_FIXED_
  TP`／`CLEAN_NO_FIXED_TP`／`FORWARD_V1_202609` 用的共用框架；v1_frozen 的 Dual-Engine
  判斷邏輯是完全獨立的一組新 pure functions（見下方 Dual-Engine 專屬區塊），只有
  `run_daily_trading_strategy` 這個 orchestrator 入口依 `strategy_version ==
  STRATEGY_VERSION_V1_FROZEN` 分流呼叫 `_run_v1_dual_engine_daily_strategy`
- `execute_pending_strategy_orders`／`create_portfolio_daily_snapshot`／`check_
  and_apply_cycle_reset` 三個 orchestrator **維持通用、不分流**：T signal→T+1 執行、
  equity 計算、35 交易日循環重置，這些行為在 Dual-Engine 規格裡完全沒有改變，v1_
  frozen 繼續共用同一份程式碼
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
    _ensure_shadow_strategy_daily_decision_episode_columns(engine)


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


def _ensure_shadow_strategy_daily_decision_episode_columns(engine: Engine) -> None:
    """2026-09-09（Dual-Engine 第二輪重寫）：`shadow_strategy_daily_decisions` 這張表
    在 production 早已有資料，`create_all` 不會替既有表補欄位，需要顯式 ALTER TABLE
    （同上一個函式的既有慣例）。"""
    inspector = inspect(engine)
    if "shadow_strategy_daily_decisions" not in inspector.get_table_names():
        return
    wanted = {
        "episode_price_return_pct": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN episode_price_return_pct FLOAT",
        "episode_low_return_pct": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN episode_low_return_pct FLOAT",
        "continuation_evidence_count": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_evidence_count INTEGER",
        "continuation_evidence": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_evidence JSON",
        "continuation_eligible": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_eligible BOOLEAN",
        "continuation_rank": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_rank INTEGER",
        "continuation_skip_reason": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_skip_reason VARCHAR(64)",
        "continuation_phase": "ALTER TABLE shadow_strategy_daily_decisions ADD COLUMN continuation_phase VARCHAR(32)",
    }
    columns = {c["name"] for c in inspector.get_columns("shadow_strategy_daily_decisions")}
    missing = [name for name in wanted if name not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            conn.execute(text(wanted[name]))

# ---------------------------------------------------------------------------
# 凍結參數 —— 逐字對應 fishtail_backtest/backtest/run_all.py 的 BASELINE_PARAMS。
# 不可調參；改動這些值視同毀棄沙盒的驗證結論。
#
# **2026-09-09 起，這份常數不再是 v1_frozen 實際使用的參數**（v1_frozen 已改版為
# Dual-Engine，見 `DUAL_ENGINE_PARAMS`）——保留這個常數本身完全不動，是因為
# `CLEAN_FIXED_TP`／`CLEAN_NO_FIXED_TP`／`FORWARD_V1_202609` 三個完全不相關的策略
# 版本仍然 spread 自它做各自的 baseline 比較，不能因為 v1_frozen 改版就連坐波及。
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
# ---------------------------------------------------------------------------
# 2026-09-09（第二輪重寫）：v1_frozen Dual-Engine 專屬參數 —— **不** spread 自
# `V1_STRATEGY_PARAMS`（那份常數屬於其他三個 strategy_version，見上方註解）。
#
# 這一輪完全重寫 Continuation（Starter/Confirmation 兩段式）與 Pullback（改用真實
# episode 價格路徑，不再用 mark_to_market_return_pct）的 entry/exit 邏輯，取代
# 上一輪驗證出「大多數進場都虧、Pullback 從未觸發」的舊版規則。所有數值逐字對應
# 使用者這次提供的新規格書（PART 1~47），一次寫定、不做 grid search／反覆調參。
# ---------------------------------------------------------------------------
DUAL_ENGINE_PARAMS: Dict[str, Any] = {
    "initial_capital": 600000.0,
    "unit_capital": 100000.0,
    # PART 14：拿掉全域 5 檔上限，改由兩個資金桶各自的容量限制風險。以目前
    # 凍結配置，Continuation 是 300,000 / 100,000 = 3 檔，Pullback 是
    # 100,000 / 100,000 = 1 檔，所以 API 的資訊性上限是 4 檔；orchestrator
    # 仍不拿它做任何 `len(...) >= max_stocks` 的硬性檢查。
    "max_stocks": 4,
    "continuation_bucket_cap": 300000.0,
    # Validated 2026-09-10 report-profile allocation: half the account is the
    # aggressive continuation sleeve, one additional 100k slot remains for a
    # pullback recovery, and the rest stays in cash when no qualified setup is
    # present.
    "pullback_bucket_cap": 100000.0,
    "continuation_starter_capital": 100000.0,
    "continuation_confirm_scale_in_capital": 0.0,
    # Report-profile rotation is point-in-time only: a stronger early
    # re-acceleration may replace a weaker breakout profile when capacity is
    # full. It never uses future returns.
    "continuation_rotation": {
        "enabled": True,
        "candidate_max_rank": 10,
        "candidate_min_evidence": 6,
        "candidate_momentum_min": 70.0,
        "candidate_momentum_max": 84.0,
        "candidate_relative_strength_min": 90.0,
        "victim_max_return_pct": 0.0,
        "victim_min_holding_trading_days": 1,
        "victim_require_starter": True,
    },
    # 2026-09-10：Starter 改到 D1。D1 不再用價格漲幅篩選，而是用既有 P3/Phase 2
    # evidence families 做 deterministic quality gate；momentum 只保留寬鬆的 data-quality
    # floor，不再單獨決定是否追價。
    "continuation_starter": {
        "day_index": 1,
        "momentum_floor": 60.0,
        "momentum_ceiling": None,
        "min_evidence_count": None,
        "min_positive_evidence": 3,
        "report_profile_gate": True,
        "report_profile_direct_entry": True,
        "report_profile_rotation": True,
        "report_profile_rotation_max_victim_return_pct": 15.0,
        "report_profile_stop_pct": -12.0,
        "report_profile_trailing_activate_pct": 10.0,
        "report_profile_trailing_drawdown_pct": 12.0,
    },
    # D2 Early Confirmation：只看魚尾 episode 的價格/動能路徑，不看同日 HIGH 成交後
    # 的 actual P&L。
    "continuation_early_confirm": {
        "episode_return_min": 0.0,
        "episode_low_return_min": -5.0,
        "momentum_min": 60.0,
        "momentum_retention_min": -15.0,
    },
    # D3 Final Confirmation：D2 沒有 early confirm 的 Starter 才會走到這裡。
    "continuation_confirm": {
        "episode_return_min": 2.0,
        "momentum_min": 60.0,
        "momentum_retention_min": -5.0,
    },
    "continuation_starter_fast_fail_pct": -5.0,
    "continuation_confirmed_stop_pct": -8.0,
    "continuation_trailing_activate_pct": 10.0,
    # PART 30：從高點「收盤價」回落 >=6% 就出場（純價格，不受 ADD/平均成本改變影響）
    "continuation_trailing_drawdown_pct": 6.0,
    # 2026-09-10：Pullback Watch 改成 day2~day7 的狀態，最多保留 4 個交易日等待
    # Recovery；仍然只用 true episode price，不用 tracking MTM。
    "pullback_watch": {
        "day_index_min": 2, "day_index_max": 7,
        "episode_return_min": -15.0, "episode_return_max": -4.0, "momentum_min": 55.0,
    },
    "pullback_watch_window_trading_days": 4,
    # Recovery：相對 watch 期間最低點回升 >=3pp；momentum 只需 >=60 且不能比前一天
    # 惡化超過 2 分，不再要求單日 +2 或 +5。
    "pullback_recovery": {
        "episode_return_recovery_pp_min": 3.0,
        "momentum_level_min": 60.0,
        "momentum_recovery_min": -2.0,
    },
    "pullback_recovery_failure_window_trading_days": 4,
    "pullback_real_stop_pct": -8.0,
    # PART 45：資料品質防護 —— 相鄰有效交易日收盤變動 >=50% 視為可疑（減資/分割/
    # 資料誤置），常數集中在這裡，判斷邏輯見 `_episode_has_corporate_action_suspect`。
    "corporate_action_suspect_pct": 0.50,
    # 沿用既有 v1_frozen 生產迴圈的 35 交易日強制循環重置（spec 沒有要求拿掉這個機制；
    # 本次 8/1~9/7 回測窗口不到 35 個交易日，這個常數在這次跑不會被觸發）
    "cycle_reset_trading_days": CYCLE_LENGTH_TRADING_DAYS,
    # 純資訊性欄位，只給 `/api/signals/shadow-portfolio` 這類唯讀 API 顯示用——
    # Continuation 一檔最多 1 次 Confirmation Scale-in（Starter 50k + Confirm 50k =
    # 100k，2 lot），Pullback 沒有 ADD（PART 22：GENERIC_ADD=OFF），所以 Pullback
    # 一檔最多 1 lot。
    "max_units_per_stock": 2,
    "max_total_units": None,  # 不再有固定的全域 unit 上限，改由兩個桶各自的容量限制
    # 以下三個 flag 只有 `run_daily_trading_strategy` 的「通用」分支（非 v1_frozen 的
    # 其他策略版本）與 `run_shadow_portfolio.py`／`update_winner_tracking` 會讀取；
    # v1_frozen 走專屬的 Dual-Engine 分支，這幾個 key 純粹是防禦性保留，維持跟其他
    # 策略版本 dict 一致的形狀，避免任何呼叫端誤讀到 KeyError
    "take_profit_signal_pct": None,
    "take_profit_basis": None,
    "max_position_exposure_pct": None,
    "add_requires_profit": False,
    "granular_skip_reasons": False,
    "track_winners": False,
}

STRATEGY_PARAMS_BY_VERSION: Dict[str, Dict[str, Any]] = {
    STRATEGY_VERSION_V1_FROZEN: DUAL_ENGINE_PARAMS,
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
# 回測窗口結束的行政性結算；和 35 交易日循環重置分開，避免把兩種原因混在一起。
EXIT_REASON_PERIOD_END_SETTLEMENT = "PERIOD_END_SETTLEMENT"

ACTION_WATCH = "WATCH"
ACTION_BUY = "BUY"
ACTION_ADD = "ADD"
ACTION_HOLD = "HOLD"
ACTION_SELL = "SELL"
ACTION_SKIPPED_CAPACITY = "SKIPPED_PORTFOLIO_CAPACITY"
# PART 32：PULLBACK_WATCH != BUY——獨立的 decision action，跟通用的 ACTION_WATCH
# （純粹「今天沒訊號」）區分開，讓報告/UI 看得出「這檔正在被觀察，還沒進場」
ACTION_PULLBACK_WATCH = "PULLBACK_WATCH"
# PART 45：資料品質防護觸發時的獨立 action——當天完全不做任何 entry/exit 判斷，
# 跟一般 HOLD/WATCH 區分開，方便報告直接看出「這天是因為資料可疑被跳過」
ACTION_DATA_QUALITY_SUSPECT = "DATA_QUALITY_SUSPECT"

ORDER_STATUS_PENDING = "PENDING"
ORDER_STATUS_EXECUTED = "EXECUTED"

# ---------------------------------------------------------------------------
# v1_frozen Dual-Engine（2026-09-09 第二輪重寫）—— entry_type／exit_reason 常數
#
# PART 1：刪除上一輪的「D2 直接 100k BUY」「D3 可建立新倉」——D2 現在只開 5 萬的
# Starter（試探倉，PART 7~17），D3 只能 CONFIRM 或 FAIL 已存在的 Starter
# （PART 19~23），不能再用 D3 強度建立全新倉位。
# ---------------------------------------------------------------------------
ENGINE_CONTINUATION = "CONTINUATION"
ENGINE_PULLBACK_RECOVERY = "PULLBACK_RECOVERY"

ENTRY_TYPE_CONTINUATION_STARTER = "CONTINUATION_STARTER"
ENTRY_TYPE_CONTINUATION_CONFIRM_SCALE_IN = "CONTINUATION_CONFIRMATION_SCALE_IN"
ENTRY_TYPE_CONTINUATION_EARLY_CONFIRM = "CONTINUATION_EARLY_CONFIRM"
ENTRY_TYPE_CONTINUATION_FINAL_CONFIRM = "CONTINUATION_FINAL_CONFIRM"
ENTRY_TYPE_CONTINUATION_EARLY_REACCEL = "CONTINUATION_EARLY_REACCEL"
ENTRY_TYPE_CONTINUATION_BREAKOUT_SURGE = "CONTINUATION_BREAKOUT_SURGE"
ENTRY_TYPE_CONTINUATION_BREAKOUT_CONFIRMED = "CONTINUATION_BREAKOUT_CONFIRMED"
ENTRY_TYPE_CONTINUATION_PULLBACK_RIDE = "CONTINUATION_PULLBACK_RIDE"
ENTRY_TYPE_PULLBACK_RECOVERY = "PULLBACK_RECOVERY_ENTRY"

# 一個 position 屬於哪個 engine，看它「第一個 lot」（最早 entry_execution_date）的
# entry_type 落在哪個集合——Confirmation Scale-in 的 lot 一定跟在 Starter 之後，
# 不會被誤判成獨立的初始進場型態，但也不能拿它來判斷 engine 本身。
_CONTINUATION_ENTRY_TYPES = frozenset({
    ENTRY_TYPE_CONTINUATION_STARTER,
    ENTRY_TYPE_CONTINUATION_EARLY_REACCEL,
    ENTRY_TYPE_CONTINUATION_BREAKOUT_SURGE,
    ENTRY_TYPE_CONTINUATION_BREAKOUT_CONFIRMED,
    ENTRY_TYPE_CONTINUATION_PULLBACK_RIDE,
})
_PULLBACK_ENTRY_TYPES = frozenset({ENTRY_TYPE_PULLBACK_RECOVERY})

EXIT_REASON_CONTINUATION_STARTER_FAST_FAIL = "CONTINUATION_STARTER_FAST_FAIL"
EXIT_REASON_CONTINUATION_NOT_CONFIRMED = "CONTINUATION_NOT_CONFIRMED"
EXIT_REASON_CONTINUATION_CONFIRMED_STOP = "CONTINUATION_CONFIRMED_STOP"
EXIT_REASON_CONTINUATION_TRAILING_EXIT = "CONTINUATION_TRAILING_EXIT"
EXIT_REASON_CONTINUATION_ROTATION = "CONTINUATION_ROTATION"
EXIT_REASON_PULLBACK_RECOVERY_FAILED = "PULLBACK_RECOVERY_FAILED"
EXIT_REASON_PULLBACK_REAL_STOP = "PULLBACK_REAL_STOP"


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
    # PART 2~6（2026-09-09 第二輪重寫）：Dual-Engine 自己的「Episode Price Metrics」，
    # 跟上面的 `mark_to_market_return_pct`（給 P3/P4 evidence／reporting 用，不動）是
    # 完全獨立的兩套數字，不要混用。三者都保留在報告裡分別可見。
    #
    # `close_price`／`low_price`：target_date 當天的原始收盤/最低價（T 日已完成資料，
    # 不是 T+1），純粹為了讓 D3 Confirmation 需要的「current_close > day2_close」
    # 這類原始價格比較不用另外查詢。
    close_price: Optional[float] = None
    low_price: Optional[float] = None
    # `first_seen_close`：這個 episode（stock_id + first_seen_date）錨點——
    # `first_seen_date` 當天的收盤價，永遠是 T 日已知資料，不存在 lookahead。
    first_seen_close: Optional[float] = None
    episode_price_return_pct: Optional[float] = None
    episode_low_return_pct: Optional[float] = None
    # 2026-09-10：D1 Continuation multi-evidence snapshot。內容只來自 target_date
    # 當天的 SignalWatchHit / SignalSnapshot，不含未來價格或 outcome。
    continuation_evidence_count: int = 0
    continuation_evidence: Optional[Dict[str, Any]] = None
    continuation_eligible: bool = False
    continuation_hard_excluded: bool = False
    # PART 45：這個 episode 從 first_seen_date 到 target_date 之間，是否曾經出現
    # 「相鄰有效交易日收盤變動 >=50%」的可疑資料斷點（減資/分割/資料誤置）。一旦
    # 出現，`episode_price_return_pct`／`episode_low_return_pct` 從那天起永久為
    # None（股價尺度已經整段改變，不是只有跳動當天的數字失真）。
    corporate_action_suspect: bool = False


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
_SNAPSHOT_WATCHLIST_PAYLOAD_CACHE: Dict[date, Dict[str, dict]] = {}

_REPORT_PROFILE_PRIORITY = {
    "REPORT_EARLY_REACCEL": 0,
    "REPORT_BREAKOUT_SURGE": 1,
    "REPORT_BREAKOUT_CONFIRMED": 1,
    "REPORT_PULLBACK_RIDE": 2,
}

_REPORT_PROFILE_ENTRY_TYPES = {
    "REPORT_EARLY_REACCEL": "CONTINUATION_EARLY_REACCEL",
    "REPORT_BREAKOUT_SURGE": "CONTINUATION_BREAKOUT_SURGE",
    "REPORT_BREAKOUT_CONFIRMED": "CONTINUATION_BREAKOUT_CONFIRMED",
    "REPORT_PULLBACK_RIDE": "CONTINUATION_PULLBACK_RIDE",
}
_REPORT_PROFILE_ENTRY_TO_PROFILE = {
    entry_type: profile for profile, entry_type in _REPORT_PROFILE_ENTRY_TYPES.items()
}


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


def _snapshot_watchlist_payload(db: Session, snapshot_date_: date, stock_id: str) -> dict:
    """Return the complete point-in-time P3 item for one stock.

    The replay fallback must use the snapshot from ``target_date`` itself.  It
    must not borrow the current ``signal_watch_hits`` row, because that row can
    contain fields written after the historical decision date.
    """
    cached = _SNAPSHOT_WATCHLIST_PAYLOAD_CACHE.get(snapshot_date_)
    if cached is None:
        snap = db.query(SignalSnapshot).filter(SignalSnapshot.snapshot_date == snapshot_date_).first()
        cached = {
            str(item.get("stock")): item
            for item in (snap.watchlist or [] if snap is not None else [])
            if item.get("stock") is not None
        }
        _SNAPSHOT_WATCHLIST_PAYLOAD_CACHE[snapshot_date_] = cached
    return cached.get(stock_id) or {}


def _continuation_evidence_from_payload(
    *,
    signal_metrics: Optional[dict],
    signal_type: Optional[str],
    payload: Optional[dict],
    p4_decision: Optional[str],
) -> Tuple[Dict[str, Any], int, bool, bool]:
    """Build the D1 deterministic evidence ledger from existing P3 fields only.

    Each family contributes at most one positive point.  Missing families are
    explicitly recorded as unavailable; no synthetic value is inferred from
    price or future outcome data.
    """
    metrics = dict(signal_metrics or {})
    item = dict(payload or {})
    item_metrics = item.get("signal_metrics") or {}
    for key, value in item_metrics.items():
        metrics.setdefault(key, value)

    quality = (
        metrics.get("initial_quality_evidence")
        or metrics.get("quality_evidence")
        or item.get("initial_quality_evidence")
        or item.get("quality_evidence")
        or {}
    )
    role = (
        metrics.get("initial_phase2_role")
        or metrics.get("phase2_role")
        or item.get("initial_phase2_role")
        or item.get("phase2_role")
        or signal_type
        or item.get("type")
    )
    freshness = (
        metrics.get("initial_momentum_freshness")
        or metrics.get("momentum_freshness")
        or item.get("initial_momentum_freshness")
        or item.get("momentum_freshness")
    )
    watch_quality = (
        metrics.get("initial_watch_quality_state")
        or metrics.get("watch_quality_state")
        or item.get("initial_watch_quality_state")
        or item.get("watch_quality_state")
    )
    entry_state = (
        metrics.get("initial_entry_state")
        or metrics.get("entry_state")
        or item.get("initial_entry_state")
        or item.get("entry_state")
    )
    momentum = metrics.get("momentum_score")
    role_upper = str(role or "").upper()
    freshness_upper = str(freshness or "").upper()
    quality_upper = str(watch_quality or "").upper()
    entry_upper = str(entry_state or "").upper()

    signals_payload = item.get("signals") or {}
    leader_payload = item.get("leader_check") or {}
    momentum_payload = item.get("momentum") or {}
    report_features = {
        # 六面向報告的 point-in-time 欄位；缺資料時保留 None，不自行推導。
        "theme_fit": item.get("theme_fit"),
        "report_type": item.get("type") or signal_type,
        "decision": item.get("decision"),
        "conviction": item.get("conviction"),
        "technical_status": signals_payload.get("technical_status"),
        "entry_quality": signals_payload.get("entry_quality"),
        "sector_rotation_status": signals_payload.get("sector_rotation_status"),
        "institution_flow_momentum": signals_payload.get("institution_flow_momentum"),
        "capital_flow": signals_payload.get("capital_flow"),
        "chip_trend": signals_payload.get("chip_trend"),
        "margin_short_signal": signals_payload.get("margin_short_signal"),
        "leader_supports_theme": leader_payload.get("leader_supports_theme"),
        "leader_price_trend": leader_payload.get("leader_price_trend"),
        "return_5d": metrics.get("return_5d", momentum_payload.get("return_5d")),
        "return_20d": metrics.get("return_20d", momentum_payload.get("return_20d")),
        "return_60d": metrics.get("return_60d", momentum_payload.get("return_60d")),
        "momentum_score": metrics.get("momentum_score", momentum_payload.get("momentum_score")),
        "return_percentile_60d": momentum_payload.get("return_percentile_60d"),
        "rs_market_percentile_20d": metrics.get(
            "rs_market_percentile_20d", momentum_payload.get("rs_market_percentile_20d")
        ),
        "rs_industry_percentile_20d": metrics.get(
            "rs_industry_percentile_20d", momentum_payload.get("rs_industry_percentile_20d")
        ),
        "rs_rank_improvement_5d": metrics.get(
            "rs_rank_improvement_5d", momentum_payload.get("rs_rank_change_5d")
        ),
        "distance_to_high_20d": metrics.get(
            "distance_to_high_20d", momentum_payload.get("distance_to_high_20d_pct")
        ),
        "distance_to_ma20": metrics.get(
            "distance_to_ma20", momentum_payload.get("distance_to_ma20_pct")
        ),
        "trend_efficiency_20d": metrics.get(
            "trend_efficiency_20d", momentum_payload.get("trend_efficiency_20d")
        ),
        "momentum_phase": metrics.get("momentum_phase", momentum_payload.get("momentum_phase")),
    }

    families = {
        "role": {
            "available": role is not None,
            "positive": "LEADER" in role_upper and "LAGGARD" not in role_upper,
            "value": role,
        },
        "freshness": {
            "available": freshness is not None,
            "positive": freshness_upper in {"FRESH_STRONG", "FRESH_STABLE", "STABLE", "FRESH"},
            "value": freshness,
        },
        "watch_quality": {
            "available": watch_quality is not None,
            "positive": quality_upper == "READY",
            "value": watch_quality,
        },
        "relative_strength": {
            "available": "RELATIVE_STRENGTH" in quality,
            "positive": bool(quality.get("RELATIVE_STRENGTH")),
            "value": metrics.get("rs_market_percentile_20d"),
        },
        "institutional_flow": {
            "available": "INSTITUTION_CONFIRMATION" in quality,
            "positive": bool(quality.get("INSTITUTION_CONFIRMATION")),
            "value": metrics.get("institution_buy_to_turnover_2d"),
        },
        "price_structure": {
            "available": "PRICE_STRUCTURE" in quality or entry_state is not None,
            "positive": bool(quality.get("PRICE_STRUCTURE")) and entry_upper != "STRUCTURE_DAMAGED",
            "value": entry_state,
        },
        "momentum": {
            "available": momentum is not None,
            "positive": momentum is not None and float(momentum) >= 60.0,
            "value": momentum,
        },
    }
    available = [name for name, value in families.items() if value["available"]]
    positive = [name for name, value in families.items() if value["available"] and value["positive"]]
    hard_excluded = (
        p4_decision == "STOP_OBSERVING"
        or "LAGGARD" in role_upper
        or entry_upper in {"STRUCTURE_DAMAGED", "COMPOSITE_RISK_EXCLUDE", "LIQUIDITY_FAILURE", "REVERSAL_FAILURE"}
    )
    # If the data source exposes fewer than seven families, require all available
    # families but never permit a single momentum-only signal to become a Starter.
    required = min(3, len(available))
    eligible = (
        len(available) >= 2
        and len(positive) >= required
        and not hard_excluded
        and momentum is not None
        and float(momentum) >= 60.0
    )
    evidence = {
        "families": families,
        "available_families": available,
        "positive_families": positive,
        "continuation_evidence_count": len(positive),
        "required_positive_evidence": required,
        "role_quality": {"LEADER": 3, "CO_LEADER": 3, "SECTOR_LEADER": 3,
                          "INDEPENDENT_LEADER": 3, "EMERGING_MOMENTUM": 2,
                          "SECTOR_FOLLOWER": 1, "FOLLOWER": 1,
                          "ROTATION_LAGGARD": 0}.get(role_upper, 0),
        "hard_excluded": hard_excluded,
        "missing_families": [name for name in families if name not in available],
        "eligible": eligible,
        "report_features": report_features,
    }
    return evidence, len(positive), eligible, hard_excluded


def _report_profile(evidence: Optional[Dict[str, Any]]) -> Optional[str]:
    """Classify a point-in-time six-dimension report into an actionable profile.

    The classifier intentionally uses only fields present in the report on the
    signal date.  It is not an outcome label and never reads future prices.
    The thresholds are kept explicit so the replay can audit why a stock was
    placed in the aggressive sleeve.
    """
    f = (evidence or {}).get("report_features") or {}
    try:
        mom = float(f.get("momentum_score") or (evidence or {}).get("families", {}).get("momentum", {}).get("value") or 0)
        r5 = float(f.get("return_5d") or 0)
        r20 = float(f.get("return_20d") or 0)
        r60 = float(f.get("return_60d") or 0)
        rs = float(f.get("rs_market_percentile_20d") or 0)
        rsi = float(f.get("rs_industry_percentile_20d") or 0)
        rsup = float(f.get("rs_rank_improvement_5d") or 0)
        rsi60 = float(f.get("return_percentile_60d") or 0)
        dh = float(f.get("distance_to_high_20d") or 0)
    except (TypeError, ValueError):
        return None

    report_type = str(f.get("report_type") or "").upper()
    theme = str(f.get("theme_fit") or "").upper()
    technical = str(f.get("technical_status") or "").lower()
    quality = str(f.get("entry_quality") or "").lower()
    sector = str(f.get("sector_rotation_status") or "").lower()
    institution = str(f.get("institution_flow_momentum") or "").lower()
    decision = str(f.get("decision") or "").upper()
    leader_supported = f.get("leader_supports_theme") is True
    is_leader = "LEADER" in report_type and "LAGGARD" not in report_type

    if theme != "HIGH" or not is_leader or decision not in {"RECOMMEND", "BUY"}:
        return None

    # Fresh re-acceleration: this is the high-confidence pullback/recovery
    # profile.  The long-term percentile + fast RS improvement alternative is
    # specifically for cases like 6226, where industry percentile is lagging
    # even though the stock is emerging from a deep base.
    early_reaccel = (
        technical == "early_turn"
        and 74.0 <= mom <= 84.0
        and rs >= 94.0
        and r20 >= 15.0
        and rsup >= 100.0
        and leader_supported
        and (
            rsi >= 90.0
            or (rsi60 >= 98.0 and rsup >= 500.0 and dh <= -5.0)
        )
    )
    if early_reaccel:
        return "REPORT_EARLY_REACCEL"

    # Immediate breakout/chase: deliberately narrower than "high momentum".
    # It requires six-dimensional agreement so a single high score cannot
    # consume the aggressive sleeve by itself.
    breakout_surge = (
        technical == "breakout"
        and quality == "extended_chase"
        and 76.0 <= mom <= 84.0
        and r5 >= 10.0
        and r20 >= 25.0
        and rs >= 96.0
        and rsi >= 93.0
        and institution == "accelerating"
        and sector in {"inflow", "cooling"}
        and leader_supported
    )
    if breakout_surge:
        return "REPORT_BREAKOUT_SURGE"

    # A confirmed breakout with unusually strong report conviction is allowed
    # as a smaller-quality aggressive candidate (e.g. 2615), but does not
    # outrank a fresh re-acceleration profile.
    breakout_confirmed = (
        technical == "breakout"
        and quality == "breakout_confirmed"
        and str(f.get("conviction") or "").lower() == "high"
        and 70.0 <= mom <= 84.0
        and r20 >= 10.0
        and rs >= 93.0
        and rsi >= 95.0
        and institution == "accelerating"
        and leader_supported
    )
    if breakout_confirmed:
        return "REPORT_BREAKOUT_CONFIRMED"

    # Pullback setup is deliberately a separate, lower-priority sleeve.
    pullback_ride = (
        (quality == "pullback_setup" or technical == "distribution")
        and 68.0 <= mom <= 82.0
        and rs >= 94.0
        and r20 >= 15.0
        and dh <= -3.0
        and (sector == "inflow" or institution == "accelerating")
    )
    if pullback_ride:
        return "REPORT_PULLBACK_RIDE"
    return None


def _profile_entry_type(evidence: Optional[Dict[str, Any]], *, enabled: bool) -> str:
    if not enabled:
        return ENTRY_TYPE_CONTINUATION_STARTER
    return _REPORT_PROFILE_ENTRY_TYPES.get(_report_profile(evidence), ENTRY_TYPE_CONTINUATION_STARTER)


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


def _episode_first_seen_close(db: Session, *, stock_id: str, first_seen_date: date) -> Optional[float]:
    """PART 3：Episode Anchor —— `first_seen_date` 當天的收盤價，T 日已完成資料，
    不存在 lookahead。"""
    row = (
        db.query(DailyPrice.close_price)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == first_seen_date)
        .scalar()
    )
    return float(row) if row is not None else None


def _episode_has_corporate_action_suspect(
    db: Session, *, stock_id: str, first_seen_date: date, as_of: date, threshold_pct: float = 0.50
) -> bool:
    """PART 45：資料品質防護——掃過 `[first_seen_date, as_of]` 整段 episode，只要出現
    過一次「相鄰有效交易日收盤變動 >= threshold_pct」（減資/分割/資料誤置的典型特徵；
    真實案例：6949 沛爾生醫-創斷資料 10 個交易日後價格從 1490 跳到 81.9，變動
    -94.5%），就回 True——一旦股價尺度整段改變，不是只有跳動當天的數字失真，
    之後每一天用舊尺度的 `first_seen_close` 算 episode return 都會是錯的，所以要
    掃整段 episode（不是只看 `as_of` 這一天），從第一次出現可疑跳動那天起永久判定
    True。這是通用防護，不是寫死排除單一股票——任何股票踩到同樣模式都會被標記。
    """
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date >= first_seen_date,
            DailyPrice.trade_date <= as_of,
        )
        .order_by(DailyPrice.trade_date.asc())
        .all()
    )
    prev_close: Optional[float] = None
    for _trade_date, close_price in rows:
        if close_price is not None and prev_close not in (None, 0):
            pct_change = abs(float(close_price) / prev_close - 1.0)
            if pct_change >= threshold_pct:
                return True
        if close_price is not None:
            prev_close = float(close_price)
    return False


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
    continuation_signal_metrics: Optional[dict] = None
    continuation_signal_type: Optional[str] = None
    continuation_payload: Optional[dict] = None

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
        if hit_today is not None:
            continuation_signal_metrics = hit_today.signal_metrics or {}
            continuation_signal_type = hit_today.signal_type
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
        continuation_payload = _snapshot_watchlist_payload(db, target_date, stock_id)
        continuation_signal_metrics = continuation_payload.get("signal_metrics") or {}
        continuation_signal_type = continuation_payload.get("type")

    # SignalWatchHit is intentionally compact and, for active cohorts, often
    # does not carry the full six-dimension report.  The same-date immutable
    # SignalSnapshot is the point-in-time source for those report fields.  It
    # is safe to merge here because it is still target_date data; hit metrics
    # remain authoritative when both sources expose the same key.
    snapshot_payload = _snapshot_watchlist_payload(db, target_date, stock_id)
    if snapshot_payload:
        snapshot_metrics = snapshot_payload.get("signal_metrics") or {}
        continuation_signal_metrics = {
            **snapshot_metrics,
            **(continuation_signal_metrics or {}),
        }
        continuation_payload = snapshot_payload
        if continuation_signal_type is None:
            continuation_signal_type = snapshot_payload.get("type")

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

    (
        continuation_evidence,
        continuation_evidence_count,
        continuation_eligible,
        continuation_hard_excluded,
    ) = _continuation_evidence_from_payload(
        signal_metrics=continuation_signal_metrics,
        signal_type=continuation_signal_type,
        payload=continuation_payload,
        p4_decision=p4_decision,
    )

    # PART 2~6, 45（2026-09-09 第二輪重寫）：Dual-Engine 專屬的 Episode Price
    # Metrics——跟上面的 `mark_to_market_return_pct` 完全獨立，不影響既有計算，
    # 純粹是新增欄位（其他 strategy_version 的邏輯不會讀這幾個新欄位，零影響）。
    today_price_row = (
        db.query(DailyPrice.close_price, DailyPrice.low_price)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == target_date)
        .first()
    )
    close_price = float(today_price_row[0]) if today_price_row is not None and today_price_row[0] is not None else None
    low_price = float(today_price_row[1]) if today_price_row is not None and today_price_row[1] is not None else None

    corporate_action_suspect = _episode_has_corporate_action_suspect(
        db, stock_id=stock_id, first_seen_date=first_seen_date, as_of=target_date
    )
    first_seen_close = _episode_first_seen_close(db, stock_id=stock_id, first_seen_date=first_seen_date)

    episode_price_return_pct: Optional[float] = None
    episode_low_return_pct: Optional[float] = None
    if not corporate_action_suspect and first_seen_close not in (None, 0):
        if close_price is not None:
            episode_price_return_pct = (close_price / first_seen_close - 1.0) * 100.0
        if low_price is not None:
            episode_low_return_pct = (low_price / first_seen_close - 1.0) * 100.0

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
        close_price=close_price,
        low_price=low_price,
        first_seen_close=first_seen_close,
        episode_price_return_pct=episode_price_return_pct,
        episode_low_return_pct=episode_low_return_pct,
        corporate_action_suspect=corporate_action_suspect,
        continuation_evidence_count=continuation_evidence_count,
        continuation_evidence=continuation_evidence,
        continuation_eligible=continuation_eligible,
        continuation_hard_excluded=continuation_hard_excluded,
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
            requested_allocation = order.planned_amount or params["unit_capital"]
            allocation = min(requested_allocation, portfolio.cash)
            if allocation < requested_allocation - 1e-6:
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
# v1_frozen Dual-Engine（2026-09-09 第二輪重寫）—— pure evaluation functions
#
# 全部是無狀態的 pure function：只讀 EvidenceRow（今天／episode 內指定 day_index
# 那天／前一個交易日），不查資料庫、不使用任何未來欄位。全部改用 PART 2~6 的
# Episode Price Metrics（`episode_price_return_pct`／`episode_low_return_pct`／
# `close_price`），不再用 `mark_to_market_return_pct` 判斷買賣（那個欄位繼續保留
# 給 P3/P4 evidence／reporting 用，兩者不要混用）。
# ---------------------------------------------------------------------------
def evaluate_continuation_starter(day1_row: EvidenceRow, cfg: dict) -> bool:
    """2026-09-10 D1 Starter gate.

    D1 is a real P3 discovery and the decision is made only from the evidence
    attached to that D1 row.  There is deliberately no price-return threshold
    here; price path is reserved for D2/D3 confirmation.
    """
    if day1_row.day_index != cfg.get("day_index", 1):
        return False
    if not day1_row.p3_selected_today:
        return False
    if day1_row.continuation_hard_excluded or day1_row.corporate_action_suspect:
        return False
    if day1_row.momentum_score is None or day1_row.momentum_score < cfg["momentum_floor"]:
        return False
    momentum_ceiling = cfg.get("momentum_ceiling")
    if momentum_ceiling is not None and day1_row.momentum_score > momentum_ceiling:
        return False
    if not day1_row.continuation_eligible:
        return False
    min_evidence_count = cfg.get("min_evidence_count")
    if min_evidence_count is not None and day1_row.continuation_evidence_count < min_evidence_count:
        return False
    required = (day1_row.continuation_evidence or {}).get(
        "required_positive_evidence", cfg["min_positive_evidence"]
    )
    if day1_row.continuation_evidence_count < required:
        return False
    if cfg.get("report_profile_gate") and _report_profile(day1_row.continuation_evidence) is None:
        return False
    return True


def evaluate_continuation_early_confirmation(
    day2_row: EvidenceRow, day1_row: EvidenceRow, cfg: dict
) -> bool:
    """D2 path confirmation; actual HIGH-entry P&L is intentionally ignored."""
    if day2_row.p4_decision == "STOP_OBSERVING":
        return False
    if day2_row.episode_price_return_pct is None or day2_row.episode_price_return_pct < cfg["episode_return_min"]:
        return False
    if day2_row.episode_low_return_pct is None or day2_row.episode_low_return_pct < cfg["episode_low_return_min"]:
        return False
    if day2_row.momentum_score is None or day2_row.momentum_score < cfg["momentum_min"]:
        return False
    if day1_row.momentum_score is not None and day2_row.momentum_score - day1_row.momentum_score < cfg["momentum_retention_min"]:
        return False
    return True


def evaluate_continuation_confirmation(
    day3_row: EvidenceRow, day2_row: EvidenceRow, *, actual_position_return: Optional[float] = None, cfg: dict
) -> bool:
    """D3 Final Confirmation. ``actual_position_return`` is accepted only for
    backwards-compatible callers and is not a prerequisite anymore."""
    if day3_row.p4_decision == "STOP_OBSERVING":
        return False
    if day3_row.close_price is None or day2_row.close_price is None:
        return False
    if day3_row.close_price <= day2_row.close_price:
        return False
    if day3_row.episode_price_return_pct is None or day3_row.episode_price_return_pct < cfg["episode_return_min"]:
        return False
    if day3_row.momentum_score is None or day3_row.momentum_score < cfg["momentum_min"]:
        return False
    if day2_row.momentum_score is not None and day3_row.momentum_score is not None:
        if day3_row.momentum_score - day2_row.momentum_score < cfg["momentum_retention_min"]:
            return False
    return True


def evaluate_pullback_watch(row: EvidenceRow, cfg: dict) -> bool:
    """PART 32：Pullback Watch——改用 `episode_price_return_pct`（真實價格路徑），
    不再用 `mark_to_market_return_pct`。`PULLBACK_WATCH != BUY`，這裡只判斷「今天是
    不是一個合法的觀察日」，不代表可以進場。"""
    return (
        cfg["day_index_min"] <= row.day_index <= cfg["day_index_max"]
        and _in_range(row.episode_price_return_pct, cfg["episode_return_min"], cfg["episode_return_max"])
        and row.momentum_score is not None
        and row.momentum_score >= cfg["momentum_min"]
        and row.p4_decision != "STOP_OBSERVING"
    )


def evaluate_pullback_recovery_entry(
    today_row: EvidenceRow,
    prev_row: EvidenceRow,
    cfg: dict,
    lowest_watch_return: Optional[float] = None,
) -> bool:
    """Recovery from a live watch state.

    The price leg is measured against the lowest watch return over the prior
    watch window, while the momentum leg only requires ``today >= previous - 2``.
    """
    if today_row.p4_decision == "STOP_OBSERVING":
        return False
    if today_row.episode_price_return_pct is None or prev_row.episode_price_return_pct is None:
        return False
    if today_row.close_price is None or prev_row.close_price is None:
        return False
    if today_row.close_price <= prev_row.close_price:
        return False
    recovery_base = prev_row.episode_price_return_pct if lowest_watch_return is None else lowest_watch_return
    price_recovery = today_row.episode_price_return_pct - recovery_base
    if price_recovery < cfg["episode_return_recovery_pp_min"]:
        return False
    if prev_row.momentum_score is None or today_row.momentum_score is None:
        return False
    if today_row.momentum_score < cfg["momentum_level_min"]:
        return False
    momentum_recovery = today_row.momentum_score - prev_row.momentum_score
    if momentum_recovery < cfg["momentum_recovery_min"]:
        return False
    return True


def _previous_market_trade_date(db: Session, *, before: date) -> Optional[date]:
    """全市場（不分股票）上一個有 `daily_price` 資料的交易日——用於 Pullback Recovery
    Confirmation 需要的「前一天 evidence」，以及決定 watch window 是否仍然有效。"""
    row = (
        db.query(DailyPrice.trade_date)
        .filter(DailyPrice.trade_date < before)
        .distinct()
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )
    return row[0] if row is not None else None


def _trading_days_elapsed_since(db: Session, *, since: date, as_of: date) -> int:
    """`since`（不含）到 `as_of`（含）之間，全市場有幾個交易日——用來判斷 Pullback
    Recovery Failure（PART 37）的「前 2 個完整交易日」是否還在監控窗口內。"""
    if as_of <= since:
        return 0
    return (
        db.query(func.count(func.distinct(DailyPrice.trade_date)))
        .filter(DailyPrice.trade_date > since, DailyPrice.trade_date <= as_of)
        .scalar()
        or 0
    )


def _resolve_day_index_evidence(
    db: Session, *, stock_id: str, stock_name: str, first_seen_date: date, day_index: int
) -> Optional[EvidenceRow]:
    """重建同一個 episode 指定 `day_index` 那天的 evidence（PART 9 的 Starter route
    需要讀到「同 episode 的 Day1 snapshot」）。找不到對應交易日（episode 還沒活到
    那天）時回 `None`。"""
    d = _resolve_nth_market_trade_date(db, first_seen_date=first_seen_date, day_index=day_index)
    if d is None:
        return None
    return build_daily_evidence(
        db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date, target_date=d
    )


def _pullback_watch_active_within_window(
    db: Session,
    *,
    stock_id: str,
    stock_name: str,
    first_seen_date: date,
    target_date: date,
    watch_cfg: dict,
    window_trading_days: int,
) -> bool:
    """PART 33：Recovery Window——判斷 `target_date` 之前 `window_trading_days` 個
    交易日內，是否曾經出現過合法的 PULLBACK_WATCH（watch 還沒過期）。逐日往回檢查，
    不持久化任何 watch 狀態（跟全站既有「deterministic 訊號逐日重算」的慣例一致）。
    """
    d = target_date
    for _ in range(window_trading_days):
        prev = _previous_market_trade_date(db, before=d)
        if prev is None:
            return False
        day_evidence = build_daily_evidence(
            db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date, target_date=prev,
        )
        if evaluate_pullback_watch(day_evidence, watch_cfg):
            return True
        d = prev
    return False


def _lowest_episode_return_before(
    db: Session,
    *,
    stock_id: str,
    stock_name: str,
    first_seen_date: date,
    target_date: date,
    watch_cfg: dict,
    window_trading_days: int,
) -> Optional[float]:
    """Lowest valid watch return in the prior bounded watch window."""
    values: List[float] = []
    d = target_date
    for _ in range(window_trading_days):
        row = build_daily_evidence(
            db, stock_id=stock_id, stock_name=stock_name,
            first_seen_date=first_seen_date, target_date=d,
        )
        if evaluate_pullback_watch(row, watch_cfg) and row.episode_price_return_pct is not None:
            values.append(row.episode_price_return_pct)
        prev = _previous_market_trade_date(db, before=d)
        if prev is None:
            break
        d = prev
    return min(values) if values else None


def _lowest_episode_return_during_watch(
    db: Session,
    *,
    stock_id: str,
    stock_name: str,
    first_seen_date: date,
    signal_date: date,
    watch_cfg: dict,
    window_trading_days: int,
) -> Optional[float]:
    """PART 32/37：從 Recovery 訊號日（`signal_date`）往回走，收集「連續滿足
    PULLBACK_WATCH 條件」的那幾天（最多 `window_trading_days` 天）的
    `episode_price_return_pct`，取最低值——這是 Recovery Failure（PART 37）比較的
    基準：進場後如果又跌破這個「觀察期間曾經看到的最低點」，代表 Recovery 是假動作，
    股票重新破底。`signal_date` 是永久存在 `ShadowPositionLot.entry_signal_date`
    上的值，之後任何一天都能用同一個 `signal_date` 重新推算出同一個結果（stateless）。
    """
    values: List[float] = []
    d = signal_date
    for _ in range(window_trading_days + 1):
        day_evidence = build_daily_evidence(
            db, stock_id=stock_id, stock_name=stock_name, first_seen_date=first_seen_date, target_date=d,
        )
        if not evaluate_pullback_watch(day_evidence, watch_cfg):
            break
        if day_evidence.episode_price_return_pct is not None:
            values.append(day_evidence.episode_price_return_pct)
        prev = _previous_market_trade_date(db, before=d)
        if prev is None:
            break
        d = prev
    return min(values) if values else None


def _position_engine(lots: List[ShadowPositionLot]) -> Optional[str]:
    """一個 position 屬於 CONTINUATION 還是 PULLBACK_RECOVERY——看「最早進場」那個 lot
    （Starter，不是 Confirmation Scale-in 那個 lot）的 `entry_type`。PART 43 保證
    一個 position 一旦建立，engine 終生不變（同股票不能同時存在兩個 engine 的
    position）。"""
    if not lots:
        return None
    first_lot = min(lots, key=lambda lot: (lot.entry_execution_date, lot.id))
    if first_lot.entry_type in _CONTINUATION_ENTRY_TYPES:
        return ENGINE_CONTINUATION
    if first_lot.entry_type in _PULLBACK_ENTRY_TYPES:
        return ENGINE_PULLBACK_RECOVERY
    return None


def _dual_engine_bucket_cost(
    db: Session,
    *,
    positions: Dict[str, ShadowVirtualPosition],
    lots_by_stock: Dict[str, List[ShadowPositionLot]],
    exclude_stock_ids: set,
    engine: str,
) -> float:
    """目前（扣掉今天已判定出場的股票）某個 engine 的資金桶總成本——PART 13/35 的
    `MAX_CONTINUATION_EXPOSURE`／`MAX_PULLBACK_EXPOSURE` 都是對 lot `allocation`
    加總比較，不是 mark-to-market 市值（比照既有 `_position_cost` 的既有慣例）。

    **只計入已經真正成交的 lot**——還在排隊等成交的 PENDING 訂單不算在這裡，見
    `_dual_engine_pending_reservation()`（PART 42：兩者在呼叫端相加才是完整的桶子
    佔用）。
    """
    total = 0.0
    for stock_id, pos in positions.items():
        if stock_id in exclude_stock_ids:
            continue
        lots = lots_by_stock.get(stock_id, [])
        if _position_engine(lots) != engine:
            continue
        total += sum(lot.allocation for lot in lots)
    return total


def _dual_engine_pending_reservation(db: Session, *, strategy_version: str, engine: str) -> float:
    """PART 42：計算 Bucket exposure 時必須包含 EXECUTED open positions + PENDING
    BUY + PENDING CONFIRMATION_SCALE_IN——避免上一輪出現過的真實 bug（pending 訂單
    卡在排隊沒被算進容量檢查，等它終於成交時桶子已經超過上限）。`planned_amount`
    在訂單建立當下就已經寫入，不需要等成交才知道金額。"""
    prefixes = (
        {
            ENTRY_TYPE_CONTINUATION_STARTER,
            ENTRY_TYPE_CONTINUATION_EARLY_REACCEL,
            ENTRY_TYPE_CONTINUATION_BREAKOUT_SURGE,
            ENTRY_TYPE_CONTINUATION_BREAKOUT_CONFIRMED,
            ENTRY_TYPE_CONTINUATION_PULLBACK_RIDE,
            ENTRY_TYPE_CONTINUATION_CONFIRM_SCALE_IN,
            ENTRY_TYPE_CONTINUATION_EARLY_CONFIRM,
            ENTRY_TYPE_CONTINUATION_FINAL_CONFIRM,
        }
        if engine == ENGINE_CONTINUATION
        else {ENTRY_TYPE_PULLBACK_RECOVERY}
    )
    rows = (
        db.query(ShadowStrategyOrder)
        .filter(
            ShadowStrategyOrder.strategy_version == strategy_version,
            ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
            ShadowStrategyOrder.action.in_([ACTION_BUY, ACTION_ADD]),
            ShadowStrategyOrder.entry_pattern.in_(list(prefixes)),
        )
        .all()
    )
    return sum(o.planned_amount or 0.0 for o in rows)


def _weighted_avg_cost_asof(lots: List[ShadowPositionLot], asof: date) -> Optional[float]:
    """「以 `asof` 這天為準」的加權平均成本——只計入 `entry_execution_date <= asof`
    的 lot（Confirmation Scale-in 發生之前的日子，只看 Starter 的成本；發生之後，
    兩個 lot 一起加權）。這是判斷「歷史上哪一天第一次真正達到 Trailing 啟動門檻」
    的正確基準——『當時真的賺了多少』本來就該用『當時』的成本算，不是事後用今天
    的加權成本回頭套用（那是完全不同的問題，見 `_confirmed_trailing_check`
    docstring 對這兩個階段的區分）。"""
    relevant = [lot for lot in lots if lot.entry_execution_date <= asof]
    if not relevant:
        return None
    total_shares = sum(lot.shares for lot in relevant)
    total_cost = sum(lot.allocation for lot in relevant)
    if total_shares <= 0:
        return None
    return total_cost / total_shares


def _confirmed_trailing_check(
    db: Session,
    *,
    stock_id: str,
    lots: Optional[List[ShadowPositionLot]] = None,
    avg_cost: Optional[float] = None,
    start_date: date,
    end_date: date,
    activate_pct: float,
    drawdown_pct: float,
) -> Tuple[bool, Optional[float]]:
    """PART 28~30：Trailing 分兩個獨立階段，各自該用不同基準——

    1. **何時第一次啟動**（`actual_position_return >= activate_pct`）：正式呼叫端用
       「當天已經存在的 lot」算當天的加權平均成本（`_weighted_avg_cost_asof`，動態、
       逐日不同）。為維持既有 pure helper 測試與外部唯讀呼叫的相容性，也接受 `avg_cost`；
       傳入時會把它當成固定成本，只影響啟動判斷，不影響後面的純價格 peak/drawdown 比較。
    2. **啟動之後的比較**（`current_close <= peak_close * (1-drawdown_pct)`）：
       純粹是原始收盤價的關係，`peak_close` 是啟動日之後每天收盤價的 `max()`，
       跟後續任何成本基準改變（包含 Confirmation Scale-in）完全無關（PART 30
       明講「這個 calculation 完全不受 ADD/average cost 改變影響」——指的正是這
       第二階段的比較，不是第一階段的啟動判斷）。

    這是上一輪回測跑出來、這一輪修正的設計：第一輪直接用『今天最終的加權成本』
    套用到整段歷史（包含 Confirmation Scale-in 發生前的日子），會讓稀釋後的成本
    使歷史高點看起來比實際更低，导致某些真實達標過的啟動事件被漏判。

    回傳 `(triggered, peak_close)`。
    """
    if lots is None and avg_cost is None:
        raise ValueError("_confirmed_trailing_check requires lots or avg_cost")
    rows = (
        db.query(DailyPrice.trade_date, DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date >= start_date,
            DailyPrice.trade_date <= end_date,
        )
        .order_by(DailyPrice.trade_date.asc())
        .all()
    )
    activation_date: Optional[date] = None
    for trade_date_, close_price in rows:
        if close_price is None:
            continue
        avg_cost_asof = (
            avg_cost
            if avg_cost is not None
            else _weighted_avg_cost_asof(lots or [], trade_date_)
        )
        if avg_cost_asof in (None, 0):
            continue
        actual_return = (float(close_price) / avg_cost_asof - 1.0) * 100.0
        if actual_return >= activate_pct:
            activation_date = trade_date_
            break
    if activation_date is None:
        return False, None
    peak_close = max(float(c) for d, c in rows if d >= activation_date and c is not None)
    today_close = next((float(c) for d, c in rows if d == end_date and c is not None), None)
    if today_close is None:
        return False, peak_close
    triggered = today_close <= peak_close * (1 - drawdown_pct / 100.0)
    return triggered, peak_close


def _run_v1_dual_engine_daily_strategy(
    db: Session, *, target_date: date, strategy_version: str
) -> Dict[str, int]:
    """v1_frozen 專屬（2026-09-09 第二輪重寫）——由 `run_daily_trading_strategy` 依
    `strategy_version == STRATEGY_VERSION_V1_FROZEN` 分流呼叫。`execute_pending_
    strategy_orders`／`create_portfolio_daily_snapshot`／`check_and_apply_cycle_reset`
    三個 orchestrator 完全通用，v1_frozen 沿用不變；這個函式只取代「今天要不要
    BUY/CONFIRM/SELL」這一段判斷。

    PART 25/43 優先序（同一檔股票同一天）：
        1. 已有任一 engine 的持倉 -> 該部位所屬 engine 的出場/確認判斷（本函式最先
           處理，同股票不會同時存在兩個 engine 的 position）
        2. Continuation Starter（僅 day_index==2）-> 判定為 Continuation
        3. Pullback Recovery -> 判定為 Pullback Recovery
        4. 以上皆非 -> 無動作（可能記錄 PULLBACK_WATCH 純觀察，不代表進場）

    以下是規格書沒有逐字規定、由本次實作明確選定並在此註明的實作假設（PART 44/59
    的精神：這些是邏輯/優先序的必要選擇，不是為了衝高報酬而調整的參數）：
    - Continuation STARTER 狀態的出場優先序：Fast Fail（-3%）> Confirmation/
      Not-Confirmed（兩者互斥，用同一個 if/else 決定）。CONFIRMED 狀態的出場優先序：
      Confirmed Hard Stop（-6%）> P4_STOP > Trailing——真實停損永遠最先短路。
      Pullback 的出場優先序：Real Stop（-8%）> P4_STOP > Recovery Failure。
    - PART 19「D3 只能處理已存在 Starter」的「D3」，本次實作解讀為「Starter 執行後
      第一次被評估的那一天」，不是硬性要求 `evidence.day_index == 3`——這是為了正確
      處理 Starter 因缺 `daily_price` 而延遲到 D4/D5 才真正成交的邊界情況（沿用
      `execute_pending_strategy_orders` 既有的 self-healing 設計）：不論實際成交在
      哪一天，Starter 只會存活恰好一個評估週期（PART 23 規定 D3 沒 Confirm 就退出，
      不等 Day4/Day5），所以「這個 position 目前是 Starter 狀態（只有 1 個 lot）」
      本身就唯一決定了「今天就是它的 Confirmation/Fail 判定日」，用 lot count 判斷
      比死板比對 `day_index==3` 更穩健，且在沒有資料缺口的正常情況下行為完全一致。
    - PART 42 的桶子容量檢查，Continuation Confirmation Scale-in（既有持倉的加碼，
      不是新進場）優先於全新 Starter 進場；Continuation 新進場優先於 Pullback
      Recovery 新進場（處理順序：先出場 -> 再 Confirmation Scale-in -> 再
      Continuation Starter 新進場 -> 最後 Pullback Recovery 新進場）。
    - PART 45 資料品質防護：一旦 `evidence.corporate_action_suspect=True`，當天對
      這檔股票完全不做任何 entry/exit 判斷（不管有沒有持倉），只記錄
      `ACTION_DATA_QUALITY_SUSPECT`，等資料回到可信狀態才恢復正常評估。
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

    prev_trade_date = _previous_market_trade_date(db, before=target_date)

    lots_by_stock: Dict[str, List[ShadowPositionLot]] = {
        stock_id: db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == pos.id).all()
        for stock_id, pos in positions.items()
    }

    starter_cfg = params["continuation_starter"]
    confirm_cfg = params["continuation_confirm"]
    watch_cfg = params["pullback_watch"]
    recovery_cfg = params["pullback_recovery"]
    watch_window = params["pullback_watch_window_trading_days"]
    failure_window = params["pullback_recovery_failure_window_trading_days"]

    continuation_phase_by_stock: Dict[str, str] = {}
    continuation_skip_reason_by_stock: Dict[str, str] = {}
    continuation_rank_by_stock: Dict[str, int] = {}

    def _continuation_decision_fields(
        evidence: EvidenceRow, *, phase: Optional[str] = None,
        skip_reason: Optional[str] = None, rank: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Persist the point-in-time continuation evidence ledger."""
        return {
            "continuation_evidence_count": evidence.continuation_evidence_count,
            "continuation_evidence": evidence.continuation_evidence,
            "continuation_eligible": evidence.continuation_eligible,
            "continuation_rank": rank,
            "continuation_skip_reason": skip_reason,
            "continuation_phase": phase,
        }

    # PART 45：資料品質可疑的股票，今天完全跳過所有判斷（entry/exit 都不做）。
    data_quality_suspect_stocks = {
        sid for sid, ev in evidence_by_stock.items() if ev.corporate_action_suspect
    }

    # ---- 出場／確認判斷（持倉優先，見 PART 25 #1／43）----
    decided_exits: Dict[str, ExitSignal] = {}
    confirmations: Dict[str, EntrySignal] = {}
    actual_return_by_stock: Dict[str, Optional[float]] = {}
    engine_by_stock: Dict[str, Optional[str]] = {}
    for stock_id, evidence in evidence_by_stock.items():
        if stock_id in data_quality_suspect_stocks:
            continue
        position = positions.get(stock_id)
        if position is None:
            continue
        lots = lots_by_stock[stock_id]
        engine = _position_engine(lots)
        engine_by_stock[stock_id] = engine

        avg_entry = _position_average_entry_price(db, position.id)
        today_close = _latest_close(db, stock_id=stock_id, as_of=target_date)
        actual_position_return = (
            (today_close / avg_entry - 1.0) * 100.0
            if avg_entry not in (None, 0) and today_close is not None
            else None
        )
        actual_return_by_stock[stock_id] = actual_position_return

        if engine == ENGINE_CONTINUATION:
            first_lot = min(lots, key=lambda lot: (lot.entry_execution_date, lot.id))
            is_profile_direct = (
                first_lot.entry_type in _REPORT_PROFILE_ENTRY_TYPES.values()
                and bool(starter_cfg.get("report_profile_direct_entry"))
            )
            is_starter_state = len(lots) == 1 and not is_profile_direct

            if is_starter_state:
                continuation_phase_by_stock[stock_id] = "PROBATION"
                # P4 and official exits apply on every state, including D2.
                if evidence.p4_decision == "STOP_OBSERVING":
                    decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_P4_STOP, row=evidence)
                    continue
                if evidence.is_official_exit_signal_day:
                    decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=evidence)
                    continue

                # D2 is the HIGH execution day.  Do not use the same day's
                # actual position P&L as a confirmation or ordinary fast stop.
                if target_date == first_lot.entry_execution_date:
                    day1_evidence = build_daily_evidence(
                        db, stock_id=stock_id, stock_name=evidence.stock_name,
                        first_seen_date=evidence.first_seen_date, target_date=first_lot.entry_signal_date,
                    )
                    if evaluate_continuation_early_confirmation(
                        evidence, day1_evidence, params["continuation_early_confirm"]
                    ):
                        continuation_phase_by_stock[stock_id] = "D2_EARLY_CONFIRM"
                        confirmations[stock_id] = EntrySignal(
                            row=evidence, entry_type=ENTRY_TYPE_CONTINUATION_EARLY_CONFIRM, entry_score=0.0
                        )
                    continue

                # The first complete day after execution is the D3 checkpoint.
                if (
                    actual_position_return is not None
                    and actual_position_return <= params["continuation_starter_fast_fail_pct"]
                ):
                    decided_exits[stock_id] = ExitSignal(
                        reason=EXIT_REASON_CONTINUATION_STARTER_FAST_FAIL, row=evidence
                    )
                    continue
                day2_evidence = build_daily_evidence(
                    db, stock_id=stock_id, stock_name=evidence.stock_name,
                    first_seen_date=evidence.first_seen_date, target_date=first_lot.entry_execution_date,
                )
                # Missing prices are data gaps, not thesis failure.
                if evidence.close_price is None or day2_evidence.close_price is None:
                    continue
                if evaluate_continuation_confirmation(
                    evidence, day2_evidence, actual_position_return=None, cfg=confirm_cfg
                ):
                    continuation_phase_by_stock[stock_id] = "D3_FINAL_CONFIRM"
                    confirmations[stock_id] = EntrySignal(
                        row=evidence, entry_type=ENTRY_TYPE_CONTINUATION_FINAL_CONFIRM, entry_score=0.0
                    )
                else:
                    decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_CONTINUATION_NOT_CONFIRMED, row=evidence)
                continue

            # CONFIRMED 狀態（2 個 lot：Starter + Confirmation Scale-in）。
            # Report-profile direct entries intentionally enter this branch
            # immediately, without waiting for a second confirmation lot.
            confirmed_stop_pct = (
                starter_cfg.get("report_profile_stop_pct", params["continuation_confirmed_stop_pct"])
                if is_profile_direct else params["continuation_confirmed_stop_pct"]
            )
            if (
                actual_position_return is not None
                and actual_position_return <= confirmed_stop_pct
            ):
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_CONTINUATION_CONFIRMED_STOP, row=evidence)
                continue
            if evidence.p4_decision == "STOP_OBSERVING" and not is_profile_direct:
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_P4_STOP, row=evidence)
                continue
            if evidence.is_official_exit_signal_day and not is_profile_direct:
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=evidence)
                continue
            continuation_phase_by_stock[stock_id] = "CONFIRMED"
            triggered, _peak_close = _confirmed_trailing_check(
                db, stock_id=stock_id, lots=lots, start_date=first_lot.entry_execution_date,
                end_date=target_date,
                activate_pct=(
                    starter_cfg.get("report_profile_trailing_activate_pct", params["continuation_trailing_activate_pct"])
                    if is_profile_direct else params["continuation_trailing_activate_pct"]
                ),
                drawdown_pct=(
                    starter_cfg.get("report_profile_trailing_drawdown_pct", params["continuation_trailing_drawdown_pct"])
                    if is_profile_direct else params["continuation_trailing_drawdown_pct"]
                ),
            )
            if triggered:
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_CONTINUATION_TRAILING_EXIT, row=evidence)
                continue

        elif engine == ENGINE_PULLBACK_RECOVERY:
            # PART 38：Real Stop（-8%）最優先
            if actual_position_return is not None and actual_position_return <= params["pullback_real_stop_pct"]:
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_PULLBACK_REAL_STOP, row=evidence)
                continue
            # PART 39：P4 STOP
            if evidence.p4_decision == "STOP_OBSERVING":
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_P4_STOP, row=evidence)
                continue
            if evidence.is_official_exit_signal_day:
                decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_OFFICIAL_EXIT, row=evidence)
                continue
            # PART 37：Recovery Failure——只在進場後前 `failure_window` 個完整交易日
            # 內檢查，跌破觀察期間曾經看到的最低點就代表 Recovery 是假動作。
            first_lot = min(lots, key=lambda lot: (lot.entry_execution_date, lot.id))
            days_elapsed = _trading_days_elapsed_since(db, since=first_lot.entry_execution_date, as_of=target_date)
            if days_elapsed <= failure_window:
                lowest_during_watch = _lowest_episode_return_before(
                    db, stock_id=stock_id, stock_name=evidence.stock_name,
                    first_seen_date=evidence.first_seen_date, target_date=first_lot.entry_signal_date,
                    watch_cfg=watch_cfg, window_trading_days=watch_window,
                )
                if (
                    lowest_during_watch is not None
                    and evidence.episode_price_return_pct is not None
                    and evidence.episode_price_return_pct < lowest_during_watch
                ):
                    decided_exits[stock_id] = ExitSignal(reason=EXIT_REASON_PULLBACK_RECOVERY_FAILED, row=evidence)
                    continue
        # engine is None 理論上不會發生（已持倉的 lot 一定來自這兩種 entry_type 之一）；
        # 保守處理：不主動出場，留給下次呼叫重新判斷，避免資料異常時中斷整批決策。

    # ---- 新進場候選（PART 8~10、32~34；未持倉、未出場、非資料可疑）----
    continuation_candidates: List[EntrySignal] = []
    pullback_candidates: List[Tuple[EntrySignal, EvidenceRow]] = []
    pullback_watch_today: set = set()

    for stock_id, evidence in evidence_by_stock.items():
        if stock_id in data_quality_suspect_stocks:
            continue
        if stock_id in decided_exits or stock_id in positions:
            continue  # PART 25 #1／43：已有任一 engine 持倉，不再評估新進場

        matched_continuation = False
        if evidence.day_index == starter_cfg.get("day_index", 1):
            # D1 is the real P3 discovery day.  The pending BUY is scheduled
            # for D2 HIGH; no D2 price is consulted for Starter eligibility.
            if evaluate_continuation_starter(evidence, starter_cfg):
                continuation_candidates.append(
                    EntrySignal(
                        row=evidence,
                        entry_type=_profile_entry_type(
                            evidence.continuation_evidence,
                            enabled=bool(starter_cfg.get("report_profile_gate")),
                        ),
                        entry_score=0.0,
                    )
                )
                matched_continuation = True
            else:
                continuation_skip_reason_by_stock[stock_id] = "NOT_ELIGIBLE"

        if matched_continuation:
            continue  # PART 25 #2 已滿足；不再檢查 Pullback（範圍上兩者本來就互斥）

        if evaluate_pullback_watch(evidence, watch_cfg):
            pullback_watch_today.add(stock_id)

        if prev_trade_date is not None:
            prev_evidence = build_daily_evidence(
                db, stock_id=stock_id, stock_name=evidence.stock_name,
                first_seen_date=evidence.first_seen_date, target_date=prev_trade_date,
            )
            if evaluate_pullback_recovery_entry(
                evidence, prev_evidence, recovery_cfg
            ) and _pullback_watch_active_within_window(
                db, stock_id=stock_id, stock_name=evidence.stock_name, first_seen_date=evidence.first_seen_date,
                target_date=target_date, watch_cfg=watch_cfg, window_trading_days=watch_window,
            ):
                pullback_candidates.append(
                    (EntrySignal(row=evidence, entry_type=ENTRY_TYPE_PULLBACK_RECOVERY, entry_score=0.0), prev_evidence)
                )

    # ---- D1 deterministic ranking: evidence count, then the specified family
    # qualities, then momentum and stock_id.  No price outcome is used here. ----
    def _continuation_rank_key(sig: EntrySignal):
        row = sig.row
        evidence = row.continuation_evidence or {}
        families = evidence.get("families", {})
        role_quality = evidence.get("role_quality", 0)
        watch_quality = 1 if families.get("watch_quality", {}).get("positive") else 0
        freshness = 1 if families.get("freshness", {}).get("positive") else 0
        relative_strength = 1 if families.get("relative_strength", {}).get("positive") else 0
        institutional = 1 if families.get("institutional_flow", {}).get("positive") else 0
        momentum_rank = -(row.momentum_score or 0.0)
        return (
            -row.continuation_evidence_count,
            -role_quality,
            -watch_quality,
            -freshness,
            -relative_strength,
            -institutional,
            momentum_rank,
            row.stock_id,
        )

    continuation_candidates.sort(key=_continuation_rank_key)
    for rank, sig in enumerate(continuation_candidates, start=1):
        continuation_rank_by_stock[sig.row.stock_id] = rank

    def _pullback_rank_key(item: Tuple[EntrySignal, EvidenceRow]):
        sig, prev_row = item
        price_recovery = (sig.row.episode_price_return_pct or 0.0) - (prev_row.episode_price_return_pct or 0.0)
        momentum_recovery = (sig.row.momentum_score or 0.0) - (prev_row.momentum_score or 0.0)
        return (
            -price_recovery,
            -momentum_recovery,
            -(sig.row.episode_price_return_pct or 0.0),
            0 if sig.row.p3_selected_today else 1,
            -sig.row.hit_count_so_far,
            sig.row.stock_id,
        )

    pullback_candidates.sort(key=_pullback_rank_key)

    # ---- 容量分配：先扣今天要賣的股票釋放出的桶容量，Confirmation Scale-in 優先於
    # 全新進場，Continuation 新進場優先於 Pullback Recovery 新進場（本函式 docstring
    # 已註明這兩條是規格未明訂、本次實作選定的處理順序）----
    exiting_stock_ids = set(decided_exits.keys())
    projected_stocks = {sid for sid in positions if sid not in decided_exits}
    continuation_bucket_used = _dual_engine_bucket_cost(
        db, positions=positions, lots_by_stock=lots_by_stock, exclude_stock_ids=exiting_stock_ids,
        engine=ENGINE_CONTINUATION,
    )
    pullback_bucket_used = _dual_engine_bucket_cost(
        db, positions=positions, lots_by_stock=lots_by_stock, exclude_stock_ids=exiting_stock_ids,
        engine=ENGINE_PULLBACK_RECOVERY,
    )
    # PART 42：還在排隊等成交的 PENDING 訂單也要算進佔用——否則卡住很久才成交的
    # 訂單會讓桶子事後超過上限。
    continuation_bucket_used += _dual_engine_pending_reservation(
        db, strategy_version=strategy_version, engine=ENGINE_CONTINUATION
    )
    pullback_bucket_used += _dual_engine_pending_reservation(
        db, strategy_version=strategy_version, engine=ENGINE_PULLBACK_RECOVERY
    )

    _EPS = 1e-6
    starter_capital = params["continuation_starter_capital"]
    confirm_capital = params["continuation_confirm_scale_in_capital"]
    pullback_capital = params["unit_capital"]
    rotation_cfg = params.get("continuation_rotation") or {}
    profile_rotation_enabled = bool(starter_cfg.get("report_profile_rotation"))

    accepted_confirmations: Dict[str, EntrySignal] = {}
    skipped_confirmations: Dict[str, EntrySignal] = {}
    for stock_id, sig in confirmations.items():
        if continuation_bucket_used + confirm_capital > params["continuation_bucket_cap"] + _EPS:
            # 桶子已滿，這筆 Confirmation Scale-in 沒有空間——position 維持 STARTER
            # 狀態（不強制平倉，只是這次沒有加碼；下一個評估日會再重新判斷一次）。
            skipped_confirmations[stock_id] = sig
            continuation_skip_reason_by_stock[stock_id] = "CAPACITY"
            continue
        continuation_bucket_used += confirm_capital
        accepted_confirmations[stock_id] = sig
        continuation_skip_reason_by_stock[stock_id] = "SELECTED"

    accepted_new: Dict[str, EntrySignal] = {}
    skipped_capacity: Dict[str, EntrySignal] = {}

    def _rotation_candidate_allowed(sig: EntrySignal) -> bool:
        """Apply only point-in-time quality gates to a full-bucket rotation.

        Rotation is deliberately narrower than ordinary Starter eligibility:
        it is only allowed for a well-supported, not-yet-exhausted continuation
        candidate.  The thresholds live behind a disabled experiment switch so
        the frozen baseline remains unchanged until replay validation passes.
        """
        if not rotation_cfg.get("enabled") and not profile_rotation_enabled:
            return False
        if sig.entry_type not in _CONTINUATION_ENTRY_TYPES:
            return False
        if profile_rotation_enabled and _report_profile(sig.row.continuation_evidence) is None:
            return False
        if (continuation_rank_by_stock.get(sig.row.stock_id) or 0) > rotation_cfg.get("candidate_max_rank", 10):
            return False
        if sig.row.continuation_evidence_count < rotation_cfg.get("candidate_min_evidence", 6):
            return False
        momentum = sig.row.momentum_score or 0.0
        if momentum < rotation_cfg.get("candidate_momentum_min", 70.0):
            return False
        if momentum > rotation_cfg.get("candidate_momentum_max", 84.0):
            return False
        families = (sig.row.continuation_evidence or {}).get("families", {})
        relative_strength = families.get("relative_strength", {}).get("value")
        if relative_strength is None or relative_strength < rotation_cfg.get("candidate_relative_strength_min", 90.0):
            return False
        return True

    def _rotation_victim(candidate: EntrySignal) -> Optional[str]:
        """Return the weakest open continuation position eligible to rotate.

        In report-profile mode, only a lower-priority profile can be replaced;
        a fresh re-acceleration therefore outranks a confirmed breakout, while
        an equal-quality candidate does not cause churn.
        """
        max_return = (
            starter_cfg.get("report_profile_rotation_max_victim_return_pct", 15.0)
            if profile_rotation_enabled else rotation_cfg.get("victim_max_return_pct", -2.0)
        )
        candidate_profile = _report_profile(candidate.row.continuation_evidence)
        candidate_priority = _REPORT_PROFILE_PRIORITY.get(candidate_profile, 99)
        candidates = []
        for stock_id, position in positions.items():
            if stock_id in decided_exits or stock_id in confirmations:
                continue
            if engine_by_stock.get(stock_id) != ENGINE_CONTINUATION:
                continue
            lots = lots_by_stock.get(stock_id, [])
            first_lot = min(lots, key=lambda lot: (lot.entry_execution_date, lot.id)) if lots else None
            if profile_rotation_enabled:
                victim_profile = _REPORT_PROFILE_ENTRY_TO_PROFILE.get(
                    first_lot.entry_type if first_lot is not None else ""
                )
                if victim_profile is None or _REPORT_PROFILE_PRIORITY.get(victim_profile, 99) <= candidate_priority:
                    continue
            if rotation_cfg.get("victim_require_starter", True) and len(lots) != 1:
                continue
            if _trading_days_elapsed_since(
                db, since=lots[0].entry_execution_date, as_of=target_date
            ) < rotation_cfg.get("victim_min_holding_trading_days", 2):
                continue
            current_return = actual_return_by_stock.get(stock_id)
            if current_return is None or current_return > max_return:
                continue
            candidates.append((current_return, lots[0].entry_execution_date, stock_id))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][2]

    for sig in continuation_candidates:
        stock_id = sig.row.stock_id
        if continuation_bucket_used + starter_capital > params["continuation_bucket_cap"] + _EPS:
            if _rotation_candidate_allowed(sig):
                victim_stock_id = _rotation_victim(sig)
                if victim_stock_id is not None:
                    victim_evidence = evidence_by_stock[victim_stock_id]
                    decided_exits[victim_stock_id] = ExitSignal(
                        reason=EXIT_REASON_CONTINUATION_ROTATION,
                        row=victim_evidence,
                    )
                    continuation_skip_reason_by_stock[victim_stock_id] = "ROTATION"
                    continuation_bucket_used -= sum(
                        lot.allocation for lot in lots_by_stock.get(victim_stock_id, [])
                    )
                    projected_stocks.discard(victim_stock_id)
            if continuation_bucket_used + starter_capital > params["continuation_bucket_cap"] + _EPS:
                skipped_capacity[stock_id] = sig
                continuation_skip_reason_by_stock[stock_id] = "CAPACITY"
                continue
        accepted_new[stock_id] = sig
        continuation_skip_reason_by_stock[stock_id] = "SELECTED"
        projected_stocks.add(stock_id)
        continuation_bucket_used += starter_capital

    for sig, _prev_row in pullback_candidates:
        stock_id = sig.row.stock_id
        if pullback_bucket_used + pullback_capital > params["pullback_bucket_cap"] + _EPS:
            skipped_capacity[stock_id] = sig
            continue
        accepted_new[stock_id] = sig
        projected_stocks.add(stock_id)
        pullback_bucket_used += pullback_capital

    counts = {
        "buy": 0, "confirm": 0, "sell": 0, "hold": 0, "watch": 0, "pullback_watch": 0,
        "skipped_capacity": 0, "data_quality_suspect": 0,
    }

    # ---- 寫 SELL orders + 決策紀錄 ----
    for stock_id, exit_sig in decided_exits.items():
        evidence = exit_sig.row
        held_units = _position_units(db, positions[stock_id].id)
        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version, stock_id=stock_id, stock_name=evidence.stock_name,
                action=ACTION_SELL, signal_date=target_date,
                scheduled_execution_date=next_weekday_guess(target_date),
                status=ORDER_STATUS_PENDING, reason=exit_sig.reason, units=held_units,
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
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["sell"] += 1

    # ---- 寫 Confirmation Scale-in orders + 決策紀錄 ----
    for stock_id, sig in accepted_confirmations.items():
        evidence = sig.row
        snapshot = {
            "day_index": evidence.day_index, "hit_count_so_far": evidence.hit_count_so_far,
            "momentum_score": evidence.momentum_score, "p4_decision": evidence.p4_decision,
            "mark_to_market_return_pct": evidence.mark_to_market_return_pct,
            "episode_price_return_pct": evidence.episode_price_return_pct,
            "first_seen_date": evidence.first_seen_date.isoformat(),
            "continuation_phase": continuation_phase_by_stock.get(stock_id),
            "continuation_evidence": evidence.continuation_evidence,
        }
        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version, stock_id=stock_id, stock_name=evidence.stock_name,
                action=ACTION_ADD, signal_date=target_date,
                scheduled_execution_date=next_weekday_guess(target_date),
                status=ORDER_STATUS_PENDING, reason=sig.entry_type, entry_pattern=sig.entry_type, units=1,
                planned_amount=confirm_capital, signal_snapshot=snapshot,
            )
        )
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_ADD,
                action_reason=sig.entry_type, entry_pattern=sig.entry_type,
                p3_selected_today=evidence.p3_selected_today, hit_count=evidence.hit_count_so_far,
                momentum_score=evidence.momentum_score, mark_to_market_return_pct=evidence.mark_to_market_return_pct,
                p4_decision=evidence.p4_decision, position_units=_position_units(db, positions[stock_id].id),
                actual_position_return=actual_return_by_stock.get(stock_id),
                scheduled_execution_date=next_weekday_guess(target_date),
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["confirm"] += 1

    # ---- Confirmation 被容量擋下：紀錄但維持 Starter 狀態，不強制平倉 ----
    for stock_id, sig in skipped_confirmations.items():
        evidence = sig.row
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SKIPPED_CAPACITY,
                action_reason=f"符合 {sig.entry_type} 但 Continuation 資金桶容量不足，維持 Starter 狀態",
                entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                position_units=_position_units(db, positions[stock_id].id),
                actual_position_return=actual_return_by_stock.get(stock_id),
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["skipped_capacity"] += 1

    # ---- 寫新進場 BUY orders + 決策紀錄 ----
    for stock_id, sig in accepted_new.items():
        evidence = sig.row
        is_continuation = sig.entry_type in _CONTINUATION_ENTRY_TYPES
        planned_amount = starter_capital if is_continuation else pullback_capital
        snapshot = {
            "day_index": evidence.day_index, "hit_count_so_far": evidence.hit_count_so_far,
            "momentum_score": evidence.momentum_score, "p4_decision": evidence.p4_decision,
            "mark_to_market_return_pct": evidence.mark_to_market_return_pct,
            "episode_price_return_pct": evidence.episode_price_return_pct,
            "episode_low_return_pct": evidence.episode_low_return_pct,
            "first_seen_date": evidence.first_seen_date.isoformat(),
            "continuation_phase": continuation_phase_by_stock.get(stock_id, "D1_STARTER"),
            "continuation_evidence": evidence.continuation_evidence,
        }
        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version, stock_id=stock_id, stock_name=evidence.stock_name,
                action=ACTION_BUY, signal_date=target_date,
                scheduled_execution_date=next_weekday_guess(target_date),
                status=ORDER_STATUS_PENDING, reason=sig.entry_type, entry_pattern=sig.entry_type, units=1,
                planned_amount=planned_amount, signal_snapshot=snapshot,
            )
        )
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_BUY,
                action_reason=sig.entry_type, entry_pattern=sig.entry_type,
                p3_selected_today=evidence.p3_selected_today, hit_count=evidence.hit_count_so_far,
                momentum_score=evidence.momentum_score, mark_to_market_return_pct=evidence.mark_to_market_return_pct,
                p4_decision=evidence.p4_decision, scheduled_execution_date=next_weekday_guess(target_date),
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence,
                    phase=(continuation_phase_by_stock.get(stock_id, "D1_STARTER")
                           if is_continuation else None),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["buy"] += 1

    # ---- 容量不足（全新進場）：紀錄但不下單，PART 16 要求完整保留排序資訊，靠
    # `episode_price_return_pct`／`episode_low_return_pct`／`momentum_score`／
    # `hit_count`／`p3_selected_today` 這些既有欄位事後就能重建排序理由 ----
    for stock_id, sig in skipped_capacity.items():
        evidence = sig.row
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_SKIPPED_CAPACITY,
                action_reason=f"符合 {sig.entry_type} 但資金桶容量不足",
                entry_pattern=sig.entry_type, p3_selected_today=evidence.p3_selected_today,
                hit_count=evidence.hit_count_so_far, momentum_score=evidence.momentum_score,
                mark_to_market_return_pct=evidence.mark_to_market_return_pct, p4_decision=evidence.p4_decision,
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id, "CAPACITY"),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["skipped_capacity"] += 1

    # ---- 資料品質可疑：獨立記錄，今天完全沒有任何 entry/exit 判斷 ----
    for stock_id in data_quality_suspect_stocks:
        evidence = evidence_by_stock[stock_id]
        held = stock_id in positions
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=ACTION_DATA_QUALITY_SUSPECT,
                action_reason="PART 45：相鄰有效交易日收盤變動 >=50%，疑似減資/分割/資料誤置，今日不做任何判斷",
                p3_selected_today=evidence.p3_selected_today, hit_count=evidence.hit_count_so_far,
                momentum_score=evidence.momentum_score, mark_to_market_return_pct=evidence.mark_to_market_return_pct,
                p4_decision=evidence.p4_decision,
                position_units=(_position_units(db, positions[stock_id].id) if held else None),
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        counts["data_quality_suspect"] += 1

    # ---- HOLD／WATCH／PULLBACK_WATCH ----
    handled = (
        set(decided_exits) | set(accepted_new) | set(accepted_confirmations)
        | set(skipped_confirmations) | set(skipped_capacity) | data_quality_suspect_stocks
    )
    for stock_id, evidence in evidence_by_stock.items():
        if stock_id in handled:
            continue
        held = stock_id in positions
        if held:
            action = ACTION_HOLD
            reason = "持有中，今日未觸發出場/確認條件"
        elif stock_id in pullback_watch_today:
            action = ACTION_PULLBACK_WATCH
            reason = "符合 PULLBACK_WATCH 條件（尚未回穩確認，不進場）"
        else:
            action = ACTION_WATCH
            reason = "未持有，今日未觸發進場條件"
        db.add(
            ShadowStrategyDailyDecision(
                strategy_version=strategy_version, trade_date=target_date,
                stock_id=stock_id, stock_name=evidence.stock_name, action=action, action_reason=reason,
                p3_selected_today=evidence.p3_selected_today, hit_count=evidence.hit_count_so_far,
                momentum_score=evidence.momentum_score, mark_to_market_return_pct=evidence.mark_to_market_return_pct,
                p4_decision=evidence.p4_decision,
                actual_position_return=(actual_return_by_stock.get(stock_id) if held else None),
                position_units=(_position_units(db, positions[stock_id].id) if held else None),
                episode_price_return_pct=evidence.episode_price_return_pct,
                episode_low_return_pct=evidence.episode_low_return_pct,
                **_continuation_decision_fields(
                    evidence, phase=continuation_phase_by_stock.get(stock_id),
                    skip_reason=continuation_skip_reason_by_stock.get(stock_id),
                    rank=continuation_rank_by_stock.get(stock_id),
                ),
            )
        )
        if held:
            counts["hold"] += 1
        elif action == ACTION_PULLBACK_WATCH:
            counts["pullback_watch"] += 1
        else:
            counts["watch"] += 1

    return counts


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

    **2026-09-09**：`strategy_version == STRATEGY_VERSION_V1_FROZEN` 分流到專屬的
    `_run_v1_dual_engine_daily_strategy`（Dual-Engine：CONTINUATION + PULLBACK_
    RECOVERY）；`CLEAN_FIXED_TP`／`CLEAN_NO_FIXED_TP`／`FORWARD_V1_202609` 完全不受
    影響，繼續走下面原本的通用邏輯。
    """
    if strategy_version == STRATEGY_VERSION_V1_FROZEN:
        return _run_v1_dual_engine_daily_strategy(db, target_date=target_date, strategy_version=strategy_version)

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


def settle_shadow_portfolio_at_period_end(
    db: Session, *, target_date: date, strategy_version: str = STRATEGY_VERSION
) -> int:
    """在指定回放終點平倉，並把下一循環本金重設為 initial_capital。

    這是歷史回放用的行政性結算，不是每日策略出場規則：
    - 以 target_date 可用的最低價建立 SELL/ShadowCompletedTrade（回放窗口最後一天
      沒有下一個交易日可執行，因此把 target_date 視為期末行政結算日）；
    - 保留結算前的 daily snapshot，讓期間報酬不被「重設本金」抹掉；
    - 清空目前持倉與 pending order，讓下一循環從固定本金開始。

    生產每日流程不會呼叫這個函式；只有 backfill replay 明確帶
    ``--settle-at-end`` 時才會使用。
    """
    params = STRATEGY_PARAMS_BY_VERSION[strategy_version]
    portfolio = _get_or_create_portfolio(db, strategy_version)
    positions = _load_positions(db, strategy_version)
    settled = 0

    for stock_id, position in positions.items():
        price_row = (
            db.query(DailyPrice.low_price)
            .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == target_date)
            .first()
        )
        lots = db.query(ShadowPositionLot).filter(ShadowPositionLot.position_id == position.id).all()
        total_allocation = sum(float(lot.allocation) for lot in lots)
        total_units = len(lots)
        exit_price = float(price_row[0]) if price_row is not None and price_row[0] is not None else None
        if exit_price is None:
            # 跟一般 cycle reset 相同：資料缺口時採 entry price 的保守 fallback。
            exit_price = float(lots[0].entry_price) if lots else 0.0

        db.add(
            ShadowStrategyOrder(
                strategy_version=strategy_version,
                stock_id=stock_id,
                stock_name=position.stock_name,
                action=ACTION_SELL,
                signal_date=target_date,
                scheduled_execution_date=target_date,
                status=ORDER_STATUS_EXECUTED,
                reason=EXIT_REASON_PERIOD_END_SETTLEMENT,
                units=total_units,
                planned_amount=total_allocation,
                execution_price=exit_price,
                executed_at=datetime.utcnow(),
            )
        )
        for lot in lots:
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
                exit_reason=EXIT_REASON_PERIOD_END_SETTLEMENT,
                exit_signal_date=target_date,
                exit_execution_date=target_date,
                exit_price=exit_price,
            )
            db.delete(lot)
            settled += 1
        db.delete(position)

    db.query(ShadowStrategyOrder).filter(
        ShadowStrategyOrder.strategy_version == strategy_version,
        ShadowStrategyOrder.status == ORDER_STATUS_PENDING,
    ).update({"status": "CANCELLED"}, synchronize_session=False)

    portfolio.cash = params["initial_capital"]
    portfolio.realized_pnl_cumulative = 0.0
    portfolio.cycle_number += 1
    portfolio.cycle_start_trade_date = None
    return settled
