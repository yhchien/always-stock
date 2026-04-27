"""
M23 Step 0 / 7 / 8 / 9：LLM batch research + explanation + market_state + 最終 payload。

Slice 6（2026-04-26）：實作完成。

對應 spec：
  - §3.2 LLM 上網查詢的資料 context
  - §5 Step 0（market_state）/ Step 7（Research Layer）/ Step 8（Explanation Layer）/ Step 9（最終組裝）
  - §10 Input / Output JSON Schema
  - §11.5 LLM batch 後 commit progress

設計：
  - 一次 prompt 處理 5~10 檔（DEFAULT_BATCH_SIZE），控制 cost / 時間
  - 第一版用支援 web search 的模型（`gpt-4o-search-preview` 或同等）
  - 沒 web search 或 OpenAI 不可用 → 每檔走 fallback dict（標記 `_unavailable`），pipeline 仍可完成 snapshot
  - System prompt 直接讀 `backend/app/prompts/watch-list-stock.md`（spec §10 對齊）；
    user_msg 內提示「只執行 STEP X」讓 LLM 聚焦於當前批次任務
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.settings import get_openai_api_key

logger = logging.getLogger(__name__)


# Spec §5 Step 7：「一次 prompt 處理 5~10 檔（batch）」，第一版取中間值
DEFAULT_BATCH_SIZE = 8

# Spec §3.2：第一版模型 fallback；workflow / Render env 可由 OPENAI_MODEL 覆寫
# （`.github/workflows/daily_signals.yml` step env 會把它設成 secrets.OPENAI_SIGNALS_MODEL
# 或預設 "gpt-4o-search-preview"）。`DEFAULT_MODEL` 在 module 載入時 snapshot env，
# 所有 entry function 預設參數都吃這個值，所以 caller 不必每次 explicit 傳 model。
_FALLBACK_MODEL = "gpt-4o-search-preview"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", _FALLBACK_MODEL).strip()

# 系統 prompt 路徑（spec §10 LLM I/O contract 全文）
_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "watch-list-stock.md"
)

# OpenAI 回應的 max tokens；reason 規則要求 500-1000 字 × batch 8 → 預留充足
_MAX_OUTPUT_TOKENS = 8000


def assemble_market_context(
    db_market_snapshot: Dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Step 0：判斷 market_state（STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK）。

    LLM 上網查 VIX / 美股 / 台指期 / USD-TWD，搭配 DB 已知數字判斷。
    LLM 不可用 → 回 fallback dict（market_state="RANGE"，reason 標記不可用）。
    """
    system_prompt = _load_system_prompt()
    user_msg = (
        "[只執行 STEP 0：判斷今日市場狀態]\n"
        "你必須上網查詢 VIX、美股、台指期、USD/TWD，搭配下方 DB 已知數字，"
        "判斷 market_state。\n\n"
        f"[DB 已知市場數字]\n"
        f"{json.dumps(db_market_snapshot, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "market_state": "STRONG_BULL | STRUCTURAL_BULL | RANGE | WEAK",\n'
        '  "taiex_change_pct": number,\n'
        '  "otc_change_pct": number,\n'
        '  "vix_status": "risk_on | neutral | risk_off",\n'
        '  "futures_bias": "LONG | SHORT | NEUTRAL",\n'
        '  "market_state_reason": "繁體中文說明"\n'
        "}\n"
    )

    payload = _call_llm_json(system_prompt, user_msg, model=model)
    if payload is None:
        return _market_context_fallback()

    return {
        "market_state": payload.get("market_state", "RANGE"),
        "taiex_change_pct": payload.get("taiex_change_pct"),
        "otc_change_pct": payload.get("otc_change_pct"),
        "vix_status": payload.get("vix_status"),
        "futures_bias": payload.get("futures_bias"),
        "market_state_reason": payload.get("market_state_reason", ""),
    }


def run_research_batch(
    stocks_batch: List[Dict[str, Any]],
    market_context: Optional[Dict[str, Any]] = None,
    *,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Step 7：LLM Research Layer。

    對 batch 內每檔股票上網查詢公司業務、產業鏈位置、題材延續性、龍頭股 / 集團股。
    輸出每檔 dict 含 spec §10.2 watchlist[] 的 research 半段欄位
    （`type` / `business_summary` / `supply_chain_position` / `theme_fit` / `theme` /
    `group_info` / `leader_check`）。

    回傳順序與輸入對齊（同 stock_id 配對；缺失走 fallback）。
    """
    if not stocks_batch:
        return []

    market_context = market_context or {}
    system_prompt = _load_system_prompt()
    user_msg = (
        "[只執行 STEP 2 / STEP 3 / STEP 4：research 部分]\n"
        "對下列每檔股票，請上網查詢主要業務、題材延續性、產業鏈位置、龍頭 / 集團，"
        "並輸出每檔 research result。**不要做最終 decision**（那是 STEP 5-9 的事）。\n\n"
        f"[market_context]\n"
        f"{json.dumps(market_context, ensure_ascii=False, indent=2)}\n\n"
        f"[stocks_batch]\n"
        f"{json.dumps(stocks_batch, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "research": [\n'
        '    {\n'
        '      "stock": "股票代碼",\n'
        '      "name": "股票名稱",\n'
        '      "industry": "產業",\n'
        '      "sub_industry": "細產業",\n'
        '      "type": "LEADER | FOLLOWER | LAGGARD",\n'
        '      "business_summary": "公司主要業務說明",\n'
        '      "supply_chain_position": "upstream | midstream | downstream | equipment | component | material | brand | channel | service | other",\n'
        '      "theme_fit": "HIGH | MEDIUM | LOW | NONE",\n'
        '      "theme": { "main_theme": "...", "theme_duration": "short | 1Q | 2Q_plus", "theme_score": 0-3, "theme_reason": "..." },\n'
        '      "group_info": { "is_group_stock": bool, "group_name": "..." | null, "related_group_stocks": [...], "group_price_sync": "strong | moderate | weak | none" },\n'
        '      "leader_check": { "industry_leader": "...", "leader_price_trend": "strong_up | up | flat | down", "leader_supports_theme": bool }\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )

    payload = _call_llm_json(system_prompt, user_msg, model=model)
    if payload is None:
        return [_research_fallback(s) for s in stocks_batch]

    research = payload.get("research")
    if not isinstance(research, list):
        return [_research_fallback(s) for s in stocks_batch]

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in research:
        if not isinstance(item, dict):
            continue
        sid = item.get("stock") or item.get("stock_id")
        if sid:
            by_id[str(sid)] = item

    aligned: List[Dict[str, Any]] = []
    for stock in stocks_batch:
        sid = str(stock.get("stock_id") or stock.get("stock") or "")
        if sid in by_id:
            aligned.append({**stock, **by_id[sid], "stock": sid})
        else:
            aligned.append(_research_fallback(stock))
    return aligned


def run_explanation_batch(
    research_results: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Step 8：LLM Explanation Layer。

    依 market_state gating + 籌碼 / 技術判斷 → 產出 `signals` / `decision`（WATCH / REMOVE）/
    500-1000 字 reason（13 點強制要點，見 watch-list-stock.md）。

    內部依 DEFAULT_BATCH_SIZE 拆 chunk（避免單次 prompt 太長）；
    對 caller 而言一次拿到完整 explanation list。
    """
    if not research_results:
        return []

    out: List[Dict[str, Any]] = []
    for i in range(0, len(research_results), DEFAULT_BATCH_SIZE):
        chunk = research_results[i : i + DEFAULT_BATCH_SIZE]
        out.extend(_run_explanation_chunk(chunk, market_context, model=model))
    return out


def assemble_final_output(
    market_context: Dict[str, Any],
    explanation: List[Dict[str, Any]],
    *,
    candidate_pool_size: int,
    model: str = DEFAULT_MODEL,
    total_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Step 9：拆 watchlist / removed、計算 summary、組裝最終 payload。

    對齊 spec §10.2 完整 schema：
      market_context / watchlist / removed / summary +
      candidate_pool_size / final_watchlist_size / llm_model / llm_total_tokens
    """
    watchlist: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for item in explanation:
        decision = str(item.get("decision") or "REMOVE").upper()
        if decision == "WATCH":
            watchlist.append(_format_watch_entry(item))
        else:
            removed.append(
                {
                    "stock": item.get("stock") or item.get("stock_id"),
                    "name": item.get("name", ""),
                    "remove_reason": item.get("reason")
                    or item.get("remove_reason")
                    or "",
                }
            )

    type_counts = {"LEADER": 0, "FOLLOWER": 0, "LAGGARD": 0}
    industries: List[str] = []
    seen_industries = set()
    for entry in watchlist:
        type_label = str(entry.get("type") or "").upper()
        if type_label in type_counts:
            type_counts[type_label] += 1
        ind = entry.get("industry")
        if ind and ind not in seen_industries:
            industries.append(ind)
            seen_industries.add(ind)

    summary = {
        "main_hot_industries": industries[:5],
        "leader_count": type_counts["LEADER"],
        "follower_count": type_counts["FOLLOWER"],
        "laggard_count": type_counts["LAGGARD"],
        "risk_note": market_context.get("market_state_reason", ""),
    }

    return {
        "market_context": market_context,
        "watchlist": watchlist,
        "removed": removed,
        "summary": summary,
        "candidate_pool_size": candidate_pool_size,
        "final_watchlist_size": len(watchlist),
        "llm_model": model,
        "llm_total_tokens": total_tokens,
    }


# ---------- internal helpers ----------


def _load_system_prompt() -> str:
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"M23 system prompt not found at {_PROMPT_PATH}. "
            "請確認 backend/app/prompts/watch-list-stock.md 已部署。"
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _call_llm_json(
    system_prompt: str,
    user_msg: str,
    *,
    model: str,
) -> Optional[Dict[str, Any]]:
    """呼叫 OpenAI 並嘗試 parse JSON。

    `gpt-4o-search-preview` 不支援 `temperature` / `response_format`，
    所以這裡只送基本 messages + max_completion_tokens。
    解析失敗 / API key 缺失 / 例外 → 回 None（caller 負責 fallback）。
    """
    api_key = get_openai_api_key()
    if not api_key:
        logger.warning(
            "OPENAI_API_KEY not configured; M23 LLM call skipped (model=%s)",
            model,
        )
        return None

    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_completion_tokens=_MAX_OUTPUT_TOKENS,
        )
        raw = response.choices[0].message.content or ""
        return _extract_json(raw)
    except Exception:
        logger.exception("M23 LLM call failed (model=%s)", model)
        return None


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """容錯地把 LLM 回傳文字解析成 dict（去 markdown fence / 去前後綴）。"""
    if not raw:
        return None
    text = raw.strip()

    # 移除 ```json ... ``` / ``` ... ``` markdown fence
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3]
        elif "```" in text:
            text = text[: text.rindex("```")]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "M23 LLM response is not valid JSON; first 500 chars: %s",
            text[:500],
        )
        return None


def _run_explanation_chunk(
    chunk: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str,
) -> List[Dict[str, Any]]:
    """單個 chunk 的 explanation LLM call（內部 helper）。"""
    system_prompt = _load_system_prompt()
    user_msg = (
        "[執行 STEP 5 / STEP 6 / STEP 7 / STEP 8 / STEP 9：依 market_state gating "
        "+ 籌碼 / 技術判斷 → 產出 decision (WATCH / REMOVE) + 500-1000 字 reason]\n\n"
        f"[market_context]\n"
        f"{json.dumps(market_context, ensure_ascii=False, indent=2)}\n\n"
        f"[research_results]\n"
        f"{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "items": [\n'
        '    {\n'
        '      "stock": "股票代碼",\n'
        '      "signals": { "capital_flow": "strong | moderate | weak", "chip_trend": "accumulating | neutral | weakening | retail_overheated | short_squeeze_potential", "margin_short_signal": "positive | neutral | negative", "technical_status": "breakout | steady_uptrend | early_turn | range_bound | distribution | weak" },\n'
        '      "decision": "WATCH | REMOVE",\n'
        '      "reason": "WATCH → 500-1000 字繁體中文（13 點強制要點）；REMOVE → 排除原因"\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )

    payload = _call_llm_json(system_prompt, user_msg, model=model)
    if payload is None:
        return [_explanation_fallback(r) for r in chunk]

    items = payload.get("items")
    if not isinstance(items, list):
        return [_explanation_fallback(r) for r in chunk]

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = item.get("stock") or item.get("stock_id")
        if sid:
            by_id[str(sid)] = item

    aligned: List[Dict[str, Any]] = []
    for research in chunk:
        sid = str(research.get("stock") or research.get("stock_id") or "")
        merged: Dict[str, Any] = {**research}
        if sid in by_id:
            ext = by_id[sid]
            merged["signals"] = ext.get("signals", _default_signals())
            merged["decision"] = str(ext.get("decision") or "REMOVE").upper()
            merged["reason"] = ext.get("reason", "")
        else:
            fb = _explanation_fallback(research)
            merged.update(fb)
        aligned.append(merged)
    return aligned


def _format_watch_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    """擷取 spec §10.2 watchlist[] 期望的欄位。"""
    return {
        "stock": item.get("stock") or item.get("stock_id"),
        "name": item.get("name", ""),
        "type": str(item.get("type") or "LEADER").upper(),
        "industry": item.get("industry"),
        "sub_industry": item.get("sub_industry"),
        "business_summary": item.get("business_summary", ""),
        "supply_chain_position": item.get("supply_chain_position", "other"),
        "theme_fit": item.get("theme_fit", "MEDIUM"),
        "theme": item.get("theme", {}),
        "group_info": item.get("group_info", {}),
        "leader_check": item.get("leader_check", {}),
        "signals": item.get("signals", _default_signals()),
        "decision": "WATCH",
        "reason": item.get("reason", ""),
    }


def _market_context_fallback() -> Dict[str, Any]:
    return {
        "market_state": "RANGE",
        "taiex_change_pct": None,
        "otc_change_pct": None,
        "vix_status": "neutral",
        "futures_bias": "NEUTRAL",
        "market_state_reason": "OpenAI 服務不可用，預設保守判斷為 RANGE。",
    }


def _research_fallback(stock: Dict[str, Any]) -> Dict[str, Any]:
    sid = stock.get("stock_id") or stock.get("stock") or ""
    return {
        **stock,
        "stock": sid,
        "name": stock.get("name", ""),
        "industry": stock.get("industry"),
        "sub_industry": stock.get("sub_industry"),
        "type": stock.get("prelim_type") or "LEADER",
        "business_summary": "（LLM 不可用，缺研究資料）",
        "supply_chain_position": "other",
        "theme_fit": "MEDIUM",
        "theme": {
            "main_theme": "",
            "theme_duration": "short",
            "theme_score": 0,
            "theme_reason": "LLM 不可用",
        },
        "group_info": {
            "is_group_stock": False,
            "group_name": None,
            "related_group_stocks": [],
            "group_price_sync": "none",
        },
        "leader_check": {
            "industry_leader": "",
            "leader_price_trend": "flat",
            "leader_supports_theme": False,
        },
        "_unavailable": True,
    }


def _explanation_fallback(research: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **research,
        "signals": _default_signals(),
        "decision": "REMOVE",
        "reason": "LLM 不可用，無法完成最終評估（標記為 REMOVE 以避免誤判）。",
    }


def _default_signals() -> Dict[str, str]:
    return {
        "capital_flow": "moderate",
        "chip_trend": "neutral",
        "margin_short_signal": "neutral",
        "technical_status": "range_bound",
    }
