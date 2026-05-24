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
    KeyFactor,
    _apply_key_factor_fallback,
    _build_factors_retry_user_msg,
    _build_user_message,
    _coerce_section,
    _derive_rating_from_factors,
    _enforce_deterministic_rating,
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


def _kf(category: str, level: str, trend: str = "stable") -> KeyFactor:
    return KeyFactor(category=category, level=level, trend=trend, note="n")


@pytest.mark.parametrize(
    "levels,expected_rating,expected_classification",
    [
        # B1 deterministic mapping rules
        (["A"] * 6, "STRONG_BUY", "A"),
        (["A", "A", "A", "A", "A", "B"], "STRONG_BUY", "A"),
        (["A", "A", "A", "A", "B", "B"], "BUY", "A"),
        (["A", "A", "A", "B", "B", "B"], "BUY", "B"),
        (["B"] * 6, "NEUTRAL", "B"),
        (["B", "B", "B", "B", "C", "C"], "WATCH", "B"),
        (["C", "C", "C", "B", "B", "B"], "WATCH", "C"),
        (["C"] * 4 + ["B"] * 2, "RUN", "C"),
        (["C"] * 6, "RUN", "C"),
    ],
)
def test_derive_rating_from_factors_counts(levels, expected_rating, expected_classification):
    """B1：rating + classification 都由 key_factors A/C counts 投票決定。"""
    categories = ["industry", "industry_heat", "return", "chip", "technical", "fundamental"]
    factors = [_kf(c, lvl) for c, lvl in zip(categories, levels)]
    rating, classification = _derive_rating_from_factors(factors)
    assert rating == expected_rating
    assert classification == expected_classification


def test_derive_rating_from_factors_returns_none_when_incomplete():
    """B1：key_factors 不齊 6 category → 不推導，caller 應該保留 LLM 原始判斷。"""
    partial = [
        _kf("industry", "A"),
        _kf("chip", "B"),
        _kf("technical", "B"),
    ]
    assert _derive_rating_from_factors(partial) is None


def test_enforce_deterministic_rating_overrides_llm_when_mismatch():
    """B1：LLM 給 STRONG_BUY 但 key_factors 是 6C → 應被覆寫成 RUN + warning。"""
    response = TradeQualityResponse(
        stock_id="2330", stock_name="x", buy_date="2024-01-01",
        rating="STRONG_BUY", rating_label="強烈推薦", classification="A",
        summary="", report_markdown="", source="openai", warnings=[],
        key_factors=[
            _kf("industry", "C"), _kf("industry_heat", "C"), _kf("return", "C"),
            _kf("chip", "C"), _kf("technical", "C"), _kf("fundamental", "C"),
        ],
    )
    out = _enforce_deterministic_rating(response)
    assert out.rating == "RUN"
    assert out.rating_label == "快跑"
    assert out.classification == "C"
    assert any("rating=STRONG_BUY" in w for w in out.warnings)
    assert any("classification=A" in w for w in out.warnings)


def test_enforce_deterministic_rating_keeps_llm_when_aligned():
    """B1：LLM rating 與 deterministic 一致 → 不加 warning、不變動。"""
    response = TradeQualityResponse(
        stock_id="2330", stock_name="x", buy_date="2024-01-01",
        rating="STRONG_BUY", rating_label="強烈推薦", classification="A",
        summary="", report_markdown="", source="openai", warnings=[],
        key_factors=[_kf(c, "A") for c in (
            "industry", "industry_heat", "return", "chip", "technical", "fundamental",
        )],
    )
    out = _enforce_deterministic_rating(response)
    assert out.rating == "STRONG_BUY"
    assert out.classification == "A"
    assert out.warnings == []


def test_enforce_deterministic_rating_noop_when_factors_missing():
    """B1：key_factors 不齊 → 不覆寫（信任 LLM 結果）。"""
    response = TradeQualityResponse(
        stock_id="2330", stock_name="x", buy_date="2024-01-01",
        rating="STRONG_BUY", rating_label="強烈推薦",
        summary="", report_markdown="", source="openai", warnings=[],
        key_factors=None,
    )
    out = _enforce_deterministic_rating(response)
    assert out.rating == "STRONG_BUY"
    assert out.warnings == []


def test_apply_key_factor_fallback_overrides_when_m21_available():
    """A2：m21 可用時即使 LLM 已給 key_factors，仍以 deterministic 覆寫。"""
    response = TradeQualityResponse(
        stock_id="2330", stock_name="x", buy_date="2024-01-01",
        rating="BUY", rating_label="推薦",
        summary="", report_markdown="", source="openai", warnings=[],
        key_factors=[_kf(c, "A") for c in (
            "industry", "industry_heat", "return", "chip", "technical", "fundamental",
        )],
    )
    m21_context = {
        "industry_summary": {
            "industry_hot_level": "C",
            "industry_price_strength": "weak",
            "industry_volume_trend": "declining",
            "industry_institution_flow": "none",
            "is_false_hot": True,
        },
        "chip_summary": {
            "chip_strength": "weak",
            "is_accumulation": False,
            "volume_trend": "declining",
            "investment_trust_buy_days": 0,
            "foreign_buy_days": 0,
        },
        "peer_rank": {"leader_or_follower": "follower", "return_5d_percentile": 0.9},
        "fundamental": {"revenue_yoy": -10.0, "revenue_mom": -5.0},
        "price_structure": {
            "trend": "downtrend", "is_breakout": False,
            "is_consolidation": False, "is_accelerating": False,
        },
    }
    out = _apply_key_factor_fallback(response, m21_context=m21_context)
    # deterministic 應該不全 A：表示 LLM 6A 被覆寫
    out_levels = [f.level for f in out.key_factors]
    assert out_levels.count("A") < 6
    assert any("覆寫 LLM" in w for w in out.warnings)


def test_build_user_message_drops_raw_blocks_when_m21_available():
    """A1：m21 可用時 user_msg 不應包含 raw 5 日 OHLC / 法人 / 月營收 3 段。"""
    ctx = {
        "stock_id": "2330", "stock_name": "台積電", "industry_name": "半導體",
        "sub_industry": "晶圓代工", "buy_date": "2026-05-04", "latest_close": 1000.0,
        "prices_text": "RAW_PRICE_MARKER",
        "flows_text": "RAW_FLOWS_MARKER",
        "revenue_text": "RAW_REVENUE_MARKER",
    }
    m21 = {"industry_summary": {"industry_hot_level": "A"}}
    msg = _build_user_message(ctx, m21_context=m21, warnings=[])
    assert "RAW_PRICE_MARKER" not in msg
    assert "RAW_FLOWS_MARKER" not in msg
    assert "RAW_REVENUE_MARKER" not in msg
    # m21 block 仍應出現
    assert "M21 預聚合訊號" in msg


def test_build_user_message_keeps_raw_blocks_when_m21_unavailable():
    """A1：m21 不可用時 user_msg 必須保留 raw 3 段供 LLM 推論。"""
    ctx = {
        "stock_id": "2330", "stock_name": "台積電", "industry_name": "半導體",
        "sub_industry": "晶圓代工", "buy_date": "2026-05-04", "latest_close": 1000.0,
        "prices_text": "RAW_PRICE_MARKER",
        "flows_text": "RAW_FLOWS_MARKER",
        "revenue_text": "RAW_REVENUE_MARKER",
    }
    msg = _build_user_message(ctx, m21_context=None, warnings=[])
    assert "RAW_PRICE_MARKER" in msg
    assert "RAW_FLOWS_MARKER" in msg
    assert "RAW_REVENUE_MARKER" in msg


def test_factors_retry_user_msg_lists_missing_categories():
    """A7：retry 訊息明列上一輪缺哪些 category，幫助 LLM 補洞而非全部重產。"""
    existing = [_kf("industry", "A"), _kf("chip", "B"), _kf("technical", "B")]
    out = _build_factors_retry_user_msg("ORIGINAL_USER_MSG", existing_factors=existing)
    assert "ORIGINAL_USER_MSG" in out
    # sorted 後輸出，三個都要在；不檢查順序
    for cat in ("industry", "chip", "technical"):
        assert cat in out
    assert "industry_heat" in out and "return" in out and "fundamental" in out  # 缺漏
    # 明示 provided / missing 兩條 label
    assert "已提供" in out and "缺漏必補" in out


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
        # B1：deterministic rating mapping 會根據 key_factors counts 覆寫 LLM rating。
        # 想要保留 LLM 的 STRONG_BUY 必須提供 6 個 level=A 的 key_factors。
        "key_factors": _FULL_KEY_FACTORS,
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    # A2：m21 deterministic 永遠覆寫 LLM key_factors。為了讓本測試聚焦於「LLM 給的
    # 6A → rating=STRONG_BUY」這條路徑，把 _synthesize_key_factors_from_context patch 成 None
    # 模擬 m21 不可用 → 走 LLM key_factors fallback path。
    with patch("app.routers.analysis.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
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

    # A2：disable deterministic synthesizer so LLM key_factors (6A) survive,
    # B1 deterministic rating mapping then produces STRONG_BUY (≥5 A) which still
    # honors the test's intent that LLM rating won the day after retry.
    # 改 expected rating = STRONG_BUY 對齊新 deterministic 行為。
    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "openai"
    # B1：LLM rating=BUY 帶 6A key_factors → deterministic 推導為 STRONG_BUY 覆寫
    assert data["rating"] == "STRONG_BUY"
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
    """Test 聚焦於 `_normalize_response` 的 5-tier 值域映射；B1 deterministic 覆寫不在此範圍，
    用 patch synthesize=None 讓 key_factors 缺、derived rating 為 None → 不覆寫。"""
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
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
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

    # A2 + B1：聚焦 4 stage 順序測試，把 deterministic synthesizer patch 成 None，
    # 避免 deterministic 覆寫 LLM rating（與本測試無關）。
    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
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


# ── M3 tests: _coerce_section + section fields ───────────────────────────────

class TestCoerceSection:
    def test_list_of_strings(self):
        items = ["bullet1", "bullet2", "bullet3"]
        assert _coerce_section(items) == items

    def test_list_strips_empty_items(self):
        assert _coerce_section(["bullet1", "", "  ", "bullet2"]) == ["bullet1", "bullet2"]

    def test_none_returns_none(self):
        assert _coerce_section(None) is None

    def test_empty_list_returns_none(self):
        assert _coerce_section([]) is None

    def test_list_all_empty_items_returns_none(self):
        assert _coerce_section(["", "  "]) is None

    def test_string_splits_by_newline(self):
        result = _coerce_section("bullet1\nbullet2\nbullet3")
        assert result == ["bullet1", "bullet2", "bullet3"]

    def test_string_strips_leading_bullet_chars(self):
        result = _coerce_section("- bullet1\n• bullet2\n· bullet3")
        assert result == ["bullet1", "bullet2", "bullet3"]

    def test_empty_string_returns_none(self):
        assert _coerce_section("") is None
        assert _coerce_section("   ") is None

    def test_single_string_becomes_single_item_list(self):
        result = _coerce_section("only one bullet")
        assert result == ["only one bullet"]


def test_trade_quality_includes_m3_section_fields(api):
    """M3：LLM 回傳 action_one_liner + 6 sections 時，API response 應包含這些欄位。"""
    client, db = api
    _seed_full_context(db)

    sections_payload = {
        "action_one_liner": "建議積極布局，外資連買 + AI 伺服器題材加速",
        "industry_section": ["AI 伺服器產業熱度 S 級", "資金屬 Re-rating Hot"],
        "chip_section": ["外資連買 5 日，籌碼集中", "量能放大 1.8 倍"],
        "fundamental_section": ["月營收 YoY +35%", "EPS 加速成長"],
        "technical_section": ["站上 60 日均線", "突破 1 月高點"],
        "peer_section": ["同產業排名前 10%", "Leader 地位確立"],
        "news_section": ["AI 伺服器需求超預期", "法說上修展望"],
    }

    fake_payload = {
        "rating": "STRONG_BUY",
        "summary": "外資連買 + AI 題材，強勢。",
        "report_markdown": "## 台積電\n分析內容",
        "key_factors": _FULL_KEY_FACTORS,
        **sections_payload,
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action_one_liner"] == "建議積極布局，外資連買 + AI 伺服器題材加速"
    assert data["industry_section"] == ["AI 伺服器產業熱度 S 級", "資金屬 Re-rating Hot"]
    assert data["chip_section"] == ["外資連買 5 日，籌碼集中", "量能放大 1.8 倍"]
    assert data["fundamental_section"] == ["月營收 YoY +35%", "EPS 加速成長"]
    assert data["technical_section"] == ["站上 60 日均線", "突破 1 月高點"]
    assert data["peer_section"] == ["同產業排名前 10%", "Leader 地位確立"]
    assert data["news_section"] == ["AI 伺服器需求超預期", "法說上修展望"]


def test_trade_quality_section_fields_are_none_when_llm_omits(api):
    """LLM 未提供 section 欄位時，回傳的 response section fields 應為 null。"""
    client, db = api
    _seed_full_context(db)

    fake_payload = {
        "rating": "NEUTRAL",
        "summary": "中立。",
        "report_markdown": "## 台積電\n分析",
        "key_factors": _FULL_KEY_FACTORS,
        # 不含任何 section 欄位
    }

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(fake_payload)))]
    )

    with patch("app.routers.analysis.get_openai_api_key", return_value="fake-key"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
        resp = client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action_one_liner"] is None
    assert data["industry_section"] is None
    assert data["chip_section"] is None


def test_trade_quality_user_message_requests_section_fields(api):
    """M3：user message 的 [輸出要求] 應包含 action_one_liner 和 6 section 欄位名稱。"""
    client, db = api
    _seed_full_context(db)

    captured: dict = {}

    def fake_create(*args, **kwargs):
        captured["user_msg"] = kwargs["messages"][1]["content"]
        return MagicMock(choices=[MagicMock(message=MagicMock(
            content=json.dumps({"rating": "NEUTRAL", "summary": "s", "report_markdown": "r"})
        ))])

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = fake_create

    with patch("app.routers.analysis.get_openai_api_key", return_value="k"), \
         patch("app.routers.analysis.OpenAI", return_value=mock_client), \
         patch("app.routers.analysis._synthesize_key_factors_from_context", return_value=None):
        client.post(
            "/api/analysis/trade-quality",
            json={"stock_id": "2330", "buy_date": "2024-01-11"},
        )

    msg = captured.get("user_msg", "")
    assert "action_one_liner" in msg
    assert "industry_section" in msg
    assert "chip_section" in msg
    assert "fundamental_section" in msg
    assert "technical_section" in msg
    assert "peer_section" in msg
    assert "news_section" in msg
