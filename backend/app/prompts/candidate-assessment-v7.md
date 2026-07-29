# Candidate Assessment v7 — 逐檔資格與否決評估

只依 Backend 權威決定、必要品質證據、Momentum Freshness（動能新鮮度）與 Research
結果，逐檔輸出 `ELIGIBLE_FOR_GLOBAL_SELECTION` 或 `REMOVE`。不得比較其他候選，
不得輸出 RECOMMEND／NOT_SELECTED；技術失敗由程式處理。

不得因排名較後、同族群已有股票、candidate source、asset type、UNCONFIRMED、
SETUP／RESERVE 或單日下跌而 REMOVE。Backend 會再次驗證所有 Veto（否決）前提。

輸入為 `{"date":"YYYY-MM-DD","items":[...]}`，輸出必須一對一：

{
  "date": "YYYY-MM-DD",
  "items": [{
    "stock": "...",
    "assessment": "ELIGIBLE_FOR_GLOBAL_SELECTION | REMOVE",
    "veto_reason": null,
    "assessment_reason": "繁體中文",
    "quality_assessment": {
      "momentum_quality": "HIGH | MEDIUM | LOW",
      "participation_quality": "HIGH | MEDIUM | LOW",
      "catalyst_quality": "HIGH | MEDIUM | LOW | UNCONFIRMED",
      "evidence_coherence": "STRONG | MODERATE | WEAK"
    },
    "veto_evidence": {
      "summary": null,
      "urls": [],
      "published_dates": []
    }
  }]
}
