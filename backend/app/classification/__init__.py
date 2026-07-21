"""
Phase 1 Canonical Market Classification System（2026-07-21）。

目標：為魚尾建立一套完整、可維護、可版本化的市場分類基礎，涵蓋所有 TWSE 上市普通股
（含金融股）與上市 ETF。這是「顯示層」的分類基礎建設，**不**是選股邏輯的一部分。

Phase 1 明確不做的事（見 docs/plans/canonical_classification/future_phase2_recommendations.md）：
    - 不修改 candidate_pool / classification / filters / momentum / market_regime 等
      選股 pipeline 邏輯
    - 不修改 industry_daily_flow 聚合或 industries.py 的 L0/L1 產業排行來源
    - 這裡算出來的 primary_sector / sub_sector 目前只透過獨立的
      `security_classification` / `etf_classification` 表 + API 呈現，
      不會回寫或覆蓋 `stocks_master.industry_name` / `sub_industry`

模組：
    taxonomy.py         canonical primary_sector / etf taxonomy 定義
    asset_type.py        asset_type 判斷（COMMON_STOCK/ETF/ETN/PREFERRED_STOCK/DR/REIT/
                          INDEX_BENCHMARK/OTHER）
    industry_mapping.py  FinMind industry_name（含歷史重複命名）→ primary_sector 系統性映射
    stock_overrides.py   個股層級 override（catch-all / 混合分類 / regression cases）
    etf_mapping.py       ETF taxonomy 規則引擎 + 個別 override
    build.py             整合入口：classify_security() / classify_all()
"""
