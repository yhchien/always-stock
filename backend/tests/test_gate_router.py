"""Tests for /api/gate/* endpoints (site-wide password gate)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_verify_returns_503_when_password_not_configured(monkeypatch, client):
    monkeypatch.delenv("SITE_GATE_PASSWORD", raising=False)
    res = client.post("/api/gate/verify", json={"password": "anything"})
    assert res.status_code == 503
    assert "SITE_GATE_PASSWORD" in res.json()["detail"]


def test_verify_accepts_correct_password(monkeypatch, client):
    monkeypatch.setenv("SITE_GATE_PASSWORD", "letmein")
    res = client.post("/api/gate/verify", json={"password": "letmein"})
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_verify_rejects_wrong_password(monkeypatch, client):
    monkeypatch.setenv("SITE_GATE_PASSWORD", "letmein")
    res = client.post("/api/gate/verify", json={"password": "nope"})
    assert res.status_code == 403


def test_verify_strips_password_whitespace_consistency(monkeypatch, client):
    """SITE_GATE_PASSWORD env 取值時 .strip() — 前端送進來不 strip，使用者
    複製貼上若多一個空白會驗失敗，這是刻意行為（避免 false positive）。"""
    monkeypatch.setenv("SITE_GATE_PASSWORD", "letmein")
    res = client.post("/api/gate/verify", json={"password": "letmein "})
    assert res.status_code == 403


def test_config_returns_attempts_and_lockout(monkeypatch, client):
    monkeypatch.setenv("SITE_GATE_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("SITE_GATE_LOCKOUT_SECONDS", "600")
    res = client.get("/api/gate/config")
    assert res.status_code == 200
    body = res.json()
    assert body == {"max_attempts": 5, "lockout_seconds": 600}


def test_config_falls_back_to_defaults(monkeypatch, client):
    monkeypatch.delenv("SITE_GATE_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("SITE_GATE_LOCKOUT_SECONDS", raising=False)
    res = client.get("/api/gate/config")
    assert res.status_code == 200
    body = res.json()
    assert body == {"max_attempts": 3, "lockout_seconds": 300}
