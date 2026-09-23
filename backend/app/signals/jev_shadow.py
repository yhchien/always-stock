"""Jev Shadow Mode for P3 research evidence.

This module is deliberately a sidecar.  It evaluates evidence already
collected by the existing research stage, records a separately versioned
result, and never returns a value that the production selector or P4 state
machine consumes.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.database import Base
from app.jev_client import JevClientError, evaluate
from app.models import JevEvaluationCache
from app.settings import get_jev_api_key

logger = logging.getLogger(__name__)


MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ASSIST = "assist"
SUPPORTED_MODES = {MODE_OFF, MODE_SHADOW, MODE_ASSIST}

DEFAULT_MODEL = "typesafe-ai/jev"
QUESTION_VERSION = "jev_news_theme_v1"
EVIDENCE_SCHEMA_VERSION = "jev_evidence_v1"
REPORT_SCHEMA_VERSION = "jev_shadow_report_v1"
PROVIDER = "vercel_ai_gateway"

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_CONCURRENCY = 2
DEFAULT_MAX_API_CALLS = 100
DEFAULT_BACKOFF_SECONDS = 0.5

QUESTION_CHOICES: Dict[str, Tuple[str, ...]] = {
    "business_relevance": ("DIRECT", "INDIRECT", "UNRELATED", "UNKNOWN"),
    "event_type": (
        "NEW_ORDER",
        "EARNINGS",
        "PRODUCT",
        "CAPACITY",
        "REGULATION",
        "INDUSTRY",
        "CORPORATE",
        "RISK_EVENT",
        "OTHER",
        "UNKNOWN",
    ),
    "catalyst_state": ("NEW", "ONGOING", "REPEATED", "UNCLEAR"),
    "thesis_impact": ("SUPPORT", "WEAKEN", "NEUTRAL", "CONFLICT", "UNKNOWN"),
    "verification_need": ("NORMAL", "REVIEW", "CONFLICT"),
}


class JevShadowError(RuntimeError):
    """A local Jev shadow processing/validation error."""


def ensure_jev_cache_table(engine: Any) -> None:
    """Create the additive cache table for CLI jobs and web startup."""
    if current_mode() == MODE_OFF:
        return
    Base.metadata.create_all(bind=engine, tables=[JevEvaluationCache.__table__])


def _cache_table_available(db: Session) -> bool:
    try:
        return inspect(db.get_bind()).has_table(JevEvaluationCache.__tablename__)
    except Exception:
        logger.warning("Unable to inspect Jev cache table; continuing without cache", exc_info=True)
        return False


def current_mode() -> str:
    value = os.getenv("JEV_MODE", MODE_OFF).strip().lower()
    return value if value in SUPPORTED_MODES else MODE_OFF


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _nonnegative_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


def _config() -> Dict[str, Any]:
    return {
        "model": os.getenv("JEV_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout": _positive_float("JEV_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        "max_retries": _nonnegative_int("JEV_MAX_RETRIES", DEFAULT_MAX_RETRIES),
        "concurrency": _positive_int("JEV_CONCURRENCY", DEFAULT_CONCURRENCY),
        "max_api_calls": _positive_int("JEV_MAX_API_CALLS", DEFAULT_MAX_API_CALLS),
        "backoff": _positive_float("JEV_RETRY_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS),
    }


def _iso_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _parse_source_date(value: Any) -> Optional[date]:
    raw = _text(value, 64)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _stock_id(item: Dict[str, Any]) -> str:
    return _text(item.get("stock_id") or item.get("stock"), 32)


def _original_llm(item: Dict[str, Any]) -> Dict[str, Any]:
    theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
    return {
        "business_validation": item.get("business_validation")
        or item.get("instrument_validation"),
        "theme_validation": item.get("theme_validation"),
        "catalyst_status": theme.get("catalyst_status")
        or item.get("catalyst_status"),
        "research_confidence": item.get("research_confidence"),
        "source_count": len(item.get("sources") or []),
    }


def _source_content(item: Dict[str, Any], source: Dict[str, Any]) -> str:
    theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
    candidates = [
        source.get("content"),
        source.get("summary"),
        item.get("research_summary"),
        item.get("business_summary"),
        theme.get("catalyst_summary"),
        theme.get("theme_reason"),
    ]
    return "\n".join(_text(value) for value in candidates if _text(value))[:8000]


def _source_units(
    item: Dict[str, Any],
    *,
    target_date: date,
) -> List[Dict[str, Any]]:
    """Create point-in-time news units and explicit non-call failures."""
    stock_id = _stock_id(item)
    base = {
        "stock_id": stock_id,
        "company_name": _text(item.get("name"), 200),
        "industry": _text(item.get("industry"), 200),
        "sub_industry": _text(item.get("sub_industry"), 200),
        "asset_type": _text(item.get("asset_type") or "COMMON_STOCK", 32),
        "original_thesis": _text(
            item.get("recommendation_thesis")
            or item.get("theme_cluster")
            or ((item.get("theme") or {}).get("main_theme") if isinstance(item.get("theme"), dict) else ""),
            1000,
        ),
        "tracking_hypothesis": _text(
            item.get("tracking_state") or item.get("momentum_freshness"), 200
        ),
        "original_llm": _original_llm(item),
    }
    sources = item.get("sources")
    if not isinstance(sources, list) or not sources:
        return [{
            **base,
            "status": "INSUFFICIENT_DATA",
            "error": "No valid existing research source was available.",
            "source_url": None,
            "source_published_at": None,
        }]

    units: List[Dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            units.append({
                **base,
                "status": "INSUFFICIENT_DATA",
                "error": "Research source was not an object.",
                "source_url": None,
                "source_published_at": None,
            })
            continue
        url = _text(source.get("url"), 2000)
        title = _text(source.get("title"), 1000)
        published_raw = _text(source.get("published_date"), 64)
        published = _parse_source_date(published_raw)
        content = _source_content(item, source)
        unit = {
            **base,
            "source_url": url or None,
            "source_published_at": published_raw or None,
            "source_title": title,
            "source_type": _text(source.get("source_type") or "OTHER", 32),
            "source_content": content,
            "retrieved_at": _iso_now(),
        }
        if not url or not title or published is None:
            unit.update({
                "status": "INSUFFICIENT_DATA",
                "error": "Source requires url, title, and ISO published_date.",
            })
        elif published > target_date:
            unit.update({
                "status": "FUTURE_EVIDENCE_REJECTED",
                "error": "Source published after the analysis date.",
            })
        elif not content:
            unit.update({
                "status": "INSUFFICIENT_DATA",
                "error": "Source has no usable content or existing research context.",
            })
        else:
            unit["status"] = "READY"
        units.append(unit)
    return units


def _unit_input_hash(unit: Dict[str, Any]) -> str:
    return _json_hash({
        "stock_id": unit.get("stock_id"),
        "company_name": unit.get("company_name"),
        "industry": unit.get("industry"),
        "source_title": unit.get("source_title"),
        "source_content": unit.get("source_content"),
        "source_url": unit.get("source_url"),
        "source_published_at": unit.get("source_published_at"),
        "original_thesis": unit.get("original_thesis"),
        "tracking_hypothesis": unit.get("tracking_hypothesis"),
    })


def _news_id(unit: Dict[str, Any]) -> str:
    return _unit_input_hash(unit)[:32]


def _prior_rows(
    db: Session,
    stock_id: str,
    *,
    before_date: date,
) -> List[JevEvaluationCache]:
    rows = (
        db.query(JevEvaluationCache)
        .filter(
            JevEvaluationCache.stock_id == stock_id,
            JevEvaluationCache.status == "OK",
        )
        .order_by(JevEvaluationCache.source_published_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for row in rows:
        published = _parse_source_date(row.source_published_at)
        if published is None or published >= before_date:
            continue
        out.append(row)
        if len(out) >= 20:
            break
    return out


def _history_payload(rows: Iterable[JevEvaluationCache]) -> Tuple[List[Dict[str, Any]], str]:
    payload = []
    for row in rows:
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        payload.append({
            "news_id": row.news_id,
            "source_url": row.source_url,
            "source_published_at": row.source_published_at,
            "event_type": evidence.get("event_type"),
            "catalyst_state": evidence.get("catalyst_state"),
            "thesis_impact": evidence.get("thesis_impact"),
        })
    return payload, _json_hash(payload)[:16]


def _questions() -> Dict[str, Any]:
    instructions = {
        "business_relevance": "How directly does this news concern the named company's own business or exposure? Use UNKNOWN when the evidence is insufficient.",
        "event_type": "What is the primary event type supported by the supplied evidence? Use UNKNOWN when unclear.",
        "catalyst_state": "Compared with prior evidence supplied in state, is this a new, ongoing, repeated, or unclear catalyst? Do not infer NEW without prior comparison evidence.",
        "thesis_impact": "How does this evidence relate to the supplied investment/tracking thesis? This is evidence relation only, not a price prediction or sell instruction.",
        "verification_need": "Does this item need normal verification, human review, or indicate a conflict in the supplied evidence?",
    }
    criteria = {
        "business_relevance": {
            "DIRECT": "Directly concerns the company's own products, orders, earnings, capacity, operations, or named exposure.",
            "INDIRECT": "Concerns an upstream/downstream or industry relationship but not the company's own event.",
            "UNRELATED": "Does not materially concern the company or its stated exposure.",
            "UNKNOWN": "The supplied evidence is insufficient to determine relevance.",
        },
        "event_type": {choice: choice for choice in QUESTION_CHOICES["event_type"]},
        "catalyst_state": {
            "NEW": "Substantive event not represented in the supplied prior evidence.",
            "ONGOING": "The same catalyst remains active or is progressing.",
            "REPEATED": "This is a repeated or substantially duplicate report of a known event.",
            "UNCLEAR": "The supplied history is insufficient to distinguish the state.",
        },
        "thesis_impact": {
            "SUPPORT": "The evidence supports the supplied thesis.",
            "WEAKEN": "The evidence weakens but does not directly contradict the thesis.",
            "NEUTRAL": "The evidence does not materially change the thesis.",
            "CONFLICT": "The evidence directly conflicts with the thesis.",
            "UNKNOWN": "The relation cannot be determined from the supplied evidence.",
        },
        "verification_need": {
            "NORMAL": "No special conflict or review signal is present.",
            "REVIEW": "Important ambiguity or incomplete evidence needs human review.",
            "CONFLICT": "Supplied evidence contains a material contradiction.",
        },
    }
    return {
        name: {
            "type": "choice",
            "instructions": instructions[name],
            "criteria": criteria[name],
        }
        for name in QUESTION_CHOICES
    }


def _state(unit: Dict[str, Any], prior: List[Dict[str, Any]], analysis_date: date) -> Dict[str, Any]:
    return {
        "analysis_date": analysis_date.isoformat(),
        "stock": {
            "stock_id": unit.get("stock_id"),
            "company_name": unit.get("company_name"),
            "asset_type": unit.get("asset_type"),
            "industry": unit.get("industry"),
            "sub_industry": unit.get("sub_industry"),
            "business_context": _text(unit.get("source_content"), 5000),
        },
        "thesis": {
            "original_thesis": unit.get("original_thesis"),
            "tracking_hypothesis": unit.get("tracking_hypothesis"),
        },
        "news": {
            "news_id": unit.get("news_id"),
            "title": unit.get("source_title"),
            "content": _text(unit.get("source_content"), 5000),
            "url": unit.get("source_url"),
            "source_type": unit.get("source_type"),
            "published_at": unit.get("source_published_at"),
        },
        "prior_evidence": prior,
    }


def _input_context(
    unit: Dict[str, Any],
    prior: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Persist the bounded input projection needed for later human review."""
    return {
        "stock_id": unit.get("stock_id"),
        "company_name": unit.get("company_name"),
        "industry": unit.get("industry"),
        "sub_industry": unit.get("sub_industry"),
        "original_thesis": unit.get("original_thesis"),
        "tracking_hypothesis": unit.get("tracking_hypothesis"),
        "news": {
            "title": unit.get("source_title"),
            "content": _text(unit.get("source_content"), 2500),
            "url": unit.get("source_url"),
            "source_type": unit.get("source_type"),
            "published_at": unit.get("source_published_at"),
            "retrieved_at": unit.get("retrieved_at"),
        },
        "prior_evidence": list(prior or []),
    }


def _answer_value(answer: Any) -> Tuple[Optional[str], Optional[float], Dict[str, float]]:
    if not isinstance(answer, dict):
        return None, None, {}
    value = answer.get("choice")
    if value is None:
        value = answer.get("value")
    if value is None:
        value = answer.get("answer")
    probabilities: Dict[str, float] = {}
    raw_probabilities = answer.get("probabilities")
    if isinstance(raw_probabilities, dict):
        for key, raw_value in raw_probabilities.items():
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                probabilities[_text(key, 64).upper()] = float(raw_value)
    probability = answer.get("probability")
    if probability is None:
        probability = answer.get("confidence")
    if probability is None and value is not None:
        probability = probabilities.get(_text(value, 64).upper())
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        probability = None
    else:
        probability = float(probability)
    return (_text(value, 64).upper() or None), probability, probabilities


def _normalize_answers(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevShadowError("Jev response is missing answers.")
    normalized: Dict[str, Dict[str, Any]] = {}
    for name, choices in QUESTION_CHOICES.items():
        value, probability, probabilities = _answer_value(answers.get(name))
        if value not in choices:
            raise JevShadowError("Jev returned an invalid choice for %s." % name)
        if probability is not None and not 0 <= probability <= 1:
            raise JevShadowError("Jev returned an invalid probability for %s." % name)
        normalized[name] = {
            "classification": value,
            "probability": probability,
            "probabilities": probabilities,
        }
    return normalized


def _retryable(exc: JevClientError) -> bool:
    return exc.status_code is None or exc.status_code in {408, 409, 425, 429} or exc.status_code >= 500


def _redact(value: Any, api_key: str) -> str:
    message = _text(value, 1000)
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return message.replace("Bearer ", "Bearer [REDACTED]")


def _evaluate_unit(
    unit: Dict[str, Any],
    *,
    analysis_date: date,
    prior: List[Dict[str, Any]],
    config: Dict[str, Any],
    api_key: str,
) -> Dict[str, Any]:
    started = time.monotonic()
    retries = 0
    try:
        response: Dict[str, Any] = {}
        while True:
            try:
                response = evaluate(
                    _state(unit, prior, analysis_date),
                    _questions(),
                    model=config["model"],
                    api_key=api_key,
                    timeout=config["timeout"],
                )
                break
            except JevClientError as exc:
                if retries >= config["max_retries"] or not _retryable(exc):
                    raise
                retries += 1
                time.sleep(config["backoff"] * (2 ** (retries - 1)))
        answers = _normalize_answers(response)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        now = _iso_now()
        return {
            "status": "OK",
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "provider": PROVIDER,
            "model": response.get("model") or config["model"],
            "mode": current_mode(),
            "evaluated_at": now,
            "stock_id": unit.get("stock_id"),
            "news_id": unit.get("news_id"),
            "source_url": unit.get("source_url"),
            "source_published_at": unit.get("source_published_at"),
            "source_retrieved_at": unit.get("retrieved_at"),
            "source_title": unit.get("source_title"),
            "input_hash": unit.get("input_hash"),
            "historical_evidence_version": unit.get("historical_evidence_version"),
            "question_version": QUESTION_VERSION,
            "input_context": _input_context(unit, prior),
            "business_relevance": answers["business_relevance"]["classification"],
            "event_type": answers["event_type"]["classification"],
            "catalyst_state": answers["catalyst_state"]["classification"],
            "thesis_impact": answers["thesis_impact"]["classification"],
            "verification_need": answers["verification_need"]["classification"],
            "answer_confidence": answers,
            "original_llm": unit.get("original_llm") or {},
            "usage": usage,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "retry_count": retries,
            "api_call_count": retries + 1,
            "cache_hit": False,
            "raw_model_output": response,
        }
    except JevClientError as exc:
        return _failure_evidence(
            unit,
            status="API_ERROR",
            error=_redact(exc.response_body or str(exc), api_key),
            latency_ms=int((time.monotonic() - started) * 1000),
            retries=retries,
            api_call_count=retries + 1,
        )
    except JevShadowError as exc:
        return _failure_evidence(
            unit,
            status="INVALID_RESPONSE",
            error=str(exc),
            latency_ms=int((time.monotonic() - started) * 1000),
            retries=retries,
            api_call_count=retries + 1,
        )
    except Exception as exc:
        logger.exception("Unexpected Jev shadow evaluation failure for %s", unit.get("stock_id"))
        return _failure_evidence(
            unit,
            status="API_ERROR",
            error="%s: %s" % (type(exc).__name__, _redact(exc, api_key)),
            latency_ms=int((time.monotonic() - started) * 1000),
            retries=retries,
            api_call_count=retries + 1,
        )


def _failure_evidence(
    unit: Dict[str, Any],
    *,
    status: str,
    error: str,
    latency_ms: int = 0,
    retries: int = 0,
    api_call_count: int = 0,
) -> Dict[str, Any]:
    return {
        "status": status,
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "provider": PROVIDER,
        "model": unit.get("model") or DEFAULT_MODEL,
        "mode": current_mode(),
        "evaluated_at": _iso_now(),
        "stock_id": unit.get("stock_id"),
        "news_id": unit.get("news_id"),
        "source_url": unit.get("source_url"),
        "source_published_at": unit.get("source_published_at"),
        "source_retrieved_at": unit.get("retrieved_at"),
        "source_title": unit.get("source_title"),
        "input_hash": unit.get("input_hash"),
        "historical_evidence_version": unit.get("historical_evidence_version"),
        "question_version": QUESTION_VERSION,
        "input_context": _input_context(unit),
        "business_relevance": "UNKNOWN",
        "event_type": "UNKNOWN",
        "catalyst_state": "UNCLEAR",
        "thesis_impact": "UNKNOWN",
        "verification_need": "REVIEW",
        "answer_confidence": {},
        "original_llm": unit.get("original_llm") or {},
        "usage": {},
        "latency_ms": latency_ms,
        "retry_count": retries,
        "api_call_count": api_call_count,
        "cache_hit": False,
        "error": _text(error, 1000),
    }


def _comparison(evidence: Dict[str, Any]) -> Dict[str, Any]:
    original = evidence.get("original_llm") or {}
    business_map = {
        "VERIFIED": "DIRECT",
        "MISMATCH": "UNRELATED",
        "UNCONFIRMED": "UNKNOWN",
    }
    theme_map = {
        "VERIFIED": "SUPPORT",
        "MISMATCH": "CONFLICT",
        "UNCONFIRMED": "UNKNOWN",
    }
    old_business = business_map.get(str(original.get("business_validation") or "").upper())
    old_theme = theme_map.get(str(original.get("theme_validation") or "").upper())
    jev_business = evidence.get("business_relevance")
    jev_impact = evidence.get("thesis_impact")
    flags = []
    if old_business and jev_business and old_business != jev_business:
        flags.append("BUSINESS_RELEVANCE_DIFFERENCE")
    if old_theme and jev_impact and old_theme != jev_impact:
        flags.append("THESIS_IMPACT_DIFFERENCE")
    if jev_business in {"INDIRECT", "UNRELATED"} and old_theme == "SUPPORT":
        flags.append("POSSIBLE_INDUSTRY_NEWS_FALSE_POSITIVE")
    if evidence.get("catalyst_state") == "REPEATED":
        flags.append("POSSIBLE_DUPLICATE_OR_OLD_EVENT")
    return {
        "stock_id": evidence.get("stock_id"),
        "news_id": evidence.get("news_id"),
        "source_url": evidence.get("source_url"),
        "original_llm": original,
        "jev": {
            "business_relevance": evidence.get("business_relevance"),
            "event_type": evidence.get("event_type"),
            "catalyst_state": evidence.get("catalyst_state"),
            "thesis_impact": evidence.get("thesis_impact"),
            "verification_need": evidence.get("verification_need"),
        },
        "heuristic_flags": flags,
    }


def empty_report(
    mode: str,
    *,
    status: str = "DISABLED",
    message: Optional[str] = None,
) -> Dict[str, Any]:
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "provider": PROVIDER,
        "model": os.getenv("JEV_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "mode": mode,
        "status": status,
        "question_version": QUESTION_VERSION,
        "evaluated_at": _iso_now(),
        "summary": {
            "candidate_count": 0,
            "news_item_count": 0,
            "evaluated_count": 0,
            "cache_hit_count": 0,
            "api_call_count": 0,
            "retry_count": 0,
            "failure_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": None,
            "latency_ms_total": 0,
        },
        "evidence": [],
        "comparison": [],
    }
    if message:
        report["message"] = message
    return report


def _usage_summary(evidence: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    input_tokens = output_tokens = total_tokens = cost = latency = 0
    has_cost = False
    for item in evidence:
        if item.get("cache_hit"):
            continue
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        input_tokens += int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
        total_tokens += int(usage.get("totalTokens") or usage.get("total_tokens") or 0)
        value = usage.get("costUsd")
        if value is None:
            value = usage.get("cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost += float(value)
            has_cost = True
        if value is None:
            raw = item.get("raw_model_output")
            gateway = (((raw or {}).get("providerMetadata") or {}).get("gateway")
                       if isinstance(raw, dict) else None)
            if isinstance(gateway, dict):
                gateway_cost = gateway.get("cost")
                try:
                    if gateway_cost is not None:
                        cost += float(gateway_cost)
                        has_cost = True
                except (TypeError, ValueError):
                    pass
        latency += int(item.get("latency_ms") or 0)
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost if has_cost else None,
        "latency_ms_total": latency,
    }


def run_shadow(
    db: Session,
    research_results: List[Dict[str, Any]],
    target_date: date,
) -> Dict[str, Any]:
    """Run independent Jev evidence classification for a P3 research batch."""
    mode = current_mode()
    if mode == MODE_OFF:
        return empty_report(mode)
    if mode == MODE_ASSIST:
        return empty_report(
            mode,
            status="ASSIST_RESERVED",
            message="JEV_MODE=assist is reserved; Shadow Mode remains non-decisioning.",
        )

    report = empty_report(mode, status="RUNNING")
    config = _config()
    api_key = get_jev_api_key()
    cache_available = _cache_table_available(db)
    units: List[Dict[str, Any]] = []
    for item in research_results or []:
        units.extend(_source_units(item, target_date=target_date))
    report["summary"]["candidate_count"] = len(research_results or [])
    report["summary"]["news_item_count"] = len(units)

    if not api_key:
        report["status"] = "NOT_CONFIGURED"
        report["message"] = "AI_GATEWAY_API_KEY is not configured; no Jev request was made."
        report["summary"]["failure_count"] = len(units)
        report["evidence"] = [
            _failure_evidence(unit, status="NOT_CONFIGURED", error="AI_GATEWAY_API_KEY is not configured.")
            for unit in units
        ]
        report["comparison"] = [_comparison(item) for item in report["evidence"]]
        return report

    evidence: List[Dict[str, Any]] = []
    pending: List[Tuple[Dict[str, Any], List[Dict[str, Any]], str]] = []
    seen_keys = set()
    for unit in units:
        unit["news_id"] = _news_id(unit)
        unit["input_hash"] = _unit_input_hash(unit)
        if unit.get("status") != "READY":
            evidence.append(_failure_evidence(unit, status=unit["status"], error=unit.get("error") or "Invalid evidence."))
            continue
        published = _parse_source_date(unit.get("source_published_at"))
        prior_rows = (
            _prior_rows(db, unit["stock_id"], before_date=published or target_date)
            if cache_available
            else []
        )
        prior, history_version = _history_payload(prior_rows)
        unit["historical_evidence_version"] = history_version
        cache_key = _json_hash({
            "stock_id": unit["stock_id"],
            "input_hash": unit["input_hash"],
            "historical_evidence_version": history_version,
            "question_version": QUESTION_VERSION,
            "model": config["model"],
        })
        if cache_key in seen_keys:
            continue
        seen_keys.add(cache_key)
        cached = (
            db.query(JevEvaluationCache)
            .filter(JevEvaluationCache.cache_key == cache_key)
            .first()
            if cache_available
            else None
        )
        if cached is not None and isinstance(cached.evidence, dict) and cached.status == "OK":
            cached_evidence = dict(cached.evidence)
            cached_evidence["cache_hit"] = True
            cached_evidence["cache_hit_at"] = _iso_now()
            cached_evidence["api_call_count"] = 0
            cached_evidence["retry_count"] = 0
            cached_evidence["original_llm"] = unit.get("original_llm") or {}
            evidence.append(cached_evidence)
            continue
        pending.append((unit, prior, cache_key))

    max_calls = config["max_api_calls"]
    if len(pending) > max_calls:
        for unit, _prior, _key in pending[max_calls:]:
            evidence.append(_failure_evidence(unit, status="BUDGET_EXCEEDED", error="Jev per-run API budget exceeded."))
        pending = pending[:max_calls]

    def evaluate_one(entry: Tuple[Dict[str, Any], List[Dict[str, Any]], str]) -> Dict[str, Any]:
        unit, prior, _key = entry
        return _evaluate_unit(
            unit,
            analysis_date=target_date,
            prior=prior,
            config=config,
            api_key=api_key,
        )

    if pending:
        with ThreadPoolExecutor(max_workers=min(config["concurrency"], len(pending))) as executor:
            future_map = {executor.submit(evaluate_one, entry): entry for entry in pending}
            for future in as_completed(future_map):
                unit, _prior, _key = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _failure_evidence(unit, status="API_ERROR", error=_redact(exc, api_key))
                result["model"] = result.get("model") or config["model"]
                result["original_llm"] = unit.get("original_llm") or {}
                result["historical_evidence_version"] = unit.get("historical_evidence_version")
                result["input_hash"] = unit.get("input_hash")
                result["input_context"] = _input_context(unit, _prior)
                evidence.append(result)
                if result.get("status") == "OK" and cache_available:
                    try:
                        db.add(JevEvaluationCache(
                            cache_key=_key,
                            stock_id=unit["stock_id"],
                            news_id=unit.get("news_id"),
                            source_url=unit.get("source_url"),
                            source_published_at=unit.get("source_published_at"),
                            input_hash=unit["input_hash"],
                            historical_evidence_version=unit.get("historical_evidence_version") or "none",
                            question_version=QUESTION_VERSION,
                            model=result.get("model") or config["model"],
                            status="OK",
                            evidence=result,
                            raw_model_output=result.get("raw_model_output"),
                            usage=result.get("usage") or {},
                            latency_ms=result.get("latency_ms"),
                            evaluated_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        ))
                    except Exception:
                        logger.exception("Unable to stage Jev cache for %s", unit.get("stock_id"))

    report["evidence"] = sorted(
        evidence,
        key=lambda item: (str(item.get("stock_id") or ""), str(item.get("news_id") or "")),
    )
    report["comparison"] = [_comparison(item) for item in report["evidence"]]
    usage = _usage_summary(report["evidence"])
    summary = report["summary"]
    summary.update(usage)
    summary["evaluated_count"] = sum(item.get("status") in {"OK", "CACHED"} for item in evidence)
    summary["cache_hit_count"] = sum(bool(item.get("cache_hit")) for item in evidence)
    summary["api_call_count"] = sum(int(item.get("api_call_count") or 0) for item in evidence)
    summary["retry_count"] = sum(int(item.get("retry_count") or 0) for item in evidence)
    summary["failure_count"] = sum(item.get("status") not in {"OK", "CACHED"} for item in evidence)
    report["status"] = "COMPLETED" if not summary["failure_count"] else "PARTIAL"
    return report


def run_shadow_safe(
    db: Session,
    research_results: List[Dict[str, Any]],
    target_date: date,
) -> Dict[str, Any]:
    """Run Shadow Mode without allowing sidecar failures to stop P3/P4."""
    try:
        return run_shadow(db, research_results, target_date)
    except Exception:
        logger.exception("Jev Shadow Mode failed outside an individual evaluation")
        return empty_report(
            current_mode(),
            status="ERROR",
            message="Jev shadow processing failed; existing P3/P4 continued without Jev.",
        )
