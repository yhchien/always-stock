import re
from typing import Dict

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


def interpret_strategy_text(
    stock_id: str,
    start_date: str,
    end_date: str,
    strategy_text: str,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
):
    normalized = _normalize_text(strategy_text)
    parts = [part.strip() for part in normalized.split(";") if part.strip()]
    if len(parts) != 2:
        raise ValueError("Strategy text must contain one buy clause and one sell clause separated by ；")

    entry_text = _strip_trade_action(parts[0])
    exit_text = _strip_trade_action(parts[1])

    entry_tokens = [token.strip() for token in re.split(r"且", entry_text) if token.strip()]
    exit_logic = "all" if "且" in exit_text else "any"
    exit_tokens = [token.strip() for token in re.split(r"且|或", exit_text) if token.strip()]

    strategy = {
        "stock_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "trade_timing": DEFAULT_TRADE_TIMING,
        "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
        "entry_logic": "all",
        "exit_logic": exit_logic,
        "entry_rules": [_parse_rule(token) for token in entry_tokens],
        "exit_rules": [_parse_rule(token) for token in exit_tokens],
    }

    normalized_text = (
        f"買進：{entry_text.replace('收盤價', '收盤價 ').replace('均線', ' 均線').strip()}；"
        f"賣出：{exit_text.replace('收盤價', '收盤價 ').replace('均線', ' 均線').strip()}"
    )

    return {
        "supported": True,
        "normalized_text": normalized_text,
        "strategy": strategy,
        "unsupported_conditions": [],
        "warnings": [],
    }
