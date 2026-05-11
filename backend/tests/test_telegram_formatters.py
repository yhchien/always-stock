"""formatters.py 測試：純函式 → 純字串斷言。"""
from datetime import date

from app.routers.analysis import KeyFactor, TradeQualityResponse
from app.telegram import formatters
from app.telegram.watchlist_service import (
    WATCHLIST_LIMIT,
    AddResult,
    DeleteResult,
    StockSnapshot,
)


def _snap(stock_id="2330", name="台積電", close=1050.0, spread=1.2) -> StockSnapshot:
    return StockSnapshot(
        stock_id=stock_id,
        stock_name=name,
        industry_name="半導體",
        sub_industry="積體電路業",
        close_price=close,
        spread_pct=spread,
        trade_date=date(2026, 5, 11),
    )


# ── help / add / delete ─────────────────────────────────────────────────────


def test_help_text_contains_all_commands():
    text = formatters.HELP_TEXT
    for keyword in ["list help", "list register", "list show", "list add",
                    "list delete", "list watch", "list run"]:
        assert keyword in text


def test_format_add_result_success():
    result = AddResult(added=[_snap()], current_count=1)
    out = formatters.format_add_result(result)
    assert "✅" in out
    assert "2330" in out
    assert "台積電" in out
    assert f"1/{WATCHLIST_LIMIT}" in out


def test_format_add_result_with_duplicates():
    result = AddResult(duplicates=["2317"], current_count=1)
    out = formatters.format_add_result(result)
    assert "已在清單" in out
    assert "2317" in out


def test_format_add_result_with_not_found():
    result = AddResult(not_found=["9999"], current_count=0)
    out = formatters.format_add_result(result)
    assert "找不到" in out
    assert "9999" in out


def test_format_add_result_over_limit():
    result = AddResult(over_limit=["2454", "2412"], current_count=20)
    out = formatters.format_add_result(result)
    assert "上限" in out
    assert "2454" in out


def test_format_delete_result_success():
    result = DeleteResult(
        removed=["2330"],
        remaining=[_snap("2317", "鴻海", close=200.0)],
        current_count=1,
    )
    out = formatters.format_delete_result(result)
    assert "已刪除" in out
    assert "2330" in out
    assert "2317" in out


def test_format_delete_result_not_in_list():
    result = DeleteResult(not_in_list=["9999"], current_count=0)
    out = formatters.format_delete_result(result)
    assert "不在清單" in out
    assert "9999" in out


def test_format_watchlist_empty():
    out = formatters.format_watchlist([])
    assert "空的" in out
    assert "list add" in out


def test_format_watchlist_with_entries():
    out = formatters.format_watchlist([_snap(), _snap("2317", "鴻海", close=200.0)])
    assert "2330" in out
    assert "台積電" in out
    assert "2317" in out
    assert "鴻海" in out


# ── trade quality ───────────────────────────────────────────────────────────


def _response(rating="STRONG_BUY", classification="A") -> TradeQualityResponse:
    return TradeQualityResponse(
        stock_id="2330",
        stock_name="台積電",
        buy_date="2026-05-11",
        rating=rating,
        rating_label="強烈推薦",
        classification=classification,
        summary="基本面強勁，法人連買 5 日",
        target_price_low=1100.0,
        target_price_high=1200.0,
        report_markdown="## 詳細分析\n\n內容...",
        key_factors=[
            KeyFactor(category="industry", level="A", trend="improving", note="產業龍頭"),
            KeyFactor(category="chip", level="A", trend="stable", note="法人連買"),
        ],
        source="openai",
    )


def test_format_trade_quality_brief():
    out = formatters.format_trade_quality_brief(_response())
    assert "2330" in out
    assert "台積電" in out
    assert "強烈推薦" in out
    assert "A" in out  # classification
    assert "$1100" in out and "$1200" in out  # target price range


def test_format_trade_quality_brief_with_factors():
    out = formatters.format_trade_quality_brief(_response())
    assert "燈號" in out
    assert "產業" in out
    assert "籌碼" in out


def test_format_trade_quality_detail_includes_markdown():
    out = formatters.format_trade_quality_detail(_response())
    assert "詳細分析" in out  # report_markdown 內容


def test_format_trade_quality_not_found():
    out = formatters.format_trade_quality_not_found("2330")
    assert "尚無分析" in out
    assert "list run 2330" in out


# ── daily report ─────────────────────────────────────────────────────────────


def test_format_daily_report_empty():
    out = formatters.format_daily_report("alice", [])
    assert "空的" in out


def test_format_daily_report_with_quality():
    out = formatters.format_daily_report(
        "alice",
        [(_snap(), _response())],
    )
    assert "每日清單報告" in out
    assert "alice" in out
    assert "2330" in out
    assert "強烈推薦" in out


def test_format_daily_report_without_quality():
    out = formatters.format_daily_report(
        "alice",
        [(_snap(), None)],
    )
    assert "尚無分析資料" in out


# ── chunk_for_telegram ──────────────────────────────────────────────────────


def test_chunk_for_telegram_short():
    text = "short text"
    assert formatters.chunk_for_telegram(text) == ["short text"]


def test_chunk_for_telegram_splits_long_text():
    lines = [f"line {i}" for i in range(1000)]
    text = "\n".join(lines)
    chunks = formatters.chunk_for_telegram(text, chunk_size=500)
    assert len(chunks) > 1
    # 每個 chunk 都 ≤ 500（最後一個 chunk 也是）
    for chunk in chunks:
        assert len(chunk) <= 500
    # 重組後等於原文
    assert "\n".join(chunks) == text


def test_chunk_for_telegram_preserves_lines():
    """單行很長也不該切斷該行（chunk_size 不夠則該行單獨成段）。"""
    text = "a" * 100 + "\n" + "b" * 100
    chunks = formatters.chunk_for_telegram(text, chunk_size=150)
    # 第一個 chunk 是 "aaaa...a"，第二個是 "bbbb...b"
    assert len(chunks) == 2
