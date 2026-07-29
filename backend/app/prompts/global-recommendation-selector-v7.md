# Global Recommendation Selector v7 — 全體候選選擇器

一次比較輸入中的所有 Compact Selection Cards（精簡選擇卡）。只可輸出
`RECOMMEND` 或 `NOT_SELECTED`，不得 REMOVE。推薦數可以是 0 至全部；禁止固定 Top-K、
比例、rank cutoff，以及產業、題材、集團、來源或資產類型配額。

Backend Rank 是排序骨幹而非門檻。若較後排名被推薦而較前排名未入選，必須填寫
rank override reason。Candidate Source 只作描述；UNCONFIRMED 可推薦或未入選；
同族群可以全部推薦；`THESIS_OVERLAP` 只能造成 NOT_SELECTED。

沿用輸入提供的 `selection_version` 與 `date`，輸出：

{
  "selection_version": "v7_global_selector",
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
    "recommendation_basis": []
  }],
  "summary": {
    "eligible_count": 0,
    "recommend_count": 0,
    "not_selected_count": 0,
    "selection_rationale": "繁體中文"
  }
}

NOT_SELECTED reason code 只能是：
LOWER_RELATIVE_PRIORITY、POSITIVE_CASE_INCOMPLETE、CATALYST_UNCONFIRMED、
PARTICIPATION_NOT_DISTINCTIVE、EVIDENCE_COHERENCE_WEAK、THESIS_OVERLAP、
SETUP_NEEDS_CONFIRMATION、RESEARCH_CONFIDENCE_LOW、NO_DISTINCT_DAILY_EDGE。
