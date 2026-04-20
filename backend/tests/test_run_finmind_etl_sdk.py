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


def test_returns_error_when_critical_step_stays_no_data_after_retry():
    # Orchestrator 在 retry 用盡後若 CRITICAL step 仍為 no_data，判定為 error
    result = determine_overall_status(
        {
            "daily_price": {"status": "ok"},
            "inst_flow": {"status": "no_data"},
            "daily_valuation": {"status": "ok"},
        }
    )

    assert result == "error"


def test_no_data_on_noncritical_step_is_treated_as_ok():
    # 月營收/財報這類非 critical 資料，no_data 在多數交易日是正常情境
    result = determine_overall_status(
        {
            "daily_price": {"status": "ok"},
            "inst_flow": {"status": "ok"},
            "daily_valuation": {"status": "ok"},
            "monthly_revenue": {"status": "no_data"},
            "financial_statement": {"status": "no_data"},
            "broker_trade_agg": {"status": "ok"},
        }
    )

    assert result == "ok"
