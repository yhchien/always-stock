import re
from typing import Dict, List, Optional

from app.backtest_catalog import (
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_POSITION_SIZE_PCT,
    DEFAULT_TRADE_TIMING,
)


def _try_ai_map_unsupported(entry_unsupported: List[str], exit_unsupported: List[str]) -> Dict:
    """嘗試用 AI 補充解析 rule-based parser 無法識別的條件。回傳空結果而不拋例外。"""
    try:
        from app.backtest_ai_mapping import map_conditions_with_ai  # lazy import 避免循環依賴

        all_phrases = list(dict.fromkeys(entry_unsupported + exit_unsupported))
        ai_result = map_conditions_with_ai(all_phrases)
        return ai_result
    except Exception:
        return {"rules": [], "risk_controls": {}, "unsupported_conditions": list(entry_unsupported + exit_unsupported), "matched_capabilities": [], "source": "rule_based"}


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


def _parse_percent(token: str, keyword: str) -> Optional[float]:
    patterns = [
        rf"(?:固定)?{keyword}\s*(\d+(?:\.\d+)?)\s*%",
        rf"(\d+(?:\.\d+)?)\s*%\s*(?:固定)?{keyword}",
    ]
    for pattern in patterns:
        match = re.search(pattern, token)
        if match:
            return float(match.group(1))
    return None


def _normalize_ma_notation(token: str) -> str:
    """把 MA5 / 5MA / ma5 / 5ma 等寫法統一轉為「5日均線」。
    不能用 \\b 因為 Python 3 把中文視為 word character，導致「MA」後接中文時邊界判斷失效。
    """
    token = re.sub(r"(?<!\d)(\d+)\s*[Mm][Aa](?!\d)", r"\1日均線", token)
    token = re.sub(r"(?<![a-zA-Z])[Mm][Aa]\s*(\d+)(?!\d)", r"\1日均線", token)
    return token


def _parse_rule(token: str) -> Dict:
    token = _normalize_ma_notation(token.strip())

    # Cross 要先判斷，避免「5日均線跌破20日均線」被誤判為 close_below_ma
    ma_cross_match = re.search(r"(\d+)日均線(黃金交叉|上穿|突破)(\d+)日均線", token)
    if ma_cross_match:
        return {
            "indicator": "ma_golden_cross",
            "params": {"short_window": int(ma_cross_match.group(1)), "long_window": int(ma_cross_match.group(3))},
        }

    ma_dead_cross_match = re.search(r"(\d+)日均線(死亡交叉|下穿|跌破)(\d+)日均線", token)
    if ma_dead_cross_match:
        return {
            "indicator": "ma_dead_cross",
            "params": {"short_window": int(ma_dead_cross_match.group(1)), "long_window": int(ma_dead_cross_match.group(3))},
        }

    # 收盤價 prefix 可選，允許「跌破MA20」「站上20日均線」等省略前綴的寫法
    ma_match = re.search(r"(?:收盤價)?(站上|跌破)(\d+)日均線", token)
    if ma_match:
        indicator = "close_above_ma" if ma_match.group(1) == "站上" else "close_below_ma"
        return {"indicator": indicator, "params": {"window": int(ma_match.group(2))}}

    breakout_high_match = re.search(r"(?:收盤價)?(?:突破|站上)(\d+)日高點", token)
    if breakout_high_match:
        return {"indicator": "close_breakout_high", "params": {"window": int(breakout_high_match.group(1))}}

    breakdown_low_match = re.search(r"(?:收盤價)?(?:跌破|失守)(\d+)日低點", token)
    if breakdown_low_match:
        return {"indicator": "close_breakdown_low", "params": {"window": int(breakdown_low_match.group(1))}}

    volume_ma_match = re.search(r"成交量高於(\d+)日均量", token)
    if volume_ma_match:
        return {"indicator": "volume_above_ma", "params": {"window": int(volume_ma_match.group(1))}}

    volume_ratio_match = re.search(r"成交量(?:暴增至|大於|超過)(\d+)日均量的?(\d+(?:\.\d+)?)倍(?:以上)?", token)
    if volume_ratio_match:
        return {
            "indicator": "volume_ratio_above_ma",
            "params": {"window": int(volume_ratio_match.group(1)), "ratio": float(volume_ratio_match.group(2))},
        }

    inst_streak_buy_match = re.search(r"(外資|投信|自營商)連買(\d+)天", token)
    if inst_streak_buy_match:
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        return {
            "indicator": f"{inst_map[inst_streak_buy_match.group(1)]}_consecutive_buy",
            "params": {"days": int(inst_streak_buy_match.group(2))},
        }

    inst_streak_sell_match = re.search(r"(外資|投信|自營商)連賣(\d+)天", token)
    if inst_streak_sell_match:
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        return {
            "indicator": f"{inst_map[inst_streak_sell_match.group(1)]}_consecutive_sell",
            "params": {"days": int(inst_streak_sell_match.group(2))},
        }

    all_inst_negative_match = re.search(r"三大法人(?:合計)?(?:轉賣|賣超)", token)
    if all_inst_negative_match:
        return {"indicator": "all_inst_net_negative", "params": {}}

    all_inst_positive_match = re.search(r"三大法人(?:合計)?買超", token)
    if all_inst_positive_match:
        return {"indicator": "all_inst_net_positive", "params": {}}

    inst_positive_match = re.search(r"(外資|投信|自營商)買超", token)
    if inst_positive_match:
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        inst_key = inst_map[inst_positive_match.group(1)]
        return {"indicator": f"{inst_key}_net_positive", "params": {}}

    inst_negative_match = re.search(r"(外資|投信|自營商)(?:轉賣|賣超)", token)
    if inst_negative_match:
        inst_map = {"外資": "foreign", "投信": "trust", "自營商": "dealer"}
        inst_key = inst_map[inst_negative_match.group(1)]
        return {"indicator": f"{inst_key}_net_negative", "params": {}}

    raise ValueError(f"Unsupported condition: {token}")


def _parse_tokens(tokens: List[str]) -> Dict[str, object]:
    parsed_rules: List[Dict] = []
    unsupported_conditions: List[str] = []
    risk_controls: Dict[str, float] = {}

    for token in tokens:
        stop_loss_pct = _parse_percent(token, "停損")
        if stop_loss_pct is not None:
            risk_controls["stop_loss_pct"] = stop_loss_pct
            continue

        take_profit_pct = _parse_percent(token, "停利")
        if take_profit_pct is not None:
            risk_controls["take_profit_pct"] = take_profit_pct
            continue

        try:
            parsed_rules.append(_parse_rule(token))
        except ValueError:
            unsupported_conditions.append(token)

    return {
        "rules": parsed_rules,
        "unsupported_conditions": unsupported_conditions,
        "risk_controls": risk_controls,
    }


def _rule_to_text(rule: Dict) -> str:
    indicator = rule["indicator"]
    params = rule.get("params", {})
    if indicator == "close_above_ma":
        return f"收盤價站上{params['window']}日均線"
    if indicator == "close_below_ma":
        return f"收盤價跌破{params['window']}日均線"
    if indicator == "ma_golden_cross":
        return f"{params['short_window']}日均線黃金交叉{params['long_window']}日均線"
    if indicator == "ma_dead_cross":
        return f"{params['short_window']}日均線死亡交叉{params['long_window']}日均線"
    if indicator == "close_breakout_high":
        return f"收盤價突破{params['window']}日高點"
    if indicator == "close_breakdown_low":
        return f"收盤價跌破{params['window']}日低點"
    if indicator == "volume_above_ma":
        return f"成交量高於{params['window']}日均量"
    if indicator == "volume_ratio_above_ma":
        return f"成交量暴增至{params['window']}日均量的{params['ratio']:g}倍以上"
    if indicator == "foreign_consecutive_buy":
        return f"外資連買{params['days']}天"
    if indicator == "trust_consecutive_buy":
        return f"投信連買{params['days']}天"
    if indicator == "dealer_consecutive_buy":
        return f"自營商連買{params['days']}天"
    if indicator == "foreign_consecutive_sell":
        return f"外資連賣{params['days']}天"
    if indicator == "trust_consecutive_sell":
        return f"投信連賣{params['days']}天"
    if indicator == "dealer_consecutive_sell":
        return f"自營商連賣{params['days']}天"
    if indicator == "foreign_net_positive":
        return "外資買超"
    if indicator == "trust_net_positive":
        return "投信買超"
    if indicator == "dealer_net_positive":
        return "自營商買超"
    if indicator == "foreign_net_negative":
        return "外資賣超"
    if indicator == "trust_net_negative":
        return "投信賣超"
    if indicator == "dealer_net_negative":
        return "自營商賣超"
    if indicator == "all_inst_net_positive":
        return "三大法人合計買超"
    if indicator == "all_inst_net_negative":
        return "三大法人合計轉賣"
    return indicator


def estimate_strategy_lookback_days(strategy: Dict) -> int:
    lookback = 1
    for rule in strategy.get("entry_rules", []) + strategy.get("exit_rules", []):
        indicator = rule.get("indicator")
        params = rule.get("params", {})
        if indicator in {"close_above_ma", "close_below_ma", "volume_above_ma", "volume_ratio_above_ma"}:
            lookback = max(lookback, int(params.get("window", 1)))
        if indicator in {"close_breakout_high", "close_breakdown_low"}:
            lookback = max(lookback, int(params.get("window", 1)) + 1)
        if indicator in {"ma_golden_cross", "ma_dead_cross"}:
            lookback = max(lookback, int(params.get("long_window", 1)) + 1)
        if indicator in {
            "foreign_consecutive_buy", "trust_consecutive_buy", "dealer_consecutive_buy",
            "foreign_consecutive_sell", "trust_consecutive_sell", "dealer_consecutive_sell",
        }:
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

    parsed_entry = _parse_tokens(entry_tokens)
    parsed_exit = _parse_tokens(exit_tokens)
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
        "stop_loss_pct": parsed_entry["risk_controls"].get("stop_loss_pct") or parsed_exit["risk_controls"].get("stop_loss_pct"),
        "take_profit_pct": parsed_entry["risk_controls"].get("take_profit_pct")
        or parsed_exit["risk_controls"].get("take_profit_pct"),
    }

    ai_mapped_conditions: List[str] = []

    if unsupported_conditions:
        ai_result = _try_ai_map_unsupported(
            parsed_entry["unsupported_conditions"],
            parsed_exit["unsupported_conditions"],
        )

        if ai_result["rules"] or ai_result["risk_controls"]:
            # 把 AI 解出的 rules 追加到 entry/exit（此處無法區分屬於 entry 或 exit，
            # 暫時依照「進場條件未滿則給 entry，否則給 exit」的簡單規則）
            ai_rules = list(ai_result["rules"])
            for rule in ai_rules:
                indicator = rule.get("indicator", "")
                # 正向信號（連買/站上/突破）傾向 entry；負向信號傾向 exit
                is_negative = any(k in indicator for k in ("net_negative", "dead_cross", "below", "breakdown"))
                if is_negative:
                    strategy["exit_rules"].append(rule)
                else:
                    strategy["entry_rules"].append(rule)
                ai_mapped_conditions.append(indicator)

            for ctrl_id, ctrl_val in ai_result["risk_controls"].items():
                if ctrl_id == "stop_loss_pct" and not strategy.get("stop_loss_pct"):
                    strategy["stop_loss_pct"] = ctrl_val
                    ai_mapped_conditions.append("stop_loss_pct")
                elif ctrl_id == "take_profit_pct" and not strategy.get("take_profit_pct"):
                    strategy["take_profit_pct"] = ctrl_val
                    ai_mapped_conditions.append("take_profit_pct")

            unsupported_conditions = [c for c in unsupported_conditions if c in ai_result["unsupported_conditions"]]

    warnings = []
    if unsupported_conditions:
        warnings.append("部分條件目前不支援，因此無法直接執行這組策略。")
    if not strategy["entry_rules"] or not strategy["exit_rules"]:
        warnings.append("策略至少需要一個可解析的進場條件與一個可解析的出場條件。")

    entry_sep = "且" if strategy["entry_logic"] == "all" else "或"
    exit_sep = "且" if strategy["exit_logic"] == "all" else "或"
    entry_parts = [_rule_to_text(rule) for rule in strategy["entry_rules"]]
    exit_parts = [_rule_to_text(rule) for rule in strategy["exit_rules"]]
    if strategy.get("stop_loss_pct"):
        exit_parts.append(f"停損{strategy['stop_loss_pct']:g}%")
    if strategy.get("take_profit_pct"):
        exit_parts.append(f"停利{strategy['take_profit_pct']:g}%")
    normalized_text = f"買進：{entry_sep.join(entry_parts)}；賣出：{exit_sep.join(exit_parts)}"

    return {
        "supported": not unsupported_conditions and bool(strategy["entry_rules"]) and bool(strategy["exit_rules"]),
        "normalized_text": normalized_text,
        "strategy": strategy,
        "unsupported_conditions": unsupported_conditions,
        "ai_mapped_conditions": ai_mapped_conditions,
        "warnings": warnings,
    }
