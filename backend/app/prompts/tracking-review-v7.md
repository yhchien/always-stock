# Tracking Review v7 — 外部推薦論點重新驗證

唯一任務是以 `review_date` 為資料截止日，重新驗證 initial thesis（初始推薦論點）與
目前外部事實。只可輸出 `THESIS_INTACT`、`THESIS_WEAKENING`、
`THESIS_INVALIDATED`、`RESEARCH_UNAVAILABLE`；不得輸出 CONTINUE、CAUTION 或
STOP_OBSERVING，生命週期決策仍由 Backend `p4_state_v1` 決定。

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
