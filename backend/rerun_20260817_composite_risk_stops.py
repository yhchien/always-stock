"""一次性腳本（2026-08-19）：用新版 P4 Observation Lifecycle v2（COMPOSITE_RISK_EXCLUDE
假突破防誤殺）重新判定 2026-08-17 當天被舊邏輯誤判 STOP_OBSERVING 的觀察。

背景：2026-08-17 當天有 6 檔股票（2357/2376/3605/3661/8039/9921）的既有觀察被舊版
`decide_observation_action()` 判定 STOP_OBSERVING，理由都是 COMPOSITE_RISK_EXCLUDE
（出貨 K 棒 + 法人單日反轉同時出現）。新版邏輯把這個理由從立即失效改成「待確認」
pending 狀態機——這支腳本用新邏輯重新判定這 6 檔在 8/17 當天「本來應該」得到的結果，
覆蓋掉舊的判定紀錄（使用者已確認可以覆蓋）。

只動這 6 檔股票，範圍精確對應這次修復的 bug（不影響同一天用其他理由（如
SUSTAINED_MOMENTUM_AND_CATALYST_FAILURE）被判 STOP 的股票，那些理由完全不受這次
修改影響）。

因為魚尾追蹤週期的結算是延後一個複核日才做（`_settle_pending_p4_fishtail_stops`），
而 8/17 之後到現在都還沒有任何一次每日複核跑過，這 6 檔的 `signal_watch_hits` 從未
被結算移出——不需要額外復原魚尾資料，只需要修正 P4 觀察本身的狀態。

做法：
    1. 重用當天已經存下來的 `backend_evidence_json`／`market_context_json`
       （`build_current_tracking_evidence()` 當時算出來的結果，不受這次修 bug 影響，
       重算一次全市場 momentum frame 沒有必要也更貴），只額外補上新版邏輯需要、當時
       還沒有的欄位（當日 OHLC／`price_change_1d`／`excess_return_vs_market`／
       `reversal_failure_check`）。
    2. 用新版邏輯，這幾檔的 hard_exclusion 理由是 COMPOSITE_RISK_EXCLUDE 時
       LLM tracking-review 不該再被跳過（見 `_skip_llm_research` 的修正）——這支腳本
       會對需要的股票發一次真正的 LLM 查證（`run_tracking_assessments`，會呼叫
       OpenAI，6 檔以內一批，成本很小）。
    3. 呼叫 `decide_observation_action()` 重新判定，套用回 `SignalObservation`
       （狀態／pending_stop_*／stop_confirm_count 等），覆蓋既有的 2026-08-17
       `SignalObservationReview` 那一列，並刪除因誤判而產生的
       `SignalObservationArchive` 紀錄。

用法：
    python3 rerun_20260817_composite_risk_stops.py           # dry-run，只印出結果
    python3 rerun_20260817_composite_risk_stops.py --execute # 真的寫入
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Optional

from app.database import SessionLocal
from app.models import (
    DailyPrice,
    SignalObservation,
    SignalObservationArchive,
    SignalObservationReview,
)
from app.signals import observation_lifecycle as lifecycle
from app.signals.phase2 import regime_gate

REVIEW_DATE = date(2026, 8, 17)
TARGET_REASON = "COMPOSITE_RISK_EXCLUDE"


def _find_target_observations(db) -> list[SignalObservation]:
    return (
        db.query(SignalObservation)
        .filter(
            SignalObservation.status == "STOPPED",
            SignalObservation.stop_reason_code == TARGET_REASON,
        )
        .join(
            SignalObservationArchive,
            SignalObservationArchive.observation_id == SignalObservation.id,
        )
        .filter(SignalObservationArchive.archived_date == REVIEW_DATE)
        .order_by(SignalObservation.stock_id)
        .all()
    )


def _close_on(db, stock_id: str, trade_date: date) -> Optional[float]:
    row = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == trade_date)
        .one_or_none()
    )
    return float(row.close_price) if row is not None and row.close_price is not None else None


def _augment_evidence(db, evidence: dict[str, Any], stock_id: str, taiex_return_1d: Optional[float]) -> dict[str, Any]:
    price_row = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date == REVIEW_DATE)
        .one_or_none()
    )
    prev_row = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.trade_date < REVIEW_DATE)
        .order_by(DailyPrice.trade_date.desc())
        .first()
    )
    open_1d = float(price_row.open_price) if price_row and price_row.open_price is not None else None
    high_1d = float(price_row.high_price) if price_row and price_row.high_price is not None else None
    low_1d = float(price_row.low_price) if price_row and price_row.low_price is not None else None
    close_1d = float(price_row.close_price) if price_row and price_row.close_price is not None else None
    prev_close = float(prev_row.close_price) if prev_row and prev_row.close_price is not None else None

    price_change_1d = None
    if close_1d is not None and prev_close not in (None, 0):
        price_change_1d = (close_1d - prev_close) / prev_close * 100.0

    excess_return_vs_market = None
    if price_change_1d is not None and taiex_return_1d is not None:
        excess_return_vs_market = price_change_1d - taiex_return_1d

    institution_flow = evidence.get("institution_flow") or {}
    volume_ratios = evidence.get("volume_ratios") or {}
    candidate = {
        "stock_id": stock_id,
        "total_institution_flow_1d": institution_flow.get("day_1"),
        "total_institution_flow_3d": institution_flow.get("day_3"),
        "price_change_1d": price_change_1d,
        "entry_state": evidence.get("entry_state"),
        "volume_1d_to_5d_ratio": volume_ratios.get("volume_1d_to_5d"),
    }
    reversal_check = regime_gate._is_reversal_failure(candidate, taiex_return_1d)

    augmented = dict(evidence)
    augmented["open_1d"] = open_1d
    augmented["high_1d"] = high_1d
    augmented["low_1d"] = low_1d
    augmented["close_1d"] = close_1d
    augmented["price_change_1d"] = price_change_1d
    augmented["excess_return_vs_market"] = excess_return_vs_market
    augmented["reversal_failure_check"] = reversal_check
    return augmented


def _prior_reviews(db, observation_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(SignalObservationReview)
        .filter(
            SignalObservationReview.observation_id == observation_id,
            SignalObservationReview.review_date < REVIEW_DATE,
            SignalObservationReview.decision != "REVIEW_FAILED",
        )
        .order_by(SignalObservationReview.review_date.asc())
        .all()
    )
    return [lifecycle._review_to_state_dict(row) for row in rows]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="真的寫入（預設 dry-run）")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        taiex_prev = _close_on(db, "TAIEX", date(2026, 8, 14))
        taiex_today = _close_on(db, "TAIEX", REVIEW_DATE)
        taiex_return_1d = None
        if taiex_prev not in (None, 0) and taiex_today is not None:
            taiex_return_1d = (taiex_today - taiex_prev) / taiex_prev * 100.0
        print(f"TAIEX 8/17 return_1d = {taiex_return_1d}\n")

        observations = _find_target_observations(db)
        print(f"找到 {len(observations)} 檔待重新判定：{[o.stock_id for o in observations]}\n")

        existing_review_by_obs: dict[int, SignalObservationReview] = {}
        evidence_by_obs: dict[int, dict[str, Any]] = {}
        prior_by_obs: dict[int, list[dict[str, Any]]] = {}
        payloads = []

        for obs in observations:
            review = (
                db.query(SignalObservationReview)
                .filter(
                    SignalObservationReview.observation_id == obs.id,
                    SignalObservationReview.review_date == REVIEW_DATE,
                )
                .one()
            )
            existing_review_by_obs[obs.id] = review
            evidence = _augment_evidence(
                db, review.backend_evidence_json or {}, obs.stock_id, taiex_return_1d
            )
            evidence_by_obs[obs.id] = evidence
            prior_by_obs[obs.id] = _prior_reviews(db, obs.id)

            hard = evidence.get("hard_exclusion") or {}
            if not lifecycle._skip_llm_research(hard, evidence):
                payloads.append(
                    lifecycle._tracking_prompt_input(
                        obs,
                        review_date=REVIEW_DATE,
                        evidence=evidence,
                        latest_review=(
                            prior_by_obs[obs.id][-1] if prior_by_obs[obs.id] else None
                        ),
                    )
                )

        print(f"需要重新查證外部事實（LLM）：{[p['stock'] for p in payloads]}\n")
        external_by_stock: dict[str, dict[str, Any]] = {}
        if payloads:
            external_by_stock, failures = lifecycle.run_tracking_assessments(payloads)
            if failures:
                print("!! LLM 查證有失敗項目：", failures)

        for obs in observations:
            evidence = evidence_by_obs[obs.id]
            external = external_by_stock.get(obs.stock_id)
            prior_reviews = prior_by_obs[obs.id]
            decision = lifecycle.decide_observation_action(
                current_backend_evidence=evidence,
                external_thesis_assessment=external,
                latest_valid_reviews=prior_reviews,
                current_observation={
                    "status": obs.status,
                    "baseline_quality": obs.baseline_quality,
                    "pending_stop_status": None,
                    "pending_stop_reason": None,
                    "pending_stop_review_count": 0,
                    "pending_stop_trigger_snapshot": None,
                },
                review_technical_failure=None,
            )
            print(
                f"[{obs.stock_id}] {obs.stock_name}: "
                f"{decision.decision} reason={decision.reason_codes} "
                f"pending_update={decision.pending_stop_update}"
            )

            if not args.execute:
                continue

            previous_caution_count = lifecycle._consecutive_caution_count(
                prior_reviews
            )
            lifecycle._apply_pending_stop_update(
                obs, decision=decision, review_date=REVIEW_DATE
            )
            if decision.decision == "CAUTION":
                obs.status = "CAUTION"
                obs.consecutive_caution_count = previous_caution_count + 1
                obs.latest_decision = "CAUTION"
                obs.last_review_date = REVIEW_DATE
                obs.stop_confirm_count = 0
                obs.stopped_at = None
                obs.stop_reason_code = None
                obs.stop_reason = None
            elif decision.decision == "CONTINUE":
                obs.status = "OBSERVING"
                obs.consecutive_caution_count = 0
                obs.latest_decision = "CONTINUE"
                obs.last_review_date = REVIEW_DATE
                obs.stopped_at = None
                obs.stop_reason_code = None
                obs.stop_reason = None
                obs.stop_confirm_count = 0
            else:  # STOP_OBSERVING（理論上只有 masked-reversal 等真正立即失效才會走到）
                obs.status = "STOPPED"
                obs.latest_decision = "STOP_OBSERVING"
                obs.last_review_date = REVIEW_DATE
                obs.stop_reason_code = decision.reason_codes[0]
                obs.stop_reason = decision.reason
                obs.stopped_at = None
                obs.stop_confirm_count = 1

            existing_review = existing_review_by_obs[obs.id]
            lifecycle._upsert_review(
                db,
                observation=obs,
                review_date=REVIEW_DATE,
                decision=decision,
                backend_evidence=evidence,
                external_assessment=external,
                market_context=existing_review.market_context_json or {},
                existing=existing_review,
            )

            if decision.decision != "STOP_OBSERVING":
                db.query(SignalObservationArchive).filter(
                    SignalObservationArchive.observation_id == obs.id,
                    SignalObservationArchive.archived_date == REVIEW_DATE,
                ).delete(synchronize_session=False)

            db.commit()
            print("  -> 已寫入")

        if not args.execute:
            print("\n（dry-run，未寫入。加 --execute 才會真的寫進 DB，且會真的呼叫 OpenAI。）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
