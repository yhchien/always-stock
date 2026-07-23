# Phase 2.5 future recommendations（2026-07-23）

本次任務（Momentum Freshness + Final Watch Quality Layer）明確禁止順手修改 Candidate
Channel / Hard Exclusion / Regime Gate / market_regime 判定等既有邏輯；過程中發現的
其他問題，依指示只記錄於此，不在本次任務內修改。

## 1. Freshness 欄位的簡化取捨（非遺漏，是刻意的工程判斷）

Spec 建議的 Freshness features 中，以下幾項未逐字實作，改用既有等效欄位替代：

- **`excess_return_vs_market_3d`**：`market_regime.py` 目前只算 `return_1d_pct`（大盤當日
  報酬），沒有大盤 3 日報酬。要新增需要對 `market_regime.py` 加一個新查詢（純新增欄位，
  同 `return_1d_pct` 的既有先例，不動 `classify_regime` 判斷邏輯本身），評估後決定用
  `rs_rank_change_5d`（既有欄位）作為中期相對強度的替代證據，避免新增額外 DB round-trip。
- **`excess_return_vs_sector_1d/3d`**：目前只有 `industry_return_20d`（20 日聚合）與
  `industry_flow_1d/3d`（資金流，非價格報酬），沒有「產業每日平均報酬」。改用既有
  `sector_rotation_status`（deterministic_signals 既有欄位：inflow/cooling/failed_rotation/
  neutral）作為產業層級 confirmation 的替代證據。
- **`rs_rank_change_1d/3d`**：目前只有 5 日排名變化（`rs_rank_improvement_5d`），1/3 日
  粒度需要重算每日全市場排名快照（現有 `momentum.py` 只在 frame 建立當下算「現在」與
  「5 日前」兩個快照，沒有存 1/3 日前的排名），成本較高。

**建議下一輪**：若 replay 觀察後發現 5 日粒度不夠敏感（例如：某些個股在轉弱前 1-2 天就
該被抓到但現在慢了 3-4 天），才值得投資新增每日排名快照儲存機制。

## 2. `exclusions.is_etf()` regex 不認得槓桿反向 ETF 字母後綴

**沿用自 LLM v6 Contract Alignment 任務發現的既有問題**（未在該任務修復，本次 replay 再次
確認）：`app/signals/exclusions.py::is_etf()` 的 regex 只認純數字 ETF 代碼（`^00\d{2,}$`
一類），無法辨識「00665L」（富邦恒生國企正2）這類帶字母後綴的槓桿/反向 ETF，導致
`asset_type` 被誤判為 `COMMON_STOCK`。

Phase 1 canonical classification（`app/classification/etf_mapping.py`）已經修好同類問題
（regex 改成 `^00\d{2,6}[A-Za-z]?$`），但 `app/signals/exclusions.py` 尚未反向對齊。

**建議下一輪**：把 `app/signals/exclusions.py::is_etf()` 的 regex 對齊
`app/classification/etf_mapping.py` 的版本，讓 `asset_type=ETF` 判斷更準確（目前不影響
資格判斷本身，因為 asset_type 只用於 research 模式選擇，但會讓 ETF 走錯研究流程，
LLM 可能誤要求它提供月營收等不適用欄位）。

## 3. Watch Quality 門檻為工程起始值——60 天真實 replay 顯示目前設定幾乎沒有區分力

`momentum_freshness.py` / `watch_quality.py` 內所有數字門檻（`_RELATIVE_RETURN_STRONG_PCT`
= 1.0、`_READY_MIN_EVIDENCE` = 4、`_SETUP_MIN_EVIDENCE` = 2 等）都是工程起始值。
**2026-04-13 ~ 2026-07-07 共 60 個交易日的 deterministic replay（`run_phase25_replay_analysis.py
--days 60`，N=617 檔去重候選，10 交易日遠期報酬 evaluation-only）顯示這組門檻目前幾乎
沒有區分力**，是本次任務最重要的誠實發現，記錄如下：

- 全體去重候選（regime gate 存活者）：正報酬率 58.2%、平均報酬 +4.81%、跌超 10% 比例 10.7%
- 目前門檻下 `RESERVE` 只攔下 **11/617（1.8%）**，且這一小群 RESERVE 候選事後平均報酬
  反而是 **+15.68%**（優於全體平均）——即使樣本極小（N=11，不具統計意義），也代表目前
  門檻完全沒有抓到「會轉弱」的訊號，甚至方向可能是反的
- 對「跌超 10%」的 66 檔真正大虧股，目前門檻只抓到 **0 檔**（0%）
- 離線試算更激進的門檻（`ready_min=6, setup_min=5`）：RESERVE 擴大到 21.9%、可攔到
  18.2% 的大虧股，但代價是：(a) 連 8039（本次 regression 案例之一，用來驗證「不該被
  誤殺」的真實大漲股）都會被推到 RESERVE，(b) RESERVE cohort 事後平均報酬仍是
  **正的 +5.71%**——代表就算調到很嚴格，還是會擋掉很多「其實會賺錢」的候選
- **根因假設**：候選池本身已經是「動能+法人資金篩選過」的子集（見 `candidate_pool.py`
  的 A/B/C/D 四通道 + Phase 2 base momentum eligibility + regime gate），本次設計的
  7 個 evidence family（MOMENTUM_STRENGTH/FRESHNESS/RELATIVE_STRENGTH/PARTICIPATION/
  SECTOR_CONFIRMATION/INSTITUTION_CONFIRMATION/PRICE_STRUCTURE）大多與「已經進入候選池」
  這件事高度相關，區分力天生有限；且本次 60 天視窗以 BULL_TREND（32 天）/VOLATILE_RANGE
  （22 天）為主、RISK_OFF 僅 6 天，整體是偏多頭環境（見全體正報酬率 58.2%），這種環境下
  「大多數合格候選本來就會漲」，讓任何額外品質過濾的邊際價值天生受限

**結論與建議**：
1. **維持程式碼內建的工程起始門檻不變**（`_READY_MIN_EVIDENCE=4` / `_SETUP_MIN_EVIDENCE=2`），
   不為了讓數字好看而硬調——目前資料顯示不管往哪個方向調，都沒有找到能同時「顯著降低
   左尾」又「不誤殺真贏家」的門檻組合，任何單點選擇都是在一條很平的 trade-off 曲線上
   武斷選點，不具統計意義
2. **`WATCH_QUALITY_MODE` 應維持 `shadow`（現行預設），不建議近期切換至 `production`**——
   在目前的 evidence 設計下，切到 production 對篩選品質幾乎沒有幫助（只會擋掉 1.8% 候選、
   且那 1.8% 事後表現還比較好），沒有理由承擔「可能誤殺真贏家」的風險換來幾乎零的
   品質提升
3. **下一輪如果要讓這層真正發揮作用，應該重新設計 evidence family 本身**，而不是調整
   既有門檻數字——例如：需要真正獨立於候選池篩選邏輯之外的訊號（考慮跨股票的相對排名
   變化速度、非線性的法人買賣行為模式、或接入 M25 trade quality 已有的 peer_rank 機制），
   或是把評估窗拉長到涵蓋更多 RISK_OFF 天數的歷史區間重新驗證（本次 60 天只有 6 天
   RISK_OFF，樣本太少無法驗證「品質層在退潮盤是否真的有用」這個 spec 原始關切的情境）
4. 完整 replay 原始資料（617 筆去重紀錄 + 60 天逐日 explain trace）保存於
   `/tmp/phase25_replay_60d.json`（本機暫存檔，非版控，供後續分析參考）

## 4. Quality veto reason 的 LLM 遵循度需要 production 觀察

v6 prompt 新增的 5 種 quality veto reason（`INSUFFICIENT_CONFIRMATION` /
`MOMENTUM_NOT_FRESH` / `WEAK_PARTICIPATION` / `CATALYST_TOO_WEAK` / `EVIDENCE_NOT_COHERENT`）
目前只有程式碼層面驗證欄位正確傳遞（`_to_evidence_view` / `_run_decision_chunk` 測試），
沒有真實 LLM 呼叫驗證 LLM 是否真的只在證據支持時才使用這些理由、或是否會濫用
`INSUFFICIENT_CONFIRMATION` 當成變相的「動能不夠強」藉口。**建議下一輪**：等
`WATCH_QUALITY_MODE=production` 正式上線後，觀察 removed 名單中這 5 種理由的實際使用頻率
與具體理由文字，確認沒有被濫用。
