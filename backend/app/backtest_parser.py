import re
from typing import Dict, List, Optional, Tuple

from app.backtest_catalog import (
    DEFAULT_INITIAL_CAPITAL,
    DEFAULT_POSITION_SIZE_PCT,
    DEFAULT_TRADE_TIMING,
)
from app.backtest_patterns import PATTERN_LOOKBACK


def _try_ai_map(phrases: List[str]) -> Dict:
    """對 rule-based parser 無法識別的條件丟 AI；異常或失敗回空結果。"""
    if not phrases:
        return {
            "rules": [],
            "risk_controls": {},
            "unsupported_conditions": [],
            "matched_capabilities": [],
            "source": "rule_based",
        }
    try:
        from app.backtest_ai_mapping import map_conditions_with_ai  # lazy import

        return map_conditions_with_ai(phrases)
    except Exception:
        return {
            "rules": [],
            "risk_controls": {},
            "unsupported_conditions": list(phrases),
            "matched_capabilities": [],
            "source": "rule_based",
        }


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
    """把 MA5 / 5MA / ma5 / 5ma 等寫法統一轉為「5日均線」。"""
    token = re.sub(r"(?<!\d)(\d+)\s*[Mm][Aa](?!\d)", r"\1日均線", token)
    token = re.sub(r"(?<![a-zA-Z])[Mm][Aa]\s*(\d+)(?!\d)", r"\1日均線", token)
    return token


# 關鍵字 → indicator id 對照（單 token、無參數的型態）
_PATTERN_KEYWORDS: List[Tuple[str, str]] = [
    # 單根 K 棒
    ("十字星", "candle_doji"),
    ("十字線", "candle_doji"),
    ("錘子線", "candle_hammer"),
    ("錘子", "candle_hammer"),
    ("吊人線", "candle_hanging_man"),
    ("吊頸線", "candle_hanging_man"),
    ("流星線", "candle_shooting_star"),
    ("射擊之星", "candle_shooting_star"),
    ("倒錘線", "candle_inverted_hammer"),
    ("倒槌線", "candle_inverted_hammer"),
    ("長紅K", "candle_long_bullish"),
    ("長紅", "candle_long_bullish"),
    ("大陽線", "candle_long_bullish"),
    ("長黑K", "candle_long_bearish"),
    ("長黑", "candle_long_bearish"),
    ("大陰線", "candle_long_bearish"),
    # 組合 K 棒（順序要把更長的詞放前面，避免 substring 誤匹配）
    ("多頭吞噬", "candle_bullish_engulfing"),
    ("看漲吞噬", "candle_bullish_engulfing"),
    ("紅K吞噬", "candle_bullish_engulfing"),
    ("空頭吞噬", "candle_bearish_engulfing"),
    ("看跌吞噬", "candle_bearish_engulfing"),
    ("黑K吞噬", "candle_bearish_engulfing"),
    ("紅三兵", "candle_three_white_soldiers"),
    ("三白兵", "candle_three_white_soldiers"),
    ("三隻烏鴉", "candle_three_black_crows"),
    ("黑三兵", "candle_three_black_crows"),
    ("三烏鴉", "candle_three_black_crows"),
    ("早晨之星", "candle_morning_star"),
    ("晨星", "candle_morning_star"),
    ("黃昏之星", "candle_evening_star"),
    ("夜星", "candle_evening_star"),
    ("好友反攻", "candle_bullish_piercing"),
    ("刺透線", "candle_bullish_piercing"),
    ("刺透", "candle_bullish_piercing"),
    ("烏雲蓋頂", "candle_dark_cloud_cover"),
    ("烏雲罩頂", "candle_dark_cloud_cover"),
    # 型態
    ("頭肩頂", "pattern_head_shoulders_top"),
    ("頭肩底", "pattern_head_shoulders_bottom"),
    ("逆頭肩", "pattern_head_shoulders_bottom"),
    ("雙重頂", "pattern_double_top"),
    ("雙頂", "pattern_double_top"),
    ("M頭", "pattern_double_top"),
    ("M 頭", "pattern_double_top"),
    ("雙重底", "pattern_double_bottom"),
    ("雙底", "pattern_double_bottom"),
    ("W底", "pattern_double_bottom"),
    ("W 底", "pattern_double_bottom"),
    ("V型反轉", "pattern_v_reversal"),
    ("V 型反轉", "pattern_v_reversal"),
    ("V反轉", "pattern_v_reversal"),
    ("A型反轉", "pattern_a_reversal"),
    ("A 型反轉", "pattern_a_reversal"),
    ("A反轉", "pattern_a_reversal"),
    ("倒V反轉", "pattern_a_reversal"),
    ("倒 V 反轉", "pattern_a_reversal"),
]


def _match_pattern_keyword(token: str) -> Optional[str]:
    for keyword, indicator in _PATTERN_KEYWORDS:
        if keyword in token:
            return indicator
    return None


def _parse_rule(token: str) -> Dict:
    token = _normalize_ma_notation(token.strip())

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

    # K 棒 / 型態 關鍵字
    pattern_id = _match_pattern_keyword(token)
    if pattern_id is not None:
        return {"indicator": pattern_id, "params": {}}

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
    # K 棒 / 型態：反查 label（用 keyword 對應的第一個中文詞）
    pattern_label_map = {
        "candle_doji": "十字星",
        "candle_hammer": "錘子線",
        "candle_hanging_man": "吊人線",
        "candle_shooting_star": "流星線",
        "candle_inverted_hammer": "倒錘線",
        "candle_long_bullish": "長紅 / 大陽線",
        "candle_long_bearish": "長黑 / 大陰線",
        "candle_bullish_engulfing": "看漲吞噬",
        "candle_bearish_engulfing": "看跌吞噬",
        "candle_three_white_soldiers": "紅三兵",
        "candle_three_black_crows": "三隻烏鴉",
        "candle_morning_star": "晨星",
        "candle_evening_star": "夜星",
        "candle_bullish_piercing": "好友反攻",
        "candle_dark_cloud_cover": "烏雲蓋頂",
        "pattern_head_shoulders_top": "頭肩頂",
        "pattern_head_shoulders_bottom": "頭肩底",
        "pattern_double_top": "雙頂（M 頭）",
        "pattern_double_bottom": "雙底（W 底）",
        "pattern_v_reversal": "V 型反轉",
        "pattern_a_reversal": "A 型反轉",
    }
    if indicator in pattern_label_map:
        return f"出現{pattern_label_map[indicator]}"
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
        if indicator in PATTERN_LOOKBACK:
            lookback = max(lookback, PATTERN_LOOKBACK[indicator])
    return lookback


def _split_tokens(text: str) -> Tuple[List[str], str]:
    """把條件文字切成 tokens，並判斷 logic（"且"=all, 否則 any）。"""
    logic = "all" if "且" in text else "any"
    tokens = [token.strip() for token in re.split(r"且|或", text) if token.strip()]
    return tokens, logic


def _process_clause(text: str) -> Dict:
    """rule-based 解一段條件子句，回傳 rules/unsupported/risk_controls/logic。"""
    normalized = _strip_trade_action(_normalize_text(text))
    tokens, logic = _split_tokens(normalized)
    parsed = _parse_tokens(tokens)
    return {
        "logic": logic,
        "rules": parsed["rules"],
        "unsupported_conditions": parsed["unsupported_conditions"],
        "risk_controls": parsed["risk_controls"],
    }


def interpret_strategy_parts(
    stock_id: str,
    start_date: str,
    end_date: str,
    entry_text: str,
    exit_text: str,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> Dict:
    if not entry_text or not entry_text.strip():
        raise ValueError("Entry text cannot be blank")
    if not exit_text or not exit_text.strip():
        raise ValueError("Exit text cannot be blank")

    entry_parsed = _process_clause(entry_text)
    exit_parsed = _process_clause(exit_text)

    # 對未解析條件分別丟 AI（entry 解出的加到 entry、exit 解出的加到 exit）
    entry_ai = _try_ai_map(entry_parsed["unsupported_conditions"])
    exit_ai = _try_ai_map(exit_parsed["unsupported_conditions"])

    entry_rules = list(entry_parsed["rules"]) + list(entry_ai["rules"])
    exit_rules = list(exit_parsed["rules"]) + list(exit_ai["rules"])

    ai_mapped_conditions: List[str] = []
    for rule in entry_ai["rules"] + exit_ai["rules"]:
        ai_mapped_conditions.append(rule["indicator"])

    # 停損停利優先吃明確參數，否則吃文字裡解出的（entry/exit/AI 任一）
    def _pick_pct(explicit: Optional[float], *sources: Dict) -> Optional[float]:
        if explicit is not None:
            return float(explicit)
        for source in sources:
            if source and source.get("stop_loss_pct") is not None:
                return source["stop_loss_pct"]
        return None

    final_stop_loss = stop_loss_pct if stop_loss_pct is not None else None
    if final_stop_loss is None:
        for source in (entry_parsed["risk_controls"], exit_parsed["risk_controls"], entry_ai["risk_controls"], exit_ai["risk_controls"]):
            if "stop_loss_pct" in source:
                final_stop_loss = source["stop_loss_pct"]
                if source in (entry_ai["risk_controls"], exit_ai["risk_controls"]):
                    ai_mapped_conditions.append("stop_loss_pct")
                break

    final_take_profit = take_profit_pct if take_profit_pct is not None else None
    if final_take_profit is None:
        for source in (entry_parsed["risk_controls"], exit_parsed["risk_controls"], entry_ai["risk_controls"], exit_ai["risk_controls"]):
            if "take_profit_pct" in source:
                final_take_profit = source["take_profit_pct"]
                if source in (entry_ai["risk_controls"], exit_ai["risk_controls"]):
                    ai_mapped_conditions.append("take_profit_pct")
                break

    unsupported_conditions = list(entry_ai["unsupported_conditions"]) + list(exit_ai["unsupported_conditions"])

    strategy = {
        "stock_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "trade_timing": DEFAULT_TRADE_TIMING,
        "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
        "entry_logic": entry_parsed["logic"],
        "exit_logic": exit_parsed["logic"],
        "entry_rules": entry_rules,
        "exit_rules": exit_rules,
        "stop_loss_pct": final_stop_loss,
        "take_profit_pct": final_take_profit,
    }

    warnings: List[str] = []
    if unsupported_conditions:
        warnings.append("部分條件即便 AI 輔助也無法對應到支援的指標。")
    if not entry_rules or not exit_rules:
        warnings.append("策略至少需要一個可解析的進場條件與一個可解析的出場條件。")

    entry_sep = "且" if strategy["entry_logic"] == "all" else "或"
    exit_sep = "且" if strategy["exit_logic"] == "all" else "或"
    entry_parts = [_rule_to_text(rule) for rule in entry_rules]
    exit_parts = [_rule_to_text(rule) for rule in exit_rules]
    if final_stop_loss is not None:
        exit_parts.append(f"停損{float(final_stop_loss):g}%")
    if final_take_profit is not None:
        exit_parts.append(f"停利{float(final_take_profit):g}%")

    normalized_text = f"買進：{entry_sep.join(entry_parts) or '（無）'}；賣出：{exit_sep.join(exit_parts) or '（無）'}"

    return {
        "supported": not unsupported_conditions and bool(entry_rules) and bool(exit_rules),
        "normalized_text": normalized_text,
        "strategy": strategy,
        "unsupported_conditions": unsupported_conditions,
        "ai_mapped_conditions": list(dict.fromkeys(ai_mapped_conditions)),
        "warnings": warnings,
    }


def interpret_strategy_text(
    stock_id: str,
    start_date: str,
    end_date: str,
    strategy_text: str,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
) -> Dict:
    """向後相容：接受 `買進：...；賣出：...` 單一文字輸入。"""
    normalized = _normalize_text(strategy_text)
    if not normalized:
        raise ValueError("Strategy text cannot be blank")

    parts = [part.strip() for part in normalized.split(";") if part.strip()]
    if len(parts) != 2:
        raise ValueError("Strategy text must contain one buy clause and one sell clause separated by ；")

    entry_text = parts[0]
    exit_text = parts[1]

    return interpret_strategy_parts(
        stock_id=stock_id,
        start_date=start_date,
        end_date=end_date,
        entry_text=entry_text,
        exit_text=exit_text,
        initial_capital=initial_capital,
    )
