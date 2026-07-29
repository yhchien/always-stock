"""P3 point-in-time research + global recommendation replay.

對指定歷史交易日重建候選池（deterministic，跟 `run_phase2_replay.py` 相同手法），
**真的呼叫 OpenAI**（research v6.1 + P3 selector），但只把結果寫進本機
scratch 檔案，**完全不寫入** `signal_snapshots` / `signal_watch_hits` 等 production
表——用來驗證 v6 prompt 是否真的照 spec 運作，不會產生任何 production 副作用。

用法：
    python run_v6_llm_validation.py 2026-07-20 --out /tmp/v6_validation_0720.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from typing import Optional

from app.database import SessionLocal
from app.signals import candidate_pool, global_selector, llm_caller, market_breadth, market_regime, market_snapshot, momentum, prompt_family
from app.signals import pipeline as pipeline_mod
from app.signals.phase2 import pipeline_v2


def _rebuild_shared_inputs(db, target_date: date):
    ingestion = candidate_pool.ingest_data(db, target_date)
    rankings = candidate_pool.compute_rankings(db, target_date, ingestion)
    momentum_frame = momentum.compute_market_momentum_frame(
        db, target_date, ingestion.get("stocks_master") or {}
    )
    pool = candidate_pool.build_candidate_pool(
        db, target_date, ingestion, rankings, momentum_frame=momentum_frame
    )
    if not pool:
        raise ValueError(f"no candidate stocks for target_date={target_date}")

    regime_info = market_regime.compute_market_regime(db, target_date)
    breadth = market_breadth.compute_breadth_from_frame(momentum_frame, ingestion.get("stocks_master") or {})
    regime_detail = market_breadth.resolve_regime_detail(regime_info["regime"], breadth.get("breadth_score"))
    regime_info = {**regime_info, "regime_detail": regime_detail, "breadth_score": breadth.get("breadth_score")}
    return pool, regime_info


def run_validation(
    target_date: date,
    out_path: str,
    *,
    prompt_family_name: Optional[str] = None,
) -> dict:
    family = prompt_family.resolve_prompt_family(prompt_family_name)
    previous_family = os.environ.get("SIGNALS_PROMPT_FAMILY")
    os.environ["SIGNALS_PROMPT_FAMILY"] = family
    db = SessionLocal()
    try:
        pool, regime_info = _rebuild_shared_inputs(db, target_date)
        print(f"{target_date} | regime={regime_info['regime']} | raw_pool={len(pool)}")

        taiex_return_1d_pct = (regime_info.get("metrics") or {}).get("return_1d_pct")
        hard_excluded: list = []
        phase2_pool = pipeline_v2.build_phase2_pool(
            pool, taiex_return_1d_pct=taiex_return_1d_pct, excluded_out=hard_excluded
        )
        result = pipeline_v2.run_phase2_pipeline(
            db, phase2_pool, regime_info["regime"],
            hard_excluded=hard_excluded, taiex_return_1d_pct=taiex_return_1d_pct,
        )
        survivors = result["survivors"]
        print(f"  hard_exclusion 後: {len(phase2_pool)} 檔 | regime gate 後存活: {len(survivors)} 檔")
        print(f"  hard_exclusion_reason_counts: {result['funnel_metrics']['hard_exclusion_reason_counts']}")

        for c in survivors:
            c["prelim_type"] = pipeline_v2.role_to_prelim_type(c)
            c["regime_conviction"] = c.get("conviction")
        after_regime = pipeline_mod._order_llm_input(survivors)
        print(f"  送進 LLM（完整召回、僅排序）: {len(after_regime)} 檔")
        asset_types = {c.get("stock_id"): c.get("asset_type") for c in after_regime}
        print(f"  asset_type 分布: { {v: list(asset_types.values()).count(v) for v in set(asset_types.values())} }")

        db_market_snapshot = market_snapshot.build_db_market_snapshot(db, target_date)
        market_context = llm_caller.assemble_market_context(db_market_snapshot)
        market_context["market_regime"] = regime_info["regime"]
        market_context["market_regime_label"] = regime_info["regime_label"]
        market_context["market_regime_reason"] = regime_info["reason"]
        market_context["target_date"] = target_date.isoformat()

        print("  → 呼叫 OpenAI research batch...")
        research_batches = [
            after_regime[i:i + llm_caller.DEFAULT_RESEARCH_BATCH_SIZE]
            for i in range(0, len(after_regime), llm_caller.DEFAULT_RESEARCH_BATCH_SIZE)
        ]
        research_results = []
        for batch in research_batches:
            research_results.extend(llm_caller.run_research_batch(batch, market_context))

        print("  → 呼叫 OpenAI eligibility/veto assessment batch...")
        assessments = llm_caller.run_explanation_batch(research_results, market_context)
        eligible, removed = global_selector.partition_assessments(assessments)
        cards = global_selector.build_compact_selection_cards(
            eligible,
            selection_date=target_date,
        )
        print(
            f"  → 全體 selector 一次比較 {len(cards)} 檔 "
            f"(selection_version={prompt_family.stage_version('global_selector', family)})..."
        )
        selection = global_selector.run_global_selection(
            cards,
            market_context,
            selection_date=target_date,
        )
        selected = global_selector.merge_selection_items(eligible, selection)
        recommend_items = [
            item for item in selected
            if str(item.get("decision") or "").upper() == "RECOMMEND"
        ]
        print(f"  → RECOMMEND {len(recommend_items)} 檔，呼叫 OpenAI reason batch...")
        enriched_watch = llm_caller.run_watch_reason_batch(recommend_items, market_context)
        if enriched_watch:
            watch_by_id = {str(item.get("stock") or ""): item for item in enriched_watch}
            merged = []
            for item in selected:
                sid = str(item.get("stock") or "")
                merged.append({**item, **watch_by_id[sid]} if sid in watch_by_id else item)
            selected = merged

        explanation = [*selected, *removed]

        final_payload = llm_caller.assemble_final_output(
            market_context, explanation, candidate_pool_size=len(pool)
        )

        # v6 contract 驗證重點摘要（不影響任何 production 表，純印出/存檔）
        backend_remove_but_llm_watch = [
            e for e in assessments
            if e.get("backend_max_decision") == "REMOVE" and str(e.get("decision")).upper() == "WATCH"
        ]
        print(f"  ⚠️ backend_max_decision=REMOVE 但最終仍是 WATCH 的筆數（應該永遠是 0）: {len(backend_remove_but_llm_watch)}")

        veto_breakdown: dict = {}
        for e in removed:
            if str(e.get("decision") or "").upper() == "REMOVE":
                reason = e.get("veto_reason") or "(none)"
                veto_breakdown[reason] = veto_breakdown.get(reason, 0) + 1
        print(f"  REMOVE veto_reason 分布: {veto_breakdown}")

        print(
            f"  最終 RECOMMEND={len(final_payload['watchlist'])} "
            f"NOT_SELECTED={len(final_payload['not_selected'])} REMOVE={len(final_payload['removed'])} "
            f"research_prompt={final_payload['prompt_version']} "
            f"selection_version={prompt_family.stage_version('global_selector', family)}"
        )
        for item in final_payload["watchlist"]:
            print(
                f"    {item['stock']} {item.get('name')} | type={item['type']} asset_type={item.get('asset_type')} "
                f"| business_validation={item.get('business_validation')} theme_validation={item.get('theme_validation')} "
                f"supply_chain_validation={item.get('supply_chain_validation')} | veto_reason={item.get('veto_reason')}"
            )

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "target_date": target_date.isoformat(),
                    "regime": regime_info["regime"],
                    "raw_pool_size": len(pool),
                    "phase2_survivor_count": len(survivors),
                    "llm_input_count": len(after_regime),
                    **prompt_family.prompt_metadata(family),
                    "outcome_used_in_decision": False,
                    "backend_remove_but_llm_watch_count": len(backend_remove_but_llm_watch),
                    "veto_breakdown": veto_breakdown,
                    "compact_selection_cards": cards,
                    "selection": selection,
                    "final_payload": final_payload,
                    "assessment_all": assessments,
                },
                f,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        print(f"  已寫入 {out_path}（不影響任何 production 表）")
        return final_payload
    finally:
        db.close()
        if previous_family is None:
            os.environ.pop("SIGNALS_PROMPT_FAMILY", None)
        else:
            os.environ["SIGNALS_PROMPT_FAMILY"] = previous_family


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target_date", type=str)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument(
        "--prompt-family",
        choices=("v7", "legacy_split"),
        default="v7",
    )
    args = parser.parse_args()
    target_date = datetime.strptime(args.target_date, "%Y-%m-%d").date()
    out_path = args.out or f"/tmp/v6_validation_{args.target_date}.json"
    run_validation(
        target_date,
        out_path,
        prompt_family_name=args.prompt_family,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
