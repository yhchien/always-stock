from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backtest_catalog import DEFAULT_STRATEGY_TEXT
from app.models import DailyPrice
from app.database import get_db
from app.main import app
from app.models import Base

from test_routers_stocks import seed_flow, seed_price, seed_stock

BASE_DATE = date(2024, 1, 1)


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
    client = TestClient(app)
    yield client, session
    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def seed_backtest_dataset(db, stock_id="2330"):
    seed_stock(db, stock_id=stock_id, name="台積電", industry="半導體業")

    for offset in range(30):
        trade_date = BASE_DATE + timedelta(days=offset)
        close = 100.0 if offset < 20 else 120.0 + offset
        seed_price(db, stock_id, trade_date, close, open_p=close)
        foreign_net = 1000 if 20 <= offset <= 22 else (-500 if offset >= 24 else 0)
        seed_flow(db, stock_id, trade_date, "foreign", foreign_net)
        seed_flow(db, stock_id, trade_date, "trust", 0)
        seed_flow(db, stock_id, trade_date, "dealer", 0)

    db.commit()


def test_interpret_backtest_strategy(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["strategy"]["entry_rules"][0]["indicator"] == "close_above_ma"
    assert payload["strategy"]["entry_rules"][1]["indicator"] == "foreign_consecutive_buy"
    assert payload["strategy"]["exit_rules"][1]["indicator"] == "foreign_net_negative"


def test_run_backtest_returns_metrics_and_trades(api):
    client, db = api
    seed_backtest_dataset(db)

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["metrics"]["trade_count"] >= 1
    assert payload["metrics"]["sharpe_ratio"] >= 0
    assert "max_consecutive_wins" in payload["metrics"]
    assert "max_consecutive_losses" in payload["metrics"]
    assert len(payload["equity_curve"]) == 30
    assert payload["trades"][0]["entry_date"] < payload["trades"][0]["exit_date"]
    assert payload["latest_recommendation"]["action"] in {"wait", "observe_buy", "observe_sell", "hold"}
    assert payload["unsupported_conditions"] == []


def test_backtest_templates(api):
    client, _ = api
    response = client.get("/api/backtest/templates")
    assert response.status_code == 200
    payload = response.json()
    assert payload
    assert payload[0]["strategy_text"]


def test_backtest_capabilities(api):
    client, _ = api
    response = client.get("/api/backtest/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["indicators"]
    assert payload["risk_controls"]
    assert any(item["id"] == "ma_golden_cross" for item in payload["indicators"])


def test_backtest_advice_returns_structured_response(api):
    client, _ = api
    response = client.post(
        "/api/backtest/advice",
        json={
            "stock_id": "2330",
            "strategy_text": DEFAULT_STRATEGY_TEXT,
            "normalized_text": "買進：收盤價站上 MA20 且外資連買 3 天；賣出：跌破 MA20 或外資賣超",
            "metrics": {
                "total_return_pct": 10.0,
                "annual_return_pct": 8.0,
                "win_rate_pct": 60.0,
                "max_drawdown_pct": -8.0,
                "sharpe_ratio": 1.1,
                "trade_count": 4,
                "avg_holding_days": 12.0,
            },
            "trades": [],
            "latest_recommendation": {
                "latest_signal_date": "2024-01-30",
                "action": "hold",
                "reason": "目前仍持有部位，且尚未出現新的出場訊號。",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]
    assert payload["strengths"]
    assert payload["rewrite_suggestions"]


def test_interpret_supports_breakout_and_risk_controls(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且突破20日高點就買進；收盤價跌破20日均線或停損8%或停利20%就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["unsupported_conditions"] == []
    assert payload["strategy"]["entry_rules"][1]["indicator"] == "close_breakout_high"
    assert payload["strategy"]["stop_loss_pct"] == 8.0
    assert payload["strategy"]["take_profit_pct"] == 20.0


def test_interpret_supports_ma_cross(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "5日均線黃金交叉20日均線就買進；5日均線死亡交叉20日均線就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["strategy"]["entry_rules"][0]["indicator"] == "ma_golden_cross"
    assert payload["strategy"]["exit_rules"][0]["indicator"] == "ma_dead_cross"


def test_run_backtest_rejects_unknown_conditions(api):
    client, db = api
    seed_backtest_dataset(db)

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且營收創高就買進；收盤價跌破20日均線就賣出",
        },
    )

    assert response.status_code == 422
    assert "Unsupported strategy conditions" in response.json()["detail"]


def test_run_backtest_supports_breakout_rule(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    for offset in range(25):
        trade_date = BASE_DATE + timedelta(days=offset)
        close = 100.0 + offset if offset < 20 else 140.0 + offset
        seed_price(
            db,
            "2330",
            trade_date,
            close,
            open_p=close,
            high=close + 1,
            low=close - 2,
        )
        seed_flow(db, "2330", trade_date, "foreign", 0)
        seed_flow(db, "2330", trade_date, "trust", 0)
        seed_flow(db, "2330", trade_date, "dealer", 0)
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-25",
            "initial_capital": 1000000,
            "strategy_text": "突破20日高點就買進；收盤價跌破5日均線就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["metrics"]["trade_count"] >= 0
    assert payload["unsupported_conditions"] == []


def test_run_backtest_exits_with_stop_loss(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    closes = [100, 100, 100, 100, 110, 100, 90, 89]
    for offset, close in enumerate(closes):
        trade_date = BASE_DATE + timedelta(days=offset)
        seed_price(db, "2330", trade_date, float(close), open_p=float(close), high=float(close) + 1, low=float(close) - 2)
        seed_flow(db, "2330", trade_date, "foreign", 0)
        seed_flow(db, "2330", trade_date, "trust", 0)
        seed_flow(db, "2330", trade_date, "dealer", 0)
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-08",
            "initial_capital": 1000000,
            "strategy_text": "突破3日高點就買進；收盤價跌破20日均線或停損8%就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["trade_count"] == 1
    assert "stop_loss_8pct" in payload["trades"][0]["exit_reason"]


def test_run_backtest_returns_lookback_and_missing_open_warnings(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    for offset in range(10):
        trade_date = BASE_DATE + timedelta(days=offset)
        db.add(DailyPrice(
            stock_id="2330",
            trade_date=trade_date,
            open_price=None,
            high_price=101.0 + offset,
            low_price=99.0 + offset,
            close_price=100.0 + offset,
            volume=1000000,
            turnover=(100.0 + offset) * 1000000,
            avg_price=100.0 + offset,
        ))
        seed_flow(db, "2330", trade_date, "foreign", 1000 if offset >= 2 else 0)
        seed_flow(db, "2330", trade_date, "trust", 0)
        seed_flow(db, "2330", trade_date, "dealer", 0)
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any("開盤價" in warning for warning in warnings)
    assert any("lookback" in warning for warning in warnings)


def test_run_backtest_rejects_invalid_date_range(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-02-01",
            "end_date": "2024-01-01",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 422
    assert "start_date" in response.json()["detail"]


def test_interpret_rejects_blank_strategy(api):
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "   ",
        },
    )

    assert response.status_code == 422
    assert "blank" in response.json()["detail"]


def test_interpret_returns_ai_mapped_conditions_field(api):
    """即使沒有 AI，interpret 回傳值也要有 ai_mapped_conditions 欄位。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "ai_mapped_conditions" in payload
    assert isinstance(payload["ai_mapped_conditions"], list)


def test_run_backtest_returns_ai_mapped_conditions_field(api):
    """run 結果也要有 ai_mapped_conditions 欄位。"""
    client, db = api
    seed_backtest_dataset(db)

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": DEFAULT_STRATEGY_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "ai_mapped_conditions" in payload
    assert isinstance(payload["ai_mapped_conditions"], list)


def test_backtest_templates_include_new_types(api):
    """模板清單應包含均線交叉與突破型。"""
    client, _ = api
    response = client.get("/api/backtest/templates")
    assert response.status_code == 200
    payload = response.json()
    ids = [t["id"] for t in payload]
    assert "ma_golden_cross" in ids
    assert "price_breakout_high" in ids
    assert "triple_ma_trend" in ids
    assert len(payload) >= 7


def test_interpret_supports_volume_ratio(api):
    """成交量暴增至 N 日均量的 X 倍以上解析正確。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "突破20日高點且成交量暴增至20日均量的1.5倍以上就買進；收盤價跌破20日均線就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    entry_ids = [r["indicator"] for r in payload["strategy"]["entry_rules"]]
    assert "volume_ratio_above_ma" in entry_ids
    vol_rule = next(r for r in payload["strategy"]["entry_rules"] if r["indicator"] == "volume_ratio_above_ma")
    assert vol_rule["params"]["ratio"] == 1.5


def test_interpret_supports_all_inst_and_net_positive(api):
    """三大法人合計轉賣 / 外資買超 / 投信買超 解析正確。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且外資買超且投信買超就買進；收盤價跌破20日均線或三大法人合計轉賣就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    entry_ids = [r["indicator"] for r in payload["strategy"]["entry_rules"]]
    assert "foreign_net_positive" in entry_ids
    assert "trust_net_positive" in entry_ids
    exit_ids = [r["indicator"] for r in payload["strategy"]["exit_rules"]]
    assert "all_inst_net_negative" in exit_ids


def test_interpret_supports_consecutive_sell(api):
    """外資連賣 N 天解析正確。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線就買進；外資連賣3天或收盤價跌破20日均線就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    exit_ids = [r["indicator"] for r in payload["strategy"]["exit_rules"]]
    assert "foreign_consecutive_sell" in exit_ids


def test_interpret_accepts_entry_exit_fields(api):
    """新流程：entry_text / exit_text / stop_loss_pct / take_profit_pct 分離輸入。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "entry_text": "收盤價站上20日均線且外資連買3天",
            "exit_text": "收盤價跌破20日均線",
            "stop_loss_pct": 8.0,
            "take_profit_pct": 20.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    entry_ids = [r["indicator"] for r in payload["strategy"]["entry_rules"]]
    assert "close_above_ma" in entry_ids
    assert "foreign_consecutive_buy" in entry_ids
    exit_ids = [r["indicator"] for r in payload["strategy"]["exit_rules"]]
    assert "close_below_ma" in exit_ids
    assert payload["strategy"]["stop_loss_pct"] == 8.0
    assert payload["strategy"]["take_profit_pct"] == 20.0


def test_interpret_accepts_candlestick_pattern_keyword(api):
    """K 棒型態關鍵字可直接被 rule-based parser 識別。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    db.commit()

    response = client.post(
        "/api/backtest/interpret",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "initial_capital": 1000000,
            "entry_text": "出現紅三兵",
            "exit_text": "出現三隻烏鴉",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    entry_ids = [r["indicator"] for r in payload["strategy"]["entry_rules"]]
    exit_ids = [r["indicator"] for r in payload["strategy"]["exit_rules"]]
    assert "candle_three_white_soldiers" in entry_ids
    assert "candle_three_black_crows" in exit_ids


def test_run_backtest_chip_ma_resonance_template(api):
    """均線 + 籌碼共振型模板（外資買超 + 投信買超）可正常執行。"""
    client, db = api
    seed_stock(db, stock_id="2330", name="台積電", industry="半導體業")
    for offset in range(30):
        trade_date = BASE_DATE + timedelta(days=offset)
        close = 100.0 + offset
        seed_price(db, "2330", trade_date, close, open_p=close)
        seed_flow(db, "2330", trade_date, "foreign", 500 if offset >= 20 else 0)
        seed_flow(db, "2330", trade_date, "trust", 200 if offset >= 20 else 0)
        seed_flow(db, "2330", trade_date, "dealer", 0)
    db.commit()

    response = client.post(
        "/api/backtest/run",
        json={
            "stock_id": "2330",
            "start_date": "2024-01-01",
            "end_date": "2024-01-30",
            "initial_capital": 1000000,
            "strategy_text": "收盤價站上20日均線且外資買超且投信買超就買進；收盤價跌破20日均線或三大法人合計轉賣就賣出",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] is True
    assert payload["unsupported_conditions"] == []
