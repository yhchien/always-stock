# 魚尾選股系統：改版前 vs 改版後完整差異

**建立日期**：2026-07-16
**改版範圍**：v2.1（2026-07-15）+ v2.2 資料前置（2026-07-15）+ v2.2 × v5 prompt（2026-07-16）
**相關文件**：[fishtail_momentum_upgrade_spec.md](fishtail_momentum_upgrade_spec.md)（canonical spec + 實作進度）

---

## 0. 一句話總結

| | 改版前 | 改版後 |
|---|---|---|
| **系統定位** | 法人異常訊號系統（法人買超金額主導進池與分類） | 動能選股系統（價格動能 / 相對強度第一優先，法人與題材只是確認訊號） |
| **選股哲學** | 「法人在買的股票」 | 「本身正在變強、且法人 / 題材能確認的股票」 |
| **要解的核心問題** | — | 「法人有買但股票本身不強」的誤判（2026-06 震盪盤勝率從 67% 崩到 39% 的檢討結論） |

---

## 1. 候選池（誰有資格進池）

### 改版前：單通道（純法人）
1. 近 2 日法人買超金額前 30 大個股
2. 前 10 大非金融產業（2 日法人淨買超排序、當日賣超黑名單剔除）成分股
3. 熱錢前 6 大個股的同集團成員

### 改版後：四通道聯集
| 通道 | 條件 | 上限 |
|---|---|---|
| **A 法人資金**（保留原三來源） | 同改版前 | 同改版前 |
| **B 價格動能**（新增） | `rs_market_percentile_20d >= 85`，或產業內 RS `>= 80`，或收盤創 20 日新高帶量（量 >= 20 日均量 ×1.2），或 60 日報酬全市場前 15% 且 5 日為正 | 40 檔 |
| **C 動能加速**（新增） | RS 排名 5 日內前進 >= 200 名，且 `rs_market_percentile >= 70` | 20 檔 |
| **D 基本面動能**（新增） | 月營收 YoY > 15% 且連兩月加速，或 YoY 由負轉正，或產業內 YoY 前 20%（用「次月 10 日」可用日 gate，無資料穿越） | 20 檔 |

**意義**：法人還沒買、但價格已經走出來的股票（改版前完全進不了池），現在有 B/C 兩條路進池；營收動能剛翻轉的股票有 D 通道。

---

## 2. 特徵層（每檔候選帶什麼資料）

### 改版前
價格：`price_change_1d/3d/5d/10d`、MA5/MA10、量比（1d/5d/60d 組合）、10 日高低點
法人：外資/投信/自營 1d/3d/5d、連買日數
融資券：餘額 / 增減 / 券資比
追蹤：`hit_count`、first_seen、max 正負報酬、failed_follow_through

### 改版後（全部新增，deterministic、全市場 66 交易日 frame 計算）
| 類別 | 新欄位 |
|---|---|
| 報酬 | `return_5d / 20d / 60d` |
| 相對強度 | `rs_market_percentile_20d`（全市場 0~100）、`rs_industry_percentile_20d`（產業內）、`relative_strength_market/industry_20d`（對中位數超額）、`industry_rs_percentile_20d`（產業層級） |
| 排名動能 | `rs_rank_20d_current / previous_5d`、`rs_rank_improvement_5d`（正 = 排名前進） |
| 位置 | `distance_to_20d/60d_high`（收盤 rolling high，0 = 創新高）、`distance_to_ma20` |
| 趨勢品質 | `trend_efficiency_20d`（淨位移 / 路徑總長）、`atr_pct_14d`、`up_down_volume_ratio_20d` |
| 法人強度 | `institution_buy_to_turnover_2d` + 全市場 percentile |
| 市值 | `shares_issued / market_cap / institution_buy_to_market_cap_2d`（新表 `stock_shares_outstanding`；目前只出欄位不進分數） |
| 基本面 | `revenue_yoy / mom / yoy_acceleration / yoy 轉正 / 產業內 yoy percentile`（available_date gate） |
| 綜合 | **`momentum_score`（0~100）**、`momentum_grade`（A/B/C/D）、`momentum_phase`（emerging / accelerating / trending / extended / weakening） |
| 市場層 | **`breadth_score`（0~100 市場廣度）**：MA20/60 上方比例、漲跌家數比、20 日新高新低、強產業比 |
| 追蹤 | `consecutive_hit_count` / `independent_hit_count`（episode 制：未命中 >= 5 交易日才算新事件） |

### momentum_score 組成（deterministic，percentile-based）
```
價格動能 30 + 相對強度 25 + 法人資金 20 + 量價品質 15 + 基本面動能 10
− 風險扣分（爆量長上影 −10 / RS 排名 5 日崩 200 名 −10 / 3 日漲幅 >12% −5）
→ clamp 0~100
```

---

## 3. 分類規則（LEADER / FOLLOWER / LAGGARD）

### LEADER
| | 改版前（4 條件） | 改版後（6 條件，明顯變嚴） |
|---|---|---|
| 價格 | 產業內 5 日漲幅前 30% | 產業 20 日 RS 全市場前 30% **且** 個股產業內 RS 前 20% |
| 分數 | — | **momentum_score >= 70** |
| 法人 | 3 日連買 >= 2 日 + 產業內 net_3d 前 20% | 3 日連買 >= 2 日，**或**買超佔成交比全市場前 20% |
| 量能 | 量比 5d/60d >= 1.5 | 量比 >= 1.3（放寬，因為已有分數把關） |
| 位置 | — | **距 20 日高點不超過 3%**（動能未斷） |

### FOLLOWER
| | 改版前 | 改版後 |
|---|---|---|
| 條件 | 同產業有 LEADER + `0 < 5日漲幅 < LEADER×0.7` + 3 日法人正 | 同產業有 LEADER + **score 55~69** + 5 日漲幅低於 LEADER + **RS 排名 5 日改善 > 0** + 3 日法人正 + **無爆量長上影** |

### LAGGARD（改名 ROTATION_LAGGARD）
| | 改版前（guard + 湊 2 hits） | 改版後（7 條件全滿足） |
|---|---|---|
| 邏輯 | LEADER 漲 >= 5% 當 guard，之後 4 條件湊滿 2 條即可 | **全部同時成立**：產業強勢（產業 RS 前 30% 或熱門產業）+ 20 日報酬落後產業平均 >= 5pct + RS 排名 5 日改善 + 法人由賣轉買或量能轉強 + 站回 10 日線或創 20 日新高 + **score >= 50** |
| 意義 | 「落後就有補漲機會」 | 「落後**且已出現可量化的早期轉強證據**才算輪動補漲」；低檔躺平的股票進不來 |

---

## 4. Deterministic Gate（進 LLM 前的硬性過濾）

### Hard Exclusions
改版前 7 條（ETF/金融、5 日法人負、3 日漲 >15%、流動性、當日量死線、failed_follow_through、派發兩型態）**全部保留**，新增：

- **第 8 條：`rs_market_percentile_20d < 40` 且 RS 排名 5 日未改善 → 直接剔除**（動能死水，法人買再多也不進 LLM）

### Regime Gate（依大盤狀態收斂）
| Regime | 改版前（M27） | 改版後新增 |
|---|---|---|
| **BULL_TREND** | 不剔除，只標 conviction | 疊**市場廣度**拆兩態：**BROAD_BULL**（廣度健康）→ score < 50 剔除；**NARROW_BULL**（指數強但廣度 < 50，少數權值股撐盤）→ 只留（LEADER 且 score >= 65）或（score >= 70 且無出貨跡象） |
| **VOLATILE_RANGE** | 剔 distribution / 急拉突破 / 單次命中非 LEADER | 加剔 **score < 60**、**RS 排名 5 日掉 > 50 名** |
| **RISK_OFF** | 只留 LEADER + 命中 >= 3 + 5 日法人正 + 非 distribution | 加一條 **RS 全市場 percentile < 90 剔除**（退潮盤只碰前 10% 最強） |
| conviction | hit_count / LEADER 導向 | BULL 高信心加一條路：**score >= 75 且獨立 episode >= 2**（重複被抓到的強勢股） |

**4 態 regime（BROAD/NARROW_BULL）只存在 deterministic gate 與 snapshot 觀察欄位；對 LLM 的 `market_regime` 契約維持 3 態不變。**

---

## 5. LLM 層（prompt 與決策權）

### Prompt 版本
| | 改版前 | 改版後 |
|---|---|---|
| 路由 | 依 regime 選版：多頭 → v1（追強）、震盪/退潮 → v4（收斂） | **所有 regime 預設 v5**（動能版）；v1/v4 保留給人工對照實驗（`SIGNALS_FORCE_PROMPT_VERSION`） |
| 方法論 | v1：法人資金主導追強；v4：deterministic_signals 語意 + 收斂 | **v5：價格動能 / 相對強度為最高優先，題材與法人只是確認訊號**（核心原則 16~22） |

### 判斷順序（v5 鎖死，不可跳過）
```
1. Momentum Gate（STEP 7.8）：score<50 / RS<40 / phase=weakening… 原則 REMOVE，
   題材分數高、法人買超大「不能補救」
2. Regime Gate（STEP 8）：三態各自的 WATCH 硬條件（震盪 score>=65、退潮 score>=75+RS>=90…）
3. Risk Cap（STEP 7.5）：backend deterministic 的 risk_gate_action
4. Theme Validation：上網查業務 / 題材（只能否決，不能救回）
5. WATCH / REMOVE
```

### 決策權分配
| | 改版前 | 改版後 |
|---|---|---|
| 大盤狀態 | STEP 0 由 LLM 上網查後判 market_state（STRONG_BULL/…） | **完全 backend deterministic**；LLM STEP 0 只查外部風險背景（VIX / 美股 / 期貨 / 匯率），`market_state` 淪為 legacy 欄位 |
| 個股訊號 | 籌碼 / 技術由 LLM 從 raw 數字自行判讀（v4 有「若 backend 提供則採用」但 backend 沒提供） | **`momentum_signals`（20 欄）+ `deterministic_signals`（8 欄）全部 backend 算好**，LLM 必須原樣採用、缺值不可幻想 |
| WATCH/REMOVE | LLM 判斷（M27 後有 conviction 蓋回，但升降級自由度大） | LLM 仍判斷，但**只能降級 / 排除，不能升級**：`risk_gate_action=EXCLUDE`、`max_decision=REMOVE` 是不可違反的天花板 |

### deterministic_signals（v5 STEP 7.5 的後端化，8 欄全新）
| 欄位 | 規則摘要 |
|---|---|
| `chip_trend` | weakening（3 日大買 1 日大賣）> retail_overheated（融資暴增法人不買）> short_squeeze_potential（資減券增價不跌）> accumulating（連買 + 價漲 + 量增）> neutral |
| `technical_status` | distribution > breakout（收盤創 20 日高）> steady_uptrend（MA20 上 + 趨勢效率 >= 0.4）> early_turn（站回 10MA + RS 改善）> range_bound > weak |
| `entry_quality` | extended_chase（急拉：量 >5 日均 ×2 且日漲 >5%，或 phase=extended）> breakout_confirmed（突破帶量非急拉）> pullback_setup（趨勢在 + 距高 -10%~-3% + 量縮）> failed_rotation > neutral |
| `sector_rotation_status` | inflow / cooling / failed_rotation / neutral（產業 1d/3d 法人流向 + 產業 RS） |
| `institution_flow_momentum` | reversal（3 日正 1 日負）/ accelerating（今日 > 3 日均 ×1.5）/ decelerating / stable / neutral |
| `risk_gate_action` | EXCLUDE（出貨 + 法人反轉，或輪動失敗 + 動能轉弱）/ MAX_B（散戶過熱 / 急拉追高）/ DOWNGRADE_ONE_LEVEL（任一風險旗標）/ PASS |
| `max_decision` | EXCLUDE → REMOVE，否則 WATCH（LLM 不可違反） |
| `risk_flags` | institution_flow_reversal / failed_rotation / distribution / retail_overheated / extended_chase / rs_deterioration |

---

## 6. 資料層（本次改版順帶修的坑）

| 項目 | 改版前 | 改版後 |
|---|---|---|
| **monthly_revenue 覆蓋** | 2026-04~06 全空、02~03 只有 ~837 檔（ETL 單日抓法踩到 FinMind「整月營收掛次月 1 號單一 key + dataset-level 只回 start_date」兩層陷阱） | 逐「月 1 號」key 抓法（daily 2 quota）；01~06 已回補至每月 ~1,080 檔 |
| **營收時間對齊** | 無公告日概念（直接用會有資料穿越） | `available_date = 次月 10 日`（法規截止日）deterministic gate |
| **NaN 髒資料** | 新上市股回算 YoY 產生 NaN 存進 float 欄（7,567 筆） | 已清 NULL；ETL 不再寫 NaN；momentum 讀取端 + JSON 出口雙層防禦 |
| **市值** | 全 DB 無股本 / 市值欄位 | 新表 `stock_shares_outstanding`（FinMind TaiwanStockShareholding，daily ETL step 8） |
| **特徵持久化** | watch hit 只存 LLM 的 `signals` | 新 JSON 欄位 **`signal_watch_hits.signal_metrics`**：momentum_score/grade/phase、RS percentiles、breadth_score、episode counts、regime detail、revenue 動能——30 日追蹤可做 v1/v4 vs v5 績效歸因 |

---

## 7. 追蹤統計

| | 改版前 | 改版後 |
|---|---|---|
| 命中統計 | 單一 `hit_count`（次數，不分事件） | 加 **episode 制**：`consecutive_hit_count`（當前事件內連續命中）+ `independent_hit_count`（獨立事件數；未命中 >= 5 交易日才算新事件）——「連 3 天被抓到」與「三週內三波獨立訊號」不再混為一談 |
| 用途 | conviction 判定 | conviction（BULL 高信心要求獨立 episode >= 2）+ signal_metrics 歸因 |

---

## 8. 對使用者的可見影響

1. **清單會更少、更嚴**：Momentum Gate + regime score gate 是新的硬底線；退潮盤可能只剩 0~2 檔（這是刻意的紀律，不是壞掉）
2. **reason 會引用具體數字**：v5 強制 momentum_reason 引用 RS 百分位 / 排名變化 / 趨勢效率 / ATR，不再有「籌碼好、題材熱」空話
3. **新增 `momentum` 區塊**：每檔 WATCH 帶 score / grade / phase / RS 百分位（前端可渲染）
4. **「補漲」品質提升**：低檔躺平股不再因為「落後 LEADER」就上榜，必須已有轉強證據
5. **版本歸因**：7/16 起新 snapshot 的 `prompt_version = v5`；`signal_metrics` 非 NULL 即為新版產出

---

## 9. 首次實測（2026-07-15 重跑）

- **Regime**：RISK_OFF（加權當日雖漲 2%，收盤 45,632 仍低於 MA20 46,020）；breadth_score 55.7
- **結果**：候選 120 檔 → deterministic gate 剔除 119 檔 → **WATCH 1 檔：1434 福懋（LEADER）**
  - momentum_score 81.6（grade A）、phase trending、RS 全市場 93 百分位 / 產業內 87 百分位
  - episode：當前連續命中 3 次、獨立事件 2 段（6/24-25、7/2 之後再啟動）
  - conviction high / watch_intensity cautious（退潮盤一律保守）
- **對照**：7/14 舊版（v4）同樣只有 1 檔（台塑）——退潮盤下新舊收斂程度相當；**新舊差異要在多頭 / 震盪日才會顯現**（Momentum Gate 與 NARROW_BULL 才有分歧空間）

---

## 10. 尚未改（v2.3 待辦）

- 動能失效退出 / ATR 停損 / drawdown 規則（目前仍是 30 日研究型追蹤 + -30% / 高點回落 30% 提前結算）
- 新舊股票競爭（entry_score vs hold_score 換股邏輯）
- 交易成本 / 滑價模型
- `institution_buy_to_market_cap` 進分數（市值資料 2026-07 起累積中，滿 60 天再評估）
