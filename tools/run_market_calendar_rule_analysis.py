"""Run the interpretable 9-factor market-calendar analysis and write a report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

try:
    from tools.market_calendar_rules import (
        FACTOR_COLUMNS,
        add_factor_states,
        build_rule_panel,
        discover_rules,
        format_rule,
        predict_with_rules,
        binary_label_from_return,
        binary_prediction_with_momentum,
        expected_return_with_overnight,
        fit_overnight_return_model,
        strict_label_from_return,
        walk_forward_rule_predictions,
    )
except ModuleNotFoundError:  # direct ``python3 tools/run_...py`` execution
    from market_calendar_rules import (  # type: ignore
        FACTOR_COLUMNS,
        add_factor_states,
        build_rule_panel,
        discover_rules,
        format_rule,
        predict_with_rules,
        binary_label_from_return,
        binary_prediction_with_momentum,
        expected_return_with_overnight,
        fit_overnight_return_model,
        strict_label_from_return,
        walk_forward_rule_predictions,
    )


def _accuracy(actual: pd.Series, predicted: pd.Series) -> Tuple[int, int, float]:
    mask = actual.notna() & predicted.notna()
    total = int(mask.sum())
    correct = int((actual[mask] == predicted[mask]).sum())
    return correct, total, correct / total if total else float("nan")


def _condition_table(train: pd.DataFrame, cutoffs: Dict[str, Tuple[float, float]]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    states = add_factor_states(train, cutoffs)
    for name, (low, high) in cutoffs.items():
        for state, cutoff in (("低", low), ("高", high)):
            selected = states[states[name + "__state"] == state]
            selected = selected.dropna(subset=["target_direction", "target_return"])
            if len(selected) < 4:
                continue
            distribution = selected["target_direction"].value_counts()
            label = str(distribution.index[0])
            precision = float(distribution.iloc[0] / len(selected))
            prevalence = float((train["target_direction"] == label).mean())
            rows.append(
                {
                    "feature": name,
                    "state": state,
                    "cutoff": cutoff,
                    "support": len(selected),
                    "dominant_direction": label,
                    "precision": precision,
                    "lift": precision / prevalence if prevalence else np.nan,
                    "mean_next_return": selected["target_return"].mean(),
                    "median_next_return": selected["target_return"].median(),
                    "up_count": int((selected["target_direction"] == "走高").sum()),
                    "range_count": int((selected["target_direction"] == "震盪").sum()),
                    "down_count": int((selected["target_direction"] == "走低").sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["lift", "support"], ascending=False)


def _matching_history(
    train: pd.DataFrame,
    states: pd.DataFrame,
    rules,
    latest_row: pd.Series,
) -> pd.Series:
    values = []
    for rule in rules:
        if not all(latest_row.get(name + "__state") == state for name, state in rule.conditions):
            continue
        mask = pd.Series(True, index=states.index)
        for name, state in rule.conditions:
            mask &= states[name + "__state"] == state
        values.extend(train.loc[mask, "target_return"].dropna().tolist())
    return pd.Series(values, dtype=float)


def _render_report(
    panel: pd.DataFrame,
    train: pd.DataFrame,
    rules,
    static_metrics: Dict[str, object],
    walk: pd.DataFrame,
    condition_table: pd.DataFrame,
    latest: Dict[str, object],
    metadata: Dict[str, object],
) -> str:
    lines = [
        "# 台股行事曆：9 類日資料＋美股隔夜／台指期夜盤規律分析",
        "",
        "> 每一列 signal_date 只使用該日收盤後資料，對應下一個台股交易日；沒有把隔日因子倒灌回當日。",
        "> 七種盤勢路徑保留在原始面板；本分析的主規則先判斷走高／震盪／走低，再估計隔日報酬。",
        "",
        "## 先說分析結論",
        "",
        "這個區間可以找到有支持度的條件組合，但 76 個交易日、且嚴格的 +2.5%／-3% 門檻下，走高 5 天、走低 7 天，樣本不足以聲稱穩定達到 75%。因此規則發現使用 ±0.5% 的實務方向標籤來找『隔日偏向』，最後仍另外轉回使用者指定的嚴格三大類。",
        "",
        "本版不是把最多的類別當答案；每一條規則都列出條件、出現次數、歷史命中率、相對基準的 lift 與隔日平均報酬。低支持度條件只作線索，不作強結論。",
        "",
        "## 資料與覆蓋",
        "",
        "- 回測日期：%s ～ %s，共 %d 列。" % (metadata["start"], metadata["end"], metadata["rows"]),
        "- 融資融券：FinMind `TaiwanStockMarginPurchaseShortSale`，逐日查詢後加總全市場；包含融資買進、融資餘額變化、融券賣出、融券餘額變化。",
        "- FinMind 這個資料集的區間查詢只回傳起始日，本次已改逐日查詢；這是資料來源限制，不是把缺值當成 0。",
        "- 其他資料沿用已下載的大盤 OHLC、法人、台灣 VIX、小台期未平倉、FRED 美 VIX／美債／美元與黃金資料。",
        "- 新增美股 S&P500、Nasdaq、SOX、ES／NQ／YM 隔夜變化，以及 TAIFEX TX／MTX 盤後 OHLC 與成交量。夜盤資料按 TAIFEX 交易日期對齊到下一個台股交易日。",
        "",
        "## 開發段找到的高支持度單／雙因素規律",
        "",
    ]
    for rule in rules[:15]:
        lines.append("- " + format_rule(rule))
    lines.extend(
        [
            "",
            "解讀方式：例如 `外資 5 日累計=高 + 台灣 VIX 當日變化=高 → 走低`，表示在開發段同時出現這兩個狀態時，隔日方向標籤多數為走低；它是歷史條件關聯，不等於因果或保證。",
            "",
            "## 時間序列驗證",
            "",
            "- 靜態 holdout：前 60%% 只用來找門檻與規則，後 40%% 只用來驗證；方向準確率：%d/%d = %.1f%%。" % (static_metrics["direction_correct"], static_metrics["direction_total"], static_metrics["direction_accuracy"] * 100.0),
            "- 靜態 holdout 的嚴格三大類準確率：%d/%d = %.1f%%。" % (static_metrics["strict_correct"], static_metrics["strict_total"], static_metrics["strict_accuracy"] * 100.0),
            "- 若只分偏漲／偏跌，靜態 holdout 準確率：%d/%d = %.1f%%。" % (static_metrics["binary_correct"], static_metrics["binary_total"], static_metrics["binary_accuracy"] * 100.0),
            "- 靜態 holdout 隔日報酬 MAE：%.2f 個百分點。" % static_metrics["mae"],
            "- 逐日 walk-forward：每一天重新只用之前資料找規則；方向準確率：%d/%d = %.1f%%。" % (walk["direction_correct"].iloc[0], walk["direction_total"].iloc[0], walk["direction_accuracy"].iloc[0] * 100.0),
            "- 逐日 walk-forward 若只分偏漲／偏跌：%d/%d = %.1f%%。" % (walk["binary_correct"].iloc[0], walk["binary_total"].iloc[0], walk["binary_accuracy"].iloc[0] * 100.0),
            "- 逐日 walk-forward 含夜盤數值校準後，隔日報酬 MAE：%.2f 個百分點；誤差在 ±0.5%%：%.1f%%；±1.0%%：%.1f%%。" % (walk["return_mae"].iloc[0], walk["return_within_0_5"].iloc[0] * 100.0, walk["return_within_1_0"].iloc[0] * 100.0),
            "",
            "這些數字是用來檢查規律能否移到未見資料，不是模型『猜最多類別』的分數。若未來擴大樣本後仍維持，才有資格提高規則權重。",
            "",
            "## 最近一筆訊號與下一交易日推估",
            "",
            "- 訊號日：%s；預測目標：下一個交易日（資料截至訊號日，未使用目標日資料）。" % latest["signal_date"],
            "- 實務方向：**%s**；納入夜盤校準後的隔日收盤報酬約 **%+.2f%%**。" % (latest["direction"], latest["expected_return"]),
            "- 二分類結果：**%s**（夜盤綜合分數使用台指期夜盤相對現貨＋Nasdaq期貨隔夜變化，門檻為 +0.3 個百分點）。" % latest["binary"],
            "- 使用者指定嚴格三大類：**%s**。" % latest["strict_direction"],
            "- 同條件歷史樣本：%d 筆；中間 50%% 區間約 %+.2f%% ～ %+.2f%%。" % (latest["history_count"], latest["q25"], latest["q75"]),
            "- 目前匹配規則：",
        ]
    )
    for rule_text in latest["matched_rules"]:
        lines.append("  - " + rule_text)
    lines.extend(
        [
            "",
            "這個最新預測應解讀為『目前因子組合偏向哪邊，以及歷史相似條件的報酬範圍』；不是保證隔日一定上漲或下跌。",
            "",
            "## 因子狀態表（開發段單因子條件）",
            "",
            "詳細表格另存 `market_calendar_rule_analysis_*_factor_conditions.csv`，包含每個因子的低／高狀態、支持度、方向比例與平均隔日報酬。",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--margin", required=True, type=Path)
    parser.add_argument("--overnight", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    raw = pd.read_csv(args.panel)
    margin = pd.read_csv(args.margin)
    overnight = pd.read_csv(args.overnight)
    panel = build_rule_panel(raw, margin, overnight)
    usable = panel.dropna(subset=["target_direction", "target_return"]).copy()
    split = max(30, int(len(usable) * 0.60))
    split = min(split, len(usable) - 1)
    train = usable.iloc[:split].copy()
    test = usable.iloc[split:].copy()

    rules, cutoffs, train_states = discover_rules(train, min_support=4, target_column="target_direction")
    test_states = add_factor_states(test, cutoffs)
    fallback = float(train["target_return"].mean())
    overnight_model = fit_overnight_return_model(train)
    predictions = []
    for _, row in test_states.iterrows():
        direction, expected_return, matched = predict_with_rules(row, rules, fallback)
        predicted_binary = binary_prediction_with_momentum(row, expected_return)
        final_return = expected_return_with_overnight(
            row, expected_return, predicted_binary, overnight_model
        )
        predictions.append(
            {
                "signal_date": row["trade_date"],
                "target_date": row["target_date"],
                "predicted_direction": direction,
                "predicted_strict": strict_label_from_return(final_return),
                "predicted_binary": predicted_binary,
                "predicted_return": final_return,
                "actual_direction": row["target_direction"],
                "actual_strict": row["target_broad"],
                "actual_return": row["target_return"],
                "actual_binary": binary_label_from_return(row["target_return"]),
                "matched_rule_count": len(matched),
            }
        )
    prediction_frame = pd.DataFrame(predictions)
    direction_correct, direction_total, direction_accuracy = _accuracy(
        prediction_frame["actual_direction"], prediction_frame["predicted_direction"]
    )
    strict_correct, strict_total, strict_accuracy = _accuracy(
        prediction_frame["actual_strict"], prediction_frame["predicted_strict"]
    )
    binary_correct, binary_total, binary_accuracy = _accuracy(
        prediction_frame["actual_binary"], prediction_frame["predicted_binary"]
    )
    mae = float((prediction_frame["actual_return"] - prediction_frame["predicted_return"]).abs().mean())
    static_metrics = {
        "direction_correct": direction_correct,
        "direction_total": direction_total,
        "direction_accuracy": direction_accuracy,
        "strict_correct": strict_correct,
        "strict_total": strict_total,
        "strict_accuracy": strict_accuracy,
        "binary_correct": binary_correct,
        "binary_total": binary_total,
        "binary_accuracy": binary_accuracy,
        "mae": mae,
    }

    walk = walk_forward_rule_predictions(panel, start_index=25, target_column="target_direction")
    evaluated_walk = walk.dropna(subset=["actual_direction"]).copy()
    walk_correct, walk_total, walk_accuracy = _accuracy(
        evaluated_walk["actual_direction"], evaluated_walk["predicted_direction"]
    )
    walk_binary_correct, walk_binary_total, walk_binary_accuracy = _accuracy(
        evaluated_walk["actual_binary"], evaluated_walk["predicted_binary"]
    )
    walk_summary = pd.DataFrame(
        [{
            "direction_correct": walk_correct,
            "direction_total": walk_total,
            "direction_accuracy": walk_accuracy,
            "binary_correct": walk_binary_correct,
            "binary_total": walk_binary_total,
            "binary_accuracy": walk_binary_accuracy,
            "return_mae": float((evaluated_walk["actual_return"] - evaluated_walk["predicted_return"]).abs().mean()),
            "return_within_0_5": float(((evaluated_walk["actual_return"] - evaluated_walk["predicted_return"]).abs() <= 0.5).mean()),
            "return_within_1_0": float(((evaluated_walk["actual_return"] - evaluated_walk["predicted_return"]).abs() <= 1.0).mean()),
        }]
    )
    walk = pd.concat([walk, walk_summary], ignore_index=True)

    condition_table = _condition_table(train, cutoffs)
    latest_raw = panel.tail(1)
    latest_states = add_factor_states(latest_raw, cutoffs)
    latest_row = latest_states.iloc[0]
    latest_direction, expected_return, matched = predict_with_rules(latest_row, rules, fallback)
    latest_binary = binary_prediction_with_momentum(latest_row, expected_return)
    latest_return = expected_return_with_overnight(
        latest_row, expected_return, latest_binary, overnight_model
    )
    history = _matching_history(train, train_states, rules, latest_row)
    if history.empty:
        history = train["target_return"]
    latest = {
        "signal_date": latest_row["trade_date"].date().isoformat(),
        "direction": latest_direction,
        "binary": latest_binary,
        "strict_direction": strict_label_from_return(latest_return),
        "expected_return": latest_return,
        "history_count": len(history),
        "q25": history.quantile(0.25),
        "q75": history.quantile(0.75),
        "matched_rules": [format_rule(rule) for rule in matched],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "market_calendar_rule_analysis_%s_%s" % (
        panel["trade_date"].min().date().isoformat(),
        panel["trade_date"].max().date().isoformat(),
    )
    prediction_frame.to_csv(args.output_dir / (stem + "_holdout.csv"), index=False)
    walk.to_csv(args.output_dir / (stem + "_walk_forward.csv"), index=False)
    condition_table.to_csv(args.output_dir / (stem + "_factor_conditions.csv"), index=False)
    metadata = {
        "start": panel["trade_date"].min().date().isoformat(),
        "end": panel["trade_date"].max().date().isoformat(),
        "rows": len(panel),
        "train_rows": len(train),
        "test_rows": len(test),
        "factor_groups": list(FACTOR_COLUMNS.keys()),
        "strict_target_counts": panel["target_broad"].value_counts(dropna=True).to_dict(),
        "direction_target_counts": panel["target_direction"].value_counts(dropna=True).to_dict(),
        "latest_forecast": latest,
    }
    (args.output_dir / (stem + ".metadata.json")).write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n")
    report = _render_report(panel, train, rules, static_metrics, walk_summary, condition_table, latest, metadata)
    (args.output_dir / (stem + ".md")).write_text(report)
    print(json.dumps({"report": str(args.output_dir / (stem + ".md")), "latest": latest, "static": static_metrics, "walk_forward": walk_summary.iloc[0].to_dict()}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
