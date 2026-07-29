from __future__ import annotations

from datetime import date
import json
import time
import tracemalloc

import pytest

from app.signals import global_selector, llm_caller, prompt_family


STAGE_DATE = "2026-07-29"


@pytest.fixture(autouse=True)
def _v7(monkeypatch):
    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "v7")
    monkeypatch.delenv("SIGNALS_FORCE_PROMPT_VERSION", raising=False)


def _research_item(stock: str = "2330"):
    return {
        "stock": stock,
        "instrument_validation": "VERIFIED",
        "theme_validation": "VERIFIED",
        "supply_chain_validation": "VERIFIED",
        "instrument_summary": "公司核心業務與輸入標示的產業一致。",
        "theme": {
            "name": "人工智慧伺服器",
            "duration": "2Q_plus",
            "maturity": "mid",
            "catalyst_status": "ACTIVE",
            "catalyst_summary": "主要客戶資本支出仍支持題材延續。",
        },
        "supply_chain_role": "晶圓代工",
        "group_name": "",
        "theme_cluster": "人工智慧",
        "material_contradictions": [],
        "sources": [{
            "title": "公司業務說明",
            "url": "https://example.com/company",
            "published_date": "2026-07-20",
            "source_type": "COMPANY",
        }],
        "research_confidence": "HIGH",
        "research_summary": "外部資料支持業務與題材關聯，未發現重大矛盾。",
    }


def test_default_and_legacy_routing_are_explicit(monkeypatch):
    assert prompt_family.resolve_prompt_family() == "v7"
    versions = prompt_family.prompt_metadata()
    assert versions["research_prompt_version"] == "v7_research"
    assert versions["tracking_state_machine_version"] == "p4_state_v1"

    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "legacy_split")
    legacy = prompt_family.prompt_metadata()
    assert legacy["research_prompt_version"] == "v6.1"
    assert legacy["assessment_prompt_version"] == "p3_assessment_v1"
    assert legacy["global_selector_version"] == "p3_global_v1"
    assert legacy["reason_prompt_version"] == "p3_reason_v1"
    assert legacy["tracking_prompt_version"] == "p4_tracking_v1"


def test_unknown_family_fails_closed(monkeypatch):
    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "v1")
    with pytest.raises(prompt_family.PromptFamilyError, match="Unknown"):
        prompt_family.resolve_prompt_family()


@pytest.mark.parametrize(
    "stage,marker",
    [
        ("research", "Candidate Research v7"),
        ("assessment", "Candidate Assessment v7"),
        ("global_selector", "Global Recommendation Selector v7"),
        ("reason", "Recommendation Reason v7"),
        ("tracking", "Tracking Review v7"),
    ],
)
def test_v7_composition_includes_shared_policy_once(stage, marker):
    assembled = prompt_family.build_stage_prompt(stage)
    assert assembled.count("# 魚尾 Prompt v7 共用政策") == 1
    assert marker in assembled
    assert "Prompt Injection（提示詞注入）" in assembled
    assert "BUY／SELL" in assembled


def test_prompt_sha_is_stable_and_stage_specific():
    first = prompt_family.prompt_metadata()["prompt_sha256"]
    second = prompt_family.prompt_metadata()["prompt_sha256"]
    assert first == second
    assert len(set(first.values())) == 5
    assert all(len(value) == 64 for value in first.values())


def test_sha_changes_for_shared_and_only_the_related_stage(monkeypatch):
    original = prompt_family.prompt_metadata()["prompt_sha256"]
    reader = prompt_family._read_prompt_file
    with monkeypatch.context() as scoped:
        scoped.setattr(
            prompt_family,
            "_read_prompt_file",
            lambda filename: (
                reader(filename) + "\nshared policy test change"
                if filename == "shared-policy-v7.md"
                else reader(filename)
            ),
        )
        prompt_family._build_v7_prompt.cache_clear()
        shared_changed = prompt_family.prompt_metadata()["prompt_sha256"]
        assert all(shared_changed[stage] != original[stage] for stage in original)
    prompt_family._build_v7_prompt.cache_clear()

    reader = prompt_family._read_prompt_file
    with monkeypatch.context() as scoped:
        scoped.setattr(
            prompt_family,
            "_read_prompt_file",
            lambda filename: (
                reader(filename) + "\nresearch stage test change"
                if filename == "candidate-research-v7.md"
                else reader(filename)
            ),
        )
        prompt_family._build_v7_prompt.cache_clear()
        stage_changed = prompt_family.prompt_metadata()["prompt_sha256"]
        assert stage_changed["research"] != original["research"]
        assert all(
            stage_changed[stage] == original[stage]
            for stage in original
            if stage != "research"
        )
    prompt_family._build_v7_prompt.cache_clear()


def test_research_allowlist_drops_debug_and_outcome_fields():
    row = {
        "stock_id": "2330",
        "name": "台積電",
        "asset_type": "COMMON_STOCK",
        "industry": "半導體",
        "role": "EMERGING_MOMENTUM",
        "momentum_score": 99,
        "full_tracking_history": [{"future_return": 100}],
        "margin_analysis": {"huge": "payload"},
    }
    projected = prompt_family.research_input(
        [row], research_date=STAGE_DATE, market_context={"market_regime": "BULL_TREND"}
    )
    text = json.dumps(projected)
    assert projected["items"][0]["stock"] == "2330"
    assert "momentum_score" not in text
    assert "future_return" not in text
    assert "margin_analysis" not in text


def test_assessment_and_reason_inputs_use_stage_allowlists():
    row = {
        **_research_item(),
        "backend_max_decision": "WATCH",
        "momentum_freshness": "FRESH",
        "quality_evidence": {"price": True},
        "recommendation_thesis": "正向論點",
        "relative_advantage": "同日相對優勢",
        "margin_analysis": {"balance": 1},
        "full_snapshot": {"secret": "not sent"},
    }
    assessment = json.dumps(
        prompt_family.assessment_input([row], assessment_date=STAGE_DATE)
    )
    reason = json.dumps(prompt_family.reason_input([row], reason_date=STAGE_DATE))
    assert "full_snapshot" not in assessment
    assert "relative_advantage" not in assessment
    assert "full_snapshot" not in reason
    assert "relative_advantage" in reason


def test_research_validator_enforces_alignment_enum_url_and_date():
    valid = {"date": STAGE_DATE, "items": [_research_item()]}
    assert prompt_family.validate_research_output(
        valid, expected_stocks=["2330"], expected_date=STAGE_DATE
    )[0]["stock"] == "2330"

    duplicate = {"date": STAGE_DATE, "items": [_research_item(), _research_item()]}
    with pytest.raises(prompt_family.PromptFamilyError):
        prompt_family.validate_research_output(
            duplicate, expected_stocks=["2330"], expected_date=STAGE_DATE
        )
    future = _research_item()
    future["sources"][0]["published_date"] = "2026-07-30"
    with pytest.raises(prompt_family.PromptFamilyError, match="after"):
        prompt_family.validate_research_output(
            {"date": STAGE_DATE, "items": [future]},
            expected_stocks=["2330"],
            expected_date=STAGE_DATE,
        )
    english_only = _research_item()
    english_only["research_summary"] = "English only output"
    with pytest.raises(prompt_family.PromptFamilyError, match="Traditional Chinese"):
        prompt_family.validate_research_output(
            {"date": STAGE_DATE, "items": [english_only]},
            expected_stocks=["2330"],
            expected_date=STAGE_DATE,
        )


def test_assessment_validator_has_strict_two_state_contract():
    item = {
        "stock": "2330",
        "assessment": "ELIGIBLE_FOR_GLOBAL_SELECTION",
        "veto_reason": None,
        "assessment_reason": "證據可進入全體候選比較。",
        "quality_assessment": {
            "momentum_quality": "HIGH",
            "participation_quality": "MEDIUM",
            "catalyst_quality": "HIGH",
            "evidence_coherence": "STRONG",
        },
        "veto_evidence": {"summary": None, "urls": [], "published_dates": []},
    }
    assert prompt_family.validate_assessment_output(
        {"date": STAGE_DATE, "items": [item]},
        expected_stocks=["2330"],
        expected_date=STAGE_DATE,
    )
    item["assessment"] = "RECOMMEND"
    with pytest.raises(prompt_family.PromptFamilyError):
        prompt_family.validate_assessment_output(
            {"date": STAGE_DATE, "items": [item]},
            expected_stocks=["2330"],
            expected_date=STAGE_DATE,
        )


def test_reason_validator_rejects_empty_or_cross_contract_output():
    item = {
        "stock": "2330",
        "theme_reason": ["實際業務符合題材定位且催化劑仍延續。"] * 2,
        "capital_reason": ["今日相對優勢與後端排序互相呼應。"] * 2,
        "chip_reason": ["法人買盤與成交量共同顯示資金參與。"] * 2,
        "margin_reason": ["融資融券資料完整，尚未出現明顯過熱。"],
        "technical_reason": ["價格結構仍完整，但需留意短線追高風險。"] * 2,
        "momentum_reason": ["相對大盤與同業動能維持領先。"] * 2,
        "margin_analysis": {},
    }
    assert prompt_family.validate_reason_output(
        {"date": STAGE_DATE, "items": [item]},
        expected_stocks=["2330"],
        expected_date=STAGE_DATE,
    )
    item["theme_reason"] = []
    with pytest.raises(prompt_family.PromptFamilyError):
        prompt_family.validate_reason_output(
            {"date": STAGE_DATE, "items": [item]},
            expected_stocks=["2330"],
            expected_date=STAGE_DATE,
        )


def test_v7_research_caller_adapts_contract_without_changing_backend_type(monkeypatch):
    response = {"date": STAGE_DATE, "items": [_research_item()]}

    def fake_call(*args, **kwargs):
        return response, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    output = llm_caller.run_research_batch(
        [{
            "stock_id": "2330",
            "name": "台積電",
            "prelim_type": "FOLLOWER",
            "asset_type": "COMMON_STOCK",
        }],
        {"target_date": STAGE_DATE},
    )
    assert output[0]["type"] == "FOLLOWER"
    assert output[0]["business_validation"] == "VERIFIED"
    assert output[0]["research_confidence"] == "HIGH"


def test_v7_payload_scale_is_linear_and_bounded():
    results = []
    for count in (25, 50, 100, 200):
        rows = [
            {
                "stock_id": str(1000 + index),
                "name": f"股票{index}",
                "asset_type": "COMMON_STOCK",
                "industry": "半導體",
                "role": "EMERGING_MOMENTUM",
                "theme_cluster": "人工智慧",
                "momentum_debug": {"series": list(range(500))},
                "tracking_history": [{"future": "禁止"}] * 20,
            }
            for index in range(count)
        ]
        started = time.perf_counter()
        tracemalloc.start()
        payload = prompt_family.research_input(
            rows, research_date=STAGE_DATE, market_context={}
        )
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append((count, len(encoded), time.perf_counter() - started, peak))
    assert all(duration < 2 for _, _, duration, _ in results)
    assert all(peak < 32 * 1024 * 1024 for _, _, _, peak in results)
    assert results[-1][1] < results[0][1] * 9
    full = json.dumps(rows, ensure_ascii=False).encode()
    assert results[-1][1] < len(full) / 5


def test_global_selector_uses_v7_version_and_keeps_zero_to_all_contract(monkeypatch):
    cards = global_selector.build_compact_selection_cards(
        [{
            "stock": "2330",
            "name": "台積電",
            "asset_type": "COMMON_STOCK",
            "momentum_score": 90,
            "business_validation": "VERIFIED",
            "theme_validation": "VERIFIED",
            "supply_chain_validation": "VERIFIED",
            "theme_cluster": "人工智慧",
        }],
        selection_date=date.fromisoformat(STAGE_DATE),
    )
    response = {
        "selection_version": "v7_global_selector",
        "date": STAGE_DATE,
        "selection_complete": True,
        "items": [{
            "stock": "2330",
            "decision": "NOT_SELECTED",
            "recommendation_rank": None,
            "selection_reason_code": "NO_DISTINCT_DAILY_EDGE",
            "selection_reason": "候選仍有效，但今日沒有足夠鮮明的相對優勢。",
            "recommendation_thesis": None,
            "relative_advantage": None,
            "theme_cluster": "人工智慧",
            "distinct_thesis": False,
            "overlap_with": [],
            "overlap_reason": None,
            "rank_override": False,
            "rank_override_reason": None,
            "recommendation_basis": [],
        }],
        "summary": {
            "eligible_count": 1,
            "recommend_count": 0,
            "not_selected_count": 1,
            "selection_rationale": "完整比較後，本日可為零檔正式推薦。",
        },
    }

    monkeypatch.setattr(
        llm_caller,
        "_call_llm_json",
        lambda *args, **kwargs: (response, {"status": "ok"}),
    )
    selected = global_selector.run_global_selection(
        cards, {}, selection_date=STAGE_DATE
    )
    assert selected["selection_version"] == "v7_global_selector"
    assert selected["summary"]["recommend_count"] == 0
