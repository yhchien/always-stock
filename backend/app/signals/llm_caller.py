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
from app.signals import market_cache

logger = logging.getLogger(__name__)


# Spec §5 Step 7：「一次 prompt 處理 5~10 檔（batch）」；research 保持 8，
# explanation 降到 4 以降低單次 payload。
DEFAULT_RESEARCH_BATCH_SIZE = 8
DEFAULT_EXPLANATION_BATCH_SIZE = 4
MAX_FINAL_WATCHLIST_SIZE = 3

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

# 魚尾 prompt 版本標記：每次對 watch-list-stock.md 做有意義的方法論改版時 bump（v1 → v2 …）。
# 會蓋進每筆 watchlist item + signal_snapshots / signal_watch_hits / completed_archives，
# 讓 30 日追蹤可以區分「這檔是哪一版 prompt 抓出來的」做績效歸因。
PROMPT_VERSION = "v1"

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
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Step 0：判斷 market_state（STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK）。

    LLM 上網查 VIX / 美股 / 台指期 / USD-TWD，搭配 DB 已知數字判斷。
    LLM 不可用 → 回 fallback dict（market_state="RANGE"，reason 標記不可用）。

    A3：4 小時 in-process cache。同日多人按「重新產生」、cron 03:00 之後 web UI 觸發都命中。
    backend 的 taiex/otc 數字會 merge 進 cached payload（避免 cache 鎖死當日漲跌幅），
    LLM 上網查的 market_state / vix / 期貨 4h 內不重打。`use_cache=False` 給測試 / cron 強制 fresh。
    """
    taiex_change_pct = _get_index_change_pct(db_market_snapshot, "taiex")
    otc_change_pct = _get_index_change_pct(db_market_snapshot, "otc")

    if use_cache:
        cached = market_cache.get_cached()
        if cached is not None:
            # 覆蓋當日 backend authoritative 指數數字（cache 鎖了昨日的 taiex/otc 不合理）
            merged = dict(cached)
            merged["taiex_change_pct"] = taiex_change_pct
            merged["otc_change_pct"] = otc_change_pct
            return merged

    system_prompt = _load_system_prompt(stage="market")
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
        # fallback 不寫 cache，避免使用者連續 4 小時都看到 RANGE 不可用文案
        return _market_context_fallback(db_market_snapshot, diagnostic=diagnostic)

    result = {
        "market_state": payload.get("market_state", "RANGE"),
        "taiex_change_pct": taiex_change_pct,
        "otc_change_pct": otc_change_pct,
        "vix_status": payload.get("vix_status"),
        "futures_bias": payload.get("futures_bias"),
        "market_state_reason": payload.get("market_state_reason", ""),
        "llm_diagnostic": diagnostic,
    }
    if use_cache:
        # 只 cache 「VIX / 期貨 / market_state」 等外部宏觀變數；
        # 當日 taiex/otc 由 caller 端 backend 數字覆寫（見上方 cache hit branch）
        market_cache.set_cached({
            "market_state": result["market_state"],
            "vix_status": result["vix_status"],
            "futures_bias": result["futures_bias"],
            "market_state_reason": result["market_state_reason"],
            "llm_diagnostic": diagnostic,
        })
    return result


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
    system_prompt = _load_system_prompt(stage="research")
    evidence_view = _to_evidence_view(stocks_batch)
    user_msg = (
        "[只執行 STEP 2 / STEP 3 / STEP 4：research 部分]\n"
        "對下列每檔股票，請上網查詢主要業務、題材延續性、產業鏈位置、龍頭 / 集團，"
        "並輸出每檔 research result。**不要做最終 decision**（那是 STEP 5-9 的事）。\n\n"
        "[硬規則]\n"
        "1. `type` 已由後端決定（prelim_type 欄位），請直接照填，不可重判 LEADER/FOLLOWER/LAGGARD。\n"
        "2. 每檔 stock 都有 `evidence` 段落，是後端 deterministic 從 DB 算出的數字；\n"
        "   你的 research 結果應與 evidence 一致（例如 evidence 顯示外資 3 日連買、\n"
        "   就不可在 theme_reason 寫「外資未進駐」）。\n\n"
        f"[market_context]\n"
        f"{json.dumps(market_context, ensure_ascii=False, indent=2)}\n\n"
        f"[stocks_batch]\n"
        f"{json.dumps(evidence_view, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only，不要 markdown code fence）：\n"
        "{\n"
        '  "research": [\n'
        '    {\n'
        '      "stock": "股票代碼",\n'
        '      "name": "股票名稱",\n'
        '      "industry": "產業",\n'
        '      "sub_industry": "細產業",\n'
        '      "type": "LEADER | FOLLOWER | LAGGARD",  // 直接照填 prelim_type\n'
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
        deterministic_type = _normalize_prelim_type(stock.get("prelim_type"))
        if sid in by_id:
            aligned.append(
                {
                    **_serialize_dates(stock),
                    **by_id[sid],
                    "stock": sid,
                    # B4：type 鎖死 deterministic prelim_type，LLM 不可改判分類
                    # （prompt 也已要求 LLM 不要重判 type，但保險起見後端強制覆寫）
                    "type": deterministic_type,
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
    """Step 9：整理最終 watchlist、計算 summary、組裝最終 payload。

    對齊 spec §10.2 完整 schema：
      market_context / watchlist / summary +
      candidate_pool_size / final_watchlist_size / llm_model / llm_total_tokens
    """
    watchlist_candidates: List[Dict[str, Any]] = []

    for item in explanation:
        decision = str(item.get("decision") or "REMOVE").upper()
        if decision == "WATCH":
            watchlist_candidates.append(_format_watch_entry(item))

    # 2026-05-05：先取消程式端 top-N 裁切，改由 prompt / LLM 自己決定保留幾檔。
    # 若之後要恢復「最後再硬裁前 3 檔」的產品策略，可直接打開下一行：
    # watchlist = _cap_final_watchlist(watchlist_candidates)
    watchlist = watchlist_candidates

    # 每筆蓋上 prompt 版本，往下流到 signal_snapshots / signal_watch_hits。
    for entry in watchlist:
        entry["prompt_version"] = PROMPT_VERSION

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
        "summary": summary,
        "candidate_pool_size": candidate_pool_size,
        "final_watchlist_size": len(watchlist),
        "llm_model": model,
        "llm_total_tokens": total_tokens,
        "prompt_version": PROMPT_VERSION,
    }


# ---------- internal helpers ----------


# A4：stage → 該 stage 需要包含的 STEP 區段。其他 STEP 區段在 _build_stage_prompt 內被裁掉。
# preamble（總原則 + INPUT 描述）與「重要限制」永遠保留，避免裁掉後 LLM 失去 ground truth。
# market / research / decision / watch_reason 各自只看自己負責的 step，input token 可省 ~50-60%。
_STAGE_INCLUDED_STEPS: Dict[str, set[int]] = {
    "market": {0},
    "research": {1, 2, 3, 4},
    "decision": {5, 6, 7, 8, 9},
    "watch_reason": {7, 8, 9},
    "full": {0, 1, 2, 3, 4, 5, 6, 7, 8, 9},
}

_PROMPT_FRAGMENT_CACHE: Dict[str, str] = {}

# M2（2026-05-24）：WATCH 寫作規則 header name 兼容新舊
# - 舊：「WATCH 長理由寫作規則」（單一 250-350 字 reason）
# - 新：「WATCH 五段 bullet 寫作規則」（5 段 string[] 各 3-5 bullet）
_WATCH_REASON_HEADERS = {
    "WATCH 長理由寫作規則",
    "WATCH 五段 bullet 寫作規則",
    # 2026-05-25：margin_analysis 段
    "WATCH margin_analysis 寫作規則",
}

# M2：WATCH reason 拆分後的 5 段欄位名（與 prompt schema 對齊）
WATCH_REASON_SECTIONS = (
    "theme_reason",
    "capital_reason",
    "chip_reason",
    "margin_reason",
    "technical_reason",
)


def _load_system_prompt(stage: str = "full") -> str:
    """A4：依 stage 載入對應 STEP fragment。

    `stage="full"` 維持原行為（整份 prompt），給未明確指定 stage 的 caller 用。
    其餘 stage 只保留 preamble + 該 stage 需要的 STEP + WATCH 寫作規則（reason stage）
    + 重要限制段。fragment 結果 in-process cache，避免每次 LLM call 重新切割。
    """
    cache_key = stage
    if cache_key in _PROMPT_FRAGMENT_CACHE:
        return _PROMPT_FRAGMENT_CACHE[cache_key]
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"M23 system prompt not found at {_PROMPT_PATH}. "
            "請確認 backend/app/prompts/watch-list-stock.md 已部署。"
        )
    full = _PROMPT_PATH.read_text(encoding="utf-8")
    fragment = _build_stage_prompt(full, stage)
    _PROMPT_FRAGMENT_CACHE[cache_key] = fragment
    return fragment


def _build_stage_prompt(full: str, stage: str) -> str:
    """根據 `_STAGE_INCLUDED_STEPS[stage]` 裁切 watch-list-stock.md。

    切割規則：
    - preamble = 檔頭至第一個 `==` 包圍的 `STEP N：` 之前
    - 每個 STEP 區段 = 從該 STEP 標題列起至下一個 `=====` 區段標題之前
    - WATCH 長理由寫作規則 / 重要限制 兩段視為「共用尾巴」，永遠保留
    - 不認得 stage → 直接回 full（保守 fallback）
    """
    included = _STAGE_INCLUDED_STEPS.get(stage)
    if included is None or stage == "full":
        return full

    lines = full.split("\n")
    # 找所有「==========」邊界（用作 section 切點）
    section_starts = [i for i, ln in enumerate(lines) if ln.strip().startswith("====")]
    # 第一個 ==== 之前是 preamble，但實際 prompt 第一個 ==== 是「核心原則」
    # 結構：preamble 是「核心原則」section + 「[INPUT]」section + 「Input 建議」section + STEP 0+

    # 找每個 STEP / WATCH / 重要限制 section 的起點
    section_headers: list[tuple[int, str]] = []  # (line_index, header_label)
    for i in range(len(lines)):
        stripped = lines[i].strip()
        if not stripped.startswith("STEP "):
            # 兼容 watch_reason header 的新舊名稱
            # 2026-05-24 M2：reason 從單一字串 → 5 段 bullet array，header 換名
            if stripped in _WATCH_REASON_HEADERS or stripped == "重要限制":
                section_headers.append((i, stripped))
            continue
        # STEP N：xxx 樣式
        try:
            step_num = int(stripped.split("STEP", 1)[1].strip().split("：")[0].split(":")[0])
            section_headers.append((i, f"STEP {step_num}"))
        except (ValueError, IndexError):
            continue

    if not section_headers:
        return full

    # preamble = 0 .. first section_header 前面最近的 ===== 邊界
    first_header_line = section_headers[0][0]
    preamble_end = first_header_line
    # 往上找最近的 ==== 邊界，preamble 結束在那之前
    for boundary in reversed(section_starts):
        if boundary < first_header_line:
            preamble_end = boundary
            break
    preamble = "\n".join(lines[:preamble_end]).rstrip()

    # 對每個 section_header 找它的 section 邊界（從 header 上方 ==== 開始到下一個 section 的上方 ====）
    fragments: list[str] = [preamble]
    for idx, (header_line, label) in enumerate(section_headers):
        # 起點：header_line 上方最近的 ==== 邊界
        start = header_line
        for boundary in reversed(section_starts):
            if boundary < header_line:
                start = boundary
                break
        # 終點：下一個 section_header 上方的 ==== 邊界（或檔尾）
        if idx + 1 < len(section_headers):
            next_header_line = section_headers[idx + 1][0]
            end = next_header_line
            for boundary in section_starts:
                if boundary < next_header_line and boundary > header_line:
                    end = boundary
                    break
        else:
            end = len(lines)

        # 判斷該 section 是否要包含
        keep = False
        if label.startswith("STEP "):
            step_num = int(label.split()[1])
            if step_num in included:
                keep = True
        elif label in _WATCH_REASON_HEADERS:
            keep = stage in {"watch_reason", "full"}
        elif label == "重要限制":
            keep = True  # 共用尾巴

        if keep:
            fragments.append("\n".join(lines[start:end]).strip())

    return "\n\n".join(fragments) + "\n"


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
    system_prompt = _load_system_prompt(stage="decision")
    user_msg = (
        "[執行 STEP 5 / STEP 6：先對全候選做短 decision]\n"
        "你現在只需要判斷 WATCH / REMOVE，並給 1-2 句短理由。"
        "請對每一檔獨立判斷，不要因為名額限制、批次內相對排序或同批有更強股票，就把原本符合條件的股票判成 REMOVE。"
        "不要產生長文分析，長理由只留給最後的 WATCH 名單。\n\n"
        "[硬規則]\n"
        "1. `type` 由 research_results 帶入，**不可修改**（後端 deterministic 決定）。\n"
        "2. short_reason 必須引用 evidence 內的至少 1 個具體數字（漲幅 / 法人金額 / 連買日數 / 量能比），\n"
        "   只寫「籌碼好」「題材熱」等空話會被視為品質不足。\n\n"
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
    """只對 WATCH 名單補 5 段 bullet array 分析（M2,2026-05-24 改版）。

    輸出 schema 從單一 `reason` 字串 → 5 段 string[]
    對應前端 5 個 TradingPlanPanel 編號 panel：題材 / 資金 / 籌碼 / 融券 / 技術。
    """
    system_prompt = _load_system_prompt(stage="watch_reason")
    user_msg = (
        "[執行 STEP 7 / STEP 8 / STEP 9：只對 WATCH 名單補 5 段 bullet + margin_analysis]\n"
        "你現在只處理已經判定為 WATCH 的股票。"
        "請根據 research、evidence、market_state,為每檔輸出 5 段 bullet array 繁體中文分析,"
        "以及一段結構化的「融資融券分析」(margin_analysis)。"
        "不要重做 WATCH / REMOVE 判斷,也不要處理 REMOVE 股票。\n\n"
        "[硬規則]\n"
        "1. 必須完全照 prompt「WATCH 五段 bullet 寫作規則」section 的格式輸出 5 段 string[]。\n"
        "2. 每段 3~5 條 bullet（margin_reason 允許 2 條),每條 15~40 字繁體中文。\n"
        "3. 必須在 5 段內具體引用 evidence 的 2-3 個數字（例「外資 3 日累計買超 X 億」、\n"
        "   「成交量擴張為 60 日均量的 X.X 倍」、「該產業 5 日漲幅排名 X / N」),\n"
        "   避免「籌碼穩定」「題材延續」這類空話。\n"
        "4. `type` 已由後端鎖定不可修改;capital_reason 內可帶到「身為 LEADER 的角色」等敘述。\n"
        "5. 禁止把同樣資訊重複寫在不同段;禁止 capital_reason 寫籌碼、technical_reason 寫法人。\n\n"
        "[margin_analysis 規則 — 用使用者要求的格式]\n"
        "請回答這個問題:「告訴我 <stock_id> 在 <date> 那天這個股票的融資融券狀況」,"
        "並依下列 schema 輸出結構化結果:\n"
        "  - stock_table 必填:close_price / margin_balance_shares / margin_change_shares /\n"
        "    short_balance_shares / short_change_shares / margin_short_ratio_pct,\n"
        "    直接抄 evidence 對應數字(margin_balance_shares / margin_change_shares /\n"
        "    short_balance_shares / short_change_shares / margin_short_ratio_pct / close_price),\n"
        "    禁止自編或四捨五入(margin_short_ratio_pct 保留 2 位小數即可)。\n"
        "  - stock_interpretation:1~2 句繁體中文,40~80 字,描述融資融券當下動向\n"
        "    (例「融資大增代表散戶追價,融券回補空單壓力解除」)。\n"
        "  - stock_conclusion:1 句 15~30 字結論標籤(例「融資追價 + 空單回補推升」)。\n"
        "  - market_summary:1 句 25~50 字,引用 market_context.margin_climate 的\n"
        "    climate_label / climate_reason / today / trend_5d,說明大盤融資環境如何\n"
        "    影響本檔判讀。資料不可用時直接寫「大盤融資資料不足」。\n"
        "  - risk_note:1 句 20~50 字,點出後續觀察重點(例「若股價橫盤而融資續增,\n"
        "    視為散戶過熱訊號」)。\n"
        "  - weight_ratio:固定填 \"market:stock=3:7\",代表分析權重(大盤 30%、個股 70%)。\n"
        "整段融資融券分析應比照使用者範例的口吻:先擺數字表、再用 1~2 句白話解讀、\n"
        "最後給結論 + 風險提示。個股篇幅應顯著大於大盤(個股 7 成、大盤 3 成)。\n\n"
        f"[market_context]\n"
        f"{json.dumps(market_context, ensure_ascii=False, indent=2)}\n\n"
        f"[watch_items]\n"
        f"{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
        "輸出格式（JSON only,不要 markdown code fence)：\n"
        "{\n"
        '  "items": [\n'
        '    {\n'
        '      "stock": "股票代碼",\n'
        '      "theme_reason": ["bullet 1", "bullet 2", "..."],\n'
        '      "capital_reason": ["bullet 1", "..."],\n'
        '      "chip_reason": ["bullet 1", "..."],\n'
        '      "margin_reason": ["bullet 1", "..."],\n'
        '      "technical_reason": ["bullet 1", "..."],\n'
        '      "margin_analysis": {\n'
        '        "stock_table": {\n'
        '          "close_price": number,\n'
        '          "margin_balance_shares": number,\n'
        '          "margin_change_shares": number,\n'
        '          "short_balance_shares": number,\n'
        '          "short_change_shares": number,\n'
        '          "margin_short_ratio_pct": number\n'
        '        },\n'
        '        "stock_interpretation": "1~2 句白話解讀",\n'
        '        "stock_conclusion": "1 句結論標籤",\n'
        '        "market_summary": "1 句大盤融資環境",\n'
        '        "risk_note": "1 句後續觀察重點",\n'
        '        "weight_ratio": "market:stock=3:7"\n'
        '      }\n'
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
            sections = _coerce_reason_sections(by_id[sid])
            merged.update(sections)
            merged["reason"] = _join_reason_sections_to_markdown(sections)
            # 2026-05-25：margin_analysis 結構化欄位（前端表格 + 解讀）
            merged["margin_analysis"] = _coerce_margin_analysis(
                by_id[sid].get("margin_analysis"),
                evidence=watch.get("evidence") if isinstance(watch.get("evidence"), dict) else None,
            )
            merged["llm_diagnostic"] = diagnostic
        else:
            merged.update(_watch_reason_fallback(watch, diagnostic=diagnostic))
        aligned.append(merged)
    return aligned


def _coerce_margin_analysis(
    raw: Any,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """從 LLM 回應抽出 margin_analysis 物件；缺漏 / 型別不對 → 用 evidence 補表格部分。"""
    if not isinstance(raw, dict):
        raw = {}

    raw_table = raw.get("stock_table") if isinstance(raw.get("stock_table"), dict) else {}

    def _maybe_num(v: Any) -> Optional[float]:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        return None

    def _from(field_in_table: str, ev_key: str) -> Optional[float]:
        candidate = _maybe_num(raw_table.get(field_in_table))
        if candidate is not None:
            return candidate
        if evidence:
            return _maybe_num(evidence.get(ev_key))
        return None

    stock_table = {
        "close_price": _from("close_price", "close_price"),
        "margin_balance_shares": _from("margin_balance_shares", "margin_balance_shares"),
        "margin_change_shares": _from("margin_change_shares", "margin_change_shares"),
        "short_balance_shares": _from("short_balance_shares", "short_balance_shares"),
        "short_change_shares": _from("short_change_shares", "short_change_shares"),
        "margin_short_ratio_pct": _from("margin_short_ratio_pct", "margin_short_ratio_pct"),
    }

    def _maybe_str(v: Any, *, max_len: int = 120) -> str:
        if isinstance(v, str):
            s = v.strip()
            if s:
                return s[:max_len]
        return ""

    return {
        "stock_table": stock_table,
        "stock_interpretation": _maybe_str(raw.get("stock_interpretation"), max_len=160),
        "stock_conclusion": _maybe_str(raw.get("stock_conclusion"), max_len=60),
        "market_summary": _maybe_str(raw.get("market_summary"), max_len=120),
        "risk_note": _maybe_str(raw.get("risk_note"), max_len=120),
        "weight_ratio": _maybe_str(raw.get("weight_ratio"), max_len=32)
            or "market:stock=3:7",
    }


def _coerce_reason_sections(item: Dict[str, Any]) -> Dict[str, List[str]]:
    """從 LLM 回應抽出 5 段 bullet array,缺漏 / 型別不對的段填空 list。"""
    out: Dict[str, List[str]] = {}
    for key in WATCH_REASON_SECTIONS:
        value = item.get(key)
        if isinstance(value, list):
            cleaned = []
            for bullet in value:
                if not isinstance(bullet, str):
                    continue
                stripped = bullet.strip()
                if not stripped:
                    continue
                cleaned.append(stripped[:80])
            out[key] = cleaned
        elif isinstance(value, str) and value.strip():
            out[key] = [value.strip()[:80]]
        else:
            out[key] = []
    return out


def _join_reason_sections_to_markdown(sections: Dict[str, List[str]]) -> str:
    """把 5 段 bullet 組回 markdown 字串,給仍使用 `reason` 欄位的舊 consumer 用。"""
    labels = {
        "theme_reason": "題材",
        "capital_reason": "資金",
        "chip_reason": "籌碼",
        "margin_reason": "融券",
        "technical_reason": "技術",
    }
    parts: List[str] = []
    for key in WATCH_REASON_SECTIONS:
        bullets = sections.get(key) or []
        if not bullets:
            continue
        parts.append(f"【{labels[key]}】")
        parts.extend(f"• {b}" for b in bullets)
    return "\n".join(parts)


def _format_watch_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    """擷取 spec §10.2 watchlist[] 期望的欄位。

    M2（2026-05-24）：新增 5 段 bullet array 欄位（theme/capital/chip/margin/technical_reason）
    對應前端 5 panel grid；舊 `reason` 欄位仍保留供向後相容 / Telegram 等舊 consumer。
    """
    entry: Dict[str, Any] = {
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
    # 5 段 bullet array；不存在時填空 list（前端 truthy 檢查 length > 0 即可隱藏 panel）
    for key in WATCH_REASON_SECTIONS:
        value = item.get(key)
        entry[key] = value if isinstance(value, list) else []
    # 2026-05-25：margin_analysis 結構化欄位
    margin = item.get("margin_analysis")
    entry["margin_analysis"] = margin if isinstance(margin, dict) else None
    return entry


def _cap_final_watchlist(
    watchlist: List[Dict[str, Any]],
    *,
    limit: int = MAX_FINAL_WATCHLIST_SIZE,
) -> List[Dict[str, Any]]:
    if limit <= 0 or len(watchlist) <= limit:
        return watchlist

    ranked = sorted(watchlist, key=_watch_rank_key)
    return ranked[:limit]


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


def _to_evidence_view(stocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """B6：把 candidate_pool dict 投影成乾淨的「deterministic 證據卡」給 LLM。

    Why: 原本整包 dict dump 進 prompt，內部欄位（in_top_stocks_3d / industry_count / 各種
         flow_1d/3d/5d）一起塞進 LLM context，重點不明且佔 token。改成只挑核心數字 +
         好讀的 key 名稱，並要求 LLM 在 reason 引用，提升 reason 的具體性、減少空話。
    """
    out: List[Dict[str, Any]] = []
    for s in stocks:
        out.append({
            "stock": s.get("stock_id") or s.get("stock"),
            "name": s.get("name", ""),
            "industry": s.get("industry") or s.get("industry_name"),
            "sub_industry": s.get("sub_industry"),
            "prelim_type": _normalize_prelim_type(s.get("prelim_type")),
            "evidence": {
                "industry_rank_5d": s.get("industry_rank_5d"),
                "industry_rank_net_3d": s.get("industry_rank_net_3d"),
                "industry_count": s.get("industry_count"),
                "consecutive_inst_buy_days_3d": s.get("consecutive_buy_days_3d"),
                "volume_5d_to_60d_ratio": s.get("volume_5d_to_60d_ratio"),
                "price_change_1d_pct": s.get("price_change_1d"),
                "price_change_3d_pct": s.get("price_change_3d"),
                "price_change_5d_pct": s.get("price_change_5d"),
                "foreign_flow_3d_twd": s.get("foreign_flow_3d"),
                "trust_flow_3d_twd": s.get("trust_flow_3d"),
                "dealer_flow_3d_twd": s.get("dealer_flow_3d"),
                "total_institution_flow_3d_twd": s.get("total_institution_flow_3d"),
                "total_institution_flow_5d_twd": s.get("total_institution_flow_5d"),
                "margin_change_1d": s.get("margin_change_1d"),
                "margin_change_3d": s.get("margin_change_3d"),
                "short_change_1d": s.get("short_change_1d"),
                "short_change_3d": s.get("short_change_3d"),
                # 2026-05-25：margin_analysis 用的絕對值 / 券資比
                "margin_balance_shares": s.get("margin_balance_shares"),
                "margin_change_shares": s.get("margin_change_shares"),
                "short_balance_shares": s.get("short_balance_shares"),
                "short_change_shares": s.get("short_change_shares"),
                "margin_short_ratio_pct": s.get("margin_short_ratio_pct"),
                "close_price": s.get("close_1d"),
                "in_top_stocks_3d": bool(s.get("in_top_stocks_3d")),
                "in_top_industries_3d": bool(s.get("in_top_industries_3d")),
            },
            "tracking_status": _tracking_status_view(s),
            "soft_hints": s.get("soft_hints", []),
        })
    return out


def _tracking_status_view(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """把 candidate_pool flat 的 tracking 欄位投影成 nested dict 給 LLM。

    Why: prompt INPUT 已宣告 tracking_status 為 nested 結構，evidence 也用同樣 shape 才能
         與 prompt 描述對齊。failed_follow_through 不暴露給 LLM（因為這類股票已被 hard filter
         排除，LLM 看不到；保留欄位反而誤導）。
    """
    first_seen = candidate.get("first_seen_date")
    return {
        "is_tracked": bool(candidate.get("is_tracked", False)),
        "first_seen_date": first_seen.isoformat() if hasattr(first_seen, "isoformat") else first_seen,
        "days_since_first_seen": candidate.get("days_since_first_seen"),
        "hit_count": candidate.get("hit_count"),
        "max_positive_return_pct": candidate.get("max_positive_return_pct"),
        "max_negative_return_pct": candidate.get("max_negative_return_pct"),
    }


def _serialize_dates(value: Any) -> Any:
    """遞迴把 dict / list 內的 date / datetime 物件轉成 ISO string。

    Why: candidate_pool 的 `first_seen_date`（M23 Phase 1.1 注入）與其他模組未來可能注入的
         date 欄位，會被 `run_research_batch` 透過 `**stock` spread 進下游 stage payload；
         下游 `_run_decision_chunk` / `_run_watch_reason_chunk` 對 chunk 跑 `json.dumps`
         會 raise `TypeError: Object of type date is not JSON serializable`。
         在 aligned 組裝時統一過一次，未來再加任何 date 欄位都自動安全。
    """
    if hasattr(value, "isoformat"):  # date / datetime / time
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_dates(v) for v in value]
    return value


def _normalize_prelim_type(raw: Any) -> str:
    """把 candidate_pool 的 prelim_type 映射到 watchlist[].type 接受的 3 個值。

    `LAGGARD_CANDIDATE` → `LAGGARD`；未知 / 缺值 → 保守 `LEADER`（沿用舊 fallback 行為）。
    """
    value = str(raw or "").upper().strip()
    if value == "LAGGARD_CANDIDATE":
        return "LAGGARD"
    if value in {"LEADER", "FOLLOWER", "LAGGARD"}:
        return value
    return "LEADER"


def _research_fallback(
    stock: Dict[str, Any],
    *,
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
    sid = stock.get("stock_id") or stock.get("stock") or ""
    return {
        **_serialize_dates(stock),
        "stock": sid,
        "name": stock.get("name", ""),
        "industry": stock.get("industry"),
        "sub_industry": stock.get("sub_industry"),
        "type": _normalize_prelim_type(stock.get("prelim_type")),
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
    """M2（2026-05-24）：5 段 bullet 改版後，fallback 也輸出 5 段，每段塞 fallback 訊息為單一 bullet。

    這樣前端 5 個 TradingPlanPanel 仍會顯示，使用者一眼看出哪段是 fallback。
    """
    fallback_msg = item.get("short_reason") or _stage_fallback_reason(
        "watch_reason", diagnostic
    )
    out: Dict[str, Any] = {
        **item,
        "reason": fallback_msg,
        "llm_diagnostic": diagnostic,
    }
    for key in WATCH_REASON_SECTIONS:
        # 已存在的段（例如 LLM 部分成功）不覆寫；缺漏才填 fallback
        if not isinstance(item.get(key), list) or not item.get(key):
            out[key] = [fallback_msg]
        else:
            out[key] = item.get(key)
    # 2026-05-25：margin_analysis 至少回傳表格（從 evidence 補），其他欄位空字串
    if not isinstance(item.get("margin_analysis"), dict):
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else None
        out["margin_analysis"] = _coerce_margin_analysis(None, evidence=evidence)
    return out


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
