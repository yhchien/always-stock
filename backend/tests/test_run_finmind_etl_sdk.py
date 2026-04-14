from run_finmind_etl_sdk import determine_overall_status


def test_returns_error_when_critical_step_fails():
    result = determine_overall_status(
        {
            "daily_price": {"status": "error"},
            "inst_flow": {"status": "ok"},
            "daily_valuation": {"status": "ok"},
        }
    )

    assert result == "error"


def test_returns_partial_when_only_noncritical_step_fails():
    result = determine_overall_status(
        {
            "daily_price": {"status": "ok"},
            "inst_flow": {"status": "ok"},
            "daily_valuation": {"status": "error"},
            "monthly_revenue": {"status": "ok"},
        }
    )

    assert result == "partial"


def test_returns_insufficient_quota_when_any_step_hits_quota():
    result = determine_overall_status(
        {
            "daily_price": {"status": "ok"},
            "inst_flow": {"status": "ok"},
            "broker_trade_agg": {"status": "insufficient_quota"},
        }
    )

    assert result == "insufficient_quota"
