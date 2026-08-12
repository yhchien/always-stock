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

from app.signals import llm_caller, market_cache


@pytest.fixture(autouse=True)
def _reset_market_cache(monkeypatch):
    """Legacy caller regressions stay on the supported P2-P4 rollback family."""
    monkeypatch.setenv("SIGNALS_PROMPT_FAMILY", "legacy_split")
    market_cache._reset_for_tests()
    yield
    market_cache._reset_for_tests()


# ---------- helpers ----------


class _FakeResponsesResponse:
    def __init__(self, content: str) -> None:
        self.output_text = content
        self.output = []
        self.id = "resp_test"
        self.status = "completed"
        self.incomplete_details = None
        self.usage = None

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
    monkeypatch.setattr(llm_caller, "_load_system_prompt", lambda stage="full", version="v4": "FAKE_SYSTEM_PROMPT")
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


def test_call_llm_json_passes_responses_structured_output(monkeypatch):
    fake_client = _patch_openai(monkeypatch, '{"ok": true}')
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    payload, diagnostic = llm_caller._call_llm_json(
        "system",
        "{}",
        model="test-model",
        stage="research",
        response_schema=schema,
        response_format_name="test_schema",
    )
    assert payload == {"ok": True}
    assert diagnostic["structured_output"] is True
    assert diagnostic["response_status"] == "completed"
    text_format = fake_client._responses_api.calls[0]["text"]["format"]
    assert text_format == {
        "type": "json_schema",
        "name": "test_schema",
        "description": "Machine-readable output for the fishtail signal pipeline.",
        "strict": True,
        "schema": schema,
    }


# ---------- assemble_market_context ----------


def test_assemble_market_context_parses_json_happy_path(monkeypatch):
    payload = {
        "external_risk_context": {
            "vix_status": "risk_on",
            "us_market_bias": "positive",
            "futures_bias": "LONG",
            "fx_risk": "neutral",
            "risk_summary": "VIX 低、台指期偏多",
        },
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(payload))
    out = llm_caller.assemble_market_context(
        {
            "taiex": {"change_pct_1d": 1.2},
            "otc": {"change_pct_1d": 1.8},
        }
    )
    assert out["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out["taiex_change_pct"] == 1.2
    assert out["otc_change_pct"] == 1.8
    assert out["vix_status"] == "risk_on"
    assert out["futures_bias"] == "LONG"
    assert out["external_risk_context"]["us_market_bias"] == "positive"
    assert "VIX" in out["market_state_reason"]
    assert out["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OK
    assert out["llm_diagnostic"]["stage"] == "market"
    assert fake_client._responses_api.calls[0]["tools"] == [{"type": "web_search"}]
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_MARKET
    assert fake_client.factory_kwargs["timeout"] == llm_caller._OPENAI_TIMEOUT_SECONDS
    assert fake_client.factory_kwargs["max_retries"] == llm_caller._OPENAI_MAX_RETRIES


def test_assemble_market_context_keeps_backend_index_values_even_if_llm_mentions_zeros(monkeypatch):
    payload = {
        "taiex_change_pct": 0,
        "otc_change_pct": 0,
        "external_risk_context": {
            "vix_status": "neutral",
            "us_market_bias": "neutral",
            "futures_bias": "NEUTRAL",
            "fx_risk": "neutral",
            "risk_summary": "外部資料不足，但 backend 數字仍可用。",
        },
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
    raw = '```json\n{"external_risk_context": {"vix_status": "neutral", "risk_summary": "ok"}}\n```'
    _patch_openai(monkeypatch, raw)
    out = llm_caller.assemble_market_context({"taiex": {"change_pct_1d": 0.1}})
    assert out["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out["vix_status"] == "neutral"
    assert out["taiex_change_pct"] == 0.1


def test_assemble_market_context_fallback_when_api_key_missing(monkeypatch):
    _patch_openai(monkeypatch, '{"market_state": "STRONG_BULL"}', api_key=None)
    out = llm_caller.assemble_market_context({"taiex": {"change_pct_1d": 1.5}})
    assert out["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out["taiex_change_pct"] == 1.5
    assert "OPENAI_API_KEY" in out["market_state_reason"]
    assert out["external_risk_context"]["vix_status"] == "unavailable"
    assert out["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_API_KEY_MISSING


def test_assemble_market_context_fallback_on_invalid_json(monkeypatch):
    _patch_openai(monkeypatch, "not valid json")
    out = llm_caller.assemble_market_context({})
    assert out["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_INVALID_JSON
    assert "合法 JSON" in out["market_state_reason"]


def test_assemble_market_context_fallback_on_openai_exception(monkeypatch):
    _patch_openai(monkeypatch, "", raise_exc=RuntimeError("boom"))
    out = llm_caller.assemble_market_context({})
    assert out["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OPENAI_EXCEPTION
    assert "RuntimeError" in out["market_state_reason"]


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
    # B4：type 鎖死 deterministic prelim_type，LLM 給的 type 會被覆寫
    batch = [
        {"stock_id": "2330", "name": "台積電", "industry": "半導體業", "prelim_type": "LEADER"},
        {"stock_id": "2454", "name": "聯發科", "industry": "半導體業", "prelim_type": "FOLLOWER"},
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 2
    by_id = {r["stock"]: r for r in out}
    assert by_id["2330"]["type"] == "LEADER"
    assert by_id["2454"]["type"] == "FOLLOWER"
    # 原 batch 欄位（industry）保留
    assert by_id["2330"]["industry"] == "半導體業"
    assert by_id["2330"]["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OK
    assert fake_client._responses_api.calls[0]["tools"] == [{"type": "web_search"}]
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_RESEARCH


def test_run_research_batch_serializes_date_fields_for_downstream_json_dumps(monkeypatch):
    """Regression：candidate_pool 注入的 first_seen_date 是 date 物件；aligned 結果必須
    可被 json.dumps 序列化，避免 _run_decision_chunk / _run_watch_reason_chunk 炸
    TypeError: Object of type date is not JSON serializable。
    """
    from datetime import date

    response = {
        "research": [
            {
                "stock": "2330",
                "name": "台積電",
                "type": "LEADER",
                "business_summary": "晶圓代工龍頭",
                "supply_chain_position": "midstream",
                "theme_fit": "HIGH",
                "theme": {"main_theme": "AI", "theme_duration": "2Q_plus", "theme_score": 3, "theme_reason": "AI"},
                "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
                "leader_check": {"industry_leader": "2330", "leader_price_trend": "strong_up", "leader_supports_theme": True},
            },
        ]
    }
    _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    batch = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "industry": "半導體業",
            "prelim_type": "LEADER",
            # M23 Phase 1.1 注入的 date 物件
            "first_seen_date": date(2026, 5, 20),
            "is_tracked": True,
            "days_since_first_seen": 4,
        },
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 1
    # 整個 aligned dict 必須能被 json.dumps 序列化（不可 raise TypeError）
    serialized = json.dumps(out, ensure_ascii=False)
    # first_seen_date 應被轉成 ISO string
    assert "2026-05-20" in serialized


def test_run_research_batch_fallback_serializes_date_fields(monkeypatch):
    """_research_fallback 走 `**stock` spread，同樣需要先把 date 物件 stringify。"""
    from datetime import date

    # LLM 回非 JSON → 所有 stock 走 fallback path
    _patch_openai(monkeypatch, "not json at all")
    batch = [
        {
            "stock_id": "2330",
            "name": "台積電",
            "industry": "半導體業",
            "prelim_type": "LEADER",
            "first_seen_date": date(2026, 5, 20),
        },
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 1
    # fallback 結果也必須能被 json.dumps 序列化
    serialized = json.dumps(out, ensure_ascii=False)
    assert "2026-05-20" in serialized


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
        {"stock_id": "2330", "name": "台積電", "prelim_type": "LEADER"},
        {"stock_id": "9999", "name": "缺資料股", "prelim_type": "FOLLOWER"},
    ]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 2
    by_id = {r["stock"]: r for r in out}
    # 9999 走 fallback
    assert by_id["9999"].get("_unavailable") is True
    assert by_id["9999"]["type"] == "FOLLOWER"  # 從 prelim_type 帶入
    # 2330 是 LLM 回應，但 type 仍鎖死 deterministic prelim_type（B4 規則）
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
    assert all(
        r["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OPENAI_EXCEPTION
        for r in out
    )


def test_run_research_batch_fallback_when_research_key_missing(monkeypatch):
    """LLM 回 valid JSON 但缺 research key → 全 fallback。"""
    _patch_openai(monkeypatch, '{"unexpected": []}')
    batch = [{"stock_id": "2330", "name": "台積電"}]
    out = llm_caller.run_research_batch(batch)
    assert len(out) == 1
    assert out[0].get("_unavailable") is True
    assert out[0]["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_INVALID_JSON


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


def test_run_explanation_batch_prompt_does_not_force_top_3_cap(monkeypatch):
    """這個測試鎖定 v5 的既有措辭（v6 是全新的 backend_max_decision 天花板語言，
    沒有這句歷史文字，改用 SIGNALS_FORCE_PROMPT_VERSION=v5 明確指定版本）。"""
    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v5")
    response = {"items": []}
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    research = [
        {"stock": "1303", "name": "南亞", "type": "FOLLOWER"},
    ]
    llm_caller.run_explanation_batch(research, market_context={"market_state": "RANGE"})
    prompt = fake_client._responses_api.calls[0]["input"]
    assert "最終 WATCH 名單只保留最值得追蹤的 3 檔" not in prompt
    assert "不要因為名額限制、批次內相對排序或同批有更強股票" in prompt


def test_run_explanation_batch_v6_uses_backend_max_decision_ceiling_language(monkeypatch):
    """v6（預設版本）的 decision prompt 不再有 legacy 的「批次相對排序」措辭，
    改用 backend_max_decision 天花板 + veto_reason 語言。"""
    response = {"items": []}
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    research = [
        {"stock": "1303", "name": "南亞", "type": "FOLLOWER", "deterministic_signals": {"max_decision": "WATCH"}},
    ]
    llm_caller.run_explanation_batch(research, market_context={"market_state": "RANGE"})
    prompt = fake_client._responses_api.calls[0]["input"]
    assert "backend_max_decision" in prompt
    assert "veto_reason" in prompt


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
    monkeypatch.setattr(llm_caller, "_load_system_prompt", lambda stage="full", version="v4": "S")

    research = [{"stock": f"S{i:03d}", "name": "x"} for i in range(13)]
    out = llm_caller.run_explanation_batch(research, market_context={})
    # 13 檔 / batch 4 → ceil(13/4) = 4 calls
    assert call_count["n"] == 4
    # 即使 LLM 都回空 items，每檔仍應走 fallback 補齊
    assert len(out) == 13
    assert all(r.get("decision") is None for r in out)
    assert all(r.get("processing_status") == "DECISION_FAILED" for r in out)


def test_run_explanation_batch_fallback_when_llm_fails(monkeypatch):
    _patch_openai(monkeypatch, "", raise_exc=TimeoutError("timeout"))
    research = [
        {"stock": "2330", "name": "台積電", "type": "LEADER"},
    ]
    out = llm_caller.run_explanation_batch(research, market_context={})
    assert len(out) == 1
    assert out[0]["decision"] is None
    assert out[0]["processing_status"] == "DECISION_FAILED"
    assert "短 decision失敗" in out[0]["short_reason"]
    assert out[0]["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OPENAI_EXCEPTION


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
    assert by_id["9999"]["decision"] is None
    assert by_id["9999"]["processing_status"] == "DECISION_FAILED"
    assert "短 decision失敗" in by_id["9999"]["short_reason"]
    assert by_id["9999"]["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OK


def test_run_watch_reason_batch_fills_five_section_bullets(monkeypatch):
    """M2（2026-05-24）：reason schema 改為 5 段 bullet array。"""
    response = {
        "items": [
            {
                "stock": "2330",
                "theme_reason": [
                    "公司主要業務為晶圓代工",
                    "屬於 AI 伺服器產業鏈核心",
                    "AI 資本支出題材可延續 2 季以上",
                ],
                "capital_reason": [
                    "今日值得關注因為外資 3 日累計買超 X 億",
                    "身為 LEADER 角色",
                    "同集團聯電同步上漲",
                ],
                "chip_reason": [
                    "外資投信同步買超",
                    "成交量擴張為 60 日均量 1.8 倍",
                ],
                "margin_reason": ["融資增幅 3% 屬正常範圍"],
                "technical_reason": [
                    "突破近期高點",
                    "站穩 20 日均線",
                ],
            }
        ]
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    watch_items = [
        {"stock": "2330", "name": "台積電", "decision": "WATCH", "short_reason": "量價轉強"},
    ]
    out = llm_caller.run_watch_reason_batch(watch_items, market_context={"market_state": "RANGE"})
    item = out[0]
    assert item["theme_reason"][0] == "公司主要業務為晶圓代工"
    assert len(item["theme_reason"]) == 3
    assert len(item["capital_reason"]) == 3
    assert len(item["chip_reason"]) == 2
    assert len(item["margin_reason"]) == 1
    assert len(item["technical_reason"]) == 2
    # 向後相容：reason 仍存在，組成 markdown 字串
    assert "【題材】" in item["reason"]
    assert "【資金】" in item["reason"]
    assert item["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_OK
    assert fake_client._responses_api.calls[0]["prompt_cache_key"] == llm_caller._CACHE_KEY_WATCH_REASON


def test_run_watch_reason_batch_fallback_fills_all_five_sections(monkeypatch):
    """M2：fallback 時 5 段全部填 fallback 訊息，前端 5 panel 仍可渲染。"""
    _patch_openai(monkeypatch, "not valid json")
    watch_items = [
        {"stock": "2330", "name": "台積電", "decision": "WATCH", "short_reason": "量價轉強"},
    ]
    out = llm_caller.run_watch_reason_batch(watch_items, market_context={})
    item = out[0]
    # 舊欄位向後相容：reason 仍是 short_reason
    assert "量價轉強" == item["reason"]
    # 5 段都填 fallback bullet
    for key in llm_caller.WATCH_REASON_SECTIONS:
        assert isinstance(item[key], list)
        assert len(item[key]) == 1
        assert item[key][0] == "量價轉強"
    assert item["llm_diagnostic"]["stage"] == "watch_reason"
    assert item["llm_diagnostic"]["status"] == llm_caller._DIAG_STATUS_INVALID_JSON
    assert item["processing_status"] == "REASON_GENERATION_FAILED"
    assert item["_unavailable"] is True


def test_coerce_reason_sections_handles_missing_sections():
    """M2：缺漏的段填空 list，不 raise。"""
    out = llm_caller._coerce_reason_sections({"theme_reason": ["bullet a"]})
    assert out["theme_reason"] == ["bullet a"]
    assert out["capital_reason"] == []
    assert out["chip_reason"] == []
    assert out["margin_reason"] == []
    assert out["technical_reason"] == []


def test_coerce_reason_sections_promotes_string_to_single_bullet():
    """M2：LLM 偶爾吐字串而非 array，要 graceful fallback 成單一 bullet。"""
    out = llm_caller._coerce_reason_sections(
        {"theme_reason": "一整段文字而非 array"}
    )
    assert out["theme_reason"] == ["一整段文字而非 array"]


def test_coerce_reason_sections_filters_empty_bullets_and_truncates_long():
    """M2：空白 bullet 過濾掉，超過 80 字 truncate（防 LLM 失控）。"""
    long_bullet = "字" * 100
    out = llm_caller._coerce_reason_sections(
        {"theme_reason": ["正常", "", "  ", None, long_bullet]}
    )
    assert out["theme_reason"][0] == "正常"
    # 空白與 None 都被濾掉
    assert len(out["theme_reason"]) == 2
    assert len(out["theme_reason"][1]) == 80


def test_format_watch_entry_includes_five_section_bullets():
    """M2：_format_watch_entry 輸出加 5 段欄位（最終 watchlist[] 持久化 dict）。"""
    item = {
        "stock": "2330",
        "name": "台積電",
        "type": "LEADER",
        "industry": "半導體",
        "signals": {"capital_flow": "strong"},
        "theme_reason": ["公司主業"],
        "capital_reason": ["外資連買"],
        "chip_reason": ["投信加碼"],
        "margin_reason": ["融資溫和"],
        "technical_reason": ["突破均線"],
        "reason": "整合 reason markdown",
    }
    out = llm_caller._format_watch_entry(item)
    assert out["theme_reason"] == ["公司主業"]
    assert out["capital_reason"] == ["外資連買"]
    assert out["chip_reason"] == ["投信加碼"]
    assert out["margin_reason"] == ["融資溫和"]
    assert out["technical_reason"] == ["突破均線"]
    assert out["reason"] == "整合 reason markdown"


def test_format_watch_entry_defaults_missing_sections_to_empty_list():
    """M2：5 段欄位若缺，輸出為空 list（前端用 length > 0 判斷是否渲染 panel）。"""
    out = llm_caller._format_watch_entry({"stock": "2330", "name": "台積電", "decision": "WATCH"})
    for key in llm_caller.WATCH_REASON_SECTIONS:
        assert out[key] == []


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
    assert [item["stock"] for item in out["removed"]] == ["2412"]
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
    assert out["summary"]["leader_count"] == 0
    assert out["summary"]["risk_note"] == "下跌"


def test_assemble_final_output_stamps_prompt_version():
    """payload 與每筆 watchlist item 都帶 prompt_version（給 30 日追蹤做版本歸因）。"""
    explanation = [
        _watch_item("2330", "台積電", "LEADER", "半導體業"),
        _watch_item("2454", "聯發科", "FOLLOWER", "半導體業"),
    ]
    out = llm_caller.assemble_final_output(
        {"market_state": "RANGE"}, explanation, candidate_pool_size=5
    )
    assert out["prompt_version"] == llm_caller.PROMPT_VERSION
    assert all(item["prompt_version"] == llm_caller.PROMPT_VERSION for item in out["watchlist"])


def test_resolve_prompt_version_routes_by_regime():
    """2026-07-29 起所有 regime 預設跑 v6.1 parity 對齊版。"""
    assert llm_caller._resolve_prompt_version("BULL_TREND") == "v6.1"
    assert llm_caller._resolve_prompt_version("VOLATILE_RANGE") == "v6.1"
    assert llm_caller._resolve_prompt_version("RISK_OFF") == "v6.1"
    assert llm_caller._resolve_prompt_version(None) == "v6.1"


def test_resolve_prompt_version_env_override(monkeypatch):
    """SIGNALS_FORCE_PROMPT_VERSION 強制覆寫 regime routing；未知值忽略。"""
    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v1")
    assert llm_caller._resolve_prompt_version("VOLATILE_RANGE") == "v1"
    assert llm_caller._resolve_prompt_version(None) == "v1"

    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v4")
    assert llm_caller._resolve_prompt_version("BULL_TREND") == "v4"

    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v99")
    assert llm_caller._resolve_prompt_version("BULL_TREND") == llm_caller.PROMPT_VERSION_BULL

    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "")
    assert llm_caller._resolve_prompt_version("VOLATILE_RANGE") == llm_caller.PROMPT_VERSION_VOLATILE


def test_assemble_final_output_prompt_version_follows_regime():
    """prompt_version label 預設走 v6.1，避免不同 regime 回到舊 prompt 方法論。"""
    explanation = [_watch_item("2330", "台積電", "LEADER", "半導體業")]

    bull = llm_caller.assemble_final_output(
        {"market_state": "STRONG_BULL", "market_regime": "BULL_TREND"},
        explanation,
        candidate_pool_size=5,
    )
    assert bull["prompt_version"] == "v6.1"
    assert all(item["prompt_version"] == "v6.1" for item in bull["watchlist"])

    volatile = llm_caller.assemble_final_output(
        {"market_state": "RANGE", "market_regime": "VOLATILE_RANGE"},
        explanation,
        candidate_pool_size=5,
    )
    assert volatile["prompt_version"] == "v6.1"


def test_load_system_prompt_v1_file_exists_and_slices():
    """v1 prompt 檔存在且可被 stage 切片（多頭盤 research stage 用得到）。"""
    fragment = llm_caller._load_system_prompt(stage="research", version="v1")
    assert "STEP 2" in fragment
    assert isinstance(fragment, str) and len(fragment) > 0


def test_load_system_prompt_v5_file_exists_and_includes_momentum_gate():
    """v5 prompt 檔存在，decision stage 需吃到 Momentum Gate。"""
    llm_caller._PROMPT_FRAGMENT_CACHE.clear()
    fragment = llm_caller._load_system_prompt(stage="decision", version="v5")
    assert "STEP 7.8：Momentum Gate" in fragment
    assert "rs_market_percentile_20d" in fragment
    assert "STEP 0：讀取市場狀態與外部風險背景" not in fragment


def test_assemble_final_output_unknown_decision_is_dropped():
    """非 WATCH 的 decision（含空字串 / 大小寫變異）不進最終 watchlist。"""
    explanation = [
        {"stock": "X", "name": "X", "decision": "MAYBE", "reason": "??"},
        {"stock": "Y", "name": "Y"},  # 完全沒 decision key
    ]
    out = llm_caller.assemble_final_output({"market_state": "RANGE"}, explanation, candidate_pool_size=2)
    assert out["watchlist"] == []


def test_assemble_final_output_keeps_all_watch_items_without_topk_or_cluster_cap():
    explanation = []
    for i in range(8):
        explanation.append(
            {
                **_watch_item(
                    f"{1000 + i}",
                    f"Stock-{i}",
                    "LEADER" if i < 10 else "FOLLOWER",
                    "半導體業",
                ),
                "momentum_rank": i + 1,
                "group_info": {
                    "is_group_stock": True,
                    "group_name": "AI伺服器",
                    "related_group_stocks": [],
                },
                "theme": {"theme_score": max(0, 35 - i)},
                "signals": {
                    "capital_flow": "strong" if i < 30 else "moderate",
                    "chip_trend": "accumulating" if i < 30 else "weakening",
                    "margin_short_signal": "positive" if i < 30 else "neutral",
                    "technical_status": "breakout" if i < 30 else "range_bound",
                },
            }
        )

    out = llm_caller.assemble_final_output(
        {"market_state": "STRUCTURAL_BULL"},
        explanation,
        candidate_pool_size=80,
    )

    assert len(out["watchlist"]) == 8
    assert out["final_watchlist_size"] == 8
    assert [item["stock"] for item in out["watchlist"]] == [str(1000 + i) for i in range(8)]


# ---------- B4 / B6 / A3 / A4 新增測試 ----------


def test_normalize_prelim_type_maps_laggard_candidate_to_laggard():
    """B4：candidate_pool 給的 LAGGARD_CANDIDATE 對外應顯示為 LAGGARD。

    2026-07-22（LLM v6 contract 對齊）：未知/缺值不再 fallback 到 LEADER
    （那是 unknown → strongest class 的偏誤），改為保守的 LAGGARD。
    """
    assert llm_caller._normalize_prelim_type("LAGGARD_CANDIDATE") == "LAGGARD"
    assert llm_caller._normalize_prelim_type("LEADER") == "LEADER"
    assert llm_caller._normalize_prelim_type("follower") == "FOLLOWER"
    assert llm_caller._normalize_prelim_type(None) == "LAGGARD"
    assert llm_caller._normalize_prelim_type("UNKNOWN") == "LAGGARD"


def test_run_research_batch_overrides_llm_type_with_prelim_type(monkeypatch):
    """B4：即使 LLM 自己回 LEADER 也應被 prelim_type=FOLLOWER 覆寫。"""
    response = {
        "research": [
            {
                "stock": "2454", "name": "聯發科",
                "type": "LEADER",  # LLM 試圖判 LEADER
                "business_summary": "IC 設計",
                "supply_chain_position": "upstream", "theme_fit": "HIGH",
                "theme": {"main_theme": "x", "theme_duration": "1Q", "theme_score": 2, "theme_reason": "."},
                "group_info": {"is_group_stock": False, "group_name": None, "related_group_stocks": [], "group_price_sync": "none"},
                "leader_check": {"industry_leader": "2330", "leader_price_trend": "up", "leader_supports_theme": True},
            }
        ]
    }
    _patch_openai(monkeypatch, json.dumps(response, ensure_ascii=False))
    batch = [{"stock_id": "2454", "name": "聯發科", "prelim_type": "FOLLOWER"}]
    out = llm_caller.run_research_batch(batch)
    assert out[0]["type"] == "FOLLOWER"  # deterministic 覆寫


def test_to_evidence_view_extracts_core_metrics():
    """B6：candidate_pool dict 投影成乾淨 evidence card。"""
    stocks = [
        {
            "stock_id": "2330", "name": "台積電", "industry": "半導體業",
            "sub_industry": "晶圓代工", "prelim_type": "LEADER",
            "industry_rank_5d": 1, "industry_rank_net_3d": 1, "industry_count": 25,
            "consecutive_buy_days_3d": 3, "volume_5d_to_60d_ratio": 1.8,
            "price_change_3d": 6.5, "price_change_5d": 9.2,
            "total_institution_flow_3d": 5_000_000_000,
            "in_top_stocks_3d": True, "in_top_industries_3d": True,
            "soft_hints": ["HINT_TEST"],
            # internal noise that should be dropped
            "industry_name": "半導體業",
        }
    ]
    out = llm_caller._to_evidence_view(stocks)
    assert out[0]["stock"] == "2330"
    assert out[0]["prelim_type"] == "LEADER"
    assert out[0]["evidence"]["industry_rank_5d"] == 1
    assert out[0]["evidence"]["consecutive_inst_buy_days_3d"] == 3
    assert out[0]["evidence"]["volume_5d_to_60d_ratio"] == 1.8
    assert out[0]["evidence"]["total_institution_flow_3d_twd"] == 5_000_000_000
    assert out[0]["evidence"]["in_top_stocks_3d"] is True
    assert out[0]["soft_hints"] == ["HINT_TEST"]


def test_run_research_batch_user_msg_includes_evidence_view(monkeypatch):
    """B6：research user_msg 必須含 evidence 區塊，且把硬規則寫進 prompt。"""
    response = {"research": []}
    fake_client = _patch_openai(monkeypatch, json.dumps(response))
    batch = [
        {
            "stock_id": "2330", "name": "台積電", "industry": "半導體業",
            "prelim_type": "LEADER", "consecutive_buy_days_3d": 3,
            "price_change_3d": 5.5,
        }
    ]
    llm_caller.run_research_batch(batch)
    sent_user_msg = fake_client._responses_api.calls[0]["input"]
    assert '"evidence"' in sent_user_msg
    assert "consecutive_inst_buy_days_3d" in sent_user_msg
    assert "硬規則" in sent_user_msg  # B4 / B6 指引段


def test_run_research_batch_loads_research_stage_prompt(monkeypatch):
    """A4：v5 research stage 收到的 system prompt 只含 research 相關 STEP（1-4），
    且**必須含 STEP 內文**（2026-07-22 修正 stage-splitter 曾把內文整段吃掉的 bug，
    這裡明確斷言內文關鍵字存在，不只是標題）。

    這個 test 走真實 prompt（不 patch `_load_system_prompt`），只 mock OpenAI client，
    明確指定走 v5 版本以鎖定既有措辭。
    """
    monkeypatch.setenv("SIGNALS_FORCE_PROMPT_VERSION", "v5")
    fake_client = _FakeOpenAIClient(json.dumps({"research": []}))

    def _factory(**_kwargs: Any) -> _FakeOpenAIClient:
        return fake_client

    monkeypatch.setattr(llm_caller, "OpenAI", _factory)
    monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: "fake-key")
    llm_caller._PROMPT_FRAGMENT_CACHE.clear()

    llm_caller.run_research_batch([{"stock_id": "2330", "name": "X", "prelim_type": "LEADER"}])
    sent_instructions = fake_client._responses_api.calls[0]["instructions"]

    # research stage 應含 STEP 1~4 的標題與內文，不含 STEP 5/6/7/8/9
    assert "STEP 1：建立候選池" in sent_instructions
    assert "STEP 4：龍頭股、同業、集團股檢查" in sent_instructions
    # 內文必須真的存在（不是只有標題被裁到剩空殼）
    assert "公司實際主要業務" in sent_instructions
    assert "STEP 5：解讀 backend 角色分類" not in sent_instructions
    assert "STEP 9：最終輸出格式" not in sent_instructions
    # 共用 preamble + 重要限制必須在
    assert "核心原則" in sent_instructions
    assert "重要限制" in sent_instructions


def test_run_research_batch_loads_v6_research_stage_prompt_with_body(monkeypatch):
    """v6（預設版本）research stage 同樣必須含 STEP 內文，不能只有標題殘影。"""
    fake_client = _FakeOpenAIClient(json.dumps({"research": []}))

    def _factory(**_kwargs: Any) -> _FakeOpenAIClient:
        return fake_client

    monkeypatch.setattr(llm_caller, "OpenAI", _factory)
    monkeypatch.setattr(llm_caller, "get_openai_api_key", lambda: "fake-key")
    llm_caller._PROMPT_FRAGMENT_CACHE.clear()

    llm_caller.run_research_batch([{"stock_id": "2330", "name": "X", "prelim_type": "LEADER"}])
    sent_instructions = fake_client._responses_api.calls[0]["instructions"]

    assert "STEP 2：業務 / ETF 曝險研究" in sent_instructions
    assert "tracking index" in sent_instructions  # STEP 2 內文（ETF 研究指示）
    assert "STEP 6：外部否決驗證" not in sent_instructions
    assert "FALSE_SUPPLY_CHAIN_LINK" not in sent_instructions  # STEP 6 內文不該滲入 research


def test_assemble_market_context_caches_for_subsequent_calls(monkeypatch):
    """A3：第一次 LLM call 命中後寫 cache，第二次直接命中（不再打 OpenAI）。"""
    response = {
        "external_risk_context": {
            "vix_status": "risk_on",
            "us_market_bias": "positive",
            "futures_bias": "LONG",
            "fx_risk": "neutral",
            "risk_summary": "fresh",
        },
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response))
    snapshot = {
        "taiex": {"change_pct_1d": 1.2},
        "otc": {"change_pct_1d": 0.8},
    }
    out1 = llm_caller.assemble_market_context(snapshot)
    out2 = llm_caller.assemble_market_context(snapshot)
    assert out1["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out2["market_state"] == "BACKEND_REGIME_AUTHORITATIVE"
    assert out2["external_risk_context"]["risk_summary"] == "fresh"
    # 只打過一次 OpenAI
    assert len(fake_client._responses_api.calls) == 1
    # taiex/otc 仍以 backend 為準
    assert out2["taiex_change_pct"] == 1.2
    assert out2["otc_change_pct"] == 0.8


def test_assemble_market_context_does_not_cache_fallback(monkeypatch):
    """A3：fallback path（OpenAI 不可用）不該寫 cache，下次仍重試。"""
    _patch_openai(monkeypatch, "", api_key=None)
    snapshot = {"taiex": {"change_pct_1d": -0.5}, "otc": {"change_pct_1d": -1.0}}
    llm_caller.assemble_market_context(snapshot)
    # 第二次應該還是 fallback（cache 沒寫）
    cached = llm_caller.market_cache.get_cached()
    assert cached is None


def test_assemble_market_context_use_cache_false_bypasses_cache(monkeypatch):
    """A3：use_cache=False 強制 fresh（cron 用）。"""
    response = {
        "external_risk_context": {
            "vix_status": "neutral",
            "us_market_bias": "neutral",
            "futures_bias": "NEUTRAL",
            "fx_risk": "neutral",
            "risk_summary": "r",
        },
    }
    fake_client = _patch_openai(monkeypatch, json.dumps(response))
    snapshot = {"taiex": {"change_pct_1d": 0.0}, "otc": {"change_pct_1d": 0.0}}
    llm_caller.assemble_market_context(snapshot, use_cache=True)  # 寫 cache
    llm_caller.assemble_market_context(snapshot, use_cache=False)  # 強制 fresh
    assert len(fake_client._responses_api.calls) == 2


def test_load_system_prompt_market_stage_drops_other_steps():
    """A4：market stage fragment 只含 STEP 0 + preamble + 重要限制。"""
    llm_caller._PROMPT_FRAGMENT_CACHE.clear()
    fragment = llm_caller._load_system_prompt(stage="market")
    assert "STEP 0：讀取市場狀態與外部風險背景" in fragment
    assert "STEP 3：公司業務" not in fragment
    assert "STEP 5：解讀 backend 角色分類" not in fragment
    assert "重要限制" in fragment
    # input 描述（preamble）也保留
    assert "stock_pool" in fragment


# ---------- tracking_status evidence（2026-05-26 1.3）----------


def test_to_evidence_view_includes_tracking_status_for_tracked_stock():
    from datetime import date
    candidate = {
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體",
        "prelim_type": "LEADER",
        "is_tracked": True,
        "first_seen_date": date(2026, 4, 13),
        "days_since_first_seen": 5,
        "hit_count": 2,
        "max_positive_return_pct": 2.8,
        "max_negative_return_pct": -4.5,
        "failed_follow_through": False,
    }
    out = llm_caller._to_evidence_view([candidate])
    ts = out[0]["tracking_status"]
    assert ts["is_tracked"] is True
    assert ts["first_seen_date"] == "2026-04-13"  # ISO 字串
    assert ts["days_since_first_seen"] == 5
    assert ts["hit_count"] == 2
    assert ts["max_positive_return_pct"] == 2.8
    assert ts["max_negative_return_pct"] == -4.5
    # 不暴露 failed_follow_through 給 LLM（hard filter 已過濾，保留會誤導）
    assert "failed_follow_through" not in ts


def test_to_evidence_view_handles_untracked_stock():
    """首次進候選池 → tracking_status.is_tracked=False，其他欄位 None。"""
    candidate = {
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體",
        "prelim_type": "LEADER",
        # 沒有 tracking 欄位
    }
    out = llm_caller._to_evidence_view([candidate])
    ts = out[0]["tracking_status"]
    assert ts["is_tracked"] is False
    assert ts["first_seen_date"] is None
    assert ts["days_since_first_seen"] is None
    assert ts["hit_count"] is None
    assert ts["max_positive_return_pct"] is None
    assert ts["max_negative_return_pct"] is None


def test_to_evidence_view_includes_momentum_signals():
    candidate = {
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體",
        "prelim_type": "LEADER",
        "momentum_score": 82,
        "momentum_phase": "trending",
        "rs_market_percentile_20d": 91,
        "rs_industry_percentile_20d": 78,
        "rs_rank_change_5d": 120,
        "trend_efficiency_20d": 0.62,
    }
    out = llm_caller._to_evidence_view([candidate])
    momentum = out[0]["momentum_signals"]
    assert momentum["momentum_score"] == 82
    assert momentum["momentum_phase"] == "trending"
    assert momentum["rs_market_percentile_20d"] == 91
    assert momentum["rs_industry_percentile_20d"] == 78
    assert momentum["rs_rank_change_5d"] == 120
    assert momentum["trend_efficiency_20d"] == 0.62


def test_to_evidence_view_handles_already_serialized_date_string():
    """first_seen_date 已是字串時不應再 isoformat 出錯。"""
    candidate = {
        "stock_id": "2330",
        "name": "台積電",
        "industry": "半導體",
        "prelim_type": "LEADER",
        "is_tracked": True,
        "first_seen_date": "2026-04-13",
        "days_since_first_seen": 5,
        "hit_count": 1,
        "max_positive_return_pct": 1.0,
        "max_negative_return_pct": -2.0,
    }
    out = llm_caller._to_evidence_view([candidate])
    assert out[0]["tracking_status"]["first_seen_date"] == "2026-04-13"


# ──────────────────────────────────────────────────────────────────────────────
# summarize_token_usage（2026-08-12 成本追蹤）
# ──────────────────────────────────────────────────────────────────────────────


def _diag(response_id, total_tokens=None, input_tokens=None, output_tokens=None):
    usage = {}
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    if input_tokens is not None:
        usage["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage["output_tokens"] = output_tokens
    return {"response_id": response_id, "usage": usage}


def test_summarize_token_usage_dedupes_by_response_id():
    """一個 batch 呼叫的多檔候選共用同一個 response_id/usage；天真加總會把同一次
    API 呼叫的 token 數重複算好幾次，這裡驗證去重後只算一次。"""
    items = [
        {"stock": "2330", "llm_diagnostic": _diag("resp-1", total_tokens=500)},
        {"stock": "2317", "llm_diagnostic": _diag("resp-1", total_tokens=500)},
        {"stock": "1301", "llm_diagnostic": _diag("resp-1", total_tokens=500)},
        {"stock": "2454", "llm_diagnostic": _diag("resp-2", total_tokens=300)},
    ]
    result = llm_caller.summarize_token_usage(items)
    assert result == {"call_count": 2, "total_tokens": 800}


def test_summarize_token_usage_falls_back_to_input_plus_output():
    items = [{"llm_diagnostic": _diag("resp-1", input_tokens=120, output_tokens=80)}]
    result = llm_caller.summarize_token_usage(items)
    assert result == {"call_count": 1, "total_tokens": 200}


def test_summarize_token_usage_skips_items_without_usable_diagnostic():
    items = [
        {"stock": "2330"},  # 沒有 llm_diagnostic（例如 backend_pre_removed）
        {"stock": "2317", "llm_diagnostic": {}},  # 有 key 但空 dict
        {"stock": "1301", "llm_diagnostic": _diag("resp-1", total_tokens=None)},  # usage 缺 token 欄位
    ]
    result = llm_caller.summarize_token_usage(items)
    assert result == {"call_count": 0, "total_tokens": 0}


def test_summarize_token_usage_single_dict_wrapped_in_list():
    """market_context／global_selection 是單一結果，呼叫端包成一元素 list。"""
    market_context = {"market_state": "RANGE", "llm_diagnostic": _diag("resp-market", total_tokens=1000)}
    result = llm_caller.summarize_token_usage([market_context])
    assert result == {"call_count": 1, "total_tokens": 1000}


def test_summarize_token_usage_custom_diagnostic_key():
    """P4 tracking 用 `_llm_diagnostic`（底線開頭），跟其他 stage 的
    `llm_diagnostic` key 名不同。"""
    items = [{"stock": "2330", "_llm_diagnostic": _diag("resp-1", total_tokens=400)}]
    result = llm_caller.summarize_token_usage(items, diagnostic_key="_llm_diagnostic")
    assert result == {"call_count": 1, "total_tokens": 400}


def test_summarize_token_usage_handles_empty_and_none_input():
    assert llm_caller.summarize_token_usage([]) == {"call_count": 0, "total_tokens": 0}
    assert llm_caller.summarize_token_usage(None) == {"call_count": 0, "total_tokens": 0}
