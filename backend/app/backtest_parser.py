import re
from typing import Dict, List

from app.backtest_catalog import (
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_POSITION_SIZE_PCT,
    DEFAULT_TRADE_TIMING,
)


def _normalize_text(text: str) -> str:
    normalized = text.strip()
    normalized = normalized.replace("，", "、")
    normalized = normalized.replace("；", ";")
    normalized = normalized.replace("。", "")
    normalized = normalized.replace("　", " ")
    return normalized


def _strip_trade_action(text: str) -> str:
    return (
        text.replace("就買進", "")
        .replace("則買進", "")
        .replace("買進", "")
        .replace("就賣出", "")
        .replace("則賣出", "")
        .replace("賣出", "")
        .strip(" 、")
    )


def _parse_rule(token: str) -> Dict:
    token = token.strip()

    ma_match = re.search(r"收盤價(站上|跌破)(\d+)日均線", token)
    if ma_match:
        indicator = "close_above_ma" if ma_match.group(1) == "站上" else "close_below_ma"
        return {"indicator": indicator, "params": {"window": int(ma_match.group(2))}}

    volume_ma_match = re.search(r"成交量高於(\d+)日均量", token)
    if volume_ma_match:
        return {"indicator": "volume_above_ma", "params": {"window": int(volume_ma_match.group(1))}}

    inst_streak_match = re.search(r"(外資|投信|自營商)連買(\d+)天", token)
    if inst_streak_match:
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        return {
            "indicator": f"{inst_map[inst_streak_match.group(1)]}_consecutive_buy",
            "params": {"days": int(inst_streak_match.group(2))},
        }

    inst_negative_match = re.search(r"(外資|投信|自營商)(?:轉賣|賣超)", token)
    if inst_negative_match:
        inst_text = inst_negative_match.group(1)
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        inst_key = inst_map.get(inst_text)
        if inst_key:
            return {"indicator": f"{inst_key}_net_negative", "params": {}}

    raise ValueError(f"Unsupported condition: {token}")


def _parse_rules(tokens: List[str]) -> Dict[str, List[Dict]]:
    parsed_rules: List[Dict] = []
    unsupported_conditions: List[str] = []

    for token in tokens:
        try:
            parsed_rules.append(_parse_rule(token))
        except ValueError:
            unsupported_conditions.append(token)

    return {
        "rules": parsed_rules,
        "unsupported_conditions": unsupported_conditions,
    }


def estimate_strategy_lookback_days(strategy: Dict) -> int:
    lookback = 1
    for rule in strategy.get("entry_rules", []) + strategy.get("exit_rules", []):
        indicator = rule.get("indicator")
        params = rule.get("params", {})
        if indicator in {"close_above_ma", "close_below_ma", "volume_above_ma"}:
            lookback = max(lookback, int(params.get("window", 1)))
        if indicator in {"foreign_consecutive_buy", "trust_consecutive_buy", "dealer_consecutive_buy"}:
            lookback = max(lookback, int(params.get("days", 1)))
    return lookback


def interpret_strategy_text(
    stock_id: str,
    start_date: str,
    end_date: str,
    strategy_text: str,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
):
    normalized = _normalize_text(strategy_text)
    if not normalized:
        raise ValueError("Strategy text cannot be blank")

    parts = [part.strip() for part in normalized.split(";") if part.strip()]
    if len(parts) != 2:
        raise ValueError("Strategy text must contain one buy clause and one sell clause separated by ；")

    entry_text = _strip_trade_action(parts[0])
    exit_text = _strip_trade_action(parts[1])

    entry_tokens = [token.strip() for token in re.split(r"且", entry_text) if token.strip()]
    exit_logic = "all" if "且" in exit_text else "any"
    exit_tokens = [token.strip() for token in re.split(r"且|或", exit_text) if token.strip()]
    if not entry_tokens or not exit_tokens:
        raise ValueError("Strategy text must include both entry and exit conditions")

    parsed_entry = _parse_rules(entry_tokens)
    parsed_exit = _parse_rules(exit_tokens)
    unsupported_conditions = parsed_entry["unsupported_conditions"] + parsed_exit["unsupported_conditions"]

    strategy = {
        "stock_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "trade_timing": DEFAULT_TRADE_TIMING,
        "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
        "entry_logic": "all",
        "exit_logic": exit_logic,
        "entry_rules": parsed_entry["rules"],
        "exit_rules": parsed_exit["rules"],
    }

    warnings = []
    if unsupported_conditions:
        warnings.append("部分條件目前不支援，因此無法直接執行這組策略。")
    if not strategy["entry_rules"] or not strategy["exit_rules"]:
        warnings.append("策略至少需要一個可解析的進場條件與一個可解析的出場條件。")

    normalized_text = (
        f"買進：{entry_text.replace('收盤價', '收盤價 ').replace('均線', ' 均線').strip()}；"
        f"賣出：{exit_text.replace('收盤價', '收盤價 ').replace('均線', ' 均線').strip()}"
    )

    return {
        "supported": not unsupported_conditions and bool(strategy["entry_rules"]) and bool(strategy["exit_rules"]),
        "normalized_text": normalized_text,
        "strategy": strategy,
        "unsupported_conditions": unsupported_conditions,
        "warnings": warnings,
    }
