"""
Tests for backend/app/routers/realtime.py
Uses unittest.mock to patch urllib — no real API calls.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.routers import realtime as realtime_module
from app.routers.realtime import _parse_price, _fetch_quotes, RealtimeQuote


@pytest.fixture(autouse=True)
def _clear_quote_cache():
    """2026-08-13：_quote_cache 是 module-level 全站共用的 dict，測試之間必須
    互相隔離，否則前一個測試快取的股價會滲透進下一個測試（例如兩個測試剛好都用
    stock_id="2330" 但期待不同的價格）。"""
    realtime_module._quote_cache.clear()
    yield
    realtime_module._quote_cache.clear()


# ── _parse_price tests ───────────────────────────────────────────────────────

class TestParsePrice:
    def test_normal(self):
        assert _parse_price("453.00") == 453.0

    def test_integer(self):
        assert _parse_price("100") == 100.0

    def test_dash_returns_none(self):
        assert _parse_price("-") is None

    def test_empty_returns_none(self):
        assert _parse_price("") is None

    def test_none_returns_none(self):
        assert _parse_price(None) is None

    def test_invalid_returns_none(self):
        assert _parse_price("N/A") is None


# ── _fetch_quotes tests ──────────────────────────────────────────────────────

def _make_twse_response(msg_array):
    payload = json.dumps({"msgArray": msg_array, "rtcode": "0000"}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = payload
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _stock_msg(sid, name, price, yclose, high, low, vol="100", time="13:30:00"):
    return {
        "c": sid, "n": name,
        "z": str(price), "y": str(yclose),
        "h": str(high), "l": str(low),
        "o": str(yclose), "v": vol, "t": time,
        "d": "20250407",
    }


class TestFetchQuotes:
    def test_returns_msg_array(self):
        msgs = [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
        with patch("app.routers.realtime.urllib.request.urlopen",
                   return_value=_make_twse_response(msgs)):
            result = _fetch_quotes(["2330"])
        assert len(result) == 1
        assert result[0]["c"] == "2330"


# ── per-stock cache tests（2026-08-13）───────────────────────────────────────
# 症狀：使用者反映首頁報價「很慢很慢，而且常常出不來」。實測本機直連 TWSE：
# 8 個並行請求（模擬多位使用者同時瀏覽首頁）延遲從循序的 0.6~1.7 秒拉到
# 1.2~4.2 秒——沒有快取時，每個使用者的每次輪詢都各自重打一次 TWSE。


class TestQuoteCache:
    def test_second_call_within_ttl_hits_cache_not_twse(self):
        msgs = [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            return_value=_make_twse_response(msgs),
        ) as mock_urlopen:
            first = _fetch_quotes(["2330"])
            second = _fetch_quotes(["2330"])

        assert mock_urlopen.call_count == 1
        assert first == second == msgs

    def test_partial_cache_hit_only_fetches_missing_ids(self):
        # 先讓 2330 進快取
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            return_value=_make_twse_response(
                [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
            ),
        ):
            _fetch_quotes(["2330"])

        # 第二次請求 2330（已快取）+ 2317（未快取）；TWSE 只應該被要求 2317
        captured_urls = []

        def fake_urlopen(req, timeout=None):
            captured_urls.append(req.full_url)
            return _make_twse_response(
                [_stock_msg("2317", "鴻海", 200, 195, 205, 190)]
            )

        with patch(
            "app.routers.realtime.urllib.request.urlopen", side_effect=fake_urlopen
        ) as mock_urlopen:
            result = _fetch_quotes(["2330", "2317"])

        assert mock_urlopen.call_count == 1
        assert "tse_2317.tw" in captured_urls[0]
        assert "tse_2330.tw" not in captured_urls[0]
        assert {item["c"] for item in result} == {"2330", "2317"}

    def test_cache_expires_after_ttl(self):
        msgs = [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            return_value=_make_twse_response(msgs),
        ) as mock_urlopen:
            _fetch_quotes(["2330"])
            # 直接把快取項目的到期時間往前調，模擬 TTL 已過（不用真的 sleep 8 秒）
            expires_at, item = realtime_module._quote_cache["2330"]
            realtime_module._quote_cache["2330"] = (time.monotonic() - 1, item)
            _fetch_quotes(["2330"])

        assert mock_urlopen.call_count == 2

    def test_twse_failure_returns_cached_partial_results_not_raise(self):
        # 2330 先進快取
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            return_value=_make_twse_response(
                [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
            ),
        ):
            _fetch_quotes(["2330"])

        # 這次請求 2330（快取命中）+ 9999（未快取，TWSE 呼叫失敗）
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            side_effect=TimeoutError("TWSE timed out"),
        ):
            result = _fetch_quotes(["2330", "9999"])

        # 9999 沒拿到資料，但 2330 的快取結果仍然正常回傳，不因為 9999 失敗而整包噴掉
        assert [item["c"] for item in result] == ["2330"]

    def test_twse_failure_with_nothing_cached_returns_empty_list(self):
        with patch(
            "app.routers.realtime.urllib.request.urlopen",
            side_effect=TimeoutError("TWSE timed out"),
        ):
            result = _fetch_quotes(["9999"])
        assert result == []


# ── API endpoint tests ───────────────────────────────────────────────────────

class TestRealtimeEndpoint:
    def test_single_stock(self):
        from fastapi.testclient import TestClient
        from app.main import app

        msgs = [_stock_msg("2330", "台積電", 900, 890, 910, 880)]
        with patch("app.routers.realtime._fetch_quotes", return_value=msgs):
            client = TestClient(app)
            resp = client.get("/api/realtime/quotes?stock_ids=2330")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["stock_id"] == "2330"
        assert data[0]["price"] == 900.0
        assert data[0]["prev_close"] == 890.0
        assert data[0]["change"] == pytest.approx(10.0)
        assert data[0]["change_pct"] == pytest.approx(10.0 / 890.0 * 100)

    def test_multiple_stocks(self):
        from fastapi.testclient import TestClient
        from app.main import app

        msgs = [
            _stock_msg("2330", "台積電", 900, 890, 910, 880),
            _stock_msg("2317", "鴻海", 200, 195, 205, 190),
        ]
        with patch("app.routers.realtime._fetch_quotes", return_value=msgs):
            client = TestClient(app)
            resp = client.get("/api/realtime/quotes?stock_ids=2330,2317")

        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_no_trade_yet(self):
        """Price is '-' before market opens — should return price=None."""
        from fastapi.testclient import TestClient
        from app.main import app

        msgs = [{"c": "2330", "n": "台積電", "z": "-", "y": "890",
                 "h": "-", "l": "-", "o": "-", "v": "0", "t": "08:30:00"}]
        with patch("app.routers.realtime._fetch_quotes", return_value=msgs):
            client = TestClient(app)
            resp = client.get("/api/realtime/quotes?stock_ids=2330")

        data = resp.json()
        assert data[0]["price"] is None
        assert data[0]["change"] is None

    def test_empty_ids_returns_400(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/realtime/quotes?stock_ids=")
        assert resp.status_code == 400

    def test_too_many_ids_returns_400(self):
        from fastapi.testclient import TestClient
        from app.main import app

        ids = ",".join(str(i) for i in range(51))
        client = TestClient(app)
        resp = client.get(f"/api/realtime/quotes?stock_ids={ids}")
        assert resp.status_code == 400
