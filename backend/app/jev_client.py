"""Small client for Jev evaluations through Vercel AI Gateway.

Jev is an evaluation model, not a chat-completions model.  The Gateway
evaluation endpoint accepts a state plus typed questions and returns bounded
answers with probabilities/scores.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from app.settings import get_jev_api_key


DEFAULT_JEV_MODEL = "typesafe-ai/jev"
DEFAULT_AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh"
DEFAULT_JEV_TIMEOUT_SECONDS = 30.0


class JevClientError(RuntimeError):
    """Raised when a Jev Gateway request cannot produce a valid response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _evaluate_url(base_url: Optional[str] = None) -> str:
    configured = base_url or DEFAULT_AI_GATEWAY_BASE_URL
    return configured.rstrip("/") + "/v1/evaluate"


def evaluate(
    state: Any,
    questions: Dict[str, Any],
    *,
    model: str = DEFAULT_JEV_MODEL,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_JEV_TIMEOUT_SECONDS,
    provider_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate typed questions against state through Vercel AI Gateway.

    The complete Gateway response is returned so callers can retain answers,
    usage, and routing metadata.  The API key is never included in exceptions.
    """
    resolved_key = (api_key or get_jev_api_key()).strip()
    if not resolved_key:
        raise JevClientError(
            "AI_GATEWAY_API_KEY is not configured; Jev evaluation skipped."
        )
    if not questions:
        raise JevClientError("Jev evaluation requires at least one question.")

    payload: Dict[str, Any] = {
        "model": model,
        "state": state,
        "questions": questions,
    }
    if provider_options is not None:
        payload["providerOptions"] = provider_options

    try:
        response = requests.post(
            _evaluate_url(base_url),
            headers={
                "Authorization": "Bearer " + resolved_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise JevClientError("Jev Gateway request failed.") from exc

    if not 200 <= response.status_code < 300:
        body = response.text[:2000]
        raise JevClientError(
            "Jev Gateway returned an HTTP error.",
            status_code=response.status_code,
            response_body=body,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise JevClientError(
            "Jev Gateway returned invalid JSON.",
            status_code=response.status_code,
            response_body=response.text[:2000],
        ) from exc

    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        raise JevClientError(
            "Jev Gateway response did not contain an answers object.",
            status_code=response.status_code,
            response_body=response.text[:2000],
        )
    return result
