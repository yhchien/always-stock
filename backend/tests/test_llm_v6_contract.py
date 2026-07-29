"""Phase 2 → LLM v6 Contract Alignment（2026-07-22）regression tests。

對應 spec §34~§41 的 code-level 可驗證部分（真實 LLM 行為需要實際 OpenAI 呼叫，
這裡驗證的是「即使 LLM 回應不理想，程式碼層的天花板/排序/欄位傳遞是否正確」）。
"""
import json
from typing import Any, Dict

import pytest

from app.signals import llm_caller
from app.signals import pipeline as pipeline_mod


@pytest.fixture(autouse=True)
def _legacy_v6_family(monkeypatch):
    """This module intentionally verifies the supported v6.1 rollback contract."""
    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "legacy_split")


def _patch_openai(monkeypatch, response_text: str):
    class _FakeResponsesAPI:
        def __init__(self, text: str):
            self._text = text
            self.calls: list = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _FakeResponse(self._text)

    class _FakeResponse:
        def __init__(self, text: str):
            self.output_text = text

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.responses = _FakeResponsesAPI(response_text)

    fake_client_holder: Dict[str, Any] = {}

    def _factory(**kwargs):
        client = _FakeClient(**kwargs)
        fake_client_holder["client"] = client
        return client

    monkeypatch.setattr(llm_caller, "OpenAI", _factory)
    monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: "fake-key")
    return fake_client_holder


def _candidate(stock_id, **overrides):
    base = {
        "stock_id": stock_id,
        "name": f"股票{stock_id}",
        "industry": "半導體業",
        "prelim_type": "LEADER",
        "deterministic_signals": {"max_decision": "WATCH", "risk_flags": []},
    }
    base.update(overrides)
    return base


# ---------------- §22/§34/§36：backend_max_decision 是天花板 ----------------


def test_backend_max_decision_remove_forces_final_remove_even_if_llm_says_watch(monkeypatch):
    """2912-type regression：backend 已判定 REMOVE（例如 REVERSAL_FAILURE），
    即使 LLM 誤判 WATCH，最終 decision 也必須是 REMOVE，不得因公司基本面好被救回。"""
    holder = _patch_openai(monkeypatch, json.dumps({
        "items": [{
            "stock": "2912", "decision": "WATCH",
            "short_reason": "基本面穩健，品牌強，防禦性佳",
        }]
    }))
    research = [_candidate("2912", deterministic_signals={"max_decision": "REMOVE", "risk_flags": []})]

    out = llm_caller.run_explanation_batch(research, market_context={"market_regime": "RISK_OFF"})

    assert len(out) == 1
    assert out[0]["decision"] == "REMOVE"
    assert out[0]["veto_reason"] == "BACKEND_MAX_REMOVE"
    assert out[0]["backend_max_decision"] == "REMOVE"


def test_backend_max_decision_watch_allows_llm_to_still_watch(monkeypatch):
    _patch_openai(monkeypatch, json.dumps({
        "items": [{
            "stock": "8039", "decision": "WATCH",
            "business_validation": "VERIFIED", "theme_validation": "VERIFIED",
            "supply_chain_validation": "VERIFIED",
            "short_reason": "FCCL 軟板材料，AI 伺服器題材受惠明確",
        }]
    }))
    research = [_candidate("8039", deterministic_signals={"max_decision": "WATCH", "risk_flags": []})]

    out = llm_caller.run_explanation_batch(research, market_context={"market_regime": "BULL_TREND"})

    assert out[0]["decision"] == "WATCH"
    assert out[0]["veto_reason"] is None


def test_llm_can_veto_watch_via_business_mismatch(monkeypatch):
    """LLM 發現外部矛盾（業務不符）時，即使 backend_max_decision=WATCH 仍可 REMOVE。"""
    _patch_openai(monkeypatch, json.dumps({
        "items": [{
            "stock": "6243", "decision": "REMOVE",
            "veto_reason": "BUSINESS_MISMATCH",
            "short_reason": "查證後公司主要業務與系統認定產業明顯不符",
        }]
    }))
    research = [_candidate("6243", deterministic_signals={"max_decision": "WATCH", "risk_flags": []})]

    out = llm_caller.run_explanation_batch(research, market_context={"market_regime": "VOLATILE_RANGE"})

    assert out[0]["decision"] == "REMOVE"
    assert out[0]["veto_reason"] == "BUSINESS_MISMATCH"


# ---------------- §19/§21：驗證三態不可被當 REMOVE 理由 ----------------


def test_validation_fields_normalize_unknown_to_unconfirmed_not_mismatch():
    """UNCONFIRMED != MISMATCH：未知/缺值必須落在 UNCONFIRMED，不可被誤判成 MISMATCH。"""
    assert llm_caller._normalize_validation(None) == "UNCONFIRMED"
    assert llm_caller._normalize_validation("") == "UNCONFIRMED"
    assert llm_caller._normalize_validation("garbage") == "UNCONFIRMED"
    assert llm_caller._normalize_validation("verified") == "VERIFIED"
    assert llm_caller._normalize_validation("MISMATCH") == "MISMATCH"


# ---------------- §29-31：LLM_INPUT_HARD_LIMIT 排序不可用 display_type 灌水 ----------------


def test_phase2_llm_priority_ranks_emerging_momentum_by_numeric_evidence_not_role_bucket():
    """INDEPENDENT_LEADER / EMERGING_MOMENTUM regression：即使 EMERGING_MOMENTUM
    會被映射成 display_type=FOLLOWER，只要它的 momentum_score/conviction 比一個
    formal LEADER 更強，排序時仍應該排在前面——不可因為角色桶被系統性往後排。
    """
    strong_emerging = {
        "stock_id": "A", "role": "EMERGING_MOMENTUM", "tracking_state": None,
        "conviction": "high", "momentum_score": 92.0, "rs_market_percentile_20d": 98.0,
        "risk_warnings": [],
    }
    weak_leader = {
        "stock_id": "B", "role": "SECTOR_LEADER", "tracking_state": None,
        "conviction": "low", "momentum_score": 55.0, "rs_market_percentile_20d": 60.0,
        "risk_warnings": ["EXTENDED_3D", "LOW_RAW_VOLUME"],
    }
    ordered = sorted([weak_leader, strong_emerging], key=pipeline_mod._llm_input_sort_key)
    assert [c["stock_id"] for c in ordered] == ["A", "B"]


def test_phase2_llm_priority_does_not_treat_unclassified_as_automatic_low_quality():
    """§31：不可因為 role=UNCLASSIFIED_MOMENTUM 就自動判定為低品質；
    numeric evidence 才是主排序依據。"""
    strong_unclassified = {
        "stock_id": "A", "role": "UNCLASSIFIED_MOMENTUM", "tracking_state": None,
        "conviction": "high", "momentum_score": 88.0, "rs_market_percentile_20d": 95.0,
        "risk_warnings": [],
    }
    weak_follower = {
        "stock_id": "B", "role": "SECTOR_FOLLOWER", "tracking_state": None,
        "conviction": "low", "momentum_score": 50.0, "rs_market_percentile_20d": 45.0,
        "risk_warnings": ["EXTENDED_3D"],
    }
    ordered = sorted([weak_follower, strong_unclassified], key=pipeline_mod._llm_input_sort_key)
    assert [c["stock_id"] for c in ordered] == ["A", "B"]


def test_legacy_candidates_without_phase2_fields_use_unchanged_prelim_type_sort():
    """legacy 候選（沒有 role/tracking_state 欄位）排序邏輯完全不變。"""
    leader = {"stock_id": "A", "prelim_type": "LEADER", "total_institution_flow_3d": 10.0}
    laggard = {"stock_id": "B", "prelim_type": "ROTATION_LAGGARD", "total_institution_flow_3d": 999.0}
    ordered = sorted([laggard, leader], key=pipeline_mod._llm_input_sort_key)
    assert [c["stock_id"] for c in ordered] == ["A", "B"]  # LEADER 優先於 ROTATION_LAGGARD，即使法人金額較低


def test_tracked_stock_with_no_role_still_uses_phase2_priority_key():
    """已追蹤股 role=None 但 tracking_state 有值，仍應走 phase2 priority（不是 legacy）。"""
    tracked = {
        "stock_id": "A", "role": None, "tracking_state": "HEALTHY_PULLBACK",
        "conviction": "medium", "momentum_score": 70.0, "rs_market_percentile_20d": 80.0,
        "risk_warnings": [],
    }
    key = pipeline_mod._llm_input_sort_key(tracked)
    assert key == pipeline_mod._phase2_llm_priority_key(tracked)


# ---------------- §四十/§四十一：ETF / 金融股 candidate 資料流 ----------------


def test_evidence_view_carries_asset_type_and_backend_max_decision():
    stocks = [
        _candidate(
            "0050",
            asset_type="ETF",
            deterministic_signals={"max_decision": "WATCH", "risk_flags": []},
        ),
        _candidate(
            "2880",
            asset_type="FINANCIAL",
            deterministic_signals={"max_decision": "REMOVE", "risk_flags": ["distribution"]},
        ),
    ]
    view = llm_caller._to_evidence_view(stocks)
    by_id = {v["stock"]: v for v in view}
    assert by_id["0050"]["asset_type"] == "ETF"
    assert by_id["0050"]["backend_max_decision"] == "WATCH"
    assert by_id["2880"]["asset_type"] == "FINANCIAL"
    assert by_id["2880"]["backend_max_decision"] == "REMOVE"


def test_evidence_view_defaults_asset_type_to_common_stock_when_missing():
    """legacy 候選沒有 asset_type 欄位，投影時保守回 COMMON_STOCK。"""
    view = llm_caller._to_evidence_view([_candidate("2330")])
    assert view[0]["asset_type"] == "COMMON_STOCK"


# ---------------- STEP 8.5（margin_analysis）等既有功能不受影響 ----------------


def test_v6_default_prompt_version_used_when_no_force_env(monkeypatch):
    monkeypatch.delenv("SIGNALS_FORCE_PROMPT_VERSION", raising=False)
    assert llm_caller._resolve_prompt_version("BULL_TREND") == "v6.1"
    assert llm_caller._resolve_prompt_version(None) == "v6.1"


def test_all_executable_prompts_enforce_asset_parity():
    for path in set(llm_caller._PROMPT_PATHS.values()):
        text = path.read_text(encoding="utf-8")
        assert "必須排除 ETF、金融股" not in text
        assert "不要把金融股或 ETF 放進 watchlist" not in text
        assert "商品類型不得作為排除理由" in text or "具有相同選股地位" in text


def test_v6_decision_prompt_contains_p0_selection_guardrails():
    llm_caller._PROMPT_FRAGMENT_CACHE.clear()
    prompt = llm_caller._load_system_prompt(stage="decision", version="v6")
    assert "固定 WATCH 名額" in prompt
    assert "Candidate Source" in prompt
    assert "Momentum Rank 只能影響排序" in prompt
    assert "Persistence" in prompt
    assert "WEAK、SHADOW_ONLY、INCOMPLETE" in prompt
    assert "REJECT 證據完全不得參與正式決策" in prompt


def test_force_prompt_version_v5_still_works_for_replay(monkeypatch):
    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v5")
    assert llm_caller._resolve_prompt_version("BULL_TREND") == "v5"
