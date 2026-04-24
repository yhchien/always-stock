"""
M21 Trade Quality Context：把 DB raw data 預聚合成結論層訊號的資料管線。

主入口：`build_trade_quality_context(db, stock_id, buy_date)`
輸出 schema：docs/plans/trade_quality_context_spec.md §REQUIRED OUTPUT SCHEMA
落地計畫：docs/plans/m21_context_pipeline_implementation.md
"""
from app.analysis.context_builder import build_trade_quality_context

__all__ = ["build_trade_quality_context"]
