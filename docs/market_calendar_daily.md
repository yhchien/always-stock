# 台股行事曆每日預測

## 每日排程

`.github/workflows/market_calendar_daily.yml` 在台北時間 06:30 執行。台指期與小台期盤後交易在 05:00 結束，這個時間保留約 90 分鐘讓 TAIFEX、Yahoo Finance 與 FinMind 完成資料整理。

執行程式為 `backend/run_market_calendar_daily.py`，會：

1. 抓取至少一年的歷史資料，確保 KDJ、20 日線、60 日線與規則暖機。
2. 使用前一個台股交易日收盤資料，以及已完成的美股／台指期夜盤資料。
3. 產生下一個交易日的偏漲／偏跌預測。
4. 更新前一日以前已經知道結果的預測。
5. UPSERT 到 `market_calendar_predictions`。

需要的 GitHub Actions secrets：

- `DATABASE_URL`
- `FINMIND_TOKEN`

## 匯入目前 50 日回測結果

部署後可以用下列指令把現有 walk-forward 結果 idempotent 匯入資料庫：

```bash
PYTHONPATH=backend python3 backend/seed_market_calendar_predictions.py \
  --input docs/reports/market_calendar_rule_analysis_2026-08-03_2026-11-23_walk_forward.csv
```

這個指令會匯入最後 50 筆，不會重複建立相同 `signal_date` 的資料。

## API 與畫面

- `GET /api/market-calendar/latest`：最新一筆預測。
- `GET /api/market-calendar/history?limit=10`：畫面使用的近 10 日。
- `limit` 最大 50，可供稽核或報表讀取。
- 前端頁面：`/market-calendar`。

資料庫保留至少 50 日結果；畫面只顯示近 10 日，另顯示已驗證筆數與方向準確率。
