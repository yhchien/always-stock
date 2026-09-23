from typing import Any, Dict

import pytest

from app import jev_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_evaluate_posts_typed_request_and_returns_gateway_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: Dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        calls["url"] = url
        calls["kwargs"] = kwargs
        return _FakeResponse(
            200,
            {
                "model": "typesafe-ai/jev",
                "answers": {"worth_tracking": {"type": "boolean", "probability": 0.9}},
                "usage": {"inputTokens": 12, "outputTokens": 2},
            },
        )

    monkeypatch.setattr(jev_client.requests, "post", fake_post)

    result = jev_client.evaluate(
        {"stock": "2330"},
        {
            "worth_tracking": {
                "type": "boolean",
                "instructions": "值得追蹤嗎？",
            }
        },
        api_key="test-key",
    )

    assert result["answers"]["worth_tracking"]["probability"] == 0.9
    assert calls["url"] == "https://ai-gateway.vercel.sh/v1/evaluate"
    assert calls["kwargs"]["headers"]["Authorization"] == "Bearer test-key"
    assert calls["kwargs"]["json"]["model"] == "typesafe-ai/jev"


def test_evaluate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    with pytest.raises(jev_client.JevClientError, match="AI_GATEWAY_API_KEY"):
        jev_client.evaluate("state", {"ok": {"type": "boolean", "instructions": "ok?"}})


def test_evaluate_rejects_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jev_client.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(401, {"error": "unauthorized"}),
    )

    with pytest.raises(jev_client.JevClientError) as exc_info:
        jev_client.evaluate(
            "state",
            {"ok": {"type": "boolean", "instructions": "ok?"}},
            api_key="bad-key",
        )

    assert exc_info.value.status_code == 401
