"""
M23 Step 0 / 7 / 8 / 9：LLM batch research + short decision + WATCH 長理由 + market_state。

Slice 6（2026-04-26）：實作完成。

對應 spec：
  - §3.2 LLM 上網查詢的資料 context
  - §5 Step 0（market_state）/ Step 7（Research Layer）/ Step 8（Explanation Layer）/ Step 9（最終組裝）
  - §10 Input / Output JSON Schema
  - §11.5 LLM batch 後 commit progress

設計：
  - research / explanation 分開調 batch，避免長 explanation 卡住整批
  - 第一版用可搭配 Responses API `web_search` 的模型（例如現有 signals model）
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


# Spec §5 Step 7：「一次 prompt 處理 5~10 檔（batch）」；research 保持 8，
# explanation 降到 4 以降低單次 payload。
DEFAULT_RESEARCH_BATCH_SIZE = 8
DEFAULT_EXPLANATION_BATCH_SIZE = 4
MAX_FINAL_WATCHLIST_SIZE = 30

# Spec §3.2：第一版模型 fallback；workflow / Render env 可由 OPENAI_MODEL 覆寫。
# 這裡避免再預設舊的 search-preview model 名稱，改以目前線上可用的 signals model
# （Responses API + tools=[{"type": "web_search"}]）為主。`DEFAULT_MODEL`
# 在 module 載入時 snapshot env，
# 所有 entry function 預設參數都吃這個值，所以 caller 不必每次 explicit 傳 model。
_FALLBACK_MODEL = "gpt-5.4-mini"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", _FALLBACK_MODEL).strip()
DEFAULT_MARKET_MODEL = os.getenv(
    "OPENAI_SIGNALS_MARKET_MODEL",
    "gpt-5.4-mini",
).strip()
DEFAULT_RESEARCH_MODEL = os.getenv(
    "OPENAI_SIGNALS_RESEARCH_MODEL",
    "gpt-5.4-mini",
).strip()
DEFAULT_DECISION_MODEL = os.getenv(
    "OPENAI_SIGNALS_DECISION_MODEL",
    "gpt-5.4",
).strip()
DEFAULT_WATCH_REASON_MODEL = os.getenv(
    "OPENAI_SIGNALS_REASON_MODEL",
    "gpt-5.4-mini",
).strip()

# 系統 prompt 路徑（spec §10 LLM I/O contract 全文）
_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "watch-list-stock.md"
)

# OpenAI 回應的 max tokens；reason 規則要求 500-1000 字 × batch 8 → 預留充足
_MAX_OUTPUT_TOKENS = 8000
_WEB_SEARCH_TOOL = {"type": "web_search"}
_OPENAI_TIMEOUT_SECONDS = 120.0
_OPENAI_MAX_RETRIES = 1
_PROMPT_CACHE_RETENTION = "in_memory"
_CACHE_KEY_MARKET = "m23:market:v2"
_CACHE_KEY_RESEARCH = "m23:research:v2"
_CACHE_KEY_DECISION = "m23:decision:v2"
_CACHE_KEY_WATCH_REASON = "m23:watch-reason:v2"
_DIAG_STATUS_OK = "ok"
_DIAG_STATUS_API_KEY_MISSING = "api_key_missing"
_DIAG_STATUS_OPENAI_EXCEPTION = "openai_exception"
_DIAG_STATUS_EMPTY_OUTPUT = "empty_output"
_DIAG_STATUS_INVALID_JSON = "invalid_json"


def assemble_market_context(
    db_market_snapshot: Dict[str, Any],
    *,
    model: str = DEFAULT_MARKET_MODEL,
) -> Dict[str, Any]:
    """Step 0：判斷 market_state（STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK）。

    LLM 上網查 VIX / 美股 / 台指期 / USD-TWD，搭配 DB 已知數字判斷。
    LLM 不可用 → 回 fallback dict（market_state="RANGE"，reason 標記不可用）。
    """
    taiex_change_pct = _get_index_change_pct(db_market_snapshot, "taiex")
    otc_change_pct = _get_index_change_pct(db_market_snapshot, "otc")
    system_prompt = _load_system_prompt()
    user_msg = (
        "[只執行 STEP 0：判斷今日市場狀態]\n"
        "你必須使用 web search 查詢 VIX、美股、台指期、USD/TWD，搭配下方 backend 已知數字，"
        "判斷 market_state。\n\n"
        "[硬規則]\n"
        "1. backend 提供的加權/櫃買數字是 authoritative，不可改寫、不可補 0、不可自行重算。\n"
        "2. 若 backend 某欄位為 null，必須明確說該欄位缺資料；不能寫成『DB 全空』。\n"
        "3. 你負責的是外部市場補充與整體 market_state 判讀，不是重寫 backend 數字。\n\n"
        f"[backend 已知市場數字]\n"
        f"{json.dumps(db_market_snapshot, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "market_state": "STRONG_BULL | STRUCTURAL_BULL | RANGE | WEAK",\n'
        '  "vix_status": "risk_on | neutral | risk_off",\n'
        '  "futures_bias": "LONG | SHORT | NEUTRAL",\n'
        '  "market_state_reason": "繁體中文說明"\n'
        "}\n"
    )

    payload, diagnostic = _call_llm_json(
        system_prompt,
        user_msg,
        model=model,
        stage="market",
        use_web_search=True,
        prompt_cache_key=_CACHE_KEY_MARKET,
    )
    if payload is None:
        return _market_context_fallback(db_market_snapshot, diagnostic=diagnostic)

    return {
        "market_state": payload.get("market_state", "RANGE"),
        "taiex_change_pct": taiex_change_pct,
        "otc_change_pct": otc_change_pct,
        "vix_status": payload.get("vix_status"),
        "futures_bias": payload.get("futures_bias"),
        "market_state_reason": payload.get("market_state_reason", ""),
        "llm_diagnostic": diagnostic,
    }


def run_research_batch(
    stocks_batch: List[Dict[str, Any]],
    market_context: Optional[Dict[str, Any]] = None,
    *,
    model: str = DEFAULT_RESEARCH_MODEL,
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

    payload, diagnostic = _call_llm_json(
        system_prompt,
        user_msg,
        model=model,
        stage="research",
        use_web_search=True,
        prompt_cache_key=_CACHE_KEY_RESEARCH,
    )
    if payload is None:
        return [_research_fallback(s, diagnostic=diagnostic) for s in stocks_batch]

    research = payload.get("research")
    if not isinstance(research, list):
        diagnostic = _with_status(
            diagnostic,
            status=_DIAG_STATUS_INVALID_JSON,
            message="LLM payload missing 'research' list.",
        )
        return [_research_fallback(s, diagnostic=diagnostic) for s in stocks_batch]

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
            aligned.append(
                {
                    **stock,
                    **by_id[sid],
                    "stock": sid,
                    "llm_diagnostic": diagnostic,
                }
            )
        else:
            aligned.append(_research_fallback(stock, diagnostic=diagnostic))
    return aligned


def run_explanation_batch(
    research_results: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str = DEFAULT_DECISION_MODEL,
) -> List[Dict[str, Any]]:
    """Step 8a：先對全候選產出短 decision。

    只產出 `signals` / `decision` / 1-2 句 short_reason，避免先替所有候選寫長文。
    長理由只留給最後的 WATCH 清單。
    """
    if not research_results:
        return []

    out: List[Dict[str, Any]] = []
    for i in range(0, len(research_results), DEFAULT_EXPLANATION_BATCH_SIZE):
        chunk = research_results[i : i + DEFAULT_EXPLANATION_BATCH_SIZE]
        out.extend(_run_decision_chunk(chunk, market_context, model=model))
    return out


def run_watch_reason_batch(
    watch_items: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str = DEFAULT_WATCH_REASON_MODEL,
) -> List[Dict[str, Any]]:
    """Step 8b：只對 WATCH 名單補長理由。

    這一層避免把 REMOVE 候選也花成本產生長文，直接降低整體 latency。
    """
    if not watch_items:
        return []

    out: List[Dict[str, Any]] = []
    for i in range(0, len(watch_items), DEFAULT_EXPLANATION_BATCH_SIZE):
        chunk = watch_items[i : i + DEFAULT_EXPLANATION_BATCH_SIZE]
        out.extend(_run_watch_reason_chunk(chunk, market_context, model=model))
    return out


def assemble_final_output(
    market_context: Dict[str, Any],
    explanation: List[Dict[str, Any]],
    *,
    candidate_pool_size: int,
    model: str = DEFAULT_WATCH_REASON_MODEL,
    total_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Step 9：拆 watchlist / removed、計算 summary、組裝最終 payload。

    對齊 spec §10.2 完整 schema：
      market_context / watchlist / removed / summary +
      candidate_pool_size / final_watchlist_size / llm_model / llm_total_tokens
    """
    watchlist_candidates: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for item in explanation:
        decision = str(item.get("decision") or "REMOVE").upper()
        if decision == "WATCH":
            watchlist_candidates.append(_format_watch_entry(item))
        else:
            removed.append(
                {
                    "stock": item.get("stock") or item.get("stock_id"),
                    "name": item.get("name", ""),
                    "remove_reason": item.get("short_reason")
                    or item.get("reason")
                    or item.get("remove_reason")
                    or "",
                }
            )

    watchlist = _cap_final_watchlist(watchlist_candidates, removed)

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
    stage: str,
    use_web_search: bool = False,
    prompt_cache_key: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """呼叫 OpenAI 並嘗試 parse JSON。

    這裡走 Responses API，只送基本 instruction / input / tools / max_output_tokens。
    解析失敗 / API key 缺失 / 例外 → 回 `(None, diagnostic)`（caller 負責 fallback）。
    """
    diagnostic = _base_diagnostic(
        stage=stage,
        model=model,
        use_web_search=use_web_search,
        prompt_cache_key=prompt_cache_key,
    )
    api_key = get_openai_api_key()
    if not api_key:
        logger.warning(
            "OPENAI_API_KEY not configured; M23 LLM call skipped (model=%s)",
            model,
        )
        diagnostic["status"] = _DIAG_STATUS_API_KEY_MISSING
        diagnostic["message"] = "OPENAI_API_KEY not configured."
        return None, diagnostic

    client = OpenAI(
        api_key=api_key,
        timeout=_OPENAI_TIMEOUT_SECONDS,
        max_retries=_OPENAI_MAX_RETRIES,
    )
    try:
        kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": user_msg,
            "max_output_tokens": _MAX_OUTPUT_TOKENS,
            "prompt_cache_retention": _PROMPT_CACHE_RETENTION,
        }
        if prompt_cache_key:
            kwargs["prompt_cache_key"] = prompt_cache_key
        if use_web_search:
            kwargs["tools"] = [_WEB_SEARCH_TOOL]
            kwargs["tool_choice"] = "auto"
        response = client.responses.create(**kwargs)
        raw = _extract_responses_output_text(response)
        if not raw.strip():
            diagnostic["status"] = _DIAG_STATUS_EMPTY_OUTPUT
            diagnostic["message"] = "OpenAI returned empty output_text."
            logger.warning(
                "M23 LLM call returned empty output (stage=%s model=%s)",
                stage,
                model,
            )
            return None, diagnostic
        payload = _extract_json(raw)
        if payload is None:
            diagnostic["status"] = _DIAG_STATUS_INVALID_JSON
            diagnostic["message"] = "OpenAI output could not be parsed as JSON."
            diagnostic["raw_preview"] = raw.strip()[:500]
            return None, diagnostic
        diagnostic["status"] = _DIAG_STATUS_OK
        return payload, diagnostic
    except Exception as exc:
        logger.exception("M23 LLM call failed (stage=%s model=%s)", stage, model)
        diagnostic["status"] = _DIAG_STATUS_OPENAI_EXCEPTION
        diagnostic["exception_type"] = exc.__class__.__name__
        diagnostic["message"] = str(exc)[:300]
        return None, diagnostic


def _extract_responses_output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str):
        return text

    output = getattr(response, "output", None) or []
    chunks: List[str] = []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "output_text":
                chunks.append(getattr(content, "text", "") or "")
    return "\n".join(chunks)


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


def _run_decision_chunk(
    chunk: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str,
) -> List[Dict[str, Any]]:
    """單個 chunk 的短 decision call。"""
    system_prompt = _load_system_prompt()
    user_msg = (
        "[執行 STEP 5 / STEP 6：先對全候選做短 decision]\n"
        "你現在只需要判斷 WATCH / REMOVE，並給 1-2 句短理由。"
        "最終 WATCH 名單應盡量控制在 30 檔內，條件普通或排序偏後者請直接判 REMOVE。"
        "不要產生長文分析，長理由只留給最後的 WATCH 名單。\n\n"
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
        '      "short_reason": "1-2 句繁體中文，120 字內，說明保留或排除主因"\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )

    payload, diagnostic = _call_llm_json(
        system_prompt,
        user_msg,
        model=model,
        stage="decision",
        prompt_cache_key=_CACHE_KEY_DECISION,
    )
    if payload is None:
        return [_decision_fallback(r, diagnostic=diagnostic) for r in chunk]

    items = payload.get("items")
    if not isinstance(items, list):
        diagnostic = _with_status(
            diagnostic,
            status=_DIAG_STATUS_INVALID_JSON,
            message="LLM payload missing 'items' list for decision stage.",
        )
        return [_decision_fallback(r, diagnostic=diagnostic) for r in chunk]

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
            merged["short_reason"] = ext.get("short_reason", "")
            merged["llm_diagnostic"] = diagnostic
        else:
            fb = _decision_fallback(research, diagnostic=diagnostic)
            merged.update(fb)
        aligned.append(merged)
    return aligned


def _run_watch_reason_chunk(
    chunk: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str,
) -> List[Dict[str, Any]]:
    """只對 WATCH 名單補長理由。"""
    system_prompt = _load_system_prompt()
    user_msg = (
        "[執行 STEP 7 / STEP 8 / STEP 9：只對 WATCH 名單補長理由]\n"
        "你現在只處理已經判定為 WATCH 的股票。"
        "請根據 research 與 market_state，為每檔輸出 250-350 字繁體中文分析。"
        "不要重做 WATCH / REMOVE 判斷，也不要處理 REMOVE 股票。\n\n"
        f"[market_context]\n"
        f"{json.dumps(market_context, ensure_ascii=False, indent=2)}\n\n"
        f"[watch_items]\n"
        f"{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "items": [\n'
        '    {\n'
        '      "stock": "股票代碼",\n'
        '      "reason": "250-350 字繁體中文分析"\n'
        '    }\n'
        '  ]\n'
        "}\n"
    )

    payload, diagnostic = _call_llm_json(
        system_prompt,
        user_msg,
        model=model,
        stage="watch_reason",
        prompt_cache_key=_CACHE_KEY_WATCH_REASON,
    )
    if payload is None:
        return [_watch_reason_fallback(item, diagnostic=diagnostic) for item in chunk]

    items = payload.get("items")
    if not isinstance(items, list):
        diagnostic = _with_status(
            diagnostic,
            status=_DIAG_STATUS_INVALID_JSON,
            message="LLM payload missing 'items' list for watch_reason stage.",
        )
        return [_watch_reason_fallback(item, diagnostic=diagnostic) for item in chunk]

    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = item.get("stock") or item.get("stock_id")
        if sid:
            by_id[str(sid)] = item

    aligned: List[Dict[str, Any]] = []
    for watch in chunk:
        sid = str(watch.get("stock") or watch.get("stock_id") or "")
        merged: Dict[str, Any] = {**watch}
        if sid in by_id:
            merged["reason"] = by_id[sid].get("reason", "")
            merged["llm_diagnostic"] = diagnostic
        else:
            merged.update(_watch_reason_fallback(watch, diagnostic=diagnostic))
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
        "reason": item.get("reason") or item.get("short_reason", ""),
    }


def _cap_final_watchlist(
    watchlist: List[Dict[str, Any]],
    removed: List[Dict[str, Any]],
    *,
    limit: int = MAX_FINAL_WATCHLIST_SIZE,
) -> List[Dict[str, Any]]:
    if limit <= 0 or len(watchlist) <= limit:
        return watchlist

    ranked = sorted(watchlist, key=_watch_rank_key)
    kept = ranked[:limit]
    overflow = ranked[limit:]

    for item in overflow:
        removed.append(
            {
                "stock": item.get("stock"),
                "name": item.get("name", ""),
                "remove_reason": f"超過最終推薦上限 {limit} 檔，依綜合排序未納入。",
            }
        )

    return kept


def _watch_rank_key(item: Dict[str, Any]) -> tuple:
    type_priority = {
        "LEADER": 0,
        "FOLLOWER": 1,
        "LAGGARD": 2,
        "LAGGARD_CANDIDATE": 2,
    }.get(str(item.get("type") or "").upper(), 3)
    signal_score = _signal_strength_score(item.get("signals"))
    theme_score = _theme_score(item.get("theme"))
    theme_fit_score = {
        "HIGH": 0,
        "MEDIUM": 1,
        "LOW": 2,
        "NONE": 3,
    }.get(str(item.get("theme_fit") or "").upper(), 4)
    stock = str(item.get("stock") or "")

    return (
        type_priority,
        -signal_score,
        -theme_score,
        theme_fit_score,
        stock,
    )


def _signal_strength_score(signals: Any) -> int:
    if not isinstance(signals, dict):
        return 0

    score = 0
    score += {
        "strong": 3,
        "moderate": 1,
        "weak": -2,
    }.get(str(signals.get("capital_flow") or "").lower(), 0)
    score += {
        "accumulating": 3,
        "neutral": 0,
        "short_squeeze_potential": 1,
        "weakening": -2,
        "retail_overheated": -3,
    }.get(str(signals.get("chip_trend") or "").lower(), 0)
    score += {
        "positive": 2,
        "neutral": 0,
        "negative": -2,
    }.get(str(signals.get("margin_short_signal") or "").lower(), 0)
    score += {
        "breakout": 3,
        "steady_uptrend": 2,
        "early_turn": 1,
        "range_bound": -1,
        "distribution": -2,
        "weak": -3,
    }.get(str(signals.get("technical_status") or "").lower(), 0)
    return score


def _theme_score(theme: Any) -> int:
    if not isinstance(theme, dict):
        return 0
    value = theme.get("theme_score")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _market_context_fallback(
    db_market_snapshot: Dict[str, Any],
    *,
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
    taiex_change_pct = _get_index_change_pct(db_market_snapshot, "taiex")
    otc_change_pct = _get_index_change_pct(db_market_snapshot, "otc")
    return {
        "market_state": "RANGE",
        "taiex_change_pct": taiex_change_pct,
        "otc_change_pct": otc_change_pct,
        "vix_status": "neutral",
        "futures_bias": "NEUTRAL",
        "market_state_reason": _market_context_fallback_reason(diagnostic),
        "llm_diagnostic": diagnostic,
    }


def _research_fallback(
    stock: Dict[str, Any],
    *,
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
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
        "_unavailable_reason": _stage_fallback_reason("research", diagnostic),
        "llm_diagnostic": diagnostic,
    }


def _decision_fallback(
    research: Dict[str, Any],
    *,
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        **research,
        "signals": _default_signals(),
        "decision": "REMOVE",
        "short_reason": _stage_fallback_reason("decision", diagnostic),
        "llm_diagnostic": diagnostic,
    }


def _watch_reason_fallback(
    item: Dict[str, Any],
    *,
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        **item,
        "reason": item.get("short_reason")
        or _stage_fallback_reason("watch_reason", diagnostic),
        "llm_diagnostic": diagnostic,
    }


def _base_diagnostic(
    *,
    stage: str,
    model: str,
    use_web_search: bool,
    prompt_cache_key: Optional[str],
) -> Dict[str, Any]:
    return {
        "stage": stage,
        "model": model,
        "status": "pending",
        "use_web_search": use_web_search,
        "prompt_cache_key": prompt_cache_key,
    }


def _with_status(
    diagnostic: Dict[str, Any],
    *,
    status: str,
    message: str,
) -> Dict[str, Any]:
    updated = dict(diagnostic)
    updated["status"] = status
    updated["message"] = message
    return updated


def _market_context_fallback_reason(diagnostic: Dict[str, Any]) -> str:
    base = _stage_fallback_reason("market", diagnostic)
    return f"{base} 預設保守判斷為 RANGE。"


def _stage_fallback_reason(stage: str, diagnostic: Dict[str, Any]) -> str:
    status = diagnostic.get("status")
    stage_label = {
        "market": "Step 0 市場外部資訊查詢",
        "research": "個股 research",
        "decision": "短 decision",
        "watch_reason": "WATCH 長理由",
    }.get(stage, "LLM")
    reason = {
        _DIAG_STATUS_API_KEY_MISSING: "未設定 OPENAI_API_KEY",
        _DIAG_STATUS_OPENAI_EXCEPTION: _exception_reason(diagnostic),
        _DIAG_STATUS_EMPTY_OUTPUT: "OpenAI 回傳空內容",
        _DIAG_STATUS_INVALID_JSON: "OpenAI 回傳格式不是合法 JSON",
    }.get(status, "LLM 回應不可用")
    return f"{stage_label}失敗（{reason}）。"


def _exception_reason(diagnostic: Dict[str, Any]) -> str:
    exc_type = diagnostic.get("exception_type")
    message = diagnostic.get("message")
    if exc_type and message:
        return f"{exc_type}: {message}"
    if exc_type:
        return exc_type
    return "OpenAI 例外"


def _default_signals() -> Dict[str, str]:
    return {
        "capital_flow": "moderate",
        "chip_trend": "neutral",
        "margin_short_signal": "neutral",
        "technical_status": "range_bound",
    }


def _get_index_change_pct(
    db_market_snapshot: Dict[str, Any],
    index_key: str,
) -> Optional[float]:
    snapshot = db_market_snapshot.get(index_key)
    if not isinstance(snapshot, dict):
        return None
    value = snapshot.get("change_pct_1d")
    if value is None:
        return None
    return float(value)
