import json
import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.backtest_catalog import BACKTEST_CAPABILITY_CATALOG
from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)

BACKTEST_MAPPING_SYSTEM_PROMPT = """你是一位台股回測策略 mapping 助手。
你的工作不是發明新策略，而是把使用者輸入的條件短語，映射到既有的受控 catalog。

輸出必須是 JSON，格式如下：
{
  "mappings": [
    {
      "phrase": "原始短語",
      "kind": "rule | risk_control | unsupported",
      "indicator": "若 kind=rule，填 indicator id",
      "params": {},
      "risk_control": "若 kind=risk_control，填 stop_loss_pct 或 take_profit_pct",
      "value": 8,
      "reason": "簡短原因"
    }
  ]
}

規則：
- 只能使用 catalog 中允許的 indicator / risk_control id
- 若無法安全映射，就回 kind=unsupported
- 不要輸出 catalog 外的新欄位或新指標
- params 只能放數字；若 indicator 的 params schema 是 {} 就留空 object
- 使用繁體中文 reason

術語對照提示（僅供參考，仍需使用 catalog 內 id）：
- 黃金交叉 / 短均上穿長均 → ma_golden_cross（需 short_window、long_window）
- 死亡交叉 / 短均下穿長均 → ma_dead_cross（需 short_window、long_window）
- 站上月線 ≈ close_above_ma window=20；站上季線 ≈ window=60；站上年線 ≈ window=240
- 跌破月線 ≈ close_below_ma window=20
- 創新高 / 突破近高 → close_breakout_high
- 破底 / 跌破近低 → close_breakdown_low
- 放量 → volume_above_ma；爆量 / 量能暴增 → volume_ratio_above_ma
- 外資 / 投信 / 自營商「買超 / 賣超 / 連買 N 天 / 連賣 N 天」 → 對應 *_net_positive / *_net_negative / *_consecutive_buy / *_consecutive_sell
- 三大法人合計買超 / 轉賣 → all_inst_net_positive / all_inst_net_negative
- K 棒型態：十字星、錘子線、吊人線、流星線、倒錘線、長紅（大陽線）、長黑（大陰線）、
  看漲吞噬、看跌吞噬、紅三兵、三隻烏鴉（黑三兵）、晨星、夜星、好友反攻（刺透）、烏雲蓋頂 → candle_* 系列
- 技術型態：頭肩頂、頭肩底（逆頭肩）、雙頂（M 頭）、雙底（W 底）、V 型反轉、A 型反轉（倒 V） → pattern_* 系列
- 停損 / stop loss → risk_control stop_loss_pct；停利 / take profit → risk_control take_profit_pct
"""


def _allowed_indicator_ids() -> List[str]:
    return [item["id"] for item in BACKTEST_CAPABILITY_CATALOG["indicators"]]


def _allowed_risk_control_ids() -> List[str]:
    return [item["id"] for item in BACKTEST_CAPABILITY_CATALOG["risk_controls"]]


def _catalog_prompt_payload() -> Dict[str, Any]:
    return {
        "indicators": BACKTEST_CAPABILITY_CATALOG["indicators"],
        "risk_controls": BACKTEST_CAPABILITY_CATALOG["risk_controls"],
    }


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_positive_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


_WINDOW_INDICATORS = {
    "close_above_ma",
    "close_below_ma",
    "close_breakout_high",
    "close_breakdown_low",
    "volume_above_ma",
}

_DAYS_INDICATORS = {
    "foreign_consecutive_buy",
    "trust_consecutive_buy",
    "dealer_consecutive_buy",
    "foreign_consecutive_sell",
    "trust_consecutive_sell",
    "dealer_consecutive_sell",
}

_NO_PARAM_INDICATORS = {
    "foreign_net_positive",
    "trust_net_positive",
    "dealer_net_positive",
    "foreign_net_negative",
    "trust_net_negative",
    "dealer_net_negative",
    "all_inst_net_positive",
    "all_inst_net_negative",
    "candle_doji",
    "candle_hammer",
    "candle_hanging_man",
    "candle_shooting_star",
    "candle_inverted_hammer",
    "candle_long_bullish",
    "candle_long_bearish",
    "candle_bullish_engulfing",
    "candle_bearish_engulfing",
    "candle_three_white_soldiers",
    "candle_three_black_crows",
    "candle_morning_star",
    "candle_evening_star",
    "candle_bullish_piercing",
    "candle_dark_cloud_cover",
    "pattern_head_shoulders_top",
    "pattern_head_shoulders_bottom",
    "pattern_double_top",
    "pattern_double_bottom",
    "pattern_v_reversal",
    "pattern_a_reversal",
}


def _validate_rule_mapping(indicator: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if indicator not in _allowed_indicator_ids():
        return None

    if indicator in _WINDOW_INDICATORS:
        window = _coerce_positive_int(params.get("window"))
        return {"indicator": indicator, "params": {"window": window}} if window else None

    if indicator in {"ma_golden_cross", "ma_dead_cross"}:
        short_window = _coerce_positive_int(params.get("short_window"))
        long_window = _coerce_positive_int(params.get("long_window"))
        if short_window and long_window and short_window < long_window:
            return {"indicator": indicator, "params": {"short_window": short_window, "long_window": long_window}}
        return None

    if indicator == "volume_ratio_above_ma":
        window = _coerce_positive_int(params.get("window"))
        ratio = _coerce_positive_float(params.get("ratio"))
        if window and ratio:
            return {"indicator": indicator, "params": {"window": window, "ratio": ratio}}
        return None

    if indicator in _DAYS_INDICATORS:
        days = _coerce_positive_int(params.get("days"))
        return {"indicator": indicator, "params": {"days": days}} if days else None

    if indicator in _NO_PARAM_INDICATORS:
        return {"indicator": indicator, "params": {}}

    return None


def map_conditions_with_ai(phrases: List[str]) -> Dict[str, Any]:
    if not phrases:
        return {"rules": [], "risk_controls": {}, "unsupported_conditions": [], "matched_capabilities": [], "source": "rule_based"}
    openai_api_key = get_openai_api_key()
    if not openai_api_key:
        return {
            "rules": [],
            "risk_controls": {},
            "unsupported_conditions": list(phrases),
            "matched_capabilities": [],
            "source": "rule_based",
        }

    client = OpenAI(api_key=openai_api_key)
    prompt_payload = {
        "phrases": phrases,
        "catalog": _catalog_prompt_payload(),
    }

    try:
        response = client.chat.completions.create(
            model=get_openai_model(),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": BACKTEST_MAPPING_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        content = response.choices[0].message.content or ""
        parsed = json.loads(content)
    except Exception:
        logger.exception("Backtest AI mapping failed, falling back to unsupported phrases")
        return {
            "rules": [],
            "risk_controls": {},
            "unsupported_conditions": list(phrases),
            "matched_capabilities": [],
            "source": "rule_based",
        }

    mapping_by_phrase = {}
    for item in parsed.get("mappings", []):
        phrase = str(item.get("phrase", "")).strip()
        if phrase:
            mapping_by_phrase[phrase] = item

    rules = []
    risk_controls = {}
    unsupported_conditions = []
    matched_capabilities = []

    for phrase in phrases:
        item = mapping_by_phrase.get(phrase)
        if not item:
            unsupported_conditions.append(phrase)
            continue

        kind = str(item.get("kind", "")).strip()
        if kind == "rule":
            mapped_rule = _validate_rule_mapping(str(item.get("indicator", "")).strip(), item.get("params", {}) or {})
            if mapped_rule:
                rules.append(mapped_rule)
                matched_capabilities.append(mapped_rule["indicator"])
                continue
        elif kind == "risk_control":
            control_id = str(item.get("risk_control", "")).strip()
            control_value = _coerce_positive_float(item.get("value"))
            if control_id in _allowed_risk_control_ids() and control_value is not None:
                risk_controls[control_id] = control_value
                matched_capabilities.append(control_id)
                continue

        unsupported_conditions.append(phrase)

    return {
        "rules": rules,
        "risk_controls": risk_controls,
        "unsupported_conditions": unsupported_conditions,
        "matched_capabilities": list(dict.fromkeys(matched_capabilities)),
        "source": "openai" if matched_capabilities else "rule_based",
    }
