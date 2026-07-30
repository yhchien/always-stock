from __future__ import annotations

import time
import tracemalloc
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, SignalOutcomeMetric
from app.signals import outcome_metrics


@pytest.mark.parametrize("row_count", [1_000, 10_000, 50_000])
def test_outcome_queries_scale_without_loading_all_items(row_count):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    start = date(2026, 1, 1)
    with Session() as db:
        rows = []
        for index in range(row_count):
            signal_date = start + timedelta(days=index % 200)
            decision = "RECOMMEND" if index % 3 == 0 else "NOT_SELECTED"
            label = ("WINNER", "NEUTRAL", "BIG_LOSER")[index % 3]
            rows.append(
                {
                    "signal_date": signal_date,
                    "stock_id": f"{index:06d}",
                    "stock_name": f"股票{index}",
                    "asset_type": "COMMON_STOCK",
                    "p3_decision": decision,
                    "global_eligible": True,
                    "recommendation_rank": index % 20 + 1 if decision == "RECOMMEND" else None,
                    "backend_priority_rank": index % 200 + 1,
                    "entry_price": 100.0,
                    "exit_price": 110.0,
                    "outcome_return_pct": 10.0,
                    "outcome_label": label,
                    "matured_at": signal_date + timedelta(days=14),
                    "outcome_horizon": "DAY10",
                    "outcome_definition_version": "day10_v1",
                    "entry_price_definition": "signal_date_close",
                    "exit_price_definition": "tenth_subsequent_market_trade_date_close",
                    "prompt_family_version": "v7",
                    "selection_version": "v7_global_selector",
                    "momentum_score_version": "v3_applicability_aware",
                    "metadata_json": {},
                }
            )
        db.bulk_insert_mappings(SignalOutcomeMetric, rows)
        db.commit()
        del rows

        tracemalloc.start()
        t0 = time.perf_counter()
        summary = outcome_metrics.get_outcome_summary(db)
        t1 = time.perf_counter()
        series = outcome_metrics.get_outcome_timeseries(db)
        t2 = time.perf_counter()
        page = outcome_metrics.get_outcome_items(db, page=2, page_size=50)
        t3 = time.perf_counter()
        csv_line_count = sum(
            chunk.count("\n")
            for chunk in outcome_metrics.iter_outcome_items_csv(db)
        )
        t4 = time.perf_counter()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(
            {
                "row_count": row_count,
                "summary_duration": round(t1 - t0, 4),
                "timeseries_duration": round(t2 - t1, 4),
                "page_query_duration": round(t3 - t2, 4),
                "serialization_duration": round(t4 - t3, 4),
                "peak_memory_mib": round(peak / 1024 / 1024, 3),
            }
        )

        assert summary["sample"]["total"] == (row_count + 2) // 3
        assert len(series["items"]) == 200
        assert len(page["items"]) == 50
        assert csv_line_count == row_count + 1
        assert t1 - t0 < 2.0
        assert t2 - t1 < 2.0
        assert t3 - t2 < 2.0
        assert t4 - t3 < 8.0
        assert peak < 32 * 1024 * 1024
    Base.metadata.drop_all(bind=engine)
