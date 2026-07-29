"""Phase 4A Step B1: development-only outcome validation.

The strategy and data-split hashes are verified before the replay snapshot is
loaded.  Only development-date rows have their forward returns accessed.  A
holdout evaluator is intentionally not invoked unless a development Champion
exists.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "plans" / "phase4"
MANIFEST_JSON = OUTPUT_DIR / "phase4_strategy_manifest.json"
MANIFEST_SHA = OUTPUT_DIR / "phase4_strategy_manifest.sha256"
SPLIT_JSON = OUTPUT_DIR / "phase4_data_split.json"
SPLIT_SHA = OUTPUT_DIR / "phase4_data_split.sha256"
LEDGER_CSV = OUTPUT_DIR / "phase4_experiment_ledger.csv"
DEVELOPMENT_ALL = OUTPUT_DIR / "phase4_development_all.csv"
DEVELOPMENT_DAILY = OUTPUT_DIR / "phase4_development_daily.csv"
DEVELOPMENT_SUMMARY = OUTPUT_DIR / "phase4_development_summary.csv"
REMOVED_WINNERS = OUTPUT_DIR / "phase4_removed_winners.csv"
REMOVED_BIG_LOSERS = OUTPUT_DIR / "phase4_removed_big_losers.csv"
REMOVED_NEUTRALS = OUTPUT_DIR / "phase4_removed_neutrals.csv"
REPORT = OUTPUT_DIR / "phase4_strategy_convergence_report.md"

CHAMPION_JSON = OUTPUT_DIR / "phase4_champion_manifest.json"
CHAMPION_SHA = OUTPUT_DIR / "phase4_champion_manifest.sha256"
HOLDOUT_FILES = [
    OUTPUT_DIR / "phase4_holdout_all.csv",
    OUTPUT_DIR / "phase4_holdout_daily.csv",
    OUTPUT_DIR / "phase4_holdout_summary.csv",
]

BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 20260729

DAILY_COLUMNS = [
    "trade_date",
    "strategy_name",
    "original_count",
    "retained_count",
    "removed_count",
    "original_winners",
    "retained_winners",
    "removed_winners",
    "original_big_losers",
    "retained_big_losers",
    "removed_big_losers",
    "winner_retention_rate",
    "big_loser_removal_rate",
    "compression_rate",
    "baseline_mean_return",
    "strategy_mean_return",
    "mean_return_delta",
]

SUMMARY_COLUMNS = [
    "strategy_name",
    "strategy_role",
    "availability",
    "original_candidate_count",
    "retained_candidate_count",
    "removed_candidate_count",
    "compression_rate",
    "compression_rate_ci_low",
    "compression_rate_ci_high",
    "baseline_winner_count",
    "retained_winner_count",
    "removed_winner_count",
    "winner_retention_rate",
    "winner_retention_rate_ci_low",
    "winner_retention_rate_ci_high",
    "baseline_big_loser_count",
    "retained_big_loser_count",
    "removed_big_loser_count",
    "big_loser_removal_rate",
    "big_loser_removal_rate_ci_low",
    "big_loser_removal_rate_ci_high",
    "baseline_neutral_count",
    "retained_neutral_count",
    "removed_neutral_count",
    "neutral_removal_rate",
    "baseline_mean_day10_return",
    "strategy_mean_day10_return",
    "mean_return_delta",
    "mean_return_delta_ci_low",
    "mean_return_delta_ci_high",
    "baseline_median_day10_return",
    "strategy_median_day10_return",
    "median_return_delta",
    "full_winner_retention_date_rate",
    "removed_winner_date_rate",
    "big_loser_removal_date_rate",
    "max_removed_winners_single_date",
    "max_compression_single_date",
    "date_dominance_result",
    "gate_1_winner_retention",
    "gate_2_mean_return_protection",
    "gate_3_big_loser_removal",
    "gate_4_compression",
    "development_decision",
]

REMOVED_COLUMNS = [
    "trade_date",
    "stock_id",
    "strategy_name",
    "removal_reason",
    "original_candidate_order",
    "day10_return",
    "outcome_group",
    "primary_sector",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, hash_path: Path) -> str:
    expected = hash_path.read_text(encoding="utf-8").split()[0]
    actual = sha256_file(path)
    if actual != expected:
        raise RuntimeError(
            f"hash mismatch for {path.name}: expected={expected}, actual={actual}"
        )
    return actual


def outcome_group(value: float) -> str:
    if value >= 10.0:
        return "WINNER"
    if value <= -10.0:
        return "BIG_LOSER"
    return "NEUTRAL"


def safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else math.nan


def q(values: Iterable[float], quantile: float) -> float:
    clean = np.asarray([v for v in values if not math.isnan(v)], dtype=float)
    return float(np.quantile(clean, quantile)) if len(clean) else math.nan


def metric_values(frame: pd.DataFrame, keep_col: str) -> dict[str, float | int]:
    kept = frame[frame[keep_col]]
    original = len(frame)
    retained = len(kept)
    winners = int((frame.outcome_group == "WINNER").sum())
    retained_winners = int((kept.outcome_group == "WINNER").sum())
    losers = int((frame.outcome_group == "BIG_LOSER").sum())
    retained_losers = int((kept.outcome_group == "BIG_LOSER").sum())
    neutrals = int((frame.outcome_group == "NEUTRAL").sum())
    retained_neutrals = int((kept.outcome_group == "NEUTRAL").sum())
    baseline_mean = float(frame.day10_return.mean())
    strategy_mean = float(kept.day10_return.mean()) if retained else math.nan
    baseline_median = float(frame.day10_return.median())
    strategy_median = float(kept.day10_return.median()) if retained else math.nan
    return {
        "original_candidate_count": original,
        "retained_candidate_count": retained,
        "removed_candidate_count": original - retained,
        "compression_rate": safe_rate(original - retained, original),
        "baseline_winner_count": winners,
        "retained_winner_count": retained_winners,
        "removed_winner_count": winners - retained_winners,
        "winner_retention_rate": safe_rate(retained_winners, winners),
        "baseline_big_loser_count": losers,
        "retained_big_loser_count": retained_losers,
        "removed_big_loser_count": losers - retained_losers,
        "big_loser_removal_rate": safe_rate(losers - retained_losers, losers),
        "baseline_neutral_count": neutrals,
        "retained_neutral_count": retained_neutrals,
        "removed_neutral_count": neutrals - retained_neutrals,
        "neutral_removal_rate": safe_rate(neutrals - retained_neutrals, neutrals),
        "baseline_mean_day10_return": baseline_mean,
        "strategy_mean_day10_return": strategy_mean,
        "mean_return_delta": strategy_mean - baseline_mean,
        "baseline_median_day10_return": baseline_median,
        "strategy_median_day10_return": strategy_median,
        "median_return_delta": strategy_median - baseline_median,
    }


def daily_metrics(
    frame: pd.DataFrame, strategy_name: str, keep_col: str
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for trade_date, day in frame.groupby("trade_date", sort=True):
        values = metric_values(day, keep_col)
        rows.append(
            {
                "trade_date": trade_date,
                "strategy_name": strategy_name,
                "original_count": values["original_candidate_count"],
                "retained_count": values["retained_candidate_count"],
                "removed_count": values["removed_candidate_count"],
                "original_winners": values["baseline_winner_count"],
                "retained_winners": values["retained_winner_count"],
                "removed_winners": values["removed_winner_count"],
                "original_big_losers": values["baseline_big_loser_count"],
                "retained_big_losers": values["retained_big_loser_count"],
                "removed_big_losers": values["removed_big_loser_count"],
                "winner_retention_rate": values["winner_retention_rate"],
                "big_loser_removal_rate": values["big_loser_removal_rate"],
                "compression_rate": values["compression_rate"],
                "baseline_mean_return": values["baseline_mean_day10_return"],
                "strategy_mean_return": values["strategy_mean_day10_return"],
                "mean_return_delta": values["mean_return_delta"],
            }
        )
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def bootstrap_by_date(frame: pd.DataFrame, keep_col: str) -> dict[str, float]:
    dates = sorted(frame.trade_date.unique())
    groups = {d: frame[frame.trade_date == d] for d in dates}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    results = {
        "winner_retention_rate": [],
        "big_loser_removal_rate": [],
        "compression_rate": [],
        "mean_return_delta": [],
    }
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        boot = pd.concat([groups[d] for d in sampled], ignore_index=True)
        values = metric_values(boot, keep_col)
        for key in results:
            results[key].append(float(values[key]))
    output: dict[str, float] = {}
    for key, values in results.items():
        output[f"{key}_ci_low"] = q(values, 0.025)
        output[f"{key}_ci_high"] = q(values, 0.975)
    return output


def date_dominance(daily: pd.DataFrame) -> tuple[dict[str, float | int], str]:
    n = len(daily)
    full_winner = (
        (daily.removed_winners == 0) & (daily.original_winners > 0)
    )
    winner_days = int((daily.original_winners > 0).sum())
    loser_days = int((daily.original_big_losers > 0).sum())
    stats: dict[str, float | int] = {
        "full_winner_retention_date_rate": safe_rate(
            int(full_winner.sum()), winner_days
        ),
        "removed_winner_date_rate": safe_rate(
            int((daily.removed_winners > 0).sum()), winner_days
        ),
        "big_loser_removal_date_rate": safe_rate(
            int((daily.removed_big_losers > 0).sum()), loser_days
        ),
        "max_removed_winners_single_date": int(daily.removed_winners.max()),
        "max_compression_single_date": float(daily.compression_rate.max()),
    }
    removal_total = int(daily.removed_count.sum())
    if removal_total == 0:
        note = "NO_REMOVALS"
    else:
        top_share = float(daily.removed_count.max() / removal_total)
        note = (
            "DATE_DOMINATED" if top_share >= 0.50 else "NOT_SINGLE_DATE_DOMINATED"
        )
    return stats, note


def classify_gates(
    values: dict[str, Any], bootstrap: dict[str, float]
) -> tuple[dict[str, bool | str], str]:
    gate1 = values["winner_retention_rate"] >= 0.95
    if not gate1:
        return (
            {
                "gate_1_winner_retention": False,
                "gate_2_mean_return_protection": "NOT_REACHED",
                "gate_3_big_loser_removal": "NOT_REACHED",
                "gate_4_compression": "NOT_REACHED",
            },
            "ELIMINATED_GATE_1",
        )
    ci_clearly_negative = bootstrap["mean_return_delta_ci_high"] < 0.0
    gate2 = values["mean_return_delta"] >= -0.10 and not ci_clearly_negative
    if not gate2:
        return (
            {
                "gate_1_winner_retention": True,
                "gate_2_mean_return_protection": False,
                "gate_3_big_loser_removal": "NOT_REACHED",
                "gate_4_compression": "NOT_REACHED",
            },
            "ELIMINATED_GATE_2",
        )
    gate3 = values["big_loser_removal_rate"] >= 0.30
    if not gate3:
        return (
            {
                "gate_1_winner_retention": True,
                "gate_2_mean_return_protection": True,
                "gate_3_big_loser_removal": False,
                "gate_4_compression": "NOT_REACHED",
            },
            "WEAK_GATE_3",
        )
    gate4 = values["compression_rate"] >= 0.20
    return (
        {
            "gate_1_winner_retention": True,
            "gate_2_mean_return_protection": True,
            "gate_3_big_loser_removal": True,
            "gate_4_compression": gate4,
        },
        "CHAMPION_ELIGIBLE" if gate4 else "ELIMINATED_GATE_4",
    )


def write_empty_diagnostic(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REMOVED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    return f"{float(value) * 100:.1f}%"


def number(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def build_report(
    ledger: pd.DataFrame,
    manifest: dict[str, Any],
    split: dict[str, Any],
    summary: pd.DataFrame,
    development: pd.DataFrame,
    final_decision: str,
) -> str:
    status_counts = ledger.status.value_counts().to_dict()
    available = [
        s for s in manifest["strategies"] if s["availability"] == "AVAILABLE"
    ]
    challenger = summary[summary.strategy_role == "CHALLENGER"]
    count_distribution = (
        development.groupby("trade_date").size().sort_index().astype(int)
    )
    timeline = (
        ledger.groupby(["experiment_date", "status"], sort=True)
        .experiment_id.apply(lambda x: ", ".join(x))
        .reset_index()
    )
    lines = [
        "# Phase 4A / 4B — Bull-Market Strategy Convergence",
        "",
        "> 研究日：2026-07-29。純研究；production 零修改。Phase 4 strategy 與 data split 均在 outcome evaluation 前封存並驗證 SHA256。",
        "",
        "## 1. Corrected Timeline",
        "",
        "| execution date | status | experiments |",
        "|---|---|---|",
    ]
    for item in timeline.itertuples():
        lines.append(
            f"| {item.experiment_date} | {item.status} | {item.experiment_id} |"
        )
    lines.extend(
        [
            "",
            "Phase 3G 的較嚴謹 attribution 覆蓋 Phase 3F：Pocket 為 `REJECT`；修正 Outcome leakage 後的 watchlist 仍僅 `SHADOW_ONLY`。Phase 3I 沒有把 Phase 3H/3G 升級，因為兩條 frozen branch 都沒有足夠獨立成熟樣本。",
            "",
            "## 2. Experiment Ledger",
            "",
            "| status | count |",
            "|---|---:|",
        ]
    )
    for status in ["PASS", "WEAK", "SHADOW_ONLY", "REJECT", "INCOMPLETE"]:
        lines.append(f"| {status} | {int(status_counts.get(status, 0))} |")
    lines.extend(
        [
            "",
            "完整逐實驗欄位見 `phase4_experiment_ledger.csv`；人類可讀時間線見 `phase4_experiment_ledger.md`。",
            "",
            "## 3. Eligible Evidence",
            "",
            "唯一可進自動 Phase 4 的證據是 `P3D_MOMENTUM_RANK_GRADIENT`（`PASS / RANK`）。它確認既有 momentum order 有穩定 Winner 梯度，但也確認前段 Big Loser 較高，所以不允許把它改寫成 hard filter。沒有任何 `PASS / FILTER`，也沒有任何已通過的固定 Top-K。",
            "",
            "## 4. Frozen Strategies",
            "",
            "| strategy | availability | exact behavior |",
            "|---|---|---|",
        ]
    )
    for strategy in manifest["strategies"]:
        behavior = "; ".join(
            strategy.get("exact_rules")
            or [strategy.get("unavailable_reason", "NOT_AVAILABLE")]
        )
        lines.append(
            f"| {strategy['strategy_name']} | {strategy['availability']} | {behavior} |"
        )
    lines.extend(
        [
            "",
            f"Strategy manifest SHA256：`{sha256_file(MANIFEST_JSON)}`。",
            "",
            "## 5. Development Tournament",
            "",
            f"- Regime enum：`{split['regime_enum']}`。",
            f"- Replay sample range：{min(split['development_dates'])}～{max(split['development_dates'])}。",
            f"- Development bull dates：{split['development_date_count']}；Holdout bull dates：{split['holdout_date_count']}。",
            f"- Development daily candidate count：min={int(count_distribution.min())}、median={median(count_distribution.tolist()):.1f}、max={int(count_distribution.max())}。",
            "- Primary sector 不存在於 frozen Phase 3D raw-union snapshot，因此欄位保留 null；未從未封存來源回填。",
            "- Split 單位是已重建的 replay sample date；不是宣稱擁有連續 production snapshot。",
            "",
            "| strategy | n kept/original | compression | winner retention | big-loser removal | mean Δ pp | mean Δ 95% CI | decision |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in summary.itertuples():
        lines.append(
            f"| {item.strategy_name} | {item.retained_candidate_count}/{item.original_candidate_count} | "
            f"{pct(item.compression_rate)} | {pct(item.winner_retention_rate)} | "
            f"{pct(item.big_loser_removal_rate)} | {number(item.mean_return_delta)} | "
            f"[{number(item.mean_return_delta_ci_low)}, {number(item.mean_return_delta_ci_high)}] | "
            f"{item.development_decision} |"
        )
    lines.extend(
        [
            "",
            f"Bootstrap 使用交易日為 block，固定 seed={BOOTSTRAP_SEED}、{BOOTSTRAP_REPLICATES} 次；沒有把同日股票視為互相獨立。",
            "",
            "## 6. Champion Decision",
            "",
        ]
    )
    if final_decision == "NO_CHAMPION":
        s2 = challenger.iloc[0] if len(challenger) else None
        if s2 is not None:
            lines.append(
                f"`S2_VALIDATED_RANKING` 保留 {int(s2.retained_candidate_count)}/{int(s2.original_candidate_count)} 筆，"
                f"Winner Retention={pct(s2.winner_retention_rate)}、Mean Δ={number(s2.mean_return_delta)}pp，"
                f"但 Big Loser Removal={pct(s2.big_loser_removal_rate)}，在 Gate 3 即判定 `WEAK`；"
                "因為沒有 PASS Top-K，不能把排序任意截斷來製造壓縮。"
            )
        lines.extend(
            [
                "",
                "Development 沒有 Challenger 通過全部四關，故沒有建立 `phase4_champion_manifest.json`。",
                "",
                "## 7. Holdout Result",
                "",
                "**Holdout not executed because no development strategy passed all gates.**",
                "",
            ]
        )
    lines.extend(
        [
            "## 8. Final Decision",
            "",
            f"# {final_decision}",
            "",
            "目前 Phase 3A～3I 的既有證據不足以形成安全的 Day0 Candidate Compression。正向的 momentum ranking 證據只支持排序，不支持未驗證的刪除門檻。",
            "",
            "## 9. Prohibited Follow-up",
            "",
            "- 不得根據 Holdout 修改策略或用同一 Holdout 重測修改版。",
            "- 不得由 removed Winner 個案新增 stock-specific 例外。",
            "- 不得降低 95% Winner Retention 門檻。",
            "- 不得為湊 Top 20 增加未驗證規則或測試 Top 15/18/20/22/25。",
            "- 不得把 WEAK、SHADOW_ONLY、REJECT 或 INCOMPLETE evidence 改名後放入自動壓縮。",
            "- 不得啟動 Phase 3J、修改 production、LLM、A/B/C/D、Phase 2/2.5、Hard Exclusion 或做 portfolio backtest。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    manifest_hash = verify(MANIFEST_JSON, MANIFEST_SHA)
    split_hash = verify(SPLIT_JSON, SPLIT_SHA)
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    split = json.loads(SPLIT_JSON.read_text(encoding="utf-8"))
    if split["strategy_manifest_sha256"] != manifest_hash:
        raise RuntimeError("data split references a different strategy manifest")

    source = Path(split["source_snapshot"])
    if sha256_file(source) != split["source_snapshot_sha256"]:
        raise RuntimeError("source replay snapshot changed after split freeze")

    development_dates = set(split["development_dates"])
    bull_enum = split["regime_enum"]
    contract = manifest["daily_cohort_contract"]

    # The source contains outcomes for every replay date, but this loop checks
    # membership in the frozen development set before accessing the outcome key.
    raw: list[dict[str, Any]] = json.loads(source.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in raw:
        trade_date = str(item[contract["trade_date_field"]])
        if trade_date not in development_dates:
            continue
        if str(item["regime"]) != bull_enum or not bool(item["selected_top120"]):
            continue
        value = item["forward_return_10d"]
        if value is None:
            continue
        sources = [
            name[-1]
            for name in ["source_A", "source_B", "source_C", "source_D"]
            if bool(item.get(name))
        ]
        ret = float(value)
        rows.append(
            {
                "trade_date": trade_date,
                "stock_id": str(item["stock_id"]),
                "original_candidate_order": int(item["raw_union_rank"]),
                "market_regime": str(item["regime"]),
                "primary_sector": None,
                "candidate_sources": "".join(sources) or "NONE",
                "source_A": bool(item.get("source_A")),
                "source_B": bool(item.get("source_B")),
                "source_C": bool(item.get("source_C")),
                "source_D": bool(item.get("source_D")),
                "momentum_score": item.get("momentum_score"),
                "all_strategy_input_features": json.dumps(
                    {"original_candidate_order": int(item["raw_union_rank"])},
                    sort_keys=True,
                ),
                "strategy_1_kept": None,
                "strategy_2_kept": True,
                "strategy_3_kept": None,
                "day10_return": ret,
                "outcome_group": outcome_group(ret),
            }
        )
    development = pd.DataFrame(rows).sort_values(
        ["trade_date", "original_candidate_order", "stock_id"]
    )
    if not set(development.trade_date.unique()) == development_dates:
        raise RuntimeError("one or more frozen development dates have no matured rows")
    development.to_csv(DEVELOPMENT_ALL, index=False)

    strategies = [
        ("S0_BASELINE", "BASELINE", "_baseline_kept"),
        ("S2_VALIDATED_RANKING", "CHALLENGER", "strategy_2_kept"),
    ]
    development["_baseline_kept"] = True
    daily_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []
    diagnostics = {
        "WINNER": [],
        "BIG_LOSER": [],
        "NEUTRAL": [],
    }
    champion_candidates: list[dict[str, Any]] = []
    for strategy_name, strategy_role, keep_col in strategies:
        values = metric_values(development, keep_col)
        bootstrap = bootstrap_by_date(development, keep_col)
        daily = daily_metrics(development, strategy_name, keep_col)
        daily_frames.append(daily)
        dominance, dominance_note = date_dominance(daily)
        if strategy_role == "BASELINE":
            gates: dict[str, Any] = {
                "gate_1_winner_retention": "REFERENCE",
                "gate_2_mean_return_protection": "REFERENCE",
                "gate_3_big_loser_removal": "REFERENCE",
                "gate_4_compression": "REFERENCE",
            }
            decision = "REFERENCE"
        else:
            gates, decision = classify_gates(values, bootstrap)
            if decision == "CHAMPION_ELIGIBLE":
                champion_candidates.append(
                    {
                        "strategy_name": strategy_name,
                        **values,
                    }
                )
        summary_rows.append(
            {
                "strategy_name": strategy_name,
                "strategy_role": strategy_role,
                "availability": "AVAILABLE",
                **values,
                **bootstrap,
                **dominance,
                "date_dominance_result": dominance_note,
                **gates,
                "development_decision": decision,
            }
        )

        if strategy_role == "CHALLENGER":
            removed = development[~development[keep_col]]
            for item in removed.itertuples():
                diagnostics[item.outcome_group].append(
                    {
                        "trade_date": item.trade_date,
                        "stock_id": item.stock_id,
                        "strategy_name": strategy_name,
                        "removal_reason": "not retained by frozen exact rules",
                        "original_candidate_order": item.original_candidate_order,
                        "day10_return": item.day10_return,
                        "outcome_group": item.outcome_group,
                        "primary_sector": item.primary_sector,
                    }
                )

    daily_all = pd.concat(daily_frames, ignore_index=True)
    daily_all.to_csv(DEVELOPMENT_DAILY, index=False)
    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)
    summary.to_csv(DEVELOPMENT_SUMMARY, index=False)
    write_empty_diagnostic(REMOVED_WINNERS, diagnostics["WINNER"])
    write_empty_diagnostic(REMOVED_BIG_LOSERS, diagnostics["BIG_LOSER"])
    write_empty_diagnostic(REMOVED_NEUTRALS, diagnostics["NEUTRAL"])

    if champion_candidates:
        ordered = sorted(
            champion_candidates,
            key=lambda x: (
                -x["winner_retention_rate"],
                -x["mean_return_delta"],
                -x["big_loser_removal_rate"],
                -x["compression_rate"],
                x["strategy_name"],
            ),
        )
        champion = ordered[0]
        champion_strategy = next(
            s
            for s in manifest["strategies"]
            if s["strategy_name"] == champion["strategy_name"]
        )
        champion_manifest = {
            "champion_manifest_version": "phase4a_champion_v1",
            "strategy_manifest_sha256": manifest_hash,
            "data_split_sha256": split_hash,
            "strategy_name": champion["strategy_name"],
            "exact_rules": champion_strategy["exact_rules"],
            "exact_rule_order": champion_strategy["exact_rule_order"],
            "required_features": champion_strategy["required_features"],
            "development_metrics": champion,
            "selection_reason": "passed all four lexicographic development gates",
            "manifest_hash": manifest_hash,
        }
        CHAMPION_JSON.write_text(
            json.dumps(champion_manifest, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        CHAMPION_SHA.write_text(
            f"{sha256_file(CHAMPION_JSON)}  {CHAMPION_JSON.name}\n",
            encoding="utf-8",
        )
        final_decision = "DEVELOPMENT_ONLY"
        raise RuntimeError(
            "A Champion exists. Run a separate holdout evaluator before writing the final report."
        )
    else:
        final_decision = "NO_CHAMPION"
        stale = [path for path in [CHAMPION_JSON, CHAMPION_SHA, *HOLDOUT_FILES] if path.exists()]
        if stale:
            raise RuntimeError(
                "NO_CHAMPION but stale champion/holdout files exist: "
                + ", ".join(path.name for path in stale)
            )

    ledger = pd.read_csv(LEDGER_CSV)
    REPORT.write_text(
        build_report(ledger, manifest, split, summary, development, final_decision),
        encoding="utf-8",
    )
    print(f"verified strategy manifest sha256={manifest_hash}")
    print(f"verified data split sha256={split_hash}")
    print(
        f"development rows={len(development)}, dates={development.trade_date.nunique()}, "
        f"W/N/L={(development.outcome_group == 'WINNER').sum()}/"
        f"{(development.outcome_group == 'NEUTRAL').sum()}/"
        f"{(development.outcome_group == 'BIG_LOSER').sum()}"
    )
    print(f"final decision={final_decision}")
    print("holdout not executed")


if __name__ == "__main__":
    main()
