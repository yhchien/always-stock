# Phase 2 建議事項（Phase 1 完成後記錄，2026-07-21）

Phase 1 執行過程中發現的、但依規則**不得**在 Phase 1 順手修的問題，集中記錄於此，
供 Phase 2（正式切換選股邏輯使用 canonical taxonomy）時參考。

## 1. 選股層要不要吃 primary_sector（本次的核心懸念）

現行魚尾 `industry_rs_percentile_20d`（產業 20 日相對強度）是用 `stocks_master.industry_name`
分組計算。這次調查的三個案例（漢翔/台虹/台化）全部顯示同一個結構性問題：**FinMind 的
industry_name 對這些股票分組不準**（漢翔在「其他」、台虹的 PCB 產業內部沒有再細分、台化在
「紡織」而非石化），導致他們在「產業排名」這一關被系統性冤枉。

Phase 2 若要解決，建議選項（互斥，需使用者決定）：
- **選項 A**：`industry_rs_percentile_20d` 改用 `primary_sector`（49 類）分組，取代
  `industry_name`（87 類含大量重複/雜訊）。好處：漢翔會被分進「航太國防」而非「其他」，
  但這個分類目前只有 1 檔（漢翔本身），percentile 計算會因樣本數過小而失效（見 spec §12
  的 `sub_sector_rs_eligible >= 5` guard）——**需要先確認 primary_sector 各類樣本數是否足夠**
  （`validation_stats.json` 的 `primary_sector_distribution` 顯示多數類別 >= 20 檔，
  但 AEROSPACE_DEFENSE=1、AVIATION=1、SHIPBUILDING=1、HEALTH_SUPPLEMENT=1 這幾類樣本
  太少，percentile 沒有意義）
- **選項 B**：`industry_name` 不動，但在 candidate_pool 階段額外用 `primary_sector` 做
  一次「產業內排名」的第二意見，兩者取較高分位數（緩解單一分類系統的誤判，不需要
  large-scale migration）
- **選項 C**：維持現狀不動，只當作使用者手動研究時的參考資訊（本次 Phase 1 的定位）

## 2. Sub-sector 樣本數過小的類別（沿用 sub_industry 的既有限制）

`stock_sector_mapping.csv` 顯示不少 `sub_sector` 樣本 < 5 檔（尤其是逐檔 override 出來的
細分類，例如「廠務工程」「環保工程」等）。若 Phase 2 要用 sub_sector 做 peer-group RS，
需要重新統計 `sub_sector_stock_count` 並套用 spec §12 的 `sub_sector_rs_eligible` guard，
本次 Phase 1 **未建立**這個欄位（設計上沿用 `stocks_master.sub_industry` 的既有樣本限制，
未額外做樣本數驗證）。

## 3. DIVERSIFIED_OTHER 殘留（59 檔）需要真正的人工查證

Phase 1 用既有市場知識 + 誠實標記完成第一輪分類，但 59 檔 `DIVERSIFIED_OTHER` +
84 檔 `review_required` 大多是：
- TDR（存託憑證）中較冷門的中國/海外公司（約 26 檔）——需要查 MOPS 公告或公司官網
- `-KY` 註冊地小型公司（約 20 檔）——業務內容不夠知名，需要查年報
- 幾檔國內公司單純是我沒有把握的（約 13 檔）

建議：Phase 2 開始前，花一次性的時間逐檔查證這份 `sector_mapping_manual_review.csv`
清單（84 檔，不算多），把 confidence 從 LOW 補到 HIGH/MEDIUM。

## 4. ETF confidence 全部卡在 MEDIUM（272/292）

規則引擎（關鍵字比對）目前沒有辦法讓 ETF confidence 自動升到 HIGH——只有落在
`ETF_OVERRIDES`（20 檔旗艦 ETF）手動白名單的才是 HIGH。若要提升覆蓋率，Phase 2
可以考慮：
- 擴充 `ETF_OVERRIDES` 白名單（規模不大，292 檔全部人工過一次也可行）
- 或接 FinMind / 公開說明書資料源取得官方 `tracking_index`，取代規則引擎猜測

## 5. 智慧電網 / 電子工業 / 再生醫療 等「歷史批次重複命名」的根因未解

Phase 1 發現 FinMind `industry_name` 存在大量「同概念不同命名」的重複值（`半導體` vs
`半導體業`、`電腦及週邊設備` vs `電腦及週邊設備業`……），這次全部在 canonical 層
consolidate 掉了，但 **`stocks_master.industry_name` 本身沒有修正**（Phase 1 規則禁止
覆寫 source_industry）。若未來 ETL 重新抓 FinMind `TaiwanStockIndustryChain` 時這個
命名不一致問題可能會再次出現在新股身上，需要留意 `industry_mapping.py` 是否要跟著補新值。

## 6. 前端顯示範圍是刻意縮小的（Phase 1B 範圍控制）

本次只掛載了兩個 UI surface（K 線圖 popup header、魚尾卡片），**沒有**做：
- `/watchlist` 卡片
- `StockList.tsx`（L1 個股卡片）
- `signals/archive` 30 日追蹤頁
- 獨立的「Comparison Debug View」管理頁（spec §28，比較 source_industry vs canonical）

原因：使用者要求「控制範圍」，且這兩個 surface（K 線 popup + 魚尾卡片）已足以驗證
2634/1326/8039/2603/2646 五個 regression case 在 UI 上正確顯示。若後續需要更多入口，
可直接複用 `<CanonicalSectorTag canonical={...} />`（純 display 元件，無副作用）。

## 7. Comparison Debug View 未實作

Spec §28 建議的管理頁（`source_industry` vs `canonical primary/sub` 並排比較 +
`confidence=LOW` / `review_required=true` filter）本次未做——目前這個功能可以直接用
`sector_mapping_manual_review.csv` + `catch_all_remap_report.csv` 兩份 CSV 達到同等效果
（人工在試算表裡篩選）。若團隊人數增加、需要多人協作複核，屆時再評估是否值得做成網頁。
