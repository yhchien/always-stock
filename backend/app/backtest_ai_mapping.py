import json
import logging
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.backtest_catalog import BACKTEST_CAPABILITY_CATALOG

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
- params 只能放數字
- 使用繁體中文 reason
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


def _validate_rule_mapping(indicator: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if indicator not in _allowed_indicator_ids():
        return None

    if indicator in {"close_above_ma", "close_below_ma", "close_breakout_high", "close_breakdown_low", "volume_above_ma"}:
        window = _coerce_positive_int(params.get("window"))
        return {"indicator": indicator, "params": {"window": window}} if window else None

    if indicator in {"ma_golden_cross", "ma_dead_cross"}:
        short_window = _coerce_positive_int(params.get("short_window"))
        long_window = _coerce_positive_int(params.get("long_window"))
        if short_window and long_window and short_window < long_window:
            return {"indicator": indicator, "params": {"short_window": short_window, "long_window": long_window}}
        return None

    if indicator in {"foreign_consecutive_buy", "trust_consecutive_buy", "dealer_consecutive_buy"}:
        days = _coerce_positive_int(params.get("days"))
        return {"indicator": indicator, "params": {"days": days}} if days else None

    if indicator in {"foreign_net_negative", "trust_net_negative", "dealer_net_negative"}:
        return {"indicator": indicator, "params": {}}

    return None


def map_conditions_with_ai(phrases: List[str]) -> Dict[str, Any]:
    if not phrases:
        return {"rules": [], "risk_controls": {}, "unsupported_conditions": [], "matched_capabilities": [], "source": "rule_based"}
    if not OPENAI_API_KEY:
        return {
            "rules": [],
            "risk_controls": {},
            "unsupported_conditions": list(phrases),
            "matched_capabilities": [],
            "source": "rule_based",
        }

    client = OpenAI(api_key=OPENAI_API_KEY)
    prompt_payload = {
        "phrases": phrases,
        "catalog": _catalog_prompt_payload(),
    }

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
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
