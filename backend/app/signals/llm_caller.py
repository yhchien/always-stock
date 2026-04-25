"""
M23 Step 7~8：LLM batch research + explanation。

骨架（slice 4）：簽章 + 設計說明，body raise NotImplementedError。
真實邏輯由 slice 6 填入（讀 `backend/app/prompts/watch-list-stock.md` prompt）。

對應 spec：
  - §3.2 LLM 上網查詢的資料 context
  - §5 Step 7（Research Layer）/ Step 8（Explanation Layer）
  - §10 Input / Output JSON Schema

設計：
  - 一次 prompt 處理 5~10 檔（DEFAULT_BATCH_SIZE），控制 cost / 時間
  - 第一版用支援 web search 的模型（`gpt-4o-search-preview` 或同等）
  - 沒 web search 的 fallback：用訓練資料 + DB 數字推論，reason 註記降 confidence
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Spec §5 Step 7：「一次 prompt 處理 5~10 檔（batch）」，第一版取中間值
DEFAULT_BATCH_SIZE = 8

# Spec §3.2：第一版模型；可由 OPENAI_SIGNALS_MODEL env override
DEFAULT_MODEL = "gpt-4o-search-preview"


def run_research_batch(
    stocks_batch: List[Dict[str, Any]],
    market_context: Optional[Dict[str, Any]] = None,
    *,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Step 7 LLM Research Layer：對 batch 內每檔股票上網查詢公司業務、產業鏈、題材延續性。

    輸入 `stocks_batch`：filter 後的候選股 dict list（每筆含 prelim_type / soft_hints
    / 籌碼數字 / 技術數字），長度應 ≤ DEFAULT_BATCH_SIZE。

    輸出：每筆股票的 research result dict，欄位對齊 spec §10.2：
      `business_summary` / `supply_chain_position` / `theme` / `leader_check` /
      `group_info`（若 deterministic 已給 group 則 LLM 校正）等。

    回傳順序與輸入對齊（同 index）。
    """
    raise NotImplementedError("Slice 6: LLM research batch not implemented yet")


def run_explanation_batch(
    research_results: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Step 8 LLM Explanation Layer：產生 500–1000 字 reason + WATCH/REMOVE decision。

    輸入：Research 階段產出的 dict list（已含業務 / 題材 / leader_check 等資訊）
    輸出：每筆 dict 加上 `signals` / `theme_fit` / `decision` / `reason` 欄位，
          對齊 spec §10.2 watchlist[i] 的完整 schema。

    market_context 的 `market_state` 影響 LLM 過濾偏好（spec §8 gating table）。
    """
    raise NotImplementedError("Slice 6: LLM explanation batch not implemented yet")


def assemble_market_context(
    db_market_snapshot: Dict[str, Any],
    *,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """Step 0：組合 DB 已知數字（昨日加權 / 櫃買收盤）+ LLM 上網查 VIX / 美股 / USD-TWD。

    輸出 spec §10.2 `market_context` 欄位（market_state / market_state_reason 等）。
    """
    raise NotImplementedError("Slice 6: market context assembly not implemented yet")


def assemble_final_output(
    market_context: Dict[str, Any],
    explanation: List[Dict[str, Any]],
    *,
    candidate_pool_size: int,
    model: str = DEFAULT_MODEL,
    total_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Step 9：組裝最終 payload，由 pipeline 寫入 SignalSnapshot。

    對齊 spec §10.2 完整輸出 schema：
      - `market_context` / `watchlist` / `removed` / `summary`
      - `candidate_pool_size` / `final_watchlist_size`
      - `llm_model` / `llm_total_tokens`

    `explanation` 為 `run_explanation_batch` 的整體輸出（含 WATCH / REMOVE 兩類）；
    本函式負責拆 watchlist / removed、計算 summary 計數、把 model / token 一併打包。
    """
    raise NotImplementedError("Slice 6: final output assembly not implemented yet")
