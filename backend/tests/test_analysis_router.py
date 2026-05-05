"""Tests for /api/analysis/trade-quality and supporting endpoints."""

from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Base,
    DailyPrice,
    IndustryDailyFlow,
    InstStockFlow,
    MonthlyRevenue,
    StockMaster,
)
from app.rate_limit import limiter
from app.routers import analysis as analysis_router
from app.routers.analysis import (
    _apply_key_factor_fallback,
    _build_user_message,
    _is_market_not_open_yet,
    _load_system_prompt,
    _synthesize_key_factors_from_context,
    TradeQualityResponse,
)


# 6 個 category 齊備的 key_factors fixture：避免測試裡的 mock OpenAI 回應觸發
# `_call_openai_with_factors_retry` 內建的「補燈號」retry（會多打一次 mock）。
# 只有要驗證「first-call 成功 + call_count == 1」的測試需要塞這個；驗證 retry 行為
# 的測試本來就會自然呼叫多次。
_FULL_KEY_FACTORS = [
    {"category": "industry", "level": "A", "trend": "stable", "note": "n1"},
    {"category": "industry_heat", "level": "A", "trend": "stable", "note": "n2"},
    {"category": "return", "level": "A", "trend": "stable", "note": "n3"},
    {"category": "chip", "level": "A", "trend": "stable", "note": "n4"},
    {"category": "technical", "level": "A", "trend": "stable", "note": "n5"},
    {"category": "fundamental", "level": "A", "trend": "stable", "note": "n6"},
]


@pytest.fixture
def api():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    # 清空 slowapi in-memory storage，避免跨測試累積 rate limit counter
    limiter.reset()
    analysis_router._trade_quality_cache.clear()
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    analysis_router._trade_quality_cache.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def _seed_stock(db, stock_id: str, stock_name: str, industry: str = "半導體") -> None:
    db.add(StockMaster(stock_id=stock_id, stock_name=stock_name, industry_name=industry))


def _seed_price(db, d: date, stock_id: str, close: float) -> None:
    db.add(
        DailyPrice(
            trade_date=d,
            stock_id=stock_id,
            open_price=close - 1,
            high_price=close + 1,
            low_price=close - 2,
            close_price=close,
            volume=1_000_000,
            turnover=close * 1_000_000,
            spread=0.5,
        )
    )


def _seed_flow(db, d: date, stock_id: str, inst_type: str, net_shares: float) -> None:
    db.add(
        InstStockFlow(
            trade_date=d,
            stock_id=stock_id,
            inst_type=inst_type,
            net_shares=net_shares,
            net_amount_est=net_shares * 100,
        )
    )


def _seed_industry_flow(db, d: date, industry: str) -> None:
    db.add(
        IndustryDailyFlow(
            trade_date=d,
            industry_name=industry,
            foreign_net_amount=0,
            trust_net_amount=0,
            dealer_net_amount=0,
            total_net_amount=0,
        )
    )


def test_build_user_message_requires_key_factors():
    text = _build_user_message(
        {
            "stock_id": "2330",
            "stock_name": "台積電",
            "industry_name": "半導體",
            "sub_industry": "晶圓代工",
            "buy_date": "2026-05-04",
            "latest_close": 1000.0,
            "prices_text": "x",
            "flows_text": "y",
            "revenue_text": "z",
        },
        m21_context=None,
        warnings=[],
    )
    assert '"key_factors": [' in text
    assert "`key_factors` 為必填欄位" in text


def test_synthesize_key_factors_from_context_returns_complete_set():
    factors = _synthesize_key_factors_from_context(
        {
            "industry_summary": {
                "industry_hot_level": "A",
                "industry_price_strength": "strong",
                "industry_volume_trend": "expanding_3d",
                "industry_institution_flow": "strong_buy",
                "is_false_hot": False,
            },
            "chip_summary": {
                "chip_strength": "strong",
                "is_accumulation": True,
                "volume_trend": "increasing",
                "investment_trust_buy_days": 2,
                "foreign_buy_days": 2,
            },
            "peer_rank": {
                "leader_or_follower": "leader",
                "return_5d_percentile": 0.1,
            },
            "fundamental": {
                "revenue_yoy": 25.0,
                "revenue_mom": 6.0,
            },
            "price_structure": {
                "trend": "uptrend",
                "is_breakout": True,
                "is_consolidation": False,
                "is_accelerating": True,
            },
        }
    )
    assert factors is not None
    assert len(factors) == 6
    assert {f.category for f in factors} == {
        "industry", "industry_heat", "return", "chip", "technical", "fundamental"
    }


def test_apply_key_factor_fallback_uses_deterministic_context():
    response = TradeQualityResponse(
        stock_id="2330",
        stock_name="台積電",
        buy_date="2026-05-04",
        rating="BUY",
        rating_label="推薦",
        summary="x",
        report_markdown="y",
        warnings=[],
        source="openai",
    )
    out = _apply_key_factor_fallback(
        response,
        m21_context={
            "industry_summary": {
                "industry_hot_level": "B",
                "industry_price_strength": "medium",
                "industry_volume_trend": "intermittent",
                "industry_institution_flow": "mixed",
                "is_false_hot": False,
            },
            "chip_summary": {
                "chip_strength": "neutral",
                "is_accumulation": False,
                "volume_trend": "flat",
                "investment_trust_buy_days": 0,
                "foreign_buy_days": 1,
            },
            "peer_rank": {
                "leader_or_follower": "follower",
                "return_5d_percentile": 0.4,
            },
            "fundamental": {
                "revenue_yoy": 5.0,
                "revenue_mom": 1.0,
            },
            "price_structure": {
                "trend": "sideways",
                "is_breakout": False,
                "is_consolidation": True,
                "is_accelerating": False,
            },
        },
    )
    assert out.key_factors is not None
    assert len(out.key_factors) == 6
    assert "已用 deterministic context 補齊 key_factors" in out.warnings


# ── /api/stocks/search ──────────────────────────────────────────────────────


def test_stocks_search_by_id_prefix(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2337", "旺宏")
    _seed_stock(db, "2317", "鴻海")
    db.commit()

    resp = client.get("/api/stocks/search?q=233")
    assert resp.status_code == 200
    data = resp.json()
    ids = [row["stock_id"] for row in data]
    assert "2330" in ids
    assert "2337" in ids
    assert "2317" not in ids


def test_stocks_search_by_name(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    _seed_stock(db, "2454", "聯發科")
    db.commit()

    resp = client.get("/api/stocks/search?q=聯發")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["stock_id"] == "2454"


def test_stocks_search_rejects_empty_query(api):
    client, _ = api
    resp = client.get("/api/stocks/search?q=")
    assert resp.status_code == 422  # pydantic min_length=1


# ── /api/market/latest-trade-date ───────────────────────────────────────────


def test_latest_trade_date_returns_max(api):
    client, db = api
    _seed_industry_flow(db, date(2024, 1, 2), "半導體")
    _seed_industry_flow(db, date(2024, 1, 5), "半導體")
    db.commit()

    resp = client.get("/api/market/latest-trade-date")
    assert resp.status_code == 200
    assert resp.json()["trade_date"] == "2024-01-05"


def test_latest_trade_date_null_when_empty(api):
    client, _ = api
    resp = client.get("/api/market/latest-trade-date")
    assert resp.status_code == 200
    assert resp.json()["trade_date"] is None


# ── /api/analysis/trade-quality ─────────────────────────────────────────────


def _seed_full_context(db, stock_id: str = "2330") -> None:
    _seed_stock(db, stock_id, "台積電", "半導體")
    for i in range(10):
        d = date(2024, 1, 2 + i)
        _seed_industry_flow(db, d, "半導體")
        _seed_price(db, d, stock_id, close=600 + i * 2)
        _seed_flow(db, d, stock_id, "foreign", 1000 * (i + 1))
        _seed_flow(db, d, stock_id, "trust", 500)
        _seed_flow(db, d, stock_id, "dealer", -200)
    db.add(
        MonthlyRevenue(
            revenue_month=date(2023, 12, 31),
            stock_id=stock_id,
            revenue=200000,
            yoy_pct=15.0,
            mom_pct=5.0,
        )
    )
    db.commit()


def test_trade_quality_unavailable_when_openai_key_missing(api):
    client, db = api
    _seed_full_context(db)

    with patch("app.routers.analysis.get_openai_api_key", return_value=""):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["stock_id"] == "2330"
    assert payload["stock_name"] == "台積電"
    assert payload["source"] == "unavailable"
    assert payload["rating"] == "WATCH"
    assert payload["rating_label"] == "再看看"
    assert "OpenAI 服務不可用" in payload["warnings"]


def test_trade_quality_parses_openai_json(api):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "stock": "台積電 (2330)",
        "buy_date": "2024-01-11",
        "classification": "A",
        "classification_reason": "外資連買、均線多頭",
        "action": "BUY",
        "core_logic": "AI 需求帶動晶圓代工訂單",
        "risk_level": "MEDIUM",
        "rating": "STRONG_BUY",
        "summary": "近 10 日法人連買、月營收 YoY +15%，屬結構性成長階段。",
        "target_price_low": 650,
        "target_price_high": 720,
        "time_horizon_days": 60,
        "exit_price_low": None,
        "exit_price_high": None,
        "max_holding_days": None,
        "report_markdown": "## 股票：台積電\n\n完整分析段落...",
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    assert data["rating"] == "STRONG_BUY"
    assert data["rating_label"] == "強烈推薦"
    assert data["classification"] == "A"
    assert data["target_price_low"] == 650
    assert data["target_price_high"] == 720
    assert data["time_horizon_days"] == 60
    assert "台積電" in data["report_markdown"]


def test_trade_quality_resolves_buy_date_when_absent(api):
    client, db = api
    _seed_full_context(db, stock_id="2330")

    captured = {}

    def fake_create(*args, **kwargs):
        messages = kwargs["messages"]
        captured["user_msg"] = messages[1]["content"]
        return MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps({
                    "rating": "NEUTRAL",
                    "summary": "中立",
                    "report_markdown": "report",
                })
            ))]
        )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post("/api/analysis/trade-quality", json={"stock_id": "2330"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["buy_date"] == "2024-01-11"  # last seeded industry flow date
    assert '"buy_date": "2024-01-11"' in captured["user_msg"]


def test_trade_quality_keeps_explicit_buy_date_in_prompt(api):
    client, db = api
    _seed_full_context(db, stock_id="2330")

    captured = {}

    def fake_create(*args, **kwargs):
        messages = kwargs["messages"]
        captured["user_msg"] = messages[1]["content"]
        return MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps({
                    "rating": "NEUTRAL",
                    "summary": "中立",
                    "report_markdown": "report",
                })
            ))]
        )

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-20"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["buy_date"] == "2024-01-20"
    assert '"buy_date": "2024-01-20"' in captured["user_msg"]


def test_trade_quality_falls_back_when_openai_returns_invalid_json(api):
    client, db = api
    _seed_full_context(db)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="not-json"))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "unavailable"
    assert data["rating"] == "WATCH"


def test_trade_quality_retries_once_when_openai_returns_invalid_json(api):
    client, db = api
    _seed_full_context(db)

    valid_payload = {
        "rating": "BUY",
        "summary": "重試後成功",
        "report_markdown": "retry-ok",
        # 帶完整 key_factors 避免又觸發 factors-retry 多打一次 OpenAI
        "key_factors": _FULL_KEY_FACTORS,
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=MagicMock(content='{"rating":"BUY","summary":"broken'))]),
        MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(valid_payload)))]),
    ]

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    assert data["rating"] == "BUY"
    assert data["summary"] == "重試後成功"
    assert mock_client.chat.completions.create.call_count == 2


def test_trade_quality_returns_404_for_unknown_stock(api):
    client, db = api
    _seed_industry_flow(db, date(2024, 1, 5), "半導體")
    db.commit()

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "9999", "buy_date": "2024-01-05"},
        )

    assert resp.status_code == 404


def test_trade_quality_returns_market_not_open_before_today_open(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    db.commit()

    fake_now = datetime(2026, 4, 21, 8, 30)

    with patch("app.routers.analysis._get_taipei_now", return_value=fake_now):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2026-04-21"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "market_not_open"
    assert data["summary"] == "還沒開盤"
    assert "所選日期台股還沒開盤" in data["warnings"]


def test_trade_quality_returns_market_not_open_for_future_date(api):
    client, db = api
    _seed_stock(db, "2330", "台積電")
    db.commit()

    fake_now = datetime(2026, 4, 21, 10, 0)

    with patch("app.routers.analysis._get_taipei_now", return_value=fake_now):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2026-04-22"},
        )

    assert resp.status_code == 200
    assert resp.json()["source"] == "market_not_open"


def test_market_not_open_helper_returns_false_after_open():
    fake_now = datetime(2026, 4, 21, 9, 1)
    assert _is_market_not_open_yet(date(2026, 4, 21), fake_now) is False


def test_trade_quality_prompt_can_be_loaded():
    prompt = _load_system_prompt()
    assert "buy-side research analyst" in prompt
    assert "時空隔離" in prompt


@pytest.mark.parametrize(
    "raw_rating,expected_rating,expected_label",
    [
        ("STRONG_BUY", "STRONG_BUY", "強烈推薦"),
        ("BUY", "BUY", "推薦"),
        ("NEUTRAL", "NEUTRAL", "中立"),
        ("WATCH", "WATCH", "再看看"),
        ("RUN", "RUN", "快跑"),
        ("strong_buy", "STRONG_BUY", "強烈推薦"),  # 小寫也要吃
        ("S", "NEUTRAL", "中立"),  # 誤把產業熱錢等級灌進 rating → fallback
        ("A+", "NEUTRAL", "中立"),
        ("", "NEUTRAL", "中立"),
    ],
)
def test_trade_quality_rating_maps_to_5_tier_labels(api, raw_rating, expected_rating, expected_label):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "rating": raw_rating,
        "classification": "A",
        "summary": "test",
        "report_markdown": "report",
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["rating"] == expected_rating
    assert data["rating_label"] == expected_label


def test_trade_quality_user_message_documents_rating_value_domain(api):
    """User message 必須明講 rating 值域 + classification 不接受 S，避免產業熱錢等級污染。"""
    client, db = api
    _seed_full_context(db)

    captured = {}

    def fake_create(*args, **kwargs):
        captured["user_msg"] = kwargs["messages"][1]["content"]
        return MagicMock(choices=[MagicMock(message=MagicMock(
            content=json.dumps({"rating": "NEUTRAL", "summary": "x", "report_markdown": "y"})
        ))])

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    msg = captured["user_msg"]
    # 5-tier 必列出
    for token in ("STRONG_BUY", "BUY", "NEUTRAL", "WATCH", "RUN"):
        assert token in msg
    # 明講 classification 不接受 S
    assert "classification" in msg
    assert "產業熱錢等級" in msg


def test_trade_quality_user_message_includes_m21_deterministic_block(api):
    """M21 Phase B：user message 必須包含預聚合訊號 JSON，AI 不用自己推產業熱度/籌碼/同儕。"""
    client, db = api
    _seed_full_context(db)

    captured = {}

    def fake_create(*args, **kwargs):
        captured["user_msg"] = kwargs["messages"][1]["content"]
        return MagicMock(choices=[MagicMock(message=MagicMock(
            content=json.dumps({"rating": "NEUTRAL", "summary": "x", "report_markdown": "y"})
        ))])

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    msg = captured["user_msg"]
    assert "[M21 預聚合訊號" in msg
    # 6 section 關鍵字都要出現在序列化 JSON 裡
    for section in (
        "industry_summary",
        "chip_summary",
        "peer_rank",
        "fundamental",
        "price_structure",
        "news_input_stub",
        "data_quality_notes",
    ):
        assert section in msg


def test_trade_quality_uses_short_ttl_cache_for_same_stock_and_date(api):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "rating": "BUY",
        "summary": "cached",
        "report_markdown": "cached-report",
        "key_factors": _FULL_KEY_FACTORS,  # 完整燈號避免 factors-retry 多打一次
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        first = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )
        second = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["summary"] == "cached"
    assert mock_client.chat.completions.create.call_count == 1


# ── /api/analysis/trade-quality/stream（progress bar 用 NDJSON）─────────────


def _parse_ndjson(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_trade_quality_stream_emits_4_stages_in_order(api):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "rating": "BUY",
        "classification": "A",
        "summary": "stream ok",
        "report_markdown": "## stream",
        "target_price_low": 650,
        "target_price_high": 700,
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    events = _parse_ndjson(resp.text)
    stages = [e["stage"] for e in events]
    assert stages == ["collect_raw", "build_context", "openai_call", "done"]

    done = events[-1]
    assert "payload" in done
    payload = done["payload"]
    assert payload["stock_id"] == "2330"
    assert payload["stock_name"] == "台積電"
    assert payload["rating"] == "BUY"
    assert payload["rating_label"] == "推薦"
    assert payload["target_price_low"] == 650
    assert payload["source"] == "openai"


def test_trade_quality_stream_returns_cached_done_event(api):
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "rating": "BUY",
        "summary": "stream cached",
        "report_markdown": "cached",
        "key_factors": _FULL_KEY_FACTORS,  # 完整燈號避免 factors-retry 多打一次
    }
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        first = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )
        assert first.status_code == 200
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    events = _parse_ndjson(resp.text)
    assert [e["stage"] for e in events] == ["done"]
    assert events[0]["label"] == "完成（快取）"
    assert mock_client.chat.completions.create.call_count == 1


def test_trade_quality_stream_sets_proxy_no_buffer_headers(api):
    """Vercel/Render 中間 reverse proxy 不能 buffer NDJSON，否則 progress UX 會被抵消。"""
    client, db = api
    _seed_full_context(db)

    fake_payload = {"rating": "NEUTRAL", "summary": "x", "report_markdown": "y"}
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client):
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("x-accel-buffering") == "no"
    assert resp.headers.get("cache-control") == "no-cache"


def test_trade_quality_stream_returns_404_for_unknown_stock(api):
    """Pre-flight：stock 不存在應該以 HTTP 404 回，不應該開 stream。"""
    client, db = api
    _seed_industry_flow(db, date(2024, 1, 5), "半導體")
    db.commit()

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"):
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "9999", "buy_date": "2024-01-05"},
        )

    assert resp.status_code == 404


def test_trade_quality_stream_market_not_open_returns_single_done_event(api):
    """未開盤情境：仍走 stream 但只 emit 一個 done event，前端 progress bar 直接跳完。"""
    client, db = api
    _seed_stock(db, "2330", "台積電")
    db.commit()

    fake_now = datetime(2026, 4, 21, 8, 30)

    with patch("app.routers.analysis._get_taipei_now", return_value=fake_now):
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "2330", "buy_date": "2026-04-21"},
        )

    assert resp.status_code == 200
    events = _parse_ndjson(resp.text)
    assert len(events) == 1
    assert events[0]["stage"] == "done"
    assert events[0]["payload"]["source"] == "market_not_open"


def test_trade_quality_stream_emits_error_event_when_openai_unavailable(api):
    """OpenAI 不可用走 fallback path：仍以 done event 完成，payload.source = unavailable。"""
    client, db = api
    _seed_full_context(db)

    with patch("app.routers.analysis.get_openai_api_key", return_value=""):
        resp = client.post(
            "/api/analysis/trade-quality/stream",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    events = _parse_ndjson(resp.text)
    stages = [e["stage"] for e in events]
    assert stages[-1] == "done"
    assert events[-1]["payload"]["source"] == "unavailable"
    assert events[-1]["payload"]["rating"] == "WATCH"


def test_trade_quality_falls_back_to_raw_when_m21_context_fails(api):
    """若 build_trade_quality_context 丟非預期例外，M17 分析仍應完成（以 raw-only 為後備）。"""
    client, db = api
    _seed_full_context(db)

    captured = {}

    def fake_create(*args, **kwargs):
        captured["user_msg"] = kwargs["messages"][1]["content"]
        return MagicMock(choices=[MagicMock(message=MagicMock(
            content=json.dumps({"rating": "NEUTRAL", "summary": "fallback", "report_markdown": "r"})
        ))])

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch(
             "app.routers.analysis.build_trade_quality_context",
             side_effect=RuntimeError("context-down"),
         ):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    assert "deterministic 訊號管線暫時不可用，僅以原始資料判斷" in data["warnings"]
    # 既使 fallback 仍要給 AI raw 區塊
    assert "近 5 交易日" in captured["user_msg"]
