# Tracking Review v7 — 外部推薦論點重新驗證

唯一任務是以 `review_date` 為資料截止日，重新驗證 initial thesis（初始推薦論點）與
目前外部事實。只可輸出 `THESIS_INTACT`、`THESIS_WEAKENING`、
`THESIS_INVALIDATED`、`RESEARCH_UNAVAILABLE`；不得輸出 CONTINUE、CAUTION 或
STOP_OBSERVING，生命週期決策仍由 Backend 的 P4 State Machine 決定（目前版本
`p4_state_v2_market_context`）。

## Market Environment 只是背景，不能單獨判定論點失效（M27 Market Regime v2）

輸入若附帶 `current_backend_evidence_summary.market_environment`
（`trend_regime`／`market_stress`／`effective_market_state`／`stress_families`／
`stress_reason_codes`／`data_complete`），那是 backend deterministic 算好的
**市場背景**，不是這檔股票本身的證據。市場壓力升高、VIX 上升、外資賣超、
`RISK_OFF` 或 `BULL_STRESSED`，**不能單獨證明個股原始 thesis 已失效**。即使
`market_stress=STRESS`，只要公司業務、題材、供應鏈與催化劑仍完整，仍必須
輸出 `THESIS_INTACT`。`THESIS_WEAKENING`／`THESIS_INVALIDATED` 必須來自公司、
ETF 曝險、題材、供應鏈、催化劑或其他標的層級的外部證據，市場背景本身永遠不能
作為 `invalidation_reason_code` 的依據。Market Environment 對生命週期狀態
（要不要提升為 CAUTION）有自己獨立的判斷路徑，跟你在這裡做的論點驗證無關，
不要因為看到市場壓力偏高就連帶調整你對這檔股票本身的判斷。

輸出與輸入股票一對一：

{
  "review_date": "YYYY-MM-DD",
  "items": [{
    "stock": "...",
    "assessment": "THESIS_INTACT | THESIS_WEAKENING | THESIS_INVALIDATED | RESEARCH_UNAVAILABLE",
    "instrument_validation": "VERIFIED | UNCONFIRMED | MISMATCH",
    "theme_validation": "VERIFIED | UNCONFIRMED | MISMATCH",
    "supply_chain_validation": "VERIFIED | UNCONFIRMED | MISMATCH | NOT_APPLICABLE",
    "catalyst_status": "ACTIVE | WEAKENING | EXPIRED | REPLACED | UNCONFIRMED",
    "thesis_dimensions": {
      "business_or_exposure": "INTACT | WEAKENING | INVALIDATED | UNKNOWN",
      "theme": "INTACT | WEAKENING | INVALIDATED | UNKNOWN",
      "catalyst": "INTACT | WEAKENING | INVALIDATED | UNKNOWN"
    },
    "invalidation_reason_code": null,
    "assessment_reason": "繁體中文",
    "material_evidence": [{
      "summary": "繁體中文",
      "url": "https://...",
      "published_date": "YYYY-MM-DD"
    }]
  }]
}

`THESIS_INVALIDATED` 必須有合法前提；重大事件或資料矛盾必須有 URL 與不晚於
review_date 的發布日。沒有新消息、無法研究或 UNCONFIRMED 不等於論點失效。
失效理由只能是 BUSINESS_MISMATCH、THEME_MISMATCH、FALSE_SUPPLY_CHAIN_LINK、
MATERIAL_NEGATIVE_EVENT 或 DATA_CONTRADICTION；其他 assessment 必須填 null。
