"""P2 scale guard: applicability scoring remains linear at production-sized pools."""
from time import perf_counter
import tracemalloc

import pytest

from app.signals import momentum


def _candidate(index: int) -> dict:
    asset_type = ("COMMON_STOCK", "FINANCIAL", "ETF")[index % 3]
    candidate = {
        "stock_id": f"S{index:04d}",
        "asset_type": asset_type,
        "rs_market_percentile_20d": float(index % 101),
        "return_percentile_60d": float((index * 3) % 101),
        "return_percentile_5d": float((index * 7) % 101),
        "rs_industry_percentile_20d": float((index * 11) % 101),
        "inst_buy_to_turnover_percentile_2d": float((index * 13) % 101),
        "consecutive_buy_days_3d": index % 4,
        "volume_ratio_percentile_5d_60d": float((index * 17) % 101),
    }
    # ETF company fundamentals stay absent/N/A. Half of company instruments
    # have actual revenue evidence; the rest exercise MISSING.
    if asset_type != "ETF" and index % 2 == 0:
        candidate.update(
            revenue_yoy=10.0,
            revenue_yoy_percentile=float((index * 19) % 101),
            revenue_yoy_acceleration=1.0,
            revenue_mom=1.0,
        )
    return candidate


@pytest.mark.parametrize("candidate_count", [100, 500, 1000])
def test_applicability_score_scale_is_linear_and_bounded(candidate_count):
    candidates = [_candidate(i) for i in range(candidate_count)]
    tracemalloc.start()
    started = perf_counter()
    scored = [momentum.compute_momentum_score(c) for c in candidates]
    elapsed = perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(scored) == candidate_count
    assert all(0.0 <= row["momentum_score"] <= 100.0 for row in scored)
    # Generous CI guardrails: catches accidental quadratic/per-candidate bulk
    # allocations without treating micro-benchmark noise as a product failure.
    assert elapsed < 2.0
    assert peak_bytes < 32 * 1024 * 1024
