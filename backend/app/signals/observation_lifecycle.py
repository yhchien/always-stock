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
    momentum,
    prompt_family,
)
from app.signals.phase2 import entry_state
from app.signals.phase2 import momentum_freshness
from app.signals.phase2 import regime_gate
from app.signals.phase2 import tracking_state
from app.signals.phase2 import watch_quality


TRACKING_PROMPT_VERSION = "v7_tracking"
STATE_MACHINE_VERSION = "p4_state_v1"
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
IMMEDIATE_HARD_REASONS = {
    "MANUAL_BLACKLIST",
    "FAILED_FOLLOW_THROUGH_CURRENT_EPISODE",
    "STRUCTURE_DAMAGED",
    "LIQUIDITY_FAILURE",
    "COMPOSITE_RISK_EXCLUDE",
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

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "tracking-review-v1.md"
)
_PROMPT_CACHE_KEY = "signals:p4:tracking-review:v1"


def current_tracking_prompt_version(family: Optional[str] = None) -> str:
    return prompt_family.stage_version("tracking", family)


@dataclass(frozen=True)
class ObservationDecision:
    decision: str
    reason_codes: List[str]
    reason: str
    caution_dimensions: List[str]
    failed_dimensions: List[str]
    technical_status: Optional[str] = None

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
        industry_name = (
            master.industry_name
            if master is not None
            else current_candidate.get("industry")
        )
        candidate = {
            "stock_id": sid,
            "name": observation.stock_name,
            "industry": industry_name,
            "sub_industry": (
                master.sub_industry
                if master is not None
                else current_candidate.get("sub_industry")
            ),
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
                    candidate_pool._normalized_industry(industry_name)
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
            if sid not in expected or sid in seen:
                if sid in expected:
                    failures.append(
                        _review_failure(
                            sid,
                            "TRACKING_OUTPUT_ALIGNMENT_FAILED",
                            "Duplicate tracking assessment.",
                        )
                    )
                    seen.add(sid)
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
                retry_diagnostic: Dict[str, Any] = diagnostic
                validated_via_retry: Optional[Dict[str, Any]] = None
                max_contract_retries = 2
                previous_exc = exc
                if retry_enabled and sid in source_by_stock:
                    for contract_attempt in range(1, max_contract_retries + 1):
                        try:
                            retry_response, retry_diagnostic, _ = call_tracking(
                                [source_by_stock[sid]],
                                contract_retry={
                                    "previous_rejection": str(previous_exc)[:1000],
                                    "required_correction": (
                                        "只重做這一檔並修正契約錯誤。若要使用 "
                                        "MATERIAL_NEGATIVE_EVENT 或 DATA_CONTRADICTION "
                                        "判定 THESIS_INVALIDATED，material_evidence 必須有"
                                        "截至 review_date 可追溯的 summary、URL、"
                                        "published_date；否則不得宣告失效，應依證據改為 "
                                        "THESIS_WEAKENING 或 RESEARCH_UNAVAILABLE，且 "
                                        "invalidation_reason_code 必須為 null。不可捏造來源。"
                                    ),
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
                                validated_via_retry = _validate_external_assessment(
                                    retry_raw,
                                    review_date=date.fromisoformat(review_date),
                                )
                                validated_via_retry["_prompt_metadata"] = {
                                    **metadata,
                                    "stage_prompt_version": tracking_version,
                                    "assembled_prompt_sha256": metadata[
                                        "prompt_sha256"
                                    ]["tracking"],
                                }
                                validated_via_retry["_llm_diagnostic"] = {
                                    **retry_diagnostic,
                                    "contract_retry_attempt": contract_attempt,
                                    "previous_contract_error": str(previous_exc)[:500],
                                }
                                break
                        except ValueError as retry_value_exc:
                            previous_exc = retry_value_exc
                            continue
                        except Exception as retry_exc:
                            retry_diagnostic = {
                                **retry_diagnostic,
                                "retry_exception": str(retry_exc)[:500],
                            }
                            break
                if validated_via_retry is not None:
                    successful[sid] = validated_via_retry
                    continue
                failures.append(
                    _review_failure(
                        sid,
                        "TRACKING_OUTPUT_INVALID",
                        str(previous_exc),
                        diagnostic=(
                            retry_diagnostic
                            if retry_enabled
                            else diagnostic
                        ),
                    )
                )
        for sid in sorted(expected - seen):
            failures.append(
                _review_failure(
                    sid,
                    "TRACKING_OUTPUT_ALIGNMENT_FAILED",
                    "Tracking assessment omitted the stock.",
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
    if hard.get("excluded") and hard_reason in IMMEDIATE_HARD_REASONS:
        return ObservationDecision(
            decision=DECISION_STOP,
            reason_codes=[hard_reason],
            reason=f"Backend 已確認 {hard_reason}，本 observation thesis 明確失效。",
            caution_dimensions=[],
            failed_dimensions=["MOMENTUM_STRUCTURE"],
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

    if not recovery and not baseline_incomplete and prior_decision == DECISION_CAUTION:
        prior_failed = {
            str(value)
            for value in (prior.get("failed_dimensions") or [])
            if value in CORE_DIMENSIONS
        }
        current_failed = {
            value for value in failed_dimensions if value in CORE_DIMENSIONS
        }
        persistent = prior_failed & current_failed
        if (
            len(persistent) >= 2
            and persistent & {"MOMENTUM_STRUCTURE", "PARTICIPATION"}
        ):
            return ObservationDecision(
                decision=DECISION_STOP,
                reason_codes=[_sustained_stop_reason(persistent)],
                reason="連續兩次成功交易日 Review 顯示多個核心維度持續失效，且未見有效恢復。",
                caution_dimensions=sorted(set(caution_dimensions)),
                failed_dimensions=sorted(current_failed),
            )

    caution_dimensions = sorted(set(caution_dimensions))
    failed_dimensions = sorted(set(failed_dimensions))
    reason_codes = _dedupe(reason_codes)
    if caution_dimensions:
        if len(set(failed_dimensions) & CORE_DIMENSIONS) >= 2:
            reason_codes.append("MULTI_DIMENSION_EARLY_WARNING")
        return ObservationDecision(
            decision=DECISION_CAUTION,
            reason_codes=_dedupe(reason_codes),
            reason=_caution_reason(caution_dimensions, external),
            caution_dimensions=caution_dimensions,
            failed_dimensions=failed_dimensions,
        )

    continue_code = _continue_reason_code(
        current_backend_evidence,
        external,
        recovered=prior_decision == DECISION_CAUTION and recovery,
    )
    return ObservationDecision(
        decision=DECISION_CONTINUE,
        reason_codes=[continue_code],
        reason="原始推薦 thesis 與目前動能／參與結構仍有效，繼續觀察。",
        caution_dimensions=[],
        failed_dimensions=[],
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
        if hard.get("excluded") or (
            evidence.get("tracking_state")
            == tracking_state.TRACKING_INVALIDATED
        ):
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
            },
            review_technical_failure=failure,
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
                # P4 確認停止觀察＝這檔的推薦論點已判定失效；魚尾（M23 signal_watch_hits）
                # 的追蹤週期跟著結算，不用再等自然滿 30 天或價格觸發的提前結算規則。
                # 找不到對應的魚尾進行中週期時是 no-op（兩套系統的候選範圍不保證完全重疊）。
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
            if hard.get("excluded") or (
                evidence.get("tracking_state")
                == tracking_state.TRACKING_INVALIDATED
            ):
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
                },
                review_technical_failure=failure_by_stock.get(
                    observation.stock_id
                ),
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

    regime = str(backend.get("market_regime") or "").upper()
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


def _sustained_stop_reason(dimensions: set[str]) -> str:
    if dimensions == {"MOMENTUM_STRUCTURE", "PARTICIPATION"}:
        return "SUSTAINED_MOMENTUM_AND_PARTICIPATION_FAILURE"
    if dimensions == {"MOMENTUM_STRUCTURE", "CATALYST_THESIS"}:
        return "SUSTAINED_MOMENTUM_AND_CATALYST_FAILURE"
    if dimensions == {"PARTICIPATION", "CATALYST_THESIS"}:
        return "SUSTAINED_PARTICIPATION_AND_CATALYST_FAILURE"
    return "SUSTAINED_MULTI_DIMENSION_FAILURE"


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
