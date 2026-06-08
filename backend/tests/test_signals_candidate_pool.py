"""M23 slice 5：candidate_pool 整合測試（in-memory SQLite）。

驗證 spec §5 Step 1～4 + §6：
  - ingest_data 取窗口元資料
  - compute_rankings 取產業 / 個股 3d 排行
  - build_candidate_pool 聯集 + 擴散 + 過濾 + 截斷
  - per-stock metrics（price_change_*、量能比率、法人累計、margin ratio）
"""
from datetime import date

import pytest

from app.models import (
    DailyPrice,
    IndustryDailyFlow,
    InstStockFlow,
    MarginTrade,
    SignalWatchHit,
    StockMaster,
)
from app.signals import candidate_pool as cp_mod
from app.signals.candidate_pool import (
    POOL_HARD_LIMIT,
    POOL_SOFT_TRIGGER,
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
    # stock_count 目前仍以 stocks_master 原始 industry_name 比對；
    # 本測試 seed 用「半導體業」，而 industry flow canonicalized 後是「半導體」。
    sem = next(r for r in rankings["top_industries_3d"] if r["industry_name"] == "半導體")
    assert sem["stock_count"] == 0


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


def test_compute_rankings_skips_financial_industries_and_backfills(db):
    """金融類產業即使三日淨買超最高，也要被跳過順延，由非金融產業遞補。"""
    stocks = {"2330": {"name": "台積電", "industry": "半導體業"}}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    for d in dates:
        _seed_industry_flow(db, d, "金融保險業", 9.0e9)  # 最高，但金融 → 跳過
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
        _seed_industry_flow(db, d, "水泥", 1.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)

    inds = [r["industry_name"] for r in rankings["top_industries_3d"]]
    assert "金融保險業" not in inds
    # canonical 後半導體業 → 半導體
    assert inds[0] == "半導體"
    assert "水泥" in inds


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


def test_build_candidate_pool_drops_etf_and_financial(db):
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

    ids = {c["stock_id"] for c in pool}
    assert "0050" not in ids
    assert "2880" not in ids
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


def test_build_candidate_pool_truncates_when_exceeding_soft_trigger(db, monkeypatch):
    """灌爆候選池 → 超過 POOL_SOFT_TRIGGER 應截斷到 POOL_HARD_LIMIT。"""
    monkeypatch.setattr(cp_mod, "POOL_SOFT_TRIGGER", 5)
    monkeypatch.setattr(cp_mod, "POOL_HARD_LIMIT", 3)

    stocks = {f"{2000 + i}": {"name": f"S{i}", "industry": "半導體業"} for i in range(8)}
    dates = [date(2026, 4, 20), date(2026, 4, 21), date(2026, 4, 22)]
    _seed_full_market(db, dates, stocks)
    # 不同股票不同法人累計，第一名 net 最高
    for d in dates:
        for i, sid in enumerate(stocks):
            _seed_flow(db, d, sid, "trust", (8 - i) * 1.0e8)
        _seed_industry_flow(db, d, "半導體業", 5.0e9)
    db.commit()

    ingestion = ingest_data(db, date(2026, 4, 22))
    rankings = compute_rankings(db, date(2026, 4, 22), ingestion)
    pool = build_candidate_pool(db, date(2026, 4, 22), ingestion, rankings)

    assert len(pool) == 3
    # 截斷後保留 net flow 最高的 3 檔
    nets = [c["total_institution_flow_3d"] for c in pool]
    assert nets == sorted(nets, reverse=True)


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
