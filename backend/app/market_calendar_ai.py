"""AI-only interpretation for the Taiwan market calendar.

The deterministic rule/model result remains authoritative.  This module adds
one structured OpenAI interpretation after the forecast has been calculated;
failure falls back to the deterministic evidence so the daily job still emits
a prediction.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from openai import OpenAI

from app.settings import get_openai_api_key, get_openai_model

logger = logging.getLogger(__name__)

PROMPT_VERSION = "market-calendar-v1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "market-calendar-v1.md"
DEFAULT_TIMEOUT_SECONDS = 45.0

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "bullish_factors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "factor": {"type": "string"},
                    "observation": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["factor", "observation", "interpretation"],
            },
        },
        "bearish_factors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "factor": {"type": "string"},
                    "observation": {"type": "string"},
                    "interpretation": {"type": "string"},
                },
                "required": ["factor", "observation", "interpretation"],
            },
        },
        "regime": {"type": "string"},
        "rule_alignment": {"type": "string"},
        "ai_bias": {"type": "string", "enum": ["偏漲", "偏跌", "中性", "資料不足"]},
        "magnitude_comment": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "missing_factors": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "bullish_factors",
        "bearish_factors",
        "regime",
        "rule_alignment",
        "ai_bias",
        "magnitude_comment",
        "risks",
        "missing_factors",
    ],
}


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _fallback(payload: Dict[str, Any], reason: str) -> Dict[str, Any]:
    baseline = payload.get("baseline_prediction", {})
    evidence = payload.get("deterministic_evidence", [])
    bullish = [item for item in evidence if item.get("direction") == "偏漲"]
    bearish = [item for item in evidence if item.get("direction") == "偏跌"]
    return {
        "summary": "AI 解讀目前不可用，以下沿用程式整理的因素證據。",
        "bullish_factors": bullish,
        "bearish_factors": bearish,
        "regime": "資料解讀 fallback",
        "rule_alignment": "以程式規則預測為準",
        "ai_bias": baseline.get("predicted_binary", "資料不足"),
        "magnitude_comment": "沿用程式預估幅度，未由 AI 調整。",
        "risks": ["AI 解讀服務不可用：" + reason],
        "missing_factors": [],
        "source": "deterministic_fallback",
        "prompt_version": PROMPT_VERSION,
        "model": None,
        "response_id": None,
    }


def interpret_market_calendar(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call the AI interpreter, or return a deterministic explanation."""
    api_key = get_openai_api_key()
    if not api_key:
        return _fallback(payload, "OPENAI_API_KEY 未設定")

    model = os.getenv("MARKET_CALENDAR_AI_MODEL", get_openai_model()).strip()
    try:
        client = OpenAI(api_key=api_key, timeout=DEFAULT_TIMEOUT_SECONDS, max_retries=0)
        response = client.responses.create(
            model=model,
            instructions=_load_prompt(),
            input=json.dumps(payload, ensure_ascii=False, default=str),
            max_output_tokens=4000,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "market_calendar_interpretation",
                    "description": "Structured interpretation of market-calendar factors.",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                }
            },
        )
        raw = getattr(response, "output_text", "") or ""
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("AI response is not a JSON object")
        result["source"] = "openai"
        result["prompt_version"] = PROMPT_VERSION
        result["model"] = model
        result["response_id"] = getattr(response, "id", None)
        return result
    except Exception as exc:  # daily forecast must survive AI outage
        logger.exception("market-calendar AI interpretation failed")
        return _fallback(payload, type(exc).__name__)
