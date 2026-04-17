import json
import logging
from typing import Any, Dict, List

from openai import OpenAI
from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)

BACKTEST_ADVICE_SYSTEM_PROMPT = """你是一位專業的台股策略研究助手。
請根據使用者提供的回測資料，輸出具體、可操作、簡潔的建議。

輸出必須是 JSON，格式如下：
{
  "summary": "一句話策略風格摘要",
  "strengths": ["...", "..."],
  "weaknesses": ["...", "..."],
  "rewrite_suggestions": ["...", "..."],
  "risk_notes": ["...", "..."]
}

規則：
- 使用繁體中文
- 每個陣列 1 到 3 點
- 盡量引用具體回測事實，例如夏普、最大回撤、交易次數、勝率、單筆平均報酬
- 不要產出可執行程式碼
- 若資料不足，也要保持具體，不要空泛
"""


def _clean_bullets(items: List[str], fallback: str) -> List[str]:
    cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    return cleaned[:3] if cleaned else [fallback]


def build_local_backtest_advice(payload: Dict[str, Any]) -> Dict[str, Any]:
    metrics = payload.get("metrics", {})
    trade_count = int(metrics.get("trade_count", 0) or 0)
    total_return_pct = float(metrics.get("total_return_pct", 0.0) or 0.0)
    sharpe_ratio = float(metrics.get("sharpe_ratio", 0.0) or 0.0)
    max_drawdown_pct = float(metrics.get("max_drawdown_pct", 0.0) or 0.0)
    win_rate_pct = float(metrics.get("win_rate_pct", 0.0) or 0.0)
    avg_holding_days = float(metrics.get("avg_holding_days", 0.0) or 0.0)
    latest_recommendation = payload.get("latest_recommendation", {})

    if trade_count <= 1:
        summary = "這是一個偏低頻、樣本數仍不足的趨勢型策略。"
    elif avg_holding_days >= 15:
        summary = "這是一個偏波段持有的趨勢追蹤策略。"
    else:
        summary = "這是一個偏短中期切換節奏的條件式策略。"

    strengths = []
    if total_return_pct > 0:
        strengths.append(f"策略在測試區間累積報酬為 {total_return_pct:.2f}%，至少具備正向獲利能力。")
    if sharpe_ratio >= 1:
        strengths.append(f"夏普值為 {sharpe_ratio:.2f}，風險調整後表現已有一定穩定度。")
    if win_rate_pct >= 55:
        strengths.append(f"勝率約 {win_rate_pct:.2f}%，進出場條件的方向判斷不算差。")

    weaknesses = []
    if trade_count < 3:
        weaknesses.append(f"交易次數只有 {trade_count} 次，樣本數偏少，結果容易受單筆交易影響。")
    if max_drawdown_pct <= -15:
        weaknesses.append(f"最大回撤達 {max_drawdown_pct:.2f}%，盤整或反轉時的承受度仍偏高。")
    if total_return_pct <= 0:
        weaknesses.append(f"總報酬為 {total_return_pct:.2f}%，目前策略還沒有跑出穩定優勢。")

    rewrite_suggestions = []
    if trade_count < 5:
        rewrite_suggestions.append("可以放寬進場條件，例如縮短連買天數或改用較短均線，先增加有效樣本。")
    if max_drawdown_pct <= -10:
        rewrite_suggestions.append("可加入固定停損或趨勢過濾條件，優先控制大幅回撤。")
    if sharpe_ratio < 0.8:
        rewrite_suggestions.append("可加入量能或更明確的出場條件，降低低品質訊號帶來的波動。")

    risk_notes = []
    action = latest_recommendation.get("action")
    if action == "observe_buy":
        risk_notes.append("最新交易日雖有進場訊號，但仍要留意下一交易日是否出現跳空追價風險。")
    elif action == "observe_sell":
        risk_notes.append("目前已出現出場訊號，若隔日開盤大幅跳空，實際成交結果可能與回測有落差。")
    else:
        risk_notes.append("此回測採次日開盤成交且成本固定為 0，真實交易績效通常會再略低一些。")

    return {
        "summary": summary,
        "strengths": _clean_bullets(strengths, "目前策略至少提供了可重複驗證的進出場框架。"),
        "weaknesses": _clean_bullets(weaknesses, "目前看不出特別明顯的結構性弱點，但仍需更多樣本驗證。"),
        "rewrite_suggestions": _clean_bullets(
            rewrite_suggestions,
            "下一步可優先增加一個簡單風控條件，再觀察夏普與回撤是否改善。",
        ),
        "risk_notes": _clean_bullets(risk_notes, "回測結果仍需搭配個股當下趨勢與流動性一起判讀。"),
        "source": "heuristic",
    }


def generate_backtest_advice(payload: Dict[str, Any]) -> Dict[str, Any]:
    fallback = build_local_backtest_advice(payload)
    openai_api_key = get_openai_api_key()
    if not openai_api_key:
        return fallback

    client = OpenAI(api_key=openai_api_key)
    prompt_payload = {
        "stock_id": payload.get("stock_id"),
        "strategy_text": payload.get("strategy_text"),
        "normalized_text": payload.get("normalized_text"),
        "metrics": payload.get("metrics"),
        "recent_trades": payload.get("trades", [])[:5],
        "latest_recommendation": payload.get("latest_recommendation"),
    }

    try:
        response = client.chat.completions.create(
            model=get_openai_model(),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BACKTEST_ADVICE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
            temperature=0.4,
            max_tokens=700,
        )
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
        return {
            "summary": parsed.get("summary") or fallback["summary"],
            "strengths": _clean_bullets(parsed.get("strengths", []), fallback["strengths"][0]),
            "weaknesses": _clean_bullets(parsed.get("weaknesses", []), fallback["weaknesses"][0]),
            "rewrite_suggestions": _clean_bullets(
                parsed.get("rewrite_suggestions", []),
                fallback["rewrite_suggestions"][0],
            ),
            "risk_notes": _clean_bullets(parsed.get("risk_notes", []), fallback["risk_notes"][0]),
            "source": "openai",
        }
    except Exception:
        logger.exception("Backtest advice generation failed, using heuristic fallback")
        return fallback
