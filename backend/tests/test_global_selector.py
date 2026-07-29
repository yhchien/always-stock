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


def _recommend(stock: str, rank: int, *, override: bool = False):
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
    }


def _not_selected(stock: str, code: str = "NO_DISTINCT_DAILY_EDGE"):
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
