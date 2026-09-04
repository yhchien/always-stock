"""P4 daily observation lifecycle.

P3 answers which candidates deserve a formal recommendation today.  This module
answers a separate question for every active recommendation episode: whether the
original thesis still deserves continued observation.  The LLM may only revalidate
external facts; ``decide_observation_action`` is the single lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    DailyPrice,
    SignalObservation,
    SignalObservationArchive,
    SignalObservationReview,
    SignalSnapshot,
    SignalWatchHit,
)
from app.signals import (
    archive,
    candidate_pool,
    deterministic_signals,
    filters,
    llm_caller,
    market_breadth,
    market_regime,
    market_snapshot,
    market_stress,
    momentum,
    prompt_family,
)
from app.signals.phase2 import entry_state
from app.signals.phase2 import momentum_freshness
from app.signals.phase2 import regime_gate
from app.signals.phase2 import tracking_state
from app.signals.phase2 import watch_quality


TRACKING_PROMPT_VERSION = "v7_tracking"
# M27 Market Regime v2 Production Integration（2026-09-04）：P4 state machine
# 正式讀 Market Environment（Market Context Overlay，見 decide_observation_
# action() 末段），行為與 p4_state_v1 不同，需要獨立版本識別；歷史 Review 維持
# 舊 version，不回填（見規格書 §21/§35）。
STATE_MACHINE_VERSION = "p4_state_v2_market_context"
DEFAULT_TRACKING_BATCH_SIZE = 12
DEFAULT_TRACKING_MODEL = os.getenv(
    "OPENAI_SIGNALS_TRACKING_MODEL",
    llm_caller.DEFAULT_RESEARCH_MODEL,
).strip()

STATUS_OBSERVING = "OBSERVING"
STATUS_CAUTION = "CAUTION"
STATUS_STOPPED = "STOPPED"

# 2026-08-12：使用者要求 STOP 判定後不再等多日複核確認，第一次 STOP 隔天（使用者
# 實際看到網站的時間點）就要從追蹤中移除——原本 =3（連續 3 個複核日皆判 STOP 才真的
# 歸檔/結算，任何一天回到 CONTINUE/CAUTION 都會取消並重新啟用觀察）改成 =1，讓第一次
# STOP 當下就滿足 `stop_confirm_count >= STOP_CONFIRM_THRESHOLD`，立即歸檔
# （`_finalize_observation_archive`）並結算魚尾追蹤週期（`archive.settle_stock_for_
# p4_stop`）。**這是刻意放棄「STOP 可能是誤判、留幾天觀察會不會打臉」的緩衝空間**，
# 換取「警戒/停止一旦發生，隔天馬上從畫面上消失」的即時性；`was_already_stopped`
# 分支（多日複核疊加 stop_confirm_count）在預設 threshold=1 下已不會被觸發（STOPPED
# 觀察一旦 confirm_count 達標就被查詢條件排除，不會再被複核），保留該分支只是防禦性
# 寫法，不是死碼——未來若把這個常數臨時調高，該分支會立刻恢復作用。
STOP_CONFIRM_THRESHOLD = 1

DECISION_CONTINUE = "CONTINUE"
DECISION_CAUTION = "CAUTION"
DECISION_STOP = "STOP_OBSERVING"
DECISION_FAILED = "REVIEW_FAILED"

CORE_DIMENSIONS = {
    "MOMENTUM_STRUCTURE",
    "PARTICIPATION",
    "CATALYST_THESIS",
}
# 2026-08-18：假突破防誤殺（P4 Observation Lifecycle v2）——COMPOSITE_RISK_EXCLUDE
# 從這個集合移除。原因：candidate selection（今天要不要「新」選這檔股票）跟
# observation lifecycle（「既有」這輪觀察是否正式失效）是兩個不同問題，過去共用同一條
# `distribution + institution_flow_reversal` 判斷，會把單日高檔換手/洗盤/獲利了結
# 誤判成論點失效並立即終止觀察。`regime_gate.build_hard_exclusion_result()` 本身
# （candidate selection 用）完全不受影響，COMPOSITE_RISK_EXCLUDE 仍是它的合法 hard
# exclusion 理由——這裡只是 observation lifecycle 不再把它當「立即」處理，改走
# STOP_CONFIRMATION_POLICY 的 CONFIRM_REQUIRED 分支（見 §COMPOSITE_RISK_EXCLUDE
# pending 狀態機）。
IMMEDIATE_HARD_REASONS = {
    "MANUAL_BLACKLIST",
    "FAILED_FOLLOW_THROUGH_CURRENT_EPISODE",
    "STRUCTURE_DAMAGED",
    "LIQUIDITY_FAILURE",
    "REVERSAL_FAILURE",
}
EXTERNAL_INVALIDATION_REASONS = {
    "BUSINESS_MISMATCH",
    "THEME_MISMATCH",
    "FALSE_SUPPLY_CHAIN_LINK",
    "MATERIAL_NEGATIVE_EVENT",
    "DATA_CONTRADICTION",
}
MATERIAL_EVIDENCE_REASONS = {
    "MATERIAL_NEGATIVE_EVENT",
    "DATA_CONTRADICTION",
}

# observation lifecycle 專屬 severity 分級——只記錄這裡實際用得到的兩級
# （IMMEDIATE 沿用既有 `IMMEDIATE_HARD_REASONS` 判斷；CONFIRM_REQUIRED 目前只有
# COMPOSITE_RISK_EXCLUDE 一種，用獨立的 pending 狀態機處理，不走 STOP_CONFIRM_
# THRESHOLD 那條路）。刻意不做成會影響判斷分支的查表——目前只有一種
# CONFIRM_REQUIRED 理由，用查表反而增加一層不必要的間接。這個 dict 只作為文件與
# 未來擴充第二種 CONFIRM_REQUIRED 理由時的錨點。
STOP_CONFIRMATION_POLICY = {
    "MANUAL_BLACKLIST": "IMMEDIATE",
    "FAILED_FOLLOW_THROUGH_CURRENT_EPISODE": "IMMEDIATE",
    "STRUCTURE_DAMAGED": "IMMEDIATE",
    "LIQUIDITY_FAILURE": "IMMEDIATE",
    "REVERSAL_FAILURE": "IMMEDIATE",
    "COMPOSITE_RISK_EXCLUDE": "CONFIRM_REQUIRED",
}

# COMPOSITE_RISK_EXCLUDE pending 狀態機常數
COMPOSITE_RISK_REASON = "COMPOSITE_RISK_EXCLUDE"
PENDING_STOP_STATUS_ACTIVE = "ACTIVE"
# pending 建立後最多容忍幾次「既未恢復也未確認」的複核（第 3 次遇到同樣情況時過期
# 清除，回到一般 lifecycle，不強制 STOP——「沒有證明恢復」不等於「證明失效」）。
COMPOSITE_PENDING_MAX_REVIEWS = 2

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "tracking-review-v1.md"
)
_PROMPT_CACHE_KEY = "signals:p4:tracking-review:v1"


def current_tracking_prompt_version(family: Optional[str] = None) -> str:
    return prompt_family.stage_version("tracking", family)


def _skip_llm_research(hard: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
    """2026-08-18：LLM tracking-review research 只在「今天已確定立即失效」時才跳過
    （省成本，反正這輪觀察當天就會被 STOP，查證外部事實已經沒有意義）。
    COMPOSITE_RISK_EXCLUDE 不在 IMMEDIATE_HARD_REASONS 裡（見常數定義），所以進入
    composite pending 狀態機的觀察，這裡不會跳過——它可能還要繼續觀察好幾天，LLM
    外部查證跟一般觀察一樣照常需要。"""
    hard_reason = str(hard.get("reason") or "").upper()
    return (
        bool(hard.get("excluded")) and hard_reason in IMMEDIATE_HARD_REASONS
    ) or evidence.get("tracking_state") == tracking_state.TRACKING_INVALIDATED


@dataclass(frozen=True)
class ObservationDecision:
    decision: str
    reason_codes: List[str]
    reason: str
    caution_dimensions: List[str]
    failed_dimensions: List[str]
    technical_status: Optional[str] = None
    # 2026-08-18：COMPOSITE_RISK_EXCLUDE pending 狀態機——告訴呼叫端要怎麼更新
    # SignalObservation.pending_stop_* 欄位。None＝不動（如 REVIEW_FAILED）；
    # "SET"＝建立全新 pending（需同時給 pending_stop_reason／trigger_snapshot）；
    # "KEEP"＝pending 持續中，呼叫端把 review_count +1，其餘欄位不變；
    # "CLEAR"＝清空 pending（不論是因為恢復、確認轉 STOP、過期，或走了跟 composite
    # 無關的立即失效路徑，都一併清乾淨，避免殘留舊 pending 狀態）。
    pending_stop_update: Optional[str] = None
    pending_stop_reason: Optional[str] = None
    pending_stop_trigger_snapshot: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "reason": self.reason,
            "caution_dimensions": list(self.caution_dimensions),
            "failed_dimensions": list(self.failed_dimensions),
            "technical_status": self.technical_status,
        }


def sync_recommendations(
    db: Session,
    *,
    signal_date: date,
    watchlist: Sequence[Dict[str, Any]],
    prompt_versions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Idempotently create P4 episodes for new P3 RECOMMEND rows.

    Existing active episodes are never duplicated or reset.  A stopped (or
    never-observed) stock always opens a fresh episode the moment P3
    recommends it again — there is no minimum-gap cooldown.  (2026-08-11:
    the previous five-unhit-trading-day gap rule was removed.  It used the
    archive's `signal_watch_hits` continuity, not P4's own timeline, to
    decide whether a restart was "genuine".  For a stock P3 keeps
    recommending without a break — e.g. one of the 68 legacy-baseline
    observations stopped by `stop_legacy_incomplete_observations.py` on
    2026-08-10 — the archive hit history never has a gap, so the gap check
    perpetually deferred the restart and the stale STOPPED badge never
    cleared even though P3 was actively recommending the stock again.  P3's
    daily RECOMMEND decision is the authority on "does this stock deserve
    attention today"; P4 should not second-guess that with its own
    cooldown.)
    """

    items = [
        item
        for item in watchlist
        if str(item.get("decision") or item.get("selection_status") or "").upper()
        == "RECOMMEND"
    ]
    if not items:
        return {"created": [], "continued": []}

    stock_ids = sorted(
        {
            str(item.get("stock") or item.get("stock_id") or "")
            for item in items
            if item.get("stock") or item.get("stock_id")
        }
    )
    rows = (
        db.query(SignalObservation)
        .filter(SignalObservation.stock_id.in_(stock_ids))
        .order_by(
            SignalObservation.stock_id.asc(),
            SignalObservation.started_signal_date.desc(),
        )
        .all()
    )
    by_stock: Dict[str, List[SignalObservation]] = {}
    for row in rows:
        by_stock.setdefault(row.stock_id, []).append(row)

    result: Dict[str, List[str]] = {
        "created": [],
        "continued": [],
        "revived": [],
    }
    for item in items:
        sid = str(item.get("stock") or item.get("stock_id") or "")
        prior = by_stock.get(sid, [])
        active = next(
            (
                row
                for row in prior
                if row.status in {STATUS_OBSERVING, STATUS_CAUTION}
            ),
            None,
        )
        if active is not None:
            result["continued"].append(sid)
            continue

        revived = _revive_if_stopped_yesterday_and_reselected_today(
            db, prior, stock_id=sid, signal_date=signal_date
        )
        if revived is not None:
            result["revived"].append(sid)
            continue

        initial_snapshot, baseline_quality = _initial_snapshot_from_recommendation(
            item,
            signal_date=signal_date,
            prompt_versions=prompt_versions or {},
        )
        observation = SignalObservation(
            stock_id=sid,
            stock_name=str(item.get("name") or sid),
            asset_type=str(item.get("asset_type") or "COMMON_STOCK").upper(),
            episode_id=str(uuid.uuid4()),
            status=STATUS_OBSERVING,
            started_signal_date=signal_date,
            baseline_quality=baseline_quality,
            initial_snapshot_json=initial_snapshot,
            latest_snapshot_json=initial_snapshot,
            selection_version=item.get("selection_version"),
            consecutive_caution_count=0,
            updated_at=datetime.utcnow(),
        )
        db.add(observation)
        by_stock.setdefault(sid, []).insert(0, observation)
        result["created"].append(sid)

    db.flush()
    return result


def _revive_if_stopped_yesterday_and_reselected_today(
    db: Session,
    prior: List[SignalObservation],
    *,
    stock_id: str,
    signal_date: date,
) -> Optional[SignalObservation]:
    """2026-08-24：若一檔股票在「上一個交易日」才剛被判定停止觀察，今天又被 P3
    重新選中，直接復活同一輪觀察（回到 OBSERVING、刪掉那筆停止封存紀錄），不建立
    新一輪、也不會在追蹤中顯示過「已停止觀察」——使用者要求：這種情況視為從未真正
    離開過，不要讓卡片經歷一次多餘的「停止→隔天又重新開始」畫面轉換，讓使用者誤以
    為兩者是不相干的兩輪。

    嚴格要求「archived_date 剛好是 signal_date 的上一個交易日」（真正意義上的
    「隔天立刻」），間隔超過一個交易日一律視為全新一輪、不復活，交給既有的延後
    結算機制（`_settle_pending_p4_fishtail_stops`）把舊資料正常結算進紀錄區——這是
    刻意的邊界：`test_stopped_stock_can_restart_after_existing_five_day_gap` 驗證
    過「間隔數個交易日的重新命中」應該視為全新一輪，這個新機制不能誤觸發那個既有
    情境。
    """
    if not prior or prior[0].status != STATUS_STOPPED:
        return None
    candidate = prior[0]
    archive_row = (
        db.query(SignalObservationArchive)
        .filter(SignalObservationArchive.observation_id == candidate.id)
        .order_by(SignalObservationArchive.archived_date.desc())
        .first()
    )
    if archive_row is None:
        return None
    prior_trade_date = (
        db.query(func.max(DailyPrice.trade_date))
        .filter(DailyPrice.trade_date < signal_date)
        .scalar()
    )
    if prior_trade_date is None or archive_row.archived_date != prior_trade_date:
        return None
    has_active_fishtail = (
        db.query(SignalWatchHit.stock_id)
        .filter(SignalWatchHit.stock_id == stock_id)
        .first()
        is not None
    )
    if not has_active_fishtail:
        return None

    candidate.status = STATUS_OBSERVING
    candidate.stopped_at = None
    candidate.stop_reason_code = None
    candidate.stop_reason = None
    candidate.stop_confirm_count = 0
    candidate.consecutive_caution_count = 0
    candidate.pending_stop_status = None
    candidate.pending_stop_reason = None
    candidate.pending_stop_since = None
    candidate.pending_stop_trigger_snapshot = None
    candidate.pending_stop_review_count = 0
    candidate.updated_at = datetime.utcnow()
    db.delete(archive_row)
    return candidate


def bootstrap_legacy_observations(db: Session) -> int:
    """Create a non-destructive baseline for active legacy hit cycles.

    Only stocks that have never had a P4 observation are bootstrapped.  The earliest
    hit is the sole point-in-time source; later data is never used as the initial
    thesis.
    """

    known_stock_ids = {
        row[0] for row in db.query(SignalObservation.stock_id).distinct().all()
    }
    rows = (
        db.query(SignalWatchHit)
        .order_by(
            SignalWatchHit.stock_id.asc(),
            SignalWatchHit.snapshot_date.asc(),
        )
        .all()
    )
    earliest: Dict[str, SignalWatchHit] = {}
    for row in rows:
        if row.stock_id not in known_stock_ids:
            earliest.setdefault(row.stock_id, row)

    for sid, hit in earliest.items():
        metrics = dict(hit.signal_metrics or {})
        recommendation_date = _parse_iso_date(
            metrics.get("initial_recommendation_date")
        ) or hit.snapshot_date
        missing_fields = [
            key
            for key in (
                "initial_recommendation_thesis",
                "initial_relative_advantage",
                "initial_instrument_validation",
                "initial_theme_validation",
                "initial_catalyst_summary",
            )
            if not metrics.get(key)
        ]
        baseline_quality = (
            "P3_COMPLETE" if not missing_fields else "LEGACY_INCOMPLETE"
        )
        initial_snapshot = {
            "recommendation_date": recommendation_date.isoformat(),
            "recommendation_rank": metrics.get("initial_recommendation_rank"),
            "backend_priority_rank": metrics.get("initial_backend_priority_rank"),
            "recommendation_thesis": metrics.get(
                "initial_recommendation_thesis"
            )
            or hit.reason,
            "relative_advantage": metrics.get("initial_relative_advantage"),
            "instrument_validation": metrics.get(
                "initial_instrument_validation"
            ),
            "theme_validation": metrics.get("initial_theme_validation"),
            "theme_cluster": metrics.get("initial_theme_cluster")
            or (hit.theme or {}).get("main_theme"),
            "catalyst_summary": metrics.get("initial_catalyst_summary"),
            "research_confidence": metrics.get("initial_research_confidence"),
            "initial_role": metrics.get("initial_phase2_role"),
            "initial_entry_state": metrics.get("initial_entry_state"),
            "initial_freshness": metrics.get("initial_momentum_freshness"),
            "initial_watch_quality_state": metrics.get(
                "initial_watch_quality_state"
            ),
            "initial_quality_evidence": metrics.get("initial_quality_evidence")
            or {},
            "selection_version": metrics.get("selection_version"),
            "prompt_versions": metrics.get("initial_prompt_versions")
            or {"research_prompt_version": hit.prompt_version or "v1"},
            "momentum_score_version": metrics.get("momentum_score_version"),
            "baseline_quality": baseline_quality,
            "missing_fields": missing_fields,
        }
        db.add(
            SignalObservation(
                stock_id=sid,
                stock_name=hit.stock_name,
                asset_type=str(metrics.get("asset_type") or "COMMON_STOCK"),
                episode_id=str(uuid.uuid4()),
                status=STATUS_OBSERVING,
                started_signal_date=recommendation_date,
                baseline_quality=baseline_quality,
                initial_snapshot_json=initial_snapshot,
                latest_snapshot_json=initial_snapshot,
                selection_version=metrics.get("selection_version"),
                consecutive_caution_count=0,
                updated_at=datetime.utcnow(),
            )
        )
    db.flush()
    return len(earliest)


def build_current_tracking_evidence(
    db: Session,
    *,
    observations: Sequence[SignalObservation],
    review_date: date,
    market_context: Optional[Dict[str, Any]] = None,
    ingestion: Optional[Dict[str, Any]] = None,
    momentum_frame: Optional[Dict[str, Dict[str, Any]]] = None,
    current_candidates: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """Batch-build deterministic P4 evidence for every supplied observation."""

    if not observations:
        return {}
    ingestion = ingestion or candidate_pool.ingest_data(db, review_date)
    masters = ingestion.get("stocks_master") or {}
    if momentum_frame is None:
        momentum_frame = momentum.compute_market_momentum_frame(
            db,
            review_date,
            masters,
        )

    stock_ids = sorted({observation.stock_id for observation in observations})
    pool_metrics = candidate_pool._compute_pool_metrics(db, ingestion, stock_ids)
    tracking_by_stock = candidate_pool._load_tracking_status(
        db,
        stock_ids,
        review_date,
    )
    industry_flow = candidate_pool._load_industry_flow_totals(db, ingestion)
    asset_types = candidate_pool._load_asset_types(db, stock_ids, masters)
    # 2026-08-19：跟 candidate_pool.build_candidate_pool() 同步——已追蹤股票的
    # 「industry」欄位也優先讀人工校正過的 canonical 分類，避免 P4 複核／外部查證
    # 看到跟 P3 選股階段不一致的錯誤產業標籤。
    canonical_labels = candidate_pool._load_canonical_industry_labels(db, stock_ids)
    current_by_id = {
        str(item.get("stock_id") or item.get("stock") or ""): item
        for item in (current_candidates or [])
    }
    returns_by_observation = _load_episode_returns(
        db,
        observations=observations,
        review_date=review_date,
    )
    latest_metrics = _load_latest_hit_metrics(db, stock_ids, review_date)
    taiex_return = (
        ((market_context or {}).get("market_regime_metrics") or {}).get(
            "return_1d_pct"
        )
    )
    if taiex_return is None:
        taiex_return = (market_context or {}).get("taiex_change_pct")

    result: Dict[int, Dict[str, Any]] = {}
    for observation in observations:
        sid = observation.stock_id
        master = masters.get(sid)
        metrics = pool_metrics.get(sid) or candidate_pool._empty_metrics()
        tracking = (
            tracking_by_stock.get(sid)
            or {
                **candidate_pool._empty_tracking_status(),
                "is_tracked": True,
                "first_seen_date": observation.started_signal_date,
            }
        )
        raw_frame = momentum_frame.get(sid)
        frame = raw_frame or momentum.empty_momentum_features()
        current_candidate = current_by_id.get(sid) or {}
        canonical = canonical_labels.get(sid)
        raw_industry_name = (
            master.industry_name
            if master is not None
            else current_candidate.get("industry")
        )
        raw_sub_industry = (
            master.sub_industry
            if master is not None
            else current_candidate.get("sub_industry")
        )
        candidate = {
            "stock_id": sid,
            "name": observation.stock_name,
            "industry": canonical[0] if canonical else raw_industry_name,
            "sub_industry": (canonical[1] or raw_sub_industry) if canonical else raw_sub_industry,
            "asset_type": asset_types.get(sid)
            or observation.asset_type
            or "COMMON_STOCK",
            "candidate_sources": list(
                current_candidate.get("candidate_sources") or []
            ),
            "is_tracked": True,
            **metrics,
            **{key: value for key, value in frame.items() if not key.startswith("_")},
            **tracking,
            **(
                industry_flow.get(
                    candidate_pool._normalized_industry(raw_industry_name)
                )
                or {"industry_flow_1d": None, "industry_flow_3d": None}
            ),
        }
        candidate.update(momentum.compute_momentum_score(candidate))
        candidate["momentum_signals"] = momentum.build_momentum_signals(candidate)
        candidate["momentum_grade"] = candidate["momentum_signals"].get(
            "momentum_grade"
        )
        candidate["momentum_phase"] = candidate["momentum_signals"].get(
            "momentum_phase"
        )
        candidate["soft_hints"] = filters._detect_soft_hints(candidate)
        candidate["deterministic_signals"] = (
            deterministic_signals.build_deterministic_signals(candidate)
        )
        entry = entry_state.compute_entry_state(candidate)
        candidate.update(entry)
        candidate["tracking_state"] = tracking_state.compute_tracking_state(candidate)
        fresh = momentum_freshness.compute_momentum_freshness(
            candidate,
            taiex_return_1d_pct=taiex_return,
        )
        candidate["momentum_freshness"] = fresh["momentum_freshness"]
        candidate["momentum_freshness_detail"] = fresh
        quality = watch_quality.compute_watch_quality(candidate, fresh)
        candidate["watch_quality_state"] = quality["watch_quality_state"]
        candidate["quality_evidence"] = quality["quality_evidence"]
        candidate["quality_reasons"] = quality["quality_reasons"]
        hard = regime_gate.build_hard_exclusion_result(
            candidate,
            taiex_return_1d_pct=taiex_return,
        )
        # 2026-08-18：`build_hard_exclusion_result` 依優先序找到第一個成立的理由就
        # return（COMPOSITE_RISK_EXCLUDE 排在 REVERSAL_FAILURE 之前）——若某天兩者
        # 剛好同時成立，只看 `hard["reason"]` 只會看到 COMPOSITE_RISK_EXCLUDE，較嚴格
        # 的 REVERSAL_FAILURE 會被短路蓋掉。這裡獨立再呼叫一次 `_is_reversal_failure`
        # （純函式、不影響 candidate selection 那條路徑的既有短路行為），讓 observation
        # lifecycle 能判斷「今天是不是同時也符合更嚴格的真失敗證據」，composite risk
        # pending 機制才不會反而繞過 REVERSAL_FAILURE 的 materiality 標準（P4 v2 spec
        # §7）。
        reversal_check = regime_gate._is_reversal_failure(candidate, taiex_return)
        current_hit_metrics = latest_metrics.get(sid) or {}
        persistence_warning = current_hit_metrics.get("persistence_warning")
        if not isinstance(persistence_warning, dict):
            persistence_warning = {
                "warning": bool(
                    current_hit_metrics.get("persistence_warning")
                ),
                "state": current_hit_metrics.get("persistence_state"),
                "count": current_hit_metrics.get("persistence_count"),
                "first_warning_date": current_hit_metrics.get(
                    "persistence_first_warning_date"
                ),
                "latest_warning_date": current_hit_metrics.get(
                    "persistence_latest_warning_date"
                ),
            }

        result[observation.id] = {
            "review_date": review_date.isoformat(),
            "stock": sid,
            "name": observation.stock_name,
            "asset_type": candidate["asset_type"],
            "current_price": candidate.get("close_1d"),
            # 2026-08-18：composite risk pending 狀態機（trigger snapshot 建立、
            # recovery 的 PRICE_RECLAIM 判斷）需要的當日原始 OHLC 與相對大盤報酬。
            "open_1d": candidate.get("open_1d"),
            "high_1d": candidate.get("high_1d"),
            "low_1d": candidate.get("low_1d"),
            "close_1d": candidate.get("close_1d"),
            "price_change_1d": candidate.get("price_change_1d"),
            "excess_return_vs_market": regime_gate._excess_return_vs_market(
                candidate, taiex_return
            ),
            # 見上方 `reversal_check` 註解：independent 於 hard_exclusion 短路序，
            # 供 composite risk pending 判斷是否應該直接視為 REVERSAL_FAILURE。
            "reversal_failure_check": reversal_check,
            "episode_returns": returns_by_observation.get(observation.id) or {},
            "market_rs": candidate.get("rs_market_percentile_20d"),
            "peer_rs": candidate.get("rs_industry_percentile_20d"),
            "rs_rank_improvement": candidate.get("rs_rank_improvement_5d"),
            "distance_to_20d_high": candidate.get("distance_to_20d_high"),
            "atr_pct_14d": candidate.get("atr_pct_14d"),
            "volume_ratios": {
                "volume_1d_to_5d": candidate.get("volume_1d_to_5d_ratio"),
                "volume_5d_to_60d": candidate.get("volume_5d_to_60d_ratio"),
            },
            "institution_flow": {
                "day_1": candidate.get("total_institution_flow_1d"),
                "day_3": candidate.get("total_institution_flow_3d"),
                "day_5": candidate.get("total_institution_flow_5d"),
                "buy_days_3d": candidate.get("consecutive_buy_days_3d"),
            },
            "deterministic_signals": candidate["deterministic_signals"],
            "entry_state": candidate.get("entry_state"),
            "tracking_state": candidate.get("tracking_state"),
            "momentum_freshness": candidate.get("momentum_freshness"),
            "watch_quality_state": candidate.get("watch_quality_state"),
            "quality_evidence": candidate.get("quality_evidence") or {},
            "momentum_phase": candidate.get("momentum_phase"),
            # 2026-08-13：跟 P3 用同一個 momentum_frame／同一個 compute_momentum_score
            # 算出來的分數（見本函式 425 行）；額外存進 SignalObservationReview.
            # momentum_score，讓動能分數折線圖在 P3 沒有再次選中的那幾天也有資料點。
            "momentum_score": candidate.get("momentum_score"),
            "risk_flags": candidate["deterministic_signals"].get("risk_flags")
            or [],
            "risk_warnings": hard.get("risk_warnings") or [],
            "hard_exclusion": hard,
            "failed_follow_through": bool(
                candidate.get("failed_follow_through")
            ),
            "backend_max_decision": (
                "REMOVE" if hard.get("excluded") else "WATCH"
            ),
            "candidate_sources": candidate.get("candidate_sources") or [],
            "persistence_warning": persistence_warning,
            "market_regime": (market_context or {}).get("market_regime")
            or (market_context or {}).get("market_state"),
            # M27 Market Regime v2（2026-08-04 shadow → 2026-09-04 Production
            # Integration）：`market_context_severity` 從這輪起會被
            # decide_observation_action() 的 Market Context Overlay（優先序全
            # 部 STOP 條件之後）讀取，可以把 CONTINUE 提升為 CAUTION，**但
            # MARKET_CONTEXT 從未進入 CORE_DIMENSIONS，數學上不可能單獨造成
            # STOP_OBSERVING**（見 §21/§25、CORE_DIMENSIONS 常數定義）。
            "market_stress": (market_context or {}).get("market_stress"),
            "effective_market_state": (market_context or {}).get(
                "effective_market_state"
            ),
            "market_context_severity": market_stress.compute_market_context_severity(
                (market_context or {}).get("effective_market_state"),
                (market_context or {}).get("market_stress"),
            ),
            # §16：給 Tracking Prompt allowlist 用的緊湊 market_environment
            # 物件（不塞 raw VIX/oil/gold history，只留摘要層級）。
            "market_environment": {
                "trend_regime": (market_context or {}).get("market_regime"),
                "market_stress": (market_context or {}).get("market_stress"),
                "effective_market_state": (market_context or {}).get(
                    "effective_market_state"
                ),
                "stress_families": (market_context or {}).get("stress_families"),
                "stress_reason_codes": (market_context or {}).get(
                    "market_stress_key_reason_codes"
                ),
                "data_complete": (market_context or {}).get(
                    "market_stress_data_complete"
                ),
            },
            "data_quality": {
                "price_available": candidate.get("close_1d") is not None,
                "momentum_frame_available": bool(raw_frame),
                "baseline_quality": observation.baseline_quality,
            },
        }
    return result


def run_tracking_assessments(
    payloads: Sequence[Dict[str, Any]],
    *,
    model: str = DEFAULT_TRACKING_MODEL,
    batch_size: Optional[int] = None,
) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Run date-bounded external thesis checks for every payload without a total cap."""

    if not payloads:
        return {}, []
    size = batch_size or _positive_env_int(
        "TRACKING_RESEARCH_BATCH_SIZE",
        DEFAULT_TRACKING_BATCH_SIZE,
    )
    successful: Dict[str, Dict[str, Any]] = {}
    failures: List[Dict[str, Any]] = []
    family = prompt_family.resolve_prompt_family()
    prompt = prompt_family.build_stage_prompt("tracking", family)
    metadata = prompt_family.prompt_metadata(family)
    tracking_version = metadata["tracking_prompt_version"]
    retry_enabled = (
        family == prompt_family.PROMPT_FAMILY_VERSION
        and os.getenv(
            "SIGNALS_TRACKING_CONTRACT_RETRY", "true"
        ).strip().lower() not in {"0", "false", "no", "off"}
    )

    def call_tracking(
        call_batch: Sequence[Dict[str, Any]],
        *,
        contract_retry: Optional[Dict[str, str]] = None,
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any], set[str]]:
        call_date = str(call_batch[0].get("date") or "")
        call_expected = {
            str(item.get("stock") or "")
            for item in call_batch
            if item.get("stock")
        }
        body: Dict[str, Any] = {
            "review_date": call_date,
            "items": list(call_batch),
        }
        if contract_retry:
            body["contract_retry"] = contract_retry
        call_response, call_diagnostic = llm_caller._call_llm_json(
            prompt,
            json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            model=model,
            stage="tracking_review",
            use_web_search=True,
            prompt_cache_key=f"signals:{family}:tracking-review",
            candidate_count=len(call_batch),
            prompt_metadata={
                **metadata,
                "stage_prompt_version": tracking_version,
                "assembled_prompt_sha256": metadata["prompt_sha256"][
                    "tracking"
                ],
                "contract_retry_attempt": 1 if contract_retry else 0,
            },
            response_schema=(
                prompt_family.tracking_output_schema(
                    expected_stocks=sorted(call_expected),
                    review_date=call_date,
                )
                if family == prompt_family.PROMPT_FAMILY_VERSION
                else None
            ),
            response_format_name=(
                "fishtail_v7_tracking"
                if family == prompt_family.PROMPT_FAMILY_VERSION
                else None
            ),
        )
        return call_response, call_diagnostic or {}, call_expected

    max_contract_retries = 2

    def _retry_single_stock(
        sid: str,
        source_by_stock: Dict[str, Dict[str, Any]],
        review_date: str,
        *,
        rejection_reason: str,
        required_correction: str,
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """對單一股票重新請求 tracking assessment。

        2026-08-22：抽出共用——原本只有「契約驗證失敗」（ValueError）路徑有這段
        單檔重試，「LLM 回應格式正確、但整批 items 裡漏答某一檔」
        （TRACKING_OUTPUT_ALIGNMENT_FAILED）完全沒有重試就直接判失敗，導致單一
        股票被 LLM 漏答就讓當天整個 pipeline 判 partial_failure（見 2026-08-21
        00738U 案例）。兩種情況本質上都是「這一檔需要重新請求」，共用同一套
        重試邏輯。
        """
        if not retry_enabled or sid not in source_by_stock:
            return None, {}
        current_reason = rejection_reason
        retry_diagnostic: Dict[str, Any] = {}
        for contract_attempt in range(1, max_contract_retries + 1):
            try:
                retry_response, retry_diagnostic, _ = call_tracking(
                    [source_by_stock[sid]],
                    contract_retry={
                        "previous_rejection": current_reason[:1000],
                        "required_correction": required_correction,
                    },
                )
                retry_items = (
                    retry_response.get("items")
                    if isinstance(retry_response, dict)
                    and retry_response.get("review_date") == review_date
                    else None
                )
                retry_raw = (
                    retry_items[0]
                    if isinstance(retry_items, list)
                    and len(retry_items) == 1
                    and isinstance(retry_items[0], dict)
                    and str(retry_items[0].get("stock") or "") == sid
                    else None
                )
                if retry_raw is not None:
                    validated = _validate_external_assessment(
                        retry_raw,
                        review_date=date.fromisoformat(review_date),
                    )
                    validated["_prompt_metadata"] = {
                        **metadata,
                        "stage_prompt_version": tracking_version,
                        "assembled_prompt_sha256": metadata["prompt_sha256"][
                            "tracking"
                        ],
                    }
                    validated["_llm_diagnostic"] = {
                        **retry_diagnostic,
                        "contract_retry_attempt": contract_attempt,
                        "previous_contract_error": current_reason[:500],
                    }
                    return validated, retry_diagnostic
            except ValueError as retry_value_exc:
                current_reason = str(retry_value_exc)
                continue
            except Exception as retry_exc:
                retry_diagnostic = {
                    **retry_diagnostic,
                    "retry_exception": str(retry_exc)[:500],
                }
                break
        return None, retry_diagnostic

    for offset in range(0, len(payloads), size):
        batch = list(payloads[offset : offset + size])
        review_date = str(batch[0].get("date") or "")
        expected = {
            str(item.get("stock") or "") for item in batch if item.get("stock")
        }
        source_by_stock = {
            str(item.get("stock") or ""): item
            for item in batch
            if item.get("stock")
        }
        max_batch_attempts = 3 if retry_enabled else 1
        response: Optional[Dict[str, Any]] = None
        diagnostic: Dict[str, Any] = {}
        batch_error: Optional[str] = None
        for batch_attempt in range(max_batch_attempts):
            try:
                response, diagnostic, _ = call_tracking(batch)
                batch_error = None
            except Exception as exc:
                response = None
                diagnostic = {}
                batch_error = str(exc) or "Tracking LLM call raised an exception."
                continue
            diagnostic = diagnostic or {}
            if not isinstance(response, dict) or not isinstance(
                response.get("items"), list
            ) or (
                family == prompt_family.PROMPT_FAMILY_VERSION
                and response.get("review_date") != review_date
            ):
                batch_error = (
                    diagnostic.get("message") or "Tracking LLM call failed."
                )
                response = None
                continue
            break
        if response is None:
            for sid in sorted(expected):
                failures.append(
                    _review_failure(
                        sid,
                        "TRACKING_RESEARCH_FAILED",
                        batch_error or "Tracking LLM call failed.",
                        diagnostic=diagnostic,
                    )
                )
            continue

        seen: set[str] = set()
        for raw in response["items"]:
            sid = str(raw.get("stock") or "") if isinstance(raw, dict) else ""
            if sid not in expected:
                continue
            if sid in seen:
                # 2026-09-04：LLM 對同一批 tracking review 的回應裡，同一檔股票
                # 出現兩次（真實案例 6226）。舊版一律直接判失敗，完全沒有重試，
                # 且會蓋掉第一次可能已經驗證成功的結果（`decide_observation_
                # action()` 只要 review_technical_failure 非 None 就無條件回
                # DECISION_FAILED，不會去看 external_thesis_assessment 是否
                # 其實有效）——這正是 2026-08-22 那次「LLM 漏答」修法想統一解決
                # 的同一類問題（「這一檔需要重新請求」），當時漏掉了「重複」這
                # 個情境。若第一次已經成功驗證，重複只是雜訊直接忽略；只有第
                # 一次沒能成功時，才需要對這一檔單獨重試。
                if sid in successful:
                    continue
                validated_via_retry, retry_diagnostic = _retry_single_stock(
                    sid,
                    source_by_stock,
                    review_date,
                    rejection_reason=(
                        "Tracking assessment returned duplicate entries for "
                        "this stock in the same batch."
                    ),
                    required_correction=(
                        "上一輪回應對同一檔股票輸出了兩筆（或以上）tracking "
                        "assessment，請重新針對這一檔股票只輸出一筆完整、正確"
                        "的評估。"
                    ),
                )
                if validated_via_retry is not None:
                    successful[sid] = validated_via_retry
                    continue
                failures.append(
                    _review_failure(
                        sid,
                        "TRACKING_OUTPUT_ALIGNMENT_FAILED",
                        "Duplicate tracking assessment.",
                        diagnostic=retry_diagnostic if retry_enabled else {},
                    )
                )
                continue
            seen.add(sid)
            try:
                validated = _validate_external_assessment(
                    raw,
                    review_date=date.fromisoformat(review_date),
                )
                validated["_prompt_metadata"] = {
                    **metadata,
                    "stage_prompt_version": tracking_version,
                    "assembled_prompt_sha256": metadata["prompt_sha256"][
                        "tracking"
                    ],
                }
                validated["_llm_diagnostic"] = diagnostic
                successful[sid] = validated
            except ValueError as exc:
                validated_via_retry, retry_diagnostic = _retry_single_stock(
                    sid,
                    source_by_stock,
                    review_date,
                    rejection_reason=str(exc),
                    required_correction=(
                        "只重做這一檔並修正契約錯誤。若要使用 "
                        "MATERIAL_NEGATIVE_EVENT 或 DATA_CONTRADICTION "
                        "判定 THESIS_INVALIDATED，material_evidence 必須有"
                        "截至 review_date 可追溯的 summary、URL、"
                        "published_date；否則不得宣告失效，應依證據改為 "
                        "THESIS_WEAKENING 或 RESEARCH_UNAVAILABLE，且 "
                        "invalidation_reason_code 必須為 null。不可捏造來源。"
                    ),
                )
                if validated_via_retry is not None:
                    successful[sid] = validated_via_retry
                    continue
                failures.append(
                    _review_failure(
                        sid,
                        "TRACKING_OUTPUT_INVALID",
                        str(exc),
                        diagnostic=(
                            retry_diagnostic
                            if retry_enabled
                            else diagnostic
                        ),
                    )
                )
        for sid in sorted(expected - seen):
            validated_via_retry, retry_diagnostic = _retry_single_stock(
                sid,
                source_by_stock,
                review_date,
                rejection_reason="Tracking assessment omitted the stock.",
                required_correction=(
                    "上一輪回應完全漏掉了這一檔股票，請重新針對這一檔股票輸出"
                    "完整的追蹤評估（tracking assessment），items 陣列裡不可再"
                    "省略這一檔。"
                ),
            )
            if validated_via_retry is not None:
                successful[sid] = validated_via_retry
                continue
            failures.append(
                _review_failure(
                    sid,
                    "TRACKING_OUTPUT_ALIGNMENT_FAILED",
                    "Tracking assessment omitted the stock.",
                    diagnostic=retry_diagnostic if retry_enabled else {},
                )
            )
    return successful, failures


def decide_observation_action(
    *,
    current_backend_evidence: Dict[str, Any],
    external_thesis_assessment: Optional[Dict[str, Any]],
    latest_valid_reviews: Sequence[Dict[str, Any]],
    current_observation: Dict[str, Any],
    review_technical_failure: Optional[Dict[str, Any]] = None,
) -> ObservationDecision:
    """Single authoritative P4 state machine."""

    if review_technical_failure is not None:
        return ObservationDecision(
            decision=DECISION_FAILED,
            reason_codes=["DATA_QUALITY_WARNING"],
            reason=str(
                review_technical_failure.get("error_summary")
                or "本次追蹤檢查未完成，維持上一個有效狀態。"
            ),
            caution_dimensions=[],
            failed_dimensions=[],
            technical_status=str(
                review_technical_failure.get("status") or DECISION_FAILED
            ),
        )

    hard = current_backend_evidence.get("hard_exclusion") or {}
    hard_reason = str(hard.get("reason") or "").upper()
    hard_excluded = bool(hard.get("excluded"))
    reversal_check = current_backend_evidence.get("reversal_failure_check") or {}
    # 2026-08-18：`build_hard_exclusion_result` 找到第一個成立的理由就短路 return，
    # COMPOSITE_RISK_EXCLUDE 排在 REVERSAL_FAILURE 之前，兩者同天成立時只會看到
    # COMPOSITE_RISK_EXCLUDE。這裡獨立判斷「今天是否也真的符合更嚴格的
    # REVERSAL_FAILURE materiality 標準」，避免較寬鬆的 composite 規則反而繞過較嚴格
    # 的 reversal failure（P4 v2 spec §7）。
    reversal_masked_by_composite = (
        hard_excluded
        and hard_reason == COMPOSITE_RISK_REASON
        and bool(reversal_check.get("triggered"))
    )
    if hard_excluded and (
        hard_reason in IMMEDIATE_HARD_REASONS or reversal_masked_by_composite
    ):
        effective_reason = (
            hard_reason if hard_reason in IMMEDIATE_HARD_REASONS else "REVERSAL_FAILURE"
        )
        return ObservationDecision(
            decision=DECISION_STOP,
            reason_codes=[effective_reason],
            reason=f"Backend 已確認 {effective_reason}，本 observation thesis 明確失效。",
            caution_dimensions=[],
            failed_dimensions=["MOMENTUM_STRUCTURE"],
            pending_stop_update="CLEAR",
        )

    if (
        str(current_backend_evidence.get("tracking_state") or "").upper()
        == tracking_state.TRACKING_INVALIDATED
    ):
        return ObservationDecision(
            decision=DECISION_STOP,
            reason_codes=["TRACKING_INVALIDATED"],
            reason="Backend tracking state 已確認為 INVALIDATED。",
            caution_dimensions=[],
            failed_dimensions=["MOMENTUM_STRUCTURE"],
            pending_stop_update="CLEAR",
        )

    external = external_thesis_assessment or {
        "assessment": "RESEARCH_UNAVAILABLE"
    }
    if str(external.get("assessment") or "").upper() == "THESIS_INVALIDATED":
        reason_code = str(
            external.get("invalidation_reason_code") or ""
        ).upper()
        if reason_code in EXTERNAL_INVALIDATION_REASONS:
            return ObservationDecision(
                decision=DECISION_STOP,
                reason_codes=[reason_code],
                reason=str(external.get("assessment_reason") or reason_code),
                caution_dimensions=[],
                failed_dimensions=["CATALYST_THESIS"],
                pending_stop_update="CLEAR",
            )

    caution_dimensions, failed_dimensions, reason_codes = (
        _current_caution_evidence(current_backend_evidence, external)
    )
    baseline_incomplete = (
        str(current_observation.get("baseline_quality") or "")
        == "LEGACY_INCOMPLETE"
    )
    if baseline_incomplete:
        caution_dimensions.append("DATA_QUALITY")
        reason_codes.append("LEGACY_BASELINE_INCOMPLETE")

    # ---- COMPOSITE_RISK_EXCLUDE pending 狀態機（P4 Observation Lifecycle v2，
    # 2026-08-18）---------------------------------------------------------
    # 前面三個立即失效路徑都沒命中時才會走到這裡。跟一般「持續警戒轉停止」路徑
    # （下面 `prior_decision == DECISION_CAUTION` 那段）是完全獨立的狀態機：composite
    # risk 的 recovery／confirmation 比較基準是「觸發當天的 trigger snapshot」，不是
    # 「上一次複核的 decision」。這段只讀 caution_dimensions/failed_dimensions/
    # reason_codes，不 mutate，所以之後若沒有從這裡 return，原本的一般邏輯仍會用同一份
    # 未受影響的 list 繼續判斷。
    pending_status = str(current_observation.get("pending_stop_status") or "").upper()
    pending_reason = str(current_observation.get("pending_stop_reason") or "").upper()
    pending_active = (
        pending_status == PENDING_STOP_STATUS_ACTIVE
        and pending_reason == COMPOSITE_RISK_REASON
    )
    is_composite_today = hard_excluded and hard_reason == COMPOSITE_RISK_REASON
    regime_now = str(current_backend_evidence.get("market_regime") or "").upper()

    # 2026-08-27 方法 A 延伸：RISK_OFF 當天不給 Composite Risk「可能是洗盤，先觀察
    # 一天」的緩衝——這套 pending 機制本身完全不看大盤 regime（`build_hard_exclusion_
    # result()` 純個股層級判斷，BULL_TREND 底下同樣會觸發），2026-08-18 設計成多日
    # 確認正是為了保護「大多頭健康換手」不被誤殺；但退潮盤沒有必要給這個benefit of the
    # doubt。RISK_OFF 時直接把 composite risk 訊號併入核心維度證據（等同於同時判定
    # MOMENTUM_STRUCTURE + PARTICIPATION 失效），交給下面統一的核心維度判斷（方法 A
    # 加速停止 / 既有兩天持續失效）處理，不再單獨走 pending 狀態機。BULL_TREND /
    # VOLATILE_RANGE 完全不受影響，維持原本的多日觀察緩衝。
    if regime_now == "RISK_OFF" and (is_composite_today or pending_active):
        failed_dimensions = sorted(
            set(failed_dimensions) | {"MOMENTUM_STRUCTURE", "PARTICIPATION"}
        )
        caution_dimensions = sorted(
            set(caution_dimensions) | {"MOMENTUM_STRUCTURE", "PARTICIPATION"}
        )
        reason_codes = _dedupe(reason_codes + ["COMPOSITE_RISK_TREATED_AS_CORE_FAILURE"])
    elif pending_active:
        trigger_snapshot = current_observation.get("pending_stop_trigger_snapshot")
        if _has_composite_risk_recovery(
            current_backend_evidence, external, trigger_snapshot
        ):
            # 4a：恢復——清 pending，落回下面一般 caution/continue 判斷（不再考慮
            # composite risk），不得因為昨天的 composite risk 又 STOP。
            pass
        else:
            participation_failed = "PARTICIPATION" in failed_dimensions
            momentum_failed = "MOMENTUM_STRUCTURE" in failed_dimensions
            if participation_failed and momentum_failed:
                # 4b：確認惡化——資金參與與動能結構「這一天」同時仍然失效，判定
                # composite risk 為真，STOP。
                return ObservationDecision(
                    decision=DECISION_STOP,
                    reason_codes=["COMPOSITE_RISK_CONFIRMED"],
                    reason=(
                        "Composite Risk（出貨 + 法人資金反轉）經隔日複核確認："
                        "資金參與與動能結構持續失效，非單日假突破。"
                    ),
                    caution_dimensions=sorted(set(caution_dimensions)),
                    failed_dimensions=sorted(set(failed_dimensions)),
                    pending_stop_update="CLEAR",
                )
            existing_reviews = int(
                current_observation.get("pending_stop_review_count") or 0
            )
            if existing_reviews >= COMPOSITE_PENDING_MAX_REVIEWS:
                # 4c 過期：連續 COMPOSITE_PENDING_MAX_REVIEWS 次複核都既未恢復也未
                # 確認，「沒有證明恢復」不等於「證明失效」——清 pending，回到一般
                # lifecycle，不強制 STOP。
                pass
            else:
                # 4c 持續 pending：既非恢復也非確認，維持 CAUTION 繼續觀察下一個
                # 複核日。
                return ObservationDecision(
                    decision=DECISION_CAUTION,
                    reason_codes=_dedupe(reason_codes + ["COMPOSITE_RISK_PENDING"]),
                    reason=(
                        _caution_reason(caution_dimensions, external)
                        if caution_dimensions
                        else (
                            "Composite Risk（出貨 + 法人資金反轉同時出現）尚在"
                            "待確認狀態，尚未看到明確恢復或惡化訊號，繼續觀察。"
                        )
                    ),
                    caution_dimensions=sorted(set(caution_dimensions)),
                    failed_dimensions=sorted(set(failed_dimensions)),
                    pending_stop_update="KEEP",
                )
    elif is_composite_today:
        # 5：全新 composite risk 觸發——不立即 STOP，建立 pending 並先標警戒，等下一
        # 個複核日才決定是恢復、確認、或繼續待定。
        return ObservationDecision(
            decision=DECISION_CAUTION,
            reason_codes=_dedupe(reason_codes + ["COMPOSITE_RISK_PENDING"]),
            reason=(
                "今日出現 Composite Risk（出貨 + 法人資金反轉同時出現），"
                "可能是高檔換手、洗盤或獲利了結，先列入警戒，"
                "待下一個複核日確認是否為假突破。"
            ),
            caution_dimensions=sorted(set(caution_dimensions)),
            failed_dimensions=sorted(set(failed_dimensions)),
            pending_stop_update="SET",
            pending_stop_reason=COMPOSITE_RISK_REASON,
            pending_stop_trigger_snapshot=_build_composite_risk_trigger_snapshot(
                current_backend_evidence
            ),
        )
    # ---- composite risk 狀態機結束；若沒有從上面 return，代表：沒有進行中的
    # pending、今天也沒有新的 composite 觸發，或剛剛的 pending 因為恢復/過期被清掉了
    # ——一律回到原本既有（本次改版沒有更動）的一般 caution/continue 判斷。
    pending_cleared_this_review = pending_active

    recovery = _has_recovery_evidence(current_backend_evidence, external)
    prior = latest_valid_reviews[-1] if latest_valid_reviews else None
    prior_decision = str((prior or {}).get("decision") or "").upper()
    if recovery:
        for dimension in list(caution_dimensions):
            if dimension in CORE_DIMENSIONS:
                caution_dimensions.remove(dimension)
        failed_dimensions = [
            dimension
            for dimension in failed_dimensions
            if dimension not in CORE_DIMENSIONS
        ]
        reason_codes = [
            code
            for code in reason_codes
            if code
            not in {
                "MOMENTUM_DETERIORATING",
                "MOMENTUM_STALE",
                "PARTICIPATION_WEAKENING",
                "INSTITUTION_FLOW_REVERSAL_WARNING",
                "CATALYST_WEAKENING",
                "CATALYST_UNCONFIRMED",
                "MULTI_DIMENSION_EARLY_WARNING",
            }
        ]

    if not recovery and not baseline_incomplete:
        current_core_failed = {
            value for value in failed_dimensions if value in CORE_DIMENSIONS
        }
        # regime_now 已在上面 composite risk 分支前算好，這裡沿用同一份，不重算。
        # 2026-08-27：RISK_OFF 當天加速停止（方法 A）——7/16 那種真實系統性下殺（大盤
        # 單日重挫且連續多日累計 -11.7%）曾讓多檔核心維度當天就已同時失效的股票，仍要
        # 多等一天複核確認才判定 STOP，錯過提前止血的機會。這裡新增：只要「今天」
        # regime=RISK_OFF 且核心維度已同時出現 >=2 個失效（交集含 MOMENTUM_STRUCTURE
        # 或 PARTICIPATION），不必等隔天複核確認即可直接 STOP。刻意只限 RISK_OFF（不含
        # VOLATILE_RANGE）——震盪盤不必然是趨勢性下殺，範圍太寬會提高誤殺健康股的風險。
        # 未觸發時退回原本既有的「連續兩天複核都失效」判斷，行為完全不變。
        if (
            regime_now == "RISK_OFF"
            and len(current_core_failed) >= 2
            and current_core_failed & {"MOMENTUM_STRUCTURE", "PARTICIPATION"}
        ):
            return ObservationDecision(
                decision=DECISION_STOP,
                reason_codes=[_risk_off_accelerated_stop_reason(current_core_failed)],
                reason=(
                    "大盤風險退潮（RISK_OFF）期間，今日核心維度已同時出現多重失效訊號，"
                    "不等待隔日複核確認即判定失效（風險退潮加速停止）。"
                ),
                caution_dimensions=sorted(set(caution_dimensions)),
                failed_dimensions=sorted(current_core_failed),
                pending_stop_update="CLEAR" if pending_cleared_this_review else None,
            )

        if prior_decision == DECISION_CAUTION:
            prior_failed = {
                str(value)
                for value in (prior.get("failed_dimensions") or [])
                if value in CORE_DIMENSIONS
            }
            persistent = prior_failed & current_core_failed
            if (
                len(persistent) >= 2
                and persistent & {"MOMENTUM_STRUCTURE", "PARTICIPATION"}
            ):
                return ObservationDecision(
                    decision=DECISION_STOP,
                    reason_codes=[_sustained_stop_reason(persistent)],
                    reason="連續兩次成功交易日 Review 顯示多個核心維度持續失效，且未見有效恢復。",
                    caution_dimensions=sorted(set(caution_dimensions)),
                    failed_dimensions=sorted(current_core_failed),
                    pending_stop_update="CLEAR" if pending_cleared_this_review else None,
                )

    caution_dimensions = sorted(set(caution_dimensions))
    failed_dimensions = sorted(set(failed_dimensions))
    reason_codes = _dedupe(reason_codes)

    # ---- Priority 6：Market Context Overlay（M27 Market Regime v2 Production
    # Integration，2026-09-04，見規格書 §19~§28）----------------------------
    # 只有走到這裡（前面所有 STOP 條件都沒觸發）才考慮市場背景。MARKET_CONTEXT
    # 從未加入 CORE_DIMENSIONS（見常數定義），數學上不可能被上面任何一段
    # sustained-stop 判斷讀到，永遠無法單獨造成 STOP_OBSERVING——這是這段邏輯
    # 刻意放在所有 STOP 分支「之後」的唯一原因。
    market_context_severity = str(
        current_backend_evidence.get("market_context_severity")
        or market_stress.CONTEXT_SEVERITY_NORMAL
    ).upper()

    if caution_dimensions:
        if len(set(failed_dimensions) & CORE_DIMENSIONS) >= 2:
            reason_codes.append("MULTI_DIMENSION_EARLY_WARNING")
        # §23：股票本身已有 caution evidence，市場壓力偏高時附加
        # MARKET_RISK_ELEVATED + MARKET_CONTEXT——純資訊性標記，不改變既有的
        # CAUTION 判定（本來就已經是 CAUTION）。UNKNOWN 只標資料品質提示。
        if market_context_severity in (
            market_stress.CONTEXT_SEVERITY_WARNING,
            market_stress.CONTEXT_SEVERITY_STRESS,
        ):
            caution_dimensions = sorted(set(caution_dimensions) | {"MARKET_CONTEXT"})
            reason_codes = _dedupe(reason_codes + ["MARKET_RISK_ELEVATED"])
        elif market_context_severity == market_stress.CONTEXT_SEVERITY_UNKNOWN:
            reason_codes = _dedupe(reason_codes + ["MARKET_STRESS_DATA_INCOMPLETE"])
        return ObservationDecision(
            decision=DECISION_CAUTION,
            reason_codes=_dedupe(reason_codes),
            reason=_caution_reason(caution_dimensions, external),
            caution_dimensions=caution_dimensions,
            failed_dimensions=failed_dimensions,
            pending_stop_update="CLEAR" if pending_cleared_this_review else None,
        )

    continue_code = _continue_reason_code(
        current_backend_evidence,
        external,
        recovered=prior_decision == DECISION_CAUTION and recovery,
    )

    # §24：股票本身完全健康（無 caution evidence、無 STOP 觸發），市場壓力達
    # STRESS 時，最低狀態提升為 CAUTION——這是「純市場因素」能造成 CAUTION 的
    # 唯一路徑，且 caution_dimensions 只會是 ["MARKET_CONTEXT"]，reason_codes
    # 不含任何個股層級失效代碼，不會被誤讀成個股論點已轉弱。
    if market_context_severity == market_stress.CONTEXT_SEVERITY_STRESS:
        return ObservationDecision(
            decision=DECISION_CAUTION,
            reason_codes=_dedupe([continue_code, "MARKET_RISK_ELEVATED"]),
            reason=(
                "個股本身動能／資金／論點皆未見失效訊號，但目前市場處於"
                "壓力偏高狀態（市場內部結構或資金流偏弱），提升為警戒觀察，"
                "個股原始推薦論點尚未失效。"
            ),
            caution_dimensions=["MARKET_CONTEXT"],
            failed_dimensions=[],
            pending_stop_update="CLEAR" if pending_cleared_this_review else None,
        )

    # §27 Market Stress Recovery：昨天若因 market_context=WARNING/STRESS 進入
    # CAUTION，今天股票健康且市場降回 NORMAL，這裡自然落到 CONTINUE（呼叫端
    # run_daily_observation_reviews 對 CONTINUE 本來就會重設
    # consecutive_caution_count=0，不需要在這裡額外處理）。
    continue_reason_codes = [continue_code]
    if market_context_severity == market_stress.CONTEXT_SEVERITY_WARNING:
        # §23：股票本身完全健康時，WARNING 不足以升級成 CAUTION，只附加提示。
        continue_reason_codes.append("MARKET_RISK_ELEVATED")
    elif market_context_severity == market_stress.CONTEXT_SEVERITY_UNKNOWN:
        # §28：UNKNOWN 不得自行 CAUTION／STOP，只增加資料品質提示。
        continue_reason_codes.append("MARKET_STRESS_DATA_INCOMPLETE")

    return ObservationDecision(
        decision=DECISION_CONTINUE,
        reason_codes=_dedupe(continue_reason_codes),
        reason="原始推薦 thesis 與目前動能／參與結構仍有效，繼續觀察。",
        caution_dimensions=[],
        failed_dimensions=[],
        pending_stop_update="CLEAR" if pending_cleared_this_review else None,
    )


def run_daily_observation_reviews(
    db: Session,
    *,
    review_date: date,
    market_context: Dict[str, Any],
    p3_recommended_stock_ids: Optional[Iterable[str]] = None,
    ingestion: Optional[Dict[str, Any]] = None,
    momentum_frame: Optional[Dict[str, Dict[str, Any]]] = None,
    current_candidates: Optional[Sequence[Dict[str, Any]]] = None,
    assessment_runner: Callable[
        [Sequence[Dict[str, Any]]],
        tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]],
    ] = run_tracking_assessments,
    persist: bool = False,
) -> Dict[str, Any]:
    """Review every active observation, excluding episodes created today."""

    # 2026-08-10：停用「從舊 M23 signal_watch_hits 回填 P4 觀察」。這個機制產生的
    # LEGACY_INCOMPLETE 觀察（selection_version 為 null）會讓 decide_observation_action()
    # 的「持續警戒→STOP」判斷整段被跳過（見該函式 baseline_incomplete 前置條件），
    # 導致觀察卡在無限警戒。既有的 LEGACY_INCOMPLETE 觀察已用
    # backend/stop_legacy_incomplete_observations.py 一次性停止；這裡拿掉呼叫避免
    # 未來再產生新的一批。函式本身保留不刪，如需要還原只要恢復這行呼叫。
    # bootstrap_legacy_observations(db)
    observations = (
        db.query(SignalObservation)
        .filter(
            (
                SignalObservation.status.in_(
                    [STATUS_OBSERVING, STATUS_CAUTION]
                )
            )
            | (
                # STOPPED observations keep being reviewed daily until
                # STOP_OBSERVING has been confirmed on STOP_CONFIRM_THRESHOLD
                # consecutive days (or recovers, which resets the counter to
                # 0 and flips status away from STOPPED). This also covers a
                # same-day idempotent re-run of an observation that was only
                # just stopped today, since a fresh stop always starts at 1.
                (SignalObservation.status == STATUS_STOPPED)
                & (SignalObservation.stop_confirm_count < STOP_CONFIRM_THRESHOLD)
            )
        )
        .order_by(SignalObservation.id.asc())
        .all()
    )
    active_before = len(
        [
            row
            for row in observations
            if row.status in {STATUS_OBSERVING, STATUS_CAUTION}
        ]
    )
    reviewable = [
        row for row in observations if row.started_signal_date < review_date
    ]
    evidence_by_id = build_current_tracking_evidence(
        db,
        observations=reviewable,
        review_date=review_date,
        market_context=market_context,
        ingestion=ingestion,
        momentum_frame=momentum_frame,
        current_candidates=current_candidates,
    )

    prior_rows = (
        db.query(SignalObservationReview)
        .filter(
            SignalObservationReview.observation_id.in_(
                [row.id for row in reviewable]
            ),
            SignalObservationReview.review_date < review_date,
            SignalObservationReview.decision != DECISION_FAILED,
        )
        .order_by(
            SignalObservationReview.observation_id.asc(),
            SignalObservationReview.review_date.asc(),
        )
        .all()
        if reviewable
        else []
    )
    prior_by_id: Dict[int, List[Dict[str, Any]]] = {}
    for row in prior_rows:
        prior_by_id.setdefault(row.observation_id, []).append(
            _review_to_state_dict(row)
        )
    same_day_reviews = (
        db.query(SignalObservationReview)
        .filter(
            SignalObservationReview.observation_id.in_(
                [row.id for row in reviewable]
            ),
            SignalObservationReview.review_date == review_date,
        )
        .all()
        if reviewable
        else []
    )
    same_day_by_id = {
        row.observation_id: row for row in same_day_reviews
    }

    prompt_payloads: List[Dict[str, Any]] = []
    hard_skipped: set[int] = set()
    for observation in reviewable:
        evidence = evidence_by_id[observation.id]
        hard = evidence.get("hard_exclusion") or {}
        # 2026-08-18：只有「立即失效」的 hard reason 才跳過 LLM research——
        # COMPOSITE_RISK_EXCLUDE 已經不再是 immediate stop，改進入多日 pending
        # 狀態機（見 decide_observation_action），既然這輪觀察還可能繼續好幾天，
        # LLM 外部查證（是否真的 THESIS_INVALIDATED）跟其他非 composite 觀察一樣
        # 應該照常執行，不能因為 hard.excluded 是 True 就整段跳過。
        if _skip_llm_research(hard, evidence):
            hard_skipped.add(observation.id)
            continue
        prompt_payloads.append(
            _tracking_prompt_input(
                observation,
                review_date=review_date,
                evidence=evidence,
                latest_review=(
                    prior_by_id.get(observation.id, [])[-1]
                    if prior_by_id.get(observation.id)
                    else None
                ),
            )
        )

    external_by_stock, failures = assessment_runner(prompt_payloads)
    # 2026-08-12（成本追蹤）：P4 每日複核也是 LLM stage 之一，一併彙整進
    # tracking_summary，讓 pipeline.py 可以把它併進整次 run 的 token 總量。
    token_usage = llm_caller.summarize_token_usage(
        external_by_stock.values(), diagnostic_key="_llm_diagnostic"
    )
    tracking_payload_metrics: List[Dict[str, Any]] = []
    seen_tracking_metrics: set[str] = set()
    for external in external_by_stock.values():
        diagnostic = external.get("_llm_diagnostic")
        metrics = (
            diagnostic.get("payload_metrics")
            if isinstance(diagnostic, dict)
            else None
        )
        if isinstance(metrics, dict):
            key = repr(sorted(metrics.items()))
            if key not in seen_tracking_metrics:
                seen_tracking_metrics.add(key)
                tracking_payload_metrics.append(dict(metrics))
    for failure in failures:
        diagnostic = failure.get("diagnostic")
        metrics = (
            diagnostic.get("payload_metrics")
            if isinstance(diagnostic, dict)
            else None
        )
        if isinstance(metrics, dict):
            key = repr(sorted(metrics.items()))
            if key not in seen_tracking_metrics:
                seen_tracking_metrics.add(key)
                tracking_payload_metrics.append(dict(metrics))
    failure_by_stock = {
        str(item.get("stock") or item.get("stock_id") or ""): item
        for item in failures
    }
    p3_recommended = {
        str(stock_id) for stock_id in (p3_recommended_stock_ids or [])
    }

    counts = {
        DECISION_CONTINUE: 0,
        DECISION_CAUTION: 0,
        DECISION_STOP: 0,
        DECISION_FAILED: 0,
    }
    conflicts: List[Dict[str, Any]] = []
    review_outputs: List[Dict[str, Any]] = []
    for observation in reviewable:
        sid = observation.stock_id
        evidence = evidence_by_id[observation.id]
        external = external_by_stock.get(sid)
        failure = failure_by_stock.get(sid)
        decision = decide_observation_action(
            current_backend_evidence=evidence,
            external_thesis_assessment=external,
            latest_valid_reviews=prior_by_id.get(observation.id, []),
            current_observation={
                "status": observation.status,
                "baseline_quality": observation.baseline_quality,
                "pending_stop_status": observation.pending_stop_status,
                "pending_stop_reason": observation.pending_stop_reason,
                "pending_stop_review_count": observation.pending_stop_review_count,
                "pending_stop_trigger_snapshot": observation.pending_stop_trigger_snapshot,
            },
            review_technical_failure=failure,
        )
        _apply_pending_stop_update(
            observation, decision=decision, review_date=review_date
        )
        review = _upsert_review(
            db,
            observation=observation,
            review_date=review_date,
            decision=decision,
            backend_evidence=evidence,
            external_assessment=external,
            market_context=market_context,
            existing=same_day_by_id.get(observation.id),
        )
        counts[decision.decision] += 1
        prior_effective = prior_by_id.get(observation.id, [])
        previous_caution_count = _consecutive_caution_count(prior_effective)
        if decision.decision == DECISION_FAILED:
            pass
        elif decision.decision == DECISION_CAUTION:
            # Any non-STOP decision breaks a pending-archive streak, even if
            # this observation was STOPPED going into today's review -- only
            # STOP_CONFIRM_THRESHOLD *consecutive* STOP confirmations archive.
            observation.status = STATUS_CAUTION
            observation.consecutive_caution_count = previous_caution_count + 1
            observation.latest_decision = DECISION_CAUTION
            observation.last_review_date = review_date
            observation.stop_confirm_count = 0
        elif decision.decision == DECISION_CONTINUE:
            observation.status = STATUS_OBSERVING
            observation.consecutive_caution_count = 0
            observation.latest_decision = DECISION_CONTINUE
            observation.last_review_date = review_date
            observation.stopped_at = None
            observation.stop_reason_code = None
            observation.stop_reason = None
            observation.stop_confirm_count = 0
        else:
            was_already_stopped = observation.status == STATUS_STOPPED
            observation.status = STATUS_STOPPED
            observation.latest_decision = DECISION_STOP
            observation.last_review_date = review_date
            observation.stop_reason_code = decision.reason_codes[0]
            observation.stop_reason = decision.reason
            if was_already_stopped:
                # Re-confirmation: keep the original stop timestamp so the
                # eventual archive row records the day STOP first fired, not
                # the day it was finalized.
                observation.stop_confirm_count += 1
            else:
                # Anchor to review_date (the logical trading day), not real
                # wall-clock time -- P6's outcome metrics module and
                # _finalize_observation_archive both derive "which trading
                # day did STOP first happen" from stopped_at.date(), and
                # that must hold under replay/backfill where review_date
                # differs from the actual moment this code executes.
                observation.stopped_at = datetime.combine(
                    review_date, datetime.utcnow().time()
                )
                observation.stop_confirm_count = 1
                if sid in p3_recommended:
                    conflicts.append(
                        {
                            "stock": sid,
                            "status": "TRACKING_SELECTION_CONFLICT",
                            "stage": "TRACKING",
                            "error_code": "TRACKING_SELECTION_CONFLICT",
                            "error_summary": (
                                "P3 recommended the stock on the same date "
                                "that P4 stopped its active observation."
                            ),
                            "observation_id": observation.id,
                        }
                    )
            if observation.stop_confirm_count >= STOP_CONFIRM_THRESHOLD:
                _finalize_observation_archive(
                    db, observation=observation, archived_date=review_date
                )
                # 2026-08-28：改回當下立即結算（撤銷 2026-08-14 那次「延後一天」的
                # 設計）。當時延後是因為 STOP 判定跟魚尾結算同一晚原子性發生，使用者
                # 永遠看不到「已停止觀察」這個過渡狀態；但現在魚尾頁已經有獨立的
                # 「今天停止觀察」區塊（archive.list_stopped_observations_for_date）
                # 專門呈現這個資訊，不再需要靠「追蹤中」多留一天 rose 底色卡片來補這個
                # 缺口——兩個地方同時顯示同一件事反而讓「追蹤中」不乾淨（使用者反映
                # 明明已經在獨立區塊看得到，追蹤中卻還留著一筆）。`_settle_pending_
                # p4_fishtail_stops`（下面 db.flush() 之後那行）保留當防禦性 self-
                # healing 機制，不會跟這裡的立即結算衝突——它的篩選條件是
                # `archived_date < review_date`（嚴格小於），今天才 archive 的不會
                # 被那條路徑重複處理。
                archive.settle_stock_for_p4_stop(
                    db, stock_id=sid, as_of_trade_date=review_date
                )
        observation.latest_snapshot_json = {
            "review_date": review_date.isoformat(),
            "decision": decision.decision,
            "backend_evidence": evidence,
            "external_assessment": external,
        }
        observation.updated_at = datetime.utcnow()
        review_outputs.append(
            {
                "observation_id": observation.id,
                "episode_id": observation.episode_id,
                "stock": sid,
                **decision.as_dict(),
                "consecutive_caution_count": observation.consecutive_caution_count,
            }
        )
    db.flush()
    settled_exit_count = _settle_pending_archive_exits(db, review_date=review_date)
    settled_fishtail_count = _settle_pending_p4_fishtail_stops(db, review_date=review_date)
    db.flush()

    summary = {
        "review_date": review_date.isoformat(),
        "active_before_review": active_before,
        "excluded_same_day_count": len(observations) - len(reviewable),
        "continue_count": counts[DECISION_CONTINUE],
        "caution_count": counts[DECISION_CAUTION],
        "stopped_count": counts[DECISION_STOP],
        "review_failed_count": counts[DECISION_FAILED],
        "conflict_count": len(conflicts),
        "review_complete": counts[DECISION_FAILED] == 0,
        "tracking_prompt_version": current_tracking_prompt_version(),
        "tracking_state_machine_version": STATE_MACHINE_VERSION,
        "prompt_payload_metrics": tracking_payload_metrics,
        "archived_exit_settled_count": settled_exit_count,
        "fishtail_stop_settled_count": settled_fishtail_count,
        "token_usage": token_usage,
    }
    if persist:
        db.commit()
    return {
        "tracking_summary": summary,
        "reviews": review_outputs,
        "technical_failures": failures,
        "conflicts": conflicts,
    }


def _finalize_observation_archive(
    db: Session, *, observation: SignalObservation, archived_date: date
) -> SignalObservationArchive:
    """Write the final P4 archive record once STOP_OBSERVING has been
    confirmed on STOP_CONFIRM_THRESHOLD consecutive review days.

    ``exit_price``/``return_pct`` are intentionally left null here -- the
    "next trading day's (open+close)/2" price does not exist yet on the day
    this fires. ``_settle_pending_archive_exits`` backfills them once that
    day's daily_price row is available.
    """
    entry_price = (
        db.query(DailyPrice.close_price)
        .filter(
            DailyPrice.stock_id == observation.stock_id,
            DailyPrice.trade_date == observation.started_signal_date,
        )
        .scalar()
    )
    first_stop_date = (
        observation.stopped_at.date()
        if observation.stopped_at is not None
        else archived_date
    )
    archive = SignalObservationArchive(
        observation_id=observation.id,
        episode_id=observation.episode_id,
        stock_id=observation.stock_id,
        stock_name=observation.stock_name,
        started_signal_date=observation.started_signal_date,
        first_stop_date=first_stop_date,
        archived_date=archived_date,
        stop_reason_code=observation.stop_reason_code,
        stop_reason=observation.stop_reason,
        entry_price=float(entry_price) if entry_price is not None else None,
    )
    db.add(archive)
    return archive


def _settle_pending_archive_exits(db: Session, *, review_date: date) -> int:
    """Backfill exit_price/return_pct for archives whose exit day has now
    arrived. Self-healing: if a trading day is skipped, the next call simply
    uses whatever daily_price row is first available after archived_date."""
    pending = (
        db.query(SignalObservationArchive)
        .filter(
            SignalObservationArchive.exit_price.is_(None),
            SignalObservationArchive.archived_date < review_date,
        )
        .all()
    )
    if not pending:
        return 0
    stock_ids = {row.stock_id for row in pending}
    price_row_by_stock = {
        stock_id: (open_price, close_price)
        for stock_id, open_price, close_price in (
            db.query(
                DailyPrice.stock_id,
                DailyPrice.open_price,
                DailyPrice.close_price,
            )
            .filter(
                DailyPrice.stock_id.in_(stock_ids),
                DailyPrice.trade_date == review_date,
            )
            .all()
        )
    }
    settled = 0
    for archive in pending:
        prices = price_row_by_stock.get(archive.stock_id)
        if prices is None:
            continue
        open_price, close_price = prices
        if open_price is None or close_price is None:
            continue
        exit_price = (float(open_price) + float(close_price)) / 2.0
        archive.exit_trade_date = review_date
        archive.exit_price = exit_price
        if archive.entry_price not in (None, 0):
            archive.return_pct = (
                (exit_price - archive.entry_price) / archive.entry_price * 100.0
            )
        archive.updated_at = datetime.utcnow()
        settled += 1
    return settled


def _settle_pending_p4_fishtail_stops(db: Session, *, review_date: date) -> int:
    """2026-08-14：P4 判定 STOP 的當晚，先不結算魚尾追蹤週期——留到「下一個複核日」
    才結算，讓使用者至少有一個觀察日能在追蹤中名單看到「已停止觀察」（rose 底色，
    見 archive/page.tsx 的 observationCardTone）這個狀態，主要目的是告知，不是直接
    消失進紀錄區。跟 `_settle_pending_archive_exits`（P4 archive 的 exit_price 延後
    一天補上）是同一種「留一個觀察日才動作」的既有 self-healing pattern，不是新機制。

    找「已有 SignalObservationArchive（代表 P4 那晚已經定案 STOP），但 archived_date
    早於今天 review_date」的股票，若魚尾（signal_watch_hits）還有進行中週期，這次才
    真的結算。self-healing：即使某天漏跑，下次呼叫一樣會抓到還沒結算的舊資料。

    2026-08-16 修正（stale-episode mismatch bug）：舊版只用 `stock_id` +
    `archived_date < review_date` 篩選，完全沒檢查這筆 archive 是不是該股票曾經最新
    一輪觀察——一檔股票只要「曾經」有任何一輪觀察被 STOP 過，之後只要重新進入追蹤
    （全新一輪、可能還在健康的 OBSERVING/CAUTION），下次複核就會被這條規則誤判成
    「該結算」，把目前正在進行、根本沒被判定失效的魚尾週期強制關掉。上線第一天
    （2026-08-14）就誤殺了 9 檔其實仍在追蹤中的股票（含台積電/聯發科/萬海等）。

    2026-08-24 修正（deferred-settlement race condition）：2026-08-16 的修法改用
    「這筆 archive 是不是該股票**目前最新一輪**觀察」判斷，但這個條件本身跟
    `pipeline.py` 的呼叫順序（`sync_recommendations()` 先跑、才輪到本函式）產生新的
    race condition——若一檔股票 STOP 之後，剛好在「延後結算」排定要執行的那一天，
    又被 P3 重新推薦，`sync_recommendations()` 會先建立一輪全新 episode（因為此時
    最新一輪已是 STOPPED，視為沒有進行中觀察）；等本函式接著跑時，「目前最新一輪」
    已經變成那個全新 episode（非 STOPPED），join 條件永遠對不上，導致舊 episode 對應
    的魚尾週期永久孤兒化、卡在「追蹤中」再也結算不到（真實案例：2026-08-24 發現
    5608 等 13 檔卡在 8/18~8/19 的舊資料，動能分數圖顯示「停止觀察」但卡片底色卻是
    最新 episode 的健康狀態，兩者對不起來）。

    修法：不再依賴「目前最新一輪」這個會隨時間變動的狀態，改直接比較「這次 archive
    的 archived_date」跟「目前魚尾週期最新一筆 snapshot_date」的先後關係——如果魚尾
    週期裡所有命中都發生在 archived_date（含）之前，代表這個週期就是被那次 STOP
    結束的那一輪，該結算；如果魚尾週期裡有命中發生在 archived_date 之後，代表已經是
    全新一輪重新命中（不論 P4 那邊有沒有建立新 episode），不該被舊的 archive 誤殺。
    這個條件同時保留 2026-08-16 要防的誤殺（新週期命中都在 archived_date 之後 →
    不結算）與正確處理這次發現的孤兒化（舊週期命中都在 archived_date 之前 → 結算），
    不再需要跟「目前最新一輪是誰」掛勾。
    """
    pending_by_stock: Dict[str, date] = {}
    for stock_id, archived_date in db.query(
        SignalObservationArchive.stock_id,
        SignalObservationArchive.archived_date,
    ).filter(SignalObservationArchive.archived_date < review_date):
        current = pending_by_stock.get(stock_id)
        if current is None or archived_date > current:
            pending_by_stock[stock_id] = archived_date
    if not pending_by_stock:
        return 0
    active_max_snapshot: Dict[str, date] = dict(
        db.query(
            SignalWatchHit.stock_id,
            func.max(SignalWatchHit.snapshot_date),
        )
        .filter(SignalWatchHit.stock_id.in_(pending_by_stock.keys()))
        .group_by(SignalWatchHit.stock_id)
        .all()
    )
    settled = 0
    for stock_id, archived_date in pending_by_stock.items():
        max_snapshot = active_max_snapshot.get(stock_id)
        if max_snapshot is None:
            continue
        if max_snapshot > archived_date:
            # 這批魚尾命中都晚於這次 STOP 事件——已經是全新一輪重新命中，
            # 不能被舊的 archive 誤殺（2026-08-16 要防的情境）。
            continue
        if archive.settle_stock_for_p4_stop(
            db, stock_id=stock_id, as_of_trade_date=review_date
        ):
            settled += 1
    return settled


def list_observations(
    db: Session,
    *,
    status: Optional[str] = None,
    limit: int = 500,
    as_of_date: Optional[date] = None,
) -> Dict[str, Any]:
    query = db.query(SignalObservation)
    if status:
        query = query.filter(SignalObservation.status == status.upper())
    rows = (
        query.order_by(
            SignalObservation.started_signal_date.desc(),
            SignalObservation.stock_id.asc(),
        )
        .limit(max(1, min(limit, 2000)))
        .all()
    )
    recommendation_date, recommended = _recommended_stock_ids(
        db,
        as_of_date=as_of_date,
    )
    latest_review_map = _latest_reviews(db, [row.id for row in rows])
    return {
        "as_of_date": recommendation_date,
        "observations": [
            _serialize_observation(
                row,
                latest_review=latest_review_map.get(row.id),
                recommended_today=row.stock_id in recommended,
            )
            for row in rows
        ],
    }


def get_observation_detail(
    db: Session,
    observation_id: int,
    *,
    as_of_date: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    observation = db.get(SignalObservation, observation_id)
    if observation is None:
        return None
    reviews = (
        db.query(SignalObservationReview)
        .filter(SignalObservationReview.observation_id == observation_id)
        .order_by(SignalObservationReview.review_date.desc())
        .all()
    )
    recommendation_date, recommended = _recommended_stock_ids(
        db,
        as_of_date=as_of_date,
    )
    next_episode_date = (
        db.query(func.min(SignalObservation.started_signal_date))
        .filter(
            SignalObservation.stock_id == observation.stock_id,
            SignalObservation.started_signal_date
            > observation.started_signal_date,
        )
        .scalar()
    )
    recommendation_query = db.query(SignalWatchHit).filter(
        SignalWatchHit.stock_id == observation.stock_id,
        SignalWatchHit.snapshot_date >= observation.started_signal_date,
    )
    if next_episode_date is not None:
        recommendation_query = recommendation_query.filter(
            SignalWatchHit.snapshot_date < next_episode_date
        )
    recommendation_history = (
        recommendation_query
        .order_by(SignalWatchHit.snapshot_date.desc())
        .all()
    )
    stock_episodes = (
        db.query(SignalObservation)
        .filter(SignalObservation.stock_id == observation.stock_id)
        .order_by(SignalObservation.started_signal_date.asc())
        .all()
    )
    payload = _serialize_observation(
        observation,
        latest_review=reviews[0] if reviews else None,
        recommended_today=observation.stock_id in recommended,
    )
    payload.update(
        {
            "as_of_date": recommendation_date,
            "initial_observation": observation.initial_snapshot_json or {},
            "latest_snapshot": observation.latest_snapshot_json or {},
            "review_timeline": _serialize_review_timeline(reviews),
            "recommendation_history": [
                {
                    "date": row.snapshot_date,
                    "signal_type": row.signal_type,
                    "prompt_version": row.prompt_version,
                }
                for row in recommendation_history
            ],
            "episode_history": [
                {
                    "id": row.id,
                    "episode_id": row.episode_id,
                    "status": row.status,
                    "started_signal_date": row.started_signal_date,
                    "stopped_at": row.stopped_at,
                    "initial_thesis": (
                        row.initial_snapshot_json or {}
                    ).get("recommendation_thesis"),
                    "stop_reason_code": row.stop_reason_code,
                    "stop_reason": row.stop_reason,
                    "is_current": row.id == observation.id,
                }
                for row in stock_episodes
            ],
        }
    )
    return payload


def _serialize_review_timeline(
    reviews_desc: Sequence[SignalObservationReview],
) -> List[Dict[str, Any]]:
    """Add the prior lifecycle state without changing persisted P4 decisions."""

    previous_status = STATUS_OBSERVING
    serialized_asc: List[Dict[str, Any]] = []
    for review in reversed(list(reviews_desc)):
        item = _serialize_review(review)
        item["previous_status"] = previous_status
        if review.decision == DECISION_CONTINUE:
            previous_status = STATUS_OBSERVING
        elif review.decision == DECISION_CAUTION:
            previous_status = STATUS_CAUTION
        elif review.decision == DECISION_STOP:
            previous_status = STATUS_STOPPED
        serialized_asc.append(item)
    return list(reversed(serialized_asc))


def get_daily_tracking_summary(
    db: Session,
    *,
    review_date: Optional[date] = None,
) -> Dict[str, Any]:
    target = review_date or db.query(
        func.max(SignalObservationReview.review_date)
    ).scalar()
    if target is None:
        return {
            "tracking_summary": {
                "review_date": None,
                "active_before_review": 0,
                "continue_count": 0,
                "caution_count": 0,
                "stopped_count": 0,
                "review_failed_count": 0,
                "conflict_count": 0,
                "review_complete": True,
                "tracking_prompt_version": current_tracking_prompt_version(),
                "tracking_state_machine_version": STATE_MACHINE_VERSION,
            }
        }
    rows = (
        db.query(SignalObservationReview)
        .filter(SignalObservationReview.review_date == target)
        .all()
    )
    counts = {
        decision: sum(1 for row in rows if row.decision == decision)
        for decision in (
            DECISION_CONTINUE,
            DECISION_CAUTION,
            DECISION_STOP,
            DECISION_FAILED,
        )
    }
    conflict_count = 0
    snapshot = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == target)
        .one_or_none()
    )
    if snapshot is not None:
        conflict_count = int(
            (((snapshot.summary or {}).get("tracking_summary") or {}).get(
                "conflict_count"
            ))
            or 0
        )
    return {
        "tracking_summary": {
            "review_date": target,
            "active_before_review": len(rows),
            "continue_count": counts[DECISION_CONTINUE],
            "caution_count": counts[DECISION_CAUTION],
            "stopped_count": counts[DECISION_STOP],
            "review_failed_count": counts[DECISION_FAILED],
            "conflict_count": conflict_count,
            "review_complete": counts[DECISION_FAILED] == 0,
            "tracking_prompt_version": current_tracking_prompt_version(),
            "tracking_state_machine_version": STATE_MACHINE_VERSION,
        }
    }


def replay_observation_lifecycle(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    observation_ids: Optional[Sequence[int]] = None,
    assessment_runner: Callable[
        [Sequence[Dict[str, Any]]],
        tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]],
    ] = run_tracking_assessments,
) -> Dict[str, Any]:
    """Read-only, chronological point-in-time replay for existing episodes.

    Replay always reconstructs each selected episode from its recommendation date,
    even when the requested output window starts later.  This prevents a later
    review from being evaluated without the state transitions that preceded it.
    No ORM object is mutated and no production review or observation row is written.
    """

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    query = db.query(SignalObservation).filter(
        SignalObservation.started_signal_date < end_date
    )
    if observation_ids:
        query = query.filter(SignalObservation.id.in_(list(observation_ids)))
    observations = query.order_by(SignalObservation.id.asc()).all()
    if not observations:
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "tracking_prompt_version": current_tracking_prompt_version(),
            "tracking_state_machine_version": STATE_MACHINE_VERSION,
            "rows": [],
            "technical_failures": [],
        }

    replay_origin = min(row.started_signal_date for row in observations)
    trade_dates = [
        row[0]
        for row in (
            db.query(DailyPrice.trade_date)
            .filter(
                DailyPrice.trade_date > replay_origin,
                DailyPrice.trade_date <= end_date,
            )
            .distinct()
            .order_by(DailyPrice.trade_date.asc())
            .all()
        )
    ]
    state = {
        row.id: {
            "status": STATUS_OBSERVING,
            "consecutive_caution_count": 0,
            "stop_reason_code": None,
            # 2026-08-18：composite risk pending 狀態機在 replay 也要走同一套邏輯，
            # 用 in-memory state dict 取代真正的 ORM row（replay 本身是唯讀，不寫回
            # signal_observations，見函式頂端說明）。
            "pending_stop_status": None,
            "pending_stop_reason": None,
            "pending_stop_review_count": 0,
            "pending_stop_trigger_snapshot": None,
        }
        for row in observations
    }
    valid_reviews: Dict[int, List[Dict[str, Any]]] = {
        row.id: [] for row in observations
    }
    output_rows: List[Dict[str, Any]] = []
    technical_failures: List[Dict[str, Any]] = []

    for review_date in trade_dates:
        active = [
            row
            for row in observations
            if row.started_signal_date < review_date
            and state[row.id]["status"] != STATUS_STOPPED
        ]
        if not active:
            continue
        ingestion = candidate_pool.ingest_data(db, review_date)
        momentum_frame = momentum.compute_market_momentum_frame(
            db,
            review_date,
            ingestion.get("stocks_master") or {},
        )
        regime = market_regime.compute_market_regime(db, review_date)
        breadth = market_breadth.compute_breadth_from_frame(
            momentum_frame,
            ingestion.get("stocks_master") or {},
        )
        market_context = llm_caller.assemble_market_context(
            market_snapshot.build_db_market_snapshot(db, review_date)
        )
        market_context.update(
            {
                "market_regime": regime.get("regime"),
                "market_regime_label": regime.get("regime_label"),
                "market_regime_reason": regime.get("reason"),
                "breadth_score": breadth.get("breadth_score"),
            }
        )
        try:
            stress_result = market_stress.compute_market_stress(
                db,
                review_date,
                trend_regime=regime.get("regime"),
                momentum_frame=momentum_frame,
                breadth=breadth,
                taiex_return_1d_pct=(regime.get("metrics") or {}).get(
                    "return_1d_pct"
                ),
            )
        except Exception:
            logger.exception(
                "compute_market_stress failed during replay for %s", review_date
            )
            stress_result = market_stress.empty_market_stress(regime.get("regime"))
        market_context["market_stress"] = stress_result.get("market_stress")
        market_context["effective_market_state"] = stress_result.get(
            "effective_market_state"
        )
        evidence_by_id = build_current_tracking_evidence(
            db,
            observations=active,
            review_date=review_date,
            market_context=market_context,
            ingestion=ingestion,
            momentum_frame=momentum_frame,
            current_candidates=[],
        )
        prompt_payloads = []
        for observation in active:
            evidence = evidence_by_id[observation.id]
            hard = evidence.get("hard_exclusion") or {}
            if _skip_llm_research(hard, evidence):
                continue
            history = valid_reviews[observation.id]
            prompt_payloads.append(
                _tracking_prompt_input(
                    observation,
                    review_date=review_date,
                    evidence=evidence,
                    latest_review=history[-1] if history else None,
                )
            )
        external_by_stock, failures = assessment_runner(prompt_payloads)
        technical_failures.extend(
            {
                **failure,
                "review_date": review_date.isoformat(),
            }
            for failure in failures
        )
        failure_by_stock = {
            str(item.get("stock") or item.get("stock_id") or ""): item
            for item in failures
        }

        for observation in active:
            previous_status = state[observation.id]["status"]
            evidence = evidence_by_id[observation.id]
            external = external_by_stock.get(observation.stock_id)
            decision = decide_observation_action(
                current_backend_evidence=evidence,
                external_thesis_assessment=external,
                latest_valid_reviews=valid_reviews[observation.id],
                current_observation={
                    "status": previous_status,
                    "baseline_quality": observation.baseline_quality,
                    "pending_stop_status": state[observation.id][
                        "pending_stop_status"
                    ],
                    "pending_stop_reason": state[observation.id][
                        "pending_stop_reason"
                    ],
                    "pending_stop_review_count": state[observation.id][
                        "pending_stop_review_count"
                    ],
                    "pending_stop_trigger_snapshot": state[observation.id][
                        "pending_stop_trigger_snapshot"
                    ],
                },
                review_technical_failure=failure_by_stock.get(
                    observation.stock_id
                ),
            )
            _apply_pending_stop_update_to_state(
                state[observation.id], decision=decision, review_date=review_date
            )
            if decision.decision == DECISION_CAUTION:
                state[observation.id]["status"] = STATUS_CAUTION
                state[observation.id]["consecutive_caution_count"] = (
                    _consecutive_caution_count(valid_reviews[observation.id]) + 1
                )
            elif decision.decision == DECISION_CONTINUE:
                state[observation.id]["status"] = STATUS_OBSERVING
                state[observation.id]["consecutive_caution_count"] = 0
            elif decision.decision == DECISION_STOP:
                state[observation.id]["status"] = STATUS_STOPPED
                state[observation.id]["stop_reason_code"] = (
                    decision.reason_codes[0]
                )

            if decision.decision != DECISION_FAILED:
                valid_reviews[observation.id].append(
                    {
                        "date": review_date.isoformat(),
                        "decision": decision.decision,
                        "reason_codes": decision.reason_codes,
                        "caution_dimensions": decision.caution_dimensions,
                        "failed_dimensions": decision.failed_dimensions,
                    }
                )
            if review_date >= start_date:
                output_rows.append(
                    {
                        "review_date": review_date.isoformat(),
                        "stock": observation.stock_id,
                        "episode_id": observation.episode_id,
                        "previous_status": previous_status,
                        "decision": decision.decision,
                        "reason_codes": decision.reason_codes,
                        "caution_dimensions": decision.caution_dimensions,
                        "consecutive_caution_count": state[observation.id][
                            "consecutive_caution_count"
                        ],
                        "stop_reason_code": state[observation.id][
                            "stop_reason_code"
                        ],
                        "tracking_prompt_version": current_tracking_prompt_version(),
                        "tracking_state_machine_version": STATE_MACHINE_VERSION,
                    }
                )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "tracking_prompt_version": current_tracking_prompt_version(),
        "tracking_state_machine_version": STATE_MACHINE_VERSION,
        "rows": output_rows,
        "technical_failures": technical_failures,
    }


def _initial_snapshot_from_recommendation(
    item: Dict[str, Any],
    *,
    signal_date: date,
    prompt_versions: Dict[str, Any],
) -> tuple[Dict[str, Any], str]:
    metrics = item.get("signal_metrics") or {}
    snapshot = {
        "recommendation_date": signal_date.isoformat(),
        "recommendation_rank": item.get("recommendation_rank"),
        "backend_priority_rank": item.get("backend_priority_rank"),
        "recommendation_thesis": item.get("recommendation_thesis"),
        "relative_advantage": item.get("relative_advantage"),
        "instrument_validation": item.get("business_validation"),
        "theme_validation": item.get("theme_validation"),
        "theme_cluster": item.get("theme_cluster"),
        "catalyst_summary": item.get("catalyst_summary"),
        "research_confidence": item.get("research_confidence"),
        "initial_role": item.get("phase2_role"),
        "initial_entry_state": item.get("phase2_entry_state"),
        "initial_freshness": item.get("phase2_momentum_freshness"),
        "initial_watch_quality_state": item.get("phase2_watch_quality_state"),
        "initial_quality_evidence": item.get("quality_evidence") or {},
        "selection_version": item.get("selection_version"),
        "prompt_versions": {
            **prompt_versions,
            "legacy_prompt_version": item.get("prompt_version"),
        },
        "momentum_score_version": metrics.get("momentum_score_version"),
        "baseline_quality": "P3_COMPLETE",
        "missing_fields": [],
    }
    required = (
        "recommendation_thesis",
        "relative_advantage",
        "instrument_validation",
        "theme_validation",
    )
    missing = [key for key in required if not snapshot.get(key)]
    if missing:
        snapshot["baseline_quality"] = "LEGACY_INCOMPLETE"
        snapshot["missing_fields"] = missing
        return snapshot, "LEGACY_INCOMPLETE"
    return snapshot, "P3_COMPLETE"


def _tracking_prompt_input(
    observation: SignalObservation,
    *,
    review_date: date,
    evidence: Dict[str, Any],
    latest_review: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    initial = observation.initial_snapshot_json or {}
    external_initial = {
        key: initial.get(key)
        for key in (
            "recommendation_date",
            "recommendation_thesis",
            "relative_advantage",
            "instrument_validation",
            "theme_validation",
            "theme_cluster",
            "catalyst_summary",
            "research_confidence",
        )
    }
    backend_summary = {
        key: evidence.get(key)
        for key in (
            "tracking_state",
            "entry_state",
            "momentum_freshness",
            "watch_quality_state",
            "market_regime",
            "data_quality",
            # §17：只傳 compact market_environment 摘要（trend_regime/
            # market_stress/effective_market_state/stress_families/
            # 前幾個 reason codes/data_complete），不塞 VIX/Oil/Gold/Futures
            # 原始歷史序列進 prompt。
            "market_environment",
        )
    }
    return {
        "date": review_date.isoformat(),
        "stock": observation.stock_id,
        "name": observation.stock_name,
        "asset_type": observation.asset_type,
        "initial_thesis": external_initial,
        "current_backend_evidence_summary": backend_summary,
        "latest_valid_review": (
            {
                key: latest_review.get(key)
                for key in (
                    "date", "decision", "reason_codes", "caution_dimensions"
                )
            }
            if isinstance(latest_review, dict)
            else None
        ),
    }


def _validate_external_assessment(
    raw: Dict[str, Any],
    *,
    review_date: date,
) -> Dict[str, Any]:
    assessment = str(raw.get("assessment") or "").upper()
    if assessment not in {
        "THESIS_INTACT",
        "THESIS_WEAKENING",
        "THESIS_INVALIDATED",
        "RESEARCH_UNAVAILABLE",
    }:
        raise ValueError("Invalid tracking assessment enum.")
    validations = {
        "instrument_validation": {"VERIFIED", "UNCONFIRMED", "MISMATCH"},
        "theme_validation": {"VERIFIED", "UNCONFIRMED", "MISMATCH"},
        "supply_chain_validation": {
            "VERIFIED",
            "UNCONFIRMED",
            "MISMATCH",
            "NOT_APPLICABLE",
        },
    }
    normalized = dict(raw)
    normalized["assessment"] = assessment
    for field, allowed in validations.items():
        value = str(raw.get(field) or "").upper()
        if value not in allowed:
            raise ValueError(f"Invalid {field}.")
        normalized[field] = value
    raw_dimensions = raw.get("thesis_dimensions")
    if not isinstance(raw_dimensions, dict):
        raise ValueError("Missing thesis_dimensions.")
    dimensions = dict(raw_dimensions)
    allowed_dimension = {"INTACT", "WEAKENING", "INVALIDATED", "UNKNOWN"}
    for field in ("business_or_exposure", "theme", "catalyst"):
        value = str(dimensions.get(field) or "").upper()
        if value not in allowed_dimension:
            raise ValueError(f"Invalid thesis dimension: {field}.")
        dimensions[field] = value
    catalyst_status = str(raw.get("catalyst_status") or "").upper()
    if catalyst_status not in {
        "ACTIVE",
        "WEAKENING",
        "EXPIRED",
        "REPLACED",
        "UNCONFIRMED",
    }:
        raise ValueError("Invalid catalyst_status.")
    normalized["catalyst_status"] = catalyst_status
    normalized["thesis_dimensions"] = dimensions
    if not prompt_family.is_traditional_chinese_text(raw.get("assessment_reason")):
        raise ValueError("assessment_reason must contain Traditional Chinese text.")
    reason_code = str(raw.get("invalidation_reason_code") or "").upper() or None
    if assessment == "THESIS_INVALIDATED":
        if reason_code not in EXTERNAL_INVALIDATION_REASONS:
            raise ValueError("Invalidated thesis requires a legal reason code.")
        if "INVALIDATED" not in {
            str(value or "").upper() for value in dimensions.values()
        }:
            raise ValueError("Invalidated thesis requires an invalidated dimension.")
        required_mismatch_field = {
            "BUSINESS_MISMATCH": "instrument_validation",
            "THEME_MISMATCH": "theme_validation",
            "FALSE_SUPPLY_CHAIN_LINK": "supply_chain_validation",
        }.get(reason_code)
        if (
            required_mismatch_field
            and normalized.get(required_mismatch_field) != "MISMATCH"
        ):
            raise ValueError(
                f"{reason_code} requires {required_mismatch_field}=MISMATCH."
            )
    else:
        if "MISMATCH" in {
            normalized["instrument_validation"],
            normalized["theme_validation"],
            normalized["supply_chain_validation"],
        }:
            raise ValueError("MISMATCH requires THESIS_INVALIDATED.")
        reason_code = None
    normalized["invalidation_reason_code"] = reason_code
    material = raw.get("material_evidence")
    normalized_material: List[Dict[str, Any]] = []
    if isinstance(material, list):
        for item in material:
            if not isinstance(item, dict):
                continue
            published = _parse_iso_date(item.get("published_date"))
            if (
                not item.get("summary")
                or not str(item.get("url") or "").startswith(("http://", "https://"))
                or published is None
                or published > review_date
            ):
                continue
            normalized_material.append(
                {
                    "summary": str(item["summary"])[:1000],
                    "url": str(item["url"]),
                    "published_date": published.isoformat(),
                    "retrieved_date": review_date.isoformat(),
                }
            )
    if (
        assessment == "THESIS_INVALIDATED"
        and reason_code in MATERIAL_EVIDENCE_REASONS
        and not normalized_material
    ):
        raise ValueError("Material/data invalidation requires traceable evidence.")
    normalized["material_evidence"] = normalized_material
    assessment_reason = str(raw.get("assessment_reason") or "").strip()
    if not assessment_reason:
        raise ValueError("Tracking assessment requires assessment_reason.")
    normalized["assessment_reason"] = assessment_reason[:1000]
    return normalized


def _current_caution_evidence(
    backend: Dict[str, Any],
    external: Dict[str, Any],
) -> tuple[List[str], List[str], List[str]]:
    caution: List[str] = []
    failed: List[str] = []
    codes: List[str] = []
    regime = str(backend.get("market_regime") or "").upper()
    tracking = str(backend.get("tracking_state") or "").upper()
    freshness = str(backend.get("momentum_freshness") or "").upper()
    phase = str(backend.get("momentum_phase") or "").lower()
    if tracking == tracking_state.TRACKING_DETERIORATING or freshness == "DETERIORATING" or phase == "weakening":
        caution.append("MOMENTUM_STRUCTURE")
        failed.append("MOMENTUM_STRUCTURE")
        codes.append("MOMENTUM_DETERIORATING")
    elif freshness == "STALE":
        caution.append("MOMENTUM_STRUCTURE")
        codes.append("MOMENTUM_STALE")

    signals = backend.get("deterministic_signals") or {}
    quality = backend.get("quality_evidence") or {}
    participation_flags = [
        signals.get("institution_flow_momentum") == "reversal",
        signals.get("chip_trend") in {"weakening", "distribution"},
        signals.get("sector_rotation_status") == "failed_rotation",
        quality.get("PARTICIPATION") is False,
        quality.get("INSTITUTION_CONFIRMATION") is False,
    ]
    negative_participation = sum(bool(value) for value in participation_flags)
    if negative_participation:
        caution.append("PARTICIPATION")
        codes.append(
            "INSTITUTION_FLOW_REVERSAL_WARNING"
            if signals.get("institution_flow_momentum") == "reversal"
            else "PARTICIPATION_WEAKENING"
        )
        # 2026-08-27：曾經試過 RISK_OFF 期間把這個門檻降到 >=1（見 git 歷史），
        # 但用真實 7/16 資料驗證後發現 `institution_flow_momentum=="reversal"`
        # 在系統性重挫當天幾乎是全市場現象（大盤重挫、法人普遍轉為淨流出，不是
        # 個股特有的惡化訊號），降到 1 旗標幾乎沒有鑑別力，會讓大多數候選同一天
        # 一起被判定失效。已回收，維持原本「至少 2 個負面旗標同時成立」不分 regime。
        if negative_participation >= 2:
            failed.append("PARTICIPATION")

    external_state = str(external.get("assessment") or "").upper()
    catalyst = str(external.get("catalyst_status") or "").upper()
    if external_state == "THESIS_WEAKENING" or catalyst in {
        "WEAKENING",
        "EXPIRED",
        "REPLACED",
    }:
        caution.append("CATALYST_THESIS")
        failed.append("CATALYST_THESIS")
        codes.append("CATALYST_WEAKENING")
    elif catalyst == "UNCONFIRMED":
        caution.append("CATALYST_THESIS")
        codes.append("CATALYST_UNCONFIRMED")

    if regime in {"RISK_OFF", "VOLATILE_RANGE"}:
        caution.append("MARKET_CONTEXT")
        codes.append("MARKET_RISK_ELEVATED")

    persistence = backend.get("persistence_warning") or {}
    if (
        persistence.get("warning")
        or str(persistence.get("state") or "").upper() in {"AT_RISK", "FAILED"}
    ):
        caution.append("PERSISTENCE_WARNING")
        codes.append("PERSISTENCE_WARNING")

    data_quality = backend.get("data_quality") or {}
    if (
        not data_quality.get("price_available", True)
        or not data_quality.get("momentum_frame_available", True)
        or external_state == "RESEARCH_UNAVAILABLE"
    ):
        caution.append("DATA_QUALITY")
        codes.append("DATA_QUALITY_WARNING")
    return caution, failed, codes


def _has_recovery_evidence(
    backend: Dict[str, Any],
    external: Dict[str, Any],
) -> bool:
    tracking = str(backend.get("tracking_state") or "").upper()
    freshness = str(backend.get("momentum_freshness") or "").upper()
    signals = backend.get("deterministic_signals") or {}
    flow = str(signals.get("institution_flow_momentum") or "").lower()
    external_state = str(external.get("assessment") or "").upper()
    return (
        tracking in {
            tracking_state.TRACKING_REACCELERATING,
            tracking_state.TRACKING_HEALTHY_PULLBACK,
        }
        or freshness == "FRESH_STRONG"
    ) and flow in {"stable", "accelerating"} and external_state in {
        "THESIS_INTACT",
        "RESEARCH_UNAVAILABLE",
    }


def _build_composite_risk_trigger_snapshot(
    backend: Dict[str, Any],
) -> Dict[str, Any]:
    """2026-08-18：COMPOSITE_RISK_EXCLUDE 第一次觸發當天存一份快照，讓下一個複核日
    判斷「風險持續」還是「快速收復」時，比較的是觸發當天的原始 K 棒/籌碼，而不是
    每天重新跑一次完全沒有記憶的單日公式（P4 v2 spec §9）。"""
    reversal_check = backend.get("reversal_failure_check") or {}
    risk_flags = backend.get("risk_flags") or []
    return {
        "trigger_date": backend.get("review_date"),
        "open": backend.get("open_1d"),
        "high": backend.get("high_1d"),
        "low": backend.get("low_1d"),
        "close": backend.get("close_1d"),
        "return_1d": backend.get("price_change_1d"),
        "excess_return_vs_market": backend.get("excess_return_vs_market"),
        "institution_flow_1d": (backend.get("institution_flow") or {}).get("day_1"),
        "institution_flow_3d": (backend.get("institution_flow") or {}).get("day_3"),
        "institution_reversal_ratio": reversal_check.get(
            "institution_reversal_ratio"
        ),
        "tracking_state": backend.get("tracking_state"),
        "momentum_freshness": backend.get("momentum_freshness"),
        "momentum_phase": backend.get("momentum_phase"),
        "rs_rank_improvement_5d": backend.get("rs_rank_improvement"),
        "distribution": "distribution" in risk_flags,
        "failed_rotation": "failed_rotation" in risk_flags,
    }


def _has_composite_risk_recovery(
    backend: Dict[str, Any],
    external: Dict[str, Any],
    trigger_snapshot: Optional[Dict[str, Any]],
) -> bool:
    """2026-08-18：COMPOSITE_RISK_EXCLUDE pending 專屬的恢復判斷（P4 v2 spec §11），
    跟既有 `_has_recovery_evidence()` 分開——後者處理的是長期 CAUTION persistence，
    這裡處理的是「前一天疑似假突破/洗盤，隔天是否快速收復」，比較基準是
    `trigger_snapshot`（觸發當天）而非「前一次 review」。

    recovery_score 三項各 1 分，>=2 分且 thesis 未被判定失效才算恢復：
      - PRICE_RECLAIM：收盤站回觸發當天高低點中值，且當日漲幅 >=2% 或相對大盤
        超額報酬 >=1.5%（避免只漲 0.5% 就被當恢復）
      - MOMENTUM_RECOVERY：tracking_state 是 REACCELERATING/HEALTHY_PULLBACK，
        或 momentum_freshness 是 FRESH_STRONG
      - PARTICIPATION_RECOVERY：法人資金動能是 stable/accelerating
    """
    if str(external.get("assessment") or "").upper() == "THESIS_INVALIDATED":
        return False
    if not isinstance(trigger_snapshot, dict):
        return False

    score = 0

    trigger_high = trigger_snapshot.get("high")
    trigger_low = trigger_snapshot.get("low")
    current_close = backend.get("close_1d")
    return_1d = backend.get("price_change_1d")
    excess_return = backend.get("excess_return_vs_market")
    if (
        trigger_high is not None
        and trigger_low is not None
        and current_close is not None
    ):
        midpoint = (trigger_high + trigger_low) / 2.0
        reclaim_strength = (return_1d is not None and return_1d >= 2.0) or (
            excess_return is not None and excess_return >= 1.5
        )
        if current_close >= midpoint and reclaim_strength:
            score += 1

    tracking = str(backend.get("tracking_state") or "").upper()
    freshness = str(backend.get("momentum_freshness") or "").upper()
    if (
        tracking
        in {
            tracking_state.TRACKING_REACCELERATING,
            tracking_state.TRACKING_HEALTHY_PULLBACK,
        }
        or freshness == "FRESH_STRONG"
    ):
        score += 1

    flow_momentum = str(
        (backend.get("deterministic_signals") or {}).get(
            "institution_flow_momentum"
        )
        or ""
    ).lower()
    if flow_momentum in {"stable", "accelerating"}:
        score += 1

    return score >= 2


def _sustained_stop_reason(dimensions: set[str]) -> str:
    if dimensions == {"MOMENTUM_STRUCTURE", "PARTICIPATION"}:
        return "SUSTAINED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    if dimensions == {"MOMENTUM_STRUCTURE", "CATALYST_THESIS"}:
        return "SUSTAINED_MOMENTUM_AND_CATALYST_FAILURE"
    if dimensions == {"PARTICIPATION", "CATALYST_THESIS"}:
        return "SUSTAINED_PARTICIPATION_AND_CATALYST_FAILURE"
    return "SUSTAINED_MULTI_DIMENSION_FAILURE"


def _risk_off_accelerated_stop_reason(dimensions: set[str]) -> str:
    """2026-08-27 方法 A：RISK_OFF 當天加速停止的 reason code，與既有
    `_sustained_stop_reason`（需連續兩天）用不同前綴區分，方便事後歸因統計
    「這次 STOP 是加速觸發還是原本的兩天持續失效觸發」。"""
    if dimensions == {"MOMENTUM_STRUCTURE", "PARTICIPATION"}:
        return "RISK_OFF_ACCELERATED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    if dimensions == {"MOMENTUM_STRUCTURE", "CATALYST_THESIS"}:
        return "RISK_OFF_ACCELERATED_MOMENTUM_AND_CATALYST_FAILURE"
    if dimensions == {"PARTICIPATION", "CATALYST_THESIS"}:
        return "RISK_OFF_ACCELERATED_PARTICIPATION_AND_CATALYST_FAILURE"
    return "RISK_OFF_ACCELERATED_MULTI_DIMENSION_FAILURE"


def _continue_reason_code(
    backend: Dict[str, Any],
    external: Dict[str, Any],
    *,
    recovered: bool,
) -> str:
    if recovered:
        return "RECOVERED_FROM_CAUTION"
    tracking = str(backend.get("tracking_state") or "").upper()
    if tracking == tracking_state.TRACKING_REACCELERATING:
        return "REACCELERATION_CONFIRMED"
    if tracking == tracking_state.TRACKING_HEALTHY_PULLBACK:
        return "HEALTHY_PULLBACK"
    if str(external.get("catalyst_status") or "").upper() == "ACTIVE":
        return "CATALYST_REMAINS_ACTIVE"
    return "THESIS_INTACT"


def _caution_reason(
    dimensions: Sequence[str],
    external: Dict[str, Any],
) -> str:
    external_reason = str(external.get("assessment_reason") or "").strip()
    dimension_text = "、".join(dimensions)
    if external_reason:
        return f"目前 {dimension_text} 出現警戒；{external_reason}"
    return f"目前 {dimension_text} 出現弱化，但尚未達停止觀察條件。"


def _apply_pending_stop_update(
    observation: SignalObservation,
    *,
    decision: ObservationDecision,
    review_date: date,
) -> None:
    """2026-08-18：把 `decide_observation_action()` 回傳的
    `pending_stop_update`（SET／KEEP／CLEAR／None）套到真正的 ORM row 上。
    `pending_stop_since`／`pending_stop_trigger_snapshot` 只在 SET（全新觸發）時
    寫入，KEEP 時刻意不動——recovery 的比較基準要永遠是「原始觸發那天」，不能隨
    每次複核往後挪。"""
    action = decision.pending_stop_update
    if action == "SET":
        observation.pending_stop_status = PENDING_STOP_STATUS_ACTIVE
        observation.pending_stop_reason = decision.pending_stop_reason
        observation.pending_stop_since = review_date
        observation.pending_stop_trigger_snapshot = (
            decision.pending_stop_trigger_snapshot
        )
        observation.pending_stop_review_count = 1
    elif action == "KEEP":
        observation.pending_stop_review_count = (
            observation.pending_stop_review_count or 0
        ) + 1
    elif action == "CLEAR":
        observation.pending_stop_status = None
        observation.pending_stop_reason = None
        observation.pending_stop_since = None
        observation.pending_stop_trigger_snapshot = None
        observation.pending_stop_review_count = 0


def _apply_pending_stop_update_to_state(
    entry: Dict[str, Any],
    *,
    decision: ObservationDecision,
    review_date: date,
) -> None:
    """`_apply_pending_stop_update()` 的 in-memory 版本，給唯讀 replay 用
    （`replay_observation_lifecycle` 不寫回真正的 SignalObservation row，狀態全部
    活在 `state` dict 裡）。"""
    action = decision.pending_stop_update
    if action == "SET":
        entry["pending_stop_status"] = PENDING_STOP_STATUS_ACTIVE
        entry["pending_stop_reason"] = decision.pending_stop_reason
        entry["pending_stop_since"] = review_date
        entry["pending_stop_trigger_snapshot"] = decision.pending_stop_trigger_snapshot
        entry["pending_stop_review_count"] = 1
    elif action == "KEEP":
        entry["pending_stop_review_count"] = (
            entry.get("pending_stop_review_count") or 0
        ) + 1
    elif action == "CLEAR":
        entry["pending_stop_status"] = None
        entry["pending_stop_reason"] = None
        entry["pending_stop_since"] = None
        entry["pending_stop_trigger_snapshot"] = None
        entry["pending_stop_review_count"] = 0


def _upsert_review(
    db: Session,
    *,
    observation: SignalObservation,
    review_date: date,
    decision: ObservationDecision,
    backend_evidence: Dict[str, Any],
    external_assessment: Optional[Dict[str, Any]],
    market_context: Dict[str, Any],
    existing: Optional[SignalObservationReview] = None,
) -> SignalObservationReview:
    row = existing
    if row is None:
        row = SignalObservationReview(
            observation_id=observation.id,
            review_date=review_date,
            decision=decision.decision,
            reason_codes=[],
            reason="",
            caution_dimensions=[],
            failed_dimensions=[],
            prompt_version=current_tracking_prompt_version(),
            state_machine_version=STATE_MACHINE_VERSION,
        )
        db.add(row)
    row.decision = decision.decision
    row.reason_codes = decision.reason_codes
    row.reason = decision.reason
    row.caution_dimensions = decision.caution_dimensions
    row.failed_dimensions = decision.failed_dimensions
    evidence_with_prompt = dict(backend_evidence)
    evidence_with_prompt["_prompt_metadata"] = prompt_family.prompt_metadata()
    row.backend_evidence_json = evidence_with_prompt
    row.external_assessment_json = external_assessment
    row.market_context_json = market_context
    row.persistence_warning_json = backend_evidence.get("persistence_warning") or {}
    row.technical_status = decision.technical_status
    row.momentum_score = backend_evidence.get("momentum_score")
    row.prompt_version = current_tracking_prompt_version()
    row.state_machine_version = STATE_MACHINE_VERSION
    row.updated_at = datetime.utcnow()
    return row


def _load_episode_returns(
    db: Session,
    *,
    observations: Sequence[SignalObservation],
    review_date: date,
) -> Dict[int, Dict[str, Optional[float]]]:
    if not observations:
        return {}
    stock_ids = sorted({row.stock_id for row in observations})
    first_date = min(row.started_signal_date for row in observations)
    price_rows = (
        db.query(DailyPrice)
        .filter(
            DailyPrice.stock_id.in_(stock_ids),
            DailyPrice.trade_date >= first_date,
            DailyPrice.trade_date <= review_date,
        )
        .order_by(DailyPrice.stock_id.asc(), DailyPrice.trade_date.asc())
        .all()
    )
    by_stock: Dict[str, List[DailyPrice]] = {}
    for row in price_rows:
        by_stock.setdefault(row.stock_id, []).append(row)
    output: Dict[int, Dict[str, Optional[float]]] = {}
    for observation in observations:
        rows = [
            row
            for row in by_stock.get(observation.stock_id, [])
            if row.trade_date >= observation.started_signal_date
            and row.close_price is not None
        ]
        baseline = rows[0].close_price if rows else None
        values: Dict[str, Optional[float]] = {}
        for day in (1, 3, 5, 10):
            target = rows[day] if len(rows) > day else None
            values[f"day_{day}_return_pct"] = (
                round((target.close_price - baseline) / baseline * 100, 4)
                if target is not None and baseline
                else None
            )
        output[observation.id] = values
    return output


def _load_latest_hit_metrics(
    db: Session,
    stock_ids: Sequence[str],
    review_date: date,
) -> Dict[str, Dict[str, Any]]:
    rows = (
        db.query(SignalWatchHit)
        .filter(
            SignalWatchHit.stock_id.in_(list(stock_ids)),
            SignalWatchHit.snapshot_date <= review_date,
        )
        .order_by(
            SignalWatchHit.stock_id.asc(),
            SignalWatchHit.snapshot_date.desc(),
        )
        .all()
    )
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        output.setdefault(row.stock_id, dict(row.signal_metrics or {}))
    return output


def _consecutive_caution_count(reviews: Sequence[Dict[str, Any]]) -> int:
    count = 0
    for review in reviews:
        decision = str(review.get("decision") or "").upper()
        if decision == DECISION_FAILED:
            continue
        if decision == DECISION_CAUTION:
            count += 1
        else:
            count = 0
    return count


def _review_to_state_dict(row: SignalObservationReview) -> Dict[str, Any]:
    return {
        "date": row.review_date.isoformat(),
        "decision": row.decision,
        "reason_codes": row.reason_codes or [],
        "caution_dimensions": row.caution_dimensions or [],
        "failed_dimensions": row.failed_dimensions or [],
    }


def _review_failure(
    stock_id: str,
    status: str,
    message: str,
    *,
    diagnostic: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "stock": stock_id,
        "stock_id": stock_id,
        "stage": "TRACKING",
        "status": status,
        "processing_status": "REVIEW_FAILED",
        "error_code": status,
        "error_summary": str(message)[:500],
        "diagnostic": diagnostic or {},
    }


def _serialize_observation(
    row: SignalObservation,
    *,
    latest_review: Optional[SignalObservationReview],
    recommended_today: bool,
) -> Dict[str, Any]:
    return {
        "id": row.id,
        "stock": row.stock_id,
        "name": row.stock_name,
        "asset_type": row.asset_type,
        "episode_id": row.episode_id,
        "status": row.status,
        "started_at": row.started_at,
        "started_signal_date": row.started_signal_date,
        "last_review_date": row.last_review_date,
        "latest_decision": row.latest_decision,
        "consecutive_caution_count": row.consecutive_caution_count,
        "latest_reason_codes": (
            latest_review.reason_codes if latest_review is not None else []
        ),
        "latest_reason": (
            latest_review.reason if latest_review is not None else None
        ),
        "latest_review_technical_status": (
            latest_review.technical_status if latest_review is not None else None
        ),
        "stopped_at": row.stopped_at,
        "stop_reason_code": row.stop_reason_code,
        "stop_reason": row.stop_reason,
        "baseline_quality": row.baseline_quality,
        "selection_version": row.selection_version,
        "recommended_today": recommended_today,
    }


def _serialize_review(row: SignalObservationReview) -> Dict[str, Any]:
    backend_evidence = row.backend_evidence_json or {}
    metadata = (
        backend_evidence.get("_prompt_metadata")
        if isinstance(backend_evidence, dict)
        else None
    ) or {}
    return {
        "review_date": row.review_date,
        "decision": row.decision,
        "reason_codes": row.reason_codes or [],
        "reason": row.reason,
        "caution_dimensions": row.caution_dimensions or [],
        "failed_dimensions": row.failed_dimensions or [],
        "backend_evidence": backend_evidence,
        "external_assessment": row.external_assessment_json,
        "market_context": row.market_context_json or {},
        "persistence_warning": row.persistence_warning_json or {},
        "technical_status": row.technical_status,
        "tracking_prompt_version": row.prompt_version,
        "tracking_state_machine_version": row.state_machine_version,
        "prompt_family_version": metadata.get("prompt_family_version"),
        "shared_policy_version": metadata.get("shared_policy_version"),
        "assembled_prompt_sha256": (
            (metadata.get("prompt_sha256") or {}).get("tracking")
            or metadata.get("assembled_prompt_sha256")
        ),
    }


def _latest_reviews(
    db: Session,
    observation_ids: Sequence[int],
) -> Dict[int, SignalObservationReview]:
    if not observation_ids:
        return {}
    rows = (
        db.query(SignalObservationReview)
        .filter(SignalObservationReview.observation_id.in_(list(observation_ids)))
        .order_by(
            SignalObservationReview.observation_id.asc(),
            SignalObservationReview.review_date.desc(),
        )
        .all()
    )
    output: Dict[int, SignalObservationReview] = {}
    for row in rows:
        output.setdefault(row.observation_id, row)
    return output


def _recommended_stock_ids(
    db: Session,
    *,
    as_of_date: Optional[date],
) -> tuple[Optional[date], set[str]]:
    target = as_of_date or db.query(func.max(SignalSnapshot.snapshot_date)).scalar()
    if target is None:
        return None, set()
    snapshot = (
        db.query(SignalSnapshot)
        .filter(SignalSnapshot.snapshot_date == target)
        .one_or_none()
    )
    if snapshot is None:
        return target, set()
    return target, {
        str(item.get("stock") or item.get("stock_id") or "")
        for item in (snapshot.watchlist or [])
        if item.get("stock") or item.get("stock_id")
    }


def _parse_iso_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _dedupe(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))
