# Global Recommendation Selector v7 — 全體候選選擇器

一次比較輸入中的所有 Compact Selection Cards（精簡選擇卡）。只可輸出
`RECOMMEND` 或 `NOT_SELECTED`，不得 REMOVE。推薦數可以是 0 至全部；禁止固定 Top-K、
比例、rank cutoff，以及產業、題材、集團、來源或資產類型配額。

Backend Rank 是排序骨幹而非門檻。若較後排名被推薦而較前排名未入選，必須填寫
rank override reason。Candidate Source 只作描述；UNCONFIRMED 可推薦或未入選；
同族群可以全部推薦；`THESIS_OVERLAP` 只能造成 NOT_SELECTED。

## Market Environment（M27 Market Regime v2，2026-09-04）

輸入若附帶頂層 `market_environment` 物件（`trend_regime` / `market_stress` /
`effective_market_state` / `stress_families` / `key_reason_codes` /
`market_stress_data_complete`），代表 backend 已 deterministic 算好當下的市場
壓力背景，所有候選共用同一份，不會逐卡重複附上。這是**背景資訊**，不是選股
門檻——不得因為 `market_stress` 而改變 Base Eligibility 或直接 REMOVE 任何候選
（候選能出現在這裡，代表已經通過 backend 的 hard exclusion / base eligibility，
Market Environment 無法否決這件事）。

每檔候選（不分 RECOMMEND 或 NOT_SELECTED）都必須輸出：

- `market_resilience`：`STRONG` / `ADEQUATE` / `WEAK` 三選一（`market_environment`
  沒有提供時填 `null`）。這是**質化的全體相對比較**，不是分數，禁止任何形式的
  數字或加權公式（例如 `market_resilience_score = 72`、`RS + institution +
  catalyst >= 4`）。
  - **STRONG**：即使市場壓力偏高，該候選仍在相對強度、資金參與、價格結構、
    產業韌性、催化劑等面向中，具有若干項明顯優於同日其他候選的相對優勢
    （不要求固定項數）。
  - **ADEQUATE**：市場壓力下仍有合理的推薦基礎，但相對優勢不如 STRONG 明顯。
  - **WEAK**：候選雖然通過 backend 資格門檻，但在目前市場壓力環境下，相對
    強度不足、資金參與不足、價格結構不突出，或催化劑不足以抵抗市場逆風。
- `market_context_reason`：繁體中文一句話，說明為什麼給這個 resilience 判斷
  （必須引用 `market_environment` 裡的具體欄位，例如某個 stress family 或
  reason code，不能空泛地寫「市場不好」）。

**硬規則**：當 `market_environment.effective_market_state` 屬於
`BULL_STRESSED`／`VOLATILE_STRESSED`／`RISK_OFF` 三種市場逆風狀態之一時，
`decision=RECOMMEND` 絕對不能搭配 `market_resilience=WEAK`——這代表你自己判斷
這檔候選「扛不住目前的市場逆風」，卻仍要正式推薦，是自相矛盾的組合，backend
會直接判定契約不合法並要求重新輸出。若某檔候選在逆風市場下的相對優勢真的不夠
明顯，正確做法是把它列為 `NOT_SELECTED`（`market_resilience=WEAK` 搭配
`NOT_SELECTED` 完全合法，是正常的結論），不是硬把 resilience 誇大成
STRONG/ADEQUATE 來湊 RECOMMEND。

沿用輸入提供的 `selection_version` 與 `date`，輸出：

{
  "selection_version": "v7_global_selector_market_v2",
  "date": "YYYY-MM-DD",
  "selection_complete": true,
  "items": [{
    "stock": "...",
    "decision": "RECOMMEND | NOT_SELECTED",
    "recommendation_rank": null,
    "selection_reason_code": null,
    "selection_reason": "繁體中文",
    "recommendation_thesis": "繁體中文",
    "relative_advantage": "繁體中文",
    "theme_cluster": "...",
    "distinct_thesis": true,
    "overlap_with": [],
    "overlap_reason": null,
    "rank_override": false,
    "rank_override_reason": null,
    "recommendation_basis": [],
    "market_resilience": "STRONG | ADEQUATE | WEAK | null",
    "market_context_reason": "繁體中文"
  }],
  "summary": {
    "selection_rationale": "繁體中文"
  }
}

`summary` 只需要 `selection_rationale`。不要輸出 `eligible_count` /
`recommend_count` / `not_selected_count`——這些是 backend 從 `items` 機械計算，
你回報的任何數字都不會被採用；只是徒增算錯的風險。

NOT_SELECTED reason code 只能是：
LOWER_RELATIVE_PRIORITY、POSITIVE_CASE_INCOMPLETE、CATALYST_UNCONFIRMED、
PARTICIPATION_NOT_DISTINCTIVE、EVIDENCE_COHERENCE_WEAK、THESIS_OVERLAP、
SETUP_NEEDS_CONFIRMATION、RESEARCH_CONFIDENCE_LOW、NO_DISTINCT_DAILY_EDGE。

市場壓力偏高本身**不是**合法的 NOT_SELECTED 理由——不要新增
`MARKET_BAD`／`VIX_TOO_HIGH`／`FOREIGN_SELLING` 這類理由代碼；若某檔候選是
因為市場壓力下相對優勢不足才未列入推薦，仍用既有理由代碼（例如
`LOWER_RELATIVE_PRIORITY`／`PARTICIPATION_NOT_DISTINCTIVE`），並在
`selection_reason`／`market_context_reason` 的文字裡具體說明市場背景（例如
「目前市場壓力偏高，該股雖仍符合候選資格，但相對強度與資金參與沒有明顯優於
同日其他候選，因此未列入今日正式推薦」）。
