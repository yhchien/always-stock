# Prompt v7 繁體中文術語表

本表供 Prompt、API 說明與 UI 文案共用。JSON key、enum 與程式 symbol 維持英文；
人類可讀敘述使用右欄繁體中文。英文專有名詞第一次出現時可用「英文（中文）」格式。

| English | 繁體中文 |
|---|---|
| Momentum | 動能 |
| Relative Strength | 相對強度 |
| Participation | 資金參與 |
| Catalyst | 催化劑 |
| Thesis | 推薦論點 |
| Veto | 否決 |
| Global Selector | 全體候選選擇器 |
| Candidate | 候選股票 |
| Recommendation | 正式推薦 |
| Not Selected | 今日未入選 |
| Compact Selection Card | 精簡選擇卡 |
| Rank Override | 排名超越說明 |
| Evidence | 證據 |
| Evidence Coherence | 證據一致性 |
| Research Confidence | 研究可信度 |
| Prompt Family | Prompt 家族 |
| Shared Policy | 共用政策 |
| Prompt Composition | Prompt 組裝方式 |
| Prompt Injection | 提示詞注入 |
| Allowlist | 允許傳入欄位清單 |
| Point-in-time | 時間點隔離 |
| Deterministic | 固定且可重現的程式規則 |
| Tracking Review | 追蹤檢查 |
| Observation | 觀察項目 |
| Observation Episode | 單次推薦觀察週期 |
| State Machine | 狀態機 |
| Continue | 繼續觀察 |
| Caution | 謹慎觀察 |
| Stop Observing | 停止觀察 |
| Research Unavailable | 研究暫時無法完成 |
| Missing | 資料缺漏 |
| Not Applicable | 不適用 |
| Fail Closed | 遇到未知設定時拒絕執行 |
| SHA256 | 組裝內容的雜湊指紋 |

## 固定語意

- `RECOMMEND` 是今日正式推薦，不代表 BUY。
- `NOT_SELECTED` 是候選仍有效但今日相對優勢不足，不是 REMOVE。
- `STOP_OBSERVING` 只停止魚尾追蹤，不是 SELL、停損或看空。
- `UNCONFIRMED` 是尚未確認，不等於 `MISMATCH`。
- `NOT_APPLICABLE` 是證據不適用，不是資料缺漏或負面證據。
