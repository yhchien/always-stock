"""AI interpretation and missing-data recovery for the Taiwan market calendar.

The deterministic rule/model result remains authoritative.  This module adds
one optional web-search recovery stage before forecasting and one structured
interpretation after the forecast.  Recovery results are evidence only; the
daily job validates them and requires complete factors before forecasting.
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
RECOVERY_PROMPT_VERSION = "market-calendar-recovery-v1"
RECOVERY_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "market-calendar-recovery-v1.md"
DEFAULT_TIMEOUT_SECONDS = 45.0
RECOVERY_TIMEOUT_SECONDS = 90.0

RECOVERY_WEB_SEARCH_TOOL = {
    "type": "web_search",
    "external_web_access": True,
    "search_context_size": "high",
    "filters": {
        "allowed_domains": [
            "taifex.com.tw",
            "twse.com.tw",
            "cbc.gov.tw",
            "cboe.com",
            "fred.stlouisfed.org",
            "home.treasury.gov",
            "finance.yahoo.com",
            "stooq.com",
            "nasdaq.com",
            "cmegroup.com",
            "wantgoo.com",
        ]
    },
}

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

RECOVERY_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["recovered", "partial", "not_found"]},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "column": {"type": "string"},
                    "value": {"type": ["number", "null"]},
                    "unit": {"type": "string"},
                    "observed_date": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_name": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": [
                    "column",
                    "value",
                    "unit",
                    "observed_date",
                    "source_url",
                    "source_name",
                    "evidence",
                ],
            },
        },
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "search_notes": {"type": "string"},
    },
    "required": ["status", "results", "unresolved", "search_notes"],
}


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_recovery_prompt() -> str:
    return RECOVERY_PROMPT_PATH.read_text(encoding="utf-8")


def recover_missing_market_calendar_factors(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ask an enabled web-search agent to recover missing factor values.

    This is deliberately separate from the interpretation call.  The agent
    may search for sources, but it cannot change the forecast direction.  The
    caller must validate the returned date/value/source and decide whether the
    row has reached 100% completeness.
    """
    api_key = get_openai_api_key()
    if not api_key:
        return {
            "status": "unavailable",
            "results": [],
            "unresolved": payload.get("missing_columns", []),
            "search_notes": "OPENAI_API_KEY 未設定",
            "source": "recovery_unavailable",
            "prompt_version": RECOVERY_PROMPT_VERSION,
            "model": None,
            "response_id": None,
        }

    model = (
        os.getenv("MARKET_CALENDAR_RECOVERY_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "gpt-5.4-mini"
    )
    try:
        client = OpenAI(api_key=api_key, timeout=RECOVERY_TIMEOUT_SECONDS, max_retries=0)
        response = client.responses.create(
            model=model,
            instructions=_load_recovery_prompt(),
            input=json.dumps(payload, ensure_ascii=False, default=str),
            tools=[RECOVERY_WEB_SEARCH_TOOL],
            # Recovery must perform a live search; a prompt alone would leave
            # web search optional and could return a remembered value.
            tool_choice="required",
            include=["web_search_call.action.sources"],
            max_output_tokens=5000,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "market_calendar_factor_recovery",
                    "description": "Machine-readable recovery evidence for missing market-calendar factors.",
                    "strict": True,
                    "schema": RECOVERY_RESPONSE_SCHEMA,
                }
            },
        )
        raw = getattr(response, "output_text", "") or ""
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("AI recovery response is not a JSON object")
        result["source"] = "openai_web_search"
        result["prompt_version"] = RECOVERY_PROMPT_VERSION
        result["model"] = model
        result["response_id"] = getattr(response, "id", None)
        return result
    except Exception as exc:
        logger.exception("market-calendar factor recovery failed")
        return {
            "status": "unavailable",
            "results": [],
            "unresolved": payload.get("missing_columns", []),
            "search_notes": f"AI recovery failed: {type(exc).__name__}",
            "source": "recovery_error",
            "prompt_version": RECOVERY_PROMPT_VERSION,
            "model": model,
            "response_id": None,
        }


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
