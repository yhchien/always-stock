"""Phase 4A Step A: freeze prior evidence and candidate strategies.

This process intentionally reads only Phase 3 reports and repository metadata.
It must not load the Phase 4 daily cohort or any forward outcome.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "plans" / "phase4"
LEDGER_CSV = OUTPUT_DIR / "phase4_experiment_ledger.csv"
LEDGER_MD = OUTPUT_DIR / "phase4_experiment_ledger.md"
MANIFEST_JSON = OUTPUT_DIR / "phase4_strategy_manifest.json"
MANIFEST_SHA = OUTPUT_DIR / "phase4_strategy_manifest.sha256"

LEDGER_FIELDS = [
    "experiment_id",
    "experiment_date",
    "hypothesis",
    "research_target",
    "cohort_definition",
    "market_regime",
    "sample_size",
    "outcome_horizon",
    "tested_rule",
    "main_result",
    "winner_effect",
    "big_loser_effect",
    "mean_return_effect",
    "robustness_result",
    "original_conclusion",
    "status",
    "eligible_for_phase4",
    "allowed_usage",
    "source_files",
]

REPORTS = [
    "docs/plans/phase3a_persistence_actionability_report.md",
    "docs/plans/phase3b_candidate_admission_report.md",
    "docs/plans/phase3c_riskoff_survival_report.md",
    "docs/plans/phase3d_candidate_discovery_report.md",
    "docs/plans/phase3e_downtrend_report.md",
    "docs/plans/phase3f_v2/phase3f_v2_report.md",
    "docs/plans/phase3g/phase3g_report.md",
    "docs/plans/phase3h/phase3h_report.md",
    "docs/plans/phase3i/phase3i_final_decision.md",
]


def row(
    experiment_id: str,
    experiment_date: str,
    hypothesis: str,
    research_target: str,
    cohort_definition: str,
    market_regime: str,
    sample_size: str,
    outcome_horizon: str,
    tested_rule: str,
    main_result: str,
    winner_effect: str,
    big_loser_effect: str,
    mean_return_effect: str,
    robustness_result: str,
    original_conclusion: str,
    status: str,
    eligible_for_phase4: bool,
    allowed_usage: str,
    source_files: str,
) -> dict[str, Any]:
    return {name: value for name, value in zip(LEDGER_FIELDS, locals().values())}


def build_ledger() -> list[dict[str, Any]]:
    p3a = REPORTS[0]
    p3b = REPORTS[1]
    p3c = REPORTS[2]
    p3d = REPORTS[3]
    p3e = REPORTS[4]
    p3f = REPORTS[5]
    p3g = REPORTS[6]
    p3h = REPORTS[7]
    p3i = REPORTS[8]
    return [
        row(
            "P3A_PERSISTENCE_DIRECT_EXIT",
            "2026-07-24",
            "Persistent risk states can become an economically useful automatic exit.",
            "Exit actionability",
            "Phase 2.7 trajectory observations with consecutive AT_RISK/FAILED states",
            "MIXED",
            "Rule-level trajectory cohort; see source report",
            "remaining forward return after trigger",
            "Rule A AT_RISK>=3; Rule B FAILED>=2; Rule C FAILED>=3",
            "Losers sometimes lost less, but winners lost more upside; no rule improved all left-tail thresholds.",
            "Winner opportunity cost exceeded loser savings.",
            "No consistent improvement across -10%/-15%/-20% tails.",
            "Rule B mean delta -0.40pp with fully negative CI; Rule C near flat.",
            "Date bootstrap and time split did not establish positive economic value.",
            "SHADOW_ONLY",
            "SHADOW_ONLY",
            False,
            "SHADOW_INFORMATION",
            p3a,
        ),
        row(
            "P3B_SOURCE_GROUP_AUTO_REMOVAL",
            "2026-07-27",
            "A noisy admission source can be removed while retaining nearly all winners.",
            "Candidate-source compression",
            "Daily source combinations in the reconstructed candidate cohort",
            "MIXED",
            "Source-combination cohort; see source report",
            "Day10",
            "Programmatic removal simulation over A/B/C source groups",
            "No source group passed the removal criteria; apparently risky groups also contained many winners.",
            "Removal would sacrifice a disproportionate number of winners.",
            "No qualifying big-loser removal group.",
            "No validated positive mean-return effect.",
            "Daily/time split did not rescue a removal rule.",
            "STOP",
            "REJECT",
            False,
            "NONE",
            p3b,
        ),
        row(
            "P3B_AC_SOURCE_EFFICIENCY",
            "2026-07-27",
            "Source combination A+C may be a more efficient candidate context.",
            "Admission attribution",
            "A/B/C source combinations in the same reconstructed cohort",
            "MIXED",
            "AC subgroup; see source report",
            "Day10",
            "Descriptive comparison of source combination A+C",
            "A+C had the best candidates-per-winner and big-losers-per-winner, but the analysis was descriptive and non-causal.",
            "Directionally favorable winner efficiency.",
            "Directionally favorable loser efficiency.",
            "Not established as an independent mean-return rule.",
            "Insufficient for an automatic removal or ranking rule.",
            "Positive descriptive finding outside the STOP conclusion",
            "WEAK",
            False,
            "TIE_BREAK|SHADOW_INFORMATION",
            p3b,
        ),
        row(
            "P3C_RISKOFF_FAILURE_VETO",
            "2026-07-27",
            "Day1 failure archetypes can veto weak-market candidates safely.",
            "Weak-market precision",
            "159 historical RISK_OFF candidates across 6 dates",
            "RISK_OFF",
            "159",
            "Day10",
            "Failure archetypes F1-F5 and veto Rules A/B/C",
            "No archetype or veto was stable; some negative-looking archetypes contained many winners.",
            "Rules failed the >=95% winner-retention requirement.",
            "Rules failed the >=25% big-loser-removal requirement.",
            "No stable improvement.",
            "Six-date historical check rejected the single-day impression.",
            "Failure Archetype / Veto STOP",
            "REJECT",
            False,
            "NONE",
            p3c,
        ),
        row(
            "P3C_CANDIDATE_DISCOVERY_RECALL",
            "2026-07-27",
            "Missed strong stocks are mainly lost before the publishing layer.",
            "Candidate discovery recall",
            "2026-07-23 WATCH plus TOP70-eligible miss audit",
            "RISK_OFF",
            "20 selected; 63 missed-strong",
            "Day1 forensic",
            "Stage-level miss attribution",
            "Most misses occurred in candidate discovery, but no safe compression rule was produced.",
            "Recall diagnosis only.",
            "Not a loser-removal rule.",
            "Not assessed.",
            "Useful pipeline diagnosis, not an automatic candidate compression predicate.",
            "RECALL_FIX",
            "SHADOW_ONLY",
            False,
            "SHADOW_INFORMATION",
            p3c,
        ),
        row(
            "P3D_MOMENTUM_RANK_GRADIENT",
            "2026-07-27",
            "Existing momentum ordering contains repeatable winner enrichment.",
            "Ranking value",
            "8,321 matured raw-union daily events across 20 replay dates",
            "BULL_TREND|VOLATILE_RANGE|RISK_OFF",
            "8321",
            "Day10",
            "Existing momentum_score order; Top120 vs 121-200 and adjacent rank buckets",
            "Winner rate declined monotonically from 31.2% in rank 1-40 to 8.2% in rank 501+; Top120 beat 121-200 by 12.0pp.",
            "Strong positive winner enrichment in earlier ranks.",
            "Big-loser rate was also higher in earlier ranks, so rank is not a safe hard filter by itself.",
            "Mean and median return declined with rank.",
            "Date-block bootstrap confirmed the Top120 winner-rate difference; adjacent buckets showed the same direction.",
            "NO_ACTIONABLE_FIX; accept current truncation ordering",
            "PASS",
            True,
            "RANK",
            p3d,
        ),
        row(
            "P3D_FIXED_SIZE_REPLACEMENT",
            "2026-07-27",
            "Replacing Top120 rows with near-miss candidates can improve winner recall without adding losers.",
            "Candidate replacement",
            "20 replay dates with raw union and Top120",
            "BULL_TREND|VOLATILE_RANGE|RISK_OFF",
            "3 predeclared simulations",
            "Day10",
            "Replacement simulations A/B/C at fixed size 120",
            "All three simulations reduced winner count.",
            "Negative.",
            "No compensating validated loser benefit.",
            "No validated improvement.",
            "Direction agreed with the monotonic rank gradient.",
            "NO_ACTIONABLE_FIX",
            "REJECT",
            False,
            "NONE",
            p3d,
        ),
        row(
            "P3D_FUNDAMENTAL_CHANNEL_D",
            "2026-07-27",
            "Fundamental channel D can contribute candidate evidence.",
            "Admission channel health",
            "20-day reconstructed raw union",
            "MIXED",
            "0 D hits",
            "Day10",
            "Existing D-channel rule",
            "Upstream revenue coverage was incomplete; the rule could not be evaluated.",
            "Unknown.",
            "Unknown.",
            "Unknown.",
            "Data gap prevented validation.",
            "D_DATA_GAP",
            "INCOMPLETE",
            False,
            "NONE",
            p3d,
        ),
        row(
            "P3E_RISKOFF_EXTREME_ACCELERATION_FILTER",
            "2026-07-28",
            "Extreme rank acceleration can remove weak-market losers.",
            "Downtrend loser control",
            "Frozen A=246 and stress B=87; stress B has one winner",
            "RISK_OFF",
            "A=246; B=87; B winner=1; B dates=6",
            "Day10",
            "Exclude rs_rank_improvement_5d >= 600",
            "Reached the loser-control goal but not winner dominance; short stress window and one winner.",
            "Winner safety could not be established in the stress cohort.",
            "Directionally reduced losers under stress.",
            "Not established for bull-market returns.",
            "Provisional six-date stress result only.",
            "LOSER_CONTROL_ONLY",
            "WEAK",
            False,
            "SHADOW_INFORMATION",
            p3e,
        ),
        row(
            "P3F_REGIME_ADAPTIVE_CONTRACTION",
            "2026-07-28",
            "Market-aware publishing contraction can control weak-market losers.",
            "Regime-adaptive contraction",
            "Frozen A=246, B=87, C=140 reconstructed episodes",
            "RISK_OFF|WEAKENING",
            "A=246; B=87; C=140",
            "Day10",
            "Frozen market, pocket, and stock-survival policy family",
            "Policy family reduced stress losers, but winner evidence was insufficient and later attribution removed pocket increment.",
            "Normal/stress winner evidence insufficient.",
            "Positive weak-market loser-control direction.",
            "Not a bull mean-return result.",
            "Reconstructed, not production exact; superseded by Phase 3G attribution.",
            "LOSER_CONTROL_ONLY",
            "WEAK",
            False,
            "SHADOW_INFORMATION",
            f"{p3f}|{p3g}",
        ),
        row(
            "P3G_STOCK_SURVIVAL_GATE",
            "2026-07-28",
            "Stock survival evidence is the primary source of stress loser control.",
            "Policy attribution",
            "Frozen A=246 and B=87",
            "RISK_OFF",
            "B selected=36 across 6 dates",
            "Day10",
            "Frozen survival predicate",
            "B loser rate fell from 44.8% to 25.0%, but safe rate was 75.0% and A winner recall only 27.3%.",
            "Removed 16 of 22 A winners.",
            "Directionally positive stress loser control.",
            "Not validated for bull-market mean return.",
            "Wilson interval wide; B had one winner and six dates.",
            "PROVISIONAL_SURVIVAL_SIGNAL",
            "WEAK",
            False,
            "SHADOW_INFORMATION",
            p3g,
        ),
        row(
            "P3G_MARKET_PLUS_SURVIVAL",
            "2026-07-28",
            "Market contraction adds protection above stock survival.",
            "Policy attribution",
            "Frozen A=246 and B=87",
            "RISK_OFF",
            "B selected=33 across 6 dates",
            "Day10",
            "Frozen market gate followed by survival gate",
            "B loser rate improved another 6.8pp to 18.2%, while A changed only from 15.2% to 16.2%.",
            "Stress cohort had only one winner; bull winner retention not established.",
            "Market increment confirmed for stress loser control.",
            "Not a bull-market mean-return result.",
            "Short stress window; later Phase 3I remained sample-pending.",
            "MARKET_INCREMENT_CONFIRMED / LOSER_CONTROL_ONLY",
            "WEAK",
            False,
            "SHADOW_INFORMATION",
            f"{p3g}|{p3i}",
        ),
        row(
            "P3G_POCKET_INCREMENT",
            "2026-07-28",
            "Momentum pocket activity/durability adds value beyond survival.",
            "Pocket attribution",
            "Frozen A=246 and B=87",
            "MIXED",
            "A=246; B=87",
            "Day10",
            "Pocket-only, pocket+survival, and durability quadrants",
            "Pocket+survival did not beat survival; directions were inconsistent across A/B.",
            "No incremental winner protection.",
            "No incremental loser removal.",
            "No incremental return evidence.",
            "Later attribution directly rejected the earlier pocket interpretation.",
            "NO_POCKET_INCREMENT",
            "REJECT",
            False,
            "NONE",
            p3g,
        ),
        row(
            "P3G_EARLY_MARKET_TRANSITION",
            "2026-07-28",
            "A point-in-time composite can warn at least two sessions before the 7/8 deterioration.",
            "Market transition timing",
            "Pre-July false-alarm audit plus Dataset B",
            "MIXED",
            "B selected=50",
            "Day10",
            "Two-session persistent PROTECTIVE composite",
            "Persistent signal was only one session earlier; false alarms remained and B loser rate was 40%.",
            "A winner recall only 59.1%.",
            "No adequate protection.",
            "No positive return evidence.",
            "Point-in-time replay explicitly failed the timing requirement.",
            "NO_EARLY_SIGNAL",
            "REJECT",
            False,
            "NONE",
            p3g,
        ),
        row(
            "P3G_POINT_IN_TIME_WATCHLIST",
            "2026-07-28",
            "Point-in-time lifecycle warnings/removals can become automatic actions.",
            "Watchlist actionability",
            "2,038 point-in-time valid action rows",
            "MIXED",
            "2038",
            "forward action-date outcome",
            "WARNING and two-family persistent REMOVE",
            "Warnings caught 67.1% of losers but prematurely warned 67.0% of winners; removals were also premature for 21.4% of winners.",
            "Unacceptably high premature warning/removal.",
            "Some early loser information.",
            "No actionable economic value established.",
            "Corrected the older outcome leakage; all rows were point-in-time valid.",
            "NO_ACTIONABLE_SIGNAL",
            "SHADOW_ONLY",
            False,
            "SHADOW_INFORMATION",
            p3g,
        ),
        row(
            "P3H_DAY0_POSITIVE_BUNDLES",
            "2026-07-28",
            "Fixed Day0 positive-structure bundles enrich normal-regime winners safely.",
            "Bull Day0 compression",
            "168 completed NORMAL episodes",
            "NORMAL",
            "168; winners=15",
            "Day10 from entry",
            "D0-A, D0-B, D0-C fixed bundles",
            "All Day0 bundles had loser rate above 20%; the nominally best dominance rule was not qualified.",
            "Winner recall/dominance trade-off failed the formal gate.",
            "Failed the required loser-rate gate.",
            "No qualified mean-return rule.",
            "Identification/locked split did not establish a qualifying Day0 filter.",
            "No qualified Day0 policy",
            "REJECT",
            False,
            "NONE",
            p3h,
        ),
        row(
            "P3H_DAY1_DAY3_CONFIRMATION",
            "2026-07-28",
            "Follow-through confirmation enriches normal-regime winners after entry.",
            "Confirmation timing",
            "168 completed NORMAL episodes plus locked evaluation slice",
            "NORMAL",
            "Best historical policy n=25; locked winner=1",
            "Day10 after confirmation and remaining episode horizon",
            "Fixed Day1 and Day3 confirmation bundles",
            "Historical Day3-A reached 25% winner dominance, but locked evaluation missed its sole winner; confirmation also incurred delay.",
            "Provisional enrichment only; independent winner retention unconfirmed.",
            "Not a validated Day0 loser-removal rule.",
            "No independently confirmed return protection.",
            "Small samples and locked slice failed to confirm generalization.",
            "PROVISIONAL_WINNER_ENRICHMENT",
            "WEAK",
            False,
            "TIE_BREAK|SHADOW_INFORMATION",
            p3h,
        ),
        row(
            "P3H_FIXED_TOPK",
            "2026-07-28",
            "A fixed momentum Top-K is a validated normal-regime compression threshold.",
            "Top-K baseline",
            "NORMAL comparison slices",
            "NORMAL",
            "Descriptive Top-K comparators",
            "Day10",
            "Top-K momentum baselines matched to policy coverage",
            "Top-K was only a comparator; no fixed K passed a predeclared compression gate.",
            "Not established.",
            "Not established.",
            "Not established.",
            "No independent fixed-K validation.",
            "Descriptive comparator only",
            "SHADOW_ONLY",
            False,
            "SHADOW_INFORMATION",
            p3h,
        ),
        row(
            "P3I_NORMAL_FROZEN_BRANCH",
            "2026-07-28",
            "Frozen NORMAL Day1-C generalizes prospectively.",
            "Independent shadow validation",
            "Frozen Dataset C plus prospective rows",
            "NORMAL",
            "eligible=0; prospective=0",
            "Day10",
            "Frozen D0-A then Day1-C branch",
            "No independent NORMAL sample was available.",
            "Unknown.",
            "Unknown.",
            "Unknown.",
            "Predicate reproduced with zero mismatches, but outcome validation is pending.",
            "NORMAL_PENDING_SAMPLE",
            "INCOMPLETE",
            False,
            "NONE",
            p3i,
        ),
        row(
            "P3I_RISK_FROZEN_BRANCH",
            "2026-07-28",
            "Frozen market+survival generalizes on new weak-market data.",
            "Independent shadow validation",
            "Dataset C existing pending plus prospective rows",
            "RISK_OFF",
            "selected=29; matured selected=5 from one date; prospective=0",
            "Day10",
            "Frozen market+survival branch",
            "Early read was favorable but came from one matured date and no prospective rows.",
            "Only one matured winner.",
            "Zero matured selected losers, early-read only.",
            "Early-read only.",
            "Predicate reproduced with zero mismatches; minimum sample not reached and input snapshot blocker remained.",
            "RISK_PENDING_SAMPLE",
            "INCOMPLETE",
            False,
            "NONE",
            p3i,
        ),
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_ledger(rows: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    statuses = ["PASS", "WEAK", "SHADOW_ONLY", "REJECT", "INCOMPLETE"]
    counts = {status: sum(r["status"] == status for r in rows) for status in statuses}
    lines = [
        "# Phase 4 Experiment Ledger",
        "",
        "> Strategy-freeze input only. This ledger was created without loading the Phase 4 daily cohort or any Phase 4 future outcome.",
        "",
        "## Status summary",
        "",
        "| status | count |",
        "|---|---:|",
        *[f"| {status} | {counts[status]} |" for status in statuses],
        "",
        "## Corrected chronology",
        "",
        "| date | experiment_id | status | Phase 4 eligible | allowed usage | main result |",
        "|---|---|---|---:|---|---|",
    ]
    for item in rows:
        result = str(item["main_result"]).replace("|", "/")
        lines.append(
            f"| {item['experiment_date']} | {item['experiment_id']} | "
            f"{item['status']} | {str(item['eligible_for_phase4']).lower()} | "
            f"{item['allowed_usage']} | {result} |"
        )
    lines.extend(
        [
            "",
            "## Chronology resolution",
            "",
            "- Phase 3G supersedes Phase 3F pocket attribution: pocket evidence is `REJECT`, not `WEAK`.",
            "- Phase 3G point-in-time replay supersedes the leaked Phase 3F lifecycle interpretation; lifecycle remains `SHADOW_ONLY`.",
            "- Phase 3I does not upgrade Phase 3H or Phase 3G: both frozen branches are `INCOMPLETE` because independent samples are absent or immature.",
            "- The only `PASS` evidence eligible for automatic Phase 4 use is the existing momentum ordering from Phase 3D, and its allowed usage is `RANK`, never `FILTER`.",
            "",
            "The full machine-readable fields, including cohort, effects, robustness, and source files, are in `phase4_experiment_ledger.csv`.",
            "",
        ]
    )
    LEDGER_MD.write_text("\n".join(lines), encoding="utf-8")


def build_manifest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    report_hashes = {path: sha256_file(ROOT / path) for path in REPORTS}
    eligible = [
        {
            "experiment_id": item["experiment_id"],
            "status": item["status"],
            "allowed_usage": item["allowed_usage"].split("|"),
            "tested_rule": item["tested_rule"],
        }
        for item in rows
        if item["status"] == "PASS" and item["eligible_for_phase4"]
    ]
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "UNKNOWN"
    return {
        "manifest_version": "phase4a_strategy_freeze_v1",
        "frozen_on": "2026-07-29",
        "frozen_from_commit": commit,
        "research_scope": "BULL_MARKET_DAILY_CANDIDATE_COMPRESSION",
        "outcome_access_during_freeze": False,
        "step_a_allowed_inputs": [
            "Phase 3A-3I reports",
            "prior experiment conclusions",
            "feature definitions",
            "production candidate schema",
        ],
        "step_a_prohibited_inputs": [
            "Phase 4 Day10 return",
            "Phase 4 outcome label",
            "Phase 4 future MFE/MAE",
            "any Phase 4 future outcome",
        ],
        "source_report_sha256": report_hashes,
        "ledger_sha256": sha256_file(LEDGER_CSV),
        "eligible_evidence": eligible,
        "daily_cohort_contract": {
            "unit": "daily_candidate_event_no_stock_deduplication",
            "source_snapshot": "/tmp/phase3d_candidate_union_all.json",
            "baseline_predicate": "selected_top120 == true",
            "bull_regime_enum": "BULL_TREND",
            "trade_date_field": "evaluation_date",
            "stock_id_field": "stock_id",
            "original_candidate_order_field": "raw_union_rank",
            "candidate_source_fields": ["source_A", "source_B", "source_C", "source_D"],
            "sector_field": None,
            "sector_missing_policy": "null; source snapshot has no sector field",
            "outcome_field_loaded_only_in_step_b": "forward_return_10d",
        },
        "fixed_outcome_definition": {
            "WINNER": "day10_return >= 10.0",
            "BIG_LOSER": "day10_return <= -10.0",
            "NEUTRAL": "-10.0 < day10_return < 10.0",
        },
        "strategies": [
            {
                "strategy_name": "S0_BASELINE",
                "strategy_role": "BASELINE",
                "availability": "AVAILABLE",
                "exact_rules": ["keep every baseline daily candidate event"],
                "exact_rule_order": [],
                "required_features": [],
                "ordering": [
                    "original_candidate_order ascending",
                    "stock_id ascending",
                ],
                "natural_compression_only": True,
            },
            {
                "strategy_name": "S1_VALIDATED_CONSERVATIVE",
                "strategy_role": "CHALLENGER",
                "availability": "NOT_AVAILABLE",
                "unavailable_reason": "No PASS, bull-eligible evidence permits FILTER usage.",
                "exact_rules": [],
                "exact_rule_order": [],
                "required_features": [],
                "natural_compression_only": True,
            },
            {
                "strategy_name": "S2_VALIDATED_RANKING",
                "strategy_role": "CHALLENGER",
                "availability": "AVAILABLE",
                "evidence_ids": ["P3D_MOMENTUM_RANK_GRADIENT"],
                "exact_rules": [
                    "keep every baseline daily candidate event",
                    "rank by the existing production/replay candidate order",
                    "do not apply a fixed Top-K because no fixed K is PASS evidence",
                ],
                "exact_rule_order": [
                    "original_candidate_order ascending",
                    "stock_id ascending",
                ],
                "required_features": ["original_candidate_order"],
                "natural_compression_only": True,
                "expected_mechanical_effect": "ordering_only_no_forced_removal",
            },
            {
                "strategy_name": "S3_VALIDATED_HYBRID",
                "strategy_role": "CHALLENGER",
                "availability": "NOT_AVAILABLE",
                "unavailable_reason": "S1 is unavailable; hybrid requires both S1 and S2.",
                "exact_rules": [],
                "exact_rule_order": [],
                "required_features": [],
                "natural_compression_only": True,
            },
        ],
        "selection_gates": [
            {"gate": 1, "metric": "winner_retention_rate", "operator": ">=", "threshold": 0.95},
            {
                "gate": 2,
                "metric": "mean_return_delta_percentage_point",
                "operator": ">=",
                "threshold": -0.10,
                "additional_rule": "trading-date bootstrap 95% CI must not be clearly negative",
            },
            {"gate": 3, "metric": "big_loser_removal_rate", "operator": ">=", "threshold": 0.30},
            {"gate": 4, "metric": "compression_rate", "operator": ">=", "threshold": 0.20},
        ],
        "holdout_gates": [
            {"metric": "winner_retention_rate", "operator": ">=", "threshold": 0.95},
            {
                "metric": "mean_return_delta_percentage_point",
                "operator": ">=",
                "threshold": -0.10,
            },
            {"metric": "big_loser_removal_rate", "operator": ">=", "threshold": 0.25},
            {"metric": "compression_rate", "operator": ">=", "threshold": 0.15},
        ],
        "forbidden_adaptations": [
            "new feature",
            "new weighted score",
            "threshold search",
            "fixed Top-K without PASS evidence",
            "stock-specific exception",
            "outcome-driven strategy modification",
        ],
    }


def main() -> None:
    rows = build_ledger()
    allowed_statuses = {"PASS", "WEAK", "SHADOW_ONLY", "REJECT", "INCOMPLETE"}
    assert {item["status"] for item in rows} <= allowed_statuses
    assert all(
        item["status"] == "PASS"
        for item in rows
        if item["eligible_for_phase4"]
    )
    write_ledger(rows)
    manifest = build_manifest(rows)
    MANIFEST_JSON.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    digest = sha256_file(MANIFEST_JSON)
    MANIFEST_SHA.write_text(
        f"{digest}  {MANIFEST_JSON.name}\n", encoding="utf-8"
    )
    print(f"wrote {LEDGER_CSV.relative_to(ROOT)}")
    print(f"wrote {LEDGER_MD.relative_to(ROOT)}")
    print(f"wrote {MANIFEST_JSON.relative_to(ROOT)}")
    print(f"strategy manifest sha256={digest}")


if __name__ == "__main__":
    main()
