from run_finmind_etl_sdk import determine_overall_status, _run_critical_step_with_retry


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


def test_holiday_from_daily_price_propagates_to_overall():
    # daily_price 偵測到假日後，下游 step 會被標 skipped_holiday；整體應為 holiday
    result = determine_overall_status(
        {
            "daily_price": {"status": "holiday"},
            "inst_flow": {"status": "skipped_holiday"},
            "daily_valuation": {"status": "skipped_holiday"},
            "monthly_revenue": {"status": "skipped_holiday"},
            "financial_statement": {"status": "skipped_holiday"},
            "broker_trade_agg": {"status": "skipped_holiday"},
        }
    )

    assert result == "holiday"


def test_holiday_takes_precedence_over_noncritical_partial():
    # 理論上 holiday 時不會有其他 step 真的跑，但若混到非 resumeable status，
    # 仍以 daily_price holiday 為最終判定
    result = determine_overall_status(
        {
            "daily_price": {"status": "holiday"},
            "inst_flow": {"status": "skipped_holiday"},
        }
    )

    assert result == "holiday"


def test_holiday_does_not_trigger_critical_retry():
    # CRITICAL step 回 holiday 時應立即返回，不進入 no_data retry 排程
    call_count = {"n": 0}

    def step():
        call_count["n"] += 1
        return {"status": "holiday"}

    result = _run_critical_step_with_retry("daily_price", step)

    assert result == {"status": "holiday"}
    assert call_count["n"] == 1
