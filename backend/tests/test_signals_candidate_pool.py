"""M23 slice 5：candidate_pool 整合測試（in-memory SQLite）。

驗證 spec §5 Step 1～4 + §6：
  - ingest_data 取窗口元資料
  - compute_rankings 取產業 / 個股 3d 排行
  - build_candidate_pool 聯集 + 擴散 + 過濾 + deterministic ordering（不截斷）
  - per-stock metrics（price_change_*、量能比率、法人累計、margin ratio）
"""
from datetime import date

import pytest

from app.models import (
    DailyPrice,
    EtfClassification,
    IndustryDailyFlow,
    InstStockFlow,
    MarginTrade,
    SignalWatchHit,
    StockMaster,
)
from app.signals import candidate_pool as cp_mod
from app.signals.candidate_pool import (
    build_candidate_pool,
    compute_rankings,
    ingest_data,
)


# ---------- helpers ----------


def _seed_master(db, stock_id, name, industry, sub=None, active=True):
    db.add(
        StockMaster(
            stock_id=stock_id,
            stock_name=name,
            industry_name=industry,
            sub_industry=sub,
            is_active=active,
        )
    )


def _seed_price(
    db,
    trade_date,
    stock_id,
    *,
    open_p=100.0,
    high=101.0,
    low=99.0,
    close=100.0,
    volume=1000.0,
    turnover=1.0e8,
):
    db.add(
        DailyPrice(
            trade_date=trade_date,
            stock_id=stock_id,
            open_price=open_p,
            high_price=high,
            low_price=low,
            close_price=close,
            volume=volume,
            turnover=turnover,
        )
    )


def _seed_flow(db, trade_date, stock_id, inst_type, net_amount):
    db.add(
        InstStockFlow(
            trade_date=trade_date,
            stock_id=stock_id,
            inst_type=inst_type,
            buy_shares=0,
            sell_shares=0,
            net_shares=0,
            buy_amount_est=max(net_amount, 0),
            sell_amount_est=abs(min(net_amount, 0)),
            net_amount_est=net_amount,
        )
    )


def _seed_industry_flow(db, trade_date, industry, net_amount):
    db.add(
        IndustryDailyFlow(
            trade_date=trade_date,
            industry_name=industry,
            total_buy_amount=max(net_amount, 0),
            total_sell_amount=abs(min(net_amount, 0)),
            total_net_amount=net_amount,
            foreign_net_amount=net_amount,
            trust_net_amount=0,
            dealer_net_amount=0,
        )
    )


def _seed_full_market(db, dates, stocks, *, flow_per_day=1.0e8):
    """為每檔每日種一筆 price + 三大法人 flow，確保 hot_money 排得出 dates。"""
    for sid, master in stocks.items():
        _seed_master(db, sid, master["name"], master["industry"], master.get("sub"))
    for d in dates:
        for sid in stocks:
            _seed_price(db, d, sid)
            _seed_flow(db, d, sid, "foreign", flow_per_day)


# ---------- ingest_data ----------


def test_ingest_data_returns_empty_when_no_flow(db):
    out = ingest_data(db, date(2026, 4, 25))
    assert out["target_date"] == date(2026, 4, 25)
    assert out["trade_dates_60d"] == []
    assert out["stocks_master"] == {}


def test_ingest_data_only_returns_active_masters(db):
    _seed_master(db, "2330", "台積電", "半導體業", active=True)
    _seed_master(db, "9999", "已下市", "其他", active=False)
    # 至少要一筆 flow 才能建立 trade_dates
    _seed_flow(db, date(2026, 4, 25), "2330", "foreign", 1.0)
    db.commit()

    out = ingest_data(db, date(2026, 4, 25))

    assert "2330" in out["stocks_master"]
    assert "9999" not in out["stocks_master"]


def test_ingest_data_windows_are_correct_subsets(db):
    _seed_master(db, "2330", "台積電", "半導體業")
    for i in range(1, 11):  # 10 個交易日
        _seed_flow(db, date(2026, 4, i), "2330", "foreign", 1.0)
    db.commit()

    out = ingest_data(db, date(2026, 4, 10))

    assert len(out["trade_dates_3d"]) == 3
    assert len(out["trade_dates_5d"]) == 5
    assert len(out["trade_dates_10d"]) == 10
    assert out["trade_dates_3d"] == out["trade_dates_60d"][-3:]


# ---------- compute_rankings ----------


def test_compute_rankings_picks_top_industries_by_3d_net(db):
    stocks = {
        "2330": {"name": "台積電", "industry": "半導體業"},
        "1101": {"name": "台泥", "industry": "水泥"},
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
        _seed_industry_flow(db, d, "水泥", 1.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    inds = [r["industry_name"] for r in rankings["top_industries_3d"]]
    assert inds[0] == "半導體"
    assert inds[1] == "水泥"
    sem = next(r for r in rankings["top_industries_3d"] if r["industry_name"] == "半導體")
    assert sem["stock_count"] == 1


def test_compute_rankings_top_stocks_uses_hot_money_service(db):
    stocks = {
        "2330": {"name": "台積電", "industry": "半導體業"},
        "1101": {"name": "台泥", "industry": "水泥"},
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    # 加碼 2330 變成第一
    for d in dates:
        _seed_flow(db, d, "2330", "trust", 5.0e8)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    ids = [s["stock_id"] for s in rankings["top_stocks_3d"]]
    assert ids[0] == "2330"


def test_compute_rankings_handles_empty_ingestion(db):
    out = compute_rankings(db, date(2026, 4, 25), {"trade_dates_3d": [], "stocks_master": {}})
    assert out == {"top_industries_3d": [], "top_stocks_3d": []}


def test_compute_rankings_includes_financial_industries_by_same_rule(db):
    """P2：金融類產業淨買超最高時必須正常取得 A 通道產業名次。"""
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "金融保險業", 9.0e9)
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
        _seed_industry_flow(db, d, "水泥", 1.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    inds = [r["industry_name"] for r in rankings["top_industries_3d"]]
    assert inds[0] == "金融保險業"
    assert "半導體" in inds
    assert "水泥" in inds


def test_compute_rankings_financial_industry_still_obeys_today_sell_brake(db):
    stocks = {"2880": {"name": "華南金", "industry": "金融保險業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    _seed_industry_flow(db, dates[0], "金融保險業", 9.0e9)
    _seed_industry_flow(db, dates[1], "金融保險業", 9.0e9)
    _seed_industry_flow(db, dates[2], "金融保險業", -5.0e9)
    db.commit()

    ingestion = ingest_data(db, dates[-1])
    rankings = compute_rankings(db, dates[-1], ingestion)
    assert "金融保險業" not in {
        row["industry_name"] for row in rankings["top_industries_3d"]
    }


def test_compute_rankings_industry_ranking_uses_two_day_window(db):
    """排序窗 = 2 日：只有最舊一天爆量的產業不該贏過近 2 日穩定買超的產業。"""
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    # A：只有最舊一天爆量 → 3 日總額高(10.2e9)、近 2 日低(0.2e9)
    _seed_industry_flow(db, dates[0], "塑膠", 10.0e9)
    _seed_industry_flow(db, dates[1], "塑膠", 0.1e9)
    _seed_industry_flow(db, dates[2], "塑膠", 0.1e9)
    # B：近 2 日穩定買超 → 2 日總額高(10e9)
    _seed_industry_flow(db, dates[0], "鋼鐵", 0.0)
    _seed_industry_flow(db, dates[1], "鋼鐵", 5.0e9)
    _seed_industry_flow(db, dates[2], "鋼鐵", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    nets = sorted((r["net_3d"] for r in rankings["top_industries_3d"]), reverse=True)
    # 2 日窗：鋼鐵 10e9 居首、塑膠近 2 日僅 0.2e9；若是 3 日窗塑膠會以 10.2e9 居首
    assert nets[0] == pytest.approx(10.0e9)
    assert min(nets) == pytest.approx(0.2e9)


def test_compute_rankings_drops_industry_in_today_sell_blacklist(db):
    """三日買超前段的產業，若當日淨賣超落在賣超前 N → 剔除（不回補）。"""
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    # 半導體三日累計仍正（前兩天大買、當日大賣）；水泥三日穩定買超
    _seed_industry_flow(db, dates[0], "半導體業", 9.0e9)
    _seed_industry_flow(db, dates[1], "半導體業", 9.0e9)
    _seed_industry_flow(db, dates[2], "半導體業", -5.0e9)  # 當日大賣超
    for d in dates:
        _seed_industry_flow(db, d, "水泥", 1.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    inds = [r["industry_name"] for r in rankings["top_industries_3d"]]
    # 半導體三日淨額最高，但當日 -5e9 賣超 → 被剔除
    assert "半導體" not in inds
    # 水泥當日買超，不在賣超黑名單 → 保留
    assert "水泥" in inds


# ---------- build_candidate_pool ----------


def test_build_candidate_pool_unions_top_stocks_and_industry_members(db):
    stocks = {
        "2330": {"name": "台積電", "industry": "半導體業"},
        "2454": {"name": "聯發科", "industry": "半導體業"},
        "9999": {"name": "孤兒股", "industry": "其他"},
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
        _seed_industry_flow(db, d, "其他", 1.0e8)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    ids = {c["stock_id"] for c in pool}
    # 半導體業在 top → 2330 / 2454 全列入
    assert "2330" in ids
    assert "2454" in ids


def test_build_candidate_pool_no_longer_drops_etf_and_financial(db):
    """2026-07-22（LLM v6 contract 對齊）：ETF / 金融股不再因資產類型被 Step 1
    排除——Phase 2 hard exclusion 已經確認資產類型不該是排除理由，候選池這裡
    也要跟進，否則會出現「hard exclusion 說可以進，candidate pool 卻不讓進」
    的矛盾。三檔都應該進候選池，並各自帶正確的 `asset_type`。"""
    stocks = {
        "0050": {"name": "元大台灣 50", "industry": "ETF"},
        "2330": {"name": "台積電", "industry": "半導體業"},
        "2880": {"name": "華南金", "industry": "金融保險業"},
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "ETF", 5.0e9)
        _seed_industry_flow(db, d, "半導體業", 4.0e9)
        _seed_industry_flow(db, d, "金融保險業", 3.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    by_id = {c["stock_id"]: c for c in pool}
    assert "0050" in by_id
    assert "2880" in by_id
    assert "2330" in by_id

    assert by_id["0050"]["asset_type"] == "ETF"
    assert by_id["0050"]["is_etf"] is True
    assert by_id["0050"]["is_financial"] is False

    assert by_id["2880"]["asset_type"] == "FINANCIAL"
    assert by_id["2880"]["is_financial"] is True
    assert by_id["2880"]["is_etf"] is False

    assert by_id["2330"]["asset_type"] == "COMMON_STOCK"
    assert by_id["2330"]["is_etf"] is False
    assert by_id["2330"]["is_financial"] is False


def test_build_candidate_pool_prefers_reliable_etf_classification(db):
    """非 00、名稱無 ETF 關鍵字時，canonical table 仍可可靠辨識 ETF。"""
    _seed_master(db, "T123", "全球市場基金", "其他")
    d = date(2026, 4, 22)
    _seed_price(db, d, "T123")
    _seed_flow(db, d, "T123", "foreign", 1.0e8)
    db.add(
        EtfClassification(
            stock_id="T123",
            asset_type="ETF",
            asset_class="EQUITY",
            region="GLOBAL",
            strategy="BROAD_MARKET",
            themes=[],
            is_leveraged=False,
            is_inverse=False,
            is_active=True,
            classification_confidence="HIGH",
            mapping_version="v1",
        )
    )
    db.commit()
    ingestion = ingest_data(db, d)
    pool = build_candidate_pool(
        db,
        d,
        ingestion,
        {"top_industries_3d": [], "top_stocks_3d": [{"stock_id": "T123"}]},
        momentum_frame={"T123": cp_mod.momentum.empty_momentum_features()},
    )
    assert pool[0]["asset_type"] == "ETF"
    assert pool[0]["fundamental_applicability"] == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    "channels,expected_sources",
    [
        ({"price_momentum": ["2330"], "acceleration": [], "fundamental": []}, ["B"]),
        ({"price_momentum": [], "acceleration": ["2330"], "fundamental": []}, ["C"]),
        ({"price_momentum": [], "acceleration": [], "fundamental": ["2330"]}, ["D"]),
        (
            {
                "price_momentum": ["2330"],
                "acceleration": ["2330"],
                "fundamental": ["2330"],
            },
            ["B", "C", "D"],
        ),
    ],
)
def test_build_candidate_pool_supports_b_c_d_without_a(
    db,
    monkeypatch,
    channels,
    expected_sources,
):
    _seed_master(db, "2330", "台積電", "半導體業")
    d = date(2026, 4, 22)
    _seed_price(db, d, "2330")
    _seed_flow(db, d, "2330", "foreign", 1.0e8)
    db.commit()
    monkeypatch.setattr(cp_mod.momentum, "select_momentum_candidates", lambda _: channels)

    ingestion = ingest_data(db, d)
    pool = build_candidate_pool(
        db,
        d,
        ingestion,
        {"top_industries_3d": [], "top_stocks_3d": []},
        momentum_frame={"2330": cp_mod.momentum.empty_momentum_features()},
    )
    assert len(pool) == 1
    assert pool[0]["source_A"] is False
    assert pool[0]["candidate_sources"] == expected_sources


def test_build_candidate_pool_still_drops_manual_blacklist(db, monkeypatch):
    """人工黑名單仍然是唯一在 Step 1 排除候選的理由。"""
    from app.signals import exclusions

    stocks = {
        "9999": {"name": "黑名單測試股", "industry": "半導體業"},
        "2330": {"name": "台積電", "industry": "半導體業"},
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "半導體業", 4.0e9)
    db.commit()

    monkeypatch.setattr(exclusions, "EXCLUSION_BLACKLIST", {"9999"})

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    ids = {c["stock_id"] for c in pool}
    assert "9999" not in ids
    assert "2330" in ids


def test_build_candidate_pool_attaches_industry_rankings(db):
    """半導體業放兩檔，price_change_5d 應有 1/2 排名 + industry_count=2。"""
    stocks = {
        "2330": {"name": "台積電", "industry": "半導體業"},
        "2454": {"name": "聯發科", "industry": "半導體業"},
    }
    dates = [date(2026, 4, d) for d in range(13, 23)]  # 10 個交易日

    # seed master
    for sid, master in stocks.items():
        _seed_master(db, sid, master["name"], master["industry"])

    # 2330 5d 上漲：close 從 100 → 110；2454 從 100 → 105
    for i, d in enumerate(dates):
        _seed_price(db, d, "2330", close=100 + i, volume=2000.0)
        _seed_price(db, d, "2454", close=100 + i * 0.5, volume=1000.0)
        _seed_flow(db, d, "2330", "foreign", 1.0e8)
        _seed_flow(db, d, "2454", "foreign", 5.0e7)
    for d in dates[-3:]:
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    by_id = {c["stock_id"]: c for c in pool}
    assert by_id["2330"]["industry_count"] == 2
    assert by_id["2454"]["industry_count"] == 2
    # 2330 漲幅較大 → industry_rank_5d=1
    assert by_id["2330"]["industry_rank_5d"] == 1
    assert by_id["2454"]["industry_rank_5d"] == 2


def test_build_candidate_pool_metrics_include_price_change_and_volume_ratio(db):
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, d) for d in range(13, 23)]
    for sid, master in stocks.items():
        _seed_master(db, sid, master["name"], master["industry"])
    # 9 天量能 1000、最後一天 5000 → 5d 平均高，1d/60d 高
    for i, d in enumerate(dates[:-1]):
        _seed_price(db, d, "2330", close=100, volume=1000)
        _seed_flow(db, d, "2330", "foreign", 1.0e8)
    _seed_price(db, dates[-1], "2330", close=110, volume=5000, turnover=2.0e9)
    _seed_flow(db, dates[-1], "2330", "foreign", 1.0e8)
    for d in dates[-3:]:
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    # 9 天量 1000 + 1 天量 5000：60d_avg=1400, 5d_avg=(4*1000+5000)/5=1800
    # ratio_5d_to_60d = 1800/1400 ≈ 1.286
    assert cand["volume_5d_to_60d_ratio"] is not None
    assert cand["volume_5d_to_60d_ratio"] > 1.2
    # 5d 漲幅：close[-1]=110, close[-6]=100 → +10%
    assert cand["price_change_5d"] is not None
    assert cand["price_change_5d"] == pytest.approx(10.0)


def test_build_candidate_pool_consecutive_buy_days_3d_count(db):
    """近 3 日法人合計 +/-/+，連買日數應為 2。"""
    _seed_master(db, "2330", "台積電", "半導體業")
    dates = [date(2026, 4, d) for d in range(13, 23)]
    for d in dates[:-3]:
        _seed_price(db, d, "2330")
        _seed_flow(db, d, "2330", "foreign", 1.0e8)
    nets = [1.0e8, -1.0e8, 1.0e8]  # 3d：正、負、正 → 2 天連買
    for d, n in zip(dates[-3:], nets):
        _seed_price(db, d, "2330")
        _seed_flow(db, d, "2330", "foreign", n)
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["consecutive_buy_days_3d"] == 2


@pytest.mark.parametrize("candidate_count", [100, 180])
def test_build_candidate_pool_keeps_full_union_in_deterministic_order(db, candidate_count):
    """P1：raw union 不論是否超過舊 150 trigger，所有候選都必須保留。"""
    stocks = {
        f"{2000 + i}": {"name": f"S{i}", "industry": "半導體業"}
        for i in range(candidate_count)
    }
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    # 不同股票不同法人累計，驗證完整結果仍以相同 deterministic key 排序。
    for d in dates:
        for i, sid in enumerate(stocks):
            _seed_flow(db, d, sid, "trust", (candidate_count - i) * 1.0e8)
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    # Directly define A's selected industry so the fixture tests union retention,
    # independently of compute_rankings' industry-name canonicalization.
    rankings = {
        "top_industries_3d": [{"industry_name": "半導體業"}],
        "top_stocks_3d": [],
    }
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    assert len(pool) == candidate_count
    expected = sorted(
        pool,
        key=lambda c: (
            -(c.get("momentum_score") or 0.0),
            -(c.get("total_institution_flow_3d") or 0.0),
            str(c.get("stock_id") or ""),
        ),
    )
    assert pool == expected
    assert all(c["source_A"] for c in pool)
    assert all("A" in c["candidate_sources"] for c in pool)


def test_build_candidate_pool_preserves_multi_channel_source_union(db, monkeypatch):
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()
    monkeypatch.setattr(
        cp_mod.momentum,
        "select_momentum_candidates",
        lambda frame: {
            "price_momentum": ["2330"],
            "acceleration": ["2330"],
            "fundamental": ["2330"],
        },
    )
    ingestion = ingest_data(db, date(2026, 4, 22))
    momentum_frame = {"2330": cp_mod.momentum.empty_momentum_features()}
    rankings = {
        "top_industries_3d": [{"industry_name": "半導體業"}],
        "top_stocks_3d": [{"stock_id": "2330"}],
    }

    pool = build_candidate_pool(
        db,
        date(2026, 4, 22),
        ingestion,
        rankings,
        momentum_frame=momentum_frame,
    )

    assert len(pool) == 1
    assert pool[0]["candidate_sources"] == ["A", "B", "C", "D"]
    assert all(pool[0][f"source_{source}"] for source in ("A", "B", "C", "D"))


def test_build_candidate_pool_returns_empty_when_no_inputs(db):
    out = build_candidate_pool(
        db,
        date(2026, 4, 25),
        {"target_date": date(2026, 4, 25), "stocks_master": {}, "trade_dates_3d": []},
        {"top_industries_3d": [], "top_stocks_3d": []},
    )
    assert out == []


# ---------- tracking_status（再偵測閘門，2026-05-26）----------


def _seed_signal_watch_hit(
    db,
    *,
    stock_id,
    snapshot_date,
    max_positive_return_pct=None,
    max_negative_return_pct=None,
    signal_type="LEADER",
    industry="半導體業",
):
    """最精簡的 SignalWatchHit seed（補必填欄位 reason/theme/group_info/leader_check/signals）。"""
    db.add(
        SignalWatchHit(
            snapshot_date=snapshot_date,
            stock_id=stock_id,
            stock_name=f"S{stock_id}",
            signal_type=signal_type,
            industry_name=industry,
            reason="test reason",
            theme={},
            group_info={},
            leader_check={},
            signals={},
            max_positive_return_pct=max_positive_return_pct,
            max_negative_return_pct=max_negative_return_pct,
        )
    )


def _seed_min_candidate_for(db, sid, *, industry="半導體業", dates=None):
    """為 tracking_status 測試 seed 一檔最小可進候選池的股票（master + price + flow）。"""
    if dates is None:
        dates = [date(2026, 4, d) for d in range(13, 23)]  # 10 個交易日
    _seed_master(db, sid, f"S{sid}", industry)
    for d in dates:
        _seed_price(db, d, sid)
        _seed_flow(db, d, sid, "foreign", 1.0e8)
    for d in dates[-3:]:
        _seed_industry_flow(db, d, industry, 5.0e9)


def test_tracking_status_defaults_when_no_prior_hits(db):
    """從未被抓過 → is_tracked=False / 各欄位 None / failed_follow_through=False。"""
    _seed_min_candidate_for(db, "2330")
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["is_tracked"] is False
    assert cand["first_seen_date"] is None
    assert cand["days_since_first_seen"] is None
    assert cand["hit_count"] is None
    assert cand["max_positive_return_pct"] is None
    assert cand["max_negative_return_pct"] is None
    assert cand["failed_follow_through"] is False


def test_tracking_status_populated_from_signal_watch_hits(db):
    """SignalWatchHit 有資料 → 欄位正確填上，failed_follow_through 計算正確。

    Scenario: 2330 在 4/13 首次抓到，到 4/22 已 7 個交易日，max_pos=2.5 / max_neg=-7.0
    → days_since=7 >= 3, max_pos<3, max_neg<-6 → failed_follow_through=True
    """
    _seed_min_candidate_for(db, "2330")
    _seed_signal_watch_hit(
        db,
        stock_id="2330",
        snapshot_date=date(2026, 4, 13),
        max_positive_return_pct=2.5,
        max_negative_return_pct=-7.0,
    )
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["is_tracked"] is True
    assert cand["first_seen_date"] == date(2026, 4, 13)
    # 4/14, 4/15, ..., 4/22 = 9 個交易日（不含 4/13 當天）
    assert cand["days_since_first_seen"] == 9
    assert cand["hit_count"] == 1
    assert cand["max_positive_return_pct"] == 2.5
    assert cand["max_negative_return_pct"] == -7.0
    assert cand["failed_follow_through"] is True


def test_tracking_status_not_failed_when_days_under_threshold(db):
    """days_since=2 → 還沒到 3 個交易日驗證期 → failed_follow_through=False。"""
    _seed_min_candidate_for(db, "2330")
    # 4/20 首次抓到，target 4/22 → days_since=2
    _seed_signal_watch_hit(
        db,
        stock_id="2330",
        snapshot_date=date(2026, 4, 20),
        max_positive_return_pct=0.5,
        max_negative_return_pct=-8.0,  # 已經 -8 但天數還沒到
    )
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["days_since_first_seen"] == 2
    assert cand["failed_follow_through"] is False


def test_tracking_status_not_failed_when_max_positive_passes_threshold(db):
    """max_pos=3.5 ≥ 3.0 → 已驗證主升段啟動 → failed=False。"""
    _seed_min_candidate_for(db, "2330")
    _seed_signal_watch_hit(
        db,
        stock_id="2330",
        snapshot_date=date(2026, 4, 14),
        max_positive_return_pct=3.5,
        max_negative_return_pct=-7.0,
    )
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["failed_follow_through"] is False


def test_tracking_status_not_failed_when_max_negative_above_threshold(db):
    """max_neg=-5.0 > -6.0 → 回撤可控 → failed=False。"""
    _seed_min_candidate_for(db, "2330")
    _seed_signal_watch_hit(
        db,
        stock_id="2330",
        snapshot_date=date(2026, 4, 14),
        max_positive_return_pct=2.0,
        max_negative_return_pct=-5.0,
    )
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["failed_follow_through"] is False


def test_tracking_status_hit_count_counts_distinct_snapshot_dates(db):
    """3 筆 hits（不同 snapshot_date）→ hit_count=3。"""
    _seed_min_candidate_for(db, "2330")
    for d in (date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 16)):
        _seed_signal_watch_hit(
            db,
            stock_id="2330",
            snapshot_date=d,
            max_positive_return_pct=1.0,
            max_negative_return_pct=-2.0,
        )
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    cand = next(c for c in pool if c["stock_id"] == "2330")
    assert cand["hit_count"] == 3
    # first_seen 為最早的 snapshot
    assert cand["first_seen_date"] == date(2026, 4, 14)


# ---------- v2.1 fishtail momentum upgrade：B/C 通道 + momentum_score ----------


def _seed_momentum_market(db):
    """22 檔兩產業、30 日、斜率遞增；只有半導體（S01~S11）有法人 flow 與產業 flow。

    S22（電子零組件、全市場動能最強）完全沒有法人買超 → 唯一進池路徑是
    v2.1 價格動能通道（rs_market_percentile_20d >= 85）。
    """
    from datetime import timedelta

    start = date(2026, 6, 1)
    dates = [start + timedelta(days=i) for i in range(30)]
    stock_ids = ["S%02d" % i for i in range(1, 23)]

    for idx, sid in enumerate(stock_ids, start=1):
        industry = "半導體" if idx <= 11 else "電子零組件"
        _seed_master(db, sid, "股票" + sid, industry)

    for d_idx, d in enumerate(dates):
        for s_idx, sid in enumerate(stock_ids, start=1):
            close = 100.0 + s_idx * 0.5 * d_idx
            _seed_price(db, d, sid, close=close, volume=2_000_000.0, turnover=2.0e8)
        for s_idx, sid in enumerate(stock_ids[:11], start=1):
            _seed_flow(db, d, sid, "foreign", 1.0e7 * s_idx)
        _seed_industry_flow(db, d, "半導體", 5.0e8)
    db.commit()
    return dates


def test_v21_price_momentum_channel_adds_stock_without_institution_flow(db):
    dates = _seed_momentum_market(db)
    target = dates[-1]

    ingestion = ingest_data(db, target)
    rankings = compute_rankings(db, target, ingestion)
    pool = build_candidate_pool(db, target, ingestion, rankings)

    by_id = {c["stock_id"]: c for c in pool}
    # S22 無任何法人 flow、產業也不在 top industries → 只能靠價格動能通道進池
    assert "S22" in by_id
    assert by_id["S22"]["in_price_momentum_pool"] is True
    assert by_id["S22"]["in_top_stocks_3d"] is False
    assert by_id["S22"]["in_top_industries_3d"] is False


def test_v21_every_candidate_has_momentum_score_and_rs_fields(db):
    dates = _seed_momentum_market(db)
    target = dates[-1]

    ingestion = ingest_data(db, target)
    rankings = compute_rankings(db, target, ingestion)
    pool = build_candidate_pool(db, target, ingestion, rankings)

    assert pool  # 池不為空
    for c in pool:
        assert "momentum_score" in c
        assert c["momentum_score"] is not None
        assert "momentum_score_detail" in c
        assert "rs_market_percentile_20d" in c
        assert "rs_industry_percentile_20d" in c
        assert "rs_rank_improvement_5d" in c


# ---------- v2.2 episode-aware hit_count（spec §7.4） ----------


def test_episode_counts_single_episode():
    """連續命中（間隔 <= 3 交易日）→ 同一 episode。"""
    from datetime import timedelta
    from app.signals.candidate_pool import _episode_counts

    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(30)]
    trade_index = {d: i for i, d in enumerate(days)}
    # 命中 day0, day2, day5（gap 1 與 2，都 < 5）
    hits = [days[0], days[2], days[5]]
    consecutive, independent = _episode_counts(hits, trade_index)
    assert independent == 1
    assert consecutive == 3


def test_episode_counts_new_episode_after_5_day_gap():
    """兩次命中間未命中 >= 5 個交易日 → 新獨立 episode。"""
    from datetime import timedelta
    from app.signals.candidate_pool import _episode_counts

    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(30)]
    trade_index = {d: i for i, d in enumerate(days)}
    # 命中 day0, day1（episode 1）→ 中斷 6 天 → day8, day9（episode 2）
    hits = [days[0], days[1], days[8], days[9]]
    consecutive, independent = _episode_counts(hits, trade_index)
    assert independent == 2
    assert consecutive == 2  # 當前（最新）episode 的命中次數


def test_episode_counts_gap_4_days_stays_same_episode():
    """4 天未命中是模糊帶 → 依 spec「至少 5 天才算新」歸同一 episode。"""
    from datetime import timedelta
    from app.signals.candidate_pool import _episode_counts

    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(30)]
    trade_index = {d: i for i, d in enumerate(days)}
    hits = [days[0], days[5]]  # gap = 4 個未命中交易日
    consecutive, independent = _episode_counts(hits, trade_index)
    assert independent == 1
    assert consecutive == 2


def test_tracking_status_includes_episode_counts(db):
    """整合：_load_tracking_status 回傳 consecutive / independent hit counts。"""
    from datetime import timedelta
    from app.signals.candidate_pool import _load_tracking_status

    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(20)]
    _seed_master(db, "2330", "台積電", "半導體")
    for d in days:
        _seed_price(db, d, "2330")
    # 命中 day0、day1（episode 1）→ 中斷 >= 5 → day10（episode 2）
    for d in [days[0], days[1], days[10]]:
        db.add(
            SignalWatchHit(
                snapshot_date=d, stock_id="2330", stock_name="台積電",
                signal_type="LEADER", reason="r", theme={}, group_info={},
                leader_check={}, signals={},
            )
        )
    db.commit()

    out = _load_tracking_status(db, ["2330"], days[-1])
    ts = out["2330"]
    assert ts["hit_count"] == 3
    assert ts["independent_hit_count"] == 2
    assert ts["consecutive_hit_count"] == 1  # 最新 episode 只有 day10 一次
