from __future__ import annotations

from datetime import date
import json
import time
import tracemalloc

import pytest

from app.signals import global_selector, llm_caller


SELECTION_DATE = date(2026, 7, 29)


def _eligible(stock: str, *, asset_type: str = "COMMON_STOCK", cluster: str = "AI"):
    return {
        "stock": stock,
        "name": f"Stock-{stock}",
        "asset_type": asset_type,
        "role": "EMERGING_MOMENTUM",
        "tracking_state": "NEW",
        "entry_state": "SETUP",
        "conviction": "high",
        "watch_quality_state": "SETUP",
        "momentum_freshness": "FRESH",
        "momentum_score": 82,
        "rs_market_percentile_20d": 91,
        "total_institution_flow_3d": 100_000_000,
        "from_a": True,
        "from_b": False,
        "from_c": False,
        "from_d": False,
        "business_summary": "具體且可驗證的公司或商品摘要",
        "business_validation": "VERIFIED",
        "theme_validation": "VERIFIED",
        "supply_chain_validation": "VERIFIED",
        "theme": {
            "main_theme": cluster,
            "theme_duration": "2Q_plus",
            "theme_maturity": "expanding",
            "theme_reason": "催化劑仍在延續",
        },
        "theme_cluster": cluster,
        "supply_chain_position": "midstream",
        "quality_assessment": {
            "momentum_quality": "HIGH",
            "participation_quality": "HIGH",
            "catalyst_quality": "HIGH",
            "evidence_coherence": "STRONG",
        },
        "quality_evidence": {"price": True, "participation": True},
        "short_reason": "量價、參與與催化劑互相印證",
    }


def _cards(count: int, *, cluster: str = "AI"):
    rows = [_eligible(str(1000 + index), cluster=cluster) for index in range(count)]
    return global_selector.build_compact_selection_cards(
        rows,
        selection_date=SELECTION_DATE,
    )


def _recommend(
    stock: str,
    rank: int,
    *,
    override: bool = False,
    market_resilience: str | None = None,
    market_context_reason: str | None = None,
):
    return {
        "stock": stock,
        "decision": "RECOMMEND",
        "recommendation_rank": rank,
        "recommendation_thesis": f"{stock} 的正向 thesis",
        "relative_advantage": f"{stock} 有獨立的相對優勢",
        "recommendation_basis": ["MOMENTUM", "CATALYST"],
        "rank_override": override,
        "rank_override_reason": "外部催化劑更明確" if override else None,
        "selection_reason_code": None,
        "selection_reason": "列入今日正式推薦。",
        "theme_cluster": "AI",
        "distinct_thesis": True,
        "overlap_with": [],
        "overlap_reason": None,
        "market_resilience": market_resilience,
        "market_context_reason": market_context_reason,
    }


def _not_selected(
    stock: str,
    code: str = "NO_DISTINCT_DAILY_EDGE",
    *,
    market_resilience: str | None = None,
    market_context_reason: str | None = None,
):
    return {
        "stock": stock,
        "decision": "NOT_SELECTED",
        "recommendation_rank": None,
        "recommendation_thesis": None,
        "relative_advantage": None,
        "recommendation_basis": [],
        "rank_override": False,
        "rank_override_reason": None,
        "selection_reason_code": code,
        "selection_reason": "候選仍有效，但今日相對優勢尚不鮮明。",
        "theme_cluster": "AI",
        "distinct_thesis": False,
        "overlap_with": [],
        "overlap_reason": None,
        "market_resilience": market_resilience,
        "market_context_reason": market_context_reason,
    }


def _payload(items):
    recommend_count = sum(item.get("decision") == "RECOMMEND" for item in items)
    return {
        "selection_version": global_selector.SELECTION_VERSION,
        "date": SELECTION_DATE.isoformat(),
        "selection_complete": True,
        "items": items,
        "summary": {
            "eligible_count": len(items),
            "recommend_count": recommend_count,
            "not_selected_count": len(items) - recommend_count,
            "selection_rationale": "依完整候選的正向論點與同日相對優勢比較。",
        },
    }


@pytest.mark.parametrize("recommended_count", [0, 8])
def test_no_fixed_count_all_or_none_is_valid(recommended_count):
    cards = _cards(8)
    if recommended_count:
        items = [_recommend(card["stock"], index + 1) for index, card in enumerate(cards)]
    else:
        items = [_not_selected(card["stock"]) for card in cards]
    result = global_selector.validate_global_selection(
        _payload(items),
        cards,
        selection_date=SELECTION_DATE,
    )
    assert result["summary"]["recommend_count"] == recommended_count
    assert result["summary"]["not_selected_count"] == 8 - recommended_count


def test_model_self_reported_summary_counts_are_ignored_and_backend_derived():
    """Live probing (2026-07-20 replay) showed the model repeatedly miscounting
    its own recommend/eligible/not_selected totals across ~25 cards -- even
    after using its one contract-correction retry -- which used to raise
    GLOBAL_SELECTION_SUMMARY_INVALID and fail the whole atomic selection even
    though every item-level decision was valid.  Those counts are fully
    derivable from `items`/`cards`, so the model's self-report (if present at
    all) must be ignored entirely and never gate validation."""
    cards = _cards(2)
    items = [_recommend("1000", 1), _not_selected("1001")]
    payload = _payload(items)
    payload["summary"]["eligible_count"] = 999
    payload["summary"]["recommend_count"] = -1
    payload["summary"]["not_selected_count"] = 42
    result = global_selector.validate_global_selection(
        payload,
        cards,
        selection_date=SELECTION_DATE,
    )
    assert result["summary"]["eligible_count"] == 2
    assert result["summary"]["recommend_count"] == 1
    assert result["summary"]["not_selected_count"] == 1


def test_global_selection_schema_does_not_require_model_reported_counts():
    schema = global_selector.global_selection_output_schema(
        expected_version=global_selector.SELECTION_VERSION,
        selection_date=SELECTION_DATE.isoformat(),
        expected_stocks=["1000", "1001"],
    )
    summary_schema = schema["properties"]["summary"]
    assert summary_schema["required"] == ["selection_rationale"]
    assert set(summary_schema["properties"]) == {"selection_rationale"}


def test_mixed_selection_requires_explicit_rank_override():
    cards = _cards(4)
    items = [
        _recommend("1000", 1),
        _not_selected("1001"),
        _recommend("1002", 2, override=True),
        _not_selected("1003"),
    ]
    result = global_selector.validate_global_selection(
        _payload(items),
        cards,
        selection_date=SELECTION_DATE,
    )
    assert result["summary"] == {
        "eligible_count": 4,
        "recommend_count": 2,
        "not_selected_count": 2,
        "selection_rationale": "依完整候選的正向論點與同日相對優勢比較。",
    }

    items[2]["rank_override"] = False
    with pytest.raises(
        global_selector.GlobalSelectionError,
        match="rank override",
    ) as exc:
        global_selector.validate_global_selection(
            _payload(items),
            cards,
            selection_date=SELECTION_DATE,
        )
    assert exc.value.code == "GLOBAL_SELECTION_RANK_OVERRIDE_MISSING"


def test_same_cluster_has_no_cap_and_overlap_requires_evidence():
    cards = _cards(8, cluster="AI伺服器")
    all_recommended = [_recommend(card["stock"], index + 1) for index, card in enumerate(cards)]
    assert (
        global_selector.validate_global_selection(
            _payload(all_recommended),
            cards,
            selection_date=SELECTION_DATE,
        )["summary"]["recommend_count"]
        == 8
    )

    overlap = [_recommend(card["stock"], index + 1) for index, card in enumerate(cards[:-1])]
    overlap.append(_not_selected(cards[-1]["stock"], "THESIS_OVERLAP"))
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.validate_global_selection(
            _payload(overlap),
            cards,
            selection_date=SELECTION_DATE,
        )
    assert exc.value.code == "GLOBAL_SELECTION_OVERLAP_INVALID"

    overlap[-1]["overlap_with"] = [cards[0]["stock"]]
    overlap[-1]["overlap_reason"] = "催化劑與曝險完全重複，沒有獨立日內優勢。"
    global_selector.validate_global_selection(
        _payload(overlap),
        cards,
        selection_date=SELECTION_DATE,
    )


def test_asset_and_source_are_descriptive_not_eligibility_or_score():
    rows = [
        _eligible("2330", asset_type="COMMON_STOCK"),
        _eligible("2881", asset_type="FINANCIAL"),
        _eligible("0050", asset_type="ETF"),
    ]
    rows[0].update({"from_a": True, "from_b": False})
    rows[1].update({"from_a": False, "from_b": True})
    rows[2].update({"from_a": False, "from_b": False, "from_c": True})
    cards = global_selector.build_compact_selection_cards(rows, selection_date=SELECTION_DATE)
    assert [card["backend_priority_rank"] for card in cards] == [1, 2, 3]
    assert [card["asset_type"] for card in cards] == [
        "COMMON_STOCK",
        "FINANCIAL",
        "ETF",
    ]
    assert cards[2]["supply_chain_role"] == "NOT_APPLICABLE"
    result = global_selector.validate_global_selection(
        _payload([_recommend(card["stock"], index + 1) for index, card in enumerate(cards)]),
        cards,
        selection_date=SELECTION_DATE,
    )
    assert result["summary"]["recommend_count"] == 3


def test_unconfirmed_is_eligible_and_mismatch_can_be_true_remove():
    unconfirmed = _eligible("1001")
    unconfirmed.update(
        {
            "assessment_status": "REMOVE",
            "decision": "REMOVE",
            "veto_reason": "BUSINESS_MISMATCH",
            "business_validation": "UNCONFIRMED",
        }
    )
    mismatch = _eligible("1002")
    mismatch.update(
        {
            "assessment_status": "REMOVE",
            "decision": "REMOVE",
            "veto_reason": "BUSINESS_MISMATCH",
            "business_validation": "MISMATCH",
        }
    )
    eligible, removed = global_selector.partition_assessments([unconfirmed, mismatch])
    assert [item["stock"] for item in eligible] == ["1001"]
    assert eligible[0]["veto_reason"] is None
    assert [item["stock"] for item in removed] == ["1002"]
    assert removed[0]["selection_status"] == "REMOVE"


def test_event_veto_requires_summary_and_traceable_source():
    unsupported = _eligible("1001")
    unsupported.update(
        {
            "assessment_status": "REMOVE",
            "decision": "REMOVE",
            "veto_reason": "MATERIAL_NEGATIVE_EVENT",
            "short_reason": "發現重大負面事件。",
            "veto_evidence": {"summary": "重大負面事件", "source_urls": []},
        }
    )
    supported = _eligible("1002")
    supported.update(
        {
            "assessment_status": "REMOVE",
            "decision": "REMOVE",
            "veto_reason": "DATA_CONTRADICTION",
            "veto_evidence": {
                "summary": "公告資料與候選卡片互相矛盾",
                "source_urls": ["https://example.com/filing"],
            },
        }
    )

    eligible, removed = global_selector.partition_assessments([unsupported, supported])

    assert [item["stock"] for item in eligible] == ["1001"]
    assert eligible[0]["veto_reason"] is None
    assert [item["stock"] for item in removed] == ["1002"]


def test_backend_max_remove_is_final_before_global_selector():
    item = _eligible("1001")
    item.update(
        {
            "backend_max_decision": "REMOVE",
            "assessment_status": "ELIGIBLE",
            "decision": "WATCH",
        }
    )

    eligible, removed = global_selector.partition_assessments([item])

    assert eligible == []
    assert removed[0]["decision"] == "REMOVE"
    assert removed[0]["veto_reason"] == "BACKEND_MAX_REMOVE"


def test_quality_remove_requires_phase2_evidence_and_matching_low_assessment():
    unsupported = _eligible("1001")
    unsupported.update(
        {
            "decision": "REMOVE",
            "veto_reason": "WEAK_PARTICIPATION",
            "quality_evidence": None,
            "quality_assessment": {"participation_quality": "LOW"},
        }
    )
    supported = _eligible("1002")
    supported.update(
        {
            "decision": "REMOVE",
            "veto_reason": "WEAK_PARTICIPATION",
            "quality_assessment": {"participation_quality": "LOW"},
        }
    )
    eligible, removed = global_selector.partition_assessments([unsupported, supported])
    assert [item["stock"] for item in eligible] == ["1001"]
    assert [item["stock"] for item in removed] == ["1002"]


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (lambda items: items.pop(), "GLOBAL_SELECTION_MISSING_STOCK"),
        (lambda items: items.append(dict(items[0])), "GLOBAL_SELECTION_DUPLICATE_STOCK"),
        (
            lambda items: items.__setitem__(0, {**items[0], "stock": "UNKNOWN"}),
            "GLOBAL_SELECTION_UNKNOWN_STOCK",
        ),
        (
            lambda items: items.__setitem__(1, {**items[1], "recommendation_rank": 1}),
            "GLOBAL_SELECTION_RANK_INVALID",
        ),
        (
            lambda items: items.__setitem__(
                0,
                {
                    **_not_selected(items[0]["stock"]),
                    "selection_reason_code": "INVALID",
                },
            ),
            "GLOBAL_SELECTION_REASON_CODE_INVALID",
        ),
    ],
)
def test_37_card_alignment_failures_are_atomic(mutator, expected_code):
    cards = _cards(37)
    items = [_recommend(card["stock"], index + 1) for index, card in enumerate(cards)]
    mutator(items)
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.validate_global_selection(
            _payload(items),
            cards,
            selection_date=SELECTION_DATE,
        )
    assert exc.value.code == expected_code


def test_context_guard_fails_without_tournament_or_partial_selection(monkeypatch):
    cards = _cards(25)
    monkeypatch.setenv("SIGNALS_GLOBAL_SELECTOR_CONTEXT_LIMIT_TOKENS", "1")
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.run_global_selection(
            cards,
            {},
            selection_date=SELECTION_DATE,
        )
    assert exc.value.code == "GLOBAL_SELECTION_CONTEXT_EXCEEDED"


def test_global_call_has_no_silent_fallback(monkeypatch):
    cards = _cards(2)
    monkeypatch.setattr(
        global_selector.llm_caller,
        "_call_llm_json",
        lambda *args, **kwargs: (
            None,
            {"status": "openai_exception", "message": "timeout"},
        ),
    )
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.run_global_selection(
            cards,
            {},
            selection_date=SELECTION_DATE,
        )
    assert exc.value.code == "GLOBAL_SELECTION_LLM_FAILED"


def test_run_global_selection_omits_market_environment_key_by_default(monkeypatch):
    """M27 Market Regime v2：不傳 market_environment 時（shadow 模式的預設
    行為），request_payload 不該多一個 key——跟這個參數加入之前逐位元組相同。"""
    cards = _cards(1)
    request_payloads = []

    def fake_call(_system, user_msg, **kwargs):
        request_payloads.append(json.loads(user_msg))
        return _payload([_recommend("1000", 1)]), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    global_selector.run_global_selection(cards, {}, selection_date=SELECTION_DATE)

    assert "market_environment" not in request_payloads[0]


def test_run_global_selection_includes_market_environment_when_provided(monkeypatch):
    """global_only／production 模式才會傳 market_environment；傳了就該原樣
    出現在送給 LLM 的 request_payload 裡。"""
    cards = _cards(1)
    request_payloads = []
    env = {
        "trend_regime": "BULL_TREND",
        "market_stress": "STRESS",
        "effective_market_state": "BULL_STRESSED",
        "stress_families": {"LOCAL_MARKET_INTERNALS": "STRESS"},
        "key_reason_codes": ["BREADTH_DETERIORATION"],
        "market_stress_data_complete": False,
    }

    def fake_call(_system, user_msg, **kwargs):
        request_payloads.append(json.loads(user_msg))
        return _payload(
            [
                _recommend(
                    "1000",
                    1,
                    market_resilience="STRONG",
                    market_context_reason="即使市場壓力偏高，相對強度仍明顯領先。",
                )
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=env
    )

    assert request_payloads[0]["market_environment"] == env


def test_global_rank_override_is_derived_without_changing_decision(monkeypatch):
    cards = _cards(2)
    invalid = _payload([
        _not_selected("1000"),
        _recommend("1001", 1),
    ])
    request_payloads = []

    def fake_call(_system, user_msg, **kwargs):
        request_payloads.append(json.loads(user_msg))
        return invalid, {"status": "ok"}

    monkeypatch.setattr(
        global_selector.llm_caller,
        "_call_llm_json",
        fake_call,
    )
    result = global_selector.run_global_selection(
        cards,
        {},
        selection_date=SELECTION_DATE,
    )

    assert len(request_payloads) == 1
    assert "contract_retry" not in request_payloads[0]
    assert result["summary"]["recommend_count"] == 1
    recommended = next(
        item for item in result["items"] if item["decision"] == "RECOMMEND"
    )
    assert recommended["stock"] == "1001"
    assert recommended["rank_override"] is True
    assert "1001 有獨立的相對優勢" in recommended["rank_override_reason"]
    assert result["llm_diagnostic"]["contract_retry_attempt"] == 0
    assert result["llm_diagnostic"]["backend_derived_rank_overrides"] == [
        "1001"
    ]


def test_global_duplicate_ranks_are_normalized_without_changing_membership(
    monkeypatch,
):
    cards = _cards(3)
    payload = _payload([
        _recommend("1000", 1),
        _recommend("1001", 1),
        _not_selected("1002"),
    ])

    monkeypatch.setattr(
        global_selector.llm_caller,
        "_call_llm_json",
        lambda *args, **kwargs: (payload, {"status": "ok"}),
    )
    result = global_selector.run_global_selection(
        cards,
        {},
        selection_date=SELECTION_DATE,
    )

    recommended = [
        item for item in result["items"] if item["decision"] == "RECOMMEND"
    ]
    assert [item["stock"] for item in recommended] == ["1000", "1001"]
    assert [item["recommendation_rank"] for item in recommended] == [1, 2]
    assert result["summary"]["recommend_count"] == 2
    assert result["llm_diagnostic"][
        "backend_normalized_recommendation_ranks"
    ] == [{"stock": "1001", "from": 1, "to": 2}]


def test_global_other_semantic_error_retries_complete_card_set_once(monkeypatch):
    cards = _cards(2)
    invalid = _payload([_recommend("1000", 1), _not_selected("1001")])
    invalid["summary"]["selection_rationale"] = ""
    corrected = _payload([_recommend("1000", 1), _not_selected("1001")])
    request_payloads = []
    responses = iter([invalid, corrected])

    def fake_call(_system, user_msg, **kwargs):
        request_payloads.append(json.loads(user_msg))
        return next(responses), {"status": "ok"}

    monkeypatch.setattr(
        global_selector.llm_caller,
        "_call_llm_json",
        fake_call,
    )
    result = global_selector.run_global_selection(
        cards,
        {},
        selection_date=SELECTION_DATE,
    )

    assert len(request_payloads) == 2
    retry = request_payloads[1]["contract_retry"]
    assert retry["previous_error_code"] == "GLOBAL_SELECTION_SUMMARY_INVALID"
    assert len(request_payloads[1]["compact_selection_cards"]) == 2
    assert result["summary"]["eligible_count"] == 2
    assert result["llm_diagnostic"]["contract_retry_attempt"] == 1


@pytest.mark.parametrize("candidate_count", [25, 50, 100, 200])
def test_compact_card_scale_guard(candidate_count):
    tracemalloc.start()
    started = time.perf_counter()
    cards = _cards(candidate_count)
    capacity = global_selector.estimate_selection_capacity(cards)
    items = [_recommend(card["stock"], index + 1) for index, card in enumerate(cards)]
    result = global_selector.validate_global_selection(
        _payload(items),
        cards,
        selection_date=SELECTION_DATE,
    )
    merged = global_selector.merge_selection_items(cards, result)
    final_payload = llm_caller.assemble_final_output(
        {},
        merged,
        candidate_pool_size=candidate_count,
    )
    serialized_api_response = json.dumps(
        {
            "watchlist": final_payload["watchlist"],
            "not_selected": final_payload["not_selected"],
            "removed": final_payload["removed"],
            "technical_failures": [],
            "summary": final_payload["summary"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    duration = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result["summary"]["eligible_count"] == candidate_count
    assert capacity.candidate_count == candidate_count
    assert capacity.serialized_bytes > 0
    assert capacity.estimated_input_tokens > 0
    assert len(final_payload["watchlist"]) == candidate_count
    assert serialized_api_response.startswith('{"watchlist":')
    assert duration < 2
    assert peak < 32 * 1024 * 1024


@pytest.mark.parametrize(
    "candidate_count,previous_fixed_reserve",
    [(116, 16_000), (135, 16_000)],
)
def test_output_token_reserve_scales_past_prior_fixed_incident_size(
    candidate_count, previous_fixed_reserve
):
    """2026-08-05／2026-08-10：候選數 135／116 撞上舊的固定 16,000 token 上限，
    3 次重試全被 max_output_tokens 截斷，當天推薦清單整個是空的。"""
    cards = _cards(candidate_count)
    capacity = global_selector.estimate_selection_capacity(cards)
    assert capacity.output_token_reserve > previous_fixed_reserve


def test_output_token_reserve_grows_linearly_with_candidate_count():
    small = global_selector.estimate_selection_capacity(_cards(10))
    large = global_selector.estimate_selection_capacity(_cards(200))
    assert large.output_token_reserve > small.output_token_reserve
    assert large.output_token_reserve == global_selector._default_output_token_reserve(200)
    assert small.output_token_reserve == global_selector._default_output_token_reserve(10)


def test_output_token_reserve_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("SIGNALS_GLOBAL_SELECTOR_OUTPUT_TOKEN_RESERVE", "9999")
    capacity = global_selector.estimate_selection_capacity(_cards(200))
    assert capacity.output_token_reserve == 9999


@pytest.mark.parametrize("candidate_count", [112, 116, 135])
def test_selection_timeout_scales_past_prior_fixed_120s_incident_size(
    candidate_count,
):
    """2026-08-11：112 檔候選在固定 120 秒逾時下連續 3 次 APITimeoutError，
    當天推薦清單整個是空的（見 CLAUDE.md）。放大 output_token_reserve 換來足夠
    輸出空間後，生成那麼多 JSON 需要的時間本來就會超過針對小候選池設計的 120 秒。"""
    timeout = global_selector._default_selection_timeout_seconds(candidate_count)
    assert timeout > 120.0


def test_selection_timeout_grows_linearly_with_candidate_count():
    small = global_selector._default_selection_timeout_seconds(10)
    large = global_selector._default_selection_timeout_seconds(200)
    assert large > small
    assert small == global_selector._SELECTION_TIMEOUT_MIN_SECONDS  # floor for tiny pools


def test_selection_timeout_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("SIGNALS_GLOBAL_SELECTOR_TIMEOUT_SECONDS", "555")
    cards = _cards(2)
    captured_kwargs = {}

    def fake_call(_system, user_msg, **kwargs):
        captured_kwargs.update(kwargs)
        return _payload([_recommend("1000", 1), _not_selected("1001")]), {
            "status": "ok"
        }

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    global_selector.run_global_selection(cards, {}, selection_date=SELECTION_DATE)

    assert captured_kwargs["timeout"] == 555.0


def test_run_global_selection_passes_scaled_timeout_and_single_sdk_try(
    monkeypatch,
):
    """呼叫 _call_llm_json 時應帶入依候選數放寬的 timeout，且 max_retries=0——
    app 層迴圈已經自己重試 3 次，SDK 層再重試一次只會把每次等待時間乘以 2，
    對成功率沒有幫助卻拖長 worst-case 總時間。"""
    cards = _cards(112)
    captured_kwargs = {}

    def fake_call(_system, user_msg, **kwargs):
        captured_kwargs.update(kwargs)
        items = [_recommend("1000", 1)] + [
            _not_selected(str(1000 + i)) for i in range(1, 112)
        ]
        return _payload(items), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    global_selector.run_global_selection(cards, {}, selection_date=SELECTION_DATE)

    assert captured_kwargs["timeout"] == global_selector._default_selection_timeout_seconds(112)
    assert captured_kwargs["timeout"] > 120.0
    assert captured_kwargs["max_retries"] == 0


# ===================== M27 Market Regime v2 §36 Regression =====================
# Production Integration（2026-09-04）：Global Selector 正式使用 Market
# Environment 後的完整矩陣，逐項對照規格書 §36。


def _stressed_env(effective_market_state: str = "BULL_STRESSED"):
    return {
        "trend_regime": "BULL_TREND",
        "market_stress": "STRESS",
        "effective_market_state": effective_market_state,
        "stress_families": {"LOCAL_MARKET_INTERNALS": "STRESS"},
        "key_reason_codes": ["BREADTH_DETERIORATION"],
        "market_stress_data_complete": False,
    }


def test_stressed_market_recommend_with_strong_resilience_is_legal(monkeypatch):
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [
                _recommend(
                    "1000",
                    1,
                    market_resilience="STRONG",
                    market_context_reason="逆風中相對強度、資金參與皆明顯領先。",
                )
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
    )
    assert result["summary"]["recommend_count"] == 1
    assert result["items"][0]["market_resilience"] == "STRONG"


def test_stressed_market_recommend_with_adequate_resilience_is_legal(monkeypatch):
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [
                _recommend(
                    "1000",
                    1,
                    market_resilience="ADEQUATE",
                    market_context_reason="逆風下仍有合理推薦基礎，優勢不算特別突出。",
                )
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
    )
    assert result["summary"]["recommend_count"] == 1


def test_stressed_market_recommend_with_weak_resilience_is_contract_invalid(monkeypatch):
    """§13：BULL_STRESSED/VOLATILE_STRESSED/RISK_OFF 下 RECOMMEND + WEAK 不合法；
    先依既有 retry policy 完整集合 retry，仍不合法則 GlobalSelectionError（不得
    Backend 靜默改成 NOT_SELECTED）。"""
    cards = _cards(1)
    calls = []

    def fake_call(_system, user_msg, **kwargs):
        calls.append(1)
        return _payload(
            [
                _recommend(
                    "1000",
                    1,
                    market_resilience="WEAK",
                    market_context_reason="相對強度不足以支撐正式推薦。",
                )
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.run_global_selection(
            cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
        )
    assert exc.value.code == "GLOBAL_SELECTION_CONTRACT_INVALID"
    # retry_enabled 預設 true -> max_attempts=3，全部都送一樣的非法回應才會用盡
    assert len(calls) == 3


@pytest.mark.parametrize("effective_state", ["BULL_STRESSED", "VOLATILE_STRESSED", "RISK_OFF"])
def test_recommend_with_weak_resilience_invalid_across_all_stressed_states(
    monkeypatch, effective_state
):
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [_recommend("1000", 1, market_resilience="WEAK", market_context_reason="不足。")]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.run_global_selection(
            cards,
            {},
            selection_date=SELECTION_DATE,
            market_environment=_stressed_env(effective_state),
        )
    assert exc.value.code == "GLOBAL_SELECTION_CONTRACT_INVALID"


def test_stressed_market_not_selected_with_weak_resilience_is_legal(monkeypatch):
    """WEAK 的守門只限制 RECOMMEND；NOT_SELECTED 搭配 WEAK 完全合法（本來就
    是「這檔在目前市場逆風下相對優勢不足以推薦」的正常結論）。"""
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [
                _not_selected(
                    "1000",
                    market_resilience="WEAK",
                    market_context_reason="逆風下資金參與不足，未列入今日推薦。",
                )
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
    )
    assert result["summary"]["not_selected_count"] == 1


def test_bull_healthy_recommend_with_weak_resilience_is_legal():
    """BULL_HEALTHY 不在守門清單裡：即使 market_resilience=WEAK，RECOMMEND
    仍合法（守門只鎖 BULL_STRESSED/VOLATILE_STRESSED/RISK_OFF 三種逆風狀態）。"""
    env = {**_stressed_env(), "effective_market_state": "BULL_HEALTHY"}
    payload = _payload(
        [_recommend("1000", 1, market_resilience="WEAK", market_context_reason="仍推薦。")]
    )
    result = global_selector.validate_global_selection(
        payload,
        _cards(1),
        selection_date=SELECTION_DATE,
        market_environment_provided=True,
        effective_market_state=env["effective_market_state"],
    )
    assert result["items"][0]["decision"] == "RECOMMEND"


def test_market_environment_provided_requires_resilience_on_every_item(monkeypatch):
    """market_environment 有送進來時，缺 market_resilience／market_context_reason
    的任何一項都應該被判定契約不合法（不是安靜放行 null）。"""
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload([_recommend("1000", 1)]), {"status": "ok"}  # 沒帶 resilience

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    with pytest.raises(global_selector.GlobalSelectionError) as exc:
        global_selector.run_global_selection(
            cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
        )
    assert exc.value.code == "GLOBAL_SELECTION_CONTRACT_INVALID"


def test_resilience_not_enforced_when_market_environment_not_provided(monkeypatch):
    """shadow/off 模式（不傳 market_environment）：完全不驗證這兩個新欄位，
    逐位元組維持這次改動之前的行為。"""
    cards = _cards(1)

    def fake_call(_system, user_msg, **kwargs):
        return _payload([_recommend("1000", 1)]), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(cards, {}, selection_date=SELECTION_DATE)
    assert result["summary"]["recommend_count"] == 1


def test_no_fixed_recommend_count_zero_recommend_under_stress_is_legal(monkeypatch):
    """§36「No Fixed Count」：壓力型市場下 0 檔 RECOMMEND 合法，不強制 Top-K。"""
    cards = _cards(3)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [
                _not_selected(
                    str(1000 + i),
                    market_resilience="WEAK",
                    market_context_reason="逆風下優勢不足。",
                )
                for i in range(3)
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
    )
    assert result["summary"]["recommend_count"] == 0
    assert result["summary"]["not_selected_count"] == 3


def test_no_fixed_recommend_count_all_recommend_under_stress_is_legal(monkeypatch):
    """§36「No Fixed Count」：全部候選都符合 resilience 契約時，全部 RECOMMEND
    也合法，不強制壓力型市場下的上限。"""
    cards = _cards(3)

    def fake_call(_system, user_msg, **kwargs):
        return _payload(
            [
                _recommend(
                    str(1000 + i),
                    i + 1,
                    market_resilience="STRONG",
                    market_context_reason="逆風下仍具明顯相對優勢。",
                )
                for i in range(3)
            ]
        ), {"status": "ok"}

    monkeypatch.setattr(global_selector.llm_caller, "_call_llm_json", fake_call)
    result = global_selector.run_global_selection(
        cards, {}, selection_date=SELECTION_DATE, market_environment=_stressed_env()
    )
    assert result["summary"]["recommend_count"] == 3
