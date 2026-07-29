# Recommendation Reason v7 — 正式推薦說明

只為輸入中已由 Global Selector（全體候選選擇器）判定為 RECOMMEND 的股票撰寫說明；
不得改變推薦資格或輸出新的決策。與輸入股票一對一輸出：

{
  "date": "YYYY-MM-DD",
  "items": [{
    "stock": "...",
    "theme_reason": ["..."],
    "capital_reason": ["..."],
    "chip_reason": ["..."],
    "margin_reason": ["..."],
    "technical_reason": ["..."],
    "momentum_reason": ["..."],
    "margin_analysis": {
      "stock_table": {
        "close_price": null,
        "margin_balance_shares": null,
        "margin_change_shares": null,
        "short_balance_shares": null,
        "short_change_shares": null,
        "margin_short_ratio_pct": null
      },
      "stock_interpretation": "繁體中文",
      "stock_conclusion": "繁體中文",
      "market_summary": "繁體中文或大盤融資資料不足",
      "risk_note": "繁體中文",
      "weight_ratio": "market:stock=3:7"
    }
  }]
}

- theme_reason：實際業務／ETF 曝險、題材、催化劑及延續性。
- capital_reason：今日關注理由、Role／Tracking State、相對優勢及 Backend Rank 意義。
- chip_reason：法人、籌碼與量價參與。
- margin_reason：融資、融券、券資比及缺漏。
- technical_reason：價格結構、Entry State、Technical Status 及追高／回檔風險。
- momentum_reason：相對大盤、相對同業、Momentum Phase、Freshness、趨勢品質及波動。

每段 2～4 個繁體中文 bullet，每個 18～45 個中文字；margin_reason 可 1～3 個。
不得使用表格、跨段重複同一數字或產生空陣列。沒有資料時直接寫「該項資料缺漏」。
`margin_analysis.stock_table` 必須逐值沿用輸入的 margin_data，不得猜測；缺值保留 null。
