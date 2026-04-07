"""
Tests for backend/app/routers/realtime.py
Uses unittest.mock to patch urllib — no real API calls.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.routers.realtime import _parse_price, _fetch_quotes, RealtimeQuote


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
