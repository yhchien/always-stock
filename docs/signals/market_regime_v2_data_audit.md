# Market Regime v2 — Market Stress Overlay 資料 Audit（2026-09-04）

對照使用者提供的「Market Regime v2 — Market Stress Integration」規格書，逐項查證每個
指標在 FinMind／既有 DB 是否可取得，實測結果如下。

## 查證方式

直接對 FinMind v4 REST API（`https://api.finmindtrade.com/api/v4/data`）用真實 token
探測各候選 dataset，觀察真實回應（欄位形狀、資料筆數、日期涵蓋範圍），不假設文件正確。

## Family A：LOCAL_MARKET_INTERNALS

| metric | 來源 | DB 可用性 | 備註 |
|---|---|---|---|
| pct_above_ma20 / pct_above_ma60 | `app/signals/market_breadth.py`（既有） | ✅ | 從 momentum frame 算，不需要新資料 |
| advance_decline_ratio | 同上 | ✅ | |
| new_high_20d_count / new_low_20d_count | 同上 | ✅ | |
| equal_weight_return_1d | 新增（`market_stress.compute_cap_weight_divergence`） | ✅ | 用 momentum frame 全市場個股 `_ret_1d` 中位數近似，樣本 <100 檔回 None |
| cap_weight_divergence | 新增 | ✅ | `= taiex_return_1d - equal_weight_return_1d` |

不需要新 ETL；`LOCAL_MARKET_INTERNALS` 完全重用既有基礎建設。

## Family B：TAIWAN_FLOW_AND_DERIVATIVES

| metric | 來源 | DB 可用性 | 備註 |
|---|---|---|---|
| foreign_net_flow_1d/3d/5d | `inst_stock_flow`（既有表，`inst_type='foreign'`） | ✅ | 已有資料，本輪只新增 percentile 計算邏輯，不新增 ETL |
| foreign_tx_long_oi / short_oi / net_oi | FinMind `TaiwanFuturesInstitutionalInvestors`（data_id=TX） | ✅ 新 ETL | 實測：`start_date`~`end_date` 區間一次回傳完整範圍（非「只回 start_date」那個坑），400 天回補只需 1 次 API 呼叫 |
| foreign_tx_net_oi_change_1d/3d/5d | 從上面欄位衍生 | ✅ | |
| taiwan_vix_close / change / percentile | ❌ **無對應 dataset** | **永久 UNKNOWN** | 探測 `TaiwanVix`、`VIX`、`WorldMarkets` 皆回 `422 Unprocessable Entity`；FinMind 官方文件與 dataset catalog 都沒有台灣期交所公告的「臺指選擇權波動率指數」對應項目 |
| txo_pc_volume_ratio / oi_ratio | FinMind `TaiwanOptionInstitutionalInvestors`（data_id=TXO） | ⚠️ 新 ETL，但**範圍受限** | 只有三大法人（自營商/投信/外資）買賣量與未平倉量，**非全市場**（TAIFEX 官方 Put/Call Ratio 用全市場含散戶成交量算，本專案無法取得逐筆全市場 TXO 成交資料）。已在欄位／模組 docstring 明確標註是「法人口徑活動量」，不宣稱等同官方 PCR |

## Family C：GLOBAL_RISK

| metric | 來源 | DB 可用性 | 備註 |
|---|---|---|---|
| us_vix_close / change / percentile | FinMind `USStockPrice`（data_id=`^VIX`） | ✅ 新 ETL | |
| nasdaq_return_1d/5d | FinMind `USStockPrice`（data_id=`^IXIC`） | ✅ 新 ETL | |
| sox_return_1d/5d | FinMind `USStockPrice`（data_id=`^SOX`） | ✅ 新 ETL | 半導體指數優先於大盤指數，符合規格書「貼近台股電子供應鏈背景」的要求 |
| us10y_yield / change_bp | ❌ **無對應 dataset** | **永久 UNKNOWN** | 探測 `USStockPrice` 的 `^TNX`／`^TYX`／`^FVX`（Yahoo 美債殖利率慣用代碼）全部回 0 筆（`msg: success` 但空 data）；FinMind 沒有債券殖利率類 dataset |

## Family D：MACRO_COMMODITY_RISK

| metric | 來源 | DB 可用性 | 備註 |
|---|---|---|---|
| wti_return_1d/5d/20d | FinMind `CrudeOilPrices`（data_id=WTI） | ✅ 新 ETL | **資料有 1~3 天延遲**：backfill 當下 WTI/Brent 最新資料只到 9/1，9/2~9/4 三天是 NULL（見下方 Gotcha） |
| brent_return_1d/5d/20d | FinMind `CrudeOilPrices`（data_id=Brent） | ✅ 新 ETL | 同上延遲問題 |
| gold_return_1d/5d/20d | FinMind `GoldPrice`（無 data_id，全球單一序列） | ✅ 新 ETL | 回應筆數異常大（79,735 筆／一次 API call，疑似該 dataset 不支援 server-side 日期過濾，本地端用 `date` 欄位精確比對正確取值） |
| usdtwd_return_1d/5d/percentile | FinMind `TaiwanExchangeRate`（data_id=USD） | ✅ 新 ETL | 用 `(spot_buy + spot_sell) / 2` 當即期匯率代表值 |

## 回補結果（2026-09-04 執行）

```
python3 scripts/backfill_market_stress_indicators.py --days 400
→ 2025-07-31 ~ 2026-09-04，共 287 個交易日
foreign_tx_net_oi:  267/287 non-null
txo_put_volume:     267/287 non-null
us_vix_close:       279/287 non-null
nasdaq_close:       276/287 non-null
sox_close:          276/287 non-null
wti_price:          269/287 non-null（最新到 9/1，9/2~9/4 缺）
brent_price:        271/287 non-null（同上）
gold_price:         287/287 non-null
usdtwd_spot:        270/287 non-null
```

配額成本：全部 9 個資料源合計 <30 quota（FinMind Sponsor 額度 6000/hour），400 天回補只需
每個 dataset 各打 1 次 API（已驗證這批「單一標的 + 區間」dataset 不會像 `margin_trade`／
`monthly_revenue`／`shareholding` 那樣「只回 start_date 當日」）。

## 結構性缺席欄位（誠實揭露，非抓取失敗）

1. **台灣 VIX（TAIWAN VIX）**：FinMind 無對應 dataset。`market_stress.py` 的
   `classify_family_flow()` 把這項永久列為 `data_expected_count` 的一部分但
   `data_available_count` 永遠不計入，讓 `data_complete` 誠實反映這個已知缺口，
   不假裝這項有被評估過。
2. **美國 10 年期公債殖利率（US10Y）**：FinMind 無對應 dataset。同上處理方式，在
   `classify_family_global()` 裡處理。

若未來要補齊這兩項，需要接其他資料源（例如 TAIFEX 官方 API、FRED API），超出本輪
FinMind-only 的範圍，留待下一輪視需求評估。

## Gotcha

- **`CrudeOilPrices` 資料本身有 1~3 天發布延遲**：這不是本專案 ETL 的問題，是資料源
  本身更新頻率較低；`classify_family_macro()` 的 5 日報酬率計算在資料延遲期間會用
  「最近可得的一筆」往回推 5 個資料點（非日曆天），不會因為最新 1~3 天缺值就整個
  Family 判 UNKNOWN。
- **`GoldPrice` dataset 疑似不支援 server-side 日期範圍過濾**：一次 API 呼叫回傳
  79,735 筆（遠超過請求區間應有的 ~400 筆），本地端用 `date` 欄位精確比對後才寫入
  正確筆數；未來若 FinMind 修正這個行為，程式邏輯不需要改動（本來就是本地端過濾）。
- **TXO Put/Call Ratio 只有法人口徑**：`classify_family_flow()` 已依規格書 §9.4
  明確要求，把 PCR 極端值限制成只能觸發 `PCR_DISLOCATION_WARNING`（附加 reason
  code），不可單獨造成任何 Family 判定 STRESS，避免這個範圍受限的資料源被誤用成
  主要判斷依據。
