"""Run the lag-safe Taiwan market-calendar backtest and write an audit report."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List

import pandas as pd

try:
    from tools.market_calendar import (
        FAMILY_WEIGHTS,
        OUTCOME_LABELS,
        _weighted_nearest_centroid,
        build_feature_panel,
        evaluate_holdout,
        outcome_counts,
        tune_family_weights,
    )
except ModuleNotFoundError:  # direct ``python3 tools/run_...py`` execution
    from market_calendar import (  # type: ignore
        FAMILY_WEIGHTS,
        OUTCOME_LABELS,
        _weighted_nearest_centroid,
        build_feature_panel,
        evaluate_holdout,
        outcome_counts,
        tune_family_weights,
    )


def _fmt_pct(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def walk_forward_predictions(panel: pd.DataFrame, weights: Dict[str, float], min_train: int = 30) -> pd.DataFrame:
    usable = panel.dropna(subset=["target_label"]).reset_index(drop=True)
    rows: List[dict] = []
    for index in range(min_train, len(usable)):
        train = usable.iloc[:index]
        test = usable.iloc[index:index + 1]
        predictions, confidence = _weighted_nearest_centroid(train, test, weights)
        rows.append({
            "signal_date": test.iloc[0]["trade_date"].date().isoformat(),
            "target_date": test.iloc[0]["target_date"].date().isoformat(),
            "actual": test.iloc[0]["target_label"],
            "predicted": predictions[0],
            "confidence": confidence[0],
            "correct": predictions[0] == test.iloc[0]["target_label"],
        })
    return pd.DataFrame(rows)


def _confusion(predictions: pd.DataFrame) -> str:
    lines = ["混淆摘要（實際 → 預測）"]
    counts = Counter(zip(predictions["actual"], predictions["predicted"]))
    for actual in OUTCOME_LABELS:
        parts = []
        for predicted in OUTCOME_LABELS:
            count = counts.get((actual, predicted), 0)
            if count:
                parts.append(f"{predicted}:{count}")
        if parts:
            lines.append(f"- {actual}: " + ", ".join(parts))
    return "\n".join(lines)


def make_report(panel: pd.DataFrame, default_result, tuned_result, walk_forward: pd.DataFrame, weights: Dict[str, float]) -> str:
    start_label = pd.to_datetime(panel["trade_date"]).min().date().isoformat()
    end_label = pd.to_datetime(panel["trade_date"]).max().date().isoformat()
    coverage = panel.notna().sum()
    total_rows = len(panel)
    missing_lines = []
    for column in ["tw_vix", "us_vix", "us10y", "usd_twd", "gold", "mtx_oi_net", "inst_total_net_amount"]:
        available = int(coverage.get(column, 0))
        missing_lines.append(f"- {column}: {available}/{total_rows} ({available / total_rows * 100:.1f}%)")

    walk_accuracy = float(walk_forward["correct"].mean()) if not walk_forward.empty else 0.0
    usable_targets = panel.dropna(subset=["target_label"]).reset_index(drop=True)
    split = max(20, int(len(usable_targets) * 0.60))
    holdout_targets = usable_targets.iloc[split:]
    majority = holdout_targets["target_label"].value_counts()
    majority_rate = float(majority.iloc[0] / majority.sum()) if not majority.empty else 0.0
    expanding_majority_hits = []
    for index in range(30, len(usable_targets)):
        prior_counts = usable_targets.iloc[:index]["target_label"].value_counts()
        expanding_majority_hits.append(prior_counts.index[0] == usable_targets.iloc[index]["target_label"])
    expanding_majority_rate = sum(expanding_majority_hits) / len(expanding_majority_hits) if expanding_majority_hits else 0.0
    counts = outcome_counts(panel)
    count_lines = [f"- {label}: {counts[label]}" for label in OUTCOME_LABELS]
    weight_lines = [f"- {family}: {value:.2f}" for family, value in weights.items()]

    return "\n".join([
        f"# 台股行事曆回測報告（{start_label}～{end_label}）",
        "",
        "> 所有預測 row t 只使用 t 日收盤後可取得的資料，預測 t+1；沒有使用 t+1 的任何因子。",
        "> 七類是盤勢路徑分類，不是報酬保證；短區間樣本不足時，準確率只作研究指標。",
        "",
        "## 資料覆蓋率",
        "",
        *missing_lines,
        "",
        "台灣 VIX 若有缺口，保留為 NaN，沒有用未來值補齊。",
        "",
        "## 實際盤勢類別數量",
        "",
        *count_lines,
        "",
        "## 權重",
        "",
        *weight_lines,
        "",
        "## 嚴格時間切分結果",
        "",
        f"最後 40% holdout 多數類別基準率：{_fmt_pct(majority_rate)}",
        f"固定預設權重 holdout：{tuned_result.total} 筆，{tuned_result.correct} 筆正確，準確率 {_fmt_pct(default_result.accuracy)}",
        f"開發段調權重、最後 40% holdout：{tuned_result.total} 筆，{tuned_result.correct} 筆正確，準確率 {_fmt_pct(tuned_result.accuracy)}",
        f"30 日以上逐日 walk-forward：{len(walk_forward)} 筆，{int(walk_forward['correct'].sum()) if not walk_forward.empty else 0} 筆正確，準確率 {_fmt_pct(walk_accuracy)}（擴張窗口多數類別基準 {_fmt_pct(expanding_majority_rate)}）",
        "",
        _confusion(walk_forward) if not walk_forward.empty else "沒有足夠樣本建立 walk-forward。",
        "",
        "## 結論",
        "",
        "1. 技術面（大盤 K 線、KDJ、均線位置）是第一權重，法人流向第二；VIX、MTX、利率/黃金/美元作為風險修正。",
        "2. 若外部資料缺失，模型不會用未來資料補值；預測信心應下降，而不是把缺失誤當成中性訊號。",
        f"3. 本區間嚴格 out-of-sample 準確率為 {_fmt_pct(tuned_result.accuracy)}，同一 holdout 的多數類別基準為 {_fmt_pct(majority_rate)}；目前不能宣稱達到 75%。",
        "4. 小台資料本版使用 MTX 三大法人多空未平倉與日變化；「重倉點位」若要精確到履約價，需再加入 TXO 選擇權各履約價未平倉資料，不能從期貨 OI 本身推導。",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    args = parser.parse_args()

    raw = pd.read_csv(args.input)
    panel = build_feature_panel(raw)
    tuned_weights = tune_family_weights(panel)
    default_result = evaluate_holdout(panel, train_fraction=0.60, weights=FAMILY_WEIGHTS)
    tuned_result = evaluate_holdout(panel, train_fraction=0.60, weights=tuned_weights)
    walk_forward = walk_forward_predictions(panel, tuned_weights)

    args.predictions.parent.mkdir(parents=True, exist_ok=True)
    walk_forward.to_csv(args.predictions, index=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(make_report(panel, default_result, tuned_result, walk_forward, tuned_weights) + "\n")

    print(make_report(panel, default_result, tuned_result, walk_forward, tuned_weights))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
