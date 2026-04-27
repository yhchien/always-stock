"""M23 slice 6：llm_caller.py 4 個 public function + _extract_json 測試。

OpenAI client 全 mock：
  - `monkeypatch.setattr(llm_caller, "OpenAI", FakeOpenAIFactory(payload))`
  - `monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: "fake-key")`

不打真實網路，不依賴外部金鑰；確保 pipeline 在 LLM 不可用 / 回非 JSON / 缺欄位
時都能走 fallback 不爆。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.signals import llm_caller


# ---------- helpers ----------


class _FakeResponsesResponse:
    def __init__(self, content: str) -> None:
        self.output_text = content
        self.output = []

class _FakeResponsesAPI:
    def __init__(self, content: str, *, raise_exc: Optional[Exception] = None) -> None:
        self._content = content
        self._raise_exc = raise_exc
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponsesResponse:
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponsesResponse(self._content)


class _FakeResponses:
    def __init__(self, responses_api: _FakeResponsesAPI) -> None:
        self.create = responses_api.create
        self._api = responses_api


class _FakeOpenAIClient:
    def __init__(self, content: str, *, raise_exc: Optional[Exception] = None) -> None:
        self._responses_api = _FakeResponsesAPI(content, raise_exc=raise_exc)
        self.responses = _FakeResponses(self._responses_api)


def _patch_openai(
    monkeypatch: pytest.MonkeyPatch,
    response_content: str,
    *,
    api_key: Optional[str] = "fake-key",
    raise_exc: Optional[Exception] = None,
) -> _FakeOpenAIClient:
    """把 llm_caller.OpenAI 換成 fake；回傳已被綁定的 client（用來檢查 calls）。"""
    fake_client = _FakeOpenAIClient(response_content, raise_exc=raise_exc)
    fake_client.factory_kwargs = {}

    def _factory(**_kwargs: Any) -> _FakeOpenAIClient:
        fake_client.factory_kwargs = _kwargs
        return fake_client

    monkeypatch.setattr(llm_caller, "OpenAI", _factory)
    monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: api_key)
    # 確保 prompt 載入不會因檔案缺失炸掉
    monkeypatch.setattr(llm_caller, "_load_system_prompt", lambda: "FAKE_SYSTEM_PROMPT")
    return fake_client


# ---------- _extract_json ----------


def test_extract_json_handles_plain_json():
    out = llm_caller._extract_json('{"a": 1, "b": "x"}')
    assert out == {"a": 1, "b": "x"}


def test_extract_json_strips_markdown_fence_with_lang():
    raw = '```json\n{"a": 1}\n```'
    assert llm_caller._extract_json(raw) == {"a": 1}


def test_extract_json_strips_markdown_fence_without_lang():
    raw = '```\n{"a": 2}\n```'
    assert llm_caller._extract_json(raw) == {"a": 2}


def test_extract_json_returns_none_for_garbage():
    assert llm_caller._extract_json("totally not json") is None


def test_extract_json_handles_empty_string():
    assert llm_caller._extract_json("") is None


def test_extract_json_handles_none_like_input():
    # 防禦性：呼叫端有時會傳 ""；只要不爆就行
    assert llm_caller._extract_json("   ") is None


# ---------- assemble_market_context ----------


def test_assemble_market_context_parses_json_happy_path(monkeypatch):
    payload = {
        "market_state": "STRUCTURAL_BULL",
        "vix_status": "risk_on",
        "futures_bias": "LONG",
        "market_state_reason": "VIX 低、台指期偏多",
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(payload))
    out = llm_caller.assemble_market_context(
        {
            "taiex": {"change_pct_1d": 1.2},
            "otc": {"change_pct_1d": 1.8},
        }
    )
    assert out["market_state"] == "STRUCTURAL_BULL"
    assert out["taiex_change_pct"] == 1.2
    assert out["otc_change_pct"] == 1.8
    assert out["vix_status"] == "risk_on"
    assert out["futures_bias"] == "LONG"
    assert "VIX" in out["market_state_reason"]
    assert fake_client._responses_api.calls[0]["tools"] == [{"type": "web_search"}]
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_MARKET
    assert fake_client.factory_kwargs["timeout"] == llm_caller._OPENAI_TIMEOUT_SECONDS
    assert fake_client.factory_kwargs["max_retries"] == llm_caller._OPENAI_MAX_RETRIES


def test_assemble_market_context_keeps_backend_index_values_even_if_llm_mentions_zeros(monkeypatch):
    payload = {
        "market_state": "RANGE",
        "taiex_change_pct": 0,
        "otc_change_pct": 0,
        "vix_status": "neutral",
        "futures_bias": "NEUTRAL",
        "market_state_reason": "外部資料不足，但 backend 數字仍可用。",
    }
    _patch_openai(monkeypatch, json.dumps(payload))
    out = llm_caller.assemble_market_context(
        {
            "taiex": {"change_pct_1d": 3.23},
            "otc": None,
        }
    )
    assert out["taiex_change_pct"] == 3.23
    assert out["otc_change_pct"] is None


def test_assemble_market_context_strips_code_fence(monkeypatch):
    raw = '```json\n{"market_state": "RANGE", "vix_status": "neutral"}\n```'
    _patch_openai(monkeypatch, raw)
    out = llm_caller.assemble_market_context({"taiex": {"change_pct_1d": 0.1}})
    assert out["market_state"] == "RANGE"
    assert out["taiex_change_pct"] == 0.1


def test_assemble_market_context_fallback_when_api_key_missing(monkeypatch):
    _patch_openai(monkeypatch, '{"market_state": "STRONG_BULL"}', api_key=None)
    out = llm_caller.assemble_market_context({"taiex": {"change_pct_1d": 1.5}})
    # 沒 key → fallback 為 RANGE
    assert out["market_state"] == "RANGE"
    assert out["taiex_change_pct"] == 1.5
    assert "OpenAI" in out["market_state_reason"]


def test_assemble_market_context_fallback_on_invalid_json(monkeypatch):
    _patch_openai(monkeypatch, "not valid json")
    out = llm_caller.assemble_market_context({})
    assert out["market_state"] == "RANGE"


def test_assemble_market_context_fallback_on_openai_exception(monkeypatch):
    _patch_openai(monkeypatch, "", raise_exc=RuntimeError("boom"))
    out = llm_caller.assemble_market_context({})
    assert out["market_state"] == "RANGE"


# ---------- run_research_batch ----------


def test_run_research_batch_empty_returns_empty_list(monkeypatch):
    _patch_openai(monkeypatch, "{}")
    assert llm_caller.run_research_batch([]) == []


def test_run_research_batch_aligns_response_by_stock_id(monkeypatch):
    response = {
        "research": [
            {
                "stock": "2330",
                "name": "台積電",
                "type": "LEADER",
                "business_summary": "晶圓代工龍頭",
                "supply_chain_position": "midstream",
                "theme_fit": "HIGH",
                "theme": {"main_theme": "AI 伺服器", "theme_duration": "2Q_plus", "theme_score": 3, "theme_reason": "AI 資本支出延續"},
                "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
                "leader_check": {"industry_leader": "2330", "leader_price_trend": "strong_up", "leader_supports_theme": True},
            },
            {
                "stock": "2454",
                "name": "聯發科",
                "type": "FOLLOWER",
                "business_summary": "IC 設計",
                "supply_chain_position": "upstream",
                "theme_fit": "MEDIUM",
                "theme": {"main_theme": "AI 伺服器", "theme_duration": "1Q", "theme_score": 2, "theme_reason": "Edge AI"},
                "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
                "leader_check": {"industry_leader": "2330", "leader_price_trend": "strong_up", "leader_supports_theme": True},
            },
        ]
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    batch = [
        {"stock_id": "2330", "name": "台積電", "industry": "半導體業"},
        {"stock_id": "2454", "name": "聯發科", "industry": "半導體業"},
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 2
    by_id = {r["stock"]: r for r in out}
    assert by_id["2330"]["type"] == "LEADER"
    assert by_id["2454"]["type"] == "FOLLOWER"
    # 原 batch 欄位（industry）保留
    assert by_id["2330"]["industry"] == "半導體業"
    assert fake_client._responses_api.calls[0]["tools"] == [{"type": "web_search"}]
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_RESEARCH


def test_run_research_batch_falls_back_for_missing_stock_in_response(monkeypatch):
    """LLM 只回 1 檔，另一檔走 fallback。"""
    response = {
        "research": [
            {
                "stock": "2330",
                "name": "台積電",
                "type": "LEADER",
                "business_summary": "晶圓代工",
                "supply_chain_position": "midstream",
                "theme_fit": "HIGH",
                "theme": {"main_theme": "AI", "theme_duration": "2Q_plus", "theme_score": 3, "theme_reason": "..."},
                "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
                "leader_check": {"industry_leader": "2330", "leader_price_trend": "strong_up", "leader_supports_theme": True},
            }
        ]
    }
    _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    batch = [
        {"stock_id": "2330", "name": "台積電"},
        {"stock_id": "9999", "name": "缺資料股", "prelim_type": "FOLLOWER"},
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 2
    by_id = {r["stock"]: r for r in out}
    # 9999 走 fallback
    assert by_id["9999"].get("_unavailable") is True
    assert by_id["9999"]["type"] == "FOLLOWER"  # 從 prelim_type 帶入
    # 2330 是 LLM 回應
    assert by_id["2330"]["type"] == "LEADER"
    assert "_unavailable" not in by_id["2330"] or by_id["2330"].get("_unavailable") is not True


def test_run_research_batch_fallback_on_llm_failure(monkeypatch):
    """LLM 整個爆掉 → 每檔走 fallback。"""
    _patch_openai(monkeypatch, "", raise_exc=RuntimeError("openai down"))
    batch = [
        {"stock_id": "2330", "name": "台積電", "prelim_type": "LEADER"},
        {"stock_id": "2454", "name": "聯發科", "prelim_type": "FOLLOWER"},
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 2
    assert all(r.get("_unavailable") is True for r in out)


def test_run_research_batch_fallback_when_research_key_missing(monkeypatch):
    """LLM 回 valid JSON 但缺 research key → 全 fallback。"""
    _patch_openai(monkeypatch, '{"unexpected": []}')
    batch = [{"stock_id": "2330", "name": "台積電"}]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 1
    assert out[0].get("_unavailable") is True


# ---------- run_explanation_batch ----------


def test_run_explanation_batch_empty_returns_empty(monkeypatch):
    _patch_openai(monkeypatch, "{}")
    assert llm_caller.run_explanation_batch([], market_context={}) == []


def test_run_explanation_batch_attaches_decision_and_short_reason(monkeypatch):
    response = {
        "items": [
            {
                "stock": "2330",
                "signals": {
                    "capital_flow": "strong",
                    "chip_trend": "accumulating",
                    "margin_short_signal": "positive",
                    "technical_status": "breakout",
                },
                "decision": "WATCH",
                "short_reason": "量價與籌碼同步轉強，可續追。",
            },
            {
                "stock": "2454",
                "signals": {},
                "decision": "REMOVE",
                "short_reason": "近期已大漲，短線過熱。",
            },
        ]
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    research = [
        {"stock": "2330", "name": "台積電", "type": "LEADER"},
        {"stock": "2454", "name": "聯發科", "type": "FOLLOWER"},
    ]
    out = llm_caller.run_explanation_batch(research, market_context={"market_state": "RANGE"})
    by_id = {x["stock"]: x for x in out}
    assert by_id["2330"]["decision"] == "WATCH"
    assert by_id["2330"]["signals"]["capital_flow"] == "strong"
    assert "續追" in by_id["2330"]["short_reason"]
    assert by_id["2454"]["decision"] == "REMOVE"
    # 原 research 欄位（name / type）仍在
    assert by_id["2330"]["type"] == "LEADER"
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_DECISION


def test_run_explanation_batch_chunks_by_default_batch_size(monkeypatch):
    """13 檔 + DEFAULT_BATCH_SIZE=4 應該分 4 個 chunk（4+4+4+1）。"""
    monkeypatch.setattr(llm_caller, "DEFAULT_EXPLANATION_BATCH_SIZE", 4)

    # 用一個能依次回應不同 chunk 的 fake client
    call_count = {"n": 0}

    class _StatefulResponses:
        def create(self, **kwargs):
            call_count["n"] += 1
            return _FakeResponsesResponse(json.dumps({"items": []}))

    class _StatefulClient:
        def __init__(self):
            self.responses = _StatefulResponses()

    client = _StatefulClient()

    monkeypatch.setattr(llm_caller, "OpenAI", lambda **_: client)
    monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: "fake-key")
    monkeypatch.setattr(llm_caller, "_load_system_prompt", lambda: "S")

    research = [{"stock": f"S{i:03d}", "name": "x"} for i in range(13)]
    out = llm_caller.run_explanation_batch(research, market_context={})
    # 13 檔 / batch 4 → ceil(13/4) = 4 calls
    assert call_count["n"] == 4
    # 即使 LLM 都回空 items，每檔仍應走 fallback 補齊
    assert len(out) == 13
    assert all(r.get("decision") == "REMOVE" for r in out)


def test_run_explanation_batch_fallback_when_llm_fails(monkeypatch):
    _patch_openai(monkeypatch, "", raise_exc=TimeoutError("timeout"))
    research = [
        {"stock": "2330", "name": "台積電", "type": "LEADER"},
    ]
    out = llm_caller.run_explanation_batch(research, market_context={})
    assert len(out) == 1
    assert out[0]["decision"] == "REMOVE"
    assert "LLM 不可用" in out[0]["short_reason"]


def test_run_explanation_batch_falls_back_for_missing_stock_in_response(monkeypatch):
    response = {
        "items": [
            {
                "stock": "2330",
                "signals": {"capital_flow": "strong"},
                "decision": "WATCH",
                "short_reason": "ok",
            }
        ]
    }
    _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    research = [
        {"stock": "2330", "name": "台積電"},
        {"stock": "9999", "name": "缺資料股"},
    ]
    out = llm_caller.run_explanation_batch(research, market_context={})
    by_id = {x["stock"]: x for x in out}
    assert by_id["2330"]["decision"] == "WATCH"
    assert by_id["9999"]["decision"] == "REMOVE"  # fallback
    assert "LLM 不可用" in by_id["9999"]["short_reason"]


def test_run_watch_reason_batch_only_fills_long_reason(monkeypatch):
    response = {
        "items": [
            {
                "stock": "2330",
                "reason": "台積電受惠 AI 資本支出與龍頭延續，籌碼與技術同步，仍具觀察價值。",
            }
        ]
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    watch_items = [
        {"stock": "2330", "name": "台積電", "decision": "WATCH", "short_reason": "量價轉強"},
    ]
    out = llm_caller.run_watch_reason_batch(watch_items, market_context={"market_state": "RANGE"})
    assert out[0]["reason"].startswith("台積電受惠")
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_WATCH_REASON


# ---------- assemble_final_output ----------


def _watch_item(stock: str, name: str, type_label: str, industry: str) -> Dict[str, Any]:
    return {
        "stock": stock,
        "name": name,
        "industry": industry,
        "sub_industry": industry,
        "type": type_label,
        "business_summary": f"{name} 的業務",
        "supply_chain_position": "midstream",
        "theme_fit": "HIGH",
        "theme": {"main_theme": "AI", "theme_duration": "2Q_plus", "theme_score": 3, "theme_reason": "..."},
        "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
        "leader_check": {"industry_leader": stock, "leader_price_trend": "strong_up", "leader_supports_theme": True},
        "signals": {
            "capital_flow": "strong",
            "chip_trend": "accumulating",
            "margin_short_signal": "positive",
            "technical_status": "breakout",
        },
        "decision": "WATCH",
        "reason": f"{name} 的詳細 reason",
    }


def _remove_item(stock: str, name: str) -> Dict[str, Any]:
    return {
        "stock": stock,
        "name": name,
        "decision": "REMOVE",
        "short_reason": "近期過熱不適合介入",
    }


def test_assemble_final_output_splits_watch_and_remove():
    explanation = [
        _watch_item("2330", "台積電", "LEADER", "半導體業"),
        _watch_item("2454", "聯發科", "FOLLOWER", "半導體業"),
        _remove_item("2412", "中華電"),
    ]
    market_context = {"market_state": "STRUCTURAL_BULL", "market_state_reason": "盤面健康"}
    out = llm_caller.assemble_final_output(market_context, explanation, candidate_pool_size=80)
    assert len(out["watchlist"]) == 2
    assert len(out["removed"]) == 1
    assert out["removed"][0]["stock"] == "2412"
    assert out["candidate_pool_size"] == 80
    assert out["final_watchlist_size"] == 2


def test_assemble_final_output_summary_counts_by_type():
    explanation = [
        _watch_item("A", "A", "LEADER", "半導體業"),
        _watch_item("B", "B", "LEADER", "電子業"),
        _watch_item("C", "C", "FOLLOWER", "半導體業"),
        _watch_item("D", "D", "LAGGARD", "金融業"),
    ]
    out = llm_caller.assemble_final_output({"market_state": "RANGE"}, explanation, candidate_pool_size=10)
    assert out["summary"]["leader_count"] == 2
    assert out["summary"]["follower_count"] == 1
    assert out["summary"]["laggard_count"] == 1
    # main_hot_industries 依 watchlist 出現順序去重，前 5
    assert out["summary"]["main_hot_industries"][:3] == ["半導體業", "電子業", "金融業"]


def test_assemble_final_output_includes_total_tokens_when_provided():
    out = llm_caller.assemble_final_output(
        {"market_state": "RANGE"}, [], candidate_pool_size=0, total_tokens=12345
    )
    assert out["llm_total_tokens"] == 12345
    assert out["llm_model"] == llm_caller.DEFAULT_WATCH_REASON_MODEL


def test_assemble_final_output_handles_empty_explanation():
    out = llm_caller.assemble_final_output(
        {"market_state": "WEAK", "market_state_reason": "下跌"}, [], candidate_pool_size=0
    )
    assert out["watchlist"] == []
    assert out["removed"] == []
    assert out["summary"]["leader_count"] == 0
    assert out["summary"]["risk_note"] == "下跌"


def test_assemble_final_output_unknown_decision_treated_as_remove():
    """非 WATCH 的 decision（含空字串 / 大小寫變異）一律進 removed。"""
    explanation = [
        {"stock": "X", "name": "X", "decision": "MAYBE", "reason": "??"},
        {"stock": "Y", "name": "Y"},  # 完全沒 decision key
    ]
    out = llm_caller.assemble_final_output({"market_state": "RANGE"}, explanation, candidate_pool_size=2)
    assert out["watchlist"] == []
    assert len(out["removed"]) == 2
