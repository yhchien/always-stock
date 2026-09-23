import json
from datetime import date

import pytest

from app import jev_client
from app.models import JevEvaluationCache
from app.signals import jev_shadow


def _research_item(source=None):
    return {
        "stock_id": "2330",
        "stock": "2330",
        "name": "台積電",
        "industry": "半導體",
        "sub_industry": "晶圓代工",
        "asset_type": "COMMON_STOCK",
        "business_summary": "晶圓代工與先進製程服務。",
        "research_summary": "公司公告新增先進製程客戶訂單，與原有題材相關。",
        "theme_cluster": "AI 先進製程",
        "business_validation": "VERIFIED",
        "theme_validation": "VERIFIED",
        "theme": {
            "main_theme": "AI 先進製程",
            "catalyst_status": "ACTIVE",
            "catalyst_summary": "需求與訂單持續。",
        },
        "sources": [source or {
            "title": "公司新增先進製程訂單",
            "url": "https://example.com/news-1",
            "published_date": "2026-09-22",
            "source_type": "NEWS",
        }],
    }


def _jev_response():
    answers = {}
    for name, choices in jev_shadow.QUESTION_CHOICES.items():
        choice = {
            "business_relevance": "DIRECT",
            "event_type": "NEW_ORDER",
            "catalyst_state": "NEW",
            "thesis_impact": "SUPPORT",
            "verification_need": "NORMAL",
        }[name]
        answers[name] = {
            "type": "choice",
            "choice": choice,
            "probabilities": {choice: 0.9, choices[-1]: 0.1},
        }
    return {
        "model": "typesafe-ai/jev",
        "answers": answers,
        "usage": {"inputTokens": 100, "outputTokens": 20},
        "providerMetadata": {"gateway": {"cost": "0.0001"}},
    }


def test_shadow_evaluates_choices_and_reuses_cache(db, monkeypatch):
    monkeypatch.setenv("JEV_MODE", "shadow")
    monkeypatch.setattr(jev_shadow, "get_jev_api_key", lambda: "test-key")
    calls = []

    def fake_evaluate(state, questions, **kwargs):
        calls.append((state, questions, kwargs))
        return _jev_response()

    monkeypatch.setattr(jev_shadow, "evaluate", fake_evaluate)
    item = _research_item()

    first = jev_shadow.run_shadow(db, [item], date(2026, 9, 23))
    second = jev_shadow.run_shadow(db, [item], date(2026, 9, 23))

    assert first["status"] == "COMPLETED"
    assert first["summary"]["api_call_count"] == 1
    evidence = first["evidence"][0]
    assert evidence["business_relevance"] == "DIRECT"
    assert evidence["event_type"] == "NEW_ORDER"
    assert evidence["answer_confidence"]["business_relevance"]["probability"] == 0.9
    assert evidence["answer_confidence"]["business_relevance"]["probabilities"]["DIRECT"] == 0.9
    assert len(calls) == 1
    assert second["summary"]["cache_hit_count"] == 1
    assert second["summary"]["api_call_count"] == 0
    assert db.query(JevEvaluationCache).count() == 1
    assert calls[0][1]["business_relevance"]["type"] == "choice"
    assert "options" not in calls[0][1]["business_relevance"]


def test_shadow_never_calls_for_future_or_missing_evidence(db, monkeypatch):
    monkeypatch.setenv("JEV_MODE", "shadow")
    monkeypatch.setattr(jev_shadow, "get_jev_api_key", lambda: "test-key")
    calls = []
    monkeypatch.setattr(jev_shadow, "evaluate", lambda *args, **kwargs: calls.append(1))

    future = _research_item({
        "title": "Future event",
        "url": "https://example.com/future",
        "published_date": "2026-09-24",
        "source_type": "NEWS",
    })
    missing = _research_item({"title": "Missing date", "url": "https://example.com/missing"})
    report = jev_shadow.run_shadow(db, [future, missing], date(2026, 9, 23))

    assert calls == []
    assert {item["status"] for item in report["evidence"]} == {
        "FUTURE_EVIDENCE_REJECTED",
        "INSUFFICIENT_DATA",
    }
    assert all(item["business_relevance"] == "UNKNOWN" for item in report["evidence"])


def test_invalid_jev_output_is_recorded_without_fake_classification(db, monkeypatch):
    monkeypatch.setenv("JEV_MODE", "shadow")
    monkeypatch.setattr(jev_shadow, "get_jev_api_key", lambda: "test-key")
    monkeypatch.setattr(
        jev_shadow,
        "evaluate",
        lambda *args, **kwargs: {"answers": {"business_relevance": {"choice": "DIRECT"}}},
    )

    report = jev_shadow.run_shadow(db, [_research_item()], date(2026, 9, 23))
    evidence = report["evidence"][0]

    assert report["status"] == "PARTIAL"
    assert evidence["status"] == "INVALID_RESPONSE"
    assert evidence["business_relevance"] == "UNKNOWN"
    assert evidence["thesis_impact"] == "UNKNOWN"


def test_missing_key_and_errors_do_not_leak_key(db, monkeypatch):
    monkeypatch.setenv("JEV_MODE", "shadow")
    secret = "super-secret-test-key"
    monkeypatch.setattr(jev_shadow, "get_jev_api_key", lambda: secret)
    monkeypatch.setattr(
        jev_shadow,
        "evaluate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            jev_client.JevClientError(
                "gateway failed",
                status_code=500,
                response_body="authorization super-secret-test-key was rejected",
            )
        ),
    )
    monkeypatch.setenv("JEV_MAX_RETRIES", "0")

    report = jev_shadow.run_shadow(db, [_research_item()], date(2026, 9, 23))

    serialized = json.dumps(report, ensure_ascii=False)
    assert secret not in serialized
    assert report["evidence"][0]["status"] == "API_ERROR"


def test_off_mode_is_a_noop(db, monkeypatch):
    monkeypatch.setenv("JEV_MODE", "off")
    monkeypatch.setattr(jev_shadow, "get_jev_api_key", lambda: pytest.fail("must not read key"))
    report = jev_shadow.run_shadow(db, [_research_item()], date(2026, 9, 23))
    assert report["status"] == "DISABLED"
    assert report["evidence"] == []
