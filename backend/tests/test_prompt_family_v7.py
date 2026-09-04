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
    # 2026-09-04 M27 Market Regime v2 Production Integration：P4 state machine
    # 正式讀 Market Environment，bump 到獨立版本（見 §21/§35）。
    assert versions["tracking_state_machine_version"] == "p4_state_v2_market_context"
    assert versions["response_contract_versions"] == {
        "research": "v7_research_json_schema_v1",
        "assessment": "v7_assessment_json_schema_v1",
        "global_selector": "v7_global_selector_json_schema_v2",
        "reason": "v7_reason_json_schema_v1",
        "tracking": "v7_tracking_json_schema_v1",
    }

    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "legacy_split")
    legacy = prompt_family.prompt_metadata()
    assert legacy["research_prompt_version"] == "v6.1"
    assert legacy["assessment_prompt_version"] == "p3_assessment_v1"
    assert legacy["global_selector_version"] == "p3_global_v1"
    assert legacy["reason_prompt_version"] == "p3_reason_v1"
    assert legacy["tracking_prompt_version"] == "p4_tracking_v1"
    assert legacy["response_contract_versions"] == {}


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


def test_research_validator_drops_unparseable_dates_instead_of_failing_the_item():
    """Live probing against gpt-5.4-mini surfaced sources with a placeholder
    day (e.g. "2026-02-??") when only the publish month is known.  That is a
    formatting gap, not a future-info leak, so the offending evidence entry
    must be dropped instead of nuking otherwise-VERIFIED research via
    PromptFamilyError."""
    item = _research_item()
    item["sources"].append({
        "title": "年報（僅知月份，無確切日期）",
        "url": "https://example.com/annual-report",
        "published_date": "2026-02-??",
        "source_type": "COMPANY",
    })
    item["material_contradictions"] = [{
        "type": "DATA_CONTRADICTION",
        "summary": "資料衝突示例，發布日期不完整。",
        "url": "https://example.com/bad-date-evidence",
        "published_date": "2026-05-??",
    }]
    validated = prompt_family.validate_research_output(
        {"date": STAGE_DATE, "items": [item]},
        expected_stocks=["2330"],
        expected_date=STAGE_DATE,
    )
    kept_dates = [s["published_date"] for s in validated[0]["sources"]]
    assert "2026-02-??" not in kept_dates
    assert "2026-07-20" in kept_dates
    assert validated[0]["material_contradictions"] == []

    # A parseable-but-future date must still hard-fail (real leak, not a
    # formatting gap) -- unchanged from the existing cutoff enforcement.
    future = _research_item()
    future["sources"][0]["published_date"] = "2026-07-30"
    with pytest.raises(prompt_family.PromptFamilyError, match="after"):
        prompt_family.validate_research_output(
            {"date": STAGE_DATE, "items": [future]},
            expected_stocks=["2330"],
            expected_date=STAGE_DATE,
        )


def test_assessment_validator_drops_unparseable_veto_evidence_dates():
    item = {
        "stock": "2330",
        "assessment": "REMOVE",
        "veto_reason": "MATERIAL_NEGATIVE_EVENT",
        "assessment_reason": "重大負面事件證據不足以精確標註日期。",
        "quality_assessment": {
            "momentum_quality": "LOW",
            "participation_quality": "LOW",
            "catalyst_quality": "UNCONFIRMED",
            "evidence_coherence": "WEAK",
        },
        "veto_evidence": {
            "summary": "負面事件摘要",
            "urls": ["https://example.com/negative-event"],
            "published_dates": ["2026-02-??", "2026-07-16"],
        },
    }
    validated = prompt_family.validate_assessment_output(
        {"date": STAGE_DATE, "items": [item]},
        expected_stocks=["2330"],
        expected_date=STAGE_DATE,
    )
    assert validated[0]["veto_evidence"]["published_dates"] == ["2026-07-16"]


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


def test_assessment_reason_and_tracking_schemas_are_strict():
    schemas = [
        prompt_family.assessment_output_schema(
            expected_stocks=["2330"], expected_date=STAGE_DATE
        ),
        prompt_family.reason_output_schema(
            expected_stocks=["2330"], expected_date=STAGE_DATE
        ),
        prompt_family.tracking_output_schema(
            expected_stocks=["2330"], review_date=STAGE_DATE
        ),
    ]
    assert all(schema["additionalProperties"] is False for schema in schemas)
    assert all(
        schema["properties"]["items"]["items"]["properties"]["stock"]["enum"]
        == ["2330"]
        for schema in schemas
    )
    assessment_veto = schemas[0]["properties"]["items"]["items"]["properties"][
        "veto_reason"
    ]["enum"]
    assert "DATA_CONTRADICTION" in assessment_veto
    tracking_reason = schemas[2]["properties"]["items"]["items"]["properties"][
        "invalidation_reason_code"
    ]["enum"]
    assert "MATERIAL_NEGATIVE_EVENT" in tracking_reason


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


def test_v7_research_uses_strict_structured_output_schema(monkeypatch):
    captured = {}

    def fake_call(*args, **kwargs):
        captured.update(kwargs)
        return {
            "date": STAGE_DATE,
            "items": [_research_item()],
        }, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    output = llm_caller.run_research_batch(
        [{"stock_id": "2330", "name": "台積電", "prelim_type": "LEADER"}],
        {"target_date": STAGE_DATE},
    )
    assert output[0]["stock"] == "2330"
    assert captured["response_format_name"] == "fishtail_v7_research"
    schema = captured["response_schema"]
    assert schema["additionalProperties"] is False
    stock_schema = schema["properties"]["items"]["items"]["properties"]["stock"]
    assert stock_schema["enum"] == ["2330"]


def test_v7_research_contract_failure_binary_retries(monkeypatch):
    candidate_counts = []
    retry_payloads = []

    def fake_call(_system, user_msg, **kwargs):
        payload = json.loads(user_msg)
        stocks = [item["stock"] for item in payload["items"]]
        candidate_counts.append(len(stocks))
        retry_payloads.append(payload.get("contract_retry"))
        assert kwargs["response_schema"]
        if len(stocks) > 1:
            return None, {
                "status": llm_caller._DIAG_STATUS_INVALID_JSON,
                "message": "malformed batch",
            }
        return {
            "date": STAGE_DATE,
            "items": [_research_item(stocks[0])],
        }, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    output = llm_caller.run_research_batch(
        [
            {"stock_id": "2330", "name": "台積電", "prelim_type": "LEADER"},
            {"stock_id": "2454", "name": "聯發科", "prelim_type": "FOLLOWER"},
        ],
        {"target_date": STAGE_DATE},
    )
    assert candidate_counts == [2, 1, 1]
    assert retry_payloads[0] is None
    assert all(
        payload["previous_rejection"] == "malformed batch"
        for payload in retry_payloads[1:]
    )
    assert all("不可使用未來資訊" in payload["required_correction"] for payload in retry_payloads[1:])
    assert [item["stock"] for item in output] == ["2330", "2454"]
    assert all(not item.get("_unavailable") for item in output)
    assert all(
        item["llm_diagnostic"]["contract_retry_depth"] == 1
        for item in output
    )


def test_v7_research_split_singleton_gets_its_own_correction_retry(monkeypatch):
    calls = []

    def fake_call(_system, user_msg, **kwargs):
        payload = json.loads(user_msg)
        stocks = [item["stock"] for item in payload["items"]]
        calls.append((stocks, payload.get("contract_retry")))
        if len(stocks) > 1 or sum(ids == ["2330"] for ids, _ in calls) == 1:
            return None, {
                "status": llm_caller._DIAG_STATUS_INVALID_JSON,
                "message": f"contract failed for {','.join(stocks)}",
            }
        return {
            "date": STAGE_DATE,
            "items": [_research_item(stocks[0])],
        }, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    output = llm_caller.run_research_batch(
        [
            {"stock_id": "2330", "name": "台積電", "prelim_type": "LEADER"},
            {"stock_id": "2454", "name": "聯發科", "prelim_type": "FOLLOWER"},
        ],
        {"target_date": STAGE_DATE},
    )

    assert [ids for ids, _ in calls] == [
        ["2330", "2454"],
        ["2330"],
        ["2330"],
        ["2454"],
    ]
    assert "contract failed for 2330" in calls[2][1]["previous_rejection"]
    assert all(not item.get("_unavailable") for item in output)


def test_v7_research_future_evidence_becomes_conservative_unconfirmed(monkeypatch):
    calls = []
    future_item = _research_item("2330")
    future_item["sources"][0]["published_date"] = "2026-07-30"
    future_item["instrument_summary"] = "這裡包含分析日之後才知道的未來內容。"
    future_item["research_summary"] = "未來事件已經發生。"

    def fake_call(_system, user_msg, **kwargs):
        calls.append(json.loads(user_msg))
        return {
            "date": STAGE_DATE,
            "items": [future_item],
        }, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    output = llm_caller.run_research_batch(
        [{
            "stock_id": "2330",
            "name": "台積電",
            "prelim_type": "LEADER",
            "asset_type": "COMMON_STOCK",
            "theme_cluster": "人工智慧",
        }],
        {"target_date": STAGE_DATE},
    )

    assert len(calls) == 2
    assert output[0]["instrument_validation"] == "UNCONFIRMED"
    assert output[0]["theme_validation"] == "UNCONFIRMED"
    assert output[0]["supply_chain_validation"] == "UNCONFIRMED"
    assert output[0]["research_confidence"] == "LOW"
    assert output[0]["sources"] == []
    assert output[0]["material_contradictions"] == []
    assert "未來事件已經發生" not in json.dumps(output[0], ensure_ascii=False)
    assert output[0]["llm_diagnostic"]["cutoff_sanitized"] is True
    assert output[0]["llm_diagnostic"]["cutoff_sanitized_stocks"] == ["2330"]
    assert not output[0].get("_unavailable")


def test_research_batch_size_routes_by_prompt_family_independently(monkeypatch):
    """2026-08-12（成本控制）：v7／legacy 的 research batch size 常數各自獨立
    設定（`DEFAULT_V7_RESEARCH_BATCH_SIZE` / `DEFAULT_RESEARCH_BATCH_SIZE`），
    改一個不會連動另一個——這裡驗證 family routing 本身仍正確，不斷言兩者的
    絕對值關係（4→8 那次調整已經讓兩者相等，但 routing 邏輯才是這個測試真正要
    保護的行為）。"""
    assert llm_caller.current_research_batch_size() == llm_caller.DEFAULT_V7_RESEARCH_BATCH_SIZE
    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "legacy_split")
    assert llm_caller.current_research_batch_size() == llm_caller.DEFAULT_RESEARCH_BATCH_SIZE

    monkeypatch.setattr(llm_caller, "DEFAULT_RESEARCH_BATCH_SIZE", 3)
    assert llm_caller.current_research_batch_size() == 3


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
        "selection_version": prompt_family.stage_version("global_selector"),
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

    captured = {}

    def fake_call(*args, **kwargs):
        captured.update(kwargs)
        return response, {"status": "ok"}

    monkeypatch.setattr(llm_caller, "_call_llm_json", fake_call)
    selected = global_selector.run_global_selection(
        cards, {}, selection_date=STAGE_DATE
    )
    assert selected["selection_version"] == prompt_family.stage_version("global_selector")
    assert selected["summary"]["recommend_count"] == 0
    assert captured["response_format_name"] == "fishtail_v7_global_selector"
    basis_schema = (
        captured["response_schema"]["properties"]["items"]["items"]["properties"][
            "recommendation_basis"
        ]["items"]
    )
    assert set(basis_schema["enum"]) == global_selector.RECOMMENDATION_BASIS_CODES
