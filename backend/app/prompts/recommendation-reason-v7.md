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
- capital_reason：今日關注理由、目前處於的動能階段（例如上升趨勢中／回檔整理中／剛啟動），
  以及在今日候選中的相對排名意義（例如排名靠前／中段／後段）。
- chip_reason：法人、籌碼與量價參與。
- margin_reason：融資、融券、券資比及缺漏。
- technical_reason：價格結構、目前的進場位置（例如剛突破／已偏高追價風險／回檔整理中）、
  價格所處的技術狀態（例如突破整理／延續上升／轉弱）以及追高／回檔風險。
- momentum_reason：相對大盤、相對同業、目前的動能所處階段（剛啟動／加速中／過熱／降溫）、
  動能是否新鮮、趨勢品質及波動。

**全文語言規則（所有段落一律適用）**：一律用繁體中文描述語意，不得在句子中出現任何英文
欄位名稱、程式變數名、enum 代碼或底線命名（例如 ACTIVE_TREND、backend_priority_rank、
sector_rotation_status、institution_flow_momentum 這類寫法一律禁止）；輸入資料中若有這類
內部欄位/狀態值，必須先在腦中把它翻譯成對應的中文語意再寫進句子，不可原樣抄錄或音譯。

每段 2～4 個繁體中文 bullet，每個 18～45 個中文字；margin_reason 可 1～3 個。
不得使用表格、跨段重複同一數字或產生空陣列。沒有資料時直接寫「該項資料缺漏」。
`margin_analysis.stock_table` 必須逐值沿用輸入的 margin_data，不得猜測；缺值保留 null。
