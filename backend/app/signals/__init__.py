"""
M23 每日異常訊號清單模組骨架（slice 4）。

7 大 stage（spec §2 / §5）：
  ingest → rank → candidate → filter → llm_research → llm_explain → persist

模組分工：
  - candidate_pool.py    Step 1~5：DB ingest、產業 / 個股 ranking、候選池建立 + 擴散
  - classification.py    Step 6：LEADER / FOLLOWER / LAGGARD candidate 預分類
  - filters.py           Step 6：hard exclusions + soft filters
  - llm_caller.py        Step 7~8：LLM batch research + explanation
  - exclusions.py        ETF / 金融股 deterministic 判斷 + 黑名單 + group_stocks 載入
  - pipeline.py          主流程：status 流轉 + 進度 update + exception → failed
  - group_stocks.json    第一版手動集團白名單（slice 4 為 stub，未來接公開資訊觀測站）

主入口：
  - run_signal_pipeline_sync(job_id, target_date)：cron / BackgroundTasks 共用

Spec：docs/plans/m23_daily_signals_spec.md
"""
from app.signals.pipeline import run_signal_pipeline_sync

__all__ = ["run_signal_pipeline_sync"]
